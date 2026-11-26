"""기기 샘플러: 몇 초마다 adb shell 한 번으로 /proc와 (실기기면) sysfs·열·배터리를 읽는다.

두 등급 공통: 프로세스·스레드 CPU 틱(`/proc/<pid>/stat`, `/proc/<pid>/task/*/stat`), 시스템 CPU(`/proc/stat`),
`/proc/loadavg`, `/proc/<pid>/status`(VmRSS·VmHWM·RssAnon·VmSwap·Threads).
실기기만: CPU 클러스터 클럭, GPU 클럭·사용률·상한(SoC별 sysfs 경로는 기기 설정), 열(`dumpsys thermalservice`,
N샘플마다), 배터리(`dumpsys battery`).

행은 `device_samples.csv`로 쓰고, 지표 계산은 `perfkit.run.metrics`가 한다.
"""

from __future__ import annotations

import csv
import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from perfkit.device import parse as P

SEP = "@@PK@@"


@dataclass
class Sample:
    t: float                         # 호스트 시각(epoch 초)
    proc_ticks: int | None = None
    cpu: list[int] | None = None
    load1: float | None = None
    load5: float | None = None
    status: dict = field(default_factory=dict)
    threads: dict[int, tuple[str, int]] = field(default_factory=dict)  # tid → (이름, 틱)
    cpu_freq_mhz: list[float | None] = field(default_factory=list)
    gpu_clock_mhz: float | None = None
    gpu_busy_pct: float | None = None
    gpu_max_mhz: float | None = None
    thermal_status: int | None = None
    temps: dict[str, float] = field(default_factory=dict)
    batt_level: int | None = None
    batt_temp_c: float | None = None
    charging: bool | None = None
    alive: bool = True


def build_command(pid: int, dev: dict, with_thermal: bool) -> str:
    parts = [f"cat /proc/{pid}/stat", "head -1 /proc/stat", "cat /proc/loadavg",
             f"grep -E '^(VmRSS|VmHWM|VmSwap|RssAnon|RssFile|RssShmem|Threads):' /proc/{pid}/status",
             f"cat /proc/{pid}/task/*/stat 2>/dev/null"]
    sysfs = dev.get("sysfs") or {}
    if dev.get("kind") == "device":
        cpu = sysfs.get("cpu_freq") or []
        parts.append(" ; ".join(f"cat {p} 2>/dev/null || echo -" for p in cpu) or "echo")
        for key in ("gpu_clock", "gpu_busy", "gpu_max_clock"):
            p = sysfs.get(key)
            parts.append(f"cat {p} 2>/dev/null || echo -" if p else "echo -")
        parts.append("dumpsys battery 2>/dev/null")
        parts.append("dumpsys thermalservice 2>/dev/null" if with_thermal else "echo")
    return f" ; echo {SEP} ; ".join(parts)


def _num(s: str) -> float | None:
    m = re.search(r"-?\d+(?:\.\d+)?", s or "")
    return float(m.group(0)) if m else None


def parse_sample(text: str, t: float, dev: dict) -> Sample:
    parts = text.split(SEP)
    s = Sample(t=t)
    st = P.proc_stat_line(parts[0]) if parts else None
    if st is None:
        s.alive = False
        return s
    s.proc_ticks = st[2]
    if len(parts) > 1:
        s.cpu = P.cpu_line(parts[1].strip())
    if len(parts) > 2:
        la = P.loadavg(parts[2])
        if la:
            s.load1, s.load5 = la[0], la[1]
    if len(parts) > 3:
        s.status = P.proc_status(parts[3])
    if len(parts) > 4:
        for line in parts[4].strip().splitlines():
            q = P.proc_stat_line(line)
            if q:
                s.threads[q[0]] = (q[1], q[2])
    if dev.get("kind") == "device" and len(parts) > 5:
        sysfs = dev.get("sysfs") or {}
        fs = float(sysfs.get("cpu_freq_scale", 0.001))
        s.cpu_freq_mhz = [(_num(x) * fs if _num(x) is not None else None) for x in parts[5].split()] if parts[5].strip() else []
        gs = float(sysfs.get("gpu_clock_scale", 1.0))
        if len(parts) > 6 and _num(parts[6]) is not None:
            s.gpu_clock_mhz = round(_num(parts[6]) * gs, 1)
        if len(parts) > 7 and _num(parts[7]) is not None:
            s.gpu_busy_pct = _num(parts[7])
        if len(parts) > 8 and _num(parts[8]) is not None:
            s.gpu_max_mhz = round(_num(parts[8]) * gs, 1)
        if len(parts) > 9:
            b = P.battery(parts[9])
            s.batt_level, s.batt_temp_c, s.charging = b.get("level"), b.get("temp_c"), b.get("charging")
        if len(parts) > 10 and parts[10].strip():
            th = P.thermalservice(parts[10])
            s.thermal_status = th["status"]
            names = (dev.get("thermal") or {}).get("sensors") or {}
            for key, sensor in names.items():
                if sensor in th["temps"]:
                    s.temps[key] = th["temps"][sensor]
    return s


def group_threads(threads: dict[str, float], groups: dict[str, str]) -> dict[str, float]:
    """스레드 이름별 점유율 → 설정한 묶음(main·gfx·workers)별 합. 맞지 않으면 other."""
    rx = {k: re.compile(v) for k, v in groups.items()}
    out = {k: 0.0 for k in groups}
    out["other"] = 0.0
    for name, v in threads.items():
        for k, r in rx.items():
            if r.search(name):
                out[k] += v
                break
        else:
            out["other"] += v
    return {k: round(v, 2) for k, v in out.items()}


