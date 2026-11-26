"""통계 함수: 알려진 값과 맞추기(R의 wilcox.test·cor 예제 등)."""

import math
import unittest

from perfkit.compare import stats as S


class KnownValuesTest(unittest.TestCase):
    def test_wilcoxon_paired_known(self):
        # R: wilcox.test(x, y, paired = TRUE) → V = 40, p-value = 0.03906
        x = [1.83, 0.50, 1.62, 2.48, 1.68, 1.88, 1.55, 3.06, 1.30]
        y = [0.878, 0.647, 0.598, 2.05, 1.06, 1.29, 1.06, 3.14, 1.29]
        r = S.wilcoxon_signed_rank([a - b for a, b in zip(x, y)])
        self.assertEqual(r.statistic, 40.0)
        self.assertAlmostEqual(r.p, 0.0390625, places=6)
        self.assertTrue(r.exact)

    def test_mann_whitney_known(self):
        # R: wilcox.test(x, y, alternative = "g") → W = 35, p = 0.1272 → 양측 0.2544
        x = [0.80, 0.83, 1.89, 1.04, 1.45, 1.38, 1.91, 1.64, 0.73, 1.46]
        y = [1.15, 0.88, 0.90, 0.74, 1.21]
        r = S.mann_whitney_u(x, y)
        self.assertEqual(r.statistic, 5 * 10 - 35)   # U_y = n·m − W_x
        self.assertAlmostEqual(r.p, 0.2544, places=3)

    def test_mann_whitney_extreme_exact(self):
        r = S.mann_whitney_u([1, 2, 3, 4, 5, 6], [7, 8, 9, 10, 11, 12])
        self.assertAlmostEqual(r.p, 2 / math.comb(12, 6), places=9)

    def test_wilcoxon_min_p_by_n(self):
        self.assertAlmostEqual(S.wilcoxon_signed_rank([1, 2, 3, 4, 5]).p, 2 / 32)   # 5쌍은 0.0625가 최소
        self.assertAlmostEqual(S.wilcoxon_signed_rank([1, 2, 3, 4, 5, 6]).p, 2 / 64)

    def test_wilcoxon_ties_and_zeros(self):
        r = S.wilcoxon_signed_rank([0, 1, 1, -1, 2, 2, 2])
        self.assertEqual(r.n, 6)          # 0 차이는 뺌
        self.assertTrue(0 < r.p <= 1)

    def test_spearman_exact(self):
        rho, p = S.spearman([1, 2, 3, 4, 5], [1, 2, 3, 4, 5])
        self.assertAlmostEqual(rho, 1.0)
        self.assertAlmostEqual(p, 2 / 120)
        rho, p = S.spearman([1, 2, 3, 4, 5], [3, 3, 3, 3, 3])
        self.assertIsNone(rho)
        self.assertEqual(p, 1.0)

    def test_hodges_lehmann(self):
        self.assertEqual(S.hl_shift([1, 2, 3], [2, 3, 4]), 1.0)
        # Walsh 평균 {1,1.5,2,5.5,2,2.5,6,3,6.5,10}의 중앙값
        self.assertEqual(S.hl_paired([1, 2, 3, 10]), 2.75)

    def test_cliffs_delta(self):
        self.assertEqual(S.cliffs_delta([1, 2], [3, 4]), 1.0)
        self.assertEqual(S.cliffs_delta([3, 4], [1, 2]), -1.0)
        self.assertEqual(S.cliffs_delta([1, 2], [1, 2]), 0.0)
        self.assertEqual(S.cliffs_magnitude(0.5), "large")

    def test_ranks_midrank(self):
        self.assertEqual(S.ranks([10, 20, 20, 30]), [1.0, 2.5, 2.5, 4.0])


class BootstrapTest(unittest.TestCase):
    def test_seed_reproducible(self):
        a, b = [10, 11, 10.5, 10.2, 10.8], [11, 11.4, 11.1, 10.9, 11.6]
        c1 = S.bootstrap_shift_ci(a, b, paired=False, iters=2000, seed=3)
        c2 = S.bootstrap_shift_ci(a, b, paired=False, iters=2000, seed=3)
        self.assertEqual(c1, c2)
        self.assertGreater(c1[0], 0)

    def test_paired_requires_same_length(self):
        with self.assertRaises(ValueError):
            S.bootstrap_shift_ci([1, 2], [1, 2, 3], paired=True, iters=10)


class NoiseFloorTest(unittest.TestCase):
    def test_sigma_and_mde(self):
        nf = S.noise_floor([100, 102, 98, 101, 99, 100])
        self.assertAlmostEqual(nf["sigma_d_pct"], math.sqrt(2) * nf["cv_pct"])
        self.assertAlmostEqual(nf["mde_pct"]["6"], 2.8 * nf["sigma_d_pct"] / math.sqrt(6))

    def test_runs_needed(self):
        # σd 4%, 목표 5% → (2.8·4/5)² = 5.02 → 6
        self.assertEqual(S.runs_needed(4.0, 5.0), 6)
        self.assertEqual(S.runs_needed(1.0, 5.0), 2)


if __name__ == "__main__":
    unittest.main()
