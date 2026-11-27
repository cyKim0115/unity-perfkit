"""L0·L1(결정적 층)·L2(에디터 Play) 결과 두 개의 diff. 비교 결과는 comparison 스키마(method=diff)로 낸다.

L2는 회차 하나끼리의 값 비교다(같은 날 같은 에디터에서 잰 추세용). 잡음 등급 규칙(낮음: +5% 경고·+10% 실패)으로 판정한다.

- 항목별 판정은 `perfkit.verdict.judge_item`(매니페스트 + 프로젝트 프로필 덮어쓰기)으로 한다.
- 「새로 생긴 위반·없어진 위반」: 소스 위반은 `findings[].fingerprint`, 자산 위반은 `violations`(경로)를 집합으로 비교한다.
- `changes`: 값이 다르거나 위반 집합이 다른 항목 수. 같은 입력을 두 번 재면 0이어야 한다.
- 후보만 주면(기준 없음) 절대 기준만 판정한다.
P5의 compare가 이 인터페이스(`diff_results`)를 이어받는다.
"""

from __future__ import annotations

from datetime import datetime, timezone

from perfkit import verdict as V

ORDER = ["fail", "warn", "review", "not_collected", "pending", "improved", "pass", "record"]


def _ids(entry: dict) -> dict[str, dict]:
    """위반 식별자 → 표시용 정보."""
    out: dict[str, dict] = {}
    for f in entry.get("findings") or []:
        fp = f.get("fingerprint") or f"{f.get('rule')}|{f.get('path')}|{f.get('line')}"
        out[fp] = {k: f.get(k) for k in ("rule", "path", "line", "method", "excerpt")}
    for p in entry.get("violations") or []:
        out[f"path:{p}"] = {"path": p}
    return out


def _side(res: dict | None, label: str | None, ref: str | None) -> dict:
    if res is None:
        return {"label": label or "(없음)", "run_ids": []}
    src = res.get("source") or {}
    side = {"label": label or src.get("ref") or "?", "run_ids": []}
    for k in ("commit", "build_hash", "apk_sha"):
        if src.get(k):
            side[k] = src[k]
    if ref:
        side["result_refs"] = [ref]
    return side


