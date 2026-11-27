"""회차 원자료 → 결과 항목(매니페스트 지표 키).

값은 측정 단계(PHASE `measure=true`)만 쓴다. 로드 구간·시작 시간은 따로 본다. 못 재는 지표는 칸을 비우고,
항목 전체를 못 재면 `not_collected` + 이유로 남긴다(지어내지 않는다).

| 항목 | 원자료 | 지표 |
|---|---|---|
| FRM-01·02 | 프레임 CSV mainMs·renderMs(FrameTiming) | p50·p95(·p99) |
| FRM-03 | 프레임 CSV dtMs | fps_avg = 프레임 수 / 실제 시간, 느린 프레임 비율, 히치 = dt > max(50ms, 3×중앙값) |
| GPU-01 | gpuMs(실기기만) | p50·p95 |
| CPU-02 | gcAllocB(개발 빌드) 또는 monoAllocB(릴리스 대체, 하한값) | 프레임 평균 B |
| CPU-03·04, MEM-04·05 | [PerfStats] gcCount·gcResMB·monoMB·totalMB·sysMB | 분당 GC 수, 평균 MB |
| CPU-15 | EVENT ui_open nextFrameMs, PlayScene 로드 구간 | 최대 ms |
| GPU-03·04·07 | 프레임 CSV dc·batches·setpass·tris·verts의 근경·원경 단계 평균(단계 이름 기본 near·far, 프로필 [run] count_phases) | 개/프레임 |
| MEM-01 | /proc status RssAnon + VmSwap | 최고·P90 MB |
| MEM-03 | 구간 밖 dumpsys meminfo | MB |
| BLD-10 | am start -W TotalTime(LaunchState COLD) | 초 |
| BLD-11·STB-11·AUX-04 | `load_*` PHASE t(앱 시작부터 실제 초): 마지막 로드 단계 = TTFD, 이웃 단계 차 = 로드 구간 | 초 |
| STB-08·09 | 앱 pid logcat | 개수·서명 |
| AUX-01·02·03 | /proc 샘플 | 점유율 % |
| THM-01·GPU-02 | 열·sysfs 샘플(실기기만) | 최고·평균 |
"""

from __future__ import annotations

import json
import math
from statistics import mean, median

from perfkit.run.probe import ProbeLog

COUNT_PHASES = ("near", "far")  # GPU-03·04·07 근경·원경 단계 이름(프로필 [run] count_phases). 지표 키는 _near·_far 그대로
SCENE_LOAD = ("load_scene_begin", "load_scene_end")  # CPU-15 addressables_load_ms 구간(프로필 [run] scene_load_phases)


def load_phases(log: ProbeLog) -> list[tuple[str, float]]:
    """`load_`로 시작하는 단계(나온 순서)와 t(앱 시작부터 실제 초)."""
    return [(p["name"], p["t"]) for p in log.phases if str(p.get("name", "")).startswith("load_") and p.get("t") is not None]


def pct(values: list[float], q: float) -> float | None:
    v = sorted(x for x in values if x is not None and not math.isnan(x))
    if not v:
        return None
    if len(v) == 1:
        return v[0]
    k = (len(v) - 1) * q / 100.0
    lo = math.floor(k)
    hi = math.ceil(k)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def r(x, n=3):
    return None if x is None else round(float(x), n)


def _vals(rows, key, positive=False):
    out = []
    for row in rows:
        v = row.get(key)
        if v is None:
            continue
        if positive and v < 0:
            continue
        out.append(v)
    return out


def measure_names(log: ProbeLog, frames: list[dict]) -> set[str]:
    names = set(log.measure_phases())
    if not names and frames:
        names = {f.get("phase") for f in frames if f.get("phase") and not str(f.get("phase")).startswith("load_")
                 and f.get("phase") not in ("settle", "end")}
    return names


