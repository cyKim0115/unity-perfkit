"""adb 출력 파서(기기 없이 시험한다). 입력은 문자열, 출력은 dict·숫자."""

from __future__ import annotations

import re


# --------------------------------------------------------------------------- am start -W

def am_start(text: str) -> dict:
    """`am start -W` → {launch_state, total_ms, wait_ms, this_ms, status, error}."""
    out: dict = {}
    m = re.search(r"LaunchState:\s*(\w+)", text)
    out["launch_state"] = m.group(1) if m else None
    for key, name in (("TotalTime", "total_ms"), ("WaitTime", "wait_ms"), ("ThisTime", "this_ms")):
        m = re.search(key + r":\s*(-?\d+)", text)
        out[name] = int(m.group(1)) if m else None
    m = re.search(r"Status:\s*(\w+)", text)
    out["status"] = m.group(1) if m else None
    m = re.search(r"^Error[^\n]*", text, re.M)
    out["error"] = m.group(0).strip() if m else None
    return out


# --------------------------------------------------------------------------- /proc

def proc_stat_line(line: str) -> tuple[int, str, int] | None:
    """`/proc/<pid>/stat` 한 줄 → (pid, 이름, utime+stime 틱). 이름에 공백·괄호가 있어도 마지막 ')'로 자른다."""
    line = line.strip()
    m = re.match(r"(\d+) \(", line)
    if not m:
        return None
    r = line.rfind(")")
    if r < 0:
        return None
    name = line[m.end():r]
    f = line[r + 2:].split()
    if len(f) < 13:
        return None
    try:
        return int(m.group(1)), name, int(f[11]) + int(f[12])
    except ValueError:
        return None


def cpu_line(line: str) -> list[int] | None:
    """`/proc/stat` 첫 줄 → 칸 목록(user nice system idle iowait irq softirq steal …)."""
    p = line.split()
    if not p or p[0] != "cpu":
        return None
    try:
        return [int(x) for x in p[1:]]
    except ValueError:
        return None


def cpu_busy_pct(a: list[int], b: list[int]) -> float | None:
    if not a or not b:
        return None
    tot = sum(b) - sum(a)
    idle = (b[3] + (b[4] if len(b) > 4 else 0)) - (a[3] + (a[4] if len(a) > 4 else 0))
    return round(100.0 * (tot - idle) / tot, 2) if tot > 0 else None


def loadavg(text: str) -> tuple[float, float, float] | None:
    p = text.split()
    try:
        return float(p[0]), float(p[1]), float(p[2])
    except (IndexError, ValueError):
        return None


def proc_status(text: str) -> dict:
    """`/proc/<pid>/status`의 kB 칸 → MB, Threads → 정수."""
    out = {}
    for k, v in re.findall(r"^(\w+):\s+(\d+)", text, re.M):
        out[k] = int(v)
    return out


# --------------------------------------------------------------------------- dumpsys meminfo

MEMINFO_ROWS = {
    "Native Heap": "native_heap", "Dalvik Heap": "dalvik_heap", "Java Heap": "java_heap", "Code": "code",
    "Stack": "stack", "Graphics": "graphics", "GL mtrack": "gl_mtrack", "EGL mtrack": "egl_mtrack",
    "Gfx dev": "gfx_dev", "Unknown": "unknown", ".so mmap": "so_mmap", "Private Other": "private_other",
    "System": "system", "TOTAL PSS": "total_pss", "TOTAL RSS": "total_rss", "TOTAL SWAP PSS": "total_swap_pss",
}