def diff_results(base: dict | None, cand: dict, *, manifest=None, base_ref: str | None = None,
                 cand_ref: str | None = None, list_max: int = 50) -> dict:
    from perfkit.manifest import load_effective

    man = manifest or load_effective()
    layer = cand["layer"]
    if base is not None and base.get("layer") != layer:
        raise ValueError(f"층이 다름: 기준 {base.get('layer')} ≠ 후보 {layer}")
    if layer not in ("L0", "L1", "L2"):
        raise ValueError(f"diff는 L0·L1·L2만(기기 층은 compare): {layer}")
    warnings = []
    if base is not None:
        if base.get("manifest_sha") != cand.get("manifest_sha"):
            warnings.append("기준·후보의 manifest_sha가 다름(매니페스트가 바뀜)")
        bt, ct = (base.get("tool") or {}), (cand.get("tool") or {})
        if bt.get("rules_sha") and ct.get("rules_sha") and bt["rules_sha"] != ct["rules_sha"]:
            warnings.append("검사 규칙(rules_sha)이 다름: 값 차이에 규칙 변경이 섞임")
        if bt.get("version") != ct.get("version"):
            warnings.append(f"도구 버전이 다름({bt.get('version')} → {ct.get('version')})")

    bitems = (base or {}).get("items") or {}
    citems = cand.get("items") or {}
    items_out: dict[str, dict] = {}
    verdicts = []
    changes = 0
    new_total = removed_total = 0
    for iid in sorted(set(bitems) | set(citems)):
        item = man.get(iid)
        if item is None or layer not in item.get("by_layer", {}):
            continue
        be, ce = bitems.get(iid), citems.get(iid)
        bok = be is not None and be.get("status") == "ok"
        cok = ce is not None and ce.get("status") == "ok"
        if ce is not None and ce.get("status") == "not_applicable" and (be is None or be.get("status") == "not_applicable"):
            iv = V.ItemVerdict(id=iid, layer=layer, outcome="record", reasons=[ce.get("reason", "해당 없음")])
        elif not cok:
            iv = V.ItemVerdict(id=iid, layer=layer, outcome="not_collected",
                               reasons=[(ce or {}).get("reason", "후보 결과에 없음")])
        else:
            bvals = be["values"] if (base is not None and bok) else None
            iv = V.judge_item(item, layer, bvals, ce["values"])
            if base is not None and not bok:
                iv.outcome = V.worst([iv.outcome, "pending"]) if iv.outcome == "pass" else iv.outcome
                iv.reasons.append("기준 값 없음: " + ((be or {}).get("reason") or "기준 결과에 없음"))
        d = iv.to_dict()
        if base is not None:
            bids = _ids(be or {}) if bok else {}
            cids = _ids(ce or {}) if cok else {}
            new = sorted(set(cids) - set(bids), key=lambda k: (cids[k].get("path") or "", cids[k].get("line") or 0, k))
            gone = sorted(set(bids) - set(cids), key=lambda k: (bids[k].get("path") or "", bids[k].get("line") or 0, k))
            changed_vals = bok != cok or (bok and cok and be["values"] != ce["values"])
            if new or gone or changed_vals:
                changes += 1
            if new or gone:
                d["violations"] = {"new_count": len(new), "removed_count": len(gone),
                                   "new": [cids[k] for k in new[:list_max]],
                                   "removed": [bids[k] for k in gone[:list_max]]}
                new_total += len(new)
                removed_total += len(gone)
            d["changed"] = bool(new or gone or changed_vals)
        items_out[iid] = d
        verdicts.append(iv)

    summary = V.summarize(verdicts)
    summary["changes"] = changes
    summary["new_violations"] = new_total
    summary["removed_violations"] = removed_total
    if warnings:
        summary["warnings"] = warnings
    out = {"schema": "perfkit/comparison@1", "layer": layer,
           "manifest_version": man.version, "manifest_sha": cand.get("manifest_sha") or man.sha256,
           "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "method": "diff", "base": _side(base, None, base_ref), "cand": _side(cand, None, cand_ref),
           "items": items_out, "summary": summary}
    for k in ("profile_name", "profile_sha"):
        if cand.get(k):
            out[k] = cand[k]
    return out


def summary_lines(cmp: dict, top: int = 15) -> list[str]:
    s = cmp["summary"]
    counts = " ".join(f"{k}={s['counts'][k]}" for k in ORDER if k in s["counts"])
    lines = [f"layer={cmp['layer']} changes={s.get('changes', '-')} new_violations={s.get('new_violations', 0)} "
             f"removed_violations={s.get('removed_violations', 0)} {counts}"]
    for w in s.get("warnings", []):
        lines.append(f"warn: {w}")
    rows = []
    for iid, d in cmp["items"].items():
        if d["outcome"] in ("pass", "record") and not d.get("changed"):
            continue
        parts = []
        for k, m in (d.get("metrics") or {}).items():
            if m.get("delta") not in (None, 0) or m["outcome"] not in ("pass", "record"):
                dv = f"{m.get('base')!r}→{m.get('cand')!r}" if not isinstance(m.get("cand"), (list, dict)) else "바뀜"
                parts.append(f"{k}:{m['outcome']}({dv})")
        v = d.get("violations")
        if v:
            parts.append(f"+{v['new_count']}/-{v['removed_count']}")
        rows.append((ORDER.index(d["outcome"]) if d["outcome"] in ORDER else 99, iid, d["outcome"], " ".join(parts)))
    for _, iid, out, txt in sorted(rows)[:top]:
        lines.append(f"  {iid} {out} {txt}"[:220])
    return lines
