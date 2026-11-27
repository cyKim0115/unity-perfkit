"""이식성: 설정 경로의 `~`·환경 변수, 빌드 명령 자리, 측정 단계 이름, 측정 모드 경고 메타, 수집기 상태, 예시 설정."""

from __future__ import annotations

import dataclasses
import json
import os
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from perfkit import collectors
from perfkit import config as C
from perfkit.compare import pipeline as PL
from perfkit.paths import REPO_ROOT, expand_path, expand_text
from perfkit.run import metrics as M
from perfkit.run import probe as PB
from perfkit.run.runner import summarize_hint
from tests.fakeadb import CSV_FIXTURE, LOG_FIXTURE


class ExpandTest(unittest.TestCase):
    def test_expand_path(self):
        with mock.patch.dict(os.environ, {"PK_TEST_ROOT": str(Path(tempfile.gettempdir()))}):
            self.assertEqual(expand_path("${PK_TEST_ROOT}/a"), Path(tempfile.gettempdir()) / "a")
        self.assertEqual(expand_path("~/x"), Path.home() / "x")
        base = Path(tempfile.gettempdir()).resolve()
        self.assertEqual(expand_path("sub/f.css", base), base / "sub" / "f.css")

    def test_expand_text_fields_after_env(self):
        with mock.patch.dict(os.environ, {"PK_TOOL": "tool"}):
            self.assertEqual(expand_text("${PK_TOOL}-{commit8}", commit8="abc12345"), "tool-abc12345")
        self.assertEqual(expand_text("{a}/{b}", a="1", b="2"), "1/2")

    def test_profile_paths_and_build_command(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "profile.toml").write_text(
                'name = "x"\n[paths]\nresults = "${PK_RES}/r"\n[baseline]\ndir = "bl"\n'
                '[build]\ncommand = ["${PK_TOOL}", "{profile_dir}/b.py", "{commit8}"]\n'
                'state_glob = "out/*/state.json"\n', encoding="utf-8")
            env = {"PK_RES": str(root / "res"), "PK_TOOL": "tool"}
            with mock.patch.dict(os.environ, env):
                s = C.load(root / "none.toml", profile_path=root / "profile.toml", env=env)
                self.assertEqual(Path(s.get("paths.results")), root / "res" / "r")
                cmd = PL._fmt_cmd(PL.build_cfg(s)["command"], commit="abc", commit8="abc12345", label="l",
                                  profile_dir=str(PL._profile_dir(s)))
                self.assertEqual(cmd[0], "tool")
                self.assertEqual(Path(cmd[1]), root / "b.py")
                self.assertEqual(cmd[2], "abc12345")
                self.assertEqual(PL._resolve(PL.build_cfg(s)["state_glob"], s), (root / "out" / "*" / "state.json").resolve())
                from perfkit.compare.baseline import baselines_dir

                self.assertEqual(baselines_dir(s), (root / "bl").resolve())


class PhaseNamesTest(unittest.TestCase):
    def setUp(self):
        self.log, _ = PB.parse_logcat_file(LOG_FIXTURE)
        self.frames = PB.parse_frames_file(CSV_FIXTURE)

    def test_count_phases_rename(self):
        ren = {"near": "close", "far": "wide"}
        log2 = dataclasses.replace(self.log, phases=[{**p, "name": ren.get(p["name"], p["name"])} for p in self.log.phases])
        frames2 = [{**f, "phase": ren.get(f.get("phase"), f.get("phase"))} for f in self.frames]
        a = M.frame_items(self.log, self.frames, "emulator")
        b = M.frame_items(log2, frames2, "emulator", ("close", "wide"))
        self.assertEqual(a["GPU-04"]["values"], b["GPU-04"]["values"])
        self.assertEqual(a["GPU-07"]["values"], b["GPU-07"]["values"])
        c = M.frame_items(log2, frames2, "emulator")  # 기본 이름이면 단계를 못 찾음
        self.assertEqual(c["GPU-07"]["status"], "not_collected")
        self.assertIn("near·far", c["GPU-07"]["reason"])

    def test_capture_start_phases(self):
        cap = {"local": "x/profiler.raw", "phase_frames": {"settle": 400, "near": 700, "orbit": 1100}}
        self.assertEqual(summarize_hint(cap)["skip_frames"], 700)
        self.assertEqual(summarize_hint(cap, ["orbit"])["skip_frames"], 1100)


class ProbeWarningsMetaTest(unittest.TestCase):
    def test_meta_has_probe_warnings(self):
        from tests.devsetup import Sandbox
        from tests.fakeadb import FakeDevice
        from tests.test_run_offline import RunOnceTest, _env
        from perfkit.run.runner import run_once

        orig = PB.parse_logcat_file

        def with_warning(path):
            log, lines = orig(path)
            log.warnings.append("[PerfProbeArgs] unknown arg -perfFoo")
            return log, lines

        with Sandbox() as sb:
            out = run_once(RunOnceTest().spec(sb, session="w1"), _env(sb, FakeDevice()))
            self.assertNotIn("probe_warnings", out.meta)
        with Sandbox() as sb, mock.patch.object(PB, "parse_logcat_file", with_warning):
            out = run_once(RunOnceTest().spec(sb, session="w2"), _env(sb, FakeDevice()))
            meta = json.loads((out.run_dir / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["probe_warnings"], ["[PerfProbeArgs] unknown arg -perfFoo"])


class CollectorStatusTest(unittest.TestCase):
    def test_status(self):
        reg = collectors.registered()
        self.assertEqual(collectors.status_of("build.apk_size", reg), "구현")
        self.assertEqual(collectors.status_of("static.ui_scan", reg), "에디터 합침")
        self.assertEqual(collectors.status_of("console.vitals", reg), "예정")
        self.assertFalse(collectors.PLANNED & set(reg))      # 예정 목록에 구현된 이름이 섞이지 않음
        self.assertFalse(collectors.EDITOR_MERGED & set(reg))

    def test_every_manifest_collector_has_known_status(self):
        from perfkit.manifest import collector_table, load

        rows = collector_table(load(REPO_ROOT / "manifest" / "checks.json"))
        self.assertEqual([r["name"] for r in rows if r["status"] == "미구현"], [])


class ExampleConfigTest(unittest.TestCase):
    def test_examples_parse_and_init_is_inert(self):
        user = tomllib.loads((REPO_ROOT / "config" / "config.example.toml").read_text(encoding="utf-8"))
        self.assertNotIn("profile", user)       # 예시를 그대로 두면 설정 없음과 같다
        prof = tomllib.loads((REPO_ROOT / "config" / "profile.example.toml").read_text(encoding="utf-8"))
        self.assertNotIn("events", prof)        # 읽지 않는 절은 예시에 두지 않는다
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "c.toml"
            (dst).write_text((REPO_ROOT / "config" / "config.example.toml").read_text(encoding="utf-8"), encoding="utf-8")
            s = C.load(dst, env={})
            self.assertIsNone(s.profile_path)
            self.assertEqual(s.devices(), {})
