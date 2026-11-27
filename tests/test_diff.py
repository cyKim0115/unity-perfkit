import tempfile
import unittest
from pathlib import Path

from perfkit.diff import resultio
from perfkit.diff.compare import diff_results
from perfkit.schema import validate
from perfkit.static.collect import run
from tests.fakeproj import isolated_settings, make_project
from tests.test_commands import run as cli_run
from tests.test_static import CFG


class DiffTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = make_project(Path(self._td.name) / "proj")

    def tearDown(self):
        self._td.cleanup()

    def _scan(self):
        with isolated_settings():
            return run(self.root, CFG)[0]

    def _diff(self, a, b):
        with isolated_settings():
            return diff_results(a, b)

    def test_same_is_zero(self):
        a, b = self._scan(), self._scan()
        cmp = self._diff(a, b)
        self.assertEqual(validate(cmp, "comparison"), [])
        self.assertEqual(cmp["summary"]["changes"], 0)
        self.assertEqual(cmp["summary"]["new_violations"], 0)
        self.assertNotIn("warn", {d["outcome"] for iid, d in cmp["items"].items() if iid.startswith("SCR")})

    def test_new_and_removed_violations(self):
        base = self._scan()
        p = self.root / "Assets/Scripts/Player.cs"
        src = p.read_text(encoding="utf-8")
        src = src.replace("    void LateUpdate() { }\n", "")  # 빈 이벤트 하나 없어짐
        # 앞 줄에 코드를 더해 줄 번호가 밀려도 기존 위반은 같은 지문이어야 한다
        src = src.replace("    void Start()\n    {\n", "    void Start()\n    {\n        var extra = Camera.main;\n")
        src = src.replace("public class Player", "// moved\n// moved\npublic class Player")
        p.write_text(src, encoding="utf-8")
        cand = self._scan()
        cmp = self._diff(base, cand)
        it = cmp["items"]
        self.assertEqual(it["SCR-03"]["violations"]["new_count"], 1)
        self.assertEqual(it["SCR-03"]["violations"]["removed_count"], 0)
        self.assertEqual(it["SCR-03"]["violations"]["new"][0]["rule"], "camera_main")
        self.assertEqual(it["SCR-03"]["outcome"], "warn")          # 결정적 개수 +1 → 경고
        self.assertEqual(it["SCR-06"]["violations"]["removed_count"], 1)
        self.assertEqual(it["SCR-06"]["outcome"], "improved")
        self.assertEqual(it["SCR-01"]["outcome"], "pass")          # +1·-1로 합계·Major가 같음
        self.assertTrue(it["SCR-01"]["changed"] is False)
        self.assertEqual(cmp["summary"]["new_violations"], 1)
        self.assertEqual(cmp["summary"]["removed_violations"], 1)

    def test_asset_violation_paths(self):
        base = self._scan()
        meta = self.root / "Assets/Tex/b.png.meta"
        meta.write_text(meta.read_text(encoding="utf-8").replace("isReadable: 0", "isReadable: 1"), encoding="utf-8")
        cand = self._scan()
        it = self._diff(base, cand)["items"]
        self.assertEqual(it["AST-02"]["violations"]["new"], [{"path": "Assets/Tex/b.png"}])
        self.assertEqual(it["AST-02"]["outcome"], "warn")

    def test_cli(self):
        a = self._scan()
        with isolated_settings() as td:
            pa, pb = Path(td) / "a.json", Path(td) / "b.json"
            resultio.write(pa, a)
            resultio.write(pb, a)
            rc, text = cli_run(["diff", str(pa), str(pb), "--out", str(Path(td) / "c.json")])
            self.assertEqual(rc, 0, text)
            self.assertIn("changes=0", text)
            rc, text = cli_run(["diff", str(pb), "--fail-on", "fail"])  # 기준 없이: BLD-06 target_api 35 < 36 → 실패
            self.assertEqual(rc, 1, text)


if __name__ == "__main__":
    unittest.main()


class DiffMissingFileTest(unittest.TestCase):
    def test_missing_file_is_error_not_traceback(self):
        with tempfile.TemporaryDirectory() as td:
            rc, out = cli_run(["diff", str(Path(td) / "nope.json")])
        self.assertEqual(rc, 2)
        self.assertIn("error: 결과 파일을 읽지 못함", out)
