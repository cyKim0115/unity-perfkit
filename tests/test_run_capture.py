import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from perfkit.run.runner import RunSpec, profiler_args, pull_profiler_capture, summarize_hint, unity_args


class FakeAdb:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def run(self, *args, timeout=None):
        self.calls.append(("run",) + args)
        if self.ok and args[0] == "pull":
            Path(args[2]).write_bytes(b"\0" * 10)
        return SimpleNamespace(ok=self.ok, stdout="", stderr="" if self.ok else "no such file")

    def shell(self, cmd, timeout=None):
        self.calls.append(("shell", cmd))
        return ""


class RunCaptureTest(unittest.TestCase):
    def test_unity_args_adds_profiler_only_when_asked(self):
        spec = RunSpec(device="emu", apk=None, scenario="scene", seconds=60)
        plain = unity_args(spec, "rid", {})
        self.assertNotIn("-profiler-enable", plain)
        spec.capture = "profiler"
        spec.capture_frames = 900
        args = unity_args(spec, "rid", {}, "/sdcard/x/perf/rid.raw")
        self.assertIn("-profiler-enable -profiler-log-file /sdcard/x/perf/rid.raw -profiler-capture-frame-count 900", args)
        self.assertIn("-profiler-maxusedmemory 268435456", args)
        self.assertTrue(args.startswith("-perfScenario scene"))
        self.assertIn("-profiler-capture-frame-count 1800", profiler_args("r.raw", 1800))

    def test_pull_and_hint(self):
        with tempfile.TemporaryDirectory() as td:
            adb = FakeAdb()
            cap = pull_profiler_capture(adb, "/sdcard/p/r.raw", Path(td), 1800)
            self.assertEqual(cap["bytes"], 10)
            self.assertIn(("shell", "rm -f /sdcard/p/r.raw"), adb.calls)
            cap["phase_frames"] = {"settle": 400, "near": 700, "far": 1500}
            hint = summarize_hint(cap)
            self.assertEqual(hint["skip_frames"], 700)
            self.assertTrue(hint["out"].endswith("profiler.profile.json"))
            bad = pull_profiler_capture(FakeAdb(ok=False), "/sdcard/p/r.raw", Path(td) / "none", 10)
            self.assertIn("pull 실패", bad["error"])
            self.assertIsNone(summarize_hint(bad))


if __name__ == "__main__":
    unittest.main()
