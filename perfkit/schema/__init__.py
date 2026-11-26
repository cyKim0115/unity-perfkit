"""JSON 스키마(run-meta·result·comparison·manifest)와 검증.

    from perfkit.schema import validate
    errors = validate(obj, "run-meta")     # [] 이면 통과

CLI: perfkit schema list / perfkit schema validate <파일...> [--kind 종류] [--manifest]
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from perfkit.paths import SCHEMA_DIR
from perfkit.schema.validator import SchemaError, Validator

KINDS = ("run-meta", "result", "comparison", "manifest")
SCHEMA_IDS = {f"perfkit/{k}@1": k for k in KINDS}


@lru_cache(maxsize=None)
def load_schema(kind: str) -> dict:
    if kind not in KINDS:
        raise KeyError(f"모르는 스키마 종류: {kind} (가능: {', '.join(KINDS)})")
    return json.loads((SCHEMA_DIR / f"{kind}.schema.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def validator(kind: str) -> Validator:
    return Validator(load_schema(kind))


def detect_kind(obj) -> str | None:
    if isinstance(obj, dict):
        return SCHEMA_IDS.get(obj.get("schema"))
    return None


def validate(obj, kind: str | None = None) -> list[SchemaError]:
    kind = kind or detect_kind(obj)
    if kind is None:
        return [SchemaError("schema", "종류를 알 수 없음(schema 칸 없음, --kind 필요)")]
    return validator(kind).errors(obj)


def cross_check(obj, kind: str, manifest) -> list[str]:
    """스키마로는 못 보는 매니페스트 대조(항목 존재, 층 허용, 버전·해시)."""
    problems: list[str] = []
    if kind not in ("result", "comparison", "run-meta"):
        return problems
    if obj.get("manifest_sha") and obj["manifest_sha"] != manifest.sha256:
        problems.append(
            f"warn: manifest_sha가 현재 매니페스트와 다름({obj['manifest_sha'][:12]} ≠ {manifest.sha256[:12]})"
        )
    if kind == "run-meta":
        return problems
    layer = obj.get("layer")
    for item_id in obj.get("items", {}):
        item = manifest.get(item_id)
        if item is None:
            problems.append(f"error: 매니페스트에 없는 항목 {item_id}")
        elif layer not in item["layers"]:
            problems.append(f"error: {item_id}는 {layer} 층에서 재지 않음(층 {item['layers']})")
    return problems


def _cmd_list(args) -> int:
    for k in KINDS:
        s = load_schema(k)
        print(f"{k:11} {s.get('$id')}  {s.get('title', '')}")
    return 0


def _cmd_validate(args) -> int:
    manifest = None
    if args.manifest:
        from perfkit.manifest import load as load_manifest

        manifest = load_manifest()
    bad = 0
    for path in args.files:
        try:
            obj = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"FAIL {path}: 읽기 실패 {exc}")
            bad += 1
            continue
        kind = args.kind or detect_kind(obj)
        errors = validate(obj, kind)
        extra = cross_check(obj, kind, manifest) if (manifest and kind) else []
        hard = [e for e in extra if e.startswith("error")]
        if errors or hard:
            bad += 1
            print(f"FAIL {path} ({kind}): {len(errors) + len(hard)}건")
            for e in errors[:50]:
                print(f"  {e}")
            for e in hard:
                print(f"  {e}")
        else:
            print(f"OK   {path} ({kind})")
        for e in extra:
            if not e.startswith("error"):
                print(f"  {e}")
    return 1 if bad else 0


def register_cli(sub) -> None:
    p = sub.add_parser("schema", help="결과·회차 메타·비교 JSON 스키마 검사")
    s2 = p.add_subparsers(dest="schema_cmd", metavar="<sub>")
    p_list = s2.add_parser("list", help="스키마 목록")
    p_list.set_defaults(func=_cmd_list)
    p_val = s2.add_parser("validate", help="JSON 파일을 스키마로 검사")
    p_val.add_argument("files", nargs="+")
    p_val.add_argument("--kind", choices=KINDS, help="생략하면 파일의 schema 칸으로 고름")
    p_val.add_argument("--manifest", action="store_true", help="항목 ID·층·해시를 매니페스트와 대조")
    p_val.set_defaults(func=_cmd_validate)
    p.set_defaults(func=lambda a: (p.print_help(), 2)[1])
