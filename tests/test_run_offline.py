"""perfkit run: 표본 logcat·CSV 파서, 지표 계산, 가짜 기기로 회차·반복·ABAB·호환 모드·관문·거부."""

from __future__ import annotations

import json
import unittest

from perfkit.device import parse as P
from perfkit.device.adb import Adb
from perfkit.run import metrics as M
from perfkit.run import probe as PB
from perfkit.run.runner import Env, Refused, RunSpec, make_run_id, quality_hash, run_once, unity_args
from perfkit.run.series import abab_order, run_series
from perfkit.schema import validate
from tests.devsetup import PROFILE, FakeProbe, Sandbox, fast_sleep
from tests.fakeadb import CSV_FIXTURE, LOG_FIXTURE, MEMINFO, FakeDevice


class ProbeParseTest(unittest.TestCase):
    def setUp(self):
        self.log, self.lines = PB.parse_logcat_file(LOG_FIXTURE)
        self.frames = PB.parse_frames_file(CSV_FIXTURE)

    def test_markers(self):
        lg = self.log
        self.assertTrue(lg.has_probe)
        self.assertEqual(lg.run_id, "20260101-1200-0a1b2c3d-scene-heavy-1")
        self.assertEqual(lg.begin["scenario"], "scene-heavy")
        self.assertEqual([p["name"] for p in lg.phases],
                         ["load_boot", "load_title_ready", "load_scene_begin", "load_scene_end", "load_world_ready",
                          "load_game_ready", "settle", "near", "pan", "far", "end"])
        self.assertEqual(lg.measure_phases(), ["near", "pan", "far"])
        self.assertEqual(lg.end["result"], "ok")
        self.assertTrue(lg.end["csv"]["path"].endswith(lg.run_id + ".csv"))
        self.assertEqual(len(lg.events), 3)
        self.assertEqual(lg.header["frameTimingStats"], "True")
        self.assertGreaterEqual(len(lg.stats), 10)
        self.assertEqual(lg.app_pid, 21203)
        self.assertIsNotNone(quality_hash(lg.settle()))

    def test_app_log_summary(self):
        s = PB.app_log_summary(self.lines, {21203}, "com.example.game")
        self.assertEqual(s["exceptions"], 1)          # 앱 pid의 ClassNotFoundException 한 줄(시스템 줄은 뺌)
        self.assertEqual(s["class_warnings"], 1)      # [ExampleSystem] 경고
        self.assertEqual(list(s["signatures"])[0].split(":")[0], "java.lang.ClassNotFoundException")
        self.assertEqual(s["anr"], 0)

    def test_frames_and_metrics(self):
        self.assertEqual(len(self.frames), 65)
        self.assertEqual(self.frames[0]["phase"], "load_boot")
        items = M.frame_items(self.log, self.frames, "emulator")
        self.assertEqual(items["FRM-01"]["status"], "ok")
        self.assertEqual(items["FRM-01"]["samples"], 50)              # near 20 + pan 10 + far 20
        self.assertIn("p95", items["FRM-01"]["values"])
        self.assertGreater(items["FRM-03"]["values"]["fps_avg"], 0)
        self.assertIn("setpass_near", items["GPU-04"]["values"])
        self.assertNotIn("batches_near", items["GPU-04"]["values"])  # dcRecorder=False
        self.assertEqual(items["GPU-03"]["status"], "not_collected")
        self.assertNotIn("GPU-01", items)                             # 에뮬레이터에서는 GPU ms를 안 냄
        self.assertEqual(items["CPU-02"]["source"], "monoAllocB")
        st = M.stats_items(self.log, "emulator", [])
        self.assertEqual(st["MEM-04"]["status"], "ok")
        self.assertIn("gc_reserved_mb", st["CPU-04"]["values"])
        ld = M.load_items(self.log, P.am_start("LaunchState: COLD\nTotalTime: 812\n"))
        self.assertEqual(ld["BLD-10"]["values"]["cold_s"], 0.812)
        self.assertAlmostEqual(ld["AUX-04"]["values"]["load_total_s"], 12.0, places=3)
        self.assertEqual(len(ld["AUX-04"]["values"]["phases"]), 5)
        self.assertEqual(ld["BLD-11"]["values"]["ttfd_s"], 14.511)
        ev = M.event_items(self.log)
        self.assertGreater(ev["CPU-15"]["values"]["ui_open_max_frame_ms"], 100)
        self.assertEqual(M.meminfo_items(P.meminfo(MEMINFO))["MEM-03"]["values"]["pss_total_mb"], 500.0)

    def test_unity_args_and_run_id(self):
        spec = RunSpec(device="emu", apk=None, scenario="scene-a", seconds=30, save="light")
        a = unity_args(spec, "20260101-1200-0a1b2c3d-scene-a-1", {"quit_after_end": False, "extra_args": ""})
        self.assertIn("-perfRunId 20260101-1200-0a1b2c3d-scene-a-1", a)
        self.assertIn("-perfQuit 0", a)
        with Sandbox() as sb:
            (sb.results / "runs" / "20260101-1200-0a1b2c3d-scene-a-1").mkdir(parents=True)
            from datetime import datetime

            rid = make_run_id(sb.results, "0a1b2c3d", "scene-a", datetime(2026, 1, 1, 12, 0))
            self.assertEqual(rid, "20260101-1200-0a1b2c3d-scene-a-2")


