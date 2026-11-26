"""호스트 PC 측정: 전체 CPU 사용률, 특정 프로세스(에뮬레이터·에디터)의 호스트 점유율, 바쁜 작업 감지.

에뮬레이터 회차는 호스트 부하를 그대로 탄다. 그래서 회차마다 측정 전(유휴 몇 초)·수집 중 호스트 CPU와
다른 에디터 프로세스의 점유를 남기고, 문턱을 넘긴 회차는 무효(valid=false)로 둔다(관문).

- Windows: `GetSystemTimes`·`GetProcessTimes`(ctypes), 프로세스 목록은 `tasklist`, 명령줄은 PowerShell CIM.
- Linux: `/proc/stat`·`/proc/<pid>/stat`.
- 그 밖: 측정 못 함(None) → 관문은 「확인 못 함」으로 무효 처리하지 않고 경고만.
시험에서는 `HostProbe`를 가짜로 바꾼다.
"""

from __future__ import annotations

import fnmatch
import glob
import os
import platform
import re
import subprocess
import time
from dataclasses import dataclass, field


@dataclass
class HostWindowResult:
    seconds: float
    busy_pct: float | None
    procs_pct: dict[str, float] = field(default_factory=dict)   # 이미지 이름 → 호스트 점유 %(전체 코어 기준)
    procs_count: dict[str, int] = field(default_factory=dict)

    def pct(self, images: list[str]) -> float:
        return round(sum(self.procs_pct.get(i, 0.0) for i in images), 2)

    def count(self, images: list[str]) -> int:
        return sum(self.procs_count.get(i, 0) for i in images)


class HostProbe:
    """플랫폼별 원시 값. 시험에서는 이 클래스를 바꾼다."""

    ncpu = os.cpu_count() or 1

    def system_times(self) -> tuple[int, int] | None:
        """(idle, total) 누적 시간. 단위는 플랫폼마다 다르지만 비율만 쓴다."""
        raise NotImplementedError

    def pids(self, image: str) -> list[int]:
        raise NotImplementedError

    def proc_time_s(self, pid: int) -> float | None:
        """프로세스 누적 CPU 초(커널+사용자)."""
        raise NotImplementedError

    def cmdlines(self, image: str) -> list[tuple[int, str]]:
        return []

    def info(self) -> dict:
        return {"cpu": platform.processor() or platform.machine() or "unknown",
                "os": platform.platform(), "gpu": "unknown", "name": platform.node()}


class WindowsProbe(HostProbe):
    def __init__(self):
        import ctypes
        import ctypes.wintypes as wt

        self.ctypes = ctypes
        self.k32 = ctypes.windll.kernel32

        class FT(ctypes.Structure):
            _fields_ = [("lo", wt.DWORD), ("hi", wt.DWORD)]

        self.FT = FT
        self._info = None

    def _ft(self, v) -> int:
        return (v.hi << 32) | v.lo

    def system_times(self):
        FT, c = self.FT, self.ctypes
        i, k, u = FT(), FT(), FT()
        if not self.k32.GetSystemTimes(c.byref(i), c.byref(k), c.byref(u)):
            return None
        idle = self._ft(i)
        return idle, self._ft(k) + self._ft(u)  # 커널 시간에 유휴가 들어 있다

    def pids(self, image: str) -> list[int]:
        try:
            out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, errors="replace", timeout=20).stdout
        except Exception:
            return []
        return [int(m.group(1)) for m in re.finditer(r'^"[^"]+","(\d+)"', out, re.M)]

    def proc_time_s(self, pid: int):
        FT, c = self.FT, self.ctypes
        h = self.k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None
        cr, ex, k, u = FT(), FT(), FT(), FT()
        ok = self.k32.GetProcessTimes(h, c.byref(cr), c.byref(ex), c.byref(k), c.byref(u))
        self.k32.CloseHandle(h)
        return (self._ft(k) + self._ft(u)) / 1e7 if ok else None

    def cmdlines(self, image: str):
        ps = ("Get-CimInstance Win32_Process -Filter \"Name='%s'\" | "
              "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }" % image.replace("'", ""))
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                                 errors="replace", timeout=40).stdout
        except Exception:
            return []
        rows = []
        for line in out.splitlines():
            if "\t" in line:
                pid, cmd = line.split("\t", 1)
                if pid.strip().isdigit():
                    rows.append((int(pid), cmd))
        return rows

    def info(self) -> dict:
        if self._info:
            return self._info
        d = super().info()
        try:
            import winreg

            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            d["cpu"] = winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
        except Exception:
            pass
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  "(Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name) -join '; '"],
                                 capture_output=True, text=True, errors="replace", timeout=40).stdout.strip()
            if out:
                d["gpu"] = out
        except Exception:
            pass
        d["os"] = f"{platform.system()} {platform.release()} {platform.version()}"
        self._info = d
        return d


