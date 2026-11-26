"""C# 소스를 규칙 검사에 맞게 다듬는 가벼운 분석기(컴파일러 없이).

- `strip`: 주석과 문자열·문자 내용을 공백으로 바꾼다(줄·열 위치 유지, 따옴표는 남김).
  보간 문자열 `$"..."` 위치를 따로 돌려준다.
- `inactive_lines`: `#if`/`#elif`/`#else`/`#endif`를 읽어, 정의 기호 표(예: UNITY_EDITOR=False)로
  확실히 꺼지는 줄을 찾는다. 모르는 기호는 켜짐/꺼짐 둘 다 가능(포함)으로 본다.
- `scopes`: 중괄호를 따라 클래스·메서드 범위를 잡는다. 각 위치가 어느 메서드 안인지, 빈 메서드인지 안다.

정확한 C# 파서가 아니다. 목적은 오탐을 줄이는 것(주석·문자열 안 일치 제외, 메서드 범위 인식)이다.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field

CONTROL = {"if", "for", "foreach", "while", "switch", "catch", "using", "lock", "fixed", "else", "do",
           "try", "finally", "unchecked", "checked", "unsafe", "return", "new", "get", "set", "init",
           "add", "remove", "when", "nameof", "typeof", "sizeof", "default", "base", "this", "await"}
TYPE_KW = ("class", "struct", "interface", "enum", "record", "namespace")


# --------------------------------------------------------------------------- 주석·문자열

@dataclass
class Stripped:
    code: str                       # 주석·문자열 내용을 공백으로 바꾼 원문(길이·줄 위치 같음)
    interp: list[int]               # 보간 문자열 시작 위치($ 위치)
    strings: list[tuple[int, int]]  # 문자열 리터럴 [시작, 끝) (따옴표 포함)


def strip(src: str) -> Stripped:
    out = list(src)
    interp: list[int] = []
    strings: list[tuple[int, int]] = []
    n = len(src)
    i = 0

    def blank(a: int, b: int):
        for k in range(a, b):
            if out[k] not in "\r\n":
                out[k] = " "

    while i < n:
        c = src[i]
        nx = src[i + 1] if i + 1 < n else ""
        if c == "/" and nx == "/":
            j = src.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
            continue
        if c == "/" and nx == "*":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            blank(i, j)
            i = j
            continue
        if c == "'":
            j = i + 1
            while j < n and src[j] != "'" and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            j = min(j + 1, n)
            blank(i + 1, j - 1)
            i = j
            continue
        if c in "$@" or c == '"':
            # 접두어 $ @ $@ @$
            k = i
            is_interp = is_verbatim = False
            while k < n and src[k] in "$@" and k - i < 2:
                is_interp |= src[k] == "$"
                is_verbatim |= src[k] == "@"
                k += 1
            if k < n and src[k] == '"' and (k > i or c == '"'):
                start = i
                j = k + 1
                depth = 0
                while j < n:
                    ch = src[j]
                    if is_interp and ch == "{":
                        if j + 1 < n and src[j + 1] == "{":
                            j += 2
                            continue
                        depth += 1
                    elif is_interp and ch == "}" and depth > 0:
                        depth -= 1
                    elif depth == 0:
                        if is_verbatim:
                            if ch == '"':
                                if j + 1 < n and src[j + 1] == '"':
                                    j += 2
                                    continue
                                break
                        else:
                            if ch == "\\":
                                j += 2
                                continue
                            if ch == '"' or ch == "\n":
                                break
                    j += 1
                end = min(j + 1, n)
                blank(k + 1, end - 1)
                if is_interp:
                    interp.append(start)
                strings.append((start, end))
                i = end
                continue
            if c in "$@":
                i += 1
                continue
        i += 1
    return Stripped("".join(out), interp, strings)


# --------------------------------------------------------------------------- 전처리기

PP_RE = re.compile(r"^[ \t]*#[ \t]*(if|elif|else|endif|region|endregion|define|undef|pragma|nullable|warning|error|line)\b(.*)$")


def _eval_pp(expr: str, defines: dict[str, bool]):
    """전처리 조건을 3값(True·False·None=모름)으로 계산."""
    toks = re.findall(r"\|\||&&|!|\(|\)|==|!=|[A-Za-z_][A-Za-z0-9_]*", expr)
    pos = 0

    def peek():
        return toks[pos] if pos < len(toks) else None

    def take():
        nonlocal pos
        t = toks[pos]
        pos += 1
        return t

    def prim():
        t = take() if peek() is not None else None
        if t == "!":
            v = prim()
            return None if v is None else not v
        if t == "(":
            v = orx()
            if peek() == ")":
                take()
            return v
        if t == "true":
            return True
        if t == "false":
            return False
        if t is None:
            return None
        return defines.get(t)

    def andx():
        v = prim()
        while peek() == "&&":
            take()
            w = prim()
            v = False if (v is False or w is False) else (None if (v is None or w is None) else True)
        return v

    def orx():
        v = andx()
        while peek() == "||":
            take()
            w = andx()
            v = True if (v is True or w is True) else (None if (v is None or w is None) else False)
        return v

    try:
        return orx()
    except IndexError:
        return None


def _or3(a, b):
    if a is True or b is True:
        return True
    if a is None or b is None:
        return None
    return False


def inactive_lines(code: str, defines: dict[str, bool]) -> set[int]:
    """확실히 컴파일에서 빠지는 줄 번호(1부터). 지시문 줄 자체도 포함."""
    inactive: set[int] = set()
    # 스택 원소: (부모가 활성인지, 이 그룹에서 이미 참인 분기가 있었는지(True/None/False), 지금 분기 상태)
    stack: list[list] = []

    def active() -> bool:
        return all(fr[2] is not False for fr in stack)

    for ln, line in enumerate(code.splitlines(), start=1):
        m = PP_RE.match(line)
        if m:
            kind, rest = m.group(1), m.group(2)
            if kind == "if":
                v = _eval_pp(rest, defines)
                stack.append([active(), v, v])
            elif kind == "elif" and stack:
                fr = stack[-1]
                v = _eval_pp(rest, defines)
                if fr[1] is True:
                    fr[2] = False
                elif fr[1] is False:
                    fr[2] = v
                else:
                    fr[2] = False if v is False else None
                fr[1] = _or3(fr[1], v)
            elif kind == "else" and stack:
                fr = stack[-1]
                fr[2] = False if fr[1] is True else (True if fr[1] is False else None)
            elif kind == "endif" and stack:
                stack.pop()
            inactive.add(ln)
            continue
        if not active():
            inactive.add(ln)
    return inactive


# --------------------------------------------------------------------------- 범위

@dataclass
class Scope:
    kind: str            # type · method · block
    name: str | None
    open: int            # '{' 위치
    close: int = -1      # '}' 위치(못 찾으면 파일 끝)
    parent: "Scope | None" = None
    header: str = ""
    children: list = field(default_factory=list)


HEADER_TAIL = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:<[^<>(){};]*>)?\s*\(([^;{}]*)\)\s*"
                         r"(?:where\s[^;{}]*|:\s*(?:base|this)\s*\([^;{}]*\)\s*)?$", re.S)
EXPR_METHOD = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*[^;{}()]*\)\s*=>")


def _header_before(code: str, pos: int) -> str:
    j = pos - 1
    depth = 0
    while j >= 0:
        ch = code[j]
        if ch in ")]":
            depth += 1
        elif ch in "([":
            depth -= 1
        elif depth <= 0 and ch in ";{}":
            break
        j -= 1
    head = code[j + 1:pos]
    return "\n".join(l for l in head.splitlines() if not l.lstrip().startswith("#")).strip()


def _classify(header: str) -> tuple[str, str | None]:
    h = re.sub(r"\[[^\[\]]*\]", " ", header)  # 특성 [..] 제거
    h = " ".join(h.split())
    if not h:
        return "block", None
    if h.endswith("=>") or h.endswith("=") or h.endswith(","):
        return "block", None  # 람다·초기화 식
    tm = re.search(r"\b(" + "|".join(TYPE_KW) + r")\s+([A-Za-z_][A-Za-z0-9_]*)", h)
    if tm and "(" not in h.split(tm.group(0), 1)[0]:
        return "type", tm.group(2)
    m = HEADER_TAIL.search(h)
    if m:
        name = m.group(1)
        before = h[:m.start(1)].strip()
        if name in CONTROL or not before or before.endswith(("=", "=>", "(", ",", "return", "new", ".")):
            return "block", None
        if re.search(r"\bnew\s*$", before):
            return "block", None
        return "method", name
    # 속성 접근자 get/set 블록, 속성 본문
    last = h.split()[-1] if h.split() else ""
    if last in ("get", "set", "init", "add", "remove"):
        return "accessor", last
    return "block", None


def scopes(code: str) -> Scope:
    root = Scope("root", None, -1, len(code))
    cur = root
    for pos, ch in enumerate(code):
        if ch == "{":
            header = _header_before(code, pos)
            kind, name = _classify(header)
            sc = Scope(kind, name, pos, parent=cur, header=header if kind in ("method", "type") else "")
            cur.children.append(sc)
            cur = sc
        elif ch == "}":
            if cur.parent is not None:
                cur.close = pos
                cur = cur.parent
    while cur is not root:
        cur.close = len(code)
        cur = cur.parent
    return root


class ScopeIndex:
    """위치 → 감싸는 메서드 이름들."""

    def __init__(self, code: str):
        self.code = code
        self.root = scopes(code)
        self.methods: list[Scope] = []
        self._walk(self.root)
        self.expr_methods: list[tuple[int, int, str]] = []
        for m in EXPR_METHOD.finditer(code):
            name = m.group(1)
            if name in CONTROL:
                continue
            end = code.find(";", m.end())
            self.expr_methods.append((m.start(), len(code) if end < 0 else end, name))
        self._line_starts = [0] + [i + 1 for i, c in enumerate(code) if c == "\n"]

    def _walk(self, sc: Scope):
        for c in sc.children:
            if c.kind == "method":
                self.methods.append(c)
            self._walk(c)

    def enclosing(self, pos: int) -> list[Scope]:
        """감싸는 메서드·타입 범위(안쪽부터)."""
        out = []
        node = self._deepest(self.root, pos)
        while node is not None:
            if node.kind in ("method", "type"):
                out.append(node)
            node = node.parent
        return out

    def enclosing_methods(self, pos: int) -> list[str]:
        """안쪽부터 바깥쪽 순서."""
        out = []
        node = self._deepest(self.root, pos)
        while node is not None:
            if node.kind == "method":
                out.append(node.name)
            node = node.parent
        for a, b, name in self.expr_methods:
            if a <= pos <= b:
                out.insert(0, name)
        return out

    def _deepest(self, sc: Scope, pos: int) -> Scope:
        for c in sc.children:
            if c.open < pos <= c.close:
                return self._deepest(c, pos)
        return sc

    def line_of(self, pos: int) -> int:
        return bisect.bisect_right(self._line_starts, pos)

    def col_of(self, pos: int) -> int:
        return pos - self._line_starts[self.line_of(pos) - 1] + 1

    def empty_methods(self) -> list[Scope]:
        return [m for m in self.methods if not self.code[m.open + 1:m.close].strip()]
