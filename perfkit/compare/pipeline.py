"""커밋·APK를 받아 기준 대비 비교를 끝까지: 빌드(캐시) → L1 점검 → 기기 교차 실행(ABAB) → 회차 묶음.

빌드 명령·산출물 위치는 프로젝트마다 다르므로 코드에 두지 않고 프로젝트 프로필 `[build]`에서 읽는다.

    [build]
    command = ["<빌드 도구>", "...", "-Commit", "{commit}"]   # 자리: {commit} {commit8} {label} {profile_dir}
    state_glob = "<빌드 결과 JSON glob>"     # 빌드가 남기는 JSON(필수 칸: commit, file=APK 경로)
    log = "{state_dir}/logs/build.log"        # (선택) L1 셰이더 변형 집계용 빌드 로그
    layout = "{state_dir}/logs/buildlayout.json"  # (선택) Addressables 레이아웃
    cache = "build-cache.json"               # 커밋 → APK 색인(기본 <결과 폴더>/build-cache.json)
    timeout_min = 40
    probe_key = "probeCode"                   # 빌드 JSON에서 측정 모드 여부 칸(값 yes·true면 측정 모드)

command의 각 인자와 state_glob·log·layout·cache는 `~`와 환경 변수(`${VAR}`, Windows `%VAR%`)를 펼친다.
state_glob·cache의 상대 경로와 `{profile_dir}`는 프로필 파일 폴더 기준이다. 그래서 PC마다 다른 위치는
환경 변수나 프로필 옆 상대 경로로 적으면 프로필을 PC마다 고치지 않아도 된다.

APK는 빌드가 둔 곳에 그대로 두고 색인만 남긴다(복사하면 빌드 정보 JSON과의 연결이 끊김). 다시 쓸 때 파일 크기·sha256이 같은지 본다.
"""

from __future__ import annotations

import fnmatch
import glob
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from perfkit.paths import REPO_ROOT


class PipelineError(RuntimeError):
    pass


@dataclass
class Built:
    label: str
    apk: Path
    commit: str | None = None
    state: Path | None = None
    probe: bool | None = None
    built_now: bool = False
    cached: bool = False
    l1: Path | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def short(self) -> str:
        return (self.commit or self.apk.stem)[:8]


def _log(msg: str) -> None:
    print(f"[pipeline] {msg}", flush=True)


def build_cfg(settings) -> dict:
    return dict((settings.profile or {}).get("build") or {})


def _profile_dir(settings) -> Path:
    return settings.profile_path.parent if settings.profile_path else Path.cwd()


def _resolve(p: str, settings) -> Path:
    from perfkit.paths import expand_path

    return expand_path(p, _profile_dir(settings))


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with Path(p).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _truthy(v) -> bool | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("yes", "true", "1", "on")


# --------------------------------------------------------------------------- 캐시

def cache_path(settings, results_dir: Path) -> Path:
    c = build_cfg(settings).get("cache")
    return _resolve(c, settings) if c else results_dir / "build-cache.json"


def load_cache(p: Path) -> dict:
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"schema": "perfkit/build-cache@1", "commits": {}}


