"""게임 쪽 측정 모드 로그·프레임 CSV 파서.

게임(측정 모드 빌드)은 logcat에 다음 줄을 찍는다(`json=`은 줄 끝까지 한 줄 JSON).

    [PerfRun] BEGIN runId=<id> json={...}
    [PerfRun] PHASE name=<이름> runId=<id> json={idx, measure, t, tGame, frame, scene, ...}
    [PerfRun] EVENT kind=<종류> name=<이름> runId=<id> json={frame, t, callMs, nextFrameMs, phase, error}
    [PerfRun] END runId=<id> result=<ok|fail> json={result, reason, frames, measuredFrames, measuredSeconds, ..., csv}
    [PerfStats] header key=value ...
    [PerfStats] t=<초> key=value ... phase=<이름>              (1초마다)

프레임 CSV(끝날 때 한 번에 씀) 머리: frame,tReal,tGame,phase,dtMs,cpuMs,mainMs,renderMs,waitMs,gpuMs,setpass,dc,
batches,tris,verts,gcAllocB,monoAllocB,monoKB. 값이 없으면 -1.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

LINE_RE = re.compile(r"^\s*(\d+\.\d+)\s+(\d+)\s+(\d+)\s+([VDIWEFA])\s+(.*?)\s*:\s(.*)$")
KV_RE = re.compile(r"(\w+)=(\S+)")
JSON_TAIL = re.compile(r"\bjson=(\{.*\})\s*$")
NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


@dataclass
class LogLine:
    epoch: float
    pid: int
    tid: int
    level: str
    tag: str
    msg: str


def parse_line(line: str) -> LogLine | None:
    m = LINE_RE.match(line)
    if not m:
        return None
    return LogLine(float(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4), m.group(5).strip(), m.group(6))


def _json_tail(msg: str) -> dict:
    m = JSON_TAIL.search(msg)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}


def _kv(text: str) -> dict:
    out = {}
    for k, v in KV_RE.findall(text):
        out[k] = float(v) if NUM_RE.match(v) else v
    return out


@dataclass
class ProbeLog:
    begin: dict | None = None
    begin_epoch: float | None = None
    run_id: str | None = None
    app_pid: int | None = None
    header: dict = field(default_factory=dict)
    phases: list[dict] = field(default_factory=list)   # {name, epoch, ...json}
    events: list[dict] = field(default_factory=list)   # {kind, name, epoch, ...json}
    end: dict | None = None
    end_epoch: float | None = None
    stats: list[dict] = field(default_factory=list)    # [PerfStats] 1초 줄
    warnings: list[str] = field(default_factory=list)  # [PerfProbeArgs] 등
    restarts: int = 0                                 # 수집기가 남긴 logcat 재연결 표시

    @property
    def has_probe(self) -> bool:
        return self.begin is not None

    def phase_t(self, name: str) -> float | None:
        for p in self.phases:
            if p.get("name") == name:
                return p.get("t")
        return None

    def measure_phases(self) -> list[str]:
        names = [p["name"] for p in self.phases if p.get("measure")]
        return names

    def settle(self) -> dict | None:
        for p in self.phases:
            if p.get("name") == "settle":
                return p
        return None


def feed(log: ProbeLog, ln: LogLine) -> str | None:
    """줄 하나를 반영한다. 측정 모드 줄이면 종류(begin·phase·event·end·stats)를 돌려준다."""
    msg = ln.msg
    if "[PerfRun]" in msg:
        body = msg.split("[PerfRun]", 1)[1].strip()
        j = _json_tail(body)
        head = body.split(" json=", 1)[0]
        kv = _kv(head)
        if body.startswith("BEGIN"):
            log.begin, log.begin_epoch = j, ln.epoch
            log.run_id = kv.get("runId") or log.run_id
            log.app_pid = ln.pid
            return "begin"
        if body.startswith("PHASE"):
            log.phases.append({"name": kv.get("name"), "epoch": ln.epoch, **j})
            return "phase"
        if body.startswith("EVENT"):
            log.events.append({"kind": kv.get("kind"), "event_name": kv.get("name"), "epoch": ln.epoch, **j})
            return "event"
        if body.startswith("END"):
            log.end, log.end_epoch = (j or {"result": kv.get("result")}), ln.epoch
            log.end.setdefault("result", kv.get("result"))
            return "end"
        return None
    if "[PerfStats]" in msg:
        body = msg.split("[PerfStats]", 1)[1].strip()
        if body.startswith("header"):
            log.header = _kv(body[len("header"):])
            return "header"
        row = _kv(body)
        if "t" in row:
            row["_epoch"] = ln.epoch
            log.stats.append(row)
            return "stats"
        return None
    if "[PerfProbeArgs]" in msg:
        log.warnings.append(msg.strip()[:300])
    return None


def parse_logcat_text(text: str) -> tuple[ProbeLog, list[LogLine]]:
    log = ProbeLog()
    lines: list[LogLine] = []
    for raw in text.splitlines():
        if raw.startswith("##PERFKIT_LOGCAT_RESTART"):
            log.restarts += 1
            continue
        ln = parse_line(raw)
        if ln is None:
            continue
        lines.append(ln)
        feed(log, ln)
    return log, lines


def parse_logcat_file(path: Path) -> tuple[ProbeLog, list[LogLine]]:
    return parse_logcat_text(Path(path).read_bytes().decode("utf-8", "replace"))


# --------------------------------------------------------------------------- 앱 로그 요약(STB-08·STB-09)

EXC_RE = re.compile(r"\b((?:[a-zA-Z_$][\w$]*\.)*[A-Z][\w$]*(?:Exception|Error))\b(?::\s*(.*))?")
CLASS_RE = re.compile(r"^\[([A-Z][A-Za-z0-9_]*)\]")
SKIP_RE = re.compile(r"Skipped (\d+) frames")


def signature(cls: str, message: str | None) -> str:
    """예외 서명: 클래스 + 메시지(숫자·16진·따옴표 안·경로를 가림), 120자."""
    m = (message or "").strip()
    m = re.sub(r'"[^"]*"', '"…"', m)
    m = re.sub(r"'[^']*'", "'…'", m)
    m = re.sub(r"(/[\w.~=+-]+)+", "/…", m)
    m = re.sub(r"\b0x[0-9a-fA-F]+\b", "0x…", m)
    m = re.sub(r"\d+", "N", m)
    return (cls + (": " + m if m else ""))[:120]


def app_log_summary(lines: list[LogLine], app_pids: set[int], package: str | None) -> dict:
    exc = cw = ce = ar = nd = 0
    sigs: dict[str, int] = {}
    skipped = anr = lmk = 0
    fatal = []
    for ln in lines:
        msg = ln.msg
        mine = ln.pid in app_pids
        if package and ("ANR in " + package) in msg:
            anr += 1
        if ln.tag in ("lowmemorykiller", "lmkd") and ((package and package in msg) or any(f"pid {p}" in msg or f" {p} " in msg for p in app_pids)):
            lmk += 1
        if ln.tag in ("DEBUG", "crash_dump64", "crash_dump32") or ("Fatal signal" in msg and mine):
            if mine or any(f"pid: {p}" in msg for p in app_pids) or (package and package in msg):
                nd += 1
                fatal.append(msg[:200])
        if not mine:
            continue
        if ln.tag == "AndroidRuntime" and ln.level == "E":
            ar += 1
            if "FATAL EXCEPTION" in msg:
                fatal.append(msg[:200])
        if ln.tag == "Choreographer":
            m = SKIP_RE.search(msg)
            if m:
                skipped += int(m.group(1))
        stripped = msg.lstrip()
        if stripped.startswith("at ") or stripped.startswith("... "):
            continue
        m = EXC_RE.search(msg)
        if m and ("Exception" in m.group(1)):
            exc += 1
            s = signature(m.group(1), m.group(2))
            sigs[s] = sigs.get(s, 0) + 1
        if ln.tag == "Unity":
            c = CLASS_RE.match(stripped)
            if c and not stripped.startswith(("[PerfRun]", "[PerfStats]", "[PerfProbeArgs]")):
                if ln.level == "W":
                    cw += 1
                elif ln.level in ("E", "F"):
                    ce += 1
    return {"exceptions": exc, "class_warnings": cw, "class_errors": ce, "android_runtime": ar, "native_debug": nd,
            "signatures": dict(sorted(sigs.items(), key=lambda kv: (-kv[1], kv[0]))),
            "choreographer_skipped": skipped, "anr": anr, "lmk": lmk, "fatal": fatal[:10]}


# --------------------------------------------------------------------------- 프레임 CSV

FRAME_FIELDS = ["frame", "tReal", "tGame", "phase", "dtMs", "cpuMs", "mainMs", "renderMs", "waitMs", "gpuMs",
                "setpass", "dc", "batches", "tris", "verts", "gcAllocB", "monoAllocB", "monoKB"]


def parse_frames_text(text: str) -> list[dict]:
    rows = []
    rd = csv.DictReader(io.StringIO(text))
    for r in rd:
        row = {}
        for k, v in r.items():
            if k is None:
                continue
            if k == "phase":
                row[k] = v
                continue
            try:
                row[k] = float(v)
            except (TypeError, ValueError):
                row[k] = None
        rows.append(row)
    return rows


def parse_frames_file(path: Path) -> list[dict]:
    return parse_frames_text(Path(path).read_text(encoding="utf-8", errors="replace"))
