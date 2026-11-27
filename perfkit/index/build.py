"""회차·비교 색인 만들기(sqlite3 + JSONL, 표준 라이브러리).

- `runs.jsonl`: 회차 하나에 한 줄(메타 요약 + 수치 지표 평탄화 `"<ID>.<지표>": 값`). 에이전트가 sqlite 없이 읽는다.
- `comparisons.jsonl`: 비교 파일 하나에 한 줄(요약).
- `runs.sqlite`: run · metric · comparison · artifact · baseline 표. 매번 임시 파일에 새로 만들어 바꿔 끼운다.
같은 폴더 내용이면 jsonl은 같은 바이트가 나온다(정렬 고정).
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from perfkit.compare import runsets as R

RUN_COLS = ["id", "session", "run_kind", "build_hash", "apk_sha", "commit", "version", "scenario", "save", "seed",
            "arm", "round", "started_at", "ended_at", "device_class", "device_profile", "layer", "serial", "os_build",
            "valid", "invalid_reason", "probe_hash", "quality_hash", "manifest_version", "manifest_sha",
            "thermal_start", "ap_c_start", "batt_c_start", "batt_pct", "charging", "fixed_perf", "game_mode",
            "brightness", "host_busy_before", "host_busy_during", "compat_mode", "dir"]

SQL_NAME = {"commit": "commit_hash"}  # commit은 sqlite 예약어


def _col(c: str) -> str:
    return SQL_NAME.get(c, c)


SCHEMA_SQL = """
CREATE TABLE run (%s);
CREATE TABLE metric (run_id TEXT NOT NULL, item TEXT NOT NULL, key TEXT NOT NULL, value REAL, PRIMARY KEY (run_id, item, key));
CREATE TABLE artifact (run_id TEXT NOT NULL, kind TEXT NOT NULL, path TEXT NOT NULL, bytes INTEGER);
CREATE TABLE comparison (id TEXT NOT NULL, path TEXT NOT NULL, layer TEXT, device_class TEXT, method TEXT,
  base_label TEXT, cand_label TEXT, base_runs TEXT, cand_runs TEXT, valid INTEGER, item TEXT, metric TEXT,
  hl REAL, hl_pct REAL, ci_lo REAL, ci_hi REAL, cliffs_delta REAL, p REAL, stat_verdict TEXT, direction TEXT,
  outcome TEXT, created_at TEXT);
