"""회차 묶음 비교: 같음·개선·악화·판단 보류·드리프트·등급 혼용 거부·무효 회차 제외·해시 불일치 무효."""

import json
import unittest
from pathlib import Path

from perfkit import verdict as V
from perfkit.compare import judge as J
from perfkit.schema import validate
from tests.fakeproj import isolated_settings
from tests.runsfake import PROFILE_COMPARE, abab_session, noisy, values
from tests.test_commands import run as cli


def _load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


class CompareValuesTest(unittest.TestCase):
    cfg = J.CompareConfig(iters=2000)

    def _cv(self, b, c, paired=True, floor=1.0, eq=3.0, better="lower"):
        return J.compare_values(b, c, paired=paired, better=better, floor_pct=floor, eq_pct=eq, cfg=self.cfg, seed=1)

    def test_same(self):
        b = [100.0, 100.4, 99.7, 100.1, 99.9, 100.2, 100.0, 99.8]
        c = [100.1, 100.2, 99.9, 99.8, 100.0, 100.3, 99.9, 100.1]
        st = self._cv(b, c)
        self.assertEqual(st["outcome"], "same")

    def test_different_better_and_worse(self):
        b = [100.0, 100.4, 99.7, 100.1, 99.9, 100.2, 100.0, 99.8]
        st = self._cv(b, [x * 0.9 for x in b])
        self.assertEqual((st["outcome"], st["direction"]), ("different", "better"))
        st = self._cv(b, [x * 1.08 for x in b])
        self.assertEqual((st["outcome"], st["direction"]), ("different", "worse"))
        st = self._cv(b, [x * 1.08 for x in b], better="higher")
        self.assertEqual(st["direction"], "better")

    def test_inconclusive_few_runs(self):
        st = self._cv([1.0, 2.0], [1.0, 2.0])
        self.assertEqual(st["outcome"], "inconclusive")
        self.assertIn("회차 부족", st["reasons"][0])

    def test_inconclusive_noisy(self):
        b = [92.0, 108.0, 100.0, 95.0, 105.0, 101.0]
        c = [97.0, 110.0, 99.0, 104.0, 94.0, 106.0]
        st = self._cv(b, c, paired=False, floor=3.0, eq=5.0)
        self.assertEqual(st["outcome"], "inconclusive")

    def test_five_pairs_cannot_be_different(self):
        b = [100.0, 100.4, 99.7, 100.1, 99.9]
        st = self._cv(b, [x * 0.8 for x in b])
        self.assertEqual(st["outcome"], "inconclusive")
        self.assertIn("6쌍 이상", st["reasons"][0])

    def test_effect_below_floor_is_not_different(self):
        b = [100.0, 100.01, 99.99, 100.0, 100.02, 99.98, 100.0, 100.01]
        c = [x + 0.5 for x in b]     # +0.5% 확실한 이동, 바닥 1%보다 작음
        st = self._cv(b, c, floor=1.0, eq=3.0)
        self.assertEqual(st["outcome"], "same")
        st = self._cv(b, c, floor=1.0, eq=0.2)
        self.assertEqual(st["outcome"], "inconclusive")

    def test_minor_shift_inside_equivalence_is_same(self):
        b = [100.0, 100.01, 99.99, 100.0, 100.02, 99.98, 100.0, 100.01]
        st = self._cv(b, [x * 1.005 for x in b], floor=0.1, eq=3.0)   # 확실한 +0.5%, 바닥보다 큼
        self.assertEqual(st["outcome"], "same")
        self.assertTrue(st["minor_shift"])

    def test_drift(self):
        b = [100.0] * 8
        c = [100.0 + r * 1.0 for r in range(1, 9)]  # 라운드마다 커지는 쌍 차이
        st = self._cv(b, c)
        self.assertTrue(st["drift"]["flag"])
        self.assertEqual(st["outcome"], "inconclusive")
        self.assertIn("드리프트", " ".join(st["reasons"]))

    def test_registered_stat_judge(self):
        fn = V.get_stat_judge(J.STAT_NAME)
        res = fn([10, 10.1, 9.9, 10, 10.2, 9.8], [12, 12.1, 11.9, 12, 12.2, 11.8], better="lower", noise="low",
                 noise_floor_pct=1.0)
        self.assertEqual(res.outcome, "different")


