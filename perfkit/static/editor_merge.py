"""에디터 점검 결과(`audit.json`, 스키마 `perfkit/editor-audit@1`)를 L0·L1 결과에 합친다.

에디터 쪽 도구는 에디터가 있어야 아는 값만 낸다(Project Auditor 진단, AssetDatabase 질의, 프리팹·모델 내용,
Addressables Analyze). 항목마다 `merge`로 합치는 법을 정한다.

- `replace`(기본): 에디터 항목으로 바꾼다. 파이썬 쪽이 「수집 못 함」이던 항목.
- `values`: 파이썬 값에 에디터 값을 덮는다(같은 키는 에디터, 둘 다 dict면 얕게 합침). `resolves`에 적힌
  `editor_needed` 항목을 지운다. `violations`는 합집합.
- `detail`: 값은 그대로 두고 `detail.editor`에 에디터 내역(예: Auditor 코드 진단 수)만 붙인다.

결정성: 에디터 결과의 실행 시각 같은 값은 합친 결과에 넣지 않는다. 합친 항목에는 `merged_from: "editor"`.
"""

from __future__ import annotations

import copy

SCHEMA = "perfkit/editor-audit@1"


class EditorAuditError(ValueError):
    pass


def _check(audit: dict) -> None:
    if audit.get("schema") != SCHEMA:
        raise EditorAuditError(f"에디터 결과 스키마가 아님: {audit.get('schema')!r} (기대 {SCHEMA})")
    if not isinstance(audit.get("items"), dict):
        raise EditorAuditError("에디터 결과에 items가 없음")


def _resolve(entry: dict, keys: list[str]) -> None:
    need = entry.get("editor_needed")
    if not need or not keys:
        return
    left = [n for n in need if n.split(":", 1)[0].strip() not in keys]
    if left:
        entry["editor_needed"] = left
    else:
        entry.pop("editor_needed", None)


def _merge_values(dst: dict, src: dict) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            merged = dict(dst[k])
            merged.update(v)
            dst[k] = merged
        else:
            dst[k] = v


def merge(result: dict, audit: dict, *, layer: str = "L0") -> dict:
    """result(L0 또는 L1)에 에디터 결과를 합친 새 사본."""
    _check(audit)
    out = copy.deepcopy(result)
    items = out["items"]
    warnings = []
    src = audit.get("source") or {}
    rsrc = out.get("source") or {}
    if src.get("commit") and rsrc.get("commit") and src["commit"] != rsrc["commit"]:
        warnings.append(f"에디터 결과 커밋({src['commit'][:12]})이 결과 커밋({rsrc['commit'][:12]})과 다름")
    scope = (rsrc.get("scope") or {}).get("asset_dirs")
    if layer == "L0" and scope is not None and sorted(src.get("scope") or []) != sorted(scope):
        warnings.append(f"에디터 범위 {src.get('scope')} ≠ 정적 점검 에셋 범위 {scope}")

    merged_ids = []
    if layer == "L0":
        for iid, e in sorted((audit.get("items") or {}).items()):
            if iid not in items:
                continue
            e = copy.deepcopy(e)
            mode = e.pop("merge", "replace")
            resolves = e.pop("resolves", [])
            py = items[iid]
            pok = py.get("status") == "ok"
            eok = e.get("status") == "ok"
            if mode == "detail":
                if pok and e.get("detail"):
                    py.setdefault("detail", {})["editor"] = e["detail"]
                    py["merged_from"] = "editor"
                    merged_ids.append(iid)
                continue
            if mode == "values" and pok and eok:
                _merge_values(py["values"], e.get("values") or {})
                if e.get("detail"):
                    py.setdefault("detail", {})["editor"] = e["detail"]
                if e.get("violations"):
                    py["violations"] = sorted(set(py.get("violations") or []) | set(e["violations"]))
                _resolve(py, resolves)
                py["merged_from"] = "editor"
                merged_ids.append(iid)
                continue
            if not eok and not pok:
                py["reason"] = f"{py.get('reason', '')} / 에디터: {e.get('reason', '')}".strip(" /")
                continue
            if not eok:
                continue  # 파이썬 값이 있으면 그대로
            if pok and py.get("detail"):
                e.setdefault("detail", {})["perfkit"] = py["detail"]
            e["merged_from"] = "editor"
            items[iid] = e
            merged_ids.append(iid)
    else:
        for iid, e in sorted(((audit.get("extra") or {}).get("l1") or {}).items()):
            py = items.get(iid)
            if py is None or py.get("status") != "ok":
                continue
            vals = {k: v for k, v in e.items() if k != "detail"}
            py["values"].update(vals)
            if e.get("detail"):
                py.setdefault("detail", {})["editor"] = e["detail"]
            _resolve(py, list(vals))
            py["merged_from"] = "editor"
            merged_ids.append(iid)

    tool = out.setdefault("tool", {})
    ed = {"name": (audit.get("tool") or {}).get("name"), "version": (audit.get("tool") or {}).get("version"),
          "unity": (audit.get("tool") or {}).get("unity"), "commit": src.get("commit"), "scope": src.get("scope"),
          "merged_items": merged_ids, "errors": audit.get("errors") or []}
    if layer == "L0":
        ed["auditor"] = audit.get("auditor") or {}
        extra = audit.get("extra") or {}
        for k in ("samples", "build_report"):
            if k in extra:
                ed[k] = extra[k]
    tool["editor"] = ed
    if warnings:
        tool["warnings"] = sorted(set((tool.get("warnings") or []) + warnings))
    return out
