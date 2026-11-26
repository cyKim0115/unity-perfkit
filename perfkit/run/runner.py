"""회차 하나: 설치 → 세이브 주입 → 실행 → 끝 신호 → 수집 → 회차 폴더(meta·result·원자료).

끝 신호는 셋 중 먼저 오는 것: logcat `[PerfRun] END`, 기기의 회차 CSV 생김(크기가 잠시 그대로), 앱 종료(실패).
logcat 스트림은 끊기면 다시 붙고(`LogcatTail`), 재연결 수는 관문에 쓴다. 측정 모드 BEGIN이 일정 시간 안에
안 오면 호환 모드(측정 모드 없는 APK): 실행 뒤 `seconds`초 동안 기기 밖 지표(proc·meminfo·시작 시간)만 잰다.

회차 폴더 `<결과>/runs/<run_id>/`
  meta.json            run-meta 스키마(관문 결과 valid·invalid_reason 포함)
  result.json          result 스키마(층 L3-E·L3-D, 매니페스트 지표 키)
  logcat.txt           logcat -v epoch 전체(재연결 표시 ##PERFKIT_LOGCAT_RESTART)
  frames.csv           게임이 쓴 프레임 CSV(측정 모드)
  device_samples.csv   proc·sysfs 샘플(interval초)
  meminfo.txt          구간 밖 dumpsys meminfo
  am_start.txt, timestats.txt(켰을 때), run.log(perfkit 진행 기록)
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import perfkit
from perfkit.device import devcfg as DC
from perfkit.device import emulator as EM
from perfkit.device import host as H
from perfkit.device import parse as P
from perfkit.device import prep as PR
from perfkit.device.adb import Adb, LogcatTail
from perfkit.device.sampler import Sampler, rows as sample_rows, write_rows
from perfkit.run import metrics as M
from perfkit.run import probe as PB
from perfkit.run.apkinfo import apk_info, build_info

RUN_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{4}-[0-9A-Za-z]{8}-[a-z0-9][a-z0-9-]*-[0-9]+$")


class Refused(RuntimeError):
    """회차를 시작하지 않음(바쁜 호스트, AVD 설정 다름, 남의 임대 등). exit code를 함께."""

    def __init__(self, msg: str, code: int = 4):
        super().__init__(msg)
        self.code = code


@dataclass
class RunSpec:
    device: str
    apk: Path | None
    scenario: str
    seconds: int = 60
    seed: int = 1
    save: str = "light"
    session: str | None = None
    arm: str | None = "A"
    round: int = 1
    kind: str = "numeric"
    install: bool = True
    inject_save: bool = True
    extra_args: str = ""
    package: str | None = None
    build_hash: str | None = None
    commit: str | None = None
    label: str | None = None
    build_info: str | None = None
    variant: str | None = None
    quit_after_end: bool | None = None
    freeze: bool | None = None
    run_id: str | None = None
    notes: str | None = None
    gate: dict | None = None            # 관문 문턱 덮기(시험·진단)
    avd_mismatch: str | None = None     # refuse|warn


@dataclass
class Outcome:
    run_dir: Path
    run_id: str
    valid: bool
    reasons: list[str]
    end_signal: str
    compat: bool
    meta: dict
    result: dict
    schema_errors: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        if self.schema_errors:
            return 6
        return 0 if self.end_signal in ("end", "csv", "compat") else 2


class Env:
    """회차가 쓰는 바깥 것들. 시험에서는 adb·호스트 측정·시계를 바꾼다."""

    def __init__(self, *, settings=None, adb: Adb | None = None, probe: H.HostProbe | None = None,
                 results: Path | None = None, log=None, clock=time.time, sleep=time.sleep, emulab=None,
                 avd_home: Path | None = None, sdk: str | None = None, runner=None,
                 lock_dir: Path | None = None):
        from perfkit.config import settings as get_settings
        from perfkit.paths import results_dir

        self.s = settings or get_settings()
        self.adb = adb
        self.probe = probe or H.default_probe()
        self.results = Path(results) if results else results_dir()
        self.log = log or (lambda m: print(m, flush=True))
        self.clock = clock
        self.sleep = sleep
        self.emulab = emulab
        self.avd_home = avd_home
        self.sdk = sdk if sdk is not None else self.s.get("tools.android_sdk")
        self.runner = runner
        self.lock_dir = lock_dir


# --------------------------------------------------------------------------- 도움 함수

def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def make_run_id(results: Path, build8: str, scenario: str, when: datetime | None = None) -> str:
    when = when or datetime.now()
    prefix = f"{when:%Y%m%d-%H%M}-{build8}-{scenario}-"
    runs = results / "runs"
    n = 1
    if runs.exists():
        for d in runs.iterdir():
            if d.name.startswith(prefix):
                tail = d.name[len(prefix):]
                if tail.isdigit():
                    n = max(n, int(tail) + 1)
    return prefix + str(n)


def build8_of(spec_hash: str | None, apk_sha: str | None) -> str:
    v = re.sub(r"[^0-9A-Za-z]", "", spec_hash or "")[:8]
    if len(v) == 8:
        return v
    return (apk_sha or "0" * 8)[:8]


def quality_hash(settle: dict | None) -> str | None:
    if not settle:
        return None
    keys = ("graphics", "targetFps", "vSync", "quality", "screen")
    sub = {k: settle.get(k) for k in keys if k in settle}
    if not sub:
        return None
    return hashlib.sha256(json.dumps(sub, sort_keys=True).encode()).hexdigest()[:16]


def unity_args(spec: RunSpec, run_id: str, run_cfg: dict) -> str:
    quit_ = spec.quit_after_end if spec.quit_after_end is not None else bool(run_cfg.get("quit_after_end"))
    parts = [f"-perfScenario {spec.scenario}", f"-perfSeconds {int(spec.seconds)}", f"-perfSeed {int(spec.seed)}",
             f"-perfSave {spec.save}", f"-perfRunId {run_id}", f"-perfQuit {1 if quit_ else 0}"]
    if spec.freeze is not None:
        parts.append(f"-perfFreeze {1 if spec.freeze else 0}")
    extra = " ".join(x for x in (run_cfg.get("extra_args") or "", spec.extra_args or "") if x)
    if extra:
        parts.append(extra)
    s = " ".join(parts)
    if "'" in s:
        raise ValueError("실행 인자에 작은따옴표를 쓸 수 없음")
    return s


class Live:
    """logcat을 받는 동안의 상태(받는 스레드가 고친다)."""

    def __init__(self, clock):
        self.lock = threading.Lock()
        self.log = PB.ProbeLog()
        self.offset: float | None = None   # 호스트 시각 - 기기 epoch (최소)
        self.begin_host: float | None = None
        self.end_host: float | None = None
        self.measure_host: float | None = None
        self.clock = clock
        self.on_measure = None

    def on_line(self, text: str, host_t: float) -> None:
        ln = PB.parse_line(text)
        if ln is None:
            return
        with self.lock:
            d = host_t - ln.epoch
            if self.offset is None or d < self.offset:
                self.offset = d
            kind = PB.feed(self.log, ln)
            if kind == "begin":
                self.begin_host = host_t
            elif kind == "phase":
                ph = self.log.phases[-1]
                if ph.get("measure") and self.measure_host is None:
                    self.measure_host = host_t
                    cb = self.on_measure
                else:
                    cb = None
            elif kind == "end":
                self.end_host = host_t
        if kind == "phase" and cb:
            cb()


def _logcat_buffer_size(adb: Adb) -> str | None:
    out = adb.run("logcat", "-g", timeout=20).stdout
    m = re.search(r"ring buffer is ([\d.]+)\s*([KMG]i?B)", out)
    if not m:
        return None
    unit = m.group(2)[0]
    return f"{int(float(m.group(1)))}{unit}"


# --------------------------------------------------------------------------- 회차

def run_once(spec: RunSpec, env: Env) -> Outcome:
    s = env.s
    dev = DC.device_config(spec.device, s)
    run_cfg = DC.run_config(s)
    host_cfg = DC.host_config(s, dev)
    kind = dev["kind"]
    layer = "L3-E" if kind == "emulator" else "L3-D"
    gate = DC.gate_config(layer, s, dev)
    if spec.gate:
        gate.update(spec.gate)
    adb = env.adb
    log_lines: list[str] = []

    def log(msg: str) -> None:
        line = f"{datetime.now():%H:%M:%S} {msg}"
        log_lines.append(line)
        env.log(line)

    # ---- 시작 전 거부 조건
    busy = H.busy_reasons(env.probe, host_cfg)
    if busy:
        raise Refused("호스트가 바쁨(회차를 시작하지 않음): " + "; ".join(busy), 4)
    st = adb.run("get-state", timeout=20)
    if st.stdout.strip() != "device":
        raise Refused(f"기기 {adb.serial} 상태가 device가 아님: {(st.stdout + st.stderr).strip()[:200]}", 3)

    apk = Path(spec.apk) if spec.apk else None
    ai = apk_info(apk) if apk else {}
    bi = build_info(apk, run_cfg.get("build_info"), spec.build_info) if apk else {}
    package = DC.package_name(run_cfg, s, ai.get("package"), spec.package)
    activity = run_cfg["activity"]
    files = run_cfg["remote_files"].format(package=package)
    perf_dir = run_cfg["remote_perf_dir"].format(files=files, package=package)
    saves_root = run_cfg["remote_saves_dir"].format(files=files, package=package)

    emu = None
    avd_issues: list[str] = []
    if kind == "emulator":
        avd = dev.get("avd")
        if not avd:
            raise Refused(f"기기 {spec.device}: avd가 설정에 없음", 3)
        emu = EM.describe(adb, avd, sdk=env.sdk, home=env.avd_home, **({"runner": env.runner} if env.runner else {}))
        avd_issues = EM.mismatches(emu, dev.get("expect") or {})
        mode = spec.avd_mismatch or dev.get("avd_mismatch") or "refuse"
        if avd_issues and mode == "refuse":
            raise Refused("AVD 설정이 기준과 다름: " + "; ".join(avd_issues), 5)

    commit = spec.commit or bi.get("commit")
    build_hash = spec.build_hash or bi.get("sha") or (commit[:8] if commit else None) or (ai.get("sha256") or "")[:8]
    b8 = build8_of(build_hash, ai.get("sha256"))
    run_id = spec.run_id or make_run_id(env.results, b8, spec.scenario)
    if not RUN_ID_RE.match(run_id):
        raise Refused(f"run_id 형식이 아님: {run_id}", 2)
    run_dir = env.results / "runs" / run_id
    if run_dir.exists():
        raise Refused(f"회차 폴더가 이미 있음: {run_dir}", 2)
    run_dir.mkdir(parents=True)
    try:
        return _run_in_dir(spec, env, s, dev, run_cfg, host_cfg, kind, layer, gate, adb, log_lines, log, apk, ai, bi,
                           package, activity, perf_dir, saves_root, emu, avd_issues, commit, build_hash, b8,
                           run_id, run_dir)
    except Refused:
        import shutil

        if not (run_dir / "meta.json").exists():
            ref = env.results / "refused"
            ref.mkdir(parents=True, exist_ok=True)
            (ref / f"{run_id}.log").write_text("\n".join(log_lines) + "\n", encoding="utf-8")
            shutil.rmtree(run_dir, ignore_errors=True)
        raise


def _run_in_dir(spec, env, s, dev, run_cfg, host_cfg, kind, layer, gate, adb, log_lines, log, apk, ai, bi, package,
                activity, perf_dir, saves_root, emu, avd_issues, commit, build_hash, b8, run_id, run_dir):
    session = spec.session or f"{datetime.now():%Y%m%d-%H%M}-{spec.scenario}-{spec.kind}"
    started_at = _now_iso()
    log(f"run {run_id} device={spec.device}({kind}) serial={adb.serial} package={package} session={session}")

    # ---- 실기기 시작 상태
    dev_start: dict = {}
    if kind == "device":
        try:
            snap = PR.thermal_snapshot(adb, dev)
            dev_start = {"thermal_start": snap["status"], "ap_c_start": snap["temps"].get("ap"),
                         "batt_c_start": snap["temps"].get("bat"), "batt_pct": snap["battery"].get("level"),
                         "charging": snap["battery"].get("charging")}
        except Exception as exc:
            log(f"warn: 열·배터리 시작 값을 못 읽음: {exc}")
        prep_file = PR.state_path(env.results, spec.device)
        prep_doc = json.loads(prep_file.read_text(encoding="utf-8")) if prep_file.exists() else {}
        dev_start.update({"fixed_perf": bool(prep_doc.get("fixed_perf")) if prep_doc else None,
                          "game_mode": prep_doc.get("game_mode") if prep_doc else None,
                          "brightness": prep_doc.get("brightness") if prep_doc else None})
        if not prep_doc:
            log("warn: device prep 기록 없음(밝기·비행기 모드·게임 모드를 고정하지 않은 회차)")

    # ---- 앱 정리·설치·세이브
    def force_stop():
        for _ in range(5):
            adb.shell(f"am force-stop {package}", timeout=30)
            if adb.pidof(package) is None:
                return True
            env.sleep(1.0)
        return False

    force_stop()
    install_s = None
    if apk and spec.install:
        t = env.clock()
        r = adb.run("install", *list(run_cfg.get("install_args") or ["-r"]), str(apk),
                    timeout=float(run_cfg["install_timeout_s"]))
        install_s = round(env.clock() - t, 1)
        if "Success" not in (r.stdout + r.stderr):
            msg = (r.stdout + r.stderr).strip()[-300:]
            if "VERSION_DOWNGRADE" in msg:
                msg += " → 옛 빌드를 덮으려면 디버그 가능 기기·앱이어야 함(-d). 아니면 기기에서 앱을 지운 뒤 다시"
            raise Refused(f"설치 실패: {msg}", 3)
        log(f"install -r {apk.name} {install_s}s")
    pushed = 0
    save_files = []
    if spec.inject_save and spec.save and spec.save != "current":
        src = DC.resolve_path(run_cfg.get("saves_dir"), s)
        src = (src / spec.save) if src else None
        if not src or not src.is_dir():
            raise Refused(f"세이브 폴더가 없음: {src} (프로필 [run] saves_dir)", 3)
        dst = f"{saves_root}/{spec.save}"
        adb.shell(f"rm -rf {dst}; mkdir -p {dst}", timeout=30)
        for f in sorted(src.iterdir()):
            if f.is_file():
                rr = adb.run("push", str(f), f"{dst}/{f.name}", timeout=120)
                if not rr.ok:
                    raise Refused(f"세이브 넣기 실패 {f.name}: {(rr.stdout + rr.stderr).strip()[-200:]}", 3)
                pushed += 1
                save_files.append(f.name)
        log(f"save {spec.save}: {pushed} files → {dst}")
    adb.shell(f"mkdir -p {perf_dir}; rm -f {perf_dir}/{run_id}.csv", timeout=30)

    # ---- logcat·timestats
    orig_buf = _logcat_buffer_size(adb)
    if run_cfg.get("logcat_buffer"):
        adb.run("logcat", "-G", str(run_cfg["logcat_buffer"]), timeout=20)
    adb.run("logcat", "-c", timeout=20)
    use_ts = bool(dev.get("timestats"))
    if use_ts:
        adb.shell("dumpsys SurfaceFlinger --timestats -clear -enable", timeout=30)

    # ---- 측정 전 호스트
    images_emu = list(host_cfg.get("emulator_images") or [])
    images_ed = list(host_cfg.get("editor_images") or [])
    idle_s = float(host_cfg.get("idle_seconds", 10))
    before = H.measure_idle(env.probe, images_emu + images_ed, idle_s, sleep=env.sleep)
    log(f"host before {idle_s:.0f}s: busy={before.busy_pct}% editor={before.pct(images_ed)}%")

    live = Live(env.clock)
    host_during: dict = {}

    def start_during():
        host_during["w"] = H.HostWindow(env.probe, images_emu + images_ed)
        host_during["t"] = env.clock()

    live.on_measure = start_during
    tail = LogcatTail(adb, run_dir / "logcat.txt", buffers=run_cfg.get("logcat_buffers", "main,system,crash"),
                      on_line=live.on_line, clock=env.clock, sleep=env.sleep).start()
    env.sleep(0.5)

    # ---- 실행
    args = unity_args(spec, run_id, run_cfg)
    t_launch = env.clock()
    am = adb.run("shell", f"am start -W -n {package}/{activity} -e unity {shlex.quote(args)}", timeout=180)
    (run_dir / "am_start.txt").write_text(am.stdout + am.stderr, encoding="utf-8")
    am_d = P.am_start(am.stdout)
    log(f"am start: {am_d}")
    pid = None
    for _ in range(60):
        pid = adb.pidof(package)
        if pid:
            break
        env.sleep(0.5)
    if not pid:
        tail.stop()
        _restore_logcat(adb, orig_buf)
        raise Refused(f"앱 프로세스가 뜨지 않음: {am.stdout.strip()[-300:]}", 2)
    interval = float(run_cfg.get("sample_interval_s", 5.0))
    sampler = Sampler(adb, pid, dev, interval, clock=env.clock).start()

    # ---- 끝 기다리기
    seconds = float(spec.seconds)
    probe_wait = float(run_cfg.get("probe_wait_s", 120))
    load_timeout = float(run_cfg.get("load_timeout_s", 600))
    stable = float(run_cfg.get("csv_stable_s", 2.0))
    remote_csv = f"{perf_dir}/{run_id}.csv"
    end_signal = "timeout"
    compat = False
    last_size, last_change = None, None
    alive = True
    while True:
        now = env.clock()
        with live.lock:
            has_begin = live.log.begin is not None
            has_end = live.log.end is not None
        if has_end:
            end_signal = "end"
            break
        size = adb.file_size(remote_csv)
        if size:
            if size != last_size:
                last_size, last_change = size, now
            elif now - last_change >= stable:
                end_signal = "csv"
                break
        if adb.pidof(package) is None:
            alive = False
            env.sleep(1.0)
            size = adb.file_size(remote_csv)
            end_signal = "csv" if size else "app_exit"
            break
        if not has_begin and now - t_launch > probe_wait:
            compat = True
            break
        if has_begin and live.begin_host and now - live.begin_host > seconds + load_timeout:
            end_signal = "timeout"
            break
        env.sleep(1.0)

    compat_window = None
    if compat:
        log(f"측정 모드 BEGIN이 {probe_wait:.0f}s 안에 없음 → 호환 모드(기기 밖 지표만 {seconds:.0f}s)")
        start_during()
        t0 = env.clock()
        while env.clock() - t0 < seconds:
            if adb.pidof(package) is None:
                alive = False
                break
            env.sleep(min(2.0, seconds))
        compat_window = (t0, env.clock())
        end_signal = "compat" if alive else "app_exit"

    t_end = env.clock()
    during = host_during["w"].end() if "w" in host_during else None
    sampler.stop(final=alive)
    log(f"end signal={end_signal} after {t_end - t_launch:.1f}s")

    # ---- 구간 밖
    meminfo_text = None
    quit_ = spec.quit_after_end if spec.quit_after_end is not None else bool(run_cfg.get("quit_after_end"))
    if alive and not quit_ and adb.pidof(package):
        meminfo_text = adb.shell(f"dumpsys meminfo {package}", timeout=60)
        (run_dir / "meminfo.txt").write_text(meminfo_text, encoding="utf-8")
    if quit_:
        t0 = env.clock()
        while adb.pidof(package) and env.clock() - t0 < float(run_cfg.get("after_end_wait_s", 20)):
            env.sleep(1.0)
    force_stop()
    if use_ts:
        ts = adb.shell("dumpsys SurfaceFlinger --timestats -dump", timeout=60)
        (run_dir / "timestats.txt").write_text(ts, encoding="utf-8")
        adb.shell("dumpsys SurfaceFlinger --timestats -disable -clear", timeout=30)
    env.sleep(1.5)
    tail.stop()
    _restore_logcat(adb, orig_buf)

    # ---- CSV 받기·기기 정리
    csv_local = run_dir / "frames.csv"
    end_csv = None
    with live.lock:
        if live.log.end and isinstance(live.log.end.get("csv"), dict):
            end_csv = live.log.end["csv"].get("path")
    pulled = False
    for path in [p for p in (end_csv, remote_csv) if p]:
        rr = adb.run("pull", path, str(csv_local), timeout=120)
        if rr.ok and csv_local.exists():
            pulled = True
            break
    adb.shell(f"rm -f {remote_csv}", timeout=30)
    if spec.inject_save and spec.save and spec.save != "current":
        adb.shell(f"rm -rf {saves_root}/{spec.save}", timeout=30)

    # ---- 해석
    plog, lines = PB.parse_logcat_file(run_dir / "logcat.txt")
    frames = PB.parse_frames_file(csv_local) if pulled else []
    app_pids = {pid}
    if plog.app_pid:
        app_pids.add(plog.app_pid)
    for ln in lines:
        m = re.search(r"Start proc (\d+):" + re.escape(package) + r"[/ ]", ln.msg)
        if m:
            app_pids.add(int(m.group(1)))
    summary = PB.app_log_summary(lines, app_pids, package)
    restarts = tail.restarts

    # 단계 시간표(호스트 시각)
    offset = live.offset
    timeline: list[tuple[float, str]] = []
    if plog.phases and offset is not None:
        timeline = [(p["epoch"] + offset, p["name"]) for p in plog.phases]
        end_host = (plog.end_epoch + offset) if plog.end_epoch else t_end
    elif frames:
        first: dict[str, float] = {}
        for f in frames:
            ph = f.get("phase")
            if ph and ph not in first and f.get("tReal") is not None:
                first[ph] = f["tReal"]
        timeline = sorted((t_launch + t, n) for n, t in first.items())
        end_host = t_end
    else:
        end_host = t_end
    if compat_window:
        timeline = [(compat_window[0], "compat")]
        end_host = compat_window[1]

    def phase_at(t: float) -> str | None:
        if not timeline or t < timeline[0][0]:
            return "launch"
        if t > end_host + 0.5:
            return "after_end"
        cur = None
        for ts_, name in timeline:
            if ts_ <= t:
                cur = name
        return cur

    srows = sample_rows(sampler.samples, _clk_tck(adb), dev.get("thread_groups") or {}, t_launch, phase_at)
    write_rows(run_dir / "device_samples.csv", srows)
    names = {"compat"} if compat else (set(plog.measure_phases()) or M.measure_names(plog, frames))

    # ---- 결과 항목
    items: dict = {}
    if not compat:
        items.update(M.frame_items(plog, frames, kind))
        items.update(M.stats_items(plog, kind, srows))
        items.update(M.event_items(plog, tuple(run_cfg.get("scene_load_phases") or M.SCENE_LOAD)))
    items.update(M.load_items(plog, am_d))
    items.update(M.sample_items(srows, names, kind))
    items.update(M.meminfo_items(P.meminfo(meminfo_text) if meminfo_text else None,
                                 None if meminfo_text else "앱이 끝난 뒤라 meminfo를 못 잡음"))
    items.update(M.log_items(summary))

    from perfkit.manifest import load_effective

    man = load_effective(s)
    allowed = {it["id"]: it for it in man.items_for(layer)}
    result_items = {}
    for iid, v in items.items():
        it = allowed.get(iid)
        if not it:
            continue
        cols = [it["by_layer"][layer].get("collector", "")] if layer in it.get("by_layer", {}) else []
        if any(c.startswith(("editor.", "static.")) for c in cols) and not any(c.startswith(("device.", "run.")) for c in cols):
            continue
        result_items[iid] = v
    for iid, it in allowed.items():
        col = (it.get("by_layer", {}).get(layer) or {}).get("collector") or ""
        if iid not in result_items and col.startswith(("device.", "run.")):
            why = ("호환 모드: 측정 모드 로그·프레임 CSV 없음(기기 밖 지표만)" if compat
                   else "perfkit run 수집기 미구현 또는 이 회차에서 안 잼")
            result_items[iid] = {"status": "not_collected", "reason": why, "collector": col.split(",")[0]}

    # ---- 관문
    reasons: list[str] = []
    end = plog.end or {}
    if compat:
        pass
    elif end_signal not in ("end", "csv"):
        reasons.append(f"회차가 끝까지 가지 않음({end_signal})")
    elif end and end.get("result") not in (None, "ok"):
        reasons.append(f"게임 시나리오 실패: {end.get('reason')}")
    if not compat and not pulled:
        reasons.append("프레임 CSV를 받지 못함")
    host_doc = None
    if kind == "emulator":
        info = env.probe.info()
        busy_before = before.busy_pct
        busy_during = during.busy_pct if during else None
        other = during.pct(images_ed) if during else None
        host_doc = {"name": info.get("name"), "cpu": info.get("cpu") or "unknown", "os": info.get("os") or "unknown",
                    "gpu": info.get("gpu") or "unknown",
                    "cpu_busy_before": busy_before if busy_before is not None else 0.0,
                    "cpu_busy_during": busy_during if busy_during is not None else 0.0,
                    "idle_gate_threshold": gate.get("host_busy_during_max"),
                    "other_unity_pct": other, "other_unity_before_pct": before.pct(images_ed),
                    "editors_running": during.count(images_ed) if during else before.count(images_ed),
                    "emulator_pct": during.pct(images_emu) if during else None,
                    "during_seconds": during.seconds if during else None, "idle_seconds": before.seconds,
                    "measured": busy_before is not None and busy_during is not None}
        if busy_before is None or busy_during is None:
            log("warn: 호스트 CPU를 못 잼(이 OS에서 지원 안 함) → 관문 「확인 못 함」")
        else:
            if busy_before > float(gate.get("host_busy_before_max", 1e9)):
                reasons.append(f"측정 전 호스트 CPU {busy_before}% > {gate['host_busy_before_max']}%")
            if busy_during > float(gate.get("host_busy_during_max", 1e9)):
                reasons.append(f"수집 중 호스트 CPU {busy_during}% > {gate['host_busy_during_max']}%")
            if other is not None and other > float(gate.get("other_unity_pct_max", 1e9)):
                reasons.append(f"다른 에디터 프로세스 호스트 점유 {other}% > {gate['other_unity_pct_max']}%")
        if avd_issues:
            reasons.append("AVD 설정 다름: " + "; ".join(avd_issues))
    else:
        cool = dev.get("cool") or {}
        ts0 = dev_start.get("thermal_start")
        if ts0 is not None and ts0 > int(cool.get("max_status", 0)):
            reasons.append(f"시작 열 상태 {ts0} > {cool.get('max_status', 0)}")
    if restarts > int(gate.get("logcat_restarts_max", 0)):
        reasons.append(f"logcat 재연결 {restarts}회 > {gate.get('logcat_restarts_max', 0)}")
    valid = not reasons

    # ---- meta·result
    begin = plog.begin or {}
    from perfkit.manifest import load as load_manifest

    pub = load_manifest()
    variant = spec.variant or ("development" if begin.get("dev") else None) or \
        (bi.get("build") if bi.get("build") in ("release", "development") else None) or "release"
    meta = {
        "schema": "perfkit/run-meta@1",
        "run_id": run_id,
        "build_hash": build_hash if build_hash and len(build_hash) >= 7 else b8,
        "apk_sha": ai.get("sha256") or "0" * 64,
        "version": begin.get("version") or ai.get("version_name") or bi.get("version") or "unknown",
        "build_config": {"variant": variant, "probe": bool(begin.get("perfProbe")) if begin else False,
                         "frame_timing_stats": bool(begin.get("frameTimingStats")) if begin else False,
                         **({"label": spec.label or bi.get("label")} if (spec.label or bi.get("label")) else {}),
                         **({"quality_hash": quality_hash(plog.settle())} if plog.settle() else {})},
        "run_kind": spec.kind,
        "compat_mode": compat,
        "scenario": spec.scenario,
        "seed": int(spec.seed),
        "save": spec.save,
        "arm": spec.arm,
        "round": int(spec.round),
        "started_at": started_at,
        "ended_at": _now_iso(),
        "device_class": kind,
        "device_profile": spec.device,
        "serial": adb.serial or "unknown",
        "os_build": adb.getprop("ro.build.fingerprint") or adb.getprop("ro.build.display.id") or "unknown",
        "probe_hash": begin.get("probeHash") or "none",
        "quality_hash": quality_hash(plog.settle()),
        "manifest_version": pub.version,
        "manifest_sha": pub.sha256,
        "session": session,
        "valid": valid,
        "logcat_restarts": restarts,
        "end_signal": end_signal,
        "package": package,
        "unity_args": args,
        "tool": {"perfkit_version": perfkit.__version__, "adb": adb.exe, "adb_retries": adb.retry_count,
                 "sampler_interval_s": interval, "sampler_errors": sampler.errors, "samples": len(sampler.samples)},
        "timing": {"install_s": install_s, "launch_to_end_s": round(t_end - t_launch, 1),
                   "wall_s": round(env.clock() - t_launch + idle_s, 1)},
        "save_files": len(save_files),
        "game": {k: begin.get(k) for k in ("probe", "saveHash", "saveFiles", "argsSource", "ignored", "unity",
                                            "device", "gpu", "api", "screen", "cpuCount", "memoryMB", "freeze")
                 if k in begin} or None,
        "end": {k: end.get(k) for k in ("result", "reason", "frames", "measuredFrames", "measuredSeconds",
                                        "errors", "exceptions", "firstError") if k in end} or None,
    }
    if commit and re.match(r"^[0-9a-f]{7,40}$", commit):
        meta["commit"] = commit
    if s.profile_name:
        meta["profile_name"] = s.profile_name
    if s.profile_sha:
        meta["profile_sha"] = s.profile_sha
    if spec.notes:
        meta["notes"] = spec.notes
    if bi.get("_path"):
        meta["build_info"] = {k: bi.get(k) for k in ("_path", "branch", "probeCode", "finished") if bi.get(k) is not None}
    if not valid:
        meta["invalid_reason"] = "; ".join(reasons)
    if kind == "emulator":
        meta["emu"] = emu
        meta["host"] = host_doc
        for k in ("thermal_start", "ap_c_start", "batt_c_start", "batt_pct", "charging", "fixed_perf", "game_mode"):
            meta[k] = None
        meta["brightness"] = None
    else:
        for k in ("thermal_start", "ap_c_start", "batt_c_start", "batt_pct", "charging", "fixed_perf", "game_mode",
                  "brightness"):
            meta[k] = dev_start.get(k)
    if meta["quality_hash"] is None:
        meta.pop("quality_hash")

    result = {
        "schema": "perfkit/result@1",
        "layer": layer,
        "device_class": kind,
        "run_id": run_id,
        "manifest_version": pub.version,
        "manifest_sha": pub.sha256,
        "collected_at": meta["ended_at"],
        "source": {"kind": "run", "ref": f"runs/{run_id}", "build_hash": meta["build_hash"],
                   "apk_sha": meta["apk_sha"], **({"commit": meta["commit"]} if meta.get("commit") else {})},
        "tool": {"perfkit_version": perfkit.__version__},
        "items": dict(sorted(result_items.items())),
    }
    if s.profile_name:
        result["profile_name"] = s.profile_name
    if s.profile_sha:
        result["profile_sha"] = s.profile_sha

    from perfkit.schema import validate

    errs = [f"meta {e}" for e in validate(meta, "run-meta")] + [f"result {e}" for e in validate(result, "result")]
    (run_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (run_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log(f"valid={valid} " + (f"reasons={reasons}" if reasons else "") + f" items={len(result_items)}")
    if errs:
        log("schema errors: " + "; ".join(str(e) for e in errs[:10]))
    (run_dir / "run.log").write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    return Outcome(run_dir, run_id, valid, reasons, end_signal, compat, meta, result, [str(e) for e in errs])


def _restore_logcat(adb: Adb, size: str | None) -> None:
    if size:
        adb.run("logcat", "-G", size, timeout=20)


_CLK: dict[str, int] = {}


def _clk_tck(adb: Adb) -> int:
    key = adb.serial or ""
    if key not in _CLK:
        out = adb.shell("getconf CLK_TCK", timeout=20).strip()
        _CLK[key] = int(out) if out.isdigit() else 100
    return _CLK[key]
