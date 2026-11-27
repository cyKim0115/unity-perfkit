"""기준 대비 비교를 한 결과로 묶는다.

- 결정적 층(L0·L1): `perfkit.diff.compare.diff_results`(method=diff)
- 회차 묶음(L2·L3-E·L3-D): 통계 판정(method=abab, L2는 same_day)
- 등급(device_class)이 다른 회차를 섞으면 `verdict.ClassMixError`로 거부한다.
- 측정 코드·시나리오·매니페스트·품질 설정 해시가 다르면 비교를 무효(`valid=false`)로 표시한다.
- 관문에서 무효가 된 회차는 통계에서 빼고 수와 이유를 남긴다.

여러 층을 한 번에 비교하면 `perfkit/comparison-set@1`(층마다 comparison@1 하나)로 묶는다.
"""

from __future__ import annotations

from datetime import datetime, timezone

from perfkit import verdict as V
from perfkit.compare import judge as J
from perfkit.compare import runsets as R

SET_SCHEMA = "perfkit/comparison-set@1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _uniq(runs: list[R.Run], key: str):
    vals = {r.meta.get(key) for r in runs if r.meta.get(key)}
    return next(iter(vals)) if len(vals) == 1 else None


def _side_doc(label: str, runs: list[R.Run], used: list[R.Run], dropped: list[dict], spec: str) -> dict:
    d = {"label": label, "run_ids": [r.run_id for r in used], "spec": spec,
         "n_runs": len(runs), "n_used": len(used), "n_invalid": len(dropped)}
    if dropped:
        d["invalid_runs"] = dropped
    for k in ("commit", "build_hash", "apk_sha", "version"):
        v = _uniq(runs, k)
        if v:
            d[k] = v
    sessions = sorted({r.meta.get("session") for r in runs if r.meta.get("session")})
    if sessions:
        d["sessions"] = sessions
    if runs:
        d["started_first"] = min(r.meta["started_at"] for r in runs)
        d["started_last"] = max(r.meta["started_at"] for r in runs)
    if any(r.meta.get("compat_mode") for r in runs):
        d["compat_mode"] = True
    return d


def _env(runs: list[R.Run]) -> dict:
    env: dict = {}
    for k in ("device_profile", "os_build", "probe_hash", "scenario", "save", "seed"):
        vals = sorted({str(r.meta.get(k)) for r in runs if r.meta.get(k) is not None})
        if vals:
            env[k] = vals[0] if len(vals) == 1 else vals
    q = sorted({str(r.quality_hash()) for r in runs if r.quality_hash()})
    if q:
        env["quality_hash"] = q[0] if len(q) == 1 else q
    emu = [r.meta["emu"] for r in runs if r.meta.get("emu")]
    if emu:
        env["emu"] = emu[0]
    hosts = [r.meta["host"] for r in runs if r.meta.get("host")]
    if hosts:
        env["host"] = {k: hosts[0].get(k) for k in ("cpu", "os", "gpu") if hosts[0].get(k)}
        for k in ("cpu_busy_before", "cpu_busy_during"):
            vs = [h[k] for h in hosts if isinstance(h.get(k), (int, float))]
            if vs:
                env["host"][k + "_range"] = [min(vs), max(vs)]
    return env


