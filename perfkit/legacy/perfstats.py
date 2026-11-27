"""옛 [PerfStats] logcat + device_samples.csv → run-meta·result."""

from __future__ import annotations

import csv
import re
import shutil
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

LINE_RE = re.compile(r"^([0-9]{2})-([0-9]{2}) ([0-9]{2}:[0-9]{2}:[0-9]{2})\.[0-9]+.*\[PerfStats\] t=(.*)$")


class LegacyError(ValueError):
    pass


def _kv(rest: str) -> dict:
    out = {}
    for tok in ("t=" + rest).split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k] = v
    return out


def _pct(vals, p):
    s = sorted(vals)
    k = (len(s) - 1) * p / 100
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _f(r, k):
    try:
        return float(r[k])
    except (KeyError, ValueError):
        return None


def _find_logcat(folder: Path, name: str | None) -> Path:
    if name:
        p = folder / name
        if not p.exists():
            raise LegacyError(f"logcat 파일이 없음: {p}")
        return p
    for p in sorted(folder.glob("*.txt")):
        with p.open("rb") as f:
            if b"[PerfStats] t=" in f.read(2_000_000):
                return p
    raise LegacyError(f"[PerfStats] 줄이 있는 .txt가 없음: {folder}")


def read(folder: Path, *, logcat: str | None = None, scene: str | None = None, year: int | None = None):
    sp = folder / "device_samples.csv"
    if not sp.exists():
        raise LegacyError(f"device_samples.csv가 없음: {folder}")
    with sp.open(encoding="utf-8") as f:
        samples = list(csv.DictReader(f))
    if not samples:
        raise LegacyError("device_samples.csv가 비어 있음")
    lp = _find_logcat(folder, logcat)
    w0, w1 = samples[0]["wall"], samples[-1]["wall"]
    recs = []
    for raw in lp.read_bytes().splitlines():
        m = LINE_RE.match(raw.decode("utf-8", "replace").rstrip())
        if not m:
            continue
        wall = m.group(3)
        if wall < w0 or wall > w1:
            continue
        kv = _kv(m.group(4))
        kv["_date"] = (m.group(1), m.group(2), wall)
        recs.append(kv)
    if not recs:
        raise LegacyError("측정 구간 안에 [PerfStats] 줄이 없음")
    scenes = Counter(r.get("scene") for r in recs)
    pick = scene or scenes.most_common(1)[0][0]
    recs = [r for r in recs if r.get("scene") == pick]
    if not recs:
        raise LegacyError(f"장면 {pick}의 줄이 없음(있는 것: {dict(scenes)})")
    y = year or datetime.fromtimestamp(lp.stat().st_mtime).year
    mo, d, hms = recs[0]["_date"]
    started = datetime.strptime(f"{y}-{mo}-{d} {hms}", "%Y-%m-%d %H:%M:%S")
    mo, d, hms = recs[-1]["_date"]
    ended = datetime.strptime(f"{y}-{mo}-{d} {hms}", "%Y-%m-%d %H:%M:%S")
    return samples, recs, pick, lp, started, ended


def summarize(recs: list[dict], samples: list[dict]) -> dict:
    fps = [_f(r, "fps") for r in recs]
    hist = [0] * 6
    for r in recs:
        for i, v in enumerate((r.get("hist") or "0,0,0,0,0,0").split(",")[:6]):
            hist[i] += int(v)
    frames = sum(hist)

    def col(k):
        return [v for v in (_f(r, k) for r in recs) if v is not None]

    def sc(k):
        out = []
        for s in samples:
            try:
                out.append(float(s[k]))
            except (KeyError, ValueError, TypeError):
                pass
        return out

    pss = [v for v in sc("pss_mb") if v > 0]
    return {
        "seconds": len(recs), "frames": frames, "fps_avg": statistics.fmean(fps), "fps_min": min(fps),
        "on_budget_pct": hist[0] / frames * 100 if frames else None, "hist": hist,
        "main": col("main"), "render": col("render"), "gpu": col("gpu"), "setpass": col("setpass"),
        "tris": col("tris"), "verts": col("verts"), "gcKB": col("gcKB"), "totalMB": col("totalMB"),
        "sysMB": col("sysMB"), "gcResMB": col("gcResMB"), "pss": pss,
    }


