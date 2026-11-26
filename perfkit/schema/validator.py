"""표준 라이브러리만으로 도는 작은 JSON Schema 검증기.

perfkit 스키마가 쓰는 키워드만 지원한다(2020-12 문법의 부분집합):
type, enum, const, required, properties, additionalProperties, patternProperties,
minProperties, maxProperties,
propertyNames, items, minItems, maxItems, uniqueItems, minimum, maximum,
exclusiveMinimum, exclusiveMaximum, minLength, maxLength, pattern, format(date-time만),
allOf, anyOf, oneOf, not, if/then/else, $ref(같은 파일의 "#/..."), $defs.

모르는 키워드는 무시하지 않고 오류로 낸다(스키마 오타를 잡으려고). 주석용 키
(title, description, $schema, $id, $comment, examples, default)는 무시한다.
"""

from __future__ import annotations

import datetime as _dt
import re
from functools import lru_cache
from dataclasses import dataclass

_ANNOTATIONS = {"title", "description", "$schema", "$id", "$comment", "examples", "default", "$defs"}
_KNOWN = _ANNOTATIONS | {
    "type", "enum", "const", "required", "properties", "additionalProperties",
    "patternProperties", "propertyNames", "items", "minItems", "maxItems", "uniqueItems", "minProperties", "maxProperties",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength",
    "pattern", "format", "allOf", "anyOf", "oneOf", "not", "if", "then", "else", "$ref",
}


@dataclass
class SchemaError:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path or '$'}: {self.message}"


class SchemaDefinitionError(Exception):
    """스키마 자체가 잘못됨(모르는 키워드, 풀 수 없는 $ref)."""


def _type_ok(value, t: str) -> bool:
    if t == "object":
        return isinstance(value, dict)
    if t == "array":
        return isinstance(value, list)
    if t == "string":
        return isinstance(value, str)
    if t == "boolean":
        return isinstance(value, bool)
    if t == "null":
        return value is None
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    raise SchemaDefinitionError(f"모르는 type: {t}")


@lru_cache(maxsize=None)
def _compile_ecma(pattern: str) -> re.Pattern:
    """JSON Schema(ECMA-262) 뜻대로 컴파일한다.

    파이썬 `$`는 끝의 줄바꿈 앞에서도 맞아 `^abc$`가 "abc\\n"을 통과시킨다. ECMA의 `$`(m 플래그 없음)는
    문자열 끝에서만 맞으므로, 이스케이프되지 않은 `$`를 `\\Z`로 바꾼다. 문자 클래스 안의 `$`는 그대로 둔다.
    """
    out, i, in_class = [], 0, False
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            out.append(pattern[i:i + 2])
            i += 2
            continue
        if in_class:
            if ch == "]":
                in_class = False
        elif ch == "[":
            in_class = True
        elif ch == "$":
            ch = r"\Z"
        out.append(ch)
        i += 1
    return re.compile("".join(out))


def _ecma_search(pattern: str, s: str) -> bool:
    return _compile_ecma(pattern).search(s) is not None


def _is_datetime(s: str) -> bool:
    try:
        _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _canon(v):
    # uniqueItems·enum 비교용. 1과 True를 구분한다.
    if isinstance(v, bool):
        return ("b", v)
    if isinstance(v, dict):
        return ("o", tuple(sorted((k, _canon(x)) for k, x in v.items())))
    if isinstance(v, list):
        return ("a", tuple(_canon(x) for x in v))
    return ("v", v)