def _env(sb, dev, probe=None, log=None):
    adb = Adb("adb", dev.serial, runner=dev.runner, popen=dev.popen, sleep=fast_sleep)
    return Env(settings=sb.settings(), adb=adb, probe=probe or FakeProbe(busy=10.0), results=sb.results,
               log=log or (lambda s: None), sleep=fast_sleep, avd_home=sb.avd_home, sdk="",
               lock_dir=sb.root / "locks")


class RunOnceTest(unittest.TestCase):
    def spec(self, sb, **kw):
        base = dict(device="emu", apk=sb.apk, scenario="scene-heavy", seconds=5, save="light", session="t1")
        base.update(kw)
        return RunSpec(**base)

    def test_emulator_run_folder(self):
        with Sandbox() as sb:
            dev = FakeDevice()
            out = run_once(self.spec(sb), _env(sb, dev))
            self.assertEqual(out.schema_errors, [])
            self.assertEqual(out.end_signal, "end")
            self.assertTrue(out.valid, out.reasons)
            d = out.run_dir
            for f in ("meta.json", "result.json", "logcat.txt", "frames.csv", "device_samples.csv", "meminfo.txt",
                      "am_start.txt", "run.log"):
                self.assertTrue((d / f).exists(), f)
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
            res = json.loads((d / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(validate(meta, "run-meta"), [])
            self.assertEqual(validate(res, "result"), [])
            self.assertEqual(meta["device_class"], "emulator")
            self.assertEqual(meta["emu"]["avd"], "test-avd")
            self.assertTrue(meta["emu"]["arm_translation"])
            self.assertEqual(meta["host"]["cpu"], "Test CPU")
            self.assertEqual(meta["probe_hash"], "probe-1-0000abcd")
            self.assertEqual(meta["logcat_restarts"], 0)
            self.assertIn("quality_hash", meta)
            self.assertEqual(res["layer"], "L3-E")
            for iid in ("FRM-01", "FRM-02", "FRM-03", "AUX-01", "AUX-02", "AUX-03", "MEM-03", "MEM-04", "BLD-10",
                        "AUX-04", "GPU-04", "GPU-07", "STB-08"):
                self.assertEqual(res["items"][iid]["status"], "ok", iid)
            self.assertNotIn("THM-01", res["items"])               # 에뮬레이터 등급에 열 항목 없음
            self.assertNotIn("GPU-01", res["items"])
            # 샘플 행은 측정 단계 이름을 가진다
            text = (d / "device_samples.csv").read_text(encoding="utf-8")
            self.assertIn(",near,", text)
            # 기기 정리: 회차 CSV와 넣은 세이브를 지움, logcat 버퍼 되돌림
            self.assertFalse(any(k.endswith(".csv") for k in dev.files))
            self.assertFalse(any("/perf/saves/" in k for k in dev.files))
            self.assertEqual(dev.logcat_size, "256K")
            # P5 compare가 읽는다
            from perfkit.compare.runsets import load_run

            r = load_run(d)
            self.assertEqual(r.layer, "L3-E")

    def test_logcat_drop_reconnects_and_invalidates(self):
        with Sandbox() as sb:
            dev = FakeDevice(drop_after=12)
            out = run_once(self.spec(sb), _env(sb, dev))
            self.assertEqual(out.end_signal, "end")
            self.assertEqual(out.meta["logcat_restarts"], 1)
            self.assertFalse(out.valid)
            self.assertIn("logcat 재연결", out.meta["invalid_reason"])
            # 다시 붙어도 줄이 빠지거나 겹치지 않는다
            log = (out.run_dir / "logcat.txt").read_text(encoding="utf-8")
            self.assertEqual(log.count("[PerfRun] BEGIN"), 1)
            self.assertEqual(log.count("PHASE name=near"), 1)

    def test_low_threshold_marks_invalid(self):
        with Sandbox() as sb:
            out = run_once(self.spec(sb, gate={"host_busy_during_max": 1.0}), _env(sb, FakeDevice(), FakeProbe(busy=10)))
            self.assertFalse(out.valid)
            self.assertIn("수집 중 호스트 CPU", out.meta["invalid_reason"])
            self.assertEqual(out.schema_errors, [])

    def test_editor_cpu_marks_invalid(self):
        with Sandbox() as sb:
            out = run_once(self.spec(sb), _env(sb, FakeDevice(), FakeProbe(busy=10, editor_pct=4.0)))
            self.assertFalse(out.valid)
            self.assertIn("다른 에디터", out.meta["invalid_reason"])
            self.assertGreater(out.meta["host"]["other_unity_pct"], 1.0)

    def test_compat_mode(self):
        with Sandbox() as sb:
            out = run_once(self.spec(sb, seconds=1), _env(sb, FakeDevice(probe=False)))
            self.assertTrue(out.compat)
            self.assertEqual(out.end_signal, "compat")
            self.assertEqual(out.schema_errors, [])
            self.assertTrue(out.meta["compat_mode"])
            self.assertEqual(out.meta["probe_hash"], "none")
            items = out.result["items"]
            self.assertEqual(items["FRM-01"]["status"], "not_collected")
            self.assertIn("호환 모드", items["FRM-01"]["reason"])
            self.assertEqual(items["AUX-01"]["status"], "ok")
            self.assertEqual(items["MEM-03"]["status"], "ok")
            self.assertEqual(items["BLD-10"]["status"], "ok")

    def test_csv_end_signal_when_end_line_lost(self):
        with Sandbox() as sb:
            dev = FakeDevice(drop_after=39)  # END 줄 직전에 끊고, 다시 붙기 전에 CSV가 생김
            env = _env(sb, dev)
            out = run_once(self.spec(sb), env)
            self.assertIn(out.end_signal, ("end", "csv"))
            self.assertTrue((out.run_dir / "frames.csv").exists())

    def test_scenario_fail_and_app_death(self):
        with Sandbox() as sb:
            out = run_once(self.spec(sb), _env(sb, FakeDevice(result="fail")))
            self.assertFalse(out.valid)
            self.assertIn("시나리오 실패", out.meta["invalid_reason"])
        with Sandbox() as sb:
            out = run_once(self.spec(sb), _env(sb, FakeDevice(app_dies=True)))
            self.assertEqual(out.end_signal, "app_exit")
            self.assertEqual(out.exit_code, 2)
            self.assertFalse(out.valid)
            self.assertEqual(out.schema_errors, [])

    def test_refusals(self):
        with Sandbox() as sb:
            build = '"Unity.exe" -batchmode -projectPath X -buildTarget Android -executeMethod B.Build'
            with self.assertRaises(Refused) as cm:
                run_once(self.spec(sb), _env(sb, FakeDevice(), FakeProbe(busy_cmd=build)))
            self.assertEqual(cm.exception.code, 4)
            self.assertFalse((sb.results / "runs").exists())
        bad_avd = PROFILE.replace("cores = 4", "cores = 2")
        with Sandbox(profile=bad_avd) as sb:
            with self.assertRaises(Refused) as cm:
                run_once(self.spec(sb), _env(sb, FakeDevice()))
            self.assertEqual(cm.exception.code, 5)
            self.assertIn("cores", str(cm.exception))
            out = run_once(self.spec(sb, avd_mismatch="warn"), _env(sb, FakeDevice()))
            self.assertFalse(out.valid)
            self.assertIn("AVD 설정 다름", out.meta["invalid_reason"])

    def test_device_run_l3d(self):
        with Sandbox() as sb:
            dev = FakeDevice("SERIAL-1", emulator=False, thermal=(0, 36.0, 33.0, 31.0))
            out = run_once(self.spec(sb, device="phone"), _env(sb, dev))
            self.assertEqual(out.schema_errors, [])
            m, res = out.meta, out.result
            self.assertEqual(m["device_class"], "device")
            self.assertNotIn("emu", m)
            self.assertEqual(m["thermal_start"], 0)
            self.assertEqual(m["ap_c_start"], 36.0)
            self.assertEqual(m["batt_pct"], 87)
            self.assertEqual(res["layer"], "L3-D")
            self.assertEqual(res["items"]["GPU-02"]["values"]["cap_mhz"], 858.0)
            self.assertEqual(res["items"]["THM-01"]["status"], "ok")
            self.assertTrue((out.run_dir / "timestats.txt").exists())


class SeriesTest(unittest.TestCase):
    def test_device_lock_refuses_second_run(self):
        from perfkit.device.lock import DeviceLock

        with Sandbox() as sb:
            env = _env(sb, FakeDevice())
            held = DeviceLock("emulator-5554", "other", sb.root / "locks").acquire()
            base = RunSpec(device="emu", apk=sb.apk, scenario="scene-heavy", seconds=5, save="light", session="lk")
            with self.assertRaises(Refused) as cm:
                run_series(base, env, lease=False)
            self.assertEqual(cm.exception.code, 4)
            self.assertFalse((sb.results / "runs").exists())
            held.release()
            stale = sb.root / "locks" / "device-emulator-5554.lock"
            stale.write_text('{"pid": 999999999}', encoding="utf-8")   # 주인 없는 잠금은 넘겨받는다
            doc = run_series(base, env, lease=False)
            self.assertEqual(doc["summary"]["runs"], 1)
            self.assertFalse(stale.exists())

    def test_cold_start(self):
        from perfkit.run.series import cold_start

        with Sandbox() as sb:
            dev = FakeDevice()
            env = _env(sb, dev)
            base = RunSpec(device="emu", apk=sb.apk, scenario="scene-a")
            out = cold_start(env, base, 3)
            self.assertEqual(out["total_ms"], {"min": 812, "median": 812, "max": 812, "n": 3})
            self.assertEqual(sum(1 for c in dev.calls if c[:2] == ["shell", "am start -S -W -n com.example.game/"
                                                                   "com.unity3d.player.UnityPlayerGameActivity"]), 3)
            self.assertTrue((sb.results / "startup").exists())

    def test_abab_order_fixed_seed(self):
        a = abab_order(4, 1)
        self.assertEqual(a, abab_order(4, 1))
        self.assertTrue(all(sorted(x) == ["A", "B"] for x in a))
        self.assertNotEqual(abab_order(8, 1), abab_order(8, 2))

    def test_abab_two_rounds(self):
        with Sandbox() as sb:
            apk_b = sb.root / "b.apk"
            apk_b.write_bytes(b"PK\x05\x06" + b"\x00" * 18 + b"b")
            dev = FakeDevice()
            base = RunSpec(device="emu", apk=sb.apk, scenario="scene-heavy", seconds=5, save="light", session="ab1")
            doc = run_series(base, _env(sb, dev), rounds=2, apk_b=apk_b, order_seed=3, lease=False)
            outs = doc.pop("_outcomes")
            self.assertEqual(len(outs), 4)
            self.assertEqual([r["order"] for r in doc["rounds"]], abab_order(2, 3))
            arms = [(o.meta["round"], o.meta["arm"]) for o in outs]
            self.assertEqual(sorted(arms), [(1, "A"), (1, "B"), (2, "A"), (2, "B")])
            self.assertTrue(all(o.meta["session"] == "ab1" for o in outs))
            shas = {o.meta["arm"]: o.meta["apk_sha"] for o in outs}
            self.assertNotEqual(shas["A"], shas["B"])
            installs = [c for c in dev.calls if c[:1] == ["install"]]
            self.assertEqual(len(installs), 4)                   # 회차마다 다시 설치
            sess = json.loads((sb.results / "sessions" / "ab1.json").read_text(encoding="utf-8"))
            self.assertEqual(sess["summary"]["runs"], 4)
            from perfkit.compare.runsets import resolve, split

            side = resolve("session:ab1", results_dir=sb.results)
            a, b = split(side.runs, "arm")
            self.assertEqual((len(a), len(b)), (2, 2))

    def test_rounds_aa_and_lease(self):
        events = []

        class FakeLab:
            def status(self):
                return {"avd": "test-avd", "owner": "me", "lease": None}

            def start(self, hours=None, headless=False):
                events.append("start")
                return {"action": "new", "serial": "emulator-5554", "launched": True, "avd": "test-avd"}

            def renew(self, hours=None):
                events.append("renew")

            def release(self, keep_running=False):
                events.append("release")
                return {"avd": "test-avd"}

        prof = PROFILE.replace('lease = "none"', 'lease = "emu-lab"')
        with Sandbox(profile=prof) as sb:
            env = _env(sb, FakeDevice())
            env.emulab = FakeLab()
            base = RunSpec(device="emu", apk=sb.apk, scenario="scene-heavy", seconds=5, save="light", kind="aa",
                           session="aa1")
            doc = run_series(base, env, rounds=3)
            outs = doc.pop("_outcomes")
            self.assertEqual([o.meta["round"] for o in outs], [1, 2, 3])
            self.assertTrue(all(o.meta["run_kind"] == "aa" and o.meta["arm"] == "A" for o in outs))
            self.assertEqual(events, ["start", "renew", "renew", "renew", "release"])
            self.assertTrue(doc["lease"]["released"])


if __name__ == "__main__":
    unittest.main()