def convert(folder: Path, *, device: str, build_hash: str, commit=None, version="unknown", scene=None,
            scenario="legacy", save="device-current", logcat=None, year=None, session=None):
    from perfkit import verdict as V
    from perfkit.config import settings
    from perfkit.device import devcfg as DC
    from perfkit.manifest import load

    if not re.fullmatch(r"[0-9A-Za-z]{8}", build_hash or ""):
        raise LegacyError("--build-hash는 8자(영문·숫자)")
    s = settings()
    try:
        dev = DC.device_config(device, s)
    except Exception as exc:  # 프로필에 기기 없음
        raise LegacyError(str(exc)) from None
    dclass = "emulator" if dev.get("kind") == "emulator" else "device"
    samples, recs, pick, lp, started, ended = read(folder, logcat=logcat, scene=scene, year=year)
    sm = summarize(recs, samples)
    m = load()
    run_id = f"{started:%Y%m%d-%H%M}-{build_hash}-{scenario}-1"

    def first(k, cast=float):
        for row in samples:
            try:
                return cast(row[k])
            except (KeyError, ValueError, TypeError):
                continue
        return None

    meta = {
        "schema": "perfkit/run-meta@1", "run_id": run_id, "session": session or f"legacy-{run_id}",
        "run_kind": "numeric", "compat_mode": True, "build_hash": build_hash, "apk_sha": "0" * 64,
        "version": version, "build_config": {"variant": "development", "probe": False, "frame_timing_stats": True},
        "scenario": scenario, "seed": None, "save": save, "arm": None, "round": 1,
        "started_at": started.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "ended_at": ended.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "device_class": dclass, "device_profile": device, "serial": "legacy", "os_build": "unknown",
        "probe_hash": "legacy-perfstats", "manifest_version": m.version, "manifest_sha": m.sha256,
        "valid": True, "legacy": {"folder": str(folder), "logcat": lp.name, "scene": pick},
        "notes": "옛 형식에서 가져옴: 빌드 APK sha·OS 빌드·serial은 모름",
    }
    if commit:
        meta["commit"] = commit
    if dclass == "device":
        meta.update({"thermal_start": first("thermal_status", int), "ap_c_start": first("ap_c"),
                     "batt_c_start": first("bat_c"), "batt_pct": first("battery_level"), "charging": None,
                     "fixed_perf": None, "game_mode": None})
    else:
        meta.update({"emu": {}, "host": {}})  # 에뮬레이터 옛 자료는 emu·host를 몰라 스키마가 거부한다
    layer = V.layer_for_class(dclass)
    items = {}

    def put(iid, vals, detail=None):
        vals = {k: round(v, 4) for k, v in vals.items() if v is not None}
        if vals:
            items[iid] = {"status": "ok", "values": vals, "collector": "run.legacy_import"}
            if detail:
                items[iid]["detail"] = detail

    put("FRM-01", {"p50": _pct(sm["main"], 50) if sm["main"] else None, "p95": _pct(sm["main"], 95) if sm["main"] else None})
    put("FRM-02", {"p50": _pct(sm["render"], 50) if sm["render"] else None,
                   "p95": _pct(sm["render"], 95) if sm["render"] else None})
    put("FRM-03", {"fps_avg": sm["fps_avg"]},
        {"on_budget_pct": sm["on_budget_pct"], "hist": sm["hist"], "frames": sm["frames"], "fps_min": sm["fps_min"],
         "seconds": sm["seconds"]})
    put("CPU-02", {"idle_b_per_frame": statistics.fmean(sm["gcKB"]) * 1024 if sm["gcKB"] else None})
    put("CPU-04", {"gc_reserved_mb": statistics.fmean(sm["gcResMB"]) if sm["gcResMB"] else None})
    put("MEM-04", {"total_used_mb": statistics.fmean(sm["totalMB"]) if sm["totalMB"] else None,
                   "system_used_mb": statistics.fmean(sm["sysMB"]) if sm["sysMB"] else None})
    put("MEM-03", {"pss_total_mb": statistics.median(sm["pss"]) if sm["pss"] else None})
    result = {"schema": "perfkit/result@1", "layer": layer, "device_class": dclass, "run_id": run_id,
              "manifest_version": m.version, "manifest_sha": m.sha256, "collected_at": meta["started_at"],
              "source": {"kind": "run", "ref": f"legacy:{folder.name}", "build_hash": build_hash},
              "tool": {"importer": "perfkit.legacy"}, "items": items}
    if s.profile:
        for d in (meta, result):
            d["profile_name"], d["profile_sha"] = s.profile_name or "profile", s.profile_sha
    summ = {"scene": pick, "seconds": sm["seconds"], "frames": sm["frames"], "fps_avg": round(sm["fps_avg"], 2),
            "on_budget_pct": round(sm["on_budget_pct"], 2) if sm["on_budget_pct"] is not None else None,
            "run_id": run_id, "layer": layer}
    return meta, result, summ, [lp, folder / "device_samples.csv"]


def write(results_dir: Path, meta: dict, result: dict, srcs: list[Path]) -> Path:
    from perfkit.diff import resultio
    from perfkit.schema import validate

    for kind, obj in (("run-meta", meta), ("result", result)):
        errs = validate(obj, kind)
        if errs:
            raise LegacyError(f"{kind} 스키마 오류: {errs[0]}")
    d = results_dir / "runs" / meta["run_id"]
    d.mkdir(parents=True, exist_ok=True)
    resultio.write(d / "meta.json", meta)
    resultio.write(d / "result.json", result)
    for p in srcs:
        shutil.copyfile(p, d / ("logcat.txt" if p.suffix == ".txt" else p.name))
    return d
