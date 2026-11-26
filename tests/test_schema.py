import copy
import json
import unittest
from pathlib import Path

from perfkit.schema import KINDS, load_schema, validate
from perfkit.schema.validator import SchemaDefinitionError, Validator

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def load(name):
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def messages(errors):
    return " | ".join(str(e) for e in errors)


class ValidatorTest(unittest.TestCase):
    def test_basic_keywords(self):
        v = Validator({
            "type": "object",
            "required": ["a"],
            "properties": {"a": {"type": "integer", "minimum": 1}, "b": {"enum": ["x", None]}},
            "additionalProperties": False,
        })
        self.assertTrue(v.is_valid({"a": 1, "b": None}))
        self.assertFalse(v.is_valid({"a": 0}))
        self.assertFalse(v.is_valid({"a": True}))  # bool은 integer가 아님
        self.assertFalse(v.is_valid({"a": 1, "c": 1}))
        self.assertFalse(v.is_valid({}))

    def test_ref_if_then_oneof(self):
        v = Validator({
            "$defs": {"pos": {"type": "number", "exclusiveMinimum": 0}},
            "type": "object",
            "properties": {"n": {"$ref": "#/$defs/pos"}},
            "if": {"properties": {"k": {"const": "x"}}, "required": ["k"]},
            "then": {"required": ["n"]},
            "oneOf": [{"required": ["k"]}, {"required": ["m"]}],
        })
        self.assertTrue(v.is_valid({"k": "x", "n": 2}))
        self.assertFalse(v.is_valid({"k": "x"}))
        self.assertFalse(v.is_valid({"k": "y", "m": 1}))  # oneOf 둘 다 맞음
        self.assertFalse(v.is_valid({"k": "x", "n": -1}))

    def test_pattern_dollar_is_end_of_string(self):
        # 파이썬 `$`는 끝 줄바꿈 앞에서도 맞는다. JSON Schema(ECMA) 뜻대로 끝에서만 맞아야 한다.
        v = Validator({"type": "string", "pattern": "^[0-9a-f]{4}$"})
        self.assertTrue(v.is_valid("abcd"))
        self.assertFalse(v.is_valid("abcd\n"))
        self.assertFalse(v.is_valid("abcd\nzz"))
        v = Validator({"type": "string", "pattern": "^a[$]b$"})  # 문자 클래스 안의 $는 글자 그대로
        self.assertTrue(v.is_valid("a$b"))
        v = Validator({"type": "object", "propertyNames": {"pattern": "^[A-Z]{3}-[0-9]{2}$"}})
        self.assertFalse(v.is_valid({"FRM-01\n": 1}))
        meta = load("run-meta.device.json")
        meta["apk_sha"] = meta["apk_sha"] + "\n"
        self.assertNotEqual(validate(meta), [])

    def test_unknown_keyword_rejected(self):
        with self.assertRaises(SchemaDefinitionError):
            Validator({"type": "object", "requird": ["a"]})

    def test_all_schemas_load(self):
        for k in KINDS:
            Validator(load_schema(k))


class RunMetaTest(unittest.TestCase):
    def test_examples_valid(self):
        for name in ("run-meta.device.json", "run-meta.emulator.json"):
            self.assertEqual(validate(load(name)), [], name)

    def test_emulator_without_emu_rejected(self):
        meta = load("run-meta.emulator.json")
        del meta["emu"]
        errs = validate(meta)
        self.assertIn("필수 칸 없음: emu", messages(errs))

    def test_emulator_with_empty_emu_field_rejected(self):
        meta = load("run-meta.emulator.json")
        meta["emu"]["gpu_mode"] = ""
        self.assertNotEqual(validate(meta), [])
        meta = load("run-meta.emulator.json")
        del meta["emu"]["arm_translation"]
        self.assertNotEqual(validate(meta), [])

    def test_emulator_without_host_busy_rejected(self):
        meta = load("run-meta.emulator.json")
        del meta["host"]["cpu_busy_during"]
        self.assertIn("cpu_busy_during", messages(validate(meta)))

    def test_emulator_must_not_fill_device_only_fields(self):
        meta = load("run-meta.emulator.json")
        meta["ap_c_start"] = 38.0
        self.assertNotEqual(validate(meta), [])

    def test_device_requires_thermal_fields(self):
        meta = load("run-meta.device.json")
        del meta["ap_c_start"]
        self.assertIn("ap_c_start", messages(validate(meta)))

    def test_invalid_run_needs_reason(self):
        meta = load("run-meta.emulator.json")
        meta["valid"] = False
        self.assertIn("invalid_reason", messages(validate(meta)))
        meta["invalid_reason"] = "호스트 CPU 측정 중 61%"
        self.assertEqual(validate(meta), [])

    def test_bad_device_class(self):
        meta = load("run-meta.device.json")
        meta["device_class"] = "phone"
        self.assertNotEqual(validate(meta), [])


class ResultTest(unittest.TestCase):
    def test_example_valid(self):
        self.assertEqual(validate(load("result.l3e.json")), [])

    def test_not_collected_needs_reason(self):
        res = load("result.l3e.json")
        res["items"]["STB-08"] = {"status": "not_collected"}
        self.assertNotEqual(validate(res), [])
        res["items"]["STB-08"] = {"status": "not_collected", "reason": ""}
        self.assertNotEqual(validate(res), [])

    def test_ok_needs_values(self):
        res = load("result.l3e.json")
        res["items"]["FRM-01"] = {"status": "ok"}
        self.assertNotEqual(validate(res), [])

    def test_l3e_must_be_emulator(self):
        res = load("result.l3e.json")
        res["device_class"] = "device"
        self.assertNotEqual(validate(res), [])
        res = load("result.l3e.json")
        res["layer"] = "L3-D"
        self.assertNotEqual(validate(res), [])  # L3-D인데 emulator
        res = load("result.l3e.json")
        del res["device_class"]
        self.assertNotEqual(validate(res), [])

    def test_bad_item_id(self):
        res = load("result.l3e.json")
        res["items"]["XYZ-01"] = {"status": "ok", "values": {"value": 1}}
        self.assertNotEqual(validate(res), [])


class ComparisonTest(unittest.TestCase):
    def test_example_valid(self):
        self.assertEqual(validate(load("comparison.l3e.json")), [])

    def test_bad_outcome(self):
        c = load("comparison.l3e.json")
        c["items"]["MEM-04"]["outcome"] = "better"
        self.assertNotEqual(validate(c), [])

    def test_l3_needs_device_class(self):
        c = load("comparison.l3e.json")
        del c["device_class"]
        self.assertNotEqual(validate(c), [])
        c = copy.deepcopy(load("comparison.l3e.json"))
        c["device_class"] = "device"
        self.assertNotEqual(validate(c), [])


if __name__ == "__main__":
    unittest.main()
