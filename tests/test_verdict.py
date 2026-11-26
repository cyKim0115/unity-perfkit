import copy
import json
import unittest

from perfkit import verdict as v
from perfkit.manifest import load
from perfkit.schema import validate

M = load()


def item(iid):
    return copy.deepcopy(M.get(iid))


class PlanExamplesTest(unittest.TestCase):
    """기본 판정 규칙의 대표 예시."""

    def test_size_plus3_warn_plus6_fail(self):
        # 결정적(none) 크기: +2% 경고, +5% 실패
        self.assertEqual(v.judge_change(100, 103, noise="none", kind="amount").outcome, "warn")
        self.assertEqual(v.judge_change(100, 106, noise="none", kind="amount").outcome, "fail")
        self.assertEqual(v.judge_change(100, 101, noise="none", kind="amount").outcome, "pass")
        # 매니페스트 항목으로도 같음(BLD-01 APK 크기, L1)
        it = item("BLD-01")
        self.assertEqual(v.judge_item(it, "L1", {"apk_mb": 100.0}, {"apk_mb": 103.0}).outcome, "warn")
        self.assertEqual(v.judge_item(it, "L1", {"apk_mb": 100.0}, {"apk_mb": 106.0}).outcome, "fail")

    def test_idle_gc_zero_to_one_fails(self):
        it = item("CPU-02")
        for layer in ("L2", "L3-D", "L3-E"):
            if layer == "L3-E":
                continue  # L3-E는 잡음 등급 대기라 아래에서 따로
            iv = v.judge_item(it, layer, {"idle_b_per_frame": 0}, {"idle_b_per_frame": 1})
            self.assertEqual(iv.outcome, "fail", layer)
        self.assertEqual(v.judge_change(0, 1, noise="low", zero_is_hard=True).outcome, "fail")

    def test_high_noise_alone_is_review(self):
        mv = v.judge_change([14.0, 14.5, 15.0], [18.0, 18.2, 18.4], noise="high", kind="time")
        self.assertEqual(mv.outcome, "review")
        mv = v.judge_change([14.0, 14.5, 15.0], [14.2, 14.6, 14.9], noise="high", kind="time")
        self.assertEqual(mv.outcome, "pass")
        # 매니페스트 GPU-01(L3-D, 잡음 높음)도 review
        iv = v.judge_item(item("GPU-01"), "L3-D", {"p95": [14.0, 14.5, 15.0]}, {"p95": [18.0, 18.2, 18.4]})
        self.assertEqual(iv.outcome, "review")


class NoiseRulesTest(unittest.TestCase):
    def test_none_count(self):
        self.assertEqual(v.judge_change(3, 4, noise="none", kind="count").outcome, "warn")
        self.assertEqual(v.judge_change(3, 4, noise="none", kind="count", increase_is_fail=True).outcome, "fail")
        self.assertEqual(v.judge_change(3, 2, noise="none", kind="count").outcome, "improved")
        self.assertEqual(v.judge_change(3, 3, noise="none", kind="count").outcome, "pass")

    def test_new_major_issue_fails(self):
        iv = v.judge_item(item("SCR-01"), "L0", {"major": 2, "issues_total": 40}, {"major": 3, "issues_total": 40})
        self.assertEqual(iv.outcome, "fail")
        iv = v.judge_item(item("SCR-01"), "L0", {"major": 2, "issues_total": 40}, {"major": 2, "issues_total": 41})
        self.assertEqual(iv.outcome, "warn")

    def test_low(self):
        self.assertEqual(v.judge_change(100, 104, noise="low").outcome, "pass")
        self.assertEqual(v.judge_change(100, 106, noise="low").outcome, "warn")
        self.assertEqual(v.judge_change(100, 111, noise="low").outcome, "fail")
        self.assertEqual(v.judge_change(100, 94, noise="low").outcome, "improved")
        self.assertEqual(v.judge_change(0, 1, noise="low", kind="count").outcome, "warn")  # 새 예외 1건

    def test_medium_needs_range(self):
        base = [10.0, 10.4, 10.2]
        self.assertEqual(v.judge_change(base, [10.9], noise="medium").outcome, "warn")   # 범위 밖 +6.9%
        self.assertEqual(v.judge_change(base, [11.5], noise="medium").outcome, "fail")   # +12.7%
        self.assertEqual(v.judge_change(base, [10.3], noise="medium").outcome, "pass")   # 범위 안
        self.assertEqual(v.judge_change(base, [9.5], noise="medium").outcome, "improved")
        self.assertEqual(v.judge_change([10.0], [12.0], noise="medium").outcome, "pending")  # 기준 1회

    def test_higher_is_better_reverses(self):
        # FPS(높을수록 좋음): 떨어지면 나빠짐
        self.assertEqual(v.judge_change(100, 94, noise="low", better="higher").outcome, "warn")
        self.assertEqual(v.judge_change(100, 106, noise="low", better="higher").outcome, "improved")

    def test_snapshot_change_is_review(self):
        self.assertEqual(v.judge_change({"a": 1}, {"a": 1}, noise="none", kind="snapshot").outcome, "pass")
        self.assertEqual(v.judge_change({"a": 1}, {"a": 2}, noise="none", kind="snapshot").outcome, "review")

    def test_pending_aa(self):
        self.assertEqual(v.judge_change(1, 2, noise="pending_aa").outcome, "pending")

    def test_record(self):
        self.assertEqual(v.judge_item(item("FRM-04"), "L3-D", {"value": 1}, {"value": 2}).outcome, "record")


