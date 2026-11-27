"""perfkit device: 파서·기기 설정·호스트 관문·AVD 대조·prep/restore·식힘·임대 도구(가짜 adb)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from perfkit.device import devcfg as DC
from perfkit.device import emulator as EM
from perfkit.device import host as H
from perfkit.device import parse as P
from perfkit.device import prep as PR
from perfkit.device.adb import Adb, LogcatTail, Result, parse_devices, parse_version
from perfkit.device.sampler import Sampler, build_command, group_threads, parse_sample, rows
from tests.devsetup import FakeProbe, Sandbox, fast_sleep
from tests.fakeadb import BATTERY, MEMINFO, THERMAL, FakeDevice


class ParseTest(unittest.TestCase):
    def test_am_start(self):
        d = P.am_start("Status: ok\nLaunchState: COLD\nActivity: x\nTotalTime: 812\nWaitTime: 815\nComplete\n")
        self.assertEqual(d["launch_state"], "COLD")
        self.assertEqual(d["total_ms"], 812)
        self.assertEqual(d["wait_ms"], 815)
        self.assertIsNone(d["error"])

    def test_proc_stat_name_with_spaces_and_parens(self):
        line = "123 (Job.Worker (1) x) S 1 2 3 4 5 6 7 8 9 10 300 45 0 0 20"
        self.assertEqual(P.proc_stat_line(line), (123, "Job.Worker (1) x", 345))
        self.assertIsNone(P.proc_stat_line("garbage"))

    def test_cpu_busy(self):
        a = P.cpu_line("cpu  100 0 50 800 50 0 0 0 0 0")
        b = P.cpu_line("cpu  200 0 100 1000 100 0 0 0 0 0")
        # 전체 +400, 유휴(idle+iowait) +250 → 37.5%
        self.assertEqual(P.cpu_busy_pct(a, b), 37.5)

    def test_meminfo(self):
        m = P.meminfo(MEMINFO)
        self.assertEqual(m["total_pss"], 500.0)
        self.assertEqual(m["native_heap"], 49.8)
        self.assertEqual(m["code"], 90.0)
        self.assertEqual(m["private_dirty"], 390.6)
        self.assertEqual(m["egl_mtrack_table"], 8.0)
        self.assertEqual(m["unknown_table"], 20.0)

    def test_thermal_battery(self):
        t = P.thermalservice(THERMAL.format(status=1, ap=40.5, skin=36.0, bat=33.1))
        self.assertEqual(t["status"], 1)
        self.assertEqual(t["temps"], {"AP": 40.5, "SKIN": 36.0, "BAT": 33.1})
        b = P.battery(BATTERY)
        self.assertEqual((b["level"], b["temp_c"], b["charging"]), (87, 31.2, True))

    def test_avd_summary_and_versions(self):
        cfg = P.ini("hw.cpu.ncore=4\nhw.ramSize=4096M\nhw.gpu.mode=host\nhw.lcd.width=1080\nhw.lcd.height=2400\n"
                    "image.sysdir.1=system-images\\android-35\\google_apis\\x86_64\\\n")
        s = P.avd_summary(cfg)
        self.assertEqual(s["system_image"], "system-images;android-35;google_apis;x86_64")
        self.assertEqual((s["cores"], s["ram_mb"], s["resolution"]), (4, 4096, "1080x2400"))
        self.assertEqual(P.emulator_version("Android emulator version 37.2.13.0 (build_id 16740729) (CL:N/A)"),
                         "37.2.13.0 (build_id 16740729)")
        self.assertEqual(parse_version("Android Debug Bridge version 1.0.41\nVersion 37.0.1-1\n"), "37.0.1-1")
        self.assertEqual(parse_devices("List of devices attached\nemulator-5554\tdevice\nX\toffline\n"),
                         [("emulator-5554", "device"), ("X", "offline")])


class ConfigTest(unittest.TestCase):
    def test_merge_preset_profile_user_env(self):
        with Sandbox() as sb:
            s = sb.settings()
            d = DC.device_config("phone", s)
            self.assertEqual(d["kind"], "device")
            self.assertEqual(d["preset"], "exynos-mali")
            self.assertTrue(d["sysfs"]["gpu_clock"].startswith("/sys/"))   # SoC preset
            self.assertEqual(d["cool"]["timeout_s"], 1.0)                   # 프로필이 이김
            self.assertEqual(d["prep"]["brightness"], 128)                  # android-device 기본
            s.env["PERFKIT_DEVICE_PHONE_SERIAL"] = "ENV-SERIAL"
            self.assertEqual(DC.device_config("phone", s)["serial"], "ENV-SERIAL")
            e = DC.device_config("emu", s)
            self.assertEqual(e["thread_groups"]["main"], "^(UnityMain|Unity Main)")
            self.assertEqual(DC.run_config(s)["sample_interval_s"], 0.15)
            self.assertEqual(DC.gate_config("L3-E", s)["host_busy_during_max"], 25.0)
            self.assertEqual(DC.package_name(DC.run_config(s), s), "com.example.game")
            self.assertEqual(DC.package_name(DC.run_config(s), s, "com.apk.pkg"), "com.apk.pkg")


class HostGateTest(unittest.TestCase):
    def test_window_busy_and_editor_pct(self):
        p = FakeProbe(busy=20.0, editor_pct=5.0)
        w = H.measure_idle(p, ["Unity.exe", "qemu-system-x86_64.exe"], 0.1)
        self.assertAlmostEqual(w.busy_pct, 20.0, delta=0.5)
        self.assertAlmostEqual(w.pct(["Unity.exe"]), 5.0, delta=0.5)
        self.assertAlmostEqual(w.pct(["qemu-system-x86_64.exe"]), 12.0, delta=0.5)

    def test_busy_batch_build_only(self):
        cfg = DC.HOST_DEFAULTS
        worker = '"Unity.exe" "-adb2" "-batchMode" "-noUpm" "-name" "AssetImportWorker1" "-projectPath" "X"'
        build = '"Unity.exe" -batchmode -quit -projectPath X -buildTarget Android -executeMethod B.Build'
        self.assertEqual(H.busy_reasons(FakeProbe(busy_cmd=worker), cfg), [])
        self.assertEqual(len(H.busy_reasons(FakeProbe(busy_cmd=build), cfg)), 1)


class OpenEditorsTest(unittest.TestCase):
    def test_counts_project_editors_only(self):
        class P(FakeProbe):
            def cmdlines(self, image):
                if image != "Unity.exe":
                    return []
                return [(1, '"C:/Hub/resources/unity.exe" serve'),
                        (2, '"Unity.exe" -projectPath "C:/proj" -logFile x'),
                        (3, '"Unity.exe" "-batchMode" "-name" "AssetImportWorker0" "-projectPath" "C:/proj"')]
        eds = H.open_editors(P(), DC.HOST_DEFAULTS)
        self.assertEqual([p for p, _ in eds], [2])
        self.assertEqual(H.open_editors(FakeProbe(), DC.HOST_DEFAULTS), [])

    def test_health_reasons(self):
        items = {"FRM-03": {"status": "ok", "values": {"fps_avg": 22.0}},
                 "MEM-04": {"status": "not_collected"}}
        rules = [{"metric": "FRM-03.fps_avg", "min": 55, "scenario": "light"},
                 {"metric": "MEM-04.total_used_mb", "max": 10}]
        self.assertEqual(len(H.health_reasons(rules, items, "light")), 1)
        self.assertIn("저하 상태", H.health_reasons(rules, items, "light")[0])
        self.assertEqual(H.health_reasons(rules, items, "heavy"), [])          # 시나리오가 다르면 안 봄
        self.assertEqual(H.health_reasons([{"metric": "FRM-03.fps_avg", "min": 20}], items, None), [])
        self.assertEqual(H.health_reasons(None, items, "light"), [])


class AvdTest(unittest.TestCase):
    def test_describe_and_mismatch(self):
        with Sandbox() as sb:
            dev = FakeDevice()
            adb = Adb("adb", "emulator-5554", runner=dev.runner, popen=dev.popen)
            emu = EM.describe(adb, "test-avd", sdk=None, home=sb.avd_home)
            self.assertEqual(emu["cores"], 4)
            self.assertTrue(emu["arm_translation"])
            self.assertEqual(emu["resolution"], "1080x2400")
            exp = DC.device_config("emu", sb.settings())["expect"]
            self.assertEqual(EM.mismatches(emu, exp), [])
            self.assertEqual(len(EM.mismatches(dict(emu, cores=2), exp)), 1)

    def test_emulab_wrapper(self):
        seen = []

        def runner(argv, timeout):
            seen.append(argv)
            if "start" in argv:
                return Result(0, "EMULATOR_READY serial=emulator-5554\n" + json.dumps(
                    {"action": "new", "serial": "emulator-5554", "launched": True, "avd": "a"}))
            if "status" in argv:
                return Result(0, json.dumps({"avd": "a", "owner": "me", "lease": {"owner": "other", "valid": True,
                                                                                 "mine": False, "expiresAt": "t"}}))
            return Result(0, json.dumps({"avd": "a"}))

        e = EM.EmuLab("tool/emu-lab.js", avd="a", owner="me", runner=runner)
        self.assertEqual(e.start()["serial"], "emulator-5554")
        self.assertEqual(seen[0][:3], ["node", "tool/emu-lab.js", "start"])
        self.assertIn("--json", seen[0])
        self.assertIn("임대 중", EM.busy_by_other_lease(e.status()))
        self.assertIsNone(EM.busy_by_other_lease({"owner": "me", "lease": {"owner": "me", "mine": True}}))


class PrepTest(unittest.TestCase):
    def test_prep_restore_roundtrip(self):
        with Sandbox() as sb:
            dev = FakeDevice("SERIAL-1", emulator=False)
            adb = Adb("adb", "SERIAL-1", runner=dev.runner, popen=dev.popen)
            cfg = DC.device_config("phone", sb.settings())
            before = dict(dev.settings)
            f = PR.state_path(sb.results, "phone")
            doc = PR.prep(adb, cfg, "com.example.game", f)
            self.assertEqual(dev.settings["global airplane_mode_on"], "1")
            self.assertEqual(dev.settings["system screen_brightness"], "128")
            self.assertEqual(dev.settings["system screen_brightness_mode"], "0")
            self.assertIn("stay_on", doc["applied"])
            PR.prep(adb, cfg, "com.example.game", f)   # 두 번 prep해도 처음 값을 지킨다
            res = PR.restore(adb, f)
            self.assertTrue(res["same"], res)
            self.assertEqual(dev.settings, before)
            self.assertFalse(f.exists())

    def test_wait_cool(self):
        with Sandbox() as sb:
            cfg = DC.device_config("phone", sb.settings())
            cool = FakeDevice("SERIAL-1", emulator=False, thermal=(0, 35.0, 33.0, 31.0))
            r = PR.wait_cool(Adb("adb", "S", runner=cool.runner), cfg, sleep=fast_sleep, log=lambda s: None)
            self.assertTrue(r.ok)
            hot = FakeDevice("SERIAL-1", emulator=False, thermal=(1, 41.0, 37.0, 33.0))
            r = PR.wait_cool(Adb("adb", "S", runner=hot.runner), cfg, sleep=fast_sleep, log=lambda s: None)
            self.assertFalse(r.ok)
            self.assertIn("시간 초과", r.reason)


class SamplerTest(unittest.TestCase):
    def test_emulator_sample_rows(self):
        dev = FakeDevice()
        dev.pid = 4242
        adb = Adb("adb", "emulator-5554", runner=dev.runner)
        cfg = {"kind": "emulator", "thread_groups": {"main": "^UnityMain", "gfx": "^UnityGfx", "workers": "^Job"}}
        sp = Sampler(adb, 4242, cfg, 0.05)
        import time as _t

        for _ in range(3):
            sp.sample_once()
            _t.sleep(0.03)
        rw = rows(sp.samples, 100, cfg["thread_groups"], sp.samples[0].t)
        self.assertEqual(len(rw), 3)
        self.assertIsNone(rw[0]["proc_cpu_pct"])
        self.assertGreater(rw[2]["proc_cpu_pct"], 0)
        self.assertGreater(rw[2]["main_pct"], 0)
        self.assertEqual(rw[2]["rss_mb"], 1171.9)
        self.assertEqual(rw[2]["threads"], 120)
        self.assertEqual(rw[2]["sys_busy_pct"], 66.67)

    def test_device_sample_sysfs_thermal(self):
        dev = FakeDevice("SERIAL-1", emulator=False, thermal=(0, 36.0, 33.0, 31.0))
        dev.pid = 4242
        cfg = {"kind": "device", "sysfs": {"cpu_freq": ["/sys/a", "/sys/b"], "cpu_freq_scale": 0.001,
                                           "gpu_clock": "/sys/gpu_clock", "gpu_busy": "/sys/gpu_busy",
                                           "gpu_max_clock": "/sys/gpu_max_clock", "gpu_clock_scale": 1.0},
               "thermal": {"sensors": {"ap": "AP", "skin": "SKIN"}}}
        cmd = build_command(4242, cfg, with_thermal=True)
        s = parse_sample(dev.shell(cmd), 1.0, cfg)
        self.assertEqual(s.gpu_clock_mhz, 585.0)
        self.assertEqual(s.gpu_busy_pct, 40.0)
        self.assertEqual(s.gpu_max_mhz, 858.0)
        self.assertEqual(s.cpu_freq_mhz, [1800.0, 1800.0])
        self.assertEqual(s.temps, {"ap": 36.0, "skin": 33.0})
        self.assertEqual(s.batt_level, 87)

    def test_group_threads(self):
        g = group_threads({"UnityMain": 50.0, "UnityGfxDeviceW": 70.0, "Job.Worker 0": 10.0, "Job.Worker 1": 5.0,
                           "Binder:1": 1.0}, {"main": "^UnityMain", "gfx": "^UnityGfx", "workers": "^Job"})
        self.assertEqual(g, {"main": 50.0, "gfx": 70.0, "workers": 15.0, "other": 1.0})


class LogcatTailTest(unittest.TestCase):
    def test_reconnect_no_duplicates(self):
        import tempfile

        dev = FakeDevice(drop_after=3)
        for i in range(6):
            dev.log.append((100.0 + i * 0.5, f"   {100.0 + i * 0.5:.3f}  1  1 I Tag     : line {i}"))
        with tempfile.TemporaryDirectory() as d:
            got = []
            adb = Adb("adb", "x", runner=dev.runner, popen=dev.popen)
            t = LogcatTail(adb, Path(d) / "l.txt", on_line=lambda s, h: got.append(s), sleep=fast_sleep).start()
            import time as _t

            for _ in range(200):
                if len(got) >= 6:
                    break
                _t.sleep(0.02)
            t.stop()
            text = (Path(d) / "l.txt").read_text()
            self.assertEqual(t.restarts, 1)
            self.assertEqual([g.split(": ")[1] for g in got], [f"line {i}" for i in range(6)])
            self.assertEqual(text.count("line 2"), 1)
            self.assertIn("##PERFKIT_LOGCAT_RESTART", text)


if __name__ == "__main__":
    unittest.main()