def meminfo(text: str) -> dict:
    """`dumpsys meminfo <pkg>` → MB 값.

    - App Summary 절의 `Native Heap: 72000` 같은 줄을 먼저 본다.
    - 표 위쪽 `TOTAL  <PSS> <PrivDirty> <PrivClean> …` 줄에서 total_pss·private_dirty를 읽는다.
    - 표 줄(`Native Heap  <Pss> …`)도 앞 칸(PSS)만 읽어 없는 값을 채운다.
    """
    out: dict = {}
    summary = text.split("App Summary", 1)[1] if "App Summary" in text else ""
    for line in summary.splitlines():
        m = re.match(r"\s*([A-Za-z .]+?):\s+(\d+)", line)
        if m and m.group(1).strip() in MEMINFO_ROWS:
            out[MEMINFO_ROWS[m.group(1).strip()]] = round(int(m.group(2)) / 1024, 1)
    table = text.split("App Summary", 1)[0]
    for line in table.splitlines():
        m = re.match(r"\s*TOTAL\s+(\d+)\s+(\d+)\s+(\d+)", line)
        if m:
            out.setdefault("total_pss", round(int(m.group(1)) / 1024, 1))
            out["private_dirty"] = round(int(m.group(2)) / 1024, 1)
            continue
        m = re.match(r"\s*(Native Heap|Dalvik Heap|Unknown|EGL mtrack|GL mtrack|Gfx dev|\.so mmap)\s+(\d+)", line)
        if m:
            out.setdefault(MEMINFO_ROWS[m.group(1)] + "_table", round(int(m.group(2)) / 1024, 1))
    return out


# --------------------------------------------------------------------------- thermal·battery

def thermalservice(text: str) -> dict:
    """`dumpsys thermalservice` → {status, temps: {이름: °C}}. HAL 현재 온도 절(Current temperatures from HAL)을 먼저 본다."""
    out: dict = {"status": None, "temps": {}}
    m = re.search(r"Thermal Status:\s*(\d+)", text)
    if m:
        out["status"] = int(m.group(1))
    part = text
    if "Current temperatures from HAL" in text:
        part = text.split("Current temperatures from HAL", 1)[1]
        part = re.split(r"Current cooling devices|Temperature static thresholds", part, maxsplit=1)[0]
    for m in re.finditer(r"mValue=(-?[\d.]+)[^}]*?mName=([^,}]+)", part):
        name = m.group(2).strip()
        out["temps"].setdefault(name, float(m.group(1)))
    return out


def battery(text: str) -> dict:
    out: dict = {}
    m = re.search(r"^\s*level:\s*(\d+)", text, re.M)
    if m:
        out["level"] = int(m.group(1))
    m = re.search(r"^\s*temperature:\s*(-?\d+)", text, re.M)
    if m:
        out["temp_c"] = int(m.group(1)) / 10.0
    powered = [bool(re.search(rf"^\s*{k} powered:\s*true", text, re.M)) for k in ("AC", "USB", "Wireless", "Dock")]
    if re.search(r"powered:", text):
        out["charging"] = any(powered)
    m = re.search(r"^\s*status:\s*(\d+)", text, re.M)
    if m:
        out["status"] = int(m.group(1))
    return out


# --------------------------------------------------------------------------- AVD·에뮬레이터

def ini(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def emulator_version(text: str) -> str | None:
    m = re.search(r"version\s+([0-9][0-9.]*)\s*(?:\(build_id\s+(\d+)\))?", text)
    if not m:
        return None
    return m.group(1) + (f" (build_id {m.group(2)})" if m.group(2) else "")


def avd_summary(config_ini: dict) -> dict:
    """AVD config.ini → 회차 메타 emu 칸(시스템 이미지·코어·RAM·GPU 모드·해상도)."""
    sysdir = (config_ini.get("image.sysdir.1") or "").replace("\\", "/").strip("/")
    image = ";".join(["system-images"] + sysdir.split("/")[1:]) if sysdir.startswith("system-images") else sysdir
    w, h = config_ini.get("hw.lcd.width"), config_ini.get("hw.lcd.height")

    def num(k):
        v = config_ini.get(k)
        try:
            return int(re.match(r"\d+", v).group(0)) if v else None
        except AttributeError:
            return None

    return {
        "system_image": image or None,
        "cores": num("hw.cpu.ncore"),
        "ram_mb": num("hw.ramSize"),
        "gpu_mode": config_ini.get("hw.gpu.mode"),
        "resolution": f"{w}x{h}" if w and h else None,
        "abi": config_ini.get("abi.type"),
        "density": num("hw.lcd.density"),
    }


def wm_size(text: str) -> str | None:
    m = re.search(r"Override size:\s*(\d+x\d+)", text) or re.search(r"Physical size:\s*(\d+x\d+)", text)
    return m.group(1) if m else None
