"""Addressables 빌드 레이아웃(`buildlayout.json`) 요약: 번들, 자산 종류별 크기, 중복 의존(MEM-09·11·12·14·17, BLD-02).

크기는 직렬화(SerializedSize) + 스트리밍(StreamedSize, .resS 텍스처·오디오) 합계로, 번들 압축 전 값이다.
메모리 상주 크기의 근사값이고 Addressables에 든 것만 센다(플레이어 씬·Resources는 빠짐).
`AssetType` 번호는 Addressables 패키지의 `BuildLayout.AssetType` 열거형 순서다.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from perfkit.build.package import mb

ASSET_TYPES = [
    "Other", "Font", "GUISkin", "AnimationClip", "Avatar", "AnimationController", "AudioClip", "AudioMixer",
    "VideoClip", "Shader", "ComputeShader", "Mesh", "Texture", "Texture2D", "Texture3D", "Sprite",
    "ScriptableObject", "Prefab", "Model", "Material", "PhysicsMaterial", "PhysicsMaterial2D", "TextAsset", "Scene",
    "GameObject", "SceneObject", "MonoBehaviour", "Component", "MonoScript", "Cubemap", "Camera", "AudioListener",
    "Light", "NavMeshSettings", "RenderSettings", "LightmapSettings", "Transform", "MeshRenderer", "MeshFilter",
    "BoxCollider2D", "BoxCollider", "SphereCollider",
]
TEXTURE_TYPES = {"Texture", "Texture2D", "Texture3D", "Cubemap"}
BUNDLE_HASH = re.compile(r"_[0-9a-f]{32}(?=\.bundle$)")


def type_name(n) -> str:
    return ASSET_TYPES[n] if isinstance(n, int) and 0 <= n < len(ASSET_TYPES) else f"type{n}"


def bundle_key(name: str) -> str:
    """번들 이름에서 내용 해시를 뺀 이름(빌드마다 바뀌는 부분 제거)."""
    return BUNDLE_HASH.sub("", name)


def summarize(path: Path, top: int = 20) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    refs = (data.get("references") or {}).get("RefIds") or []
    by_rid = {r.get("rid"): r for r in refs}
    bundles = []
    objects = []  # (asset_path, type, object_name, bytes, main_type)
    guid_obj_size: dict[tuple[str, str], int] = {}
    asset_total: dict[str, int] = defaultdict(int)
    for r in refs:
        cls = (r.get("type") or {}).get("class", "")
        d = r.get("data") or {}
        if cls == "BuildLayout/Bundle":
            bundles.append({"name": bundle_key(d.get("Name", "")), "mb": mb(d.get("FileSize", 0)),
                            "compression": d.get("Compression"), "assets": d.get("AssetCount", 0)})
        elif cls in ("BuildLayout/ExplicitAsset", "BuildLayout/DataFromOtherAsset"):
            apath = d.get("AssetPath", "")
            guid = d.get("Guid") or d.get("AssetGuid") or ""
            for o in d.get("Objects") or []:
                size = int(o.get("SerializedSize", 0)) + int(o.get("StreamedSize", 0))
                objects.append((apath, type_name(o.get("AssetType")), o.get("ObjectName", ""), size,
                                type_name(d.get("MainAssetType"))))
                asset_total[apath] += size
                guid_obj_size.setdefault((guid, str(o.get("LocalIdentifierInFile"))), size)

    by_type = Counter()
    for _, t, _, size, _ in objects:
        by_type[t] += size

    def total(pred) -> int:
        return sum(o[3] for o in objects if pred(o))

    def top_assets(pred) -> list:
        acc: dict[str, int] = defaultdict(int)
        for o in objects:
            if pred(o):
                acc[o[0]] += o[3]
        return [{"path": p, "mb": mb(s)} for p, s in sorted(acc.items(), key=lambda x: (-x[1], x[0]))[:top]]

    is_tex = lambda o: o[1] in TEXTURE_TYPES
    is_mesh = lambda o: o[1] == "Mesh"
    is_clip = lambda o: o[1] == "AnimationClip"
    is_font_atlas = lambda o: o[1] == "Font" or (o[1] in TEXTURE_TYPES and o[0].lower().endswith(".asset")
                                                  and o[2].endswith(" Atlas"))

    dups = data.get("DuplicatedAssets") or []
    dup_bytes = 0
    dup_rows = []
    for dup in dups:
        guid = dup.get("AssetGuid", "")
        extra = 0
        for obj in dup.get("DuplicatedObjects") or []:
            copies = len(obj.get("IncludedInBundleFiles") or [])
            extra += guid_obj_size.get((guid, str(obj.get("LocalIdentifierInFile"))), 0) * max(0, copies - 1)
        dup_bytes += extra
        dup_rows.append({"guid": guid, "extra_mb": mb(extra)})
    # 중복 자산 경로: DataFromOtherAsset의 guid → 경로
    guid_path = {}
    for r in refs:
        d = r.get("data") or {}
        g = d.get("AssetGuid") or d.get("Guid")
        if g and d.get("AssetPath"):
            guid_path.setdefault(g, d["AssetPath"])
    for row in dup_rows:
        row["path"] = guid_path.get(row["guid"])

    return {
        "build": {"unity": data.get("UnityVersion"), "package": data.get("PackageVersion"),
                  "build_target": data.get("BuildTarget"), "error": data.get("BuildError") or None},
        "bundles": sorted(bundles, key=lambda b: b["name"]),
        "total_mb": mb(sum(o[3] for o in objects)),
        "by_type_mb": {k: mb(v) for k, v in sorted(by_type.items())},
        "texture_total_mb": mb(total(is_tex)), "texture_top": top_assets(is_tex),
        "mesh_total_mb": mb(total(is_mesh)), "mesh_top": top_assets(is_mesh),
        "animation_clip_mb": mb(total(is_clip)),
        "font_atlas_mb": mb(total(is_font_atlas)), "font_atlas_top": top_assets(is_font_atlas),
        "top_assets": [{"path": p, "mb": mb(s)} for p, s in sorted(asset_total.items(), key=lambda x: (-x[1], x[0]))[:top]],
        "dup_count": len(dups), "dup_mb": mb(dup_bytes),
        "duplicates": sorted(dup_rows, key=lambda r: (-r["extra_mb"], r["path"] or "", r["guid"]))[:top],
        "build_started": data.get("BuildStartTime"),
    }