class AbsoluteTest(unittest.TestCase):
    def test_frame_time_absolute(self):
        it = item("FRM-01")
        self.assertEqual(v.judge_item(it, "L3-D", None, {"p95": 10.5}).outcome, "pass")
        self.assertEqual(v.judge_item(it, "L3-D", None, {"p95": 12.0}).outcome, "warn")
        self.assertEqual(v.judge_item(it, "L3-D", None, {"p95": 15.0}).outcome, "fail")

    def test_high_noise_absolute_needs_all_runs(self):
        rule = {"metric": "fps_avg", "op": "lt", "warn": 59, "fail": 58, "basis": "제안", "sources": []}
        self.assertEqual(v.judge_absolute([57, 57.5, 57.9], rule, layer="L3-D", noise="high")[0], "fail")
        self.assertEqual(v.judge_absolute([57, 59.5, 57.9], rule, layer="L3-D", noise="high")[0], "review")

    def test_memory_limit_by_ram_class(self):
        it = item("MEM-01")
        self.assertEqual(v.judge_item(it, "L3-D", None, {"anon_swap_peak_mb": 3600}, ram_class="8GB").outcome, "fail")
        self.assertEqual(v.judge_item(it, "L3-D", None, {"anon_swap_peak_mb": 1200}, ram_class="8GB").outcome, "pass")
        self.assertEqual(v.judge_item(it, "L3-D", None, {"anon_swap_peak_mb": 1200}).outcome, "pending")

    def test_store_gates(self):
        self.assertEqual(v.judge_item(item("BLD-06"), "L0", None, {"target_api": 35}).outcome, "fail")
        self.assertEqual(v.judge_item(item("BLD-05"), "L1", None, {"so_unaligned": 1, "zip_aligned": True}).outcome, "fail")
        self.assertEqual(v.judge_item(item("BLD-05"), "L1", None, {"so_unaligned": 0, "zip_aligned": True}).outcome, "pass")

    def test_absolute_plus_change_takes_worst(self):
        # 변화는 개선인데 절대 기준 실패면 실패
        it = item("BLD-01")
        iv = v.judge_item(it, "L1", {"aab_base_module_mb": 560}, {"aab_base_module_mb": 520})
        self.assertEqual(iv.metrics["aab_base_module_mb"].change, "improved")
        self.assertEqual(iv.outcome, "fail")


