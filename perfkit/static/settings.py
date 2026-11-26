"""프로젝트 설정 스냅숏(L0): ProjectSettings·QualitySettings·물리·URP 에셋·렌더러·볼륨 프로필.

설정 값은 바뀌면 「확인 필요」가 되는 스냅숏이고, 매니페스트에 절대 기준이 있는 지표(목표 API, ARM64만,
증분 GC 등)는 값 그대로 판정에 쓴다. 에디터에서만 아는 값(빌드 프로필의 Script Debugging 등)은
`editor_needed`로 남긴다.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from perfkit.static import unityyaml as Y
from perfkit.static.project import Project

GRAPHICS_APIS = {0: "Direct3D9", 2: "Direct3D11", 4: "Null", 8: "OpenGLES2", 11: "OpenGLES3", 16: "Metal",
                 17: "OpenGLCore", 18: "Direct3D12", 21: "Vulkan", 22: "Switch", 23: "XboxOneD3D12", 24: "GameCoreXboxOne",
                 25: "GameCoreXboxSeries", 26: "PlayStation5", 27: "PlayStation5NGGC", 28: "WebGPU"}
IL2CPP_CONFIG = {0: "Debug", 1: "Release", 2: "Master"}
IL2CPP_CODEGEN = {0: "OptimizeSpeed", 1: "OptimizeSize"}
STRIPPING = {0: "Disabled", 1: "Low", 2: "Medium", 3: "High", 4: "Minimal"}
DEFORMATION = {0: "CPU", 1: "GPU", 2: "GPUBatched"}
ARCH_BITS = {1: "ARMv7", 2: "ARM64", 4: "X86", 8: "X86_64"}
RENDERING_MODE = {0: "Forward", 1: "Deferred", 2: "ForwardPlus", 3: "DeferredPlus"}
ADDITIONAL_LIGHTS = {0: "Disabled", 1: "PerVertex", 2: "PerPixel"}
DEPTH_PRIMING = {0: "Disabled", 1: "Auto", 2: "Forced"}
INTERMEDIATE = {0: "Auto", 1: "Always"}
COPY_DEPTH = {0: "AfterOpaques", 1: "AfterTransparents", 2: "ForcePrepass"}
CODE_VARIANT = {0: "Release", 1: "Instrumented", 2: "Checked", 3: "Debug"}

URP_KEYS = {
    "GPU-06": ["m_SupportsDynamicBatching", "m_GPUResidentDrawerMode", "m_GPUResidentDrawerEnableOcclusionCullingInCameras",
               "m_SmallMeshScreenPercentage", "m_UseSRPBatcher"],
    "GPU-12": ["m_RenderScale", "m_UpscalingFilter", "m_FsrOverrideSharpness", "m_FsrSharpness"],
    "GPU-17": ["m_RequireDepthTexture", "m_RequireOpaqueTexture", "m_OpaqueDownsampling", "m_SupportsHDR",
               "m_HDRColorBufferPrecision", "m_MSAA", "m_StoreActionsOptimization", "m_SupportsTerrainHoles"],
    "GPU-19": ["m_MainLightShadowsSupported", "m_MainLightShadowmapResolution", "m_ShadowDistance",
               "m_ShadowCascadeCount", "m_Cascade2Split", "m_Cascade3Split", "m_Cascade4Split",
               "m_SoftShadowsSupported", "m_SoftShadowQuality", "m_AdditionalLightShadowsSupported",
               "m_AdditionalLightsShadowmapResolution", "m_ShadowDepthBias", "m_ShadowNormalBias",
               "m_ConservativeEnclosingSphere"],
    "GPU-23": ["m_MainLightRenderingMode", "m_AdditionalLightsRenderingMode", "m_AdditionalLightsPerObjectLimit",
               "m_AdditionalLightShadowsSupported", "m_ReflectionProbeBlending", "m_ReflectionProbeBoxProjection"],
}
RENDERER_KEYS = {
    "GPU-17": ["m_DepthPrimingMode", "m_IntermediateTextureMode", "m_CopyDepthMode", "m_DepthTextureFormat",
               "m_DepthAttachmentFormat", "m_AccurateGbufferNormals"],
    "GPU-23": ["m_RenderingMode"],
}


def _read(project: Project, rel: str) -> dict:
    p = project.abs(rel)
    if not p.exists():
        return {}
    return Y.read(p)


def _body(doc: dict, name: str) -> dict:
    v = doc.get(name)
    return v if isinstance(v, dict) else {}


def _plat(v, platform: str = "Android"):
    """`{Android: 1}` 같은 플랫폼별 칸에서 값. 없으면 None."""
    if isinstance(v, dict):
        return v.get(platform)
    return None


def _short_sha(obj) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]


def _pick(body: dict, keys: list[str]) -> dict:
    out = {}
    for k in keys:
        if k in body:
            v = body[k]
            n = Y.num(v)
            out[k] = n if n is not None else v
    return out


class Settings:
    """설정 파일을 한 번 읽어 두고 항목별 값을 꺼낸다."""

    def __init__(self, project: Project, platform: str = "Android", scope=None):
        self.p = project
        self.scope = scope
        self.platform = platform
        self.player = _body(_read(project, "ProjectSettings/ProjectSettings.asset"), "PlayerSettings")
        self.quality = _body(_read(project, "ProjectSettings/QualitySettings.asset"), "QualitySettings")
        self.graphics = _body(_read(project, "ProjectSettings/GraphicsSettings.asset"), "GraphicsSettings")
        self.physics = _body(_read(project, "ProjectSettings/DynamicsManager.asset"), "PhysicsManager")
        self.presets = _body(_read(project, "ProjectSettings/PresetManager.asset"), "PresetManager")
        self.levels = [l for l in (self.quality.get("m_QualitySettings") or []) if isinstance(l, dict)]
        d = (self.quality.get("m_PerPlatformDefaultQuality") or {})
        idx = Y.num(d.get(platform)) if isinstance(d, dict) else None
        if idx is None:
            idx = Y.num(self.quality.get("m_CurrentQuality"), 0)
        self.default_level_index = idx if self.levels and 0 <= idx < len(self.levels) else 0
        self.notes: list[str] = []
        if not self.player:
            self.notes.append("ProjectSettings.asset을 못 읽음")

    @property
    def default_level(self) -> dict:
        return self.levels[self.default_level_index] if self.levels else {}

    # ---- 보조
    def default_texture_compression(self) -> list[str]:
        rows = self.player.get("m_BuildTargetDefaultTextureCompressionFormat") or []
        for r in rows if isinstance(rows, list) else []:
            if isinstance(r, dict) and r.get("m_BuildTarget") == self.platform:
                from perfkit.static.assets import DEFAULT_COMPRESSION
                return [DEFAULT_COMPRESSION.get(v, f"fmt{v}") for v in Y.hex_ints(r.get("m_Formats", ""))]
        return []

    def streaming_settings(self) -> dict:
        lv = self.default_level
        return {"level": lv.get("name"), "active": Y.flag(lv.get("streamingMipmapsActive")),
                "budget_mb": Y.num(lv.get("streamingMipmapsMemoryBudget"))}

    def default_presets(self) -> int:
        dp = self.presets.get("m_DefaultPresets")
        if isinstance(dp, list):
            total = 0
            for row in dp:
                if isinstance(row, dict):
                    for v in row.values():
                        if isinstance(v, list):
                            total += len(v)
                        elif isinstance(v, dict) and "defaultPresets" in v:
                            total += len(v["defaultPresets"] or [])
            return total
        if isinstance(dp, dict):
            return sum(len(v.get("defaultPresets") or []) if isinstance(v, dict) else 0 for v in dp.values())
        return 0

    def render_pipeline_assets(self) -> list[dict]:
        """쓰이는 URP 에셋들: [{"path", "used_by": [...]}], 경로순."""
        used: dict[str, set] = {}

        def add(ref, who):
            guid = ref.get("guid") if isinstance(ref, dict) else None
            path = self.p.path_of_guid(guid)
            if path:
                used.setdefault(path, set()).add(who)

        add(self.graphics.get("m_CustomRenderPipeline"), "graphics")
        for i, lv in enumerate(self.levels):
            add(lv.get("customRenderPipeline"), f"quality:{lv.get('name', i)}" + ("*" if i == self.default_level_index else ""))
        return [{"path": p, "used_by": sorted(w)} for p, w in sorted(used.items())]

    def urp(self) -> list[dict]:
        """URP 에셋 + 렌더러 + 렌더러 기능(문서별 본문)."""
        out = []
        for a in self.render_pipeline_assets():
            docs = Y.read_docs(self.p.abs(a["path"])) if self.p.abs(a["path"]).exists() else []
            body = docs[0].body if docs else {}
            renderers = []
            for ref in body.get("m_RendererDataList") or []:
                rpath = self.p.path_of_guid(ref.get("guid") if isinstance(ref, dict) else None)
                if not rpath or not self.p.abs(rpath).exists():
                    continue
                rdocs = Y.read_docs(self.p.abs(rpath))
                main = next((d for d in rdocs if "m_RendererFeatures" in d.body), rdocs[0] if rdocs else None)
                feats = []
                ids = [f.get("fileID") for f in (main.body.get("m_RendererFeatures") or []) if isinstance(f, dict)] if main else []
                by_id = {d.file_id: d for d in rdocs}
                for fid in ids:
                    d = by_id.get(str(fid))
                    if d is None:
                        continue
                    feats.append({"name": d.body.get("m_Name"), "active": Y.flag(d.body.get("m_Active"), True),
                                  "settings": d.body.get("m_Settings") if isinstance(d.body.get("m_Settings"), dict) else None})
                renderers.append({"path": rpath, "body": main.body if main else {}, "features": feats})
            out.append({**a, "body": body, "renderers": renderers})
        return out

    def volume_profiles(self) -> list[dict]:
        out = []
        for rel in self.p.files:
            if not rel.endswith(".asset") or (self.scope is not None and not self.scope.contains(rel)):
                continue
            try:
                with open(self.p.abs(rel), encoding="utf-8", errors="replace") as f:
                    head = f.read(4096)
            except OSError:
                continue
            if "components:" not in head or "m_EditorClassIdentifier" not in head:
                continue
            docs = Y.read_docs(self.p.abs(rel))
            main = next((d for d in docs if isinstance(d.body.get("components"), list)), None)
            if main is None:
                continue
            by_id = {d.file_id: d for d in docs}
            comps = []
            for ref in main.body["components"]:
                d = by_id.get(str(ref.get("fileID"))) if isinstance(ref, dict) else None
                if d is not None:
                    comps.append({"name": d.body.get("m_Name"), "active": Y.flag(d.body.get("active"), True)})
            out.append({"path": rel, "components": sorted(comps, key=lambda c: c["name"] or "")})
        return out


# --------------------------------------------------------------------------- 항목

def items(s: Settings, src: dict) -> dict[str, dict]:
    """설정 항목들의 결과. src: 소스 검사에서 모은 대입(targetFrameRate 등)·렌더 패스·후처리기."""
    P, lv = s.player, s.default_level
    res: dict[str, dict] = {}

    def ok(iid, values, detail=None, collector="static.project_settings", partial=None):
        d = {"status": "ok", "values": values, "collector": collector}
        if detail:
            d["detail"] = detail
        if partial:
            d["editor_needed"] = partial
        res[iid] = d

    def editor(iid, why, collector):
        res[iid] = {"status": "not_collected", "reason": f"에디터 필요: {why}", "collector": collector}

    levels = [{"name": l.get("name"), "vsync": Y.num(l.get("vSyncCount"))} for l in s.levels]
    tfr = sorted({a[0] for a in src.get("target_frame_rate", [])})
    lits = [Y.num(v) for v in tfr if Y.num(v) is not None]
    target = lits[0] if len(lits) == 1 and len(tfr) == 1 else None
    ok("FRM-05", {"target_fps": target, "vsync_count": Y.num(lv.get("vSyncCount")),
                  "optimized_frame_pacing": Y.flag(P.get("androidUseSwappy"), False)},
       {"default_quality": lv.get("name"), "quality_levels": levels,
        "target_frame_rate_assignments": _assign_list(src.get("target_frame_rate", [])),
        "vsync_assignments": _assign_list(src.get("vsync_count", [])),
        "rule": "target_fps는 코드의 Application.targetFrameRate 대입이 상수 하나일 때만 값, 아니면 null"})
    ok("CPU-03", {"incremental_gc": Y.flag(P.get("gcIncremental"), True)})
    lcm = s.physics.get("m_LayerCollisionMatrix")
    ok("CPU-06", {"auto_sync_transforms": Y.flag(s.physics.get("m_AutoSyncTransforms"), False),
                  "reuse_collision_callbacks": Y.flag(s.physics.get("m_ReuseCollisionCallbacks"), True),
                  "prebake_collision_meshes": Y.flag(P.get("bakeCollisionMeshes"), False),
                  "layer_collision_matrix": _short_sha(lcm) if lcm else None},
       {"layer_collision_matrix_enabled_bits": _bits(lcm)})
    ok("CPU-08", {"deformation_mode": DEFORMATION.get(Y.num(P.get("meshDeformation")), P.get("meshDeformation"))},
       {"gpu_skinning": Y.flag(P.get("gpuSkinning"))})
    ok("CPU-13", {"upload_settings": {"time_slice_ms": Y.num(lv.get("asyncUploadTimeSlice")),
                                      "buffer_mb": Y.num(lv.get("asyncUploadBufferSize")),
                                      "persistent_buffer": Y.flag(lv.get("asyncUploadPersistentBuffer"))}},
       {"level": lv.get("name")})
    blp = _assign_list(src.get("background_loading_priority", []))
    ok("CPU-14", {"value": sorted({a["value"] for a in blp})}, {"assignments": blp,
                                                                "rule": "코드의 Application.backgroundLoadingPriority 대입 값 목록"})
    ok("MEM-15", {"variant_chunk_settings": {"chunk_size_mb": Y.num(P.get("m_DefaultShaderChunkSizeInMB")),
                                             "chunk_count": Y.num(P.get("m_DefaultShaderChunkCount")),
                                             "per_platform": P.get("m_BuildTargetShaderSettings") or []}})
    ok("BLD-04", {"value": s.default_texture_compression()})
    api = Y.num(P.get("AndroidTargetSdkVersion"))
    ok("BLD-06", {"target_api": api if api else None},
       {"min_api": Y.num(P.get("AndroidMinSdkVersion")),
        "note": "0이면 「설치된 가장 높은 SDK」(자동): 값은 null, 빌드 산출물에서 확인"} if not api else
       {"min_api": Y.num(P.get("AndroidMinSdkVersion"))})
    conf = _plat(P.get("il2cppCompilerConfiguration"))
    gen = _plat(P.get("il2cppCodeGeneration"))
    strip = _plat(P.get("managedStrippingLevel"))
    ok("BLD-07", {"il2cpp_settings": {
        "scripting_backend": {"0": "Mono", "1": "IL2CPP"}.get(str(_plat(P.get("scriptingBackend"))), "default"),
        "compiler_configuration": IL2CPP_CONFIG.get(Y.num(conf), "default(Release)") if conf is not None else "default(Release)",
        "code_generation": IL2CPP_CODEGEN.get(Y.num(gen), "?") if gen is not None else "default(OptimizeSpeed)",
        "managed_stripping": STRIPPING.get(Y.num(strip), "?") if strip is not None else "default",
        "strip_engine_code": Y.flag(P.get("stripEngineCode")),
        "lto": _plat(P.get("il2cppLTOMode"))}})
    arch = Y.num(P.get("AndroidTargetArchitectures"), 0)
    ok("BLD-08", {"arm64_only": arch == 2}, {"architectures": [n for b, n in ARCH_BITS.items() if arch & b]})
    variant = _plat(P.get("managedCodeVariant"))
    ok("BLD-09", {"value": {"managed_code_variant": CODE_VARIANT.get(Y.num(variant), "default(Release)")
                            if variant is not None else "default(Release)",
                            "frame_timing_stats": Y.flag(P.get("enableFrameTimingStats"), False)}},
       partial=["script_debugging·development: 빌드 프로필 값(에디터)"])
    apis = next((r for r in (P.get("m_BuildTargetGraphicsAPIs") or []) if isinstance(r, dict)
                 and r.get("m_BuildTarget") == "AndroidPlayer"), {})
    deny = P.get("androidVulkanDenyFilterList")
    ok("BLD-13", {"value": {"apis": [GRAPHICS_APIS.get(v, f"api{v}") for v in Y.hex_ints(apis.get("m_APIs", ""))],
                            "automatic": Y.flag(apis.get("m_Automatic"), True),
                            "vulkan_deny_list": len(deny) if isinstance(deny, list) else 0,
                            "vulkan_allow_list": len(P.get("androidVulkanAllowFilterList") or [])
                            if isinstance(P.get("androidVulkanAllowFilterList"), list) else 0,
                            "device_filter_asset": bool((P.get("androidVulkanDeviceFilterListAsset") or {}).get("guid"))
                            if isinstance(P.get("androidVulkanDeviceFilterListAsset"), dict) else False}})
    ok("THM-04", {"value": _adaptive_performance(s.p)})

    # ---- URP
    urp = s.urp()
    if not urp:
        for iid in ("GPU-06", "GPU-12", "GPU-17", "GPU-19", "GPU-20", "GPU-23"):
            res[iid] = {"status": "not_collected", "reason": "URP 에셋을 찾지 못함(GraphicsSettings·QualitySettings 참조)",
                        "collector": "static.urp_settings"}
    else:
        def per_asset(iid, extra=None):
            rows = []
            for a in urp:
                row = {"path": a["path"], "used_by": a["used_by"], **_pick(a["body"], URP_KEYS.get(iid, []))}
                if iid in RENDERER_KEYS:
                    row["renderers"] = [{"path": r["path"], **_named(_pick(r["body"], RENDERER_KEYS[iid]))}
                                        for r in a["renderers"]]
                if extra:
                    row.update(extra(a))
                rows.append(row)
            return rows

        gs = {"player_resolution_scaling_mode": Y.num(P.get("resolutionScalingMode")),
              "player_target_dpi": Y.num(P.get("targetPixelDensity"))}
        ok("GPU-06", {"value": per_asset("GPU-06")}, {"instancing_materials": src.get("instancing_materials")},
           collector="static.urp_settings", partial=["dynamic_batching_candidates·GRD 대상 렌더러: 씬 분석(에디터)"])
        ok("GPU-12", {"value": {"assets": per_asset("GPU-12"), **gs}}, collector="static.urp_settings",
           partial=["camera_dynamic_resolution: 카메라별 설정(씬·프리팹)"])
        ok("GPU-17", {"value": per_asset("GPU-17")}, collector="static.urp_settings")
        ok("GPU-19", {"shadow_settings": per_asset("GPU-19")}, collector="static.urp_settings",
           partial=["shadow_casters: 그림자 드리우는 렌더러 수(씬)"])
        ok("GPU-20", {"value": [{"path": a["path"], "renderer": r["path"], "name": f["name"], "active": f["active"],
                                 "settings": f["settings"]}
                                for a in urp for r in a["renderers"] for f in r["features"]
                                if f["settings"] and ("Downsample" in f["settings"] or "AOMethod" in f["settings"]
                                                      or "BlurQuality" in f["settings"])]},
           {"note": "SSAO 렌더러 기능(설정에 Downsample·AOMethod·BlurQuality가 있는 기능)"}, collector="static.urp_settings")
        ok("GPU-23", {"value": per_asset("GPU-23")}, collector="static.urp_settings")
        res["GPU-21"] = {"status": "ok", "collector": "static.urp_settings",
                         "values": {"effects": [{"path": v["path"],
                                                 "active": [c["name"] for c in v["components"] if c["active"]]}
                                                for v in s.volume_profiles()]},
                         "detail": {"renderer_features": sorted({f"{f['name']}:{'on' if f['active'] else 'off'}"
                                                                 for a in urp for r in a["renderers"] for f in r["features"]})},
                         "editor_needed": ["post_passes: 실제 후처리 패스 수(프레임 디버거, L2)"]}

    # ---- 렌더 에셋
    mats = src.get("materials") or []
    res["GPU-10"] = {"status": "ok", "collector": "static.render_assets",
                     "values": {"alpha_clip_materials": sum(1 for m in mats if m.get("alpha_clip"))},
                     "detail": {"materials": len(mats), "transparent_materials": sum(1 for m in mats if m.get("transparent"))},
                     "violations": sorted(m["path"] for m in mats if m.get("alpha_clip"))}
    editor("GPU-05", "SRP Batcher 호환 여부는 셰이더 컴파일 정보(ShaderUtil)가 있어야 앎", "static.render_assets")
    editor("GPU-11", "투명 드로콜·파티클 수는 씬·프레임 분석", "static.render_assets")
    editor("GPU-14", "셰이더 정밀도 사용은 컴파일된 셰이더(또는 셰이더 그래프 정밀도 분석) 필요", "static.render_assets")
    # GPU-18: 소스의 렌더 패스
    rp = src.get("render_passes") or []
    res["GPU-18"] = {"status": "ok", "collector": "static.source_scan",
                     "values": {"legacy_api_features": sum(1 for _, ex, rg in rp if ex and not rg)},
                     "detail": {"render_passes": [{"class": c, "execute": ex, "record_render_graph": rg}
                                                  for c, ex, rg in sorted(rp)],
                                "rule": "ScriptableRenderPass 파생 클래스 중 Execute만 있고 RecordRenderGraph가 없는 수"},
                     "editor_needed": ["merged_passes: Render Graph 뷰어(L2)"]}
    editor("CPU-10", "UI 컴포넌트 판정(상호작용 여부·LayoutGroup 중첩)은 타입 정보가 필요", "static.ui_scan")
    editor("THM-06", "팝업·정적 화면 프레임 정책은 씬·코드 흐름 분석", "static.ui_scan")
    editor("MEM-09", "Addressables Analyze 「Check Duplicate Bundle Dependencies」 규칙(빌드 산출물은 L1 build.layout)",
           "static.addressables_dupes")
    return res


def _named(d: dict) -> dict:
    out = dict(d)
    for k, table in (("m_RenderingMode", RENDERING_MODE), ("m_DepthPrimingMode", DEPTH_PRIMING),
                     ("m_IntermediateTextureMode", INTERMEDIATE), ("m_CopyDepthMode", COPY_DEPTH)):
        if k in out and isinstance(out[k], int):
            out[k] = table.get(out[k], out[k])
    return out


def _bits(v) -> int | None:
    if not isinstance(v, list):
        return None
    total = 0
    for row in v:
        try:
            total += bin(int(str(row), 16)).count("1")
        except ValueError:
            pass
    return total


def _assign_list(rows) -> list[dict]:
    return [{"value": v, "path": p, "line": ln} for v, p, ln in sorted(rows, key=lambda r: (r[1], r[2], r[0]))]


def _adaptive_performance(p: Project) -> dict:
    out = {"packages": {}, "settings_assets": []}
    man = p.root / "Packages" / "manifest.json"
    if man.exists():
        try:
            deps = json.loads(man.read_text(encoding="utf-8-sig")).get("dependencies", {})
            out["packages"] = {k: v for k, v in sorted(deps.items()) if "adaptiveperformance" in k}
        except ValueError:
            out["packages"] = {"error": "manifest.json 읽기 실패"}
    for rel in p.files:
        if rel.endswith(".asset") and "adaptiveperformance" in rel.lower().replace(" ", ""):
            out["settings_assets"].append(rel)
    return out


# --------------------------------------------------------------------------- 설정 규칙(SCR-09)

def settings_rules(s: Settings, res: dict) -> dict:
    """Project Auditor 설정 규칙(PAS*) 중 이 도구가 값으로 판정할 수 있는 것.

    규칙 패키지가 없어 Auditor의 정확한 정의를 읽을 수 없으므로, 매니페스트 문구가 직접 가리키는 규칙만
    여기서 검사한다. 정의를 모르는 규칙은 `unknown`으로 두고 에디터(규칙 패키지) 몫으로 남긴다.
    """
    v = lambda iid, k: (res.get(iid, {}).get("values") or {}).get(k)
    lv = s.default_level
    srp = None
    for a in s.urp():
        val = Y.num(a["body"].get("m_UseSRPBatcher"))
        if val is not None:
            srp = bool(val) if srp is None else (srp and bool(val))
    conf = _plat(s.player.get("il2cppCompilerConfiguration"))
    checks = [
        ("PAS0012", "Auto Sync Transforms 켜짐", v("CPU-06", "auto_sync_transforms") is True),
        ("PAS0028", "Reuse Collision Callbacks 꺼짐", v("CPU-06", "reuse_collision_callbacks") is False),
        ("PAS0030", "Optimized Frame Pacing 꺼짐", v("FRM-05", "optimized_frame_pacing") is False),
        ("PAS0034", "증분 GC 꺼짐", v("CPU-03", "incremental_gc") is False),
        ("PAS0020", "Async Upload Time Slice 기본값(2ms)", Y.num(lv.get("asyncUploadTimeSlice")) == 2),
        ("PAS0021", "Async Upload Buffer Size 기본값(16MB)", Y.num(lv.get("asyncUploadBufferSize")) == 16),
        ("PAS1004", "IL2CPP 구성 Debug", Y.num(conf) == 0 if conf is not None else False),
        ("PAS1008", "SRP Batcher 꺼짐", srp is False),
    ]
    rows = [{"id": i, "rule": t, "failed": bool(f)} for i, t, f in checks]
    unknown = ["PAS0014", "PAS0016", "PAS0017"]
    return {"status": "ok", "collector": "static.auditor_settings",
            "values": {"value": sum(r["failed"] for r in rows)},
            "detail": {"checks": rows, "not_checked": unknown,
                       "note": "규칙 정의는 매니페스트 문구 기준. PAS0020/0021은 기본값을 경고로 보는 해석(Auditor 원문 확인 필요)"},
            "editor_needed": [f"{u}: 규칙 정의(규칙 패키지)" for u in unknown]}
