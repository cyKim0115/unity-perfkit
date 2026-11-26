import copy
import json
import re
import unittest

from perfkit import manifest as mf
from perfkit.paths import checklist_path

# (선택) 항목표 원본 md. 설정 paths.checklist(환경 변수 PERFKIT_CHECKLIST)가 있을 때만 ID를 대조한다.
CHECKLIST = checklist_path()
HAVE_CHECKLIST = bool(CHECKLIST and CHECKLIST.exists())
ID_RE = re.compile(r"\b([A-Z]{3})-(\d{2})((?:(?:\([^)]*\))?[~·]\d{2})*)")


def checklist_ids(text):
    """항목표 3절 표의 ID."""
    sec = text.split("## 3.", 1)[1].split("## 4.", 1)[0]
    return {m.group(1) for m in re.finditer(r"^\|\s*([A-Z]{3}-\d{2})\s*\|", sec, re.M)}


def expand(fragment):
    out = []
    for m in ID_RE.finditer(fragment):
        area, nums, tail = m.group(1), [int(m.group(2))], re.sub(r"\([^)]*\)", "", m.group(3))
        for op, n in re.findall(r"([~·])(\d{2})", tail):
            nums += list(range(nums[-1] + 1, int(n) + 1)) if op == "~" else [int(n)]
        out += [f"{area}-{n:02d}" for n in nums]
    return out


def checklist_sets(text):
    sec4 = text.split("## 4.", 1)[1].split("## 5.", 1)[0]
    s41, s42 = sec4.split("### 4.1", 1)[1].split("### 4.2", 1)

    def ids(block):
        found = set()
        for line in block.splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if line.startswith("|") and not line.startswith("|---") and len(cells) >= 2:
                found.update(expand(cells[1]))
        return found

    return ids(s41), ids(s42)


# 기본 세트(항목표 4.1)를 손으로 옮긴 값. 원본이 없는 PC에서도 도는 대조용
BASIC_41 = (
    [f"SCR-{n:02d}" for n in range(1, 10)] + [f"AST-{n:02d}" for n in range(1, 21)]
    + ["CPU-06", "CPU-10", "FRM-05", "GPU-05", "GPU-17", "GPU-19", "GPU-20", "GPU-21", "GPU-23",
       "BLD-04", "BLD-06", "BLD-08", "BLD-09", "THM-04"]
    + ["BLD-01", "BLD-02", "BLD-05", "BLD-07", "GPU-15", "MEM-09", "MEM-17"]
    + ["CPU-02", "CPU-09", "CPU-15", "GPU-03", "GPU-04", "GPU-07"]
    + ["FRM-01", "FRM-02", "FRM-03", "GPU-01", "GPU-02", "CPU-04", "MEM-01", "MEM-04",
       "THM-01", "THM-02", "THM-03", "THM-07", "BLD-10", "STB-08", "STB-09", "STB-10"]
)
BOTH = {"BLD-05", "BLD-07", "CPU-15", "GPU-20", "THM-02"}
EMU_FORBIDDEN_IDS = {"GPU-01", "GPU-02", "GPU-08", "GPU-09", "GPU-16", "GPU-24"} | {f"THM-{n:02d}" for n in range(1, 8)}


def load_doc():
    return mf.load().doc


class ManifestCountsTest(unittest.TestCase):
    def setUp(self):
        self.doc = load_doc()

    def test_check_passes(self):
        errors, _ = mf.check(self.doc)
        self.assertEqual(errors, [])

    def test_summary_line(self):
        line = mf.summary_line(self.doc)
        self.assertRegex(line, r"^checks=123 areas=9 FRM=5 CPU=15 SCR=9 GPU=25 MEM=18 AST=20 BLD=13 THM=7 STB=11 "
                               r"basic=73 deep=50 sha256=[0-9a-f]{64} ")

    def test_ids_unique_and_formatted(self):
        ids = [c["id"] for c in self.doc["checks"] + self.doc["aux"]]
        self.assertEqual(len(ids), len(set(ids)))
        for i in ids:
            self.assertRegex(i, r"^[A-Z]{3}-\d{2}$")

    def test_sets_match_section4(self):
        checks = {c["id"]: c for c in self.doc["checks"]}
        self.assertEqual(len(set(BASIC_41)), 72)
        basic = {i for i, c in checks.items() if c["set"] == "basic"}
        self.assertEqual(basic, set(BASIC_41) | {"MEM-05"})
        self.assertEqual({i for i, c in checks.items() if c["deep_variant"]}, BOTH)
        self.assertEqual(checks["GPU-14"]["set"], "deep")
        self.assertIn("제안", checks["GPU-14"]["set_basis"])
        self.assertIn("제안", checks["MEM-05"]["set_basis"])

    @unittest.skipUnless(HAVE_CHECKLIST, "항목표 원본 설정 없음(paths.checklist)")
    def test_one_to_one_with_checklist(self):
        text = CHECKLIST.read_text(encoding="utf-8")
        self.assertEqual(checklist_ids(text), {c["id"] for c in self.doc["checks"]})
        basic, deep = checklist_sets(text)
        self.assertEqual(basic, set(BASIC_41))
        self.assertEqual(len(deep - basic), 49)
        self.assertEqual(basic & deep, BOTH)


