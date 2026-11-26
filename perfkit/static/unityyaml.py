"""Unity 직렬화 YAML(.meta·.asset·.mat·ProjectSettings)을 읽는 작은 파서.

Unity가 쓰는 YAML의 좁은 부분집합만 다룬다. 외부 패키지(PyYAML)를 쓰지 않으려고 직접 짰다.

- 문서 구분 `--- !u!<클래스ID> &<fileID>` (헤더 없는 .meta는 문서 하나)
- 들여쓰기 맵, `- ` 목록(목록 안 맵 포함), 한 줄 흐름 맵·목록 `{a: 1, b: {c: 2}}`·`[]`
- 따옴표 문자열('·"), 여러 줄로 이어지는 일반 문자열(이어 붙임)

값은 모두 문자열이다(숫자 변환은 `num`·`flag`로). 모르는 모양은 문자열 그대로 둔다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

DOC_RE = re.compile(r"^--- !u!(\d+) &(-?\d+)(?: stripped)?\s*$")
KEY_RE = re.compile(r"^([^\s:'\"\[\]{},#-][^:]*?|-?\d[^:]*?|'[^']*'|\"[^\"]*\")\s*:(?:\s+(.*))?$")


@dataclass
class Doc:
    class_id: int | None
    file_id: str | None
    data: dict = field(default_factory=dict)

    @property
    def type_name(self) -> str | None:
        """최상위 키(예: TextureImporter, MonoBehaviour)."""
        return next(iter(self.data), None)

    @property
    def body(self) -> dict:
        v = self.data.get(self.type_name) if self.type_name else None
        return v if isinstance(v, dict) else {}


# --------------------------------------------------------------------------- 흐름 표기

def _split_flow(s: str) -> list[str]:
    parts, depth, cur, quote = [], 0, [], None
    for ch in s:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    tail = "".join(cur).strip()
    if tail:
        parts.append(tail)
    return parts


def _unquote(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        inner = s[1:-1]
        return inner.replace("''", "'") if s[0] == "'" else inner.replace('\\"', '"')
    return s


def parse_scalar(s: str):
    s = s.strip()
    if s.startswith("{") and s.endswith("}"):
        out = {}
        for part in _split_flow(s[1:-1]):
            if ":" in part:
                k, v = part.split(":", 1)
                out[_unquote(k.strip())] = parse_scalar(v)
        return out
    if s.startswith("[") and s.endswith("]"):
        return [parse_scalar(p) for p in _split_flow(s[1:-1])]
    return _unquote(s)


# --------------------------------------------------------------------------- 블록

def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _parse_block(lines: list[str], i: int, indent: int):
    """lines[i]부터 들여쓰기 indent인 블록을 읽어 (값, 다음 줄 번호)."""
    if i >= len(lines):
        return None, i
    first = lines[i]
    if _indent(first) == indent and first.lstrip().startswith("- ") or first.strip() == "-":
        return _parse_list(lines, i, indent)
    return _parse_map(lines, i, indent)


def _parse_list(lines: list[str], i: int, indent: int):
    out = []
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        ind = _indent(line)
        st = line.strip()
        if ind < indent or not (st.startswith("- ") or st == "-"):
            break
        if ind > indent:  # 앞 항목 값의 이어지는 줄(드묾)
            i += 1
            continue
        rest = st[2:] if st.startswith("- ") else ""
        item_indent = ind + 2
        if not rest:
            val, i = _parse_block(lines, i + 1, _indent(lines[i + 1]) if i + 1 < len(lines) else item_indent)
            out.append(val)
            continue
        m = KEY_RE.match(rest)
        if m and not rest.startswith(("{", "[")):
            # 목록 항목이 맵이다: 첫 키는 이 줄, 나머지는 item_indent 줄들
            synthetic = [" " * item_indent + rest]
            j = i + 1
            while j < len(lines):
                ln = lines[j]
                if ln.strip() and _indent(ln) < item_indent:
                    break
                if ln.strip() and _indent(ln) == indent and ln.strip().startswith("- "):
                    break
                synthetic.append(ln)
                j += 1
            val, _ = _parse_map(synthetic, 0, item_indent)
            out.append(val)
            i = j
        else:
            out.append(parse_scalar(rest))
            i += 1
    return out, i


def _parse_map(lines: list[str], i: int, indent: int):
    out: dict = {}
    last_key = None
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        ind = _indent(line)
        if ind < indent:
            break
        st = line.strip()
        if ind > indent:
            # 앞 키의 일반 문자열이 다음 줄로 이어짐
            if last_key is not None and isinstance(out.get(last_key), str):
                out[last_key] = (out[last_key] + " " + st).strip()
            i += 1
            continue
        if st.startswith("- "):
            break
        m = KEY_RE.match(st)
        if not m:
            i += 1
            continue
        key = _unquote(m.group(1).strip())
        rest = (m.group(2) or "").strip()
        last_key = key
        if rest == "":
            nxt = i + 1
            while nxt < len(lines) and not lines[nxt].strip():
                nxt += 1
            if nxt < len(lines):
                nind = _indent(lines[nxt])
                nst = lines[nxt].strip()
                if nind > indent or (nind == indent and nst.startswith("- ")):
                    val, i = _parse_block(lines, nxt, nind)
                    out[key] = val
                    continue
            out[key] = ""
            i += 1
            continue
        if rest[0] in "{[" and not _balanced(rest):
            # 흐름 표기가 여러 줄에 걸침
            buf = [rest]
            i += 1
            while i < len(lines) and not _balanced(" ".join(buf)):
                buf.append(lines[i].strip())
                i += 1
            out[key] = parse_scalar(" ".join(buf))
            continue
        out[key] = parse_scalar(rest)
        i += 1
    return out, i


def _balanced(s: str) -> bool:
    depth, quote = 0, None
    for ch in s:
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
    return depth <= 0


def parse_docs(text: str) -> list[Doc]:
    docs: list[Doc] = []
    cur_head: tuple[int | None, str | None] = (None, None)
    buf: list[str] = []

    def flush():
        body = [l for l in buf if not l.startswith("%")]
        if any(l.strip() for l in body):
            data, _ = _parse_map(body, 0, 0)
            docs.append(Doc(cur_head[0], cur_head[1], data))

    for line in text.splitlines():
        if line.startswith("---"):
            m = DOC_RE.match(line)
            flush()
            buf = []
            cur_head = (int(m.group(1)), m.group(2)) if m else (None, None)
            continue
        buf.append(line.rstrip("\r"))
    flush()
    return docs


def drop_keys(text: str, keys: frozenset | set | tuple) -> str:
    """이름이 keys인 키의 하위 블록을 줄 단위로 지운다(큰 스프라이트 표·본 목록을 건너뛰어 빠르게 읽기)."""
    if not keys:
        return text
    out, skip_indent = [], None
    for line in text.splitlines():
        st = line.lstrip(" ")
        ind = len(line) - len(st)
        if skip_indent is not None:
            if not st or ind > skip_indent or (ind == skip_indent and st.startswith("- ")):
                continue
            skip_indent = None
        if st and not st.startswith("- "):
            k = st.split(":", 1)[0]
            if k in keys and ":" in st:
                out.append(" " * ind + k + ": ")
                skip_indent = ind
                continue
        out.append(line)
    return "\n".join(out)


def parse(text: str, skip: frozenset | set | tuple = ()) -> dict:
    """문서 하나짜리(.meta·ProjectSettings 등)를 맵으로. skip: 건너뛸 키 이름."""
    docs = parse_docs(drop_keys(text, skip) if skip else text)
    return docs[0].data if docs else {}


def read(path, skip: frozenset | set | tuple = ()) -> dict:
    with open(path, encoding="utf-8", errors="replace") as f:
        return parse(f.read(), skip)


def read_docs(path) -> list[Doc]:
    with open(path, encoding="utf-8", errors="replace") as f:
        return parse_docs(f.read())


# --------------------------------------------------------------------------- 값 도우미

def dig(d, *path, default=None):
    cur = d
    for p in path:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        elif isinstance(cur, list) and isinstance(p, int) and -len(cur) <= p < len(cur):
            cur = cur[p]
        else:
            return default
    return cur


def num(v, default=None):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    if not isinstance(v, str):
        return default
    s = v.strip()
    try:
        return int(s)
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return default


def flag(v, default=None):
    n = num(v)
    if n is None:
        return default
    return bool(n)


def hex_ints(v: str, width: int = 8) -> list[int]:
    """Unity가 정수 배열을 16진 문자열로 쓴 것(`150000000b000000i`)을 리틀 엔디언 정수 목록으로."""
    if not isinstance(v, str):
        return []
    s = v.strip().rstrip("i")
    out = []
    for k in range(0, len(s) - width + 1, width):
        chunk = s[k:k + width]
        try:
            out.append(int.from_bytes(bytes.fromhex(chunk), "little"))
        except ValueError:
            return []
    return out