class Validator:
    def __init__(self, schema: dict):
        self.root = schema
        self._check_keywords(schema, "#")

    def _check_keywords(self, node, where):
        if isinstance(node, bool):
            return
        if not isinstance(node, dict):
            raise SchemaDefinitionError(f"{where}: 스키마는 객체여야 함")
        unknown = set(node) - _KNOWN
        if unknown:
            raise SchemaDefinitionError(f"{where}: 모르는 키워드 {sorted(unknown)}")
        for key in ("properties", "patternProperties", "$defs"):
            for k, sub in node.get(key, {}).items():
                self._check_keywords(sub, f"{where}/{key}/{k}")
        for key in ("additionalProperties", "items", "not", "if", "then", "else", "propertyNames"):
            if key in node and not isinstance(node[key], bool):
                self._check_keywords(node[key], f"{where}/{key}")
        for key in ("allOf", "anyOf", "oneOf"):
            for i, sub in enumerate(node.get(key, [])):
                self._check_keywords(sub, f"{where}/{key}/{i}")

    def _resolve(self, ref: str):
        if not ref.startswith("#"):
            raise SchemaDefinitionError(f"외부 $ref는 지원하지 않음: {ref}")
        node = self.root
        for part in [p for p in ref[1:].split("/") if p]:
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or part not in node:
                raise SchemaDefinitionError(f"$ref를 풀 수 없음: {ref}")
            node = node[part]
        return node

    def errors(self, instance) -> list[SchemaError]:
        out: list[SchemaError] = []
        self._validate(instance, self.root, "", out)
        return out

    def is_valid(self, instance) -> bool:
        return not self.errors(instance)

    def _validate(self, inst, schema, path, out):
        if schema is True:
            return
        if schema is False:
            out.append(SchemaError(path, "허용하지 않는 값"))
            return
        if "$ref" in schema:
            self._validate(inst, self._resolve(schema["$ref"]), path, out)

        if "type" in schema:
            types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
            if not any(_type_ok(inst, t) for t in types):
                out.append(SchemaError(path, f"형식이 {'|'.join(types)}가 아님({type(inst).__name__})"))
                return
        if "const" in schema and _canon(inst) != _canon(schema["const"]):
            out.append(SchemaError(path, f"값은 {schema['const']!r}이어야 함"))
        if "enum" in schema and _canon(inst) not in {_canon(e) for e in schema["enum"]}:
            out.append(SchemaError(path, f"허용 값 {schema['enum']} 밖: {inst!r}"))

        if isinstance(inst, str):
            if "minLength" in schema and len(inst) < schema["minLength"]:
                out.append(SchemaError(path, f"길이 {schema['minLength']} 미만"))
            if "maxLength" in schema and len(inst) > schema["maxLength"]:
                out.append(SchemaError(path, f"길이 {schema['maxLength']} 초과"))
            if "pattern" in schema and not _ecma_search(schema["pattern"], inst):
                out.append(SchemaError(path, f"형식 {schema['pattern']} 불일치: {inst!r}"))
            if schema.get("format") == "date-time" and not _is_datetime(inst):
                out.append(SchemaError(path, f"ISO 8601 시각이 아님: {inst!r}"))

        if _type_ok(inst, "number"):
            if "minimum" in schema and inst < schema["minimum"]:
                out.append(SchemaError(path, f"{schema['minimum']} 미만"))
            if "maximum" in schema and inst > schema["maximum"]:
                out.append(SchemaError(path, f"{schema['maximum']} 초과"))
            if "exclusiveMinimum" in schema and inst <= schema["exclusiveMinimum"]:
                out.append(SchemaError(path, f"{schema['exclusiveMinimum']} 이하"))
            if "exclusiveMaximum" in schema and inst >= schema["exclusiveMaximum"]:
                out.append(SchemaError(path, f"{schema['exclusiveMaximum']} 이상"))

        if isinstance(inst, list):
            if "minItems" in schema and len(inst) < schema["minItems"]:
                out.append(SchemaError(path, f"항목 {schema['minItems']}개 미만"))
            if "maxItems" in schema and len(inst) > schema["maxItems"]:
                out.append(SchemaError(path, f"항목 {schema['maxItems']}개 초과"))
            if schema.get("uniqueItems"):
                seen = set()
                for i, v in enumerate(inst):
                    c = _canon(v)
                    if c in seen:
                        out.append(SchemaError(f"{path}[{i}]", "중복 항목"))
                    seen.add(c)
            if "items" in schema:
                for i, v in enumerate(inst):
                    self._validate(v, schema["items"], f"{path}[{i}]", out)

        if isinstance(inst, dict):
            if "minProperties" in schema and len(inst) < schema["minProperties"]:
                out.append(SchemaError(path, f"칸 {schema['minProperties']}개 미만"))
            if "maxProperties" in schema and len(inst) > schema["maxProperties"]:
                out.append(SchemaError(path, f"칸 {schema['maxProperties']}개 초과"))
            for key in schema.get("required", []):
                if key not in inst:
                    out.append(SchemaError(path, f"필수 칸 없음: {key}"))
            props = schema.get("properties", {})
            pats = schema.get("patternProperties", {})
            for k, v in inst.items():
                sub = f"{path}.{k}" if path else k
                matched = False
                if k in props:
                    matched = True
                    self._validate(v, props[k], sub, out)
                for pat, psch in pats.items():
                    if _ecma_search(pat, k):
                        matched = True
                        self._validate(v, psch, sub, out)
                if not matched and "additionalProperties" in schema:
                    ap = schema["additionalProperties"]
                    if ap is False:
                        out.append(SchemaError(path, f"허용하지 않는 칸: {k}"))
                    elif isinstance(ap, dict):
                        self._validate(v, ap, sub, out)
                if "propertyNames" in schema:
                    errs: list[SchemaError] = []
                    self._validate(k, schema["propertyNames"], sub, errs)
                    if errs:
                        out.append(SchemaError(path, f"칸 이름 규칙 위반: {k}"))

        for sub in schema.get("allOf", []):
            self._validate(inst, sub, path, out)
        if "anyOf" in schema:
            if not any(not self._sub_errors(inst, s, path) for s in schema["anyOf"]):
                out.append(SchemaError(path, "anyOf 어느 것에도 맞지 않음"))
        if "oneOf" in schema:
            n = sum(1 for s in schema["oneOf"] if not self._sub_errors(inst, s, path))
            if n != 1:
                out.append(SchemaError(path, f"oneOf 중 정확히 하나여야 함({n}개 맞음)"))
        if "not" in schema and not self._sub_errors(inst, schema["not"], path):
            out.append(SchemaError(path, "not 조건에 맞음"))
        if "if" in schema:
            if not self._sub_errors(inst, schema["if"], path):
                if "then" in schema:
                    self._validate(inst, schema["then"], path, out)
            elif "else" in schema:
                self._validate(inst, schema["else"], path, out)

    def _sub_errors(self, inst, schema, path):
        errs: list[SchemaError] = []
        self._validate(inst, schema, path, errs)
        return errs