class ManifestClassRulesTest(unittest.TestCase):
    def setUp(self):
        self.doc = load_doc()

    def test_emulator_has_no_thermal_gpu_clock_battery_power(self):
        forbidden = set(self.doc["emulator_forbidden_tags"])
        for tag in ("thermal", "gpu_clock", "gpu_usage", "battery", "power"):
            self.assertIn(tag, forbidden)
        for it in self.doc["checks"] + self.doc["aux"]:
            if "L3-E" in it["layers"]:
                self.assertNotIn(it["id"], EMU_FORBIDDEN_IDS)
                self.assertFalse(forbidden & set(it["tags"]), it["id"])

    def test_emulator_judge_is_change_only(self):
        for it in self.doc["checks"] + self.doc["aux"]:
            spec = it["by_layer"].get("L3-E")
            if spec:
                self.assertIn(spec["judge"], ("change_only", "record"), it["id"])
                self.assertIn(spec["noise"], ("pending_aa",), it["id"])

    def test_basis_tags(self):
        for it in self.doc["checks"]:
            self.assertTrue(set(it["criteria"]["basis"]) <= {"공식", "제안", "프로젝트가 정함", "없음"})
            for r in it["criteria"]["absolute"]:
                self.assertIn(r["basis"], ("공식", "제안", "프로젝트가 정함"))

    def test_collectors_named_by_owner_package(self):
        for it in self.doc["checks"] + self.doc["aux"]:
            for layer, spec in it["by_layer"].items():
                self.assertRegex(spec["collector"], r"^(static|build|editor|device|run|console)\.[a-z0-9_]+$")


class ManifestNegativeTest(unittest.TestCase):
    """틀린 매니페스트를 check가 잡는지."""

    def setUp(self):
        self.doc = load_doc()

    def errors_after(self, mutate):
        d = copy.deepcopy(self.doc)
        mutate(d)
        errors, _ = mf.check(d)
        return " | ".join(errors)

    def find(self, d, iid):
        return next(c for c in d["checks"] + d["aux"] if c["id"] == iid)

    def test_emulator_absolute_judge_rejected(self):
        def m(d):
            self.find(d, "FRM-01")["by_layer"]["L3-E"]["judge"] = "absolute+change"
        self.assertIn("에뮬레이터는 절대 기준 금지", self.errors_after(m))

    def test_thermal_item_on_emulator_rejected(self):
        def m(d):
            it = self.find(d, "THM-01")
            it["layers"] = ["L3-E", "L3-D"]
            it["by_layer"]["L3-E"] = dict(it["by_layer"]["L3-D"], noise="pending_aa", judge="change_only")
        self.assertIn("에뮬레이터 금지 지표", self.errors_after(m))

    def test_duplicate_id_rejected(self):
        def m(d):
            d["checks"][1]["id"] = d["checks"][0]["id"]
        self.assertIn("ID 중복", self.errors_after(m))

    def test_missing_field_rejected(self):
        def m(d):
            del d["checks"][0]["collector" if "collector" in d["checks"][0] else "better"]
        self.assertIn("필수 칸 없음", self.errors_after(m))

    def test_count_rejected(self):
        def m(d):
            d["checks"].pop()
        out = self.errors_after(m)
        self.assertIn("항목 수 122", out)

    def test_absolute_on_l2_rejected(self):
        def m(d):
            self.find(d, "GPU-03")["by_layer"]["L2"]["judge"] = "absolute+change"
        self.assertIn("L2에는 절대 기준을 쓰지 않음", self.errors_after(m))

    def test_collector_package_must_match_layer(self):
        def m(d):
            self.find(d, "BLD-01")["by_layer"]["L1"]["collector"] = "device.apk_size"
        self.assertIn("패키지가 이 층에 맞지 않음", self.errors_after(m))


class ManifestHashTest(unittest.TestCase):
    def test_hash_ignores_key_order_and_whitespace(self):
        doc = load_doc()
        reordered = json.loads(json.dumps(doc, sort_keys=False, indent=4))
        reordered = dict(reversed(list(reordered.items())))
        self.assertEqual(mf.sha256_of(doc), mf.sha256_of(reordered))

    def test_hash_changes_with_content(self):
        doc = load_doc()
        d = copy.deepcopy(doc)
        d["checks"][0]["title"] += " "
        self.assertNotEqual(mf.sha256_of(doc), mf.sha256_of(d))

    def test_hash_matches_cli_value(self):
        m = mf.load()
        self.assertEqual(len(m.sha256), 64)
        self.assertRegex(m.version, r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