CREATE TABLE baseline (name TEXT PRIMARY KEY, path TEXT, updated_at TEXT, classes TEXT, deterministic TEXT);
CREATE INDEX metric_item ON metric(item, key);
CREATE INDEX comparison_id ON comparison(id);
""" % ", ".join(f"{_col(c)} {'TEXT PRIMARY KEY' if c == 'id' else ''}".strip() for c in RUN_COLS)

ARTIFACT_KIND = {".txt": "log", ".csv": "csv", ".json": "json", ".raw": "profile", ".html": "report"}


def _run_row(run: R.Run, results_dir: Path) -> dict:
    m = run.meta
    host = m.get("host") or {}
    row = {c: m.get(c) for c in RUN_COLS}
    row.update({"id": run.run_id, "layer": run.layer, "quality_hash": run.quality_hash(),
                "host_busy_before": host.get("cpu_busy_before"), "host_busy_during": host.get("cpu_busy_during"),
                "dir": run.dir.relative_to(results_dir).as_posix() if _under(run.dir, results_dir) else str(run.dir),
                "valid": m.get("valid", True)})
    return row


def _under(p: Path, root: Path) -> bool:
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _flat_metrics(run: R.Run) -> dict:
    out = {}
    for iid, ent in sorted((run.result.get("items") or {}).items()):
        if ent.get("status") != "ok":
            continue
        for k, v in sorted((ent.get("values") or {}).items()):
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[f"{iid}.{k}"] = v
    return out


def _sql_val(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    return v


def _comparison_files(results_dir: Path) -> list[Path]:
    d = results_dir / "comparisons"
    return sorted(d.glob("*.json")) if d.exists() else []


def _comparison_rows(path: Path, results_dir: Path):
    doc = json.loads(path.read_text(encoding="utf-8"))
    from perfkit.compare.core import parts_of

    if doc.get("schema") not in ("perfkit/comparison@1", "perfkit/comparison-set@1"):
        return None, []
    cid = path.stem
    rel = path.relative_to(results_dir).as_posix()
    rows, parts = [], parts_of(doc)
    for p in parts:
        common = {"id": cid, "path": rel, "layer": p["layer"], "device_class": p.get("device_class"),
                  "method": p["method"], "base_label": p["base"]["label"], "cand_label": p["cand"]["label"],
                  "base_runs": json.dumps(p["base"].get("run_ids", [])), "cand_runs": json.dumps(p["cand"].get("run_ids", [])),
                  "valid": int(bool(p.get("valid", True))), "created_at": p.get("created_at")}
        for iid, it in sorted(p["items"].items()):
            for k, m in sorted((it.get("metrics") or {}).items()):
                st = m.get("stat") or {}
                ci = st.get("ci") or [None, None]
                rows.append({**common, "item": iid, "metric": k, "hl": st.get("hl", m.get("delta")),
                             "hl_pct": st.get("hl_pct", m.get("delta_pct")), "ci_lo": ci[0], "ci_hi": ci[1],
                             "cliffs_delta": st.get("cliffs_delta"), "p": st.get("p"),
                             "stat_verdict": "excluded" if m.get("excluded") else st.get("outcome"),
                             "direction": st.get("direction"), "outcome": m.get("outcome")})
    summ = {"id": cid, "path": rel, "schema": doc.get("schema"), "created_at": doc.get("created_at"),
            "valid": all(p.get("valid", True) for p in parts),
            "parts": [{"layer": p["layer"], "device_class": p.get("device_class"), "method": p["method"],
                       "base": p["base"]["label"], "cand": p["cand"]["label"],
                       "counts": p["summary"].get("counts"), "stat_counts": p["summary"].get("stat_counts")}
                      for p in parts]}
    return summ, rows


def rebuild(results_dir: Path) -> dict:
    results_dir = Path(results_dir)
    run_dirs = R.list_run_dirs(R.runs_root(results_dir))
    runs, bad = [], []
    for d in run_dirs:
        try:
            runs.append(R.load_run(d))
        except R.RunsetError as exc:
            bad.append(f"{d.name}: {exc}")
    runs.sort(key=lambda r: r.run_id)

    tmp = results_dir / "runs.sqlite.tmp"
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    try:
        con.executescript(SCHEMA_SQL)
        lines = []
        for r in runs:
            row = _run_row(r, results_dir)
            con.execute(f"INSERT INTO run ({', '.join(_col(c) for c in RUN_COLS)}) VALUES ({', '.join('?' * len(RUN_COLS))})",
                        [_sql_val(row[c]) for c in RUN_COLS])
            flat = _flat_metrics(r)
            for key, v in flat.items():
                iid, k = key.split(".", 1)
                con.execute("INSERT INTO metric VALUES (?, ?, ?, ?)", (r.run_id, iid, k, float(v)))
            for f in sorted(r.dir.rglob("*")):
                if f.is_file():
                    con.execute("INSERT INTO artifact VALUES (?, ?, ?, ?)",
                                (r.run_id, ARTIFACT_KIND.get(f.suffix.lower(), "file"),
                                 f.relative_to(results_dir).as_posix() if _under(f, results_dir) else str(f), f.stat().st_size))
            lines.append(json.dumps({**{k: row[k] for k in RUN_COLS if row[k] is not None}, "metrics": flat},
                                    ensure_ascii=False, sort_keys=True))
        clines, ncrows = [], 0
        for cp in _comparison_files(results_dir):
            try:
                summ, crow = _comparison_rows(cp, results_dir)
            except (OSError, json.JSONDecodeError, KeyError) as exc:
                bad.append(f"{cp.name}: {exc}")
                continue
            if summ is None:
                continue
            clines.append(json.dumps(summ, ensure_ascii=False, sort_keys=True))
            for row in crow:
                cols = list(row)
                con.execute(f"INSERT INTO comparison ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                            [_sql_val(row[c]) for c in cols])
                ncrows += 1
        nb = 0
        bdir = results_dir / "baselines"
        for bp in sorted(bdir.glob("*.json")) if bdir.exists() else []:
            try:
                b = json.loads(bp.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if b.get("schema") != "perfkit/baseline@1":
                continue
            con.execute("INSERT OR REPLACE INTO baseline VALUES (?, ?, ?, ?, ?)",
                        (b["name"], bp.relative_to(results_dir).as_posix(), b.get("updated_at"),
                         json.dumps({c: e.get("run_ids") for c, e in (b.get("classes") or {}).items()}),
                         json.dumps(sorted(b.get("deterministic") or {}) + sorted(b.get("editor") or {}))))
            nb += 1
        con.commit()
    finally:
        con.close()
    os.replace(tmp, results_dir / "runs.sqlite")
    _write_lines(results_dir / "runs.jsonl", lines)
    _write_lines(results_dir / "comparisons.jsonl", clines)
    st = {"run_dirs": len(run_dirs), "runs": len(runs), "jsonl_lines": len(lines), "comparisons": len(clines),
          "comparison_rows": ncrows, "baselines": nb, "errors": len(bad)}
    for b in bad:
        print(f"skip: {b}")
    return st


def _write_lines(p: Path, lines: list[str]) -> None:
    p.write_text("".join(line + "\n" for line in lines), encoding="utf-8", newline="\n")


def check(results_dir: Path) -> dict:
    results_dir = Path(results_dir)
    n_dirs = len(R.list_run_dirs(R.runs_root(results_dir)))
    jp = results_dir / "runs.jsonl"
    n_lines = sum(1 for line in jp.read_text(encoding="utf-8").splitlines() if line.strip()) if jp.exists() else -1
    sp = results_dir / "runs.sqlite"
    n_sql = -1
    if sp.exists():
        con = sqlite3.connect(sp)
        try:
            n_sql = con.execute("SELECT COUNT(*) FROM run").fetchone()[0]
        finally:
            con.close()
    return {"run_dirs": n_dirs, "jsonl_lines": n_lines, "sqlite_runs": n_sql, "ok": n_dirs == n_lines == n_sql}
