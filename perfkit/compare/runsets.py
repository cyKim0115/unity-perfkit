"""회차 묶음 읽기·고르기·검사.

회차 폴더(`<결과 폴더>/runs/<run_id>/`)마다 다음 두 파일이 있어야 한다.
- `meta.json`  : run-meta 스키마(등급·관문·해시)
- `result.json`: result 스키마(L2·L3 항목 값). `run_id`가 meta와 같아야 한다.

묶음 지정(SPEC)
- `session:<이름>` : runs/ 아래 meta.session이 그 이름인 회차
- `baseline:<이름>`: 기준선 파일의 회차 목록(등급별)
- `run:<id>[,<id>…]`: 회차 ID
- 폴더 경로      : 그 폴더가 회차 폴더면 하나, 아니면 바로 아래 회차 폴더들
- 결과 파일 경로 : L0·L1(·L2) 결과 하나(결정적 비교)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from perfkit import verdict as V

HASH_FIELDS = ("probe_hash", "manifest_sha", "scenario", "quality_hash")
SOFT_FIELDS = ("save", "seed", "device_profile", "os_build")


class RunsetError(ValueError):
    """회차 묶음을 만들 수 없음(파일 없음, 형식 오류)."""


@dataclass
class Run:
    dir: Path
    meta: dict
    result: dict

    @property
    def run_id(self) -> str:
        return self.meta["run_id"]

    @property
    def started(self) -> datetime:
        return datetime.fromisoformat(self.meta["started_at"])

    @property
    def layer(self) -> str:
        return self.result.get("layer") or V.layer_for_class(self.meta["device_class"])

    def quality_hash(self):
        return self.meta.get("quality_hash") or (self.meta.get("build_config") or {}).get("quality_hash")

    def field(self, name: str):
        if name == "quality_hash":
            return self.quality_hash()
        return self.meta.get(name)


@dataclass
class Side:
    label: str
    runs: list[Run] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)       # 결정적 결과(L0·L1·L2)
    result_refs: list[str] = field(default_factory=list)
    baseline: dict | None = None
    spec: str = ""


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunsetError(f"읽기 실패 {p}: {exc}") from exc


def load_run(d: Path, *, check_schema: bool = True) -> Run:
    mp, rp = d / "meta.json", d / "result.json"
    if not mp.exists() or not rp.exists():
        raise RunsetError(f"회차 폴더에 meta.json·result.json이 없음: {d}")
    meta, res = _read_json(mp), _read_json(rp)
    if check_schema:
        from perfkit.schema import validate

        for kind, obj, p in (("run-meta", meta, mp), ("result", res, rp)):
            errs = validate(obj, kind)
            if errs:
                raise RunsetError(f"{p}: {kind} 스키마에 맞지 않음: {errs[0]}")
    if res.get("run_id") and res["run_id"] != meta["run_id"]:
        raise RunsetError(f"{d}: result.run_id {res['run_id']} ≠ meta.run_id {meta['run_id']}")
    return Run(d, meta, res)


def is_run_dir(d: Path) -> bool:
    return (d / "meta.json").exists() and (d / "result.json").exists()


def list_run_dirs(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and is_run_dir(p))


def runs_root(results_dir: Path) -> Path:
    return results_dir / "runs"


def sort_runs(runs: list[Run]) -> list[Run]:
    return sorted(runs, key=lambda r: (r.meta["started_at"], r.meta.get("round", 0), r.run_id))


def resolve(spec: str, *, results_dir: Path | None, label: str | None = None,
            baseline_class: str | None = None) -> Side:
    """SPEC 하나를 Side로."""
    side = Side(label=label or spec, spec=spec)
    if spec.startswith("session:"):
        name = spec.split(":", 1)[1]
        root = runs_root(_need(results_dir))
        side.runs = [r for r in (load_run(d) for d in list_run_dirs(root)) if r.meta.get("session") == name]
        if not side.runs:
            raise RunsetError(f"session {name}인 회차가 {root}에 없음")
    elif spec.startswith("run:"):
        root = runs_root(_need(results_dir))
        side.runs = [load_run(root / rid) for rid in spec.split(":", 1)[1].split(",") if rid]
    elif spec.startswith("baseline:"):
        from perfkit.compare.baseline import load_baseline

        bl = load_baseline(spec.split(":", 1)[1])
        side.baseline = bl
        side.label = label or f"baseline:{bl['name']}"
        root = runs_root(_need(results_dir))
        classes = bl.get("classes") or {}
        want = [baseline_class] if baseline_class else list(classes)
        for cls in want:
            c = classes.get(cls)
            if not c:
                continue
            side.runs += [load_run(root / rid) for rid in c.get("run_ids", [])]
        for layer, d in sorted((bl.get("deterministic") or {}).items()):
            p = Path(d["ref"])
            if not p.is_absolute() and results_dir is not None:
                p = results_dir / p
            side.results.append(_read_json(p))
            side.result_refs.append(str(p))
    else:
        p = Path(spec)
        if p.is_dir():
            if is_run_dir(p):
                side.runs = [load_run(p)]
            else:
                side.runs = [load_run(d) for d in list_run_dirs(p)]
                if not side.runs:
                    raise RunsetError(f"회차 폴더가 없음: {p}")
        elif p.is_file():
            doc = _read_json(p)
            if doc.get("schema") == "perfkit/result@1":
                side.results.append(doc)
                side.result_refs.append(str(p))
            elif doc.get("schema") == "perfkit/baseline@1":
                return resolve(f"baseline:{p}", results_dir=results_dir, label=label, baseline_class=baseline_class)
            else:
                raise RunsetError(f"결과·기준선 파일이 아님: {p}")
        else:
            raise RunsetError(f"묶음을 찾을 수 없음: {spec}")
    side.runs = sort_runs(side.runs)
    return side


def _need(results_dir):
    if results_dir is None:
        raise RunsetError("결과 폴더가 없음(--results 또는 설정 paths.results)")
    return results_dir


def split(runs: list[Run], how: str) -> tuple[list[Run], list[Run]]:
    """한 묶음을 둘로: arm(A·B), alternate(시간순 번갈아), half(앞·뒤 절반)."""
    rs = sort_runs(runs)
    if how == "arm":
        a = [r for r in rs if r.meta.get("arm") in ("A", None)]
        b = [r for r in rs if r.meta.get("arm") == "B"]
        if not b:
            raise RunsetError("arm B 회차가 없음(A/A면 --split alternate)")
        return a, b
    if how == "alternate":
        return rs[0::2], rs[1::2]
    if how == "half":
        k = len(rs) // 2
        return rs[:k], rs[k:2 * k] if len(rs) % 2 == 0 else rs[k + 1:]
    raise RunsetError(f"모르는 split: {how}")


# --------------------------------------------------------------------------- 관문·검사

def gate_check(run: Run, gate: dict) -> str | None:
    """프로필 관문 문턱을 다시 대 본다. 넘으면 이유, 통과면 None. 값이 없으면 그 문턱은 건너뛴다."""
    host = run.meta.get("host") or {}
    checks = [
        ("host_busy_before_max", host.get("cpu_busy_before"), "측정 전 호스트 CPU"),
        ("host_busy_during_max", host.get("cpu_busy_during"), "수집 중 호스트 CPU"),
        ("other_unity_pct_max", host.get("other_unity_pct"), "다른 Unity 프로세스 호스트 점유"),
        ("logcat_restarts_max", run.meta.get("logcat_restarts"), "logcat 재연결"),
    ]
    for key, val, label in checks:
        lim = gate.get(key)
        if lim is None or val is None:
            continue
        if float(val) > float(lim):
            return f"관문: {label} {val} > {lim}"
    return None


def usable(runs: list[Run], gate: dict | None) -> tuple[list[Run], list[dict]]:
    """관문을 통과한 회차와 뺀 회차(이유)."""
    ok, bad = [], []
    for r in runs:
        if r.meta.get("valid", True) is False:
            bad.append({"run_id": r.run_id, "reason": r.meta.get("invalid_reason") or "valid=false"})
            continue
        why = gate_check(r, gate or {})
        if why:
            bad.append({"run_id": r.run_id, "reason": why})
            continue
        ok.append(r)
    return ok, bad


def consistency(base: list[Run], cand: list[Run]) -> tuple[list[str], list[str]]:
    """(무효 사유, 경고). 측정 코드·시나리오·매니페스트·품질 설정 해시가 다르면 무효."""
    invalid, warns = [], []
    compat = any(r.meta.get("compat_mode") for r in base + cand)
    if compat:
        warns.append("호환 모드: 측정 코드가 없는 회차가 있어 측정 코드·품질 설정 해시를 맞대지 않고 두 쪽에 다 있는 지표만 비교"
                     "(프로필 [compare.\"<층>\"] compat_items가 있으면 그 항목만)")
    for f in HASH_FIELDS:
        if compat and f in ("probe_hash", "quality_hash"):
            continue
        bv = {r.field(f) for r in base}
        cv = {r.field(f) for r in cand}
        if len(bv) > 1:
            invalid.append(f"기준 묶음 안에서 {f}가 섞임: {sorted(map(str, bv))}")
        if len(cv) > 1:
            invalid.append(f"후보 묶음 안에서 {f}가 섞임: {sorted(map(str, cv))}")
        if len(bv) == 1 and len(cv) == 1 and bv != cv:
            b1, c1 = next(iter(bv)), next(iter(cv))
            if b1 is None and c1 is None:
                continue
            if b1 is None or c1 is None:
                warns.append(f"{f}가 한쪽에만 있음(기준 {b1}, 후보 {c1}): 같은지 확인 못 함")
            else:
                invalid.append(f"기준·후보의 {f}가 다름({b1} ≠ {c1})")
        if bv == {None} and cv == {None} and f == "quality_hash":
            warns.append("quality_hash 없음: 품질 설정이 같은지 확인 못 함")
    for f in SOFT_FIELDS:
        vals = {r.field(f) for r in base + cand}
        if len(vals) > 1:
            warns.append(f"{f}가 회차마다 다름: {sorted(map(str, vals))}")
    emu = {json.dumps(r.meta.get("emu"), sort_keys=True) for r in base + cand if r.meta.get("emu")}
    if len(emu) > 1:
        warns.append("에뮬레이터 설정(emu)이 회차마다 다름")
    return invalid, warns


def interleaved(base: list[Run], cand: list[Run]) -> bool:
    """두 묶음의 측정 시각이 겹치는가(ABAB 교차). 한쪽이 다른 쪽보다 모두 앞서면 False."""
    if not base or not cand:
        return False
    b0, b1 = min(r.started for r in base), max(r.started for r in base)
    c0, c1 = min(r.started for r in cand), max(r.started for r in cand)
    return not (b1 < c0 or c1 < b0)


def pair_up(base: list[Run], cand: list[Run], how: str) -> list[tuple[Run, Run]] | None:
    """쌍 만들기. how: round(라운드 번호), order(시간순 위치), none."""
    if how == "none":
        return None
    if how == "order":
        n = min(len(base), len(cand))
        return list(zip(base[:n], cand[:n])) if n else None
    def key(r):
        rnd = r.meta.get("round")
        return None if rnd is None else (r.meta.get("session") or "", rnd)

    bmap = {key(r): r for r in base}
    cmap = {key(r): r for r in cand}
    if len(bmap) != len(base) or len(cmap) != len(cand) or None in bmap or None in cmap:
        return None
    common = sorted(set(bmap) & set(cmap))
    return [(bmap[k], cmap[k]) for k in common] if common else None


# --------------------------------------------------------------------------- 값 모으기

def metric_values(runs: list[Run]) -> dict[str, dict[str, list]]:
    """{항목: {지표: [회차별 값]}}(status ok인 것만, 회차 순서 유지. 값이 없는 회차는 빠진다)."""
    out: dict[str, dict[str, list]] = {}
    for r in runs:
        for iid, ent in (r.result.get("items") or {}).items():
            if ent.get("status") != "ok":
                continue
            for k, v in (ent.get("values") or {}).items():
                out.setdefault(iid, {}).setdefault(k, []).append(v)
    return out


def paired_values(pairs: list[tuple[Run, Run]]) -> tuple[dict, dict]:
    """쌍 순서대로, 두 회차 모두 값이 있는 지표만."""
    bo: dict[str, dict[str, list]] = {}
    co: dict[str, dict[str, list]] = {}
    for b, c in pairs:
        bi, ci = b.result.get("items") or {}, c.result.get("items") or {}
        for iid in sorted(set(bi) | set(ci)):
            be, ce = bi.get(iid) or {}, ci.get(iid) or {}
            if be.get("status") != "ok" or ce.get("status") != "ok":
                continue
            for k in sorted(set(be.get("values") or {}) & set(ce.get("values") or {})):
                bo.setdefault(iid, {}).setdefault(k, []).append(be["values"][k])
                co.setdefault(iid, {}).setdefault(k, []).append(ce["values"][k])
    return bo, co