def thread_pct(a: Sample, b: Sample, clk_tck: int) -> dict[str, float]:
    """두 샘플 사이 스레드 이름별 점유율(% of 1코어). 이름이 같은 스레드는 합친다."""
    dt = b.t - a.t
    if dt <= 0:
        return {}
    acc: dict[str, int] = {}
    for tid, (name, ticks) in b.threads.items():
        prev = a.threads.get(tid)
        if prev is None:
            continue
        acc[name] = acc.get(name, 0) + max(0, ticks - prev[1])
    return {k: round(100.0 * v / clk_tck / dt, 2) for k, v in acc.items()}


ROW_FIELDS = ["t_host", "t_rel", "phase", "alive", "proc_cpu_pct", "sys_busy_pct", "load1", "load5", "rss_mb",
              "hwm_mb", "anon_mb", "swap_mb", "threads", "main_pct", "gfx_pct", "workers_pct", "other_threads_pct",
              "cpu_freq_mhz", "gpu_clock_mhz", "gpu_busy_pct", "gpu_max_mhz", "thermal_status", "temps",
              "batt_level", "batt_temp_c", "charging"]


def rows(samples: list[Sample], clk_tck: int, groups: dict[str, str], t0: float,
         phase_at=lambda t: None) -> list[dict]:
    """샘플 목록 → CSV 행. 점유율 칸은 앞 샘플과의 차이."""
    out = []
    prev = None
    for s in samples:
        r = {k: None for k in ROW_FIELDS}
        r.update({"t_host": round(s.t, 3), "t_rel": round(s.t - t0, 3), "phase": phase_at(s.t), "alive": int(s.alive)})
        st = s.status
        if st:
            r["rss_mb"] = round(st.get("VmRSS", 0) / 1024, 1) if "VmRSS" in st else None
            r["hwm_mb"] = round(st.get("VmHWM", 0) / 1024, 1) if "VmHWM" in st else None
            r["anon_mb"] = round(st.get("RssAnon", 0) / 1024, 1) if "RssAnon" in st else None
            r["swap_mb"] = round(st.get("VmSwap", 0) / 1024, 1) if "VmSwap" in st else None
            r["threads"] = st.get("Threads")
        r["load1"], r["load5"] = s.load1, s.load5
        if prev is not None and s.alive and prev.alive and s.t > prev.t:
            dt = s.t - prev.t
            if s.proc_ticks is not None and prev.proc_ticks is not None:
                r["proc_cpu_pct"] = round(100.0 * (s.proc_ticks - prev.proc_ticks) / clk_tck / dt, 2)
            r["sys_busy_pct"] = P.cpu_busy_pct(prev.cpu, s.cpu)
            g = group_threads(thread_pct(prev, s, clk_tck), groups)
            r["main_pct"], r["gfx_pct"], r["workers_pct"] = g.get("main"), g.get("gfx"), g.get("workers")
            r["other_threads_pct"] = g.get("other")
        if s.cpu_freq_mhz:
            r["cpu_freq_mhz"] = "|".join("" if v is None else f"{v:.0f}" for v in s.cpu_freq_mhz)
        r["gpu_clock_mhz"], r["gpu_busy_pct"], r["gpu_max_mhz"] = s.gpu_clock_mhz, s.gpu_busy_pct, s.gpu_max_mhz
        r["thermal_status"] = s.thermal_status
        r["temps"] = json.dumps(s.temps, sort_keys=True) if s.temps else None
        r["batt_level"], r["batt_temp_c"], r["charging"] = s.batt_level, s.batt_temp_c, s.charging
        out.append(r)
        prev = s
    return out


def write_rows(path: Path, rws: list[dict]) -> None:
    with Path(path).open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=ROW_FIELDS)
        w.writeheader()
        for r in rws:
            w.writerow({k: ("" if v is None else v) for k, v in r.items()})


class Sampler:
    """백그라운드 스레드로 interval초마다 샘플을 모은다."""

    def __init__(self, adb, pid: int, dev: dict, interval: float, clock=time.time):
        self.adb = adb
        self.pid = pid
        self.dev = dev
        self.interval = interval
        self.clock = clock
        self.samples: list[Sample] = []
        self.errors = 0
        self._stop = threading.Event()
        self._th: threading.Thread | None = None
        self.thermal_every = int(((dev.get("thermal") or {}).get("every_samples")) or 6)

    def sample_once(self) -> Sample:
        n = len(self.samples)
        cmd = build_command(self.pid, self.dev, with_thermal=(n % self.thermal_every == 0))
        t = self.clock()
        out = self.adb.shell(cmd, timeout=max(20.0, self.interval * 3))
        s = parse_sample(out, t, self.dev)
        self.samples.append(s)
        return s

    def _loop(self):
        while not self._stop.is_set():
            t0 = self.clock()
            try:
                s = self.sample_once()
                if not s.alive:
                    self.errors += 1
            except Exception:
                self.errors += 1
            wait = self.interval - (self.clock() - t0)
            if wait > 0:
                self._stop.wait(wait)

    def start(self) -> "Sampler":
        self._th = threading.Thread(target=self._loop, name="perfkit-sampler", daemon=True)
        self._th.start()
        return self

    def stop(self, final: bool = True) -> None:
        self._stop.set()
        if self._th is not None:
            self._th.join(self.interval * 4 + 30)
        if final:
            try:
                self.sample_once()
            except Exception:
                self.errors += 1