def save_cache(p: Path, doc: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def cache_lookup(doc: dict, commit: str) -> dict | None:
    for full, e in (doc.get("commits") or {}).items():
        if full.startswith(commit) or commit.startswith(full):
            apk = Path(e["apk"])
            if apk.exists() and apk.stat().st_size == e.get("size"):
                return {**e, "commit": full}
    return None


def cache_put(doc: dict, b: Built, sha: str) -> None:
    if not b.commit:
        return
    doc.setdefault("commits", {})[b.commit] = {
        "apk": str(b.apk), "size": b.apk.stat().st_size, "sha256": sha, "state": str(b.state) if b.state else None,
        "probe": b.probe, "added": datetime.now().isoformat(timespec="seconds")}


# --------------------------------------------------------------------------- 빌드

def _state_for(apk: Path, settings) -> tuple[Path | None, dict]:
    from perfkit.run.apkinfo import build_info

    tmpl = ((settings.profile or {}).get("run") or {}).get("build_info")
    bi = build_info(apk, tmpl)
    return (Path(bi["_path"]) if bi.get("_path") else None), bi


def from_apk(apk: str | Path, settings, label: str) -> Built:
    p = Path(apk)
    if not p.is_file():
        raise PipelineError(f"APK가 없음: {p}")
    st, bi = _state_for(p, settings)
    probe_key = build_cfg(settings).get("probe_key", "probeCode")
    b = Built(label=label, apk=p, commit=bi.get("commit"), state=st, probe=_truthy(bi.get(probe_key)))
    if not bi:
        b.notes.append("빌드 정보 JSON 없음(커밋 모름)")
    return b


def _fmt_cmd(cmd: list, **kw) -> list[str]:
    from perfkit.paths import expand_text

    return [expand_text(str(x), **kw) for x in cmd]


def build_commit(commit: str, settings, label: str, *, dry_run: bool = False) -> Built:
    bc = build_cfg(settings)
    if not bc.get("command") or not bc.get("state_glob"):
        raise PipelineError("커밋을 빌드하려면 프로젝트 프로필 [build] command·state_glob이 필요")
    cmd = _fmt_cmd(bc["command"], commit=commit, commit8=commit[:8], label=label,
                   profile_dir=str(_profile_dir(settings)))
    _log("빌드: " + " ".join(cmd))
    if dry_run:
        raise PipelineError("dry-run: 빌드하지 않음")
    t0 = time.time()
    proc = subprocess.run(cmd, timeout=float(bc.get("timeout_min", 40)) * 60)
    if proc.returncode != 0:
        raise PipelineError(f"빌드 명령 실패(종료 코드 {proc.returncode})")
    cands = []
    for s in glob.glob(str(_resolve(bc["state_glob"], settings))):
        sp = Path(s)
        if sp.stat().st_mtime < t0 - 5:
            continue
        try:
            d = json.loads(sp.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            continue
        c = str(d.get("commit") or "")
        if c and (c.startswith(commit) or commit.startswith(c)):
            cands.append((sp.stat().st_mtime, sp, d))
    if not cands:
        raise PipelineError(f"빌드 뒤 커밋 {commit}의 빌드 결과 JSON을 못 찾음({bc['state_glob']})")
    _, sp, d = max(cands)
    apk = Path(d.get("file") or "")
    if not apk.is_file():
        raise PipelineError(f"빌드 결과 JSON의 APK가 없음: {apk}")
    b = Built(label=label, apk=apk, commit=d.get("commit"), state=sp,
              probe=_truthy(d.get(bc.get("probe_key", "probeCode"))), built_now=True)
    _log(f"빌드 끝 {time.time() - t0:.0f}초: {apk}")
    return b


def resolve_commit(commit: str, settings, results_dir: Path, label: str, *, rebuild: bool = False,
                   dry_run: bool = False) -> Built:
    cp = cache_path(settings, results_dir)
    doc = load_cache(cp)
    hit = None if rebuild else cache_lookup(doc, commit)
    if hit:
        b = Built(label=label, apk=Path(hit["apk"]), commit=hit["commit"], state=Path(hit["state"]) if hit.get("state") else None,
                  probe=hit.get("probe"), cached=True)
        _log(f"캐시: {commit} → {b.apk}")
        return b
    b = build_commit(commit, settings, label, dry_run=dry_run)
    cache_put(doc, b, sha256_file(b.apk))
    save_cache(cp, doc)
    return b


def remember_apk(b: Built, settings, results_dir: Path) -> None:
    """--*-apk로 받은 APK도 커밋을 알면 캐시에 넣는다(다음에 같은 커밋을 빌드하지 않게)."""
    if not b.commit:
        return
    cp = cache_path(settings, results_dir)
    doc = load_cache(cp)
    if cache_lookup(doc, b.commit):
        return
    cache_put(doc, b, sha256_file(b.apk))
    save_cache(cp, doc)


# --------------------------------------------------------------------------- 하위 명령

def perfkit(args: list[str], *, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-X", "utf8", "-m", "perfkit", *args]
    _log("$ perfkit " + " ".join(args))
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env, text=True, encoding="utf-8", errors="replace",
                          stdout=subprocess.PIPE if capture else None)
    if check and proc.returncode != 0:
        raise PipelineError(f"perfkit {args[0]} 실패(종료 코드 {proc.returncode})")
    return proc


def l1_for(b: Built, settings, results_dir: Path, *, reuse: bool = True) -> Path:
    bc = build_cfg(settings)
    stamp = b.apk.parent.name
    out = results_dir / "builds" / stamp / "l1.json"
    if reuse and out.exists():
        try:
            if json.loads(out.read_text(encoding="utf-8")).get("source", {}).get("ref", "").endswith(b.apk.name):
                b.l1 = out
                return out
        except json.JSONDecodeError:
            pass
    args = ["build", "--apk", str(b.apk), "--stamp", stamp, "--out", str(out)]
    sd = str(b.state.parent) if b.state else None
    for key, flag in (("log", "--log"), ("layout", "--layout")):
        t = bc.get(key)
        if t and sd:
            from perfkit.paths import expand_text

            p = _resolve(expand_text(t, state_dir=sd), settings)
            if p.exists():
                args += [flag, str(p)]
    perfkit(args)
    b.l1 = out
    return out


def revive_device(device: str, settings) -> None:
    """배치 빌드가 adb 서버를 끊는 일이 있어 실행 전에 서버를 다시 띄우고 기기 점검을 한다."""
    adb = settings.get("tools.adb") or "adb"
    try:
        subprocess.run([adb, "start-server"], timeout=60, capture_output=True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log(f"adb start-server 실패: {exc}")
    perfkit(["device", "doctor", "--device", device], check=False)


@dataclass
class AbabResult:
    sessions: list[str]
    reruns: int


def run_abab(base: Built, cand: Built, *, device: str, rounds: int, scenario: str | None, seconds: int | None,
             session: str, max_reruns: int, extra: list[str], results_dir: Path) -> AbabResult:
    """교차 실행. 관문 무효가 난 라운드 수만큼 한 번 더(최대 max_reruns번) 돌린다."""
    from perfkit.compare import runsets as R

    sessions = []
    need = rounds
    for attempt in range(max_reruns + 1):
        name = session if attempt == 0 else f"{session}-r{attempt}"
        args = ["run", "abab", "--device", device, "--a", str(base.apk), "--b", str(cand.apk), "--rounds", str(need),
                "--session", name, "--order-seed", str(attempt + 1)]
        if scenario:
            args += ["--scenario", scenario]
        if seconds:
            args += ["--seconds", str(seconds)]
        proc = perfkit(args + extra, check=False)
        if proc.returncode not in (0,):
            raise PipelineError(f"교차 실행 실패(종료 코드 {proc.returncode}, 세션 {name})")
        sessions.append(name)
        side = R.resolve(f"session:{name}", results_dir=results_dir)
        bad_rounds = {r.meta.get("round") for r in side.runs if r.meta.get("valid") is False}
        if not bad_rounds:
            break
        need = len(bad_rounds)
        _log(f"관문 무효 라운드 {sorted(bad_rounds)} → {need}라운드 다시")
    return AbabResult(sessions, len(sessions) - 1)


def run_aa(apk: Path, *, device: str, rounds: int, scenario: str | None, seconds: int | None, session: str,
           extra: list[str]) -> str:
    args = ["run", "--device", device, "--apk", str(apk), "--rounds", str(rounds), "--kind", "aa", "--session", session]
    if scenario:
        args += ["--scenario", scenario]
    if seconds:
        args += ["--seconds", str(seconds)]
    proc = perfkit(args + extra, check=False)
    if proc.returncode != 0:
        raise PipelineError(f"A/A 실행 실패(종료 코드 {proc.returncode})")
    return session


def session_name(base: Built, cand: Built, scenario: str | None) -> str:
    return f"{datetime.now():%Y%m%d-%H%M}-{base.short}-vs-{cand.short}-{scenario or 'default'}".lower()


def glob_match(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in patterns)
