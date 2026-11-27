"""옛 [PerfStats] logcat + device_samples.csv 가져오기(합성 자료)."""

import json
import unittest
from pathlib import Path

from tests.fakeproj import isolated_settings
from tests.runsfake import PROFILE_COMPARE
from tests.test_commands import run as cli

PROFILE = PROFILE_COMPARE + """
[devices.phone-a]
kind = "device"
ram_class = "8GB"
"""


def make_legacy(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    rows = ["t,wall,thermal_status,ap_c,skin_c,bat_c,battery_level,pss_mb"]
    for i in range(4):
        rows.append(f"{i * 5.0},10:00:{i * 5:02d},0,40.0,34.0,31.0,90,{900 + i}")
    (d / "device_samples.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    lines = ["01-02 09:59:58.000 I/Unity (1): [PerfStats] t=1 scene=Title fps=1 main=1 render=1 hist=0,0,0,0,0,1"]
    for i in range(16):
        hist = "60,0,0,0,0,0" if i else "58,1,0,1,0,0"
        lines.append(f"01-02 10:00:{i:02d}.500 I/Unity (1): [PerfStats] t={i} scene=Main target=60 fps={60 - (i == 0) * 2} "
                     f"main={5 + i * 0.1:.2f} render=2.00 gcKB=0.20 totalMB=500.0 sysMB=900.0 gcResMB=8.0 hist={hist}")
    lines.append("01-02 10:00:30.000 I/Unity (1): [PerfStats] t=30 scene=Main fps=1 main=1 render=1 hist=0,0,0,0,0,1")
    (d / "logcat_unity.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


class LegacyTest(unittest.TestCase):
    def test_import(self):
        with isolated_settings(PROFILE) as td:
            src = td / "old"
            make_legacy(src)
            rc, text = cli(["import", "legacy", str(src), "--device", "phone-a", "--build-hash", "abcd1234",
                            "--year", "2026", "--results", str(td / "results")])
            self.assertEqual(rc, 0, text)
            self.assertIn("scene=Main seconds=16 frames=960", text)
            self.assertIn("fps_avg=59.88", text)          # (58 + 15·60) / 16
            self.assertIn("on_budget_pct=99.79", text)    # 958 / 960
            d = td / "results" / "runs" / "20260102-1000-abcd1234-legacy-1"
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
            res = json.loads((d / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["device_class"], "device")
            self.assertTrue(meta["compat_mode"])
            self.assertEqual(meta["ap_c_start"], 40.0)
            self.assertEqual(res["layer"], "L3-D")
            self.assertEqual(res["items"]["MEM-03"]["values"]["pss_total_mb"], 901.5)
            self.assertTrue((d / "logcat.txt").exists())

    def test_bad_hash(self):
        with isolated_settings(PROFILE) as td:
            make_legacy(td / "old")
            rc, text = cli(["import", "legacy", str(td / "old"), "--device", "phone-a", "--build-hash", "x", "--print"])
            self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
