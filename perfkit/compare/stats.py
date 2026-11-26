"""실행 단위 통계(표준 라이브러리만).

분석 단위는 회차(실행) 하나다. 회차마다 지표 값 하나(평균·중앙값 등)를 넣고, 프레임 수만 개를 표본으로 넣지 않는다.

- 효과 크기: Hodges-Lehmann 이동량(독립: median(b_j - a_i), 쌍: 쌍 차이의 Walsh 평균 중앙값),
  중앙값 비율 median(b)/median(a), Cliff's δ.
- 신뢰구간: 회차 단위 부트스트랩(난수 씨 고정 → 같은 입력이면 같은 구간), 백분위 구간.
- 검정: 쌍이면 Wilcoxon 부호순위, 독립이면 Mann-Whitney U. 동점은 중간 순위로 두고, 순위를 두 배 한 정수로
  정확 분포를 센다(쌍 50개·합계 60개 이하). 그보다 크면 동점 보정 정규 근사.
- 드리프트: 라운드 번호와 쌍 차이의 Spearman 순위상관(8개 이하 정확 순열, 넘으면 씨 고정 무작위 순열).
- 잡음 바닥: 같은 빌드 A/A 회차의 변동계수 → 쌍 차이 표준편차 σ_d(%) ≈ √2·CV,
  검출 가능한 최소 변화 MDE(n) ≈ 2.8·σ_d/√n(유의수준 0.05 양측, 검정력 80%), 필요한 회차 n = (2.8·σ_d/MDE)².
"""

from __future__ import annotations

import itertools
import math
import random
import statistics
from dataclasses import dataclass
from typing import Sequence

Z_POWER = 2.8  # z(0.975) + z(0.8) ≈ 1.96 + 0.84
EXACT_SIGNED_MAX = 50
EXACT_RANKSUM_MAX = 60
EXACT_PERM_MAX = 8


# --------------------------------------------------------------------------- 기초

def median(xs: Sequence[float]) -> float:
    if not xs:
        raise ValueError("빈 목록의 중앙값")
    return float(statistics.median(xs))