def compare_runsets(base: R.Side, cand: R.Side, *, manifest, cfg: J.CompareConfig, pairing: str = "auto",
                    noise_floor: dict | None = None, noise_source: str | None = None,
                    settings=None, include_invalid: bool = False) -> dict:
    """회차 묶음 둘을 비교해 comparison@1 하나를 만든다. 등급이 섞이면 ClassMixError."""
    all_runs = base.runs + cand.runs
    if not base.runs or not cand.runs:
        raise R.RunsetError("기준·후보 회차가 모두 있어야 함")
    cls = V.ensure_same_class([r.meta for r in all_runs])
    layer = V.layer_for_class(cls)
    for r in all_runs:
        if r.layer != layer:
            raise V.ClassMixError(f"{r.run_id}: 결과 층 {r.layer} ≠ 등급 층 {layer}")
    gate = (cfg.layer_cfg(layer).get("gate") or {})
    b_use, b_bad = R.usable(base.runs, gate)
    c_use, c_bad = R.usable(cand.runs, gate)
    diag = []
    if include_invalid and (b_bad or c_bad):
        diag.append(f"진단용: 관문에서 무효인 회차 {len(b_bad) + len(c_bad)}개를 통계에 넣음(판정에 쓰지 말 것)")
        b_use, c_use = list(base.runs), list(cand.runs)
        for x in b_bad + c_bad:
            x["included"] = True
    invalid, warns = R.consistency(b_use or base.runs, c_use or cand.runs)
    warns = diag + warns
    inter = R.interleaved(b_use, c_use)
    if not inter:
        warns.append("교차(ABAB) 아님: 두 묶음을 다른 시각에 따로 재서 묶음 간 오프셋이 차이에 섞일 수 있음")

    how = pairing
    if how == "auto":
        how = "round" if inter else "none"
    pairs = R.pair_up(b_use, c_use, how) if how != "none" else None
    if pairs and len(pairs) < min(len(b_use), len(c_use)):
        warns.append(f"쌍을 이루지 못한 회차가 있어 쌍 {len(pairs)}개만 씀")
    if pairs:
        bvals, cvals = R.paired_values(pairs)
    else:
        bvals, cvals = R.metric_values(b_use), R.metric_values(c_use)

    compat = any(r.meta.get("compat_mode") for r in b_use + c_use)
    compat_skipped = []
    if compat:
        rb, rc = R.metric_values(b_use), R.metric_values(c_use)
        compat_skipped = sorted(i for i in set(rb) | set(rc) if not (set(rb.get(i, {})) & set(rc.get(i, {}))))
        for iid in sorted(set(bvals) | set(cvals)):
            keys = set(bvals.get(iid, {})) & set(cvals.get(iid, {}))
            if not keys:
                bvals.pop(iid, None), cvals.pop(iid, None)
                continue
            for d in (bvals, cvals):
                for k in list(d.get(iid, {})):
                    if k not in keys:
                        d[iid].pop(k)
    items: dict[str, dict] = {}
    stat_counts: dict[str, int] = {}
    verdict_counts: dict[str, int] = {}
    for iid in sorted(set(bvals) | set(cvals)):
        item = manifest.get(iid)
        if item is None or layer not in item.get("by_layer", {}):
            warns.append(f"{iid}: 매니페스트에서 {layer} 층에 없는 항목이라 건너뜀")
            continue
        d = J.judge_item_runs(item, layer, bvals.get(iid, {}), cvals.get(iid, {}), paired=bool(pairs),
                              cfg=cfg, noise_floor=noise_floor)
        items[iid] = d
        verdict_counts[d["outcome"]] = verdict_counts.get(d["outcome"], 0) + 1
        for m in d["metrics"].values():
            st = m.get("stat") or {}
            if st.get("info_only"):
                key = "info_only"
            elif st.get("outcome") == "different":
                key = "different_" + st.get("direction", "?")
            else:
                key = st.get("outcome") or ("excluded" if m.get("excluded") else "no_stat")
            stat_counts[key] = stat_counts.get(key, 0) + 1

    summary = {"counts": verdict_counts, "stat_counts": stat_counts, "valid": not invalid,
               "paired": bool(pairs), "pairs": len(pairs) if pairs else 0, "interleaved": inter,
               "n_base": len(b_use), "n_cand": len(c_use), "n_invalid_runs": len(b_bad) + len(c_bad),
               "include_invalid": bool(include_invalid and (b_bad or c_bad))}
    if compat:
        summary["compat_mode"] = True
        summary["compat_skipped"] = compat_skipped
    if invalid:
        summary["invalid_reasons"] = invalid
    if warns:
        summary["warnings"] = warns
    method = "same_day" if layer == "L2" else "abab"
    out = {"schema": "perfkit/comparison@1", "layer": layer, "device_class": cls,
           "manifest_version": manifest.version, "manifest_sha": _uniq(all_runs, "manifest_sha") or manifest.sha256,
           "created_at": _now(), "method": method,
           "stat": {**cfg.to_dict(), "pairing": how if pairs else "none",
                    "noise_floor": noise_source or "등급별 기본값", **({"layer_cfg": cfg.layer_cfg(layer)} if cfg.layer_cfg(layer) else {})},
           "valid": not invalid,
           "base": _side_doc(base.label, base.runs, b_use, b_bad, base.spec),
           "cand": _side_doc(cand.label, cand.runs, c_use, c_bad, cand.spec),
           "env": _env((b_use + c_use) or all_runs),
           "items": items, "summary": summary}
    if settings is not None and getattr(settings, "profile", None):
        out["profile_name"] = settings.profile_name or "profile"
        out["profile_sha"] = settings.profile_sha
    return out


def compare_results(base: R.Side, cand: R.Side, *, manifest) -> list[dict]:
    """결정적 결과(L0·L1)를 층마다 diff."""
    from perfkit.diff.compare import diff_results

    out = []
    bmap = {r["layer"]: (r, ref) for r, ref in zip(base.results, base.result_refs or [None] * len(base.results))}
    cmap = {r["layer"]: (r, ref) for r, ref in zip(cand.results, cand.result_refs or [None] * len(cand.results))}
    for layer in sorted(cmap):
        if layer not in ("L0", "L1"):
            raise ValueError(f"결과 파일 비교는 L0·L1만(받은 층 {layer}). L2는 회차처럼 여러 번 잰 결과로 비교한다")
        c, cref = cmap[layer]
        b, bref = bmap.get(layer, (None, None))
        cmp = diff_results(b, c, manifest=manifest, base_ref=bref, cand_ref=cref)
        cmp["base"]["label"] = base.label if b is not None else "(없음)"
        cmp["cand"]["label"] = cand.label
        cmp["valid"] = True
        out.append(cmp)
    return out


def bundle(parts: list[dict], *, base_label: str, cand_label: str) -> dict:
    counts: dict[str, int] = {}
    for p in parts:
        for k, v in p["summary"]["counts"].items():
            counts[k] = counts.get(k, 0) + v
    return {"schema": SET_SCHEMA, "created_at": _now(), "base_label": base_label, "cand_label": cand_label,
            "layers": [p["layer"] for p in parts], "valid": all(p.get("valid", True) for p in parts),
            "parts": parts, "summary": {"counts": counts}}


def parts_of(doc: dict) -> list[dict]:
    if doc.get("schema") == SET_SCHEMA:
        return list(doc["parts"])
    return [doc]


def worst_stat(doc: dict) -> str:
    """bisect 등에서 쓸 한 줄 결과: worse(악화 있음) · same(모두 같음) · unknown."""
    parts = parts_of(doc)
    any_worse = any_unknown = False
    for p in parts:
        for it in p["items"].values():
            for m in (it.get("metrics") or {}).values():
                st = m.get("stat") or {}
                if st.get("info_only") or m.get("excluded"):
                    continue
                if st.get("outcome") == "different" and st.get("direction") == "worse":
                    any_worse = True
                elif st.get("outcome") == "inconclusive":
                    any_unknown = True
            if it.get("outcome") in ("fail", "warn") and p.get("method") == "diff":
                any_worse = True
    return "worse" if any_worse else ("unknown" if any_unknown else "same")
