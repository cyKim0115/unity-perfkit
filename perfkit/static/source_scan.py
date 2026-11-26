"""스크립트 규칙 검사(L0, SCR-*·CPU-14·FRM-05·GPU-18·AST-20의 소스 부분).

Project Auditor의 코드 규칙 패키지 없이도 결과가 나오도록 perfkit이 직접 소스를 훑는다.
규칙은 Auditor 규칙(PAC*)과 Unity 권장 패턴을 정규식으로 옮긴 것이고, 다음으로 오탐을 줄인다.

- 주석·문자열 안은 보지 않는다(`csharp.strip`).
- 빌드에서 빠지는 코드는 보지 않는다: `Editor` 폴더, 에디터 전용 asmdef, `#if UNITY_EDITOR` 등
  꺼지는 전처리 구간(정의 기호 표는 프로필 `[static] defines`로 바꿀 수 있음).
- 메서드 범위를 안다: 매 프레임 도는 메서드(Update 등) 안이면 `hot=True`(Auditor처럼 Major로 올림).
  일부 규칙은 hot 안에서만 센다(GetComponent·new 할당 의심).

결과는 규칙 ID·파일·줄·열·메서드·발췌와 지문(fingerprint)을 남긴다. 지문은 줄 번호를 쓰지 않아
위아래 코드가 바뀌어도 같은 위반은 같은 지문이다(새로 생긴·없어진 위반 비교용).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from perfkit.static import csharp

HOT_METHODS = frozenset({
    "Update", "LateUpdate", "FixedUpdate", "OnGUI", "OnRenderObject", "OnWillRenderObject",
    "OnPreRender", "OnPostRender", "OnPreCull", "OnAnimatorMove", "OnAnimatorIK",
    "OnTriggerStay", "OnTriggerStay2D", "OnCollisionStay", "OnCollisionStay2D",
})
EMPTY_EVENT_METHODS = frozenset({"Update", "LateUpdate", "FixedUpdate"})

# 빌드 대상(기본 Android 플레이어) 기준 정의 기호. True=켜짐, False=꺼짐, 없는 기호=모름(켜진 쪽으로 셈).
DEFAULT_DEFINES = {
    "UNITY_EDITOR": False, "UNITY_EDITOR_WIN": False, "UNITY_EDITOR_OSX": False, "UNITY_EDITOR_LINUX": False,
    "UNITY_EDITOR_64": False, "UNITY_INCLUDE_TESTS": False,
    "UNITY_ANDROID": True, "UNITY_IOS": False, "UNITY_IPHONE": False, "UNITY_WEBGL": False,
    "UNITY_STANDALONE": False, "UNITY_STANDALONE_WIN": False, "UNITY_STANDALONE_OSX": False,
    "UNITY_STANDALONE_LINUX": False, "UNITY_SERVER": False, "UNITY_TVOS": False, "UNITY_SWITCH": False,
    "UNITY_PS4": False, "UNITY_PS5": False, "UNITY_XBOXONE": False, "UNITY_GAMECORE": False,
    "UNITY_WSA": False, "UNITY_VISIONOS": False,
}

NOT_A_LINQ_RECEIVER = re.compile(r"(?:\bMathf|\bMath|\bVector[234]|\bVector[234]Int|\bColor|\bQuaternion|\bmath)\s*$")


@dataclass(frozen=True)
class Rule:
    id: str
    item: str
    pattern: re.Pattern
    note: str
    hot_only: bool = False
    needs_using: str | None = None  # 파일에 이 using이 있어야 셈(예: System.Linq)
    skip_assign: bool = False       # 바로 뒤가 대입(=)이면 세지 않음(속성 쓰기)
    group: int = 0                  # 위치로 쓸 정규식 그룹


def _r(p: str) -> re.Pattern:
    return re.compile(p)


RULES: tuple[Rule, ...] = (
    # SCR-02 핫 패스 할당 API
    Rule("linq", "SCR-02", _r(r"\.\s*(Where|Select|SelectMany|Any|All|First|FirstOrDefault|Last|LastOrDefault|"
                              r"Single|SingleOrDefault|OrderBy|OrderByDescending|ThenBy|ThenByDescending|GroupBy|"
                              r"Distinct|ToList|ToArray|ToDictionary|ToHashSet|ToLookup|Count|Sum|Average|Aggregate|"
                              r"Concat|Union|Intersect|Except|Skip|SkipWhile|Take|TakeWhile|Cast|OfType|Zip|"
                              r"DefaultIfEmpty|SequenceEqual)\s*(?:<[^<>;(){}]*>)?\s*\("),
         "LINQ 호출(PAC1000). System.Linq를 쓰는 파일만", needs_using="System.Linq", group=1),
    Rule("string_concat", "SCR-02", _r(r"\"\s*\+(?![+=])|(?<![+])\+\s*[$@]{0,2}\"|\+=\s*[$@]{0,2}\"|\b[Ss]tring\.Concat\s*\("),
         "문자열 연결(PAC1002)"),
    Rule("string_interp", "SCR-02", _r(r"\$@?\"|@\$\""), "보간 문자열(할당)"),
    Rule("tag_compare", "SCR-02", _r(r"\.\s*tag\b(?!\s*=(?!=))"), ".tag 읽기(PAC0223/0229, CompareTag 권장)"),
    Rule("get_components_in", "SCR-02", _r(r"\bGetComponentsIn(?:Children|Parent)\s*(?:<[^<>;(){}]*>)?\s*\(\s*[^,()]*\)"),
         "GetComponentsInChildren/InParent 배열 반환(PAC0026~0029). 목록 인자 오버로드(쉼표 인자)는 제외"),
    Rule("physics_all", "SCR-02", _r(r"\bPhysics(?:2D)?\s*\.\s*(RaycastAll|SphereCastAll|CapsuleCastAll|BoxCastAll|"
                                     r"OverlapSphere|OverlapBox|OverlapCapsule|CircleCastAll|OverlapCircleAll|"
                                     r"OverlapAreaAll|OverlapPointAll|LinecastAll)\s*\("),
         "Physics.*All·Overlap*(PAC0008, NonAlloc 권장)", group=1),
    Rule("mesh_arrays", "SCR-02", _r(r"\.\s*(vertices|triangles|normals|tangents|colors|colors32|uv|uv2|boneWeights)\b"),
         "Mesh 배열 속성 읽기(PAC0069)", skip_assign=True, group=1),
    Rule("new_alloc_hot", "SCR-02", _r(r"\bnew\s+(?:(?:System\.Collections\.Generic\.)?(?:List|Dictionary|HashSet|Queue|Stack|"
                                       r"LinkedList|SortedList|SortedDictionary|SortedSet)\s*<|(?:System\.Text\.)?StringBuilder\b|"
                                       r"WaitFor(?:Seconds|SecondsRealtime|EndOfFrame|FixedUpdate)\b|WaitUntil\b|WaitWhile\b|"
                                       r"string\s*\(|[A-Za-z_][\w.]*\s*\[\s*[^\]\s])"),
         "매 프레임 메서드 안 new 할당 의심(컬렉션·배열·StringBuilder·Wait*)", hot_only=True),
    # SCR-03 탐색 API
    Rule("camera_main", "SCR-03", _r(r"\bCamera\s*\.\s*main\b"), "Camera.main(PAC0000)"),
    Rule("find_object", "SCR-03", _r(r"\b(?:FindObjectsOfType|FindObjectOfType|FindFirstObjectByType|FindAnyObjectByType|"
                                     r"FindObjectsByType)\b"),
         "FindObject(s)OfType·ByType(PAC0234/0129)"),
    Rule("gameobject_find", "SCR-03", _r(r"\bGameObject\s*\.\s*(?:Find|FindWithTag|FindGameObjectWithTag|"
                                         r"FindGameObjectsWithTag)\s*\("), "GameObject.Find*"),
    Rule("resources_find_all", "SCR-03", _r(r"\bResources\s*\.\s*(?:FindObjectsOfTypeAll|LoadAll)\b"),
         "Resources.FindObjectsOfTypeAll·LoadAll(PAC0004/0120)"),
    Rule("get_component_hot", "SCR-03", _r(r"\b(?:Try)?GetComponents?(?:InChildren|InParent)?\s*(?:<[^<>;(){}]*>)?\s*\("),
         "매 프레임 메서드 안 GetComponent 계열", hot_only=True),
    # SCR-04 리플렉션·SendMessage·OnGUI
    Rule("reflection", "SCR-04", _r(r"\.\s*(?:GetMethods?|GetFields?|GetPropert(?:y|ies)|GetMembers?|GetCustomAttributes?|"
                                    r"InvokeMember|GetConstructors?|GetEvents?|GetInterfaces)\s*\(|"
                                    r"\bActivator\s*\.\s*CreateInstance\b|\bType\s*\.\s*GetType\s*\("),
         "리플렉션 호출(PAC1001)"),
    Rule("send_message", "SCR-04", _r(r"\b(?:SendMessage|SendMessageUpwards|BroadcastMessage)\s*\("),
         "SendMessage 계열(PAC0056/0057)"),
    # SCR-07
    Rule("renderer_material", "SCR-07", _r(r"\.\s*materials?\b"),
         ".material(s) 읽기(PAC0039, 머티리얼 복제). sharedMaterial 제외, 대입 제외", skip_assign=True),
)

DEBUG_LOG = re.compile(r"(?<![\w.])((?:UnityEngine\s*\.\s*)?Debug\s*\.\s*(?:Log|LogWarning|LogError|LogException|LogFormat|"
                       r"LogWarningFormat|LogErrorFormat|LogAssertion|LogAssertionFormat)\s*\()")
DEBUG_LOG_QUALIFIED = re.compile(r"(?<=\.)Debug\s*\.\s*(?:Log|LogWarning|LogError|LogException|LogFormat|"
                                 r"LogWarningFormat|LogErrorFormat)\s*\(")
CONDITIONAL_ATTR = re.compile(r"\[\s*(?:System\s*\.\s*Diagnostics\s*\.\s*)?Conditional\s*\(")
ASSIGN_RE = {
    "target_frame_rate": re.compile(r"\bApplication\s*\.\s*targetFrameRate\s*=(?!=)\s*([^;]+);"),
    "vsync_count": re.compile(r"\bQualitySettings\s*\.\s*vSyncCount\s*=(?!=)\s*([^;]+);"),
    "background_loading_priority": re.compile(r"\bApplication\s*\.\s*backgroundLoadingPriority\s*=(?!=)\s*([^;]+);"),
}
RENDER_PASS_CLASS = re.compile(r"\bclass\s+([A-Za-z_]\w*)[^{]*:\s*[^{]*\bScriptableRenderPass\b")
ASSET_POSTPROCESSOR = re.compile(r"\bclass\s+([A-Za-z_]\w*)[^{;]*:\s*(?:UnityEditor\s*\.\s*)?AssetPostprocessor\b")
USING_RE = re.compile(r"^\s*using\s+(?:static\s+)?([\w.]+)\s*;", re.M)


@dataclass
class Finding:
    rule: str
    item: str
    path: str
    line: int
    col: int
    method: str | None
    hot: bool
    excerpt: str
    fingerprint: str = ""

    def to_dict(self) -> dict:
        return {"rule": self.rule, "item": self.item, "path": self.path, "line": self.line, "col": self.col,
                "method": self.method, "hot": self.hot, "excerpt": self.excerpt, "fingerprint": self.fingerprint}


@dataclass
class ScanOptions:
    defines: dict = field(default_factory=lambda: dict(DEFAULT_DEFINES))
    code_rules: list = field(default_factory=list)  # [{"id", "pattern", "note"}]
    excerpt_max: int = 160


@dataclass
class FileScan:
    path: str
    editor_only: bool
    findings: list[Finding] = field(default_factory=list)
    code_rule_hits: list[Finding] = field(default_factory=list)
    debug_logs: list[Finding] = field(default_factory=list)
    debug_logs_excluded: dict = field(default_factory=dict)
    assignments: dict = field(default_factory=dict)  # 이름 → [(rhs, path, line)]
    render_passes: list = field(default_factory=list)  # (class, has_execute, has_record_render_graph)
    postprocessors: list = field(default_factory=list)
    empty_events: list[Finding] = field(default_factory=list)
    on_gui: list[Finding] = field(default_factory=list)
    inactive_line_count: int = 0


def _excerpt(lines: list[str], ln: int, n: int) -> str:
    if 1 <= ln <= len(lines):
        s = " ".join(lines[ln - 1].split())
        return s if len(s) <= n else s[: n - 1] + "…"
    return ""


ONCE_OUTSIDE_METHOD = frozenset({"string_concat", "string_interp"})


def _literal_only_concat(code: str, start: int, end: int) -> bool:
    """`"a" + "b"`처럼 양쪽이 모두 일반 문자열 리터럴이면 컴파일 때 합쳐지므로 세지 않는다."""
    seg = code[start:end]
    if "+=" in seg or "Concat" in seg:
        return False
    plus = start + seg.index("+")
    left = code[:plus].rstrip()
    right = code[plus + 1:].lstrip()
    return left.endswith('"') and right.startswith('"') and not re.search(r"[$]@?\"[^\"]*\"$|@\$\"[^\"]*\"$", left[-200:])


def _assign_follows(code: str, end: int) -> bool:
    m = re.match(r"\s*(=)(?!=)|\s*(\+=|-=)", code[end:end + 4])
    return bool(m)


def scan_text(rel_path: str, src: str, opts: ScanOptions, *, editor_only: bool = False) -> FileScan:
    fs = FileScan(rel_path, editor_only)
    st = csharp.strip(src)
    code = st.code
    orig_lines = src.splitlines()
    inactive = csharp.inactive_lines(code, opts.defines)
    fs.inactive_line_count = len(inactive)
    ix = csharp.ScopeIndex(code)
    usings = set(USING_RE.findall(code))

    def mk(rule: str, item: str, pos: int) -> Finding | None:
        ln = ix.line_of(pos)
        if ln in inactive:
            return None
        meths = ix.enclosing_methods(pos)
        return Finding(rule, item, rel_path, ln, ix.col_of(pos), meths[0] if meths else None,
                       any(m in HOT_METHODS for m in meths), _excerpt(orig_lines, ln, opts.excerpt_max))

    # 클래스 단위 정보(에디터 코드 포함): 후처리기·렌더 패스
    for m in ASSET_POSTPROCESSOR.finditer(code):
        fs.postprocessors.append(m.group(1))

    if not editor_only:
        for rule in RULES:
            if rule.needs_using and rule.needs_using not in usings:
                continue
            for m in rule.pattern.finditer(code):
                pos = m.start(rule.group) if rule.group else m.start()
                if rule.skip_assign and _assign_follows(code, m.end()):
                    continue
                if rule.id == "linq" and NOT_A_LINQ_RECEIVER.search(code[max(0, m.start() - 12):m.start()]):
                    continue
                if rule.id == "renderer_material" and code[m.start():m.end()].endswith("material") is False \
                        and not code[m.start():m.end()].endswith("materials"):
                    continue
                if rule.id == "string_concat" and _literal_only_concat(code, m.start(), m.end()):
                    continue
                f = mk(rule.id, rule.item, pos)
                if f is None:
                    continue
                if rule.hot_only and not f.hot:
                    continue
                if rule.id in ONCE_OUTSIDE_METHOD and f.method is None:
                    continue  # 필드 초기화·상수: 한 번만 만들어짐
                fs.findings.append(f)
        for cr in opts.code_rules:
            pat = re.compile(cr["pattern"])
            for m in pat.finditer(code):
                f = mk(cr["id"], "SCR-08", m.start())
                if f is not None:
                    fs.code_rule_hits.append(f)
        for m in DEBUG_LOG.finditer(code):
            pos = m.start(1)
            ln = ix.line_of(pos)
            if ln in inactive:
                fs.debug_logs_excluded["inactive_region"] = fs.debug_logs_excluded.get("inactive_region", 0) + 1
                continue
            if any(sc.kind == "method" and CONDITIONAL_ATTR.search(sc.header) for sc in ix.enclosing(pos)):
                fs.debug_logs_excluded["conditional_method"] = fs.debug_logs_excluded.get("conditional_method", 0) + 1
                continue
            f = mk("debug_log", "SCR-05", pos)
            if f is not None:
                fs.debug_logs.append(f)
        q = len(DEBUG_LOG_QUALIFIED.findall(code))
        if q:
            fs.debug_logs_excluded["other_qualified_debug_class"] = q
        for name, rx in ASSIGN_RE.items():
            for m in rx.finditer(code):
                ln = ix.line_of(m.start())
                if ln in inactive:
                    continue
                fs.assignments.setdefault(name, []).append((" ".join(m.group(1).split()), rel_path, ln))
        for meth in ix.methods:
            ln = ix.line_of(meth.open)
            if ln in inactive:
                continue
            if meth.name in EMPTY_EVENT_METHODS and not code[meth.open + 1:meth.close].strip():
                f = mk("empty_event", "SCR-06", meth.open)
                if f is not None:
                    f.method = meth.name
                    fs.empty_events.append(f)
            if meth.name == "OnGUI":
                f = mk("on_gui", "SCR-04", meth.open)
                if f is not None:
                    f.method = meth.name
                    f.hot = True
                    fs.on_gui.append(f)
        for m in RENDER_PASS_CLASS.finditer(code):
            brace = code.find("{", m.end())
            if brace < 0:
                continue
            body_scope = _scope_at(ix.root, brace)
            body = code[brace:body_scope.close if body_scope else len(code)]
            fs.render_passes.append((m.group(1), bool(re.search(r"\bExecute\s*\(", body)),
                                     bool(re.search(r"\bRecordRenderGraph\s*\(", body))))
    return fs


def _scope_at(root, open_pos: int):
    stack = [root]
    while stack:
        sc = stack.pop()
        if sc.open == open_pos:
            return sc
        stack.extend(sc.children)
    return None


# --------------------------------------------------------------------------- 파일 고르기

def editor_only_dirs(project: Path, files: list[Path]) -> list[Path]:
    """includePlatforms가 Editor뿐인 asmdef가 있는 폴더들."""
    out = []
    for f in files:
        if f.suffix.lower() != ".asmdef":
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        plats = d.get("includePlatforms") or []
        if plats == ["Editor"]:
            out.append(f.parent)
    return out


def is_editor_path(rel: str, editor_dirs_rel: list[str]) -> bool:
    parts = rel.split("/")
    if "Editor" in parts[:-1]:
        return True
    return any(rel.startswith(d.rstrip("/") + "/") for d in editor_dirs_rel)


def fingerprint_all(findings: list[Finding]) -> None:
    """지문: 규칙·파일·메서드·정규화한 발췌 + 같은 조합의 순번. 줄 번호는 쓰지 않는다."""
    seen: dict[str, int] = {}
    for f in sorted(findings, key=lambda x: (x.path, x.line, x.col, x.rule)):
        key = f"{f.rule}|{f.path}|{f.method or ''}|{' '.join(f.excerpt.split())}"
        n = seen.get(key, 0)
        seen[key] = n + 1
        f.fingerprint = hashlib.sha1(f"{key}|{n}".encode("utf-8")).hexdigest()[:16]
