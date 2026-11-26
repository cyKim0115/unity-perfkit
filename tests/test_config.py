import re
import tempfile
import unittest
from pathlib import Path

from perfkit import config
from perfkit.manifest import apply_profile, check, load
from perfkit.paths import REPO_ROOT

EXAMPLE_USER = REPO_ROOT / "config" / "config.example.toml"
EXAMPLE_PROFILE = REPO_ROOT / "config" / "profile.example.toml"


def write(dirpath, name, text):
    p = Path(dirpath) / name
    p.write_text(text, encoding="utf-8")
    return p


class PrecedenceTest(unittest.TestCase):
    def test_env_beats_user_beats_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            prof = write(tmp, "profile.toml", 'name = "x"\n[app]\npackage = "com.example.a"\n[paths]\nresults = "res"\n')
            user = write(tmp, "config.toml", f'profile = "profile.toml"\n[app]\npackage = "com.example.b"\n')
            s = config.load(user_path=user, env={})
            self.assertEqual(s.lookup("app.package"), ("com.example.b", "user"))
            self.assertEqual(s.lookup("paths.results")[1], "profile")
            self.assertEqual(Path(s.get("paths.results")), (Path(tmp) / "res").resolve())  # 프로필 폴더 기준
            s = config.load(user_path=user, env={"PERFKIT_PACKAGE": "com.example.c"})
            self.assertEqual(s.lookup("app.package"), ("com.example.c", "env:PERFKIT_PACKAGE"))

    def test_missing_required_raises(self):
        s = config.load(user_path=Path(tempfile.gettempdir()) / "nope-perfkit.toml", env={})
        with self.assertRaises(config.ConfigError):
            s.require("paths.results")
        self.assertEqual(s.get("tools.adb"), "adb")  # 기본값은 PATH의 명령

    def test_command_names_not_resolved_as_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            user = write(tmp, "config.toml", '[tools]\nadb = "adb"\nunity_slot = "bin/slot.cmd"\n')
            s = config.load(user_path=user, env={})
            self.assertEqual(s.get("tools.adb"), "adb")
            self.assertEqual(Path(s.get("tools.unity_slot")), (Path(tmp) / "bin" / "slot.cmd").resolve())

    def test_devices_merge_profile_user_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, "p.toml", '[devices.phone-a]\nkind = "device"\nram_class = "8GB"\n')
            user = write(tmp, "c.toml", 'profile = "p.toml"\n[devices.phone-a]\nserial = "AAA"\n')
            s = config.load(user_path=user, env={})
            self.assertEqual(s.device("phone-a"), {"kind": "device", "ram_class": "8GB", "serial": "AAA"})
            s = config.load(user_path=user, env={"PERFKIT_DEVICE_PHONE_A_SERIAL": "BBB"})
            self.assertEqual(s.device("phone-a")["serial"], "BBB")
            with self.assertRaises(config.ConfigError):
                s.device("nope")

    def test_missing_profile_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            user = write(tmp, "c.toml", 'profile = "missing.toml"\n')
            with self.assertRaises(config.ConfigError):
                config.load(user_path=user, env={})


class ExampleFilesTest(unittest.TestCase):
    def test_examples_parse(self):
        s = config.load(user_path=EXAMPLE_USER, profile_path=EXAMPLE_PROFILE, env={})
        self.assertEqual(s.get("app.package"), "com.example.game")
        self.assertEqual(s.device("phone-a")["kind"], "device")
        self.assertEqual(len(s.profile_sha), 64)

    def test_example_profile_applies_to_manifest(self):
        s = config.load(user_path=Path(tempfile.gettempdir()) / "nope-perfkit.toml", profile_path=EXAMPLE_PROFILE, env={})
        doc = apply_profile(load().doc, s.profile)
        errors, _ = check(doc)
        self.assertEqual(errors, [])
        mem = next(c for c in doc["checks"] if c["id"] == "MEM-04")
        self.assertEqual(mem["by_layer"]["L3-E"]["noise"], "low")
        self.assertIn("project_note", next(c for c in doc["checks"] if c["id"] == "FRM-01"))

    def test_profile_drop_layer_and_bad_keys(self):
        base = load().doc
        doc = apply_profile(base, {"overrides": {"GPU-07": {"drop_layers": ["L3-E"]}}})
        g = next(c for c in doc["checks"] if c["id"] == "GPU-07")
        self.assertNotIn("L3-E", g["layers"])
        self.assertEqual(check(doc)[0], [])
        with self.assertRaises(ValueError):
            apply_profile(base, {"overrides": {"GPU-07": {"typo": 1}}})
        with self.assertRaises(ValueError):
            apply_profile(base, {"overrides": {"XYZ-01": {"note": "x"}}})
        bad = apply_profile(base, {"overrides": {"FRM-01": {"layers": {"L3-E": {"judge": "absolute+change"}}}}})
        self.assertTrue(any("에뮬레이터는 절대 기준 금지" in e for e in check(bad)[0]))


class NoMachineValuesInCodeTest(unittest.TestCase):
    """PC 전용 값(드라이브 경로)이 코드·예시·매니페스트에 박히지 않았는지."""

    def test_no_drive_paths(self):
        drive = re.compile(r"\b[A-Za-z]:[\\/](?!path/)")
        files = list((REPO_ROOT / "perfkit").rglob("*.py")) + list((REPO_ROOT / "perfkit").rglob("*.json"))
        files += [REPO_ROOT / "manifest" / "checks.json"] + list((REPO_ROOT / "examples").glob("*.json"))
        for p in files:
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                self.assertIsNone(drive.search(line), f"{p.relative_to(REPO_ROOT)}:{n}: {line.strip()[:80]}")


if __name__ == "__main__":
    unittest.main()