def quantile(sorted_xs: Sequence[float], q: float) -> float:
    """선형 보간 분위수(정렬된 목록)."""
    n = len(sorted_xs)
    if n == 0:
        raise ValueError("빈 목록의 분위수")
    if n == 1:
        return float(sorted_xs[0])
    pos = q * (n - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return float(sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * frac)


def ranks(xs: Sequence[float]) -> list[float]:
    """중간 순위(1부터, 동점은 평균 순위)."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        r = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            out[order[k]] = r
        i = j + 1
    return out


def _norm_sf(z: float) -> float:
    """표준정규 위쪽 꼬리 확률."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def pct_of(delta: float, base: float) -> float | None:
    if base == 0:
        return None
    return delta / abs(base) * 100.0


# --------------------------------------------------------------------------- 효과 크기

def hl_shift(a: Sequence[float], b: Sequence[float]) -> float:
    """독립 두 묶음의 Hodges-Lehmann 이동량 median(b_j - a_i). 양수면 b가 큼."""
    return median([y - x for x in a for y in b])


def hl_paired(d: Sequence[float]) -> float:
    """쌍 차이의 Hodges-Lehmann 추정(Walsh 평균 (d_i + d_j)/2, i ≤ j 의 중앙값)."""
    n = len(d)
    return median([(d[i] + d[j]) / 2.0 for i in range(n) for j in range(i, n)])


def ratio_of_medians(a: Sequence[float], b: Sequence[float]) -> float | None:
    ma = median(a)
    return None if ma == 0 else median(b) / ma


def cliffs_delta(a: Sequence[float], b: Sequence[float]) -> float:
    """(#(b > a) - #(b < a)) / (n·m). +1이면 b가 모두 큼."""
    gt = lt = 0
    for x in a:
        for y in b:
            if y > x:
                gt += 1
            elif y < x:
                lt += 1
    return (gt - lt) / (len(a) * len(b))


def cliffs_magnitude(delta: float) -> str:
    """Romano 등(2006) 문턱: |δ| < 0.147 무시, < 0.33 작음, < 0.474 중간, 그 밖 큼."""
    v = abs(delta)
    if v < 0.147:
        return "negligible"
    if v < 0.33:
        return "small"
    if v < 0.474:
        return "medium"
    return "large"


# --------------------------------------------------------------------------- 검정

@dataclass
class TestResult:
    name: str
    statistic: float
    p: float
    n: int
    exact: bool

    def to_dict(self) -> dict:
        return {"test": self.name, "statistic": round(self.statistic, 6), "p": round(self.p, 6),
                "n": self.n, "exact": self.exact}


def _subset_sum_counts(weights: Sequence[int]) -> dict[int, int]:
    """각 원소를 넣거나 빼서 만드는 합의 개수(2^n 가지)."""
    dp = {0: 1}
    for w in weights:
        nxt = dict(dp)
        for s, c in dp.items():
            nxt[s + w] = nxt.get(s + w, 0) + c
        dp = nxt
    return dp


def wilcoxon_signed_rank(d: Sequence[float]) -> TestResult:
    """Wilcoxon 부호순위 검정(양측). 0 차이는 뺀다(Wilcoxon 방식)."""
    nz = [x for x in d if x != 0]
    n = len(nz)
    if n == 0:
        return TestResult("wilcoxon", 0.0, 1.0, 0, True)
    r = ranks([abs(x) for x in nz])
    w_plus = sum(rk for rk, x in zip(r, nz) if x > 0)
    if n <= EXACT_SIGNED_MAX:
        w2 = [int(round(2 * rk)) for rk in r]
        counts = _subset_sum_counts(w2)
        total = 2 ** n
        obs = int(round(2 * w_plus))
        le = sum(c for s, c in counts.items() if s <= obs)
        ge = sum(c for s, c in counts.items() if s >= obs)
        p = min(1.0, 2.0 * min(le, ge) / total)
        return TestResult("wilcoxon", w_plus, p, n, True)
    mean = n * (n + 1) / 4.0
    ties = {}
    for rk in r:
        ties[rk] = ties.get(rk, 0) + 1
    var = n * (n + 1) * (2 * n + 1) / 24.0 - sum(t ** 3 - t for t in ties.values()) / 48.0
    if var <= 0:
        return TestResult("wilcoxon", w_plus, 1.0, n, False)
    z = (abs(w_plus - mean) - 0.5) / math.sqrt(var)
    return TestResult("wilcoxon", w_plus, min(1.0, 2.0 * _norm_sf(max(z, 0.0))), n, False)


def mann_whitney_u(a: Sequence[float], b: Sequence[float]) -> TestResult:
    """Mann-Whitney U 검정(양측). statistic = U_b = #(b > a) + 0.5·#(동점)."""
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        raise ValueError("빈 묶음")
    r = ranks(list(a) + list(b))
    rb = sum(r[n:])
    u_b = rb - m * (m + 1) / 2.0
    N = n + m
    if N <= EXACT_RANKSUM_MAX:
        w2 = [int(round(2 * x)) for x in r]
        # dp[k][s]: k개를 골라 합(두 배 순위)이 s인 경우의 수
        dp: list[dict[int, int]] = [dict() for _ in range(m + 1)]
        dp[0][0] = 1
        for w in w2:
            for k in range(m, 0, -1):
                prev = dp[k - 1]
                cur = dp[k]
                for s, c in prev.items():
                    cur[s + w] = cur.get(s + w, 0) + c
        dist = dp[m]
        total = math.comb(N, m)
        obs = int(round(2 * rb))
        le = sum(c for s, c in dist.items() if s <= obs)
        ge = sum(c for s, c in dist.items() if s >= obs)
        p = min(1.0, 2.0 * min(le, ge) / total)
        return TestResult("mann_whitney", u_b, p, N, True)
    mean = n * m / 2.0
    ties = {}
    for x in r:
        ties[x] = ties.get(x, 0) + 1
    var = n * m / 12.0 * ((N + 1) - sum(t ** 3 - t for t in ties.values()) / (N * (N - 1)))
    if var <= 0:
        return TestResult("mann_whitney", u_b, 1.0, N, False)
    z = (abs(u_b - mean) - 0.5) / math.sqrt(var)
    return TestResult("mann_whitney", u_b, min(1.0, 2.0 * _norm_sf(max(z, 0.0))), N, False)


def _pearson(x: Sequence[float], y: Sequence[float]) -> float | None:
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((v - mx) ** 2 for v in x)
    syy = sum((v - my) ** 2 for v in y)
    if sxx == 0 or syy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / math.sqrt(sxx * syy)


def spearman(x: Sequence[float], y: Sequence[float], *, seed: int = 1, perms: int = 20000) -> tuple[float | None, float]:
    """Spearman 순위상관과 양측 순열 p. 값이 모두 같으면 (None, 1.0)."""
    n = len(x)
    if n != len(y):
        raise ValueError("길이가 다름")
    if n < 3:
        return None, 1.0
    rx, ry = ranks(x), ranks(y)
    rho = _pearson(rx, ry)
    if rho is None:
        return None, 1.0
    eps = 1e-12
    if n <= EXACT_PERM_MAX:
        hits = total = 0
        for p in itertools.permutations(ry):
            r = _pearson(rx, p) or 0.0
            total += 1
            if abs(r) >= abs(rho) - eps:
                hits += 1
        return rho, hits / total
    rng = random.Random(seed)
    yy = list(ry)
    hits = 0
    for _ in range(perms):
        rng.shuffle(yy)
        r = _pearson(rx, yy) or 0.0
        if abs(r) >= abs(rho) - eps:
            hits += 1
    return rho, (hits + 1) / (perms + 1)


# --------------------------------------------------------------------------- 부트스트랩

def bootstrap_shift_ci(base: Sequence[float], cand: Sequence[float], *, paired: bool, iters: int = 10000,
                       seed: int = 1, alpha: float = 0.05, relative: bool = True) -> tuple[float, float] | None:
    """회차 단위 부트스트랩으로 HL 이동량의 (1-alpha) 백분위 구간.

    relative=True면 표본마다 기준 중앙값 대비 %로 바꿔 구간을 낸다(기준 중앙값이 0인 표본이 나오면 None).
    paired=True면 같은 라운드 쌍을 함께 다시 뽑는다.
    """
    rng = random.Random(seed)
    n, m = len(base), len(cand)
    if paired and n != m:
        raise ValueError("쌍 부트스트랩인데 길이가 다름")
    out = []
    for _ in range(iters):
        if paired:
            idx = [rng.randrange(n) for _ in range(n)]
            bs = [base[i] for i in idx]
            d = [cand[i] - base[i] for i in idx]
            shift = hl_paired(d)
        else:
            bs = [base[rng.randrange(n)] for _ in range(n)]
            cs = [cand[rng.randrange(m)] for _ in range(m)]
            shift = hl_shift(bs, cs)
        if relative:
            bm = median(bs)
            if bm == 0:
                return None
            out.append(shift / abs(bm) * 100.0)
        else:
            out.append(shift)
    out.sort()
    return quantile(out, alpha / 2.0), quantile(out, 1.0 - alpha / 2.0)


# --------------------------------------------------------------------------- 잡음 바닥

def mde_pct(sigma_d_pct: float, n: int) -> float:
    """쌍 n개(또는 한쪽 n회 ABAB)로 검출 가능한 최소 변화(%)."""
    return Z_POWER * sigma_d_pct / math.sqrt(n)


def runs_needed(sigma_d_pct: float, target_pct: float) -> int:
    """MDE를 target_pct까지 낮추는 데 필요한 쌍(라운드) 수."""
    if target_pct <= 0:
        raise ValueError("target_pct > 0")
    return max(2, math.ceil((Z_POWER * sigma_d_pct / target_pct) ** 2))


def noise_floor(values: Sequence[float], *, pairs: Sequence[tuple[float, float]] | None = None) -> dict:
    """같은 빌드 A/A 회차 값으로 지표 하나의 잡음 바닥.

    sigma_d_pct = √2·CV(회차가 서로 독립이라고 볼 때 두 회차 차이의 표준편차, 기준 중앙값 대비 %).
    pairs(이웃 회차 쌍)가 3개 이상이면 쌍 차이로 직접 잰 값 sigma_d_emp_pct도 남긴다.
    """
    xs = [float(v) for v in values]
    n = len(xs)
    med = median(xs)
    out: dict = {"n": n, "median": med, "min": min(xs), "max": max(xs)}
    if n < 2 or med == 0:
        out.update({"cv_pct": None, "sigma_d_pct": None})
        return out
    sd = statistics.stdev(xs)
    mad = median([abs(x - med) for x in xs])
    cv = sd / abs(med) * 100.0
    out.update({
        "sd": sd,
        "cv_pct": cv,
        "mad_cv_pct": 1.4826 * mad / abs(med) * 100.0,
        "spread_pct": (max(xs) - min(xs)) / abs(med) * 100.0,
        "sigma_d_pct": math.sqrt(2.0) * cv,
    })
    if pairs and len(pairs) >= 3:
        diffs = [(b - a) / abs(med) * 100.0 for a, b in pairs]
        out["sigma_d_emp_pct"] = statistics.stdev(diffs)
    out["mde_pct"] = {str(k): mde_pct(out["sigma_d_pct"], k) for k in (5, 6, 8, 10)}
    return out