def _ok(values: dict, collector: str, unit: str | None = None, **extra) -> dict:
    vals = {k: v for k, v in values.items() if v is not None}
    if not vals:
        return {"status": "not_collected", "reason": extra.pop("why", "값 없음"), "collector": collector}
    d = {"status": "ok", "values": vals, "collector": collector}
    if unit:
        d["unit"] = unit
    d.update({k: v for k, v in extra.items() if k != "why" and v is not None})
    return d


def _nc(collector: str, reason: str) -> dict:
    return {"status": "not_collected", "reason": reason, "collector": collector}


def frame_items(log: ProbeLog, frames: list[dict], device_kind: str,
                count_phases: tuple[str, str] | list[str] | None = None) -> dict:
    items: dict = {}
    if not frames:
        why = "프레임 CSV 없음(측정 모드 없음 또는 받기 실패)"
        for iid in ("FRM-01", "FRM-02", "FRM-03", "GPU-01", "CPU-02", "GPU-03", "GPU-04", "GPU-07"):
            items[iid] = _nc("device.frame_timing" if iid.startswith(("FRM", "GPU-01")) else "device.render_counters", why)
        items["CPU-02"]["collector"] = "device.gc_alloc"
        return items
    names = measure_names(log, frames)
    mf = [f for f in frames if f.get("phase") in names]
    main = _vals(mf, "mainMs", True)
    render = _vals(mf, "renderMs", True)
    dt = _vals(mf, "dtMs", True)
    by_phase = {}
    for n in sorted(names):
        rows = [f for f in mf if f.get("phase") == n]
        by_phase[n] = {"frames": len(rows), "main_p50": r(pct(_vals(rows, "mainMs", True), 50)),
                       "render_p50": r(pct(_vals(rows, "renderMs", True), 50)),
                       "dt_p50": r(pct(_vals(rows, "dtMs", True), 50))}
    cpu = _vals(mf, "cpuMs", True)
    items["FRM-01"] = _ok({"p50": r(pct(main, 50)), "p95": r(pct(main, 95)), "p99": r(pct(main, 99))},
                          "device.frame_timing", "ms", samples=len(main), phases=by_phase,
                          cpu_frame_p50=r(pct(cpu, 50)), cpu_frame_p95=r(pct(cpu, 95)),
                          dt_p50=r(pct(_vals(mf, "dtMs", True), 50)),
                          why="FrameTiming main 값 없음(-1)")
    items["FRM-02"] = _ok({"p50": r(pct(render, 50)), "p95": r(pct(render, 95))}, "device.frame_timing", "ms",
                          samples=len(render), why="FrameTiming render 값 없음(-1)")
    if dt:
        total_s = sum(dt) / 1000.0
        med = median(dt)
        hitch_ms = max(50.0, 3 * med)
        items["FRM-03"] = _ok({
            "fps_avg": r(len(dt) / total_s if total_s > 0 else None, 2),
            "slow_16_7_pct": r(100.0 * sum(1 for x in dt if x > 16.7) / len(dt), 2),
            "slow_33_pct": r(100.0 * sum(1 for x in dt if x > 33.3) / len(dt), 2),
            "slow_50_pct": r(100.0 * sum(1 for x in dt if x > 50.0) / len(dt), 2),
            "hitch_count": sum(1 for x in dt if x > hitch_ms),
        }, "device.frame_timing", samples=len(dt), hitch_rule=f"dt > max(50ms, 3×중앙값) = {hitch_ms:.1f}ms")
    else:
        items["FRM-03"] = _nc("device.frame_timing", "dtMs 없음")
    if device_kind == "device":
        gpu = _vals(mf, "gpuMs", True)
        items["GPU-01"] = _ok({"p50": r(pct(gpu, 50)), "p95": r(pct(gpu, 95))}, "device.frame_timing", "ms",
                              samples=len(gpu), why="FrameTiming gpu 값 없음(-1)")
    gc = _vals(mf, "gcAllocB", True)
    mono = _vals(mf, "monoAllocB", True)
    if gc and any(x > 0 for x in gc):
        items["CPU-02"] = _ok({"idle_b_per_frame": r(mean(gc), 1)}, "device.gc_alloc", source="gcAllocB")
    elif mono:
        items["CPU-02"] = _ok({"idle_b_per_frame": r(mean(mono), 1)}, "device.gc_alloc",
                              source="monoAllocB", note="릴리스 대체 지표(Mono 힙 증가분, 하한값)")
    else:
        items["CPU-02"] = _nc("device.gc_alloc", "GC 할당 칸 없음")
    dc_ok = str(log.header.get("dcRecorder", "")).startswith("True")

    def ph_mean(phase, key):
        rows = [f for f in mf if f.get("phase") == phase]
        v = _vals(rows, key, True)
        return r(mean(v), 1) if v else None

    near, far = tuple(count_phases or COUNT_PHASES)[:2]
    items["GPU-04"] = _ok({"setpass_near": ph_mean(near, "setpass"), "setpass_far": ph_mean(far, "setpass"),
                           "batches_near": ph_mean(near, "batches") if dc_ok else None,
                           "batches_far": ph_mean(far, "batches") if dc_ok else None},
                          "device.render_counters", why=f"{near}·{far} 단계 없음",
                          note=None if dc_ok else "Batches 카운터를 이 빌드에서 못 읽음(dcRecorder=False)")
    items["GPU-07"] = _ok({"verts_near": ph_mean(near, "verts"), "verts_far": ph_mean(far, "verts"),
                           "tris_near": ph_mean(near, "tris"), "tris_far": ph_mean(far, "tris")},
                          "device.render_counters", why=f"{near}·{far} 단계 없음")
    if dc_ok:
        items["GPU-03"] = _ok({"near": ph_mean(near, "dc"), "far": ph_mean(far, "dc")}, "device.render_counters")
    else:
        items["GPU-03"] = _nc("device.render_counters", "Draw Calls 카운터를 이 빌드에서 못 읽음(dcRecorder=False)")
    return items


