"""매니페스트(manifest/checks.json) 읽기·검증·해시.

    from perfkit.manifest import load
    m = load()
    m.version, m.sha256          # 회차 메타·결과에 그대로 남긴다
    m.get("FRM-01")              # 항목 dict
    m.items_for(layer="L3-E", set_="basic")

해시는 키를 정렬하고 공백 없이 직렬화한 JSON(UTF-8)의 sha256이다. 파일의 줄바꿈·들여쓰기와
무관하므로 CRLF 변환에도 값이 같다. 내용을 바꾸면 manifest_version을 올리고 CHANGES.md에 적는다.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from perfkit import collectors
from perfkit.paths import manifest_path

EXPECTED_TOTAL = 123
EXPECTED_AREAS = {"FRM": 5, "CPU": 15, "SCR": 9, "GPU": 25, "MEM": 18, "AST": 20, "BLD": 13, "THM": 7, "STB": 11}
EXPECTED_SETS = {"basic": 73, "deep": 50}
LAYERS = ("L0", "L1", "L2", "L3-E", "L3-D", "console")
NO_ABSOLUTE_LAYERS = ("L2", "L3-E")  # L2는 추세용, L3-E는 기준 대비 변화만
EMULATOR_JUDGES = ("change_only", "record")


def canonical_bytes(doc) -> bytes:
    return json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_of(doc) -> str:
    return hashlib.sha256(canonical_bytes(doc)).hexdigest()


@dataclass
class Manifest:
    doc: dict
    path: Path | None = None

    @property
    def version(self) -> str:
        return self.doc["manifest_version"]

    @property
    def sha256(self) -> str:
        return sha256_of(self.doc)

    @property
    def checks(self) -> list[dict]:
        return self.doc["checks"]

    @property
    def aux(self) -> list[dict]:
        return self.doc.get("aux", [])

    def all_items(self) -> list[dict]:
        return self.checks + self.aux

    def get(self, item_id: str) -> dict | None:
        for it in self.all_items():
            if it["id"] == item_id:
                return it
        return None

    def items_for(self, layer: str | None = None, set_: str | None = None, area: str | None = None,
                  include_aux: bool = True) -> list[dict]:
        pool = self.all_items() if include_aux else self.checks
        out = []
        for it in pool:
            if layer and layer not in it["layers"]:
                continue
            if set_ == "basic" and it["set"] != "basic":
                continue
            if set_ == "deep" and not (it["set"] == "deep" or it["deep_variant"]):
                continue
            if area and it["area"] != area:
                continue
            out.append(it)
        return out

    def metrics_of(self, item: dict) -> list[dict]:
        """지표 목록. 없으면 항목 기본값으로 지표 하나(value)."""
        base = {"unit": item["unit"], "better": item["better"], "kind": item["kind"]}
        ms = item.get("metrics") or [{"key": "value"}]
        return [{**base, **m} for m in ms]


def load(path: str | Path | None = None) -> Manifest:
    """저장소의 공개 매니페스트(프로젝트 프로필을 덮지 않은 원본)."""
    p = Path(path) if path else manifest_path()
    return Manifest(json.loads(p.read_text(encoding="utf-8")), p)


# --------------------------------------------------------------------------- 프로젝트 프로필 덮어쓰기

OVERRIDE_LAYER_KEYS = ("noise", "judge", "noise_hint", "note", "collector", "build_config")


def apply_profile(doc: dict, profile: dict) -> dict:
    """프로젝트 프로필의 [overrides."<ID>"]를 덮은 새 문서를 돌려준다(원본은 그대로).

    지원하는 칸
      drop_layers = ["L3-E"]                       그 층에서 재지 않음(예: 에뮬레이터에서 못 잼)
      note = "..."                                 항목에 project_note로 붙임
      [overrides."<ID>".layers."<층>"]             noise·judge·noise_hint·note·collector·build_config
      [[overrides."<ID>".absolute]]                절대 기준 규칙 목록을 통째로 바꿈(근거 basis 필수)
    [notes] "<ID>" = "..." 도 project_note로 붙는다.
    """
    out = json.loads(json.dumps(doc))
    items = {it["id"]: it for it in out["checks"] + out.get("aux", [])}
    for iid, note in (profile.get("notes") or {}).items():
        if iid not in items:
            raise ValueError(f"프로필 notes에 없는 항목: {iid}")
        items[iid]["project_note"] = str(note)
    for iid, ov in (profile.get("overrides") or {}).items():
        it = items.get(iid)
        if it is None:
            raise ValueError(f"프로필 overrides에 없는 항목: {iid}")
        unknown = set(ov) - {"drop_layers", "note", "layers", "absolute"}
        if unknown:
            raise ValueError(f"{iid}: 모르는 덮어쓰기 칸 {sorted(unknown)}")
        for layer in ov.get("drop_layers", []):
            if layer in it["layers"]:
                it["layers"] = [l for l in it["layers"] if l != layer]
                it["by_layer"].pop(layer, None)
        for layer, lov in (ov.get("layers") or {}).items():
            if layer not in it["by_layer"]:
                raise ValueError(f"{iid}: 덮을 층 {layer}이 항목에 없음")
            bad = set(lov) - set(OVERRIDE_LAYER_KEYS)
            if bad:
                raise ValueError(f"{iid}.{layer}: 모르는 칸 {sorted(bad)}")
            it["by_layer"][layer].update(lov)
        if "absolute" in ov:
            it["criteria"]["absolute"] = [dict(r, sources=list(r.get("sources", []))) for r in ov["absolute"]]
            if "프로젝트가 정함" not in it["criteria"]["basis"]:
                it["criteria"]["basis"] = [b for b in it["criteria"]["basis"] if b != "없음"] + ["프로젝트가 정함"]
        if "note" in ov:
            it["project_note"] = str(ov["note"])
    return out


def load_effective(settings=None) -> Manifest:
    """공개 매니페스트에 설정된 프로젝트 프로필을 덮은 것. 프로필이 없으면 원본과 같다."""
    from perfkit.config import settings as get_settings

    s = settings or get_settings()
    m = load()
    if not s.profile:
        return m
    return Manifest(apply_profile(m.doc, s.profile), m.path)


# --------------------------------------------------------------------------- 검사

def check(doc: dict) -> tuple[list[str], list[str]]:
    """(오류, 경고). 오류가 하나라도 있으면 매니페스트를 쓰면 안 된다."""
    from perfkit.schema import validate

    errors = [f"schema: {e}" for e in validate(doc, "manifest")]
    warnings: list[str] = []
    if errors:
        return errors, warnings

    checks, aux = doc["checks"], doc["aux"]
    ids = [c["id"] for c in checks + aux]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        errors.append(f"ID 중복: {dup}")
    if len(checks) != EXPECTED_TOTAL:
        errors.append(f"항목 수 {len(checks)} ≠ {EXPECTED_TOTAL}")
    for area, n in EXPECTED_AREAS.items():
        got = sum(1 for c in checks if c["area"] == area)
        if got != n:
            errors.append(f"영역 {area} 항목 수 {got} ≠ {n}")
        if doc["areas"].get(area, {}).get("count") != n:
            errors.append(f"areas.{area}.count가 {n}이 아님")
    extra_areas = {c["area"] for c in checks} - set(EXPECTED_AREAS)
    if extra_areas:
        errors.append(f"123항목에 모르는 영역: {sorted(extra_areas)}")
    for st, n in EXPECTED_SETS.items():
        got = sum(1 for c in checks if c["set"] == st)
        if got != n:
            errors.append(f"세트 {st} 수 {got} ≠ {n}")
    for a in aux:
        if a["area"] != "AUX" or not a["id"].startswith("AUX-"):
            errors.append(f"aux에 AUX가 아닌 항목: {a['id']}")
    for c in checks:
        if not c["id"].startswith(c["area"] + "-"):
            errors.append(f"{c['id']}: area {c['area']}와 ID 접두어가 다름")

    forbidden = set(doc["emulator_forbidden_tags"])
    sources = doc["sources"]
    for it in checks + aux:
        iid = it["id"]
        layers = it["layers"]
        if set(it["by_layer"]) != set(layers):
            errors.append(f"{iid}: by_layer 칸 {sorted(it['by_layer'])} ≠ layers {layers}")
        if list(layers) != [l for l in LAYERS if l in layers]:
            errors.append(f"{iid}: layers 순서가 {LAYERS} 순이 아님")
        if "L3-E" in layers and "L3-D" not in layers:
            errors.append(f"{iid}: L3-E만 있고 L3-D가 없음")
        if it["deep_variant"] and it["set"] != "basic":
            errors.append(f"{iid}: deep_variant는 basic 항목에만")
        keys = [m["key"] for m in it.get("metrics", [])]
        if len(keys) != len(set(keys)):
            errors.append(f"{iid}: 지표 키 중복")
        metric_keys = set(keys) or {"value"}
        for layer, spec in it["by_layer"].items():
            col = spec["collector"]
            if collectors.package_of(col) not in collectors.LAYER_PACKAGES[layer]:
                errors.append(f"{iid}: {layer} 수집기 {col}의 패키지가 이 층에 맞지 않음")
            if layer == "L3-E":
                if spec["judge"] not in EMULATOR_JUDGES:
                    errors.append(f"{iid}: L3-E 판정이 {spec['judge']}(에뮬레이터는 절대 기준 금지)")
                bad = forbidden & set(it["tags"])
                if bad:
                    errors.append(f"{iid}: 에뮬레이터 금지 지표({sorted(bad)})인데 L3-E에 있음")
            if layer in NO_ABSOLUTE_LAYERS and spec["judge"] in ("absolute+change", "absolute_only"):
                errors.append(f"{iid}: {layer}에는 절대 기준을 쓰지 않음")
            if spec["judge"] in ("absolute+change", "absolute_only") and not it["criteria"]["absolute"]:
                errors.append(f"{iid}: {layer} 판정이 {spec['judge']}인데 절대 기준이 없음")
            if spec["noise"] == "pending_aa":
                if layer != "L3-E":
                    errors.append(f"{iid}: pending_aa는 L3-E에만")
        for r in it["criteria"]["absolute"]:
            if r["metric"] not in metric_keys:
                errors.append(f"{iid}: 절대 기준 지표 {r['metric']}가 metrics에 없음")
            for l in r.get("layers", []):
                if l in NO_ABSOLUTE_LAYERS or l not in layers:
                    errors.append(f"{iid}: 절대 기준 층 {l}이 허용되지 않음")
            if r["basis"] == "공식" and not r["sources"]:
                errors.append(f"{iid}: 공식 기준인데 출처가 없음")
        for s in it["criteria"]["sources"]:
            if s not in sources:
                errors.append(f"{iid}: 출처 {s}가 sources에 없음")
        for t in ("title", "set_basis"):
            if not str(it.get(t, "")).strip():
                errors.append(f"{iid}: {t} 비어 있음")
        if not re.match(r"^[A-Z]{3}-\d{2}$", iid):
            errors.append(f"{iid}: ID 형식")

    pending = sum(1 for it in checks + aux if it["by_layer"].get("L3-E", {}).get("noise") == "pending_aa")
    if pending:
        warnings.append(f"L3-E 잡음 등급 pending_aa {pending}건(에뮬레이터 A/A 잡음 바닥 대기, 프로젝트 프로필에서 채움)")
    unknown = sum(1 for it in checks + aux for s in it["by_layer"].values() if s["noise"] == "unknown")
    if unknown:
        warnings.append(f"잡음 근거 없음(unknown, high처럼 다룸) {unknown}칸")
    return errors, warnings


def summary_line(doc: dict) -> str:
    checks = doc["checks"]
    parts = [f"checks={len(checks)}", f"areas={len({c['area'] for c in checks})}"]
    parts += [f"{a}={sum(1 for c in checks if c['area'] == a)}" for a in EXPECTED_AREAS]
    parts += [f"basic={sum(1 for c in checks if c['set'] == 'basic')}",
              f"deep={sum(1 for c in checks if c['set'] == 'deep')}"]
    parts += [f"sha256={sha256_of(doc)}", f"aux={len(doc.get('aux', []))}", f"version={doc.get('manifest_version')}"]
    return " ".join(parts)


# --------------------------------------------------------------------------- CLI

def _load_arg(args) -> Manifest:
    m = load(args.manifest_file)
    if getattr(args, "profile", False):
        from perfkit.config import settings

        s = settings()
        if not s.profile:
            raise SystemExit("error: 프로젝트 프로필이 설정되지 않음(python -m perfkit config show)")
        m = Manifest(apply_profile(m.doc, s.profile), m.path)
    return m


def _cmd_check(args) -> int:
    try:
        m = _load_arg(args)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL 매니페스트 읽기 실패: {exc}")
        return 1
    errors, warnings = check(m.doc)
    for e in errors:
        print(f"error: {e}")
    for w in warnings:
        print(f"warn: {w}", file=sys.stderr)
    if errors:
        print(f"FAIL {len(errors)}건")
        return 1
    print(summary_line(m.doc))
    return 0


def _cmd_hash(args) -> int:
    m = _load_arg(args)
    print(f"version={m.version} sha256={m.sha256}")
    return 0


def _cmd_list(args) -> int:
    m = _load_arg(args)
    items = m.items_for(layer=args.layer, set_=args.set, area=args.area, include_aux=not args.no_aux)
    for it in items:
        cols = ",".join(sorted({s["collector"] for s in it["by_layer"].values()}))
        mark = "basic+deep" if it["deep_variant"] else it["set"]
        print(f"{it['id']}\t{mark}\t{'/'.join(it['layers'])}\t{cols}\t{it['title']}")
    print(f"# {len(items)}개", file=sys.stderr)
    return 0


def _cmd_show(args) -> int:
    m = _load_arg(args)
    it = m.get(args.id)
    if it is None:
        print(f"없는 항목: {args.id}")
        return 1
    print(json.dumps(it, ensure_ascii=False, indent=2))
    return 0


def collector_table(m: Manifest) -> list[dict]:
    reg = collectors.registered()
    table: dict[str, dict] = {}
    for it in m.all_items():
        for layer, spec in it["by_layer"].items():
            row = table.setdefault(spec["collector"], {"items": [], "layers": set()})
            if it["id"] not in row["items"]:
                row["items"].append(it["id"])
            row["layers"].add(layer)
    out = []
    for name in sorted(table):
        row = table[name]
        out.append({
            "name": name,
            "owner": collectors.owner_of(name),
            "layers": [l for l in LAYERS if l in row["layers"]],
            "items": row["items"],
            "implemented": name in reg,
        })
    return out


def _cmd_collectors(args) -> int:
    rows = collector_table(_load_arg(args))
    if args.markdown:
        print("| 수집기 | 구현 패키지 | 층 | 항목 수 | 항목 |")
        print("|---|---|---|---|---|")
        for r in rows:
            print(f"| `{r['name']}` | {r['owner']} | {'·'.join(r['layers'])} | {len(r['items'])} | {', '.join(r['items'])} |")
    else:
        for r in rows:
            flag = "구현" if r["implemented"] else "미구현"
            print(f"{r['name']:28} {r['owner']:6} {flag:4} {'/'.join(r['layers']):18} {len(r['items']):3}  {' '.join(r['items'])}")
    print(f"# 수집기 {len(rows)}개, 구현 {sum(r['implemented'] for r in rows)}개", file=sys.stderr)
    return 0


def register_cli(sub) -> None:
    p = sub.add_parser("manifest", help="점검 항목 매니페스트 검사·해시·목록")
    p.add_argument("--file", dest="manifest_file", default=None, help="매니페스트 경로(기본 manifest/checks.json)")
    p.add_argument("--profile", action="store_true", help="설정된 프로젝트 프로필의 덮어쓰기를 적용한 결과로")
    s2 = p.add_subparsers(dest="manifest_cmd", metavar="<sub>")
    c = s2.add_parser("check", help="항목 수·ID·필수 칸·등급 규칙 검사")
    c.set_defaults(func=_cmd_check)
    h = s2.add_parser("hash", help="버전과 내용 해시(sha256)")
    h.set_defaults(func=_cmd_hash)
    ls = s2.add_parser("list", help="항목 목록")
    ls.add_argument("--layer", choices=LAYERS)
    ls.add_argument("--set", choices=("basic", "deep"))
    ls.add_argument("--area")
    ls.add_argument("--no-aux", action="store_true")
    ls.set_defaults(func=_cmd_list)
    sh = s2.add_parser("show", help="항목 하나를 JSON으로")
    sh.add_argument("id")
    sh.set_defaults(func=_cmd_show)
    co = s2.add_parser("collectors", help="수집기 이름별 담당·층·항목")
    co.add_argument("--markdown", action="store_true")
    co.set_defaults(func=_cmd_collectors)
    p.set_defaults(func=lambda a: (p.print_help(), 2)[1])
