"""HTML 보고와 회차 색인."""

import json
import re
import sqlite3
import unittest
from pathlib import Path

from tests.fakeproj import isolated_settings
from tests.runsfake import PROFILE_COMPARE, abab_session, noisy, values
from tests.test_commands import run as cli


class ReportIndexTest(unittest.TestCase):
    def setUp(self):
        self._cm = isolated_settings(PROFILE_COMPARE)
        self.td = self._cm.__enter__()
        self.results = self.td / "results"
        g = noisy(1, 400.0, 0.002)
        abab_session(self.results, "s1", 8, lambda r: values(g(), 30.0 + r * 0.01, 160.0),
                     lambda r: values(g() * 0.9, 30.0 + r * 0.01, 160.0))

    def tearDown(self):
        self._cm.__exit__(None, None, None)

    def _compare(self):
        rc, text = cli(["compare", "--results", str(self.results), "--runs", "session:s1", "--split", "arm", "--report"])
        self.assertEqual(rc, 0, text)
        saved = re.search(r"saved: (.+\.json)", text).group(1).strip()
        return Path(saved), Path(saved).with_suffix(".html")

    def test_report_html(self):
        cj, html_p = self._compare()
        self.assertTrue(cj.parent.name == "comparisons")
        h = html_p.read_text(encoding="utf-8")
        self.assertIn("L3-E 에뮬레이터", h)
        self.assertIn("실기기 확인 대기", h)
        self.assertIn("<svg", h)
        self.assertIn("prefers-color-scheme: dark", h)
        self.assertNotRegex(h, r"<link|<script|src=|https?://")      # 외부 자원 없음
        self.assertIn("측정 코드 해시", h)
        self.assertIn("probe-1", h)
        self.assertIn("판정하지 않은 지표", h)                       # 판정 제외 지표는 따로
        # 한 절(section)에는 한 등급만
        for sec in re.findall(r"<section id=\"(L[^\"]+)\">(.*?)</section>", h, re.S):
            labels = set(re.findall(r"L3-[ED] (?:에뮬레이터|실기기)", sec[1]))
            self.assertLessEqual(len(labels), 1)

    def test_report_custom_css_and_result(self):
        cj, _ = self._compare()
        css = self.td / "theme.css"
        css.write_text(":root { --bg: #123456; }\n", encoding="utf-8")
        out = self.td / "r.html"
        rc, text = cli(["report", str(cj), "--out", str(out), "--css", str(css)])
        self.assertEqual(rc, 0, text)
        self.assertIn("#123456", out.read_text(encoding="utf-8"))
        res = next((self.results / "runs").iterdir()) / "result.json"
        rc, text = cli(["report", str(res), "--out", str(self.td / "res.html")])
        self.assertEqual(rc, 0, text)
        self.assertIn("점검 결과", (self.td / "res.html").read_text(encoding="utf-8"))

    def test_profile_css(self):
        css = self.td / "p.css"
        css.write_text(":root { --bg: #abcdef; }\n", encoding="utf-8")
        with isolated_settings(PROFILE_COMPARE + f'\n[report]\ncss = "{css.as_posix()}"\n'):
            from perfkit.report.render import theme_css

            self.assertIn("#abcdef", theme_css())

    def test_index_counts(self):
        self._compare()
        rc, text = cli(["index", "rebuild", "--results", str(self.results)])
        self.assertEqual(rc, 0, text)
        self.assertIn("run_dirs=16 runs=16 jsonl_lines=16 comparisons=1", text)
        rc, text = cli(["index", "check", "--results", str(self.results)])
        self.assertEqual(rc, 0, text)
        con = sqlite3.connect(self.results / "runs.sqlite")
        try:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM run").fetchone()[0], 16)
            n = con.execute("SELECT COUNT(*) FROM metric WHERE item='MEM-04'").fetchone()[0]
            self.assertEqual(n, 16)
            v = con.execute("SELECT stat_verdict, direction FROM comparison WHERE item='MEM-04'").fetchone()
            self.assertEqual(v, ("different", "better"))
            self.assertGreater(con.execute("SELECT COUNT(*) FROM artifact").fetchone()[0], 16)
        finally:
            con.close()
        line = json.loads((self.results / "runs.jsonl").read_text(encoding="utf-8").splitlines()[0])
        self.assertIn("MEM-04.total_used_mb", line["metrics"])
        before = (self.results / "runs.jsonl").read_bytes()
        cli(["index", "rebuild", "--results", str(self.results)])
        self.assertEqual(before, (self.results / "runs.jsonl").read_bytes())   # 다시 만들어도 같음


if __name__ == "__main__":
    unittest.main()
