"""기준선(baseline)과 A/A 잡음 바닥.

기준선 파일 `perfkit/baseline@1`은 등급(device_class)마다 따로 둔다. 같은 이름에 다른 등급을 더할 수 있고,
같은 등급을 바꾸려면 --force.

    {"schema": "perfkit/baseline@1", "name", "created_at", "manifest_version", "manifest_sha",
     "profile_name", "profile_sha",
     "classes": {"emulator": {"layer": "L3-E", "run_ids", "n_runs", "n_valid", "invalid_runs",
                              "probe_hash", "quality_hash", "scenario", "save", "env",
                              "metrics": {"<ID>.<지표>": {n, median, min, max}},
                              "noise_floor": {"<ID>.<지표>": {cv_pct, sigma_d_pct, mde_pct, …}} | null,
                              "noise_source": {...}}},
     "deterministic": {"L0": {"ref", "sha256"}, "L1": {...}},
     "editor": {"L2": {"ref", "sha256", "runs", "commit", "probe_hash", "scenario", "save", "dirty", "tree"}}}

L2(에디터 Play)는 결정적 층이 아니라 같은 날 같은 에디터 추세용이라 `deterministic`과 따로 둔다.
기준선을 비교 한쪽(`baseline:<이름>`)으로 쓸 때 L2는 읽지 않는다. `perfkit diff <ref> <후보 L2>`로 비교한다.

저장 위치: 프로젝트 프로필 `[baseline] dir`(프로필 폴더 기준 상대 경로) → 없으면 `<결과 폴더>/baselines`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from perfkit import verdict as V
from perfkit.compare import runsets as R
from perfkit.compare import stats as S

SCHEMA = "perfkit/baseline@1"
NOISE_SCHEMA = "perfkit/noise@1"


def baselines_dir(settings=None) -> Path:
    from perfkit.config import ConfigError, settings as get_settings
    from perfkit.paths import results_dir

    s = settings or get_settings()
    d = (s.profile.get("baseline") or {}).get("dir") if s.profile else None
    if d:
        p = Path(d)
        if not p.is_absolute() and s.profile_path is not None:
            p = (s.profile_path.parent / p).resolve()
        return p
    try:
        return results_dir() / "baselines"
    except ConfigError as exc:
        raise ConfigError(f"기준선 위치를 모름: 프로필 [baseline] dir 또는 paths.results가 필요 ({exc})") from None


def _path_of(name_or_path: str) -> Path:
    p = Path(name_or_path)
    if p.suffix == ".json" and p.exists():
        return p
    return baselines_dir() / f"{name_or_path}.json"


def load_baseline(name_or_path: str) -> dict:
    p = _path_of(name_or_path)
    if not p.exists():
        raise R.RunsetError(f"기준선이 없음: {p}")
    doc = json.loads(p.read_text(encoding="utf-8"))
    if doc.get("schema") != SCHEMA:
        raise R.RunsetError(f"기준선 파일이 아님: {p}")
    return doc


def summarize_metrics(runs: list[R.Run]) -> dict:
    out = {}
    for iid, ms in sorted(R.metric_values(runs).items()):
        for k, vals in sorted(ms.items()):
            nums = [float(v) for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
            if len(nums) != len(vals) or not nums:
                continue
            out[f"{iid}.{k}"] = {"n": len(nums), "median": S.median(nums), "min": min(nums), "max": max(nums)}
    return out


def noise_from_runs(runs: list[R.Run]) -> dict:
    """같은 빌드 A/A 회차 → 지표별 잡음 바닥. 이웃 회차(시간순) 쌍으로 쌍 차이도 직접 잰다."""
    rs = R.sort_runs(runs)
    vals = R.metric_values(rs)
    out = {}
    for iid, ms in sorted(vals.items()):
        for k, vs in sorted(ms.items()):
            nums = [float(v) for v in vs if isinstance(v, (int, float)) and not isinstance(v, bool)]
            if len(nums) != len(vs) or len(nums) < 2:
                continue
            pairs = [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]
            nf = S.noise_floor(nums, pairs=pairs)
            out[f"{iid}.{k}"] = {kk: (round(v, 4) if isinstance(v, float) else v) for kk, v in nf.items()
                                 if kk != "mde_pct"}
            if "mde_pct" in nf:
                out[f"{iid}.{k}"]["mde_pct"] = {n: round(v, 3) for n, v in nf["mde_pct"].items()}
    return out


def noise_doc(runs: list[R.Run], *, gate: dict | None = None, label: str = "") -> dict:
    cls = V.ensure_same_class([r.meta for r in runs])
    use, bad = R.usable(runs, gate)
    builds = sorted({r.meta.get("build_hash") for r in use})
    warns = []
    if len(builds) > 1:
        warns.append(f"A/A인데 빌드가 여럿: {builds}")
    return {"schema": NOISE_SCHEMA, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "label": label, "device_class": cls, "layer": V.layer_for_class(cls),
            "run_ids": [r.run_id for r in use], "n_valid": len(use), "invalid_runs": bad,
            "build_hash": builds[0] if len(builds) == 1 else builds, "warnings": warns,
            "metrics": noise_from_runs(use) if len(use) >= 2 else {}}


def _class_entry(runs: list[R.Run], used: list[R.Run], bad: list[dict], noise: dict | None, noise_src) -> dict:
    from perfkit.compare.core import _env

    def one(k):
        vals = {r.field(k) for r in used}
        return next(iter(vals)) if len(vals) == 1 else sorted(map(str, vals))

    cls = V.ensure_same_class([r.meta for r in runs])
    return {"layer": V.layer_for_class(cls), "run_ids": [r.run_id for r in used], "n_runs": len(runs),
            "n_valid": len(used), "invalid_runs": bad,
            "probe_hash": one("probe_hash"), "quality_hash": one("quality_hash"), "scenario": one("scenario"),
            "save": one("save"), "build_hash": one("build_hash"), "commit": one("commit"), "version": one("version"),
            "env": _env(used or runs), "metrics": summarize_metrics(used), "noise_floor": noise,
            "noise_source": noise_src}


def _sha_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def register(name: str, *, runs: list[R.Run] | None = None, noise_runs: list[R.Run] | None = None,
             deterministic: dict[str, Path] | None = None, l2: Path | None = None, gate_of=None,
             force: bool = False, results_dir: Path | None = None) -> tuple[Path, dict]:
    """기준선 등록(등급별로 합침). gate_of(layer) → 관문 문턱 dict."""
    from perfkit.config import settings
    from perfkit.manifest import load

    if not name or any(c in name for c in "/\\:") or name.endswith(".json"):
        raise ValueError(f"기준선 이름이 잘못됨: {name!r}")
    p = baselines_dir() / f"{name}.json"
    doc = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    m, s = load(), settings()
    if doc is None:
        doc = {"schema": SCHEMA, "name": name, "classes": {}, "deterministic": {}}
    doc.update({"updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "manifest_version": m.version, "manifest_sha": m.sha256})
    doc.setdefault("created_at", doc["updated_at"])
    if s.profile:
        doc["profile_name"], doc["profile_sha"] = s.profile_name or "profile", s.profile_sha
    if runs:
        cls = V.ensure_same_class([r.meta for r in runs])
        if cls in doc["classes"] and not force:
            raise ValueError(f"기준선 {name}에 등급 {cls}가 이미 있음(바꾸려면 --force)")
        layer = V.layer_for_class(cls)
        gate = gate_of(layer) if gate_of else {}
        used, bad = R.usable(runs, gate)
        noise, src = None, None
        if noise_runs:
            V.ensure_same_class([r.meta for r in runs + noise_runs])
            nd = noise_doc(noise_runs, gate=gate)
            noise, src = nd["metrics"], {"run_ids": nd["run_ids"], "n_valid": nd["n_valid"],
                                         "invalid_runs": nd["invalid_runs"], "build_hash": nd["build_hash"]}
        doc["classes"][cls] = _class_entry(runs, used, bad, noise, src)
    for layer, path in (deterministic or {}).items():
        path = Path(path)
        res = json.loads(path.read_text(encoding="utf-8"))
        if res.get("layer") != layer:
            raise ValueError(f"{path}: 층 {res.get('layer')} ≠ {layer}")
        ref = str(path)
        if results_dir is not None:
            try:
                ref = path.resolve().relative_to(results_dir.resolve()).as_posix()
            except ValueError:
                pass
        doc["deterministic"][layer] = {"ref": ref, "sha256": _sha_file(path),
                                       "source": res.get("source"), "manifest_sha": res.get("manifest_sha")}
    if l2 is not None:
        path = Path(l2)
        res = json.loads(path.read_text(encoding="utf-8"))
        if res.get("schema") != "perfkit/result@1" or res.get("layer") != "L2":
            raise ValueError(f"{path}: L2 결과가 아님(층 {res.get('layer')})")
        ed = doc.setdefault("editor", {})
        if "L2" in ed and not force:
            raise ValueError(f"기준선 {name}에 L2가 이미 있음(바꾸려면 --force)")
        src = res.get("source") or {}
        ref = str(path)
        if results_dir is not None:
            try:
                ref = path.resolve().relative_to(results_dir.resolve()).as_posix()
            except ValueError:
                pass
        entry = {"ref": ref, "sha256": _sha_file(path), "runs": int(src.get("runs") or 1),
                 "manifest_sha": res.get("manifest_sha"), "collected_at": res.get("collected_at")}
        for k in ("commit", "probe_hash", "scenario", "save", "seed", "frames", "capture_fps", "freeze", "unity",
                  "quality", "dirty"):
            if k in src:
                entry[k] = src[k]
        if isinstance(src.get("tree"), dict):
            entry["tree"] = {k: src["tree"][k] for k in ("head", "dirty", "changed", "ignored") if k in src["tree"]}
        ed["L2"] = entry
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return p, doc


def list_baselines() -> list[dict]:
    d = baselines_dir()
    out = []
    for p in sorted(d.glob("*.json")) if d.exists() else []:
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if doc.get("schema") != SCHEMA:
            continue
        out.append({"name": doc["name"], "path": str(p), "updated_at": doc.get("updated_at"),
                    "classes": {c: {"n_valid": e.get("n_valid"), "noise": bool(e.get("noise_floor"))}
                                for c, e in (doc.get("classes") or {}).items()},
                    "deterministic": sorted(doc.get("deterministic") or {}),
                    "editor": sorted(doc.get("editor") or {})})
    return out


def noise_for(doc: dict | None, device_class: str) -> dict | None:
    """기준선·잡음 문서에서 그 등급의 잡음 바닥 {"<ID>.<지표>": {...}}."""
    if not doc:
        return None
    if doc.get("schema") == NOISE_SCHEMA:
        if doc.get("device_class") != device_class:
            raise V.ClassMixError(f"잡음 바닥 등급 {doc.get('device_class')} ≠ 비교 등급 {device_class}")
        return doc.get("metrics") or None
    if doc.get("schema") == SCHEMA:
        return ((doc.get("classes") or {}).get(device_class) or {}).get("noise_floor")
    return None
