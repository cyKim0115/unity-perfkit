"""`perfkit static`: Unity 프로젝트를 읽기만 해서 L0 결과(result 스키마)를 만든다."""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import perfkit
from perfkit.diff import resultio
from perfkit.static import assets as A
from perfkit.static import project as PR
from perfkit.static import settings as ST
from perfkit.static import source_scan as S

AUDITOR_CODE_ITEMS = ("SCR-02", "SCR-03", "SCR-04", "SCR-06", "SCR-07")


# --------------------------------------------------------------------------- 설정

def static_config(project_arg: str | None = None) -> dict:
    """프로필 [static]에 사용자 설정 [static]을 덮은 값. 경로는 그 파일 폴더 기준으로 푼다."""
    from perfkit.config import settings

    s = settings()
    cfg: dict = {}
    for src, path in ((s.profile, s.profile_path), (s.user, s.user_path)):
        sec = (src or {}).get("static") or {}
        for k, v in sec.items():
            if k == "project" and v and path is not None and not Path(str(v)).is_absolute():
                v = str((path.parent / str(v)).resolve())
            cfg[k] = v
    env = s.env.get("PERFKIT_STATIC_PROJECT")
    if env:
        cfg["project"] = env
    if project_arg:
        cfg["project"] = project_arg
    cfg["code_rules"] = list((s.profile or {}).get("code_rules") or [])
    return cfg


def options_from(cfg: dict) -> tuple[S.ScanOptions, A.ImportOptions, PR.Scope, PR.Scope]:
    defines = dict(S.DEFAULT_DEFINES)
    for k, v in (cfg.get("defines") or {}).items():
        defines[str(k)] = None if v is None or v == "unknown" else bool(v)
    defines = {k: v for k, v in defines.items() if v is not None}
    so = S.ScanOptions(defines=defines, code_rules=cfg.get("code_rules") or [])
    imp = cfg.get("import") or {}
    io = A.ImportOptions(**{k: imp[k] for k in ("texture_size_limit", "streaming_min_size", "long_clip_seconds",
                                                 "short_clip_seconds", "platform", "top_n") if k in imp})
    exclude = list(cfg.get("exclude") or [])
    src_scope = PR.Scope(list(cfg.get("source_dirs") or ["Assets"]), exclude)
    asset_scope = PR.Scope(list(cfg.get("asset_dirs") or ["Assets"]), exclude)
    return so, io, src_scope, asset_scope


# --------------------------------------------------------------------------- 실행