class EmulatorClassTest(unittest.TestCase):
    def test_absolute_on_emulator_raises(self):
        rule = item("FRM-01")["criteria"]["absolute"][0]
        with self.assertRaises(v.EmulatorAbsoluteError):
            v.judge_absolute(20.0, rule, layer="L3-E")

    def test_manifest_item_forced_absolute_on_emulator_raises(self):
        it = item("FRM-01")
        it["by_layer"]["L3-E"]["judge"] = "absolute+change"
        with self.assertRaises(v.EmulatorAbsoluteError):
            v.judge_item(it, "L3-E", {"p95": [30, 31, 32]}, {"p95": [40, 40, 40]})

    def test_emulator_ignores_absolute(self):
        # 에뮬레이터 p95 40ms(절대 기준 14ms 초과)라도 절대 판정은 하지 않음 → 잡음 등급 전에는 판단 보류
        it = item("FRM-01")
        iv = v.judge_item(it, "L3-E", {"p95": [40, 41, 42]}, {"p95": [40, 41, 42]})
        self.assertIsNone(iv.absolute)
        self.assertEqual(iv.outcome, "pending")

    def test_emulator_improved_needs_device_confirm(self):
        it = item("MEM-04")
        it["by_layer"]["L3-E"]["noise"] = "low"  # 프로필이 잡음 「낮음」을 채웠다고 가정
        iv = v.judge_item(it, "L3-E", {"total_reserved_mb": 800}, {"total_reserved_mb": 700})
        self.assertEqual(iv.outcome, "improved")
        self.assertTrue(iv.needs_device_confirm)
        self.assertIn("실기기 확인 대기", iv.reasons)

    def test_l2_absolute_raises(self):
        rule = item("GPU-03")["criteria"]["absolute"][0]
        with self.assertRaises(v.AbsoluteNotAllowedError):
            v.judge_absolute(1200, rule, layer="L2")

    def test_class_mix_rejected(self):
        with self.assertRaises(v.ClassMixError):
            v.ensure_same_class([{"device_class": "device"}, {"device_class": "emulator"}])
        self.assertEqual(v.ensure_same_class([{"device_class": "emulator"}] * 3), "emulator")
        self.assertEqual(v.layer_for_class("emulator"), "L3-E")
        self.assertEqual(v.layer_for_class("device"), "L3-D")

    def test_invalid_runs_dropped(self):
        runs = [{"valid": True}, {"valid": False, "invalid_reason": "호스트 바쁨"}, {}]
        self.assertEqual(len(v.usable_runs(runs)), 2)


class StatInterfaceTest(unittest.TestCase):
    """ABAB 판정기가 붙는 자리."""

    def test_stat_plugin(self):
        def fake(base_runs, cand_runs, *, better, noise, noise_floor_pct):
            return v.StatResult("different", delta_pct=12.0, interval=(8.0, 16.0))

        v.register_stat_judge("zz_fake", fake)
        try:
            mv = v.judge_change([10, 10, 10, 10], [11.2, 11.2, 11.2, 11.2], noise="medium", stat="zz_fake")
            self.assertEqual(mv.outcome, "fail")
            self.assertEqual(mv.interval, [8.0, 16.0])
            same = v.register_stat_judge("zz_same", lambda *a, **k: v.StatResult("same"))
            self.assertEqual(v.judge_change([1, 2], [5, 6], noise="high", stat=same).outcome, "pass")
            inc = lambda *a, **k: v.StatResult("inconclusive")
            self.assertEqual(v.judge_change([1, 2], [5, 6], noise="medium", stat=inc).outcome, "pending")
        finally:
            v._STAT_JUDGES.pop("zz_fake", None)
            v._STAT_JUDGES.pop("zz_same", None)

    def test_unknown_stat(self):
        with self.assertRaises(KeyError):
            v.get_stat_judge("nope")


class OutputShapeTest(unittest.TestCase):
    def test_item_verdict_fits_comparison_schema(self):
        it = item("BLD-01")
        iv = v.judge_item(it, "L1", {"apk_mb": 100.0}, {"apk_mb": 106.0})
        comp = {
            "schema": "perfkit/comparison@1", "layer": "L1", "manifest_version": M.version,
            "manifest_sha": M.sha256, "created_at": "2026-11-26T22:00:00+09:00", "method": "diff",
            "base": {"label": "base", "run_ids": []}, "cand": {"label": "cand", "run_ids": []},
            "items": {"BLD-01": iv.to_dict()}, "summary": v.summarize([iv]),
        }
        json.dumps(comp)  # inf 없이 직렬화
        self.assertEqual(validate(comp), [])

    def test_worst_order(self):
        self.assertEqual(v.worst(["pass", "improved"]), "improved")
        self.assertEqual(v.worst(["improved", "warn"]), "warn")
        self.assertEqual(v.worst(["pending", "improved"]), "pending")
        self.assertEqual(v.worst(["fail", "review"]), "fail")


if __name__ == "__main__":
    unittest.main()