class CompareCliTest(unittest.TestCase):
    def setUp(self):
        self._cm = isolated_settings(PROFILE_COMPARE)
        self.td = self._cm.__enter__()
        self.results = self.td / "results"

    def tearDown(self):
        self._cm.__exit__(None, None, None)

    def _compare(self, *argv):
        out = self.td / "cmp.json"
        rc, text = cli(["compare", "--results", str(self.results), "--out", str(out), *argv])
        return rc, text, (_load(out) if out.exists() else None)

    def _same_session(self, name="s1", rounds=8, **kw):
        gb, gc = noisy(1, 400.0, 0.002), noisy(2, 400.0, 0.002)
        fb, fc = noisy(3, 30.0, 0.01), noisy(4, 30.0, 0.01)
        return abab_session(self.results, name, rounds, lambda r: values(gb(), fb(), 160.0 + r * 0.01),
                            lambda r: values(gc(), fc(), 160.0 + r * 0.01), **kw)

    def test_aa_same(self):
        self._same_session()
        rc, text, doc = self._compare("--runs", "session:s1", "--split", "arm")
        self.assertEqual(rc, 0, text)
        self.assertEqual(validate(doc, "comparison"), [])
        st = doc["items"]["MEM-04"]["metrics"]["total_used_mb"]["stat"]
        self.assertEqual(st["outcome"], "same")
        self.assertTrue(doc["summary"]["paired"])
        self.assertNotIn("different_worse", doc["summary"]["stat_counts"])
        self.assertNotIn("different_better", doc["summary"]["stat_counts"])

    def test_improved_and_device_confirm_tag(self):
        g = noisy(1, 400.0, 0.002)
        abab_session(self.results, "s2", 8, lambda r: values(g(), 30.0 + r * 0.01, 160.0),
                     lambda r: values(g() * 0.9, 30.0 + r * 0.01, 160.0))
        rc, text, doc = self._compare("--runs", "session:s2", "--split", "arm")
        self.assertEqual(rc, 0, text)
        it = doc["items"]["MEM-04"]
        self.assertEqual(it["metrics"]["total_used_mb"]["stat"]["direction"], "better")
        self.assertEqual(it["outcome"], "improved")
        self.assertTrue(it["needs_device_confirm"])
        self.assertIn("실기기 확인 대기", it["reasons"])
        self.assertNotIn("absolute", it)          # 에뮬레이터는 절대 기준 없음
        self.assertIn("실기기 확인 대기", text)

    def test_worse_warn_and_fail(self):
        g = noisy(1, 400.0, 0.002)
        abab_session(self.results, "s3", 8, lambda r: values(g(), 30.0, 160.0), lambda r: values(g() * 1.07, 30.0, 160.0 * 1.15))
        rc, text, doc = self._compare("--runs", "session:s3", "--split", "arm", "--fail-on", "worse")
        self.assertEqual(rc, 1, text)
        mem = doc["items"]["MEM-04"]
        self.assertEqual(mem["metrics"]["total_used_mb"]["stat"]["direction"], "worse")
        self.assertEqual(mem["outcome"], "warn")              # low: +7% ≥ 5%
        self.assertEqual(doc["items"]["AUX-01"]["outcome"], "fail")   # low: +15% ≥ 10%

    def test_excluded_metric_values_only(self):
        self._same_session("s4")
        rc, text, doc = self._compare("--runs", "session:s4", "--split", "arm")
        m = doc["items"]["FRM-03"]["metrics"]["slow_50_pct"]
        self.assertTrue(m["excluded"])
        self.assertEqual(m["outcome"], "record")
        self.assertEqual(len(m["cand"]), 8)

    def test_pending_aa_noise_is_pending(self):
        g = noisy(1, 400.0, 0.002)
        abab_session(self.results, "s5", 6, lambda r: {"MEM-03": {"pss_total_mb": g()}},
                     lambda r: {"MEM-03": {"pss_total_mb": g() * 0.8}})
        rc, text, doc = self._compare("--runs", "session:s5", "--split", "arm")
        m = doc["items"]["MEM-03"]["metrics"]["pss_total_mb"]
        self.assertEqual(m["outcome"], "pending")
        self.assertTrue(m["stat"]["info_only"])
        self.assertNotIn("needs_device_confirm", doc["items"]["MEM-03"])

    def test_invalid_runs_excluded_and_counted(self):
        bad = {"per_round": {1: {"valid": False, "invalid_reason": "host busy"}, 2: {"host": {
            "cpu": "cpu", "os": "os", "gpu": "gpu", "cpu_busy_before": 5.0, "cpu_busy_during": 40.0}}}}
        g = noisy(1, 400.0, 0.002)
        abab_session(self.results, "s6", 8, lambda r: values(g(), 30.0, 160.0), lambda r: values(g(), 30.0, 160.0),
                     base_meta=bad)
        rc, text, doc = self._compare("--runs", "session:s6", "--split", "arm")
        self.assertEqual(rc, 0, text)
        self.assertEqual(doc["summary"]["n_invalid_runs"], 2)
        self.assertEqual(doc["summary"]["n_base"], 6)
        reasons = {b["run_id"][-1]: b["reason"] for b in doc["base"]["invalid_runs"]}
        self.assertEqual(reasons["1"], "host busy")
        self.assertIn("관문", reasons["2"])
        self.assertEqual(doc["summary"]["pairs"], 6)

    def test_class_mix_refused(self):
        g = noisy(1, 400.0, 0.002)
        abab_session(self.results, "s7", 4, lambda r: values(g(), 30.0, 160.0), lambda r: values(g(), 30.0, 160.0),
                     cand_device_class="device")
        rc, text, doc = self._compare("--runs", "session:s7", "--split", "arm")
        self.assertEqual(rc, 2)
        self.assertIn("거부", text)
        self.assertIsNone(doc)

    def test_hash_mismatch_invalid(self):
        g = noisy(1, 400.0, 0.002)
        abab_session(self.results, "s8", 6, lambda r: values(g(), 30.0, 160.0), lambda r: values(g(), 30.0, 160.0),
                     cand_meta={"probe_hash": "probe-2"})
        rc, text, doc = self._compare("--runs", "session:s8", "--split", "arm")
        self.assertEqual(rc, 3)
        self.assertFalse(doc["valid"])
        self.assertIn("probe_hash", " ".join(doc["summary"]["invalid_reasons"]))

    def test_not_interleaved_warns_and_independent(self):
        from datetime import timedelta

        from tests.runsfake import T0

        g = noisy(1, 400.0, 0.002)
        abab_session(self.results, "early", 3, lambda r: values(g(), 30.0, 160.0), lambda r: values(g(), 30.0, 160.0))
        abab_session(self.results, "late", 3, lambda r: values(g(), 30.0, 160.0), lambda r: values(g(), 30.0, 160.0),
                     start=T0 + timedelta(days=1))
        rc, text, doc = self._compare("--base", "session:early", "--cand", "session:late")
        self.assertEqual(rc, 0, text)
        self.assertFalse(doc["summary"]["paired"])
        self.assertFalse(doc["summary"]["interleaved"])
        self.assertTrue(any("교차" in w for w in doc["summary"]["warnings"]))

    def test_commit_placeholder(self):
        rc, text = cli(["compare", "--base-commit", "abc1234", "--cand", "x"])
        self.assertEqual(rc, 2)
        self.assertIn("아직 없음", text)

    def test_baseline_register_and_noise(self):
        self._same_session("aa", rounds=6)
        g = noisy(9, 400.0, 0.002)
        from datetime import timedelta

        from tests.runsfake import T0

        abab_session(self.results, "abab", 6, lambda r: values(g(), 30.0, 160.0), lambda r: values(g() * 0.95, 30.0, 160.0),
                     start=T0 + timedelta(days=1))
        rc, text = cli(["baseline", "register", "--name", "bl1", "--results", str(self.results),
                        "--runs", "session:abab", "--noise-runs", "session:aa"])
        self.assertEqual(rc, 0, text)
        bl = _load(self.results / "baselines" / "bl1.json")
        e = bl["classes"]["emulator"]
        self.assertEqual(e["layer"], "L3-E")
        self.assertIn("MEM-04.total_used_mb", e["noise_floor"])
        self.assertEqual(e["probe_hash"], "probe-1")
        rc, text = cli(["baseline", "register", "--name", "bl1", "--results", str(self.results), "--runs", "session:abab"])
        self.assertEqual(rc, 2)                     # 같은 등급은 --force 없이 안 바뀜
        rc, text = cli(["baseline", "list"])
        self.assertIn("bl1", text)
        rc, text = cli(["baseline", "show", "bl1"])
        self.assertIn("σd", text)
        rc, text = cli(["noise", "aa", "--runs", "session:aa", "--results", str(self.results)])
        self.assertEqual(rc, 0, text)
        self.assertIn("MEM-04.total_used_mb", text)
        rc, text, doc = self._compare("--runs", "session:abab", "--split", "arm", "--noise", "bl1")
        st = doc["items"]["MEM-04"]["metrics"]["total_used_mb"]["stat"]
        self.assertEqual(st["floor_source"], "aa")

    def test_bundle_with_deterministic(self):
        from perfkit.diff import resultio
        from perfkit.manifest import load

        m = load()
        l1 = {"schema": "perfkit/result@1", "layer": "L1", "manifest_version": m.version, "manifest_sha": m.sha256,
              "collected_at": "2026-01-01T00:00:00+00:00", "source": {"kind": "build", "ref": "x.apk"},
              "items": {"BLD-01": {"status": "ok", "values": {"apk_mb": 100.0}}}}
        resultio.write(self.td / "a.json", l1)
        l1["items"]["BLD-01"]["values"]["apk_mb"] = 106.0
        resultio.write(self.td / "b.json", l1)
        self._same_session("s9", rounds=6)
        rc, text, doc = self._compare("--base", str(self.td / "a.json"), "--cand", str(self.td / "b.json"),
                                      "--runs", "session:s9")
        self.assertEqual(rc, 2)                     # --runs와 --base는 함께 못 씀
        from tests.runsfake import T0  # noqa: F401

        run_ids = sorted(p.name for p in (self.results / "runs").iterdir())
        a_ids = [r for r in run_ids if "0123abcd" in r]
        b_ids = [r for r in run_ids if "4567cdef" in r]
        rc, text, doc = self._compare("--base", str(self.td / "a.json"), "--base", "run:" + ",".join(a_ids),
                                      "--cand", str(self.td / "b.json"), "--cand", "run:" + ",".join(b_ids))
        self.assertEqual(rc, 0, text)
        self.assertEqual(doc["schema"], "perfkit/comparison-set@1")
        self.assertEqual(doc["layers"], ["L1", "L3-E"])
        self.assertEqual(doc["parts"][0]["items"]["BLD-01"]["outcome"], "fail")
        for p in doc["parts"]:
            self.assertEqual(validate(p, "comparison"), [])


if __name__ == "__main__":
    unittest.main()