class LinuxProbe(HostProbe):
    def system_times(self):
        try:
            with open("/proc/stat") as f:
                v = [int(x) for x in f.readline().split()[1:]]
        except OSError:
            return None
        return v[3] + (v[4] if len(v) > 4 else 0), sum(v)

    def pids(self, image: str) -> list[int]:
        out = []
        for d in glob.glob("/proc/[0-9]*/comm"):
            try:
                if open(d).read().strip() == image[:15]:
                    out.append(int(d.split("/")[2]))
            except OSError:
                pass
        return out

    def proc_time_s(self, pid: int):
        try:
            s = open(f"/proc/{pid}/stat").read()
        except OSError:
            return None
        f = s[s.rfind(")") + 2:].split()
        return (int(f[11]) + int(f[12])) / os.sysconf("SC_CLK_TCK")

    def cmdlines(self, image: str):
        rows = []
        for pid in self.pids(image):
            try:
                rows.append((pid, open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace")))
            except OSError:
                pass
        return rows


class NullProbe(HostProbe):
    def system_times(self):
        return None

    def pids(self, image):
        return []

    def proc_time_s(self, pid):
        return None


def default_probe() -> HostProbe:
    if os.name == "nt":
        try:
            return WindowsProbe()
        except Exception:
            return NullProbe()
    if os.path.exists("/proc/stat"):
        return LinuxProbe()
    return NullProbe()


class HostWindow:
    """구간 하나의 호스트 CPU 사용률과 프로세스별 점유율(전체 코어 기준 %)."""

    def __init__(self, probe: HostProbe, images: list[str], clock=time.perf_counter):
        self.probe = probe
        self.images = list(dict.fromkeys(images))
        self.clock = clock
        self.t0 = clock()
        self.s0 = probe.system_times()
        self.p0: dict[tuple[str, int], float | None] = {}
        for im in self.images:
            for pid in probe.pids(im):
                self.p0[(im, pid)] = probe.proc_time_s(pid)

    def end(self) -> HostWindowResult:
        s1 = self.probe.system_times()
        dt = max(self.clock() - self.t0, 1e-6)
        busy = None
        if self.s0 and s1:
            idle = s1[0] - self.s0[0]
            tot = s1[1] - self.s0[1]
            if tot > 0:
                busy = round(100.0 * (tot - idle) / tot, 2)
        pct: dict[str, float] = {}
        cnt: dict[str, int] = {}
        for im in self.images:
            pids_now = self.probe.pids(im)
            cnt[im] = len(set(pids_now) | {p for (i, p) in self.p0 if i == im})
            v = 0.0
            for pid in pids_now:
                b = self.p0.get((im, pid))
                e = self.probe.proc_time_s(pid)
                if e is None:
                    continue
                v += e - (b if b is not None else e)  # 구간 중 새로 뜬 프로세스는 0부터 못 잼 → 0
            pct[im] = round(100.0 * v / dt / self.probe.ncpu, 2)
        return HostWindowResult(round(dt, 2), busy, pct, cnt)


def measure_idle(probe: HostProbe, images: list[str], seconds: float, sleep=time.sleep) -> HostWindowResult:
    w = HostWindow(probe, images)
    sleep(seconds)
    return w.end()


def busy_reasons(probe: HostProbe, host_cfg: dict) -> list[str]:
    """회차를 시작하면 안 되는 이유(배치 빌드 같은 프로세스, 잠금 파일)."""
    reasons = []
    for rule in host_cfg.get("busy") or []:
        image = rule.get("image")
        if not image:
            continue
        pat = rule.get("cmdline")
        if pat:
            rx = re.compile(pat)
            for pid, cmd in probe.cmdlines(image):
                if rx.search(cmd or ""):
                    reasons.append(f"{image} pid {pid}이 「{pat}」로 돌고 있음(배치 빌드 등): {(cmd or '')[:160]}")
        elif probe.pids(image):
            reasons.append(f"{image}이 돌고 있음")
    for pat in host_cfg.get("busy_files") or []:
        hits = [p for p in glob.glob(os.path.expanduser(os.path.expandvars(pat)))]
        for h in hits:
            reasons.append(f"잠금 파일 있음: {h}")
    return reasons


def match_any(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in patterns)