def stats_items(log: ProbeLog, device_kind: str, samples: list[dict]) -> dict:
    items: dict = {}
    names = set(log.measure_phases())
    rows = [s for s in log.stats if s.get("phase") in names] if names else []
    if not rows:
        why = "[PerfStats] 측정 단계 줄 없음"
        for iid, col in (("CPU-03", "device.unity_memory"), ("CPU-04", "device.unity_memory"),
                         ("MEM-04", "device.unity_memory"), ("MEM-05", "device.unity_memory")):
            items[iid] = _nc(col, why)
        return items

    def m(key):
        v = _vals(rows, key)
        return r(mean(v), 2) if v else None

    secs = len(rows)  # [PerfStats]는 1초에 한 줄
    gcc = _vals(rows, "gcCount")
    items["CPU-03"] = _ok({"gc_count_per_min": r(sum(gcc) / (secs / 60.0), 2) if gcc else None},
                          "device.unity_memory", why="gcCount 칸 없음",
                          note="gc_collect_ms_max는 릴리스 빌드에서 못 잼")
    res = _vals(rows, "gcResMB")
    items["CPU-04"] = _ok({"gc_reserved_mb": m("gcResMB"), "gc_used_mb": m("monoMB"),
                           "gc_reserved_monotonic": (res[-1] > res[0]) if len(res) > 1 else None},
                          "device.unity_memory", "MB")
    items["MEM-04"] = _ok({"total_used_mb": m("totalMB"), "system_used_mb": m("sysMB")}, "device.unity_memory", "MB",
                          gfx_used_mb=m("gfxMB"), note="Total Reserved는 측정 모드 로그에 없음")
    items["MEM-05"] = _ok({"managed_heap_mb": m("gcResMB")}, "device.unity_memory", "MB")
    if device_kind == "device":
        head = _vals(rows, "thermal")
        stt = _vals(rows, "thermStatus")
        temps = [s for s in samples if s.get("phase") in names]

        def tmax(key):
            v = []
            for s in temps:
                t = s.get("temps") or {}
                if isinstance(t, str):
                    t = json.loads(t)
                if t.get(key) is not None:
                    v.append(t[key])
            return max(v) if v else None
        items["THM-01"] = _ok({"headroom_max": max((h for h in head if h >= 0), default=None),
                               "thermal_status_max": max((x for x in stt if x >= 0), default=None),
                               "soc_c_max": tmax("ap"), "batt_c_max": max(_vals(temps, "batt_temp_c"), default=None)},
                              "device.thermal")
    return items


