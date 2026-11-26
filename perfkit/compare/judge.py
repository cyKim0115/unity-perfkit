"""회차 묶음 통계 판정: 같음(same) · 다름(different, 개선·악화) · 판단 보류(inconclusive).

판정 순서(지표 하나)
1. 판정 제외(프로필 `[compare."<층>"] exclude`)면 값만 싣고 판정하지 않는다(record).
2. 한쪽 회차가 `min_runs`보다 적으면 판단 보류.
3. 효과 = HL 이동량(쌍이면 쌍 차이, 독립이면 두 묶음), 기준 중앙값 대비 %.
   95% 구간 = 회차 단위 부트스트랩. 검정 = Wilcoxon(쌍)·Mann-Whitney(독립).
4. 같음: 구간 전체가 동등 범위 [-eq, +eq] 안(먼저 본다. 0이 아닌 작은 이동이면 minor_shift로 표시).
   다름: 그 밖에서, 구간이 0을 품지 않고, 검정 p < alpha이고, |효과| > 잡음 바닥.
   그 밖: 판단 보류(필요한 회차 수를 권고).
5. 쌍이면 라운드 번호와 쌍 차이의 순위상관(드리프트)을 본다. 유의하면 같음·다름을 판단 보류로 내린다.

이 결과를 `verdict.judge_change`의 통계 판정기로 넘겨 항목 판정(통과·개선·경고·실패·확인 필요)을 만든다.
경고·실패 문턱(잡음 등급별 5%·10% 등)은 매니페스트 판정 규칙 그대로다.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from typing import Sequence

from perfkit import verdict as V
from perfkit.compare import stats as S

DEFAULT_EQUIVALENCE_PCT = {"none": 1.0, "low": 3.0, "medium": 5.0, "high": 10.0, "very_high": 15.0, "unknown": 10.0}
DEFAULT_FLOOR_PCT = {"none": 0.0, "low": 1.0, "medium": 3.0, "high": 8.0, "very_high": 12.0, "unknown": 8.0}
STAT_NAME = "hl-bootstrap"
STAT_LABEL_KO = {"same": "같음", "different": "다름", "inconclusive": "판단 보류", "excluded": "판정 제외"}


@dataclass
class CompareConfig:
    """비교 설정. 기본값은 공개 기본, 프로젝트 값은 프로필 `[compare]`에서 덮는다."""
    iters: int = 10000
    seed: int = 1
    alpha: float = 0.05
    min_runs: int = 3
    equivalence_pct: dict = field(default_factory=lambda: dict(DEFAULT_EQUIVALENCE_PCT))
    floor_pct: dict = field(default_factory=lambda: dict(DEFAULT_FLOOR_PCT))
    layers: dict = field(default_factory=dict)   # "<층>": {exclude, gate, equivalence_pct, floor_pct}

    @classmethod
    def from_profile(cls, profile: dict | None) -> "CompareConfig":
        c = cls()
        sec = (profile or {}).get("compare") or {}
        c.iters = int(sec.get("bootstrap", c.iters))
        c.seed = int(sec.get("seed", c.seed))
        c.alpha = float(sec.get("alpha", c.alpha))
        c.min_runs = int(sec.get("min_runs", c.min_runs))
        c.equivalence_pct.update({k: float(v) for k, v in (sec.get("equivalence_pct") or {}).items()})
        c.floor_pct.update({k: float(v) for k, v in (sec.get("floor_pct") or {}).items()})
        c.layers = {k: v for k, v in sec.items() if isinstance(v, dict) and k.startswith("L")}
        return c

    def layer_cfg(self, layer: str) -> dict:
        return self.layers.get(layer) or {}

    def excluded(self, layer: str, item_id: str, key: str) -> bool:
        ex = self.layer_cfg(layer).get("exclude") or []
        return item_id in ex or f"{item_id}.{key}" in ex

    def equivalence(self, layer: str, item_id: str, key: str, noise: str) -> float:
        by = self.layer_cfg(layer).get("equivalence_pct") or {}
        for k in (f"{item_id}.{key}", item_id):
            if k in by:
                return float(by[k])
        return float(self.equivalence_pct.get(noise, self.equivalence_pct["unknown"]))

    def default_floor(self, layer: str, item_id: str, key: str, noise: str) -> float:
        by = self.layer_cfg(layer).get("floor_pct") or {}
        for k in (f"{item_id}.{key}", item_id):
            if k in by:
                return float(by[k])
        return float(self.floor_pct.get(noise, self.floor_pct["unknown"]))

    def to_dict(self) -> dict:
        return {"name": STAT_NAME, "bootstrap": self.iters, "seed": self.seed, "alpha": self.alpha,
                "min_runs": self.min_runs, "equivalence_pct": self.equivalence_pct, "floor_pct": self.floor_pct}


def metric_seed(base_seed: int, *parts: str) -> int:
    """지표마다 다르지만 실행마다 같은 씨(파이썬 hash는 실행마다 바뀌어 쓰지 않는다)."""
    return (base_seed * 1_000_003 + zlib.crc32("|".join(parts).encode("utf-8"))) & 0x7FFFFFFF


def _r(v, nd=4):
    return None if v is None else round(float(v), nd)


def compare_values(base: Sequence[float], cand: Sequence[float], *, paired: bool, better: str,
                   floor_pct: float, eq_pct: float, cfg: CompareConfig, seed: int,
                   floor_source: str = "default", sigma_d_pct: float | None = None) -> dict:
    """회차 값 두 묶음의 통계 판정. 돌려주는 dict의 outcome: same·different·inconclusive."""
    b, c = [float(x) for x in base], [float(x) for x in cand]
    out: dict = {"n_base": len(b), "n_cand": len(c), "paired": paired, "floor_pct": _r(floor_pct, 3),
                 "floor_source": floor_source, "equivalence_pct": _r(eq_pct, 3), "reasons": []}
    if len(b) < cfg.min_runs or len(c) < cfg.min_runs:
        out["outcome"] = "inconclusive"
        out["reasons"].append(f"회차 부족(기준 {len(b)}·후보 {len(c)} < {cfg.min_runs})")
        out["recommend_runs"] = max(cfg.min_runs, 5)
        return out
    bm, cm = S.median(b), S.median(c)
    out.update({"base_median": bm, "cand_median": cm})
    if paired:
        d = [y - x for x, y in zip(b, c)]
        shift = S.hl_paired(d)
        test = S.wilcoxon_signed_rank(d)
    else:
        shift = S.hl_shift(b, c)
        test = S.mann_whitney_u(b, c)
    shift_pct = S.pct_of(shift, bm)
    cd = S.cliffs_delta(b, c)
    out.update({"hl": _r(shift, 6), "hl_pct": _r(shift_pct), "ratio": _r(S.ratio_of_medians(b, c), 6),
                "cliffs_delta": _r(cd), "cliffs_magnitude": S.cliffs_magnitude(cd), **test.to_dict()})
    relative = bm != 0
    ci = S.bootstrap_shift_ci(b, c, paired=paired, iters=cfg.iters, seed=seed, alpha=cfg.alpha, relative=relative)
    if ci is None and relative:  # 표본 안에 기준 중앙값 0이 있음 → 원 단위로
        relative = False
        ci = S.bootstrap_shift_ci(b, c, paired=paired, iters=cfg.iters, seed=seed, alpha=cfg.alpha, relative=False)
    out["ci"] = [_r(ci[0]), _r(ci[1])]
    out["ci_unit"] = "pct" if relative else "abs"

    if b == c or (min(b) == max(b) == min(c) == max(c)):
        out["outcome"] = "same"
        out["reasons"].append("값이 모두 같음")
        return out

    lo, hi = ci
    excludes_zero = lo > 0 or hi < 0
    sig = test.p < cfg.alpha
    if relative:
        big = abs(shift_pct) > floor_pct
        within = -eq_pct <= lo and hi <= eq_pct
    else:
        big = shift != 0
        within = False
    if within:
        # 동등 범위가 먼저다(TOST 방식): 통계로 0이 아니어도 구간 전체가 범위 안이면 실질적으로 같다
        outcome = "same"
        out["reasons"].append(f"구간 [{lo:+.2f}, {hi:+.2f}]%가 동등 범위 ±{eq_pct:g}% 안")
        if excludes_zero and sig:
            out["minor_shift"] = True
            out["reasons"].append("0이 아닌 작은 이동이 있으나 동등 범위 안(실질 같음)")
    elif excludes_zero and sig and big:
        outcome = "different"
        out["reasons"].append(f"구간이 0을 품지 않고 p={test.p:.3g} < {cfg.alpha}, |효과| > 바닥 {floor_pct:.2f}%"
                              if relative else f"구간이 0을 품지 않고 p={test.p:.3g} < {cfg.alpha}")
    else:
        outcome = "inconclusive"
        why = []
        if not excludes_zero:
            why.append("구간이 0을 품음")
        if not sig:
            why.append(f"p={test.p:.3g} ≥ {cfg.alpha}")
            if paired and test.exact and len(b) < 6:
                why.append(f"쌍 {len(b)}개로는 Wilcoxon이 {cfg.alpha} 아래로 못 내려감(6쌍 이상 필요)")
        if relative and not big:
            why.append(f"|효과| {abs(shift_pct):.2f}% ≤ 바닥 {floor_pct:.2f}%")
        if relative and not within:
            why.append(f"구간이 동등 범위 ±{eq_pct:g}%를 넘음")
        out["reasons"].append("판단 보류: " + ", ".join(why))
        if sigma_d_pct:
            out["recommend_runs"] = S.runs_needed(sigma_d_pct, eq_pct)
    if paired and len(d) >= 5:
        rho, p_drift = S.spearman(list(range(1, len(d) + 1)), d, seed=seed)
        out["drift"] = {"rho": _r(rho), "p": _r(p_drift)}
        if rho is not None and p_drift < cfg.alpha:
            out["drift"]["flag"] = True
            if outcome != "inconclusive":
                out["reasons"].append(f"드리프트: 라운드와 쌍 차이 순위상관 ρ={rho:+.2f}(p={p_drift:.3g}) → 판단 보류")
                outcome = "inconclusive"
    out["outcome"] = outcome
    if outcome == "different":
        worse = (shift < 0) if better == "higher" else (shift > 0)
        out["direction"] = "worse" if worse else "better"
    return out


def stat_label(st: dict) -> str:
    o = st.get("outcome")
    if o == "different":
        return "다름·" + ("악화" if st.get("direction") == "worse" else "개선")
    return STAT_LABEL_KO.get(o, o or "-")


def judge_metric(item: dict, layer: str, key: str, mspec: dict, base: list, cand: list, *, paired: bool,
                 cfg: CompareConfig, noise_floor: dict | None) -> dict:
    """지표 하나: 통계 판정 + 항목 판정 규칙(verdict.judge_change). comparison 스키마의 metric 모양."""
    spec = item["by_layer"][layer]
    noise = spec["noise"]
    iid = item["id"]
    out = {"base": base, "cand": cand, "noise": noise}
    if cfg.excluded(layer, iid, key):
        out.update({"outcome": "record", "excluded": True, "reasons": ["이 등급에서 판정 제외(값만 실음)"]})
        out["stat"] = {"outcome": "excluded"}
        return out
    numeric = all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in list(base) + list(cand))
    if not numeric or mspec.get("better") == "none":
        out.update({"outcome": "record", "reasons": ["수치가 아니거나 좋아지는 방향이 없는 값: 기록만"]})
        return out
    nb, nc = [float(x) for x in base], [float(x) for x in cand]

    nf = (noise_floor or {}).get(f"{iid}.{key}") or {}
    sigma = nf.get("sigma_d_pct")
    if sigma is not None:
        floor, src = float(sigma), "aa"
    else:
        floor, src = cfg.default_floor(layer, iid, key, noise), "default"
    eq = cfg.equivalence(layer, iid, key, noise)
    captured: dict = {}

    def judge_fn(bn, cn, *, better, noise, noise_floor_pct):
        st = compare_values(bn, cn, paired=paired, better=better, floor_pct=floor, eq_pct=eq, cfg=cfg,
                            seed=metric_seed(cfg.seed, layer, iid, key), floor_source=src, sigma_d_pct=sigma)
        captured.update(st)
        ci = st.get("ci") if st.get("ci_unit") == "pct" else None
        return V.StatResult(st["outcome"], delta=st.get("hl"), delta_pct=st.get("hl_pct"),
                            interval=tuple(ci) if ci else None, detail=st)

    mv = V.judge_change(nb, nc, noise=noise, kind=mspec["kind"], better=mspec["better"],
                        zero_is_hard=mspec.get("zero_is_hard", False),
                        increase_is_fail=mspec.get("increase_is_fail", False),
                        min_base_runs=cfg.min_runs, stat=judge_fn if noise != "none" else None,
                        noise_floor_pct=floor, key=key)
    d = mv.to_dict()
    d.pop("base", None), d.pop("cand", None)
    out.update(d)
    if not captured and noise == "pending_aa":
        # 판정은 보류지만 수치는 참고로 싣는다(잡음 등급을 정할 때 쓴다)
        judge_fn(nb, nc, better=mspec["better"], noise="unknown", noise_floor_pct=floor)
        captured["info_only"] = True
    if captured:
        out["stat"] = captured
        if captured.get("hl") is not None:
            out["delta"] = captured["hl"]
            out["delta_pct"] = captured.get("hl_pct")
        if captured.get("ci_unit") == "pct" and captured.get("ci"):
            out["interval"] = captured["ci"]
        else:
            out.pop("interval", None)
    return out


def judge_item_runs(item: dict, layer: str, base_vals: dict, cand_vals: dict, *, paired: bool,
                    cfg: CompareConfig, noise_floor: dict | None) -> dict:
    """항목 하나: base_vals·cand_vals = {지표 키: 회차별 값 목록}(쌍이면 같은 순서)."""
    defaults = {"unit": item["unit"], "better": item["better"], "kind": item["kind"]}
    mspecs = {m["key"]: {**defaults, **m} for m in (item.get("metrics") or [{"key": "value"}])}
    keys = [k for k in mspecs if k in cand_vals or k in base_vals]
    keys += [k for k in list(cand_vals) + list(base_vals) if k not in mspecs and k not in keys]
    metrics: dict[str, dict] = {}
    reasons: list[str] = []
    spec = item["by_layer"][layer]
    if spec["judge"] == "record":
        return {"outcome": "record", "metrics": {}, "reasons": ["판정 방식 record(기록만)"]}
    for k in keys:
        ms = mspecs.get(k, {**defaults, "key": k})
        bv, cv = base_vals.get(k, []), cand_vals.get(k, [])
        if not cv:
            metrics[k] = {"outcome": "not_collected", "base": bv, "cand": cv, "reasons": ["후보 값 없음"]}
            continue
        if not bv:
            metrics[k] = {"outcome": "pending", "base": bv, "cand": cv, "reasons": ["기준 값 없음"]}
            continue
        metrics[k] = judge_metric(item, layer, k, ms, bv, cv, paired=paired, cfg=cfg, noise_floor=noise_floor)
    judged = [m["outcome"] for m in metrics.values() if not m.get("excluded")]
    outcome = V.worst(judged) if judged else ("record" if metrics else "not_collected")
    d = {"outcome": outcome, "metrics": metrics}
    better_stat = any((m.get("stat") or {}).get("direction") == "better" and not (m.get("stat") or {}).get("info_only")
                      for m in metrics.values())
    if layer == "L3-E" and (better_stat or any(m["outcome"] == "improved" for m in metrics.values())):
        d["needs_device_confirm"] = True
        reasons.append("실기기 확인 대기")
    if spec["noise"] == "pending_aa":
        reasons.append("잡음 등급 대기(pending_aa): 프로필에 A/A 결과를 넣어야 판정")
    if reasons:
        d["reasons"] = reasons
    return d


def _abab_judge(base_runs, cand_runs, *, better, noise, noise_floor_pct):
    """verdict 인터페이스용 기본 판정기(독립 묶음, 기본 설정)."""
    cfg = CompareConfig()
    st = compare_values(base_runs, cand_runs, paired=False, better=better,
                        floor_pct=noise_floor_pct if noise_floor_pct is not None else cfg.floor_pct.get(noise, 3.0),
                        eq_pct=cfg.equivalence_pct.get(noise, 5.0), cfg=cfg, seed=cfg.seed)
    ci = st.get("ci") if st.get("ci_unit") == "pct" else None
    return V.StatResult(st["outcome"], delta=st.get("hl"), delta_pct=st.get("hl_pct"),
                        interval=tuple(ci) if ci else None, detail=st)


V.register_stat_judge(STAT_NAME, _abab_judge)
