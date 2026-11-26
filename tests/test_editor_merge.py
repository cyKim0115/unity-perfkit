import json
import tempfile
import unittest
from pathlib import Path

from perfkit.diff import resultio
from perfkit.diff.compare import diff_results
from perfkit.schema import validate
from perfkit.static.collect import run
from perfkit.static.editor_merge import EditorAuditError, merge
from tests.fakeproj import isolated_settings, make_project
from tests.test_commands import run as cli_run
from tests.test_static import CFG


def audit(commit=None):
    return {
        "schema": "perfkit/editor-audit@1", "layer": "L0",
        "tool": {"name": "editor-tool", "version": "1.0.0", "unity": "6000.0.0f1"},
        "source": {"kind": "editor", "commit": commit, "scope": ["Assets"]},
        "items": {
            "CPU-10": {"status": "ok", "collector": "static.ui_scan", "values": {"value": 3},
                       "detail": {"raycast_target_not_needed": 3}, "violations": ["raycast|a.prefab|x"]},
            "AST-06": {"status": "ok", "collector": "static.import_rules", "merge": "values", "values": {"value": 2},
                       "resolves": ["solid_color_not_1x1"], "violations": ["Assets/Tex/b.png"]},
            "BLD-09": {"status": "ok", "collector": "static.project_settings", "merge": "values",
                       "values": {"value": {"script_debugging": False, "development": True}},
                       "resolves": ["script_debugging·development"]},
            "SCR-02": {"status": "ok", "collector": "static.auditor_code", "merge": "detail",
                       "detail": {"auditor": {"PAC1000": 4}}},
            "MEM-09": {"status": "not_collected", "collector": "static.addressables_dupes", "reason": "설정 없음"},
            "XYZ-01": {"status": "ok", "values": {"value": 1}},
        },
        "auditor": {"settings": {"by_id": {"PAS0016": 1}}},
        "extra": {"samples": {"ast02_readable_texture_importers": 1},
                  "l1": {"GPU-15": {"keywords": 42, "detail": {"shaders": 3}}}},
        "errors": [],
    }


class EditorMergeTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = make_project(Path(self._td.name) / "proj")
        with isolated_settings():
            self.res, _ = run(self.root, CFG)

    def tearDown(self):
        self._td.cleanup()

    def test_merge_modes(self):
        out = merge(self.res, audit())
        it = out["items"]
        self.assertEqual(validate(out, "result"), [])
        self.assertEqual(it["CPU-10"]["status"], "ok")                    # 수집 못 함 → 에디터 값
        self.assertEqual(it["CPU-10"]["values"], {"value": 3})
        self.assertEqual(it["AST-06"]["values"], {"value": 2})
        self.assertNotIn("editor_needed", it["AST-06"])                    # 해결됨
        self.assertIn("Assets/Tex/b.png", it["AST-06"]["violations"])
        self.assertEqual(it["BLD-09"]["values"]["value"]["development"], True)
        self.assertIn("managed_code_variant", it["BLD-09"]["values"]["value"])  # dict 얕은 합침
        self.assertEqual(it["SCR-02"]["values"], self.res["items"]["SCR-02"]["values"])  # 값 그대로
        self.assertEqual(it["SCR-02"]["detail"]["editor"], {"auditor": {"PAC1000": 4}})
        self.assertEqual(it["MEM-09"]["status"], "not_collected")
        self.assertIn("에디터: 설정 없음", it["MEM-09"]["reason"])
        self.assertNotIn("XYZ-01", it)
        self.assertEqual(out["tool"]["editor"]["auditor"], {"settings": {"by_id": {"PAS0016": 1}}})
        self.assertNotIn("warnings", out["tool"])                           # 범위 같음, 커밋 정보 없음
        other = merge(self.res, {**audit(), "source": {"kind": "editor", "scope": ["Assets/Other"]}})
        self.assertIn("에디터 범위", other["tool"]["warnings"][0])
        self.assertEqual(self.res["items"]["CPU-10"]["status"], "not_collected")  # 원본은 그대로

    def test_merge_deterministic_and_diff_zero(self):
        a = merge(self.res, audit())
        b = merge(self.res, audit())
        self.assertEqual(resultio.dumps(a), resultio.dumps(b))
        with isolated_settings():
            cmp = diff_results(a, b)
        self.assertEqual(cmp["summary"]["changes"], 0)

    def test_l1_merge(self):
        l1 = {"schema": "perfkit/result@1", "layer": "L1", "manifest_version": "1.0.0", "manifest_sha": "0" * 64,
              "collected_at": "2026-01-01T00:00:00+00:00", "source": {"kind": "build", "ref": "x.apk"},
              "items": {"GPU-15": {"status": "ok", "collector": "build.shader_variants", "values": {"variants": 10},
                                   "editor_needed": ["keywords: 로그에 없음"]}}}
        out = merge(l1, audit(), layer="L1")
        self.assertEqual(out["items"]["GPU-15"]["values"], {"variants": 10, "keywords": 42})
        self.assertNotIn("editor_needed", out["items"]["GPU-15"])

    def test_bad_schema(self):
        with self.assertRaises(EditorAuditError):
            merge(self.res, {"schema": "x", "items": {}})

    def test_cli(self):
        with isolated_settings() as td:
            ap = Path(td) / "audit.json"
            ap.write_text(json.dumps(audit()), encoding="utf-8")
            out = Path(td) / "s" / "source.json"
            rc, text = cli_run(["static", "--project", str(self.root), "--editor", str(ap), "--out", str(out)])
            self.assertEqual(rc, 0, text)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["items"]["CPU-10"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
