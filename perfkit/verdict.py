"""판정 규칙: 절대 기준 + 잡음 등급별 변화 규칙(기본 판정 규칙, 1판 제안값).

두 축
1. 절대 기준(`judge_absolute`): 매니페스트 `criteria.absolute` 규칙. L0·L1·L3-D·console에만 쓴다.
   L2(에디터, 추세용)와 L3-E(에뮬레이터)에 쓰면 `AbsoluteNotAllowedError`.
2. 기준 대비 변화(`judge_change`): 잡음 등급마다 문턱이 다르다.

   | 잡음 | 경고 | 실패 | 개선(거꾸로) |
   |---|---|---|---|
   | none | 개수 +1건 이상, 크기 +2% | increase_is_fail 개수 증가, 크기 +5% | 개수 -1건, 크기 -2% |
   | low | +5%, 기준 0에서 새로 생김 | +10%, zero_is_hard 지표가 0에서 깨짐 | -5% |
   | medium | 기준 회차 범위 밖 + 5% | 범위 밖 + 10% | 범위 밖 - 5% |
   | high·very_high·unknown | 단독 판정 안 함: 후보 중앙값이 범위 밖이면 review | (절대 기준이 모든 회차에서 넘을 때만) | review |
   | pending_aa | 판단 보류(pending) | | |

ABAB 교차 회차 통계는 비교 모듈이 `register_stat_judge`로 붙인다. 붙이면 range 규칙 대신 통계 결과(same·different·
inconclusive)를 쓰고, 방향·크기 문턱은 위 표를 그대로 쓴다.

판정 값: pass 통과 · improved 개선 · warn 경고 · fail 실패 · review 확인 필요 · pending 판단 보류 ·
record 기록만 · not_collected 수집 못 함.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass, field
from typing import Callable, Iterable, Protocol, Sequence

OUTCOMES = ("pass", "improved", "warn", "fail", "review", "pending", "record", "not_collected")
LABEL_KO = {
    "pass": "통과", "improved": "개선", "warn": "경고", "fail": "실패", "review": "확인 필요",
    "pending": "판단 보류", "record": "기록만", "not_collected": "수집 못 함",
}
_SEVERITY = {"record": 0, "pass": 1, "improved": 2, "pending": 3, "not_collected": 4, "review": 5, "warn": 6, "fail": 7}

LAYER_OF_CLASS = {"device": "L3-D", "emulator": "L3-E"}
NO_ABSOLUTE_LAYERS = ("L2", "L3-E")
HIGH_NOISE = ("high", "very_high", "unknown")

# 기본 문턱(퍼센트). 매니페스트 noise_rules와 같은 값.
NONE_AMOUNT_WARN, NONE_AMOUNT_FAIL = 2.0, 5.0
LOW_WARN, LOW_FAIL = 5.0, 10.0
MEDIUM_WARN, MEDIUM_FAIL = 5.0, 10.0
DEFAULT_MIN_BASE_RUNS = 3


class AbsoluteNotAllowedError(ValueError):
    """절대 기준을 쓰면 안 되는 층(L2·L3-E)에 썼다."""


class EmulatorAbsoluteError(AbsoluteNotAllowedError):
    """에뮬레이터 등급(L3-E)에 절대 기준을 썼다."""


class ClassMixError(ValueError):
    """등급(device_class)이 다른 회차를 한 비교에 섞었다."""


def worst(outcomes: Iterable[str]) -> str:
    outs = list(outcomes)
    if not outs:
        return "record"
    return max(outs, key=lambda o: _SEVERITY[o])


# --------------------------------------------------------------------------- 등급

def layer_for_class(device_class: str) -> str:
    try:
        return LAYER_OF_CLASS[device_class]
    except KeyError:
        raise ValueError(f"모르는 device_class: {device_class}") from None


def ensure_same_class(metas: Iterable[dict]) -> str:
    """회차 메타들이 한 등급인지 확인하고 그 등급을 돌려준다. 섞이면 ClassMixError."""
    classes = {m.get("device_class") for m in metas}
    if len(classes) != 1 or None in classes:
        raise ClassMixError(f"등급이 다른 회차를 섞을 수 없음: {sorted(map(str, classes))}")
    return classes.pop()


def usable_runs(metas: Iterable[dict]) -> list[dict]:
    """관문(호스트 유휴·식힘)을 통과한 회차만. valid가 False면 뺀다."""
    return [m for m in metas if m.get("valid", True) is not False]


# --------------------------------------------------------------------------- 값 도우미

def _as_list(v) -> list:
    if isinstance(v, list):
        return v
    return [v]


def _num(v):
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _nums(v) -> list[float] | None:
    out = [_num(x) for x in _as_list(v)]
    if not out or any(x is None for x in out):
        return None
    return out


def _median(xs: Sequence[float]) -> float:
    return float(statistics.median(xs))


def _worse_delta(b: float, c: float, better: str) -> float:
    """나빠지는 쪽이 양수."""
    return (b - c) if better == "higher" else (c - b)


def _pct(delta: float, base: float) -> float:
    if base == 0:
        return 0.0 if delta == 0 else math.copysign(math.inf, delta)
    return delta / abs(base) * 100.0


def _bad(value, op: str, threshold) -> bool:
    if op == "gt":
        return value > threshold
    if op == "ge":
        return value >= threshold
    if op == "lt":
        return value < threshold
    if op == "le":
        return value <= threshold
    if op == "eq":
        return value == threshold
    if op == "ne":
        return value != threshold
    raise ValueError(f"모르는 op: {op}")


# --------------------------------------------------------------------------- 결과 모양

@dataclass
class MetricVerdict:
    key: str
    outcome: str
    reasons: list[str] = field(default_factory=list)
    base: object = None
    cand: object = None
    base_range: list | None = None
    delta: float | None = None
    delta_pct: float | None = None
    interval: list | None = None
    noise: str | None = None
    change: str | None = None
    absolute: str | None = None

    def to_dict(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if v is not None and k != "key"}
        for k in ("delta", "delta_pct"):
            if k in d and isinstance(d[k], float) and math.isinf(d[k]):
                d[k] = None  # JSON에 inf를 쓰지 않는다
        return d


@dataclass
class ItemVerdict:
    id: str
    layer: str
    outcome: str
    metrics: dict[str, MetricVerdict] = field(default_factory=dict)
    absolute: str | None = None
    needs_device_confirm: bool = False
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """comparison 스키마의 items.<ID> 모양."""
        d = {"outcome": self.outcome, "metrics": {k: m.to_dict() for k, m in self.metrics.items()}}
        if self.absolute:
            d["absolute"] = {"outcome": self.absolute}
        if self.needs_device_confirm:
            d["needs_device_confirm"] = True
        if self.reasons:
            d["reasons"] = self.reasons
        return d


# --------------------------------------------------------------------------- ABAB 통계(인터페이스)

@dataclass
class StatResult:
    """ABAB 통계 판정 결과. outcome: same(차이 없음) · different(차이 있음) · inconclusive(판단 보류)."""
    outcome: str
    delta: float | None = None          # 후보 - 기준(원 단위)
    delta_pct: float | None = None      # 기준 대비 %
    interval: tuple[float, float] | None = None
    detail: dict = field(default_factory=dict)


class StatJudge(Protocol):
    def __call__(self, base_runs: Sequence[float], cand_runs: Sequence[float], *,
                 better: str, noise: str, noise_floor_pct: float | None) -> StatResult: ...


_STAT_JUDGES: dict[str, StatJudge] = {}


def register_stat_judge(name: str, fn: StatJudge) -> StatJudge:
    """ABAB 판정기를 붙인다: register_stat_judge("abab_xxx", fn)."""
    _STAT_JUDGES[name] = fn
    return fn


def get_stat_judge(name: str) -> StatJudge:
    try:
        return _STAT_JUDGES[name]
    except KeyError:
        raise KeyError(f"등록되지 않은 통계 판정기: {name} (등록: {sorted(_STAT_JUDGES)})") from None


def _from_stat(res: StatResult, noise: str, better: str, base_med: float) -> tuple[str, list[str], float | None]:
    if res.outcome == "same":
        return "pass", ["통계: 차이 없음"], res.delta_pct
    if res.outcome == "inconclusive":
        return "pending", ["통계: 판단 보류"], res.delta_pct
    if res.outcome != "different":
        raise ValueError(f"StatResult.outcome 값이 아님: {res.outcome}")
    pct = res.delta_pct
    if pct is None and res.delta is not None:
        pct = _pct(res.delta, base_med)
    if pct is None:
        return "review", ["통계: 다름(크기 모름)"], None
    worse_pct = -pct if better == "higher" else pct
    if noise in HIGH_NOISE:
        return "review", [f"통계: 다름 {pct:+.1f}%(잡음 높음, 단독 판정 안 함)"], pct
    warn_t, fail_t = (LOW_WARN, LOW_FAIL) if noise == "low" else (MEDIUM_WARN, MEDIUM_FAIL)
    if worse_pct >= fail_t:
        return "fail", [f"통계: 나빠짐 {pct:+.1f}% ≥ {fail_t}%"], pct
    if worse_pct >= warn_t:
        return "warn", [f"통계: 나빠짐 {pct:+.1f}% ≥ {warn_t}%"], pct
    if worse_pct <= -warn_t:
        return "improved", [f"통계: 좋아짐 {pct:+.1f}%"], pct
    return "pass", [f"통계: 다르지만 문턱 안 {pct:+.1f}%"], pct


# --------------------------------------------------------------------------- 변화 판정

def judge_change(base, cand, *, noise: str, kind: str = "amount", better: str = "lower",
                 zero_is_hard: bool = False, increase_is_fail: bool = False,
                 min_base_runs: int = DEFAULT_MIN_BASE_RUNS, stat: StatJudge | str | None = None,
                 noise_floor_pct: float | None = None, key: str = "value") -> MetricVerdict:
    """기준(base)과 후보(cand) 값(하나 또는 회차별 목록)의 변화를 잡음 등급 규칙으로 판정한다."""
    mv = MetricVerdict(key=key, outcome="pass", base=base, cand=cand, noise=noise)
    if noise == "n/a":
        mv.outcome, mv.change = "record", "record"
        return mv
    if noise == "pending_aa":
        mv.outcome = mv.change = "pending"
        mv.reasons.append("에뮬레이터 잡음 등급 대기(A/A 잡음 바닥을 프로필에 채워야 함)")
        return mv

    bn, cn = _nums(base), _nums(cand)
    if kind == "snapshot" or bn is None or cn is None or (better == "none" and noise != "none"):
        if better == "none" and bn is not None and cn is not None and noise != "none":
            mv.outcome = mv.change = "record"
            mv.reasons.append("좋아지는 방향이 없는 잡음 지표: 기록만")
            return mv
        same = _as_list(base) == _as_list(cand)
        mv.outcome = mv.change = "pass" if same else "review"
        if not same:
            mv.reasons.append("설정·값 묶음이 바뀜: 확인 필요")
        return mv

    b_med, c_med = _median(bn), _median(cn)
    mv.delta = c_med - b_med
    mv.delta_pct = _pct(mv.delta, b_med)
    if better == "none":
        mv.outcome = mv.change = "pass" if mv.delta == 0 else "review"
        if mv.delta:
            mv.reasons.append("방향 없는 값이 바뀜: 확인 필요")
        return mv
    worse = _worse_delta(b_med, c_med, better)
    worse_pct = _pct(worse, b_med)

    if stat is not None and noise != "none":
        fn = get_stat_judge(stat) if isinstance(stat, str) else stat
        res = fn(bn, cn, better=better, noise=noise, noise_floor_pct=noise_floor_pct)
        mv.interval = list(res.interval) if res.interval else None
        out, why, pct = _from_stat(res, noise, better, b_med)
        if pct is not None:
            mv.delta_pct = pct
        if zero_is_hard and b_med == 0 and worse > 0:
            out, why = "fail", why + ["기준 0에서 깨짐(zero_is_hard)"]
        mv.outcome = mv.change = out
        mv.reasons += why
        return mv

    if zero_is_hard and b_med == 0 and worse > 0:
        mv.outcome = mv.change = "fail"
        mv.reasons.append(f"기준 0에서 {c_med:g}로 깨짐(zero_is_hard)")
        return mv

    if noise == "none":
        if kind in ("count", "bool"):
            if worse >= 1:
                out = "fail" if increase_is_fail else "warn"
                mv.reasons.append(f"나빠지는 쪽 {worse:g}건" + (" (증가 = 실패)" if increase_is_fail else ""))
            elif worse <= -1:
                out = "improved"
                mv.reasons.append(f"좋아지는 쪽 {-worse:g}건")
            else:
                out = "pass"
        else:
            if worse_pct >= NONE_AMOUNT_FAIL:
                out = "fail"
            elif worse_pct >= NONE_AMOUNT_WARN:
                out = "warn"
            elif worse_pct <= -NONE_AMOUNT_WARN:
                out = "improved"
            else:
                out = "pass"
            if out != "pass":
                mv.reasons.append(f"{mv.delta_pct:+.2f}% (경고 {NONE_AMOUNT_WARN}%·실패 {NONE_AMOUNT_FAIL}%)")
        mv.outcome = mv.change = out
        return mv

    if noise == "low":
        if b_med == 0 and worse > 0:
            out = "warn"
            mv.reasons.append(f"기준 0에서 새로 생김({c_med:g})")
        elif worse_pct >= LOW_FAIL:
            out = "fail"
        elif worse_pct >= LOW_WARN:
            out = "warn"
        elif worse_pct <= -LOW_WARN:
            out = "improved"
        else:
            out = "pass"
        if out != "pass" and not mv.reasons:
            mv.reasons.append(f"{mv.delta_pct:+.2f}% (경고 {LOW_WARN}%·실패 {LOW_FAIL}%)")
        mv.outcome = mv.change = out
        return mv

    # medium·high: 기준 회차 범위가 필요
    if len(bn) < min_base_runs:
        mv.outcome = mv.change = "pending"
        mv.reasons.append(f"기준 회차 {len(bn)}개 < {min_base_runs}개(범위를 만들 수 없음)")
        return mv
    lo, hi = min(bn), max(bn)
    mv.base_range = [lo, hi]
    outside = c_med < lo or c_med > hi

    if noise == "medium":
        if outside and worse_pct >= MEDIUM_FAIL:
            out = "fail"
        elif outside and worse_pct >= MEDIUM_WARN:
            out = "warn"
        elif outside and worse_pct <= -MEDIUM_WARN:
            out = "improved"
        else:
            out = "pass"
        if out != "pass":
            mv.reasons.append(f"범위 [{lo:g}, {hi:g}] 밖 {mv.delta_pct:+.2f}%")
        mv.outcome = mv.change = out
        return mv

    if noise in HIGH_NOISE:
        out = "review" if outside else "pass"
        if outside:
            mv.reasons.append(f"잡음 높음: 중앙값 {c_med:g}이 범위 [{lo:g}, {hi:g}] 밖 → 확인 필요(단독 판정 안 함)")
        mv.outcome = mv.change = out
        return mv

    raise ValueError(f"모르는 잡음 등급: {noise}")


# --------------------------------------------------------------------------- 절대 기준

def judge_absolute(values, rule: dict, *, layer: str, noise: str = "none",
                   ram_class: str | None = None) -> tuple[str, list[str]]:
    """규칙 하나로 값(하나 또는 회차별 목록)을 판정한다. 돌려주는 값: (pass·warn·fail·review·pending, 이유)."""
    if layer == "L3-E":
        raise EmulatorAbsoluteError("에뮬레이터 등급(L3-E)에는 절대 기준을 쓰지 않음(기준 대비 변화만)")
    if layer in NO_ABSOLUTE_LAYERS:
        raise AbsoluteNotAllowedError(f"{layer}에는 절대 기준을 쓰지 않음(추세용)")
    if rule.get("layers") and layer not in rule["layers"]:
        return "pass", []

    warn_t, fail_t = rule.get("warn"), rule.get("fail")
    if "fail_by_ram_class" in rule or "warn_by_ram_class" in rule:
        if ram_class is None:
            return "pending", [f"{rule['metric']}: RAM 등급을 몰라 한도를 고를 수 없음"]
        fail_t = rule.get("fail_by_ram_class", {}).get(ram_class, fail_t)
        warn_t = rule.get("warn_by_ram_class", {}).get(ram_class, warn_t)
        if fail_t is None and warn_t is None:
            return "pending", [f"{rule['metric']}: RAM 등급 {ram_class} 한도 없음"]

    vals = _as_list(values)
    if any(v is None for v in vals) or not vals:
        return "pending", [f"{rule['metric']}: 값 없음"]
    op = rule["op"]
    numeric = all(_num(v) is not None and not isinstance(v, bool) for v in vals)
    tag = f"[{rule['basis']}{'·' + ','.join(rule['sources']) if rule.get('sources') else ''}]"

    def level(v) -> str:
        if fail_t is not None and _bad(v, op, fail_t):
            return "fail"
        if warn_t is not None and _bad(v, op, warn_t):
            return "warn"
        return "pass"

    if noise in HIGH_NOISE and len(vals) > 1:
        levels = [level(v) for v in vals]
        if all(l == "fail" for l in levels):
            return "fail", [f"{rule['metric']}: 모든 회차가 실패 기준 {op} {fail_t} {tag}"]
        if all(l in ("fail", "warn") for l in levels):
            return "warn", [f"{rule['metric']}: 모든 회차가 경고 기준 {op} {warn_t} {tag}"]
        if any(l != "pass" for l in levels):
            return "review", [f"{rule['metric']}: 일부 회차만 기준을 넘음(잡음 높음) {tag}"]
        return "pass", []

    v = _median([float(x) for x in vals]) if (numeric and len(vals) > 1) else vals[0]
    out = level(v)
    if out == "pass":
        return out, []
    t = fail_t if out == "fail" else warn_t
    return out, [f"{rule['metric']}: {v!r} {op} {t!r} {tag}"]


# --------------------------------------------------------------------------- 항목 판정

def judge_item(item: dict, layer: str, base: dict | None, cand: dict | None, *,
               ram_class: str | None = None, stat: StatJudge | str | None = None,
               min_base_runs: int = DEFAULT_MIN_BASE_RUNS, noise_floor_pct: float | None = None) -> ItemVerdict:
    """매니페스트 항목 하나를 층 하나에서 판정한다.

    base·cand: {지표 키: 값 또는 회차별 값 목록}. cand가 None이면 수집 못 함.
    base가 None이면 변화 판정 없이 절대 기준만(L0·L1·L3-D 점검 1회).
    """
    spec = item["by_layer"].get(layer)
    if spec is None:
        raise ValueError(f"{item['id']}는 {layer} 층에서 재지 않음(층 {item['layers']})")
    iv = ItemVerdict(id=item["id"], layer=layer, outcome="pass")
    judge = spec["judge"]
    if layer == "L3-E" and judge in ("absolute+change", "absolute_only"):
        raise EmulatorAbsoluteError(f"{item['id']}: L3-E 판정이 {judge}(에뮬레이터는 절대 기준 금지)")
    if layer in NO_ABSOLUTE_LAYERS and judge in ("absolute+change", "absolute_only"):
        raise AbsoluteNotAllowedError(f"{item['id']}: {layer}에는 절대 기준을 쓰지 않음")
    if judge == "record":
        iv.outcome = "record"
        return iv
    if cand is None:
        iv.outcome = "not_collected"
        iv.reasons.append("후보 값 없음")
        return iv

    defaults = {"unit": item["unit"], "better": item["better"], "kind": item["kind"]}
    mspecs = {m["key"]: {**defaults, **m} for m in (item.get("metrics") or [{"key": "value"}])}
    keys = list(dict.fromkeys(list(cand) + [k for k in (base or {}) if k in mspecs]))
    use_abs = judge in ("absolute+change", "absolute_only")
    use_change = judge in ("absolute+change", "change_only") and base is not None
    rules = item["criteria"]["absolute"] if use_abs else []

    for key in keys:
        ms = mspecs.get(key, {**defaults, "key": key})
        if key not in cand:
            iv.metrics[key] = MetricVerdict(key=key, outcome="not_collected", reasons=["후보 값 없음"])
            continue
        if use_change:
            if key not in base:
                mv = MetricVerdict(key=key, outcome="pending", cand=cand[key], reasons=["기준 값 없음"])
            else:
                mv = judge_change(base[key], cand[key], noise=spec["noise"], kind=ms["kind"], better=ms["better"],
                                  zero_is_hard=ms.get("zero_is_hard", False),
                                  increase_is_fail=ms.get("increase_is_fail", False),
                                  min_base_runs=min_base_runs, stat=stat, noise_floor_pct=noise_floor_pct, key=key)
        else:
            mv = MetricVerdict(key=key, outcome="pass", cand=cand[key], noise=spec["noise"])
        abs_outs = []
        for r in rules:
            if r["metric"] != key:
                continue
            out, why = judge_absolute(cand[key], r, layer=layer, noise=spec["noise"], ram_class=ram_class)
            abs_outs.append(out)
            mv.reasons += why
        if abs_outs:
            mv.absolute = worst(abs_outs)
            mv.outcome = worst([mv.outcome, mv.absolute])
        iv.metrics[key] = mv

    outs = [m.outcome for m in iv.metrics.values()]
    iv.outcome = worst(outs) if outs else "not_collected"
    abs_all = [m.absolute for m in iv.metrics.values() if m.absolute]
    if abs_all:
        iv.absolute = worst(abs_all)
    if layer == "L3-E" and any(m.outcome == "improved" for m in iv.metrics.values()):
        iv.needs_device_confirm = True
        iv.reasons.append("실기기 확인 대기")
    return iv


def summarize(verdicts: Iterable[ItemVerdict]) -> dict:
    counts: dict[str, int] = {}
    for v in verdicts:
        counts[v.outcome] = counts.get(v.outcome, 0) + 1
    return {"counts": counts}


# --------------------------------------------------------------------------- CLI

def _parse_values(xs: list[str]):
    out = []
    for x in xs:
        low = x.lower()
        if low in ("true", "false"):
            out.append(low == "true")
        else:
            try:
                out.append(float(x))
            except ValueError:
                out.append(x)
    return out[0] if len(out) == 1 else out


def _cmd_judge(args) -> int:
    from perfkit.manifest import load

    m = load()
    item = m.get(args.id)
    if item is None:
        print(f"없는 항목: {args.id}")
        return 1
    base = {args.metric: _parse_values(args.base)} if args.base else None
    cand = {args.metric: _parse_values(args.cand)}
    try:
        iv = judge_item(item, args.layer, base, cand, ram_class=args.ram_class, min_base_runs=args.min_base_runs)
    except (ValueError, KeyError) as exc:
        print(f"error: {exc}")
        return 1
    for k, mv in iv.metrics.items():
        print(f"{item['id']} {args.layer} {k}: {mv.outcome}({LABEL_KO[mv.outcome]}) noise={mv.noise}")
        for r in mv.reasons:
            print(f"  - {r}")
    print(f"=> {iv.outcome}({LABEL_KO[iv.outcome]})" + (" [실기기 확인 대기]" if iv.needs_device_confirm else ""))
    return 0


def register_cli(sub) -> None:
    p = sub.add_parser("verdict", help="판정 규칙을 값 하나로 시험")
    s2 = p.add_subparsers(dest="verdict_cmd", metavar="<sub>")
    j = s2.add_parser("judge", help="항목·층·지표 하나를 기준·후보 값으로 판정")
    j.add_argument("--id", required=True)
    j.add_argument("--layer", required=True, choices=("L0", "L1", "L2", "L3-E", "L3-D", "console"))
    j.add_argument("--metric", default="value")
    j.add_argument("--base", nargs="*", help="기준 값(회차별로 여러 개)")
    j.add_argument("--cand", nargs="+", required=True, help="후보 값(회차별로 여러 개)")
    j.add_argument("--ram-class", help="예: 8GB(메모리 한도 규칙)")
    j.add_argument("--min-base-runs", type=int, default=DEFAULT_MIN_BASE_RUNS)
    j.set_defaults(func=_cmd_judge)
    p.set_defaults(func=lambda a: (p.print_help(), 2)[1])
