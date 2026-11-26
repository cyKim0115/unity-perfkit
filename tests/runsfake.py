"""합성 회차 폴더(meta.json + result.json) 만들기. 값은 시험용 예시다."""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from perfkit.manifest import load

PROFILE_COMPARE = """name = "test-profile"

[paths]
results = "results"

[baseline]
dir = "results/baselines"

[compare]
bootstrap = 2000
seed = 7

[compare."L3-E"]
exclude = ["FRM-03.slow_50_pct"]

[compare."L3-E".gate]
host_busy_during_max = 25

[overrides."MEM-04".layers."L3-E"]
noise = "low"

[overrides."FRM-03".layers."L3-E"]
noise = "medium"

[overrides."AUX-01".layers."L3-E"]
noise = "low"
"""

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def meta(run_id: str, *, device_class: str = "emulator", arm: str = "A", rnd: int = 1, started: datetime = T0,
         session: str = "s1", build: str = "0123abcd", **extra) -> dict:
    m = load()
    d = {
        "schema": "perfkit/run-meta@1", "run_id": run_id, "build_hash": build, "apk_sha": "1" * 64,
        "version": "1.0.0", "build_config": {"variant": "release", "probe": True, "frame_timing_stats": True},
        "run_kind": "numeric", "scenario": "scenario-a", "seed": 1, "save": "light", "arm": arm, "round": rnd,
        "started_at": started.isoformat(), "probe_hash": "probe-1", "quality_hash": "q-1",
        "manifest_version": m.version, "manifest_sha": m.sha256, "device_class": device_class,
        "serial": "emulator-5554" if device_class == "emulator" else "serial-1", "os_build": "os-1",
        "session": session, "valid": True,
    }
    if device_class == "emulator":
        d.update({"device_profile": "emulator",
                  "emu": {"avd": "avd-1", "system_image": "img", "cores": 4, "ram_mb": 4096, "gpu_mode": "host",
                          "resolution": "1080x2400", "emulator_version": "1", "arm_translation": True},
                  "host": {"cpu": "cpu", "os": "os", "gpu": "gpu", "cpu_busy_before": 5.0, "cpu_busy_during": 15.0}})
    else:
        d.update({"device_profile": "phone-a", "thermal_start": 0, "ap_c_start": 37.0, "batt_c_start": 33.0,
                  "batt_pct": 80, "charging": False, "fixed_perf": None, "game_mode": None})
    d.update(extra)
    return d


def result(run_id: str, values: dict, *, device_class: str = "emulator") -> dict:
    m = load()
    layer = "L3-E" if device_class == "emulator" else "L3-D"
    return {"schema": "perfkit/result@1", "layer": layer, "device_class": device_class, "run_id": run_id,
            "manifest_version": m.version, "manifest_sha": m.sha256, "collected_at": T0.isoformat(),
            "source": {"kind": "run", "ref": f"runs/{run_id}"},
            "items": {iid: {"status": "ok", "values": vals} for iid, vals in values.items()}}


def write_run(root: Path, m: dict, r: dict) -> Path:
    d = root / "runs" / m["run_id"]
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    (d / "result.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    (d / "logcat.txt").write_text("log\n", encoding="utf-8")
    return d


def values(mem: float, fps: float, cpu: float, slow: float = 10.0) -> dict:
    return {"MEM-04": {"total_used_mb": mem}, "FRM-03": {"fps_avg": fps, "slow_50_pct": slow},
            "AUX-01": {"proc_cpu_pct_avg": cpu}}


def abab_session(root: Path, session: str, rounds: int, base_fn, cand_fn, *, device_class: str = "emulator",
                 start: datetime = T0, base_meta: dict | None = None, cand_meta: dict | None = None,
                 cand_device_class: str | None = None) -> list[str]:
    """라운드마다 A 다음 B. base_fn(r)·cand_fn(r) → values()."""
    ids = []
    t = start
    for rnd in range(1, rounds + 1):
        for arm, fn, extra, dc in (("A", base_fn, base_meta or {}, device_class),
                                   ("B", cand_fn, cand_meta or {}, cand_device_class or device_class)):
            rid = f"{t:%Y%m%d-%H%M}-{'0123abcd' if arm == 'A' else '4567cdef'}-scenario-a-{rnd}"
            ex = dict(extra)
            per_round = ex.pop("per_round", {}).get(rnd, {})
            m = meta(rid, device_class=dc, arm=arm, rnd=rnd, started=t, session=session,
                     build="0123abcd" if arm == "A" else "4567cdef", **ex, **per_round)
            write_run(root, m, result(rid, fn(rnd), device_class=dc))
            ids.append(rid)
            t += timedelta(minutes=3)
    return ids


def noisy(seed: int, center: float, rel_sd: float):
    rng = random.Random(seed)
    return lambda: center * (1.0 + rng.gauss(0.0, rel_sd))
