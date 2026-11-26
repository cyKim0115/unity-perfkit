import json
import tempfile
import unittest
from pathlib import Path

from perfkit.diff import resultio
from perfkit.schema import validate
from perfkit.static import csharp
from perfkit.static import unityyaml as Y
from perfkit.static.collect import run
from tests.fakeproj import isolated_settings, make_project

CFG = {"source_dirs": ["Assets"], "asset_dirs": ["Assets"],
       "code_rules": [{"id": "find_object_of_type", "pattern": "FindObjectOfType", "note": "t"}]}


class UnityYamlTest(unittest.TestCase):
    def test_nested_map_list_flow(self):
        d = Y.parse("A:\n  b: 1\n  c:\n  - x: 2\n    y: {p: 3, q: [4, 5]}\n  - plain\n  e: []\n  f: 'q''s'\n")
        self.assertEqual(d["A"]["b"], "1")
        self.assertEqual(d["A"]["c"][0], {"x": "2", "y": {"p": "3", "q": ["4", "5"]}})
        self.assertEqual(d["A"]["c"][1], "plain")
        self.assertEqual(d["A"]["e"], [])
        self.assertEqual(d["A"]["f"], "q's")

    def test_docs_and_skip(self):
        docs = Y.parse_docs("--- !u!114 &5\nMono:\n  m_Name: a\n--- !u!114 &-7\nMono:\n  m_Name: b\n")
        self.assertEqual([(d.class_id, d.file_id, d.body["m_Name"]) for d in docs], [(114, "5", "a"), (114, "-7", "b")])
        d = Y.parse("T:\n  big:\n  - a: 1\n  - a: 2\n  keep: 3\n", skip={"big"})
        self.assertEqual(d["T"]["keep"], "3")

    def test_hex_ints(self):
        self.assertEqual(Y.hex_ints("150000000b000000i"), [21, 11])


class CSharpTest(unittest.TestCase):
    def test_strip_keeps_positions(self):
        src = 'var a = "x // y"; // Camera.main\n/* Camera.main */ var b = @"q""r"; var c = $"{d}";\n'
        st = csharp.strip(src)
        self.assertEqual(len(st.code), len(src))
        self.assertNotIn("Camera", st.code)
        self.assertEqual(len(st.interp), 1)
        self.assertEqual(st.code.count("\n"), 2)

    def test_preprocessor(self):
        code = "#if UNITY_EDITOR\na\n#elif FOO\nb\n#else\nc\n#endif\n#if !UNITY_EDITOR\nd\n#endif\n"
        off = csharp.inactive_lines(code, {"UNITY_EDITOR": False})
        self.assertIn(2, off)       # a
        self.assertNotIn(4, off)    # b: FOO 모름
        self.assertNotIn(6, off)    # c: 모름 → 포함
        self.assertNotIn(9, off)    # d

    def test_scopes(self):
        code = csharp.strip("class A { void Update() { if (x) { y(); } } int P { get { return 1; } } void F() => G(); }").code
        ix = csharp.ScopeIndex(code)
        self.assertEqual([m.name for m in ix.methods], ["Update"])
        self.assertEqual(ix.enclosing_methods(code.index("y()")), ["Update"])
        self.assertEqual(ix.enclosing_methods(code.index("G()")), ["F"])


class StaticCollectTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = make_project(Path(self._td.name) / "proj")

    def tearDown(self):
        self._td.cleanup()

    def _run(self):
        with isolated_settings():
            return run(self.root, CFG)

    def test_values_on_fake_project(self):
        res, meta = self._run()
        self.assertEqual(validate(res, "result"), [])
        it = res["items"]
        v = lambda iid: it[iid]["values"]
        self.assertEqual(it["SCR-02"]["detail"]["by_rule"],
                         {"linq": 2, "mesh_arrays": 1, "new_alloc_hot": 1, "string_concat": 1, "tag_compare": 1})
        self.assertEqual(it["SCR-03"]["detail"]["by_rule"], {"camera_main": 1, "find_object": 1, "get_component_hot": 2})
        self.assertEqual(v("SCR-04"), {"value": 2})
        self.assertEqual(v("SCR-06"), {"value": 1})
        self.assertEqual(v("SCR-07"), {"value": 1})  # 대입(= null)은 세지 않음
        self.assertEqual(v("SCR-05"), {"debug_log_calls": 1, "debug_log_in_update": 1})
        self.assertEqual(it["SCR-05"]["detail"]["excluded"], {"conditional_method": 1, "inactive_region": 1})
        self.assertEqual(v("SCR-08"), {"violations": 1, "by_rule": {"find_object_of_type": 1}})
        self.assertEqual(v("SCR-01"), {"issues_total": 14, "major": 8})
        # 주석·문자열·에디터 코드 안 Camera.main은 세지 않는다
        lines = [f["line"] for f in it["SCR-03"]["findings"] if f["rule"] == "camera_main"]
        self.assertEqual(lines, [12])
        # 임포트 규칙
        self.assertEqual(v("AST-01"), {"value": 1})
        self.assertEqual(it["AST-01"]["violations"], ["Assets/Tex/a.png"])
        self.assertEqual(v("AST-02"), {"value": 1})
        self.assertEqual(it["AST-03"]["detail"]["ui_with_mipmaps"], 1)
        self.assertEqual(it["AST-03"]["detail"]["default_without_mipmaps"], 1)
        self.assertEqual(it["AST-04"]["detail"]["npot_not_scaled"], 1)
        for iid in ("AST-09", "AST-10", "AST-17", "AST-18"):
            self.assertEqual(v(iid), {"value": 1}, iid)
        self.assertEqual(v("AST-12"), {"value": 1})
        self.assertEqual(v("AST-14"), {"value": 1})
        self.assertEqual(v("AST-15"), {"value": 1})
        self.assertEqual(it["AST-16"]["detail"]["stereo_not_forced_mono"], 1)
        self.assertEqual(v("AST-20"), {"value": 1})
        self.assertEqual(v("AST-19"), {"value": 1})  # 아틀라스 데이터가 앞에 와도 폰트 칸을 찾음
        self.assertEqual(it["AST-19"]["detail"]["fonts"][0]["atlas_width"], 1024)
        # 설정
        self.assertEqual(v("BLD-06"), {"target_api": 35})
        self.assertEqual(v("BLD-08"), {"arm64_only": False})
        self.assertEqual(v("CPU-03"), {"incremental_gc": False})
        self.assertEqual(v("BLD-13")["value"]["apis"], ["Vulkan", "OpenGLES3"])
        self.assertEqual(v("GPU-17")["value"][0]["m_MSAA"], 4)
        self.assertEqual(v("GPU-20")["value"][0]["name"], "SSAO")
        self.assertEqual(v("GPU-10"), {"alpha_clip_materials": 1})
        failed = {c["id"] for c in it["SCR-09"]["detail"]["checks"] if c["failed"]}
        self.assertEqual(failed, {"PAS0012", "PAS0034", "PAS1008"})
        # 에디터 필요 항목은 이유와 함께 수집 못 함
        for iid in ("CPU-10", "GPU-05", "MEM-09", "AST-07", "AST-13"):
            self.assertEqual(it[iid]["status"], "not_collected")
            self.assertTrue(it[iid]["reason"].startswith("에디터 필요"), iid)
        # L0 항목만, 빠짐없이
        from perfkit.manifest import load
        with isolated_settings():
            l0 = {i["id"] for i in load().items_for(layer="L0")}
        self.assertEqual(set(it), l0)

    def test_same_input_same_bytes(self):
        a, ma = self._run()
        b, mb = self._run()
        self.assertEqual(resultio.dumps(a), resultio.dumps(b))
        self.assertNotIn(str(self.root), resultio.dumps(a))  # 절대 경로는 실행 메타에만
        self.assertIn("project", ma)

    def test_editor_and_cli_write(self):
        from tests.test_commands import run as cli_run
        with isolated_settings() as td:
            out = Path(td) / "out" / "source.json"
            rc, text = cli_run(["static", "--project", str(self.root), "--out", str(out)])
            self.assertEqual(rc, 0, text)
            self.assertTrue(out.exists())
            self.assertTrue(out.with_name("source.run.json").exists())
            self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["layer"], "L0")


if __name__ == "__main__":
    unittest.main()