def event_items(log: ProbeLog, scene_load: tuple[str, str] = SCENE_LOAD) -> dict:
    ev = [e for e in log.events if e.get("kind") == "ui_open"]
    first_seen: set = set()
    first, again = [], []
    for e in ev:
        v = e.get("nextFrameMs")
        if v is None:
            continue
        (again if e.get("event_name") in first_seen else first).append(v)
        first_seen.add(e.get("event_name"))
    scene = None
    a, b = log.phase_t(scene_load[0]), log.phase_t(scene_load[1])
    if a is not None and b is not None:
        scene = round((b - a) * 1000.0, 1)
    vals = {"ui_open_max_frame_ms": r(max(first + again), 1) if ev else None,
            "addressables_load_ms": scene, "events": len(log.events) if log.has_probe else None}
    return {"CPU-15": _ok(vals, "device.phase_markers", why="이벤트·로드 마커 없음",
                          ui_open_first_max_ms=r(max(first), 1) if first else None,
                          ui_open_again_max_ms=r(max(again), 1) if again else None,
                          note=f"addressables_load_ms = 씬 로드 구간({scene_load[0]}→{scene_load[1]})")}


def load_items(log: ProbeLog, am: dict) -> dict:
    items: dict = {}
    if am and am.get("total_ms") is not None and (am.get("launch_state") or "COLD").upper() == "COLD":
        items["BLD-10"] = _ok({"cold_s": r(am["total_ms"] / 1000.0, 3)}, "device.startup", "s",
                              wait_ms=am.get("wait_ms"), launch_state=am.get("launch_state"))
    else:
        items["BLD-10"] = _nc("device.startup", "am start -W 결과 없음 또는 콜드 시작 아님")
    lp = load_phases(log)
    stages = {n: t for n, t in lp}
    game = lp[-1][1] if lp else None
    boot = lp[0][1] if lp else None
    items["BLD-11"] = _ok({"ttfd_s": r(game, 3)}, "device.phase_markers", "s", why="load_* 마커 없음",
                          note=f"앱 시작(realtimeSinceStartup)부터 마지막 로드 단계({lp[-1][0]})까지" if lp else None)
    items["STB-11"] = _ok({"stages": stages or None, "boot_total_s": r(game, 3)}, "device.phase_markers",
                          why="로드 마커 없음")
    segs = {f"{a[0]}->{b[0]}": round(b[1] - a[1], 3) for a, b in zip(lp, lp[1:])}
    items["AUX-04"] = _ok({"load_total_s": r(game - boot, 3) if game is not None and boot is not None else None,
                           "phases": segs or None}, "device.phase_markers", "s", why="로드 마커 없음")
    return items


