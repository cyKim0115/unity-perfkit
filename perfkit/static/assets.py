"""임포트 설정 규칙(L0, AST-01~20)과 머티리얼·폰트 에셋 점검. `.meta`·에셋 텍스트를 읽기만 한다.

Android 플레이어 기준 「실제로 쓰이는 값」을 고른다: 플랫폼 덮어쓰기(overridden=1)가 있으면 그 값,
없으면 기본(DefaultTexturePlatform) 값. 형식 자동(-1)은 압축 품질이 0이면 비압축, 아니면 프로젝트의
기본 텍스처 압축 형식(ProjectSettings)을 따른다.

에디터가 있어야 정확히 아는 것(텍스처 단색 여부, 아틀라스 빈 공간, 메시 정점 수, 실제 클립 유무)은
값으로 지어내지 않고 「에디터 필요」로 남긴다.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from perfkit.static import headers
from perfkit.static import unityyaml as Y
from perfkit.static.project import Project, Scope

TEXTURE_EXT = {".png", ".jpg", ".jpeg", ".tga", ".psd", ".tif", ".tiff", ".exr", ".hdr", ".bmp", ".gif", ".iff", ".pict"}
MODEL_EXT = {".fbx", ".obj", ".blend", ".dae", ".3ds", ".dxf", ".max", ".ma", ".mb"}
AUDIO_EXT = {".wav", ".ogg", ".mp3", ".aif", ".aiff", ".flac", ".mod", ".it", ".s3m", ".xm"}

META_SKIP = frozenset({"internalIDToNameTable", "spriteSheet", "humanDescription", "fileIDToRecycleName",
                       "externalObjects", "extraUserProperties", "referencedClips", "clipAnimations"})

# TextureImporterFormat 값 → 이름(자주 쓰는 것). 나머지는 "fmt<값>".
TEX_FORMATS = {
    -1: "Automatic", 1: "Alpha8", 2: "ARGB16", 3: "RGB24", 4: "RGBA32", 5: "ARGB32", 7: "RGB16", 9: "R16",
    10: "DXT1", 12: "DXT5", 13: "RGBA16", 17: "RGBAHalf", 20: "RGBAFloat", 24: "BC6H", 25: "BC7", 26: "BC4",
    27: "BC5", 28: "DXT1Crunched", 29: "DXT5Crunched", 30: "PVRTC_RGB2", 31: "PVRTC_RGBA2", 32: "PVRTC_RGB4",
    33: "PVRTC_RGBA4", 34: "ETC_RGB4", 41: "EAC_R", 42: "EAC_R_SIGNED", 43: "EAC_RG", 44: "EAC_RG_SIGNED",
    45: "ETC2_RGB4", 46: "ETC2_RGB4_PUNCHTHROUGH_ALPHA", 47: "ETC2_RGBA8", 48: "ASTC_4x4", 49: "ASTC_5x5",
    50: "ASTC_6x6", 51: "ASTC_8x8", 52: "ASTC_10x10", 53: "ASTC_12x12", 62: "RG16", 63: "R8",
    64: "ETC_RGB4Crunched", 65: "ETC2_RGBA8Crunched", 66: "ASTC_HDR_4x4", 67: "ASTC_HDR_5x5", 68: "ASTC_HDR_6x6",
    69: "ASTC_HDR_8x8", 70: "ASTC_HDR_10x10", 71: "ASTC_HDR_12x12",
}
CRUNCHED_FORMATS = {28, 29, 64, 65}
ASTC_BPP = {"4x4": 8.0, "5x5": 5.12, "6x6": 3.56, "8x8": 2.0, "10x10": 1.28, "12x12": 0.89}
# PlayerSettings 텍스처 압축 형식(m_BuildTargetDefaultTextureCompressionFormat)
DEFAULT_COMPRESSION = {0: "Unknown", 1: "ETC", 2: "ETC2", 3: "ASTC", 4: "PVRTC", 5: "DXTC", 6: "BPTC", 7: "DXTC_RGTC"}
TEXTURE_TYPES = {0: "Default", 1: "NormalMap", 2: "GUI", 4: "Cookie", 6: "Lightmap", 7: "Cursor", 8: "Sprite",
                 10: "SingleChannel", 11: "Shadowmask", 12: "DirectionalLightmap"}
UI_TEXTURE_TYPES = {2, 8}
WORLD_TEXTURE_TYPES = {0, 1}
LOAD_TYPES = {0: "DecompressOnLoad", 1: "CompressedInMemory", 2: "Streaming"}
AUDIO_FORMATS = {0: "PCM", 1: "Vorbis", 2: "ADPCM", 3: "MP3", 4: "VAG", 5: "HEVAG", 6: "XMA", 7: "AAC", 8: "GCADPCM",
                 9: "ATRAC9"}
ANIMATION_TYPES = {0: "None", 1: "Legacy", 2: "Generic", 3: "Human"}
NORMAL_MODES = {0: "Import", 1: "Calculate", 2: "None"}
ANIM_COMPRESSION = {0: "Off", 1: "KeyframeReduction", 2: "KeyframeReductionAndCompression", 3: "Optimal"}
MESH_COMPRESSION = {0: "Off", 1: "Low", 2: "Medium", 3: "High"}
INDEX_FORMATS = {0: "Auto", 1: "UInt16", 2: "UInt32"}


@dataclass
class ImportOptions:
    texture_size_limit: int = 2048       # Auditor TextureSizeLimit 기본
    streaming_min_size: int = 1024       # 이 크기(긴 변) 이상이면 밉 스트리밍 대상
    long_clip_seconds: float = 10.0      # 이 길이 이상은 음악·긴 소리
    short_clip_seconds: float = 2.0      # 이 길이 미만은 짧은 효과음
    platform: str = "Android"
    top_n: int = 20


@dataclass
class AssetScan:
    textures: list[dict] = field(default_factory=list)
    models: list[dict] = field(default_factory=list)
    audio: list[dict] = field(default_factory=list)
    fonts: list[dict] = field(default_factory=list)
    materials: list[dict] = field(default_factory=list)
    presets: list[str] = field(default_factory=list)
    atlases: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _platform(imp: dict, platform: str) -> dict:
    rows = imp.get("platformSettings") or []
    default = next((r for r in rows if isinstance(r, dict) and r.get("buildTarget") == "DefaultTexturePlatform"), {})
    plat = next((r for r in rows if isinstance(r, dict) and r.get("buildTarget") == platform), None)
    if plat and Y.flag(plat.get("overridden")):
        return {**plat, "_overridden": True}
    return {**default, "_overridden": False}


def _pow2(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0


def scan(project: Project, scope: Scope, opts: ImportOptions, default_compression: str = "ASTC") -> AssetScan:
    out = AssetScan()
    for rel in project.files:
        if not rel.endswith(".meta"):
            continue
        target = rel[:-5]
        if not scope.contains(target):
            continue
        low = target.lower()
        ext = "." + low.rsplit(".", 1)[-1] if "." in low.rsplit("/", 1)[-1] else ""
        kind = "texture" if ext in TEXTURE_EXT else "model" if ext in MODEL_EXT else "audio" if ext in AUDIO_EXT else None
        if kind is None:
            if low.endswith(".preset"):
                out.presets.append(target)
            elif low.endswith(".spriteatlasv2") or low.endswith(".spriteatlas"):
                out.atlases.append(target)
            continue
        try:
            meta = Y.read(project.abs(rel), META_SKIP)
        except OSError as exc:
            out.errors.append(f"{rel}: {exc}")
            continue
        if kind == "texture" and "TextureImporter" in meta:
            out.textures.append(_texture(project, target, meta["TextureImporter"], opts, default_compression))
        elif kind == "model" and "ModelImporter" in meta:
            out.models.append(_model(target, meta["ModelImporter"]))
        elif kind == "audio" and "AudioImporter" in meta:
            out.audio.append(_audio(project, target, meta["AudioImporter"], opts))
    for rel in project.files:
        if not scope.contains(rel):
            continue
        low = rel.lower()
        if low.endswith(".mat"):
            out.materials.append(_material(project, rel))
        elif low.endswith(".asset"):
            f = _tmp_font(project, rel)
            if f:
                out.fonts.append(f)
    return out


def _texture(project: Project, path: str, imp: dict, opts: ImportOptions, default_compression: str) -> dict:
    eff = _platform(imp, opts.platform)
    fmt = Y.num(eff.get("textureFormat"), -1)
    comp = Y.num(eff.get("textureCompression"), 1)
    if fmt == -1:
        fmt_name = "Uncompressed(auto)" if comp == 0 else f"{default_compression}(auto)"
    else:
        fmt_name = TEX_FORMATS.get(fmt, f"fmt{fmt}")
    size = headers.image_size(project.abs(path))
    max_size = Y.num(eff.get("maxTextureSize"), Y.num(imp.get("maxTextureSize"), 2048))
    ttype = Y.num(imp.get("textureType"), 0)
    mip = Y.flag(Y.dig(imp, "mipmaps", "enableMipMap"), True)
    crunched = Y.flag(eff.get("crunchedCompression"), False) or fmt in CRUNCHED_FORMATS
    w, h = size if size else (None, None)
    imported_long = None
    if size:
        imported_long = min(max(w, h), max_size or max(w, h))
    return {
        "path": path, "type": TEXTURE_TYPES.get(ttype, f"type{ttype}"), "type_id": ttype,
        "width": w, "height": h, "max_size": max_size, "imported_long": imported_long,
        "format": fmt_name, "format_id": fmt, "compression": comp, "overridden": eff["_overridden"],
        "readable": Y.flag(imp.get("isReadable"), False), "mipmaps": mip,
        "streaming": Y.flag(imp.get("streamingMipmaps"), False),
        "aniso": Y.num(Y.dig(imp, "textureSettings", "aniso"), -1),
        "npot_scale": Y.num(imp.get("nPOTScale"), 1), "crunched": crunched,
    }


def _model(path: str, imp: dict) -> dict:
    anim = imp.get("animations") or {}
    meshes = imp.get("meshes") or {}
    tan = imp.get("tangentSpace") or {}
    return {
        "path": path,
        "readable": Y.flag(meshes.get("isReadable", anim.get("isReadable")), False),
        "index_format": INDEX_FORMATS.get(Y.num(meshes.get("indexFormat"), 0), "?"),
        "mesh_compression": MESH_COMPRESSION.get(Y.num(meshes.get("meshCompression"), 0), "?"),
        "import_blendshapes": Y.flag(meshes.get("importBlendShapes"), True),
        "normal_mode": NORMAL_MODES.get(Y.num(tan.get("normalImportMode"), 0), "?"),
        "blendshape_normal_mode": NORMAL_MODES.get(Y.num(tan.get("blendShapeNormalImportMode"), 1), "?"),
        "tangent_mode": Y.num(tan.get("tangentImportMode"), 3),
        "animation_type": ANIMATION_TYPES.get(Y.num(imp.get("animationType"), 2), "?"),
        "import_animation": Y.flag(imp.get("importAnimation"), True),
        "anim_compression": ANIM_COMPRESSION.get(Y.num(anim.get("animationCompression"), 3), "?"),
        "anim_errors": [Y.num(anim.get(k)) for k in ("animationRotationError", "animationPositionError",
                                                     "animationScaleError")],
        "optimize_game_objects": Y.flag(anim.get("optimizeGameObjects"), False),
        "extra_exposed": len(anim.get("extraExposedTransformPaths") or []) if isinstance(
            anim.get("extraExposedTransformPaths"), list) else 0,
    }


def _audio(project: Project, path: str, imp: dict, opts: ImportOptions) -> dict:
    ds = imp.get("defaultSettings") or {}
    ov = imp.get("platformSettingOverrides") or {}
    plat = ov.get(opts.platform) if isinstance(ov, dict) else None
    eff = plat if isinstance(plat, dict) and plat else ds
    info = headers.audio_info(project.abs(path)) or {}
    return {
        "path": path,
        "load_type": LOAD_TYPES.get(Y.num(eff.get("loadType"), 0), "?"),
        "format": AUDIO_FORMATS.get(Y.num(eff.get("compressionFormat"), 1), "?"),
        "preload": Y.flag(eff.get("preloadAudioData"), True),
        "sample_rate_setting": Y.num(eff.get("sampleRateSetting"), 0),
        "sample_rate_override": Y.num(eff.get("sampleRateOverride")),
        "force_mono": Y.flag(imp.get("forceToMono"), False),
        "load_in_background": Y.flag(imp.get("loadInBackground"), False),
        "channels": info.get("channels"), "rate": info.get("rate"), "seconds": info.get("seconds"),
    }


KEYWORD_ALPHATEST = re.compile(r"_ALPHATEST_ON\b")
FLOAT_RE = r"^\s*-\s*{}:\s*([-0-9.eE]+)\s*$"


def _material(project: Project, rel: str) -> dict:
    try:
        text = project.read_text(rel)
    except OSError:
        return {"path": rel, "error": "read"}

    def fval(name):
        m = re.search(FLOAT_RE.format(re.escape(name)), text, re.M)
        return float(m.group(1)) if m else None

    shader_guid = None
    m = re.search(r"m_Shader:\s*\{[^}]*guid:\s*([0-9a-f]{32})", text)
    if m:
        shader_guid = m.group(1)
    rq = re.search(r"m_CustomRenderQueue:\s*(-?\d+)", text)
    return {
        "path": rel,
        "shader": project.path_of_guid(shader_guid) or (f"builtin:{shader_guid}" if shader_guid else None),
        "alpha_clip": bool(KEYWORD_ALPHATEST.search(text)) or (fval("_AlphaClip") or 0) >= 0.5,
        "transparent": (fval("_Surface") or 0) >= 0.5 or (int(rq.group(1)) if rq else -1) >= 3000,
        "instancing": bool(re.search(r"m_EnableInstancingVariants:\s*1", text)),
    }


def _tmp_font(project: Project, rel: str) -> dict | None:
    # 아틀라스 텍스처(긴 image data 줄)가 앞에 오는 경우가 많아 파일 전체에서 짧은 줄만 모은다.
    try:
        with open(project.abs(rel), "rb") as f:
            raw = f.read()
    except OSError:
        return None
    if b"m_AtlasPopulationMode" not in raw:
        return None
    head = "\n".join(l for l in raw.decode("utf-8", errors="replace").splitlines() if len(l) < 400)

    def ival(name, default=None):
        m = re.search(rf"^\s*{name}:\s*(-?\d+)\s*$", head, re.M)
        return int(m.group(1)) if m else default

    fallback = 0
    m = re.search(r"m_FallbackFontAssetTable:\s*(\[\])?\s*\n((?:\s*-\s*\{[^\n]*\n)*)", head)
    if m and not m.group(1):
        fallback = m.group(2).count("{")
    return {
        "path": rel,
        "dynamic": ival("m_AtlasPopulationMode", 0) == 1,
        "atlas_width": ival("m_AtlasWidth"), "atlas_height": ival("m_AtlasHeight"),
        "multi_atlas": ival("m_IsMultiAtlasTexturesEnabled", 0) == 1,
        "clear_dynamic_on_build": ival("m_ClearDynamicDataOnBuild", 0) == 1,
        "fallbacks": fallback,
    }


# --------------------------------------------------------------------------- 항목 값

def _paths(rows: list[dict], pred, n: int | None = None) -> list[str]:
    """위반 자산 경로 전부(정렬). 새로 생긴·없어진 위반 비교에 쓴다."""
    return sorted(r["path"] for r in rows if pred(r))


def items(scan_: AssetScan, opts: ImportOptions, *, postprocessors: list[str], default_presets: int,
          streaming_settings: dict) -> dict[str, dict]:
    """AST-* 항목 결과(result 스키마 items 모양)."""
    T, M, A, F = scan_.textures, scan_.models, scan_.audio, scan_.fonts
    N = opts.top_n
    res: dict[str, dict] = {}

    def ok(iid, value, detail, violations=None, partial=None, values=None):
        d = {"status": "ok", "values": values if values is not None else {"value": value},
             "collector": "static.import_rules", "detail": detail}
        if violations is not None:
            d["violations"] = violations
        if partial:
            d["editor_needed"] = partial
        res[iid] = d

    def editor(iid, why):
        res[iid] = {"status": "not_collected", "reason": f"에디터 필요: {why}", "collector": "static.import_rules"}

    # AST-01 압축 형식
    non_astc = lambda t: not t["format"].startswith(("ASTC", "ASTC_HDR"))
    fmts = Counter(t["format"] for t in T)
    blocks = Counter(t["format"].split("_")[-1].split("(")[0] for t in T if t["format"].startswith("ASTC_"))
    ok("AST-01", sum(1 for t in T if non_astc(t)),
       {"textures": len(T), "by_format": dict(sorted(fmts.items())), "astc_blocks": dict(sorted(blocks.items())),
        "astc_bpp": {k: ASTC_BPP[k] for k in sorted(blocks) if k in ASTC_BPP},
        "rule": "Android에서 실제 형식이 ASTC가 아닌 텍스처 수(자동은 프로젝트 기본 압축 형식을 따름)"},
       _paths(T, non_astc, N))
    # AST-02 Read/Write
    ok("AST-02", sum(t["readable"] for t in T), {"textures": len(T)}, _paths(T, lambda t: t["readable"], N))
    # AST-03 밉맵
    ui_mip = lambda t: t["type_id"] in UI_TEXTURE_TYPES and t["mipmaps"]
    world_nomip = lambda t: t["type_id"] in WORLD_TEXTURE_TYPES and not t["mipmaps"]
    ok("AST-03", sum(map(ui_mip, T)) + sum(map(world_nomip, T)),
       {"ui_with_mipmaps": sum(map(ui_mip, T)), "default_without_mipmaps": sum(map(world_nomip, T)),
        "rule": "UI·스프라이트(GUI·Sprite 유형)에 밉 켜짐 + 3D(Default·NormalMap 유형)에 밉 꺼짐. 유형으로만 가름"},
       _paths(T, lambda t: ui_mip(t) or world_nomip(t), N))
    # AST-04 크기·NPOT
    lim = opts.texture_size_limit
    over = lambda t: t["imported_long"] is not None and t["imported_long"] > lim
    npot = lambda t: (t["width"] is not None and t["npot_scale"] == 0 and t["type_id"] not in UI_TEXTURE_TYPES
                      and not (_pow2(t["width"]) and _pow2(t["height"])))
    unknown = sum(1 for t in T if t["width"] is None)
    ok("AST-04", sum(map(over, T)) + sum(map(npot, T)),
       {"over_limit": sum(map(over, T)), "limit": lim, "npot_not_scaled": sum(map(npot, T)),
        "size_unknown": unknown, "rule": "임포트 크기(원본과 최대 크기 중 작은 쪽) > 한도, 또는 NPOT인데 크기 조정 없음(스프라이트·GUI 제외)"},
       _paths(T, lambda t: over(t) or npot(t), N))
    # AST-05 밉 스트리밍
    no_stream = lambda t: (t["mipmaps"] and not t["streaming"] and t["imported_long"] is not None
                           and t["imported_long"] >= opts.streaming_min_size)
    ok("AST-05", sum(map(no_stream, T)),
       {"min_size": opts.streaming_min_size, "streaming_on": sum(t["streaming"] for t in T),
        "quality": streaming_settings}, _paths(T, no_stream, N))
    # AST-06 이방성(단색 판정은 에디터)
    ok("AST-06", sum(1 for t in T if (t["aniso"] or 0) > 1),
       {"aniso_gt1": sum(1 for t in T if (t["aniso"] or 0) > 1)}, _paths(T, lambda t: (t["aniso"] or 0) > 1, N),
       partial=["solid_color_not_1x1: 픽셀을 봐야 함(에디터)"])
    editor("AST-07", "스프라이트 아틀라스 빈 공간은 패킹 결과가 있어야 앎")
    res["AST-07"]["detail"] = {"atlases": len(scan_.atlases)}
    # AST-08 Crunch
    ok("AST-08", sum(t["crunched"] for t in T), {}, _paths(T, lambda t: t["crunched"], N))
    # AST-09 메시 Read/Write
    ok("AST-09", sum(m["readable"] for m in M), {"models": len(M)}, _paths(M, lambda m: m["readable"], N))
    # AST-10 32비트 인덱스(정점 수는 에디터)
    ok("AST-10", sum(m["index_format"] == "UInt32" for m in M),
       {"by_index_format": dict(sorted(Counter(m["index_format"] for m in M).items())),
        "rule": "인덱스 형식을 UInt32로 고정한 모델 수(후보). 정점 ≤ 65535인지는 에디터"},
       _paths(M, lambda m: m["index_format"] == "UInt32", N), partial=["vertex_count: 메시 정점 수(에디터)"])
    # AST-11 메시 압축
    ok("AST-11", sum(m["mesh_compression"] != "Off" for m in M),
       {"by_mesh_compression": dict(sorted(Counter(m["mesh_compression"] for m in M).items()))},
       _paths(M, lambda m: m["mesh_compression"] != "Off", N))
    # AST-12 노멀·블렌드셰이프 노멀
    mis = lambda m: m["import_blendshapes"] and m["normal_mode"] != m["blendshape_normal_mode"]
    combos = Counter(f"{m['normal_mode']}/{m['blendshape_normal_mode']}" for m in M if m["import_blendshapes"])
    ok("AST-12", sum(map(mis, M)),
       {"normal/blendshape_normal": dict(sorted(combos.items())),
        "rule": "블렌드셰이프를 가져오는데 Blend Shape Normals가 Normals와 다른 모델 수"}, _paths(M, mis, N))
    editor("AST-13", "메시에 실제 애니메이션·블렌드셰이프가 있는지, 정점·삼각형 수는 임포트 결과가 있어야 앎")
    res["AST-13"]["detail"] = {"rig_enabled": sum(m["animation_type"] != "None" for m in M),
                               "blendshapes_import_on": sum(m["import_blendshapes"] for m in M)}
    # AST-14 Load Type
    long_ = lambda a: a["seconds"] is not None and a["seconds"] >= opts.long_clip_seconds
    short = lambda a: a["seconds"] is not None and a["seconds"] < opts.short_clip_seconds
    bad_load = lambda a: (long_(a) and a["load_type"] == "DecompressOnLoad") or (short(a) and a["load_type"] == "Streaming")
    ok("AST-14", sum(map(bad_load, A)),
       {"clips": len(A), "by_load_type": dict(sorted(Counter(a["load_type"] for a in A).items())),
        "length_unknown": sum(a["seconds"] is None for a in A),
        "rule": f"긴 소리(≥{opts.long_clip_seconds:g}초) Decompress On Load + 짧은 소리(<{opts.short_clip_seconds:g}초) Streaming"},
       _paths(A, bad_load, N))
    # AST-15 압축 형식
    bad_fmt = lambda a: long_(a) and a["format"] in ("PCM", "ADPCM")
    ok("AST-15", sum(map(bad_fmt, A)),
       {"by_format": dict(sorted(Counter(a["format"] for a in A).items())),
        "rule": f"긴 소리(≥{opts.long_clip_seconds:g}초)가 PCM·ADPCM"}, _paths(A, bad_fmt, N))
    # AST-16 모노·Preload·샘플레이트
    stereo = lambda a: (a["channels"] or 0) >= 2 and not a["force_mono"]
    ok("AST-16", sum(1 for a in A if stereo(a) or a["preload"]),
       {"stereo_not_forced_mono": sum(map(stereo, A)), "preload_on": sum(a["preload"] for a in A),
        "sample_rate_override": sum(a["sample_rate_setting"] == 2 for a in A),
        "rule": "스테레오인데 Force To Mono 꺼짐, 또는 Preload Audio Data 켜짐(어느 하나)"},
       _paths(A, lambda a: stereo(a) or a["preload"], N))
    # AST-17 애니메이션 압축
    off = lambda m: m["import_animation"] and m["animation_type"] != "None" and m["anim_compression"] == "Off"
    ok("AST-17", sum(map(off, M)),
       {"by_compression": dict(sorted(Counter(m["anim_compression"] for m in M if m["import_animation"]).items())),
        "error_settings": dict(sorted(Counter(",".join(f"{e:g}" if e is not None else "-" for e in m["anim_errors"])
                                              for m in M if m["import_animation"]).items()))},
       _paths(M, off, N))
    # AST-18 Optimize Game Objects
    rig = lambda m: m["animation_type"] in ("Generic", "Human")
    not_opt = lambda m: rig(m) and not m["optimize_game_objects"]
    ok("AST-18", sum(map(not_opt, M)),
       {"rigged": sum(map(rig, M)), "optimized": sum(1 for m in M if rig(m) and m["optimize_game_objects"]),
        "extra_exposed_transforms": sum(m["extra_exposed"] for m in M)}, _paths(M, not_opt, N))
    # AST-19 TMP 폰트
    dyn_keep = lambda f: f["dynamic"] and not f["clear_dynamic_on_build"]
    ok("AST-19", sum(map(dyn_keep, F)),
       {"fonts": [{k: f[k] for k in ("path", "dynamic", "atlas_width", "atlas_height", "multi_atlas",
                                       "clear_dynamic_on_build", "fallbacks")} for f in sorted(F, key=lambda f: f["path"])],
        "rule": "동적 폰트인데 Clear Dynamic Data on Build 꺼짐"}, _paths(F, dyn_keep, N))
    # AST-20 강제 수단
    ok("AST-20", len(postprocessors) + default_presets,
       {"asset_postprocessors": sorted(postprocessors), "default_presets": default_presets,
        "preset_files": len(scan_.presets), "rule": "AssetPostprocessor 클래스 수 + Preset Manager 기본 프리셋 수"})
    return res
