"""에뮬레이터: AVD 설정 고정 검사와 임대 도구(emu-lab) 감싸기.

- AVD 설정(시스템 이미지·코어·RAM·GPU 모드·해상도·에뮬레이터 버전·ARM 번역)을 회차 메타 `emu`에 남기고,
  기기 설정 `expect`와 다르면 거부(`avd_mismatch = "refuse"`, 기본) 또는 경고(`"warn"`)한다.
- 임대·부팅·반납은 emu-lab CLI(`start`·`renew`·`release`, `--json`)에 맡긴다. 수집은 perfkit이 adb로 한다.
  emu-lab 쪽 파일은 고치지 않는다.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from perfkit.device import parse as P
from perfkit.device.adb import Adb, Result, subprocess_runner

EXPECT_KEYS = ("system_image", "cores", "ram_mb", "gpu_mode", "resolution", "emulator_version", "arm_translation")


def avd_home(env: dict | None = None) -> Path:
    env = os.environ if env is None else env
    if env.get("ANDROID_AVD_HOME"):
        return Path(env["ANDROID_AVD_HOME"])
    return Path.home() / ".android" / "avd"


def read_avd_config(avd: str, home: Path | None = None) -> dict:
    home = home or avd_home()
    p = home / f"{avd}.avd" / "config.ini"
    if not p.exists():
        ini_file = home / f"{avd}.ini"
        if ini_file.exists():
            path = P.ini(ini_file.read_text(encoding="utf-8", errors="replace")).get("path")
            if path and (Path(path) / "config.ini").exists():
                p = Path(path) / "config.ini"
    if not p.exists():
        raise FileNotFoundError(f"AVD 설정이 없음: {p}")
    return P.ini(p.read_text(encoding="utf-8", errors="replace"))


def emulator_exe(sdk: str | None) -> str | None:
    if not sdk:
        return None
    for name in ("emulator.exe", "emulator"):
        p = Path(sdk) / "emulator" / name
        if p.exists():
            return str(p)
    return None


def emulator_version(sdk: str | None, runner=subprocess_runner) -> str | None:
    exe = emulator_exe(sdk)
    if not exe:
        return None
    r = runner([exe, "-version"], 60)
    return P.emulator_version(r.stdout + r.stderr)


def describe(adb: Adb, avd: str, *, sdk: str | None = None, home: Path | None = None,
             runner=subprocess_runner) -> dict:
    """회차 메타 `emu` 칸. 기기에서 읽는 것(ARM 번역·화면·ABI)과 AVD 파일에서 읽는 것을 합친다."""
    cfg = read_avd_config(avd, home)
    s = P.avd_summary(cfg)
    bridge = adb.getprop("ro.dalvik.vm.native.bridge")
    abilist = adb.getprop("ro.product.cpu.abilist")
    size = P.wm_size(adb.shell("wm size", timeout=20))
    emu = {
        "avd": avd,
        "system_image": s["system_image"],
        "cores": s["cores"],
        "ram_mb": s["ram_mb"],
        "gpu_mode": s["gpu_mode"],
        "resolution": size or s["resolution"],
        "emulator_version": emulator_version(sdk, runner) or "unknown",
        "arm_translation": bool(bridge and bridge != "0") and "arm" in (abilist or ""),
        "abilist": abilist,
        "native_bridge": bridge or None,
        "fingerprint": adb.getprop("ro.build.fingerprint") or None,
    }
    if s.get("density"):
        emu["density"] = s["density"]
    return emu


def mismatches(emu: dict, expect: dict, skip_missing: bool = False) -> list[str]:
    """기대값과 다른 칸. skip_missing이면 emu에 없는 칸(기기를 안 띄워 못 읽은 것)은 건너뛴다."""
    out = []
    for k in EXPECT_KEYS:
        if k not in (expect or {}) or expect[k] in (None, ""):
            continue
        want, got = expect[k], emu.get(k)
        if skip_missing and got is None:
            continue
        if isinstance(want, str) and isinstance(got, str):
            same = want.strip().lower() == got.strip().lower() or (k == "emulator_version" and got.startswith(want))
        else:
            same = want == got
        if not same:
            out.append(f"{k}: 기대 {want!r} ≠ 지금 {got!r}")
    return out


# --------------------------------------------------------------------------- emu-lab

class LeaseError(RuntimeError):
    pass


def emulab_argv(tool: str) -> list[str]:
    """tools.emu_lab 값 → 실행 argv. .js면 node로, .ps1이면 PowerShell로, .cmd면 cmd /c로."""
    t = str(tool)
    low = t.lower()
    if low.endswith(".js") or low.endswith(".mjs") or low.endswith(".ts"):
        return ["node", t]
    if low.endswith(".ps1"):
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", t]
    if low.endswith(".cmd") or low.endswith(".bat"):
        return ["cmd", "/c", t]
    if os.name == "nt" and not any(sep in t for sep in "/\\"):
        return ["cmd", "/c", t]  # PATH의 emu-lab.cmd
    return [t]


class EmuLab:
    """emu-lab CLI. 결과는 마지막 줄 JSON(`--json`)."""

    def __init__(self, tool: str, avd: str | None = None, owner: str | None = None, runner=subprocess_runner,
                 log=None):
        self.base = emulab_argv(tool)
        self.avd = avd
        self.owner = owner
        self.runner = runner
        self.log = log or (lambda s: None)

    def _call(self, *args: str, timeout: float = 600) -> tuple[Result, dict | None]:
        argv = self.base + list(args)
        if self.avd:
            argv += ["--avd", self.avd]
        if self.owner:
            argv += ["--owner", self.owner]
        argv.append("--json")
        r = self.runner(argv, timeout)
        doc = None
        for line in reversed((r.stdout or "").strip().splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    doc = json.loads(line)
                    break
                except json.JSONDecodeError:
                    pass
        self.log(f"emu-lab {' '.join(args)} → rc={r.returncode}")
        return r, doc

    def status(self) -> dict | None:
        r, doc = self._call("status", timeout=120)
        return doc

    def start(self, hours: float | None = None, headless: bool = False) -> dict:
        args = ["start"]
        if hours:
            args += ["--hours", str(hours)]
        if headless:
            args.append("--headless")
        r, doc = self._call(*args, timeout=900)
        if r.returncode != 0 or not doc or not doc.get("serial"):
            msg = (r.stdout + r.stderr).strip().splitlines()
            raise LeaseError("emu-lab start 실패: " + " | ".join(msg[-6:]))
        return doc

    def renew(self, hours: float | None = None) -> dict | None:
        args = ["renew"] + (["--hours", str(hours)] if hours else [])
        r, doc = self._call(*args, timeout=120)
        if r.returncode != 0:
            raise LeaseError("emu-lab renew 실패: " + (r.stdout + r.stderr).strip()[-400:])
        return doc

    def release(self, keep_running: bool = False) -> dict | None:
        args = ["release"] + (["--keep-running"] if keep_running else [])
        r, doc = self._call(*args, timeout=300)
        if r.returncode != 0:
            raise LeaseError("emu-lab release 실패: " + (r.stdout + r.stderr).strip()[-400:])
        return doc


def busy_by_other_lease(status: dict | None) -> str | None:
    """emu-lab status JSON에서 남의 유효한 임대가 있으면 이유."""
    if not status:
        return None
    lease = status.get("lease")
    if not lease:
        return None
    owner = str(lease.get("owner", ""))
    me = str(status.get("owner", ""))
    mine = lease.get("mine")
    if mine is None:
        mine = bool(owner and me and owner.lower() == me.lower())
    if not mine and lease.get("valid", True):
        return f"에뮬레이터 {status.get('avd')}을 {owner}이 임대 중(만료 {lease.get('expiresAt')})"
    return None