def run(project_dir: str | Path, cfg: dict) -> tuple[dict, dict]:
    """(result, run_meta)."""
    t0 = time.time()
    so, io, src_scope, asset_scope = options_from(cfg)
    proj = PR.load(project_dir)
    git = PR.git_info(proj.root)
    timings = {}

    # ---- 소스
    t = time.time()
    editor_dirs = [d.relative_to(proj.root).as_posix() for d in
                   S.editor_only_dirs(proj.root, [proj.abs(f) for f in proj.files if f.endswith(".asmdef")])]
    scans: list[S.FileScan] = []
    all_scans_for_pp: list[str] = []
    n_editor = 0
    for rel in proj.files:
        if not rel.endswith(".cs"):
            continue
        editor_only = S.is_editor_path(rel, editor_dirs)
        in_src = src_scope.contains(rel)
        if not in_src and not editor_only:
            continue
        if not in_src and editor_only and not asset_scope.contains(rel):
            continue
        text = proj.read_text(rel)
        fs = S.scan_text(rel, text, so, editor_only=editor_only or not in_src)
        if editor_only:
            n_editor += 1
        all_scans_for_pp += fs.postprocessors
        if in_src:
            scans.append(fs)
    timings["source"] = round(time.time() - t, 2)

    findings = [f for fs in scans if not fs.editor_only for f in fs.findings]
    empty = [f for fs in scans if not fs.editor_only for f in fs.empty_events]
    ongui = [f for fs in scans if not fs.editor_only for f in fs.on_gui]
    logs = [f for fs in scans if not fs.editor_only for f in fs.debug_logs]
    hits = [f for fs in scans if not fs.editor_only for f in fs.code_rule_hits]
    S.fingerprint_all(findings + empty + ongui + logs + hits)
    excluded = Counter()
    for fs in scans:
        excluded.update(fs.debug_logs_excluded)
    assigns: dict[str, list] = {}
    passes = []
    for fs in scans:
        for k, v in fs.assignments.items():
            assigns.setdefault(k, []).extend(v)
        passes += fs.render_passes

    items: dict[str, dict] = {}

    def fdicts(xs):
        return [x.to_dict() for x in sorted(xs, key=lambda f: (f.path, f.line, f.col, f.rule))]

    by_item: dict[str, list] = {}
    for f in findings + empty + ongui:
        by_item.setdefault(f.item, []).append(f)
    for iid in ("SCR-02", "SCR-03", "SCR-04", "SCR-07"):
        xs = by_item.get(iid, [])
        items[iid] = {"status": "ok", "collector": "static.auditor_code", "values": {"value": len(xs)},
                      "detail": {"by_rule": dict(sorted(Counter(x.rule for x in xs).items())),
                                 "hot_path": dict(sorted(Counter(x.rule for x in xs if x.hot).items())),
                                 "method": "perfkit-source-scan"},
                      "findings": fdicts(xs)}
    items["SCR-06"] = {"status": "ok", "collector": "static.auditor_code", "values": {"value": len(empty)},
                       "detail": {"method": "perfkit-source-scan", "events": sorted(S.EMPTY_EVENT_METHODS)},
                       "findings": fdicts(empty)}
    auditor = [f for iid in AUDITOR_CODE_ITEMS for f in by_item.get(iid, [])]
    items["SCR-01"] = {"status": "ok", "collector": "static.auditor_code",
                       "values": {"issues_total": len(auditor), "major": sum(1 for f in auditor if f.hot)},
                       "detail": {"by_item": {iid: len(by_item.get(iid, [])) for iid in AUDITOR_CODE_ITEMS},
                                  "method": "perfkit-source-scan",
                                  "rule": "SCR-02·03·04·06·07 위반 합계. 매 프레임 메서드 안이면 Major(호출 그래프는 따라가지 않음)"}}
    items["SCR-05"] = {"status": "ok", "collector": "static.source_scan",
                       "values": {"debug_log_calls": len(logs), "debug_log_in_update": sum(1 for f in logs if f.hot)},
                       "detail": {"excluded": dict(sorted(excluded.items())),
                                  "rule": "UnityEngine.Debug.Log* 호출(Assert 제외). 에디터 폴더·에디터 전용 asmdef·"
                                          "꺼진 전처리 구간·[Conditional] 메서드 안은 뺌. 다른 클래스의 Debug.Log(X.Debug.Log)는 따로 셈"},
                       "findings": fdicts(logs)}
    if so.code_rules:
        items["SCR-08"] = {"status": "ok", "collector": "static.source_scan",
                           "values": {"violations": len(hits),
                                      "by_rule": {r["id"]: sum(1 for h in hits if h.rule == r["id"]) for r in
                                                  sorted(so.code_rules, key=lambda r: r["id"])}},
                           "detail": {"rules": [{k: r.get(k) for k in ("id", "pattern", "note")} for r in
                                                sorted(so.code_rules, key=lambda r: r["id"])]},
                           "findings": fdicts(hits)}
    else:
        items["SCR-08"] = {"status": "not_collected", "reason": "프로필에 code_rules가 없음",
                           "collector": "static.source_scan"}

    # ---- 설정·에셋
    t = time.time()
    st = ST.Settings(proj, io.platform, asset_scope)
    comp = st.default_texture_compression()
    t2 = time.time()
    scan_ = A.scan(proj, asset_scope, io, default_compression=comp[0] if comp else "ASTC")
    timings["assets"] = round(time.time() - t2, 2)
    items.update(A.items(scan_, io, postprocessors=sorted(set(all_scans_for_pp)),
                         default_presets=st.default_presets(), streaming_settings=st.streaming_settings()))
    src = {**assigns, "render_passes": passes, "materials": scan_.materials,
           "instancing_materials": sum(1 for m in scan_.materials if m.get("instancing"))}
    items.update(ST.items(st, src))
    items["SCR-09"] = ST.settings_rules(st, items)
    timings["settings"] = round(time.time() - t - timings["assets"], 2)

    # ---- 매니페스트 대조: L0 항목만, 빠진 L0 항목은 수집 못 함
    from perfkit.manifest import load_effective
    man = load_effective()
    l0 = {it["id"]: it for it in man.items_for(layer="L0")}
    for iid in list(items):
        if iid not in l0:
            items.pop(iid)
    for iid, it in l0.items():
        if iid not in items:
            items[iid] = {"status": "not_collected", "reason": "이 도구가 아직 다루지 않음",
                          "collector": it["by_layer"]["L0"]["collector"]}

    commit = git.get("commit")
    if git.get("commit_time"):
        collected = datetime.fromisoformat(git["commit_time"]).astimezone(timezone.utc).isoformat(timespec="seconds")
    else:
        newest = max((proj.abs(f).stat().st_mtime for f in proj.files[:5000]), default=0)
        collected = datetime.fromtimestamp(newest, timezone.utc).isoformat(timespec="seconds")
    source = {"kind": "static", "ref": f"project@{commit[:12]}" if commit else f"project:{proj.root.name}",
              "scope": {"source_dirs": src_scope.include, "asset_dirs": asset_scope.include,
                        "exclude": src_scope.exclude, "platform": io.platform}}
    if commit:
        source.update({"commit": commit, "dirty": git["dirty"], "dirty_count": git["dirty_count"]})
    rules_sha = hashlib.sha256(json.dumps(
        [[r.id, r.item, r.pattern.pattern, r.hot_only, r.needs_using, r.skip_assign] for r in S.RULES]
        + [sorted(so.defines.items()), so.code_rules, vars(io)], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    result = {"schema": "perfkit/result@1", "layer": "L0", "collected_at": collected, "source": source,
              "tool": {"name": "perfkit static", "version": perfkit.__version__, "rules_sha": rules_sha,
                       "counts": {"cs_files": len(scans), "editor_cs_files": n_editor, "textures": len(scan_.textures),
                                  "models": len(scan_.models), "audio": len(scan_.audio), "materials": len(scan_.materials),
                                  "fonts": len(scan_.fonts)},
                       "errors": sorted(scan_.errors + st.notes)},
              "items": dict(sorted(items.items()))}
    resultio.manifest_stamp(result)
    run_ = resultio.run_meta(t0, time.time(), project=str(proj.root), timings=timings,
                             command="perfkit static")
    return result, run_


def default_out(result: dict) -> Path:
    from perfkit import paths

    date = result["collected_at"][:10].replace("-", "")
    commit = (result["source"].get("commit") or "nocommit")[:8]
    tag = f"{date}-{commit}" + ("-dirty" if result["source"].get("dirty") else "")
    return paths.results_dir() / "static" / tag / "source.json"


def summary_lines(result: dict, top: int = 5) -> list[str]:
    it = result["items"]
    ok = [k for k, v in it.items() if v["status"] == "ok"]
    nc = [k for k, v in it.items() if v["status"] != "ok"]
    lines = [f"layer=L0 items={len(it)} ok={len(ok)} not_collected={len(nc)} "
             f"commit={(result['source'].get('commit') or '-')[:12]} dirty={result['source'].get('dirty')}"]
    counts = []
    for k in ok:
        v = it[k]["values"]
        n = v.get("value", v.get("violations", v.get("issues_total", v.get("debug_log_calls"))))
        if isinstance(n, int) and not isinstance(n, bool):
            counts.append((n, k))
    counts.sort(key=lambda x: (-x[0], x[1]))
    lines.append("top: " + ", ".join(f"{k}={n}" for n, k in counts[:top]))
    if nc:
        lines.append("not_collected: " + ", ".join(nc))
    return lines
