"""실기기 준비·되돌리기·식힘 관문.

prep: 비행기 모드, 밝기 고정(자동 끔), 화면 켜짐 유지·꺼짐 시간, 게임 모드(standard), 고정 성능 모드(선택).
바꾸기 전 값을 상태 파일에 남기고 restore가 그 값으로 되돌린다. **기기 소리·음량은 건드리지 않는다.**
wait-cool: 열 상태·AP·SKIN 온도가 문턱 아래로 내려올 때까지 기다린다. 화면이 꺼지거나 시간이 넘으면 멈추고
사람을 부른다(종료 코드 3).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from perfkit.device import parse as P
from perfkit.device.adb import Adb

SETTINGS = {
    # 키: (namespace, 이름)
    "brightness_mode": ("system", "screen_brightness_mode"),
    "brightness": ("system", "screen_brightness"),
    "airplane": ("global", "airplane_mode_on"),
    "stay_on": ("global", "stay_on_while_plugged_in"),
    "screen_off_timeout": ("system", "screen_off_timeout"),
}


def read_state(adb: Adb, package: str | None = None) -> dict:
    st = {}
    for key, (ns, name) in SETTINGS.items():
        v = adb.shell(f"settings get {ns} {name}", timeout=20).strip()
        st[key] = None if v in ("", "null") else v
    if package:
        st["game_mode_raw"] = _game_mode(adb, package)
    return st


def _game_mode(adb: Adb, package: str) -> str | None:
    out = adb.shell(f"cmd game list-modes {package} 2>&1", timeout=20).strip()
    if not out or "Unknown command" in out or "No shell command" in out:
        return None
    return out.splitlines()[0][:200]


def _put(adb: Adb, key: str, value) -> None:
    ns, name = SETTINGS[key]
    if value is None:
        adb.shell(f"settings delete {ns} {name}", timeout=20)
    else:
        adb.shell(f"settings put {ns} {name} {value}", timeout=20)


def _airplane(adb: Adb, on: bool) -> None:
    r = adb.shell(f"cmd connectivity airplane-mode {'enable' if on else 'disable'} 2>&1", timeout=30)
    if "Unknown" in r or "Error" in r or "rror" in r:
        _put(adb, "airplane", "1" if on else "0")
        adb.shell(f"am broadcast -a android.intent.action.AIRPLANE_MODE --ez state {'true' if on else 'false'}",
                  timeout=30)


def state_path(results: Path, device: str) -> Path:
    return results / "device-state" / f"{device}.prep.json"


def prep(adb: Adb, cfg: dict, package: str | None, state_file: Path, now=None) -> dict:
    """기기 준비. 이미 준비 상태 파일이 있으면 원래 값을 덮지 않는다(두 번 prep해도 restore가 처음 값으로)."""
    p = cfg.get("prep") or {}
    if state_file.exists():
        saved = json.loads(state_file.read_text(encoding="utf-8"))
        original = saved["original"]
    else:
        original = read_state(adb, package)
    applied = []
    if p.get("airplane"):
        _airplane(adb, True)
        applied.append("airplane")
    if p.get("brightness") is not None:
        _put(adb, "brightness_mode", 0)
        _put(adb, "brightness", int(p["brightness"]))
        applied.append("brightness")
    if p.get("stay_on"):
        adb.shell("svc power stayon true", timeout=20)
        _put(adb, "screen_off_timeout", int(p.get("screen_off_timeout_ms", 1800000)))
        applied.append("stay_on")
    if p.get("game_mode") and package:
        adb.shell(f"cmd game mode {p['game_mode']} {package} 2>&1", timeout=20)
        applied.append("game_mode")
    fixed = bool(p.get("fixed_perf"))
    if fixed:
        adb.shell("cmd power set-fixed-performance-mode-enabled true 2>&1", timeout=20)
        applied.append("fixed_perf")
    doc = {"device": cfg.get("name"), "serial": adb.serial, "package": package, "original": original,
           "applied": applied, "fixed_perf": fixed, "game_mode": p.get("game_mode"),
           "brightness": p.get("brightness"), "at": now or time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return doc


def restore(adb: Adb, state_file: Path) -> dict:
    """상태 파일의 처음 값으로 되돌리고 다시 읽어 비교한다. 같으면 상태 파일을 지운다."""
    doc = json.loads(state_file.read_text(encoding="utf-8"))
    orig = doc["original"]
    applied = doc.get("applied", [])
    if "fixed_perf" in applied:
        adb.shell("cmd power set-fixed-performance-mode-enabled false 2>&1", timeout=20)
    if "game_mode" in applied and doc.get("package"):
        # 원래 모드를 정확히 모르면 standard로 둔다(앱 기본값)
        adb.shell(f"cmd game mode standard {doc['package']} 2>&1", timeout=20)
    if "stay_on" in applied:
        _put(adb, "stay_on", orig.get("stay_on"))
        _put(adb, "screen_off_timeout", orig.get("screen_off_timeout"))
    if "brightness" in applied:
        _put(adb, "brightness", orig.get("brightness"))
        _put(adb, "brightness_mode", orig.get("brightness_mode"))
    if "airplane" in applied:
        _airplane(adb, orig.get("airplane") == "1")
    now = read_state(adb)
    diff = {k: (orig.get(k), now.get(k)) for k in SETTINGS if orig.get(k) != now.get(k)}
    if not diff:
        state_file.unlink()
    return {"restored": applied, "diff": diff, "same": not diff}


# --------------------------------------------------------------------------- 식힘

@dataclass
class CoolResult:
    ok: bool
    waited_s: float
    reason: str | None = None
    last: dict = field(default_factory=dict)


def thermal_snapshot(adb: Adb, cfg: dict) -> dict:
    th = P.thermalservice(adb.shell("dumpsys thermalservice", timeout=30))
    bt = P.battery(adb.shell("dumpsys battery", timeout=30))
    names = (cfg.get("thermal") or {}).get("sensors") or {}
    temps = {k: th["temps"].get(v) for k, v in names.items()}
    if temps.get("bat") is None and bt.get("temp_c") is not None:
        temps["bat"] = bt["temp_c"]
    return {"status": th["status"], "temps": temps, "battery": bt}


def screen_awake(adb: Adb) -> bool | None:
    out = adb.shell("dumpsys power | grep -E 'mWakefulness=|mHoldingDisplaySuspendBlocker'", timeout=20)
    if "mWakefulness=" not in out:
        return None
    return "mWakefulness=Awake" in out


def wait_cool(adb: Adb, cfg: dict, *, max_ap=None, max_skin=None, max_status=None, timeout_s=None, poll_s=None,
              clock=time.time, sleep=time.sleep, log=print) -> CoolResult:
    c = cfg.get("cool") or {}
    max_ap = float(max_ap if max_ap is not None else c.get("max_ap", 38.0))
    max_skin = float(max_skin if max_skin is not None else c.get("max_skin", 35.5))
    max_status = int(max_status if max_status is not None else c.get("max_status", 0))
    timeout_s = float(timeout_s if timeout_s is not None else c.get("timeout_s", 1800))
    poll_s = float(poll_s if poll_s is not None else c.get("poll_s", 15))
    t0 = clock()
    last: dict = {}
    while True:
        last = thermal_snapshot(adb, cfg)
        ap, skin, st = last["temps"].get("ap"), last["temps"].get("skin"), last["status"]
        hot = []
        if st is not None and st > max_status:
            hot.append(f"열 상태 {st} > {max_status}")
        if ap is not None and ap > max_ap:
            hot.append(f"AP {ap} > {max_ap}")
        if skin is not None and skin > max_skin:
            hot.append(f"SKIN {skin} > {max_skin}")
        if not hot:
            if st is None and ap is None and skin is None:
                return CoolResult(False, round(clock() - t0, 1), "열 값을 못 읽음(dumpsys thermalservice)", last)
            return CoolResult(True, round(clock() - t0, 1), None, last)
        if screen_awake(adb) is False:
            return CoolResult(False, round(clock() - t0, 1), "화면이 꺼짐·잠김: 사람이 화면을 켜고 잠금을 풀어야 함", last)
        if clock() - t0 >= timeout_s:
            return CoolResult(False, round(clock() - t0, 1), f"식힘 시간 초과 {timeout_s:.0f}s: " + ", ".join(hot), last)
        log(f"wait-cool: {', '.join(hot)} — {poll_s:.0f}s 뒤 다시 봄")
        sleep(poll_s)