def sample_items(samples: list[dict], names: set[str] | None, device_kind: str) -> dict:
    """samples = sampler.rows 결과. names가 None이면(호환 모드) 모든 행."""
    rows = [s for s in samples if (names is None or s.get("phase") in names)]
    items: dict = {}
    if len(rows) < 2:
        why = f"측정 구간 샘플 부족({len(rows)}개)"
        for iid, col in (("AUX-01", "device.proc_cpu"), ("AUX-02", "device.proc_cpu"), ("AUX-03", "device.sys_load"),
                         ("MEM-01", "device.proc_mem")):
            items[iid] = _nc(col, why)
        return items
    cpu = _vals(rows, "proc_cpu_pct")
    items["AUX-01"] = _ok({"proc_cpu_pct_avg": r(mean(cpu), 2) if cpu else None,
                           "proc_cpu_pct_p95": r(pct(cpu, 95), 2)}, "device.proc_cpu", "%", samples=len(cpu))
    thr = _vals(rows, "threads")
    items["AUX-02"] = _ok({"main_pct": r(mean(_vals(rows, "main_pct")), 2) if _vals(rows, "main_pct") else None,
                           "gfx_pct": r(mean(_vals(rows, "gfx_pct")), 2) if _vals(rows, "gfx_pct") else None,
                           "workers_pct": r(mean(_vals(rows, "workers_pct")), 2) if _vals(rows, "workers_pct") else None,
                           "threads": int(round(mean(thr))) if thr else None}, "device.proc_cpu", "%")
    sb = _vals(rows, "sys_busy_pct")
    la = _vals(rows, "load1")
    items["AUX-03"] = _ok({"sys_busy_pct": r(mean(sb), 2) if sb else None, "loadavg_1m": r(mean(la), 2) if la else None},
                          "device.sys_load")
    anon = [(s.get("anon_mb") or 0) + (s.get("swap_mb") or 0) for s in rows if s.get("anon_mb") is not None]
    rss = _vals(rows, "rss_mb")
    items["MEM-01"] = _ok({"anon_swap_peak_mb": r(max(anon), 1) if anon else None,
                           "anon_swap_p90_mb": r(pct(anon, 90), 1)}, "device.proc_mem", "MB",
                          rss_avg_mb=r(mean(rss), 1) if rss else None, rss_max_mb=r(max(rss), 1) if rss else None,
                          hwm_mb=r(max(_vals(rows, "hwm_mb")), 1) if _vals(rows, "hwm_mb") else None)
    if device_kind == "device":
        clk = _vals(rows, "gpu_clock_mhz")
        cap = _vals(rows, "gpu_max_mhz")
        busy = [(s.get("gpu_busy_pct"), s.get("gpu_clock_mhz"), s.get("gpu_max_mhz")) for s in rows]
        use = [b * c / m for b, c, m in busy if b is not None and c and m]
        items["GPU-02"] = _ok({"clock_avg_mhz": r(mean(clk), 1) if clk else None,
                               "cap_mhz": r(min(cap), 1) if cap else None,
                               "usage_vs_cap_p95": r(pct(use, 95), 1)}, "device.gpu_freq",
                              why="GPU sysfs 값 없음(기기 설정 sysfs 확인)")
    return items


def meminfo_items(mi: dict | None, why: str | None = None) -> dict:
    if not mi:
        return {"MEM-03": _nc("device.meminfo", why or "dumpsys meminfo 없음")}
    vals = {"pss_total_mb": mi.get("total_pss"), "private_dirty_mb": mi.get("private_dirty"),
            "native_heap_mb": mi.get("native_heap"), "egl_mtrack_mb": mi.get("egl_mtrack_table"),
            "gl_mtrack_mb": mi.get("gl_mtrack_table"), "gfx_dev_mb": mi.get("gfx_dev_table"),
            "unknown_mb": mi.get("unknown_table"), "code_mb": mi.get("code")}
    return {"MEM-03": _ok(vals, "device.meminfo", "MB", java_heap_mb=mi.get("java_heap"),
                          graphics_mb=mi.get("graphics"), note="회차 구간 밖(측정 단계가 끝난 뒤)")}


def log_items(summary: dict | None) -> dict:
    if summary is None:
        return {"STB-08": _nc("device.logcat", "logcat 없음"), "STB-09": _nc("device.logcat", "logcat 없음")}
    return {
        "STB-08": _ok({k: summary[k] for k in ("exceptions", "class_warnings", "class_errors", "android_runtime",
                                                 "native_debug")}, "device.logcat", "count",
                      signatures=summary.get("signatures") or None,
                      note="개수는 참고. 비교는 새로 생긴 서명(signatures)으로 본다"),
        "STB-09": _ok({k: summary[k] for k in ("choreographer_skipped", "anr", "lmk")}, "device.logcat", "count"),
    }
