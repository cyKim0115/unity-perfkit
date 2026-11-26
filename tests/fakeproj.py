"""테스트용 가짜 Unity 프로젝트·가짜 APK·AAB를 만드는 도우미(표준 라이브러리만)."""

from __future__ import annotations

import contextlib
import struct
import tempfile
import wave
import zipfile
import zlib
from pathlib import Path

from perfkit import config

PLAYER_CS = """using System.Linq;
using UnityEngine;

public class Player : MonoBehaviour
{
    private const string K = "a" + "b";
    private string[] _names;

    void Update()
    {
        var r = GetComponent<Rigidbody>();
        var c = Camera.main;
        var n = _names.Where(x => x != null).ToList();
        Debug.Log("tick " + Time.frameCount);
        // Camera.main in comment
        var s = "Camera.main";
        var arr = new int[10];
        var m = GetComponent<Renderer>().material;
        var f = Mathf.Max(1, 2);
    }

    void LateUpdate() { }

    void Start()
    {
        var t = gameObject.tag == "x";
        var o = FindObjectOfType<Player>();
        var v = GetComponent<MeshFilter>().mesh.vertices;
        SendMessage("Hi");
        var mi = typeof(Player).GetMethod("Start");
        GetComponent<Renderer>().material = null;
    }
#if UNITY_EDITOR
    void OnValidate() { Debug.Log("editor"); var c = Camera.main; }
#endif

    [System.Diagnostics.Conditional("DEV_LOG")]
    static void Dbg(string s) { Debug.Log(s); }
}
"""

EDITOR_CS = """using UnityEditor;
using UnityEngine;

public class ImportHook : AssetPostprocessor
{
    void OnPreprocessTexture() { Debug.Log(Camera.main); }
}
"""

PROJECT_SETTINGS = """%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!129 &1
PlayerSettings:
  m_ObjectHideFlags: 0
  androidUseSwappy: 1
  bakeCollisionMeshes: 1
  gpuSkinning: 1
  meshDeformation: 2
  enableFrameTimingStats: 0
  AndroidMinSdkVersion: 26
  AndroidTargetSdkVersion: 35
  stripEngineCode: 1
  AndroidTargetArchitectures: 3
  m_BuildTargetGraphicsAPIs:
  - m_BuildTarget: AndroidPlayer
    m_APIs: 150000000b000000i
    m_Automatic: 0
  m_DefaultShaderChunkSizeInMB: 16
  m_DefaultShaderChunkCount: 0
  m_BuildTargetDefaultTextureCompressionFormat:
  - serializedVersion: 3
    m_BuildTarget: Android
    m_Formats: 03000000i
  scriptingBackend:
    Android: 1
  il2cppCompilerConfiguration: {}
  managedStrippingLevel:
    Android: 1
  gcIncremental: 0
  managedCodeVariant: {}
  androidVulkanDenyFilterList: []
"""

QUALITY = """%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!47 &1
QualitySettings:
  m_CurrentQuality: 0
  m_QualitySettings:
  - serializedVersion: 5
    name: Mobile
    vSyncCount: 0
    streamingMipmapsActive: 0
    streamingMipmapsMemoryBudget: 512
    asyncUploadTimeSlice: 4
    asyncUploadBufferSize: 32
    asyncUploadPersistentBuffer: 1
    customRenderPipeline: {fileID: 11400000, guid: 1111111111111111111111111111aaaa, type: 2}
  m_PerPlatformDefaultQuality:
    Android: 0
"""

GRAPHICS = """%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!30 &1
GraphicsSettings:
  m_CustomRenderPipeline: {fileID: 11400000, guid: 1111111111111111111111111111aaaa, type: 2}
"""

DYNAMICS = """%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!55 &1
PhysicsManager:
  m_AutoSyncTransforms: 1
  m_ReuseCollisionCallbacks: 1
  m_LayerCollisionMatrix: ffffffffffffffff
"""

URP_ASSET = """%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!114 &11400000
MonoBehaviour:
  m_Name: URP
  m_RendererDataList:
  - {fileID: 11400000, guid: 2222222222222222222222222222bbbb, type: 2}
  m_RequireDepthTexture: 1
  m_MSAA: 4
  m_RenderScale: 0.8
  m_UseSRPBatcher: 0
  m_ShadowDistance: 30
  m_AdditionalLightsRenderingMode: 1
"""

RENDERER = """%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!114 &-111
MonoBehaviour:
  m_Name: SSAO
  m_Active: 1
  m_Settings:
    AOMethod: 1
    Downsample: 1
    Samples: 1
--- !u!114 &11400000
MonoBehaviour:
  m_Name: Renderer
  m_RendererFeatures:
  - {fileID: -111}
  m_RenderingMode: 0
  m_DepthPrimingMode: 0
"""


FONT_ASSET_HEAD = """--- !u!28 &1
Texture2D:
  m_Name: F Atlas
  image data: 4096
  _typelessdata: """

FONT_ASSET_TAIL = """
--- !u!114 &2
MonoBehaviour:
  m_Name: F SDF
  m_AtlasPopulationMode: 1
  m_ClearDynamicDataOnBuild: 0
  m_AtlasWidth: 1024
  m_AtlasHeight: 1024
  m_FallbackFontAssetTable: []
"""


def png_bytes(w: int, h: int) -> bytes:
    raw = b"".join(b"\x00" + b"\x00\x00\x00\x00" * w for _ in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def tex_meta(guid: str, *, readable: int, ttype: int, mip: int, android_override: bool, fmt: int = -1,
             default_compression: int = 1, npot_scale: int = 1) -> str:
    plat = lambda target, over, f, comp: f"""  - serializedVersion: 4
    buildTarget: {target}
    maxTextureSize: 2048
    textureFormat: {f}
    textureCompression: {comp}
    crunchedCompression: 0
    overridden: {over}
"""
    return f"""fileFormatVersion: 2
guid: {guid}
TextureImporter:
  internalIDToNameTable: []
  mipmaps:
    mipMapMode: 0
    enableMipMap: {mip}
  isReadable: {readable}
  streamingMipmaps: 0
  maxTextureSize: 2048
  textureSettings:
    serializedVersion: 2
    aniso: 1
  nPOTScale: {npot_scale}
  textureType: {ttype}
  platformSettings:
{plat('DefaultTexturePlatform', 0, -1, default_compression)}{plat('Android', 1 if android_override else 0, fmt, 1)}  spriteSheet:
    sprites: []
"""


MODEL_META = """fileFormatVersion: 2
guid: 3333333333333333333333333333cccc
ModelImporter:
  animations:
    optimizeGameObjects: 0
    animationCompression: 0
    animationRotationError: 0.5
    animationPositionError: 0.5
    animationScaleError: 0.5
    extraExposedTransformPaths: []
    clipAnimations: []
    isReadable: 1
  meshes:
    meshCompression: 0
    importBlendShapes: 1
    indexFormat: 2
  tangentSpace:
    normalImportMode: 0
    tangentImportMode: 3
    blendShapeNormalImportMode: 1
  importAnimation: 1
  animationType: 2
"""


def audio_meta(guid: str, load_type: int, fmt: int) -> str:
    return f"""fileFormatVersion: 2
guid: {guid}
AudioImporter:
  defaultSettings:
    serializedVersion: 2
    loadType: {load_type}
    sampleRateSetting: 0
    compressionFormat: {fmt}
    preloadAudioData: 0
  platformSettingOverrides: {{}}
  forceToMono: 0
"""


def write_wav(path: Path, seconds: float, channels: int, rate: int = 8000) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * channels * int(seconds * rate))


def make_project(root: Path) -> Path:
    def w(rel, text, mode="w"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if mode == "wb":
            p.write_bytes(text)
        else:
            p.write_text(text, encoding="utf-8", newline="\n")

    w("ProjectSettings/ProjectSettings.asset", PROJECT_SETTINGS)
    w("ProjectSettings/QualitySettings.asset", QUALITY)
    w("ProjectSettings/GraphicsSettings.asset", GRAPHICS)
    w("ProjectSettings/DynamicsManager.asset", DYNAMICS)
    w("Assets/Scripts/Player.cs", PLAYER_CS)
    w("Assets/Scripts/Editor/ImportHook.cs", EDITOR_CS)
    w("Assets/Tex/a.png", png_bytes(3, 5), "wb")
    w("Assets/Tex/a.png.meta", tex_meta("4444444444444444444444444444dddd", readable=1, ttype=0, mip=0,
                                        android_override=True, fmt=4, npot_scale=0))
    w("Assets/Tex/b.png", png_bytes(4, 4), "wb")
    w("Assets/Tex/b.png.meta", tex_meta("5555555555555555555555555555eeee", readable=0, ttype=8, mip=1,
                                        android_override=False))
    w("Assets/Mdl/m.fbx", b"fake", "wb")
    w("Assets/Mdl/m.fbx.meta", MODEL_META)
    (root / "Assets/Snd").mkdir(parents=True, exist_ok=True)
    write_wav(root / "Assets/Snd/long.wav", 11.0, 1)
    w("Assets/Snd/long.wav.meta", audio_meta("6666666666666666666666666666ffff", 0, 0))
    write_wav(root / "Assets/Snd/short.wav", 0.5, 2)
    w("Assets/Snd/short.wav.meta", audio_meta("7777777777777777777777777777aaaa", 0, 2))
    w("Assets/Settings/URP.asset", URP_ASSET)
    w("Assets/Settings/URP.asset.meta", "fileFormatVersion: 2\nguid: 1111111111111111111111111111aaaa\n")
    w("Assets/Settings/Renderer.asset", RENDERER)
    w("Assets/Settings/Renderer.asset.meta", "fileFormatVersion: 2\nguid: 2222222222222222222222222222bbbb\n")
    w("Assets/Font/F SDF.asset", FONT_ASSET_HEAD + "00" * 4000 + FONT_ASSET_TAIL)
    w("Assets/Mat/clip.mat", "Material:\n  m_ValidKeywords:\n  - _ALPHATEST_ON\n  m_Floats:\n    - _Surface: 0\n")
    return root


PROFILE = """name = "test-profile"

[paths]
results = "results"

[[code_rules]]
id = "find_object_of_type"
pattern = "FindObjectOfType"
note = "test"
"""


@contextlib.contextmanager
def isolated_settings(profile_text: str = PROFILE):
    """사용자 설정 없이 임시 프로필만 쓰는 설정으로 바꿨다가 되돌린다."""
    old = config._CACHE
    with tempfile.TemporaryDirectory() as td:
        prof = Path(td) / "profile.toml"
        prof.write_text(profile_text, encoding="utf-8")
        config._CACHE = config.load(user_path=Path(td) / "no-user.toml", profile_path=prof, env={})
        try:
            yield Path(td)
        finally:
            config._CACHE = old


# --------------------------------------------------------------------------- ELF·APK

def elf64(align: int, loads: int = 2) -> bytes:
    phnum = loads
    hdr = b"\x7fELF\x02\x01\x01" + b"\x00" * 9
    hdr += struct.pack("<HHIQQQIHHHHHH", 3, 183, 1, 0, 64, 0, 0, 64, 56, phnum, 0, 0, 0)
    ph = b"".join(struct.pack("<IIQQQQQQ", 1, 5, 0, 0, 0, 0x100, 0x100, align) for _ in range(loads))
    return hdr + ph + b"\x00" * 256


def elf32(align: int) -> bytes:
    hdr = b"\x7fELF\x01\x01\x01" + b"\x00" * 9
    hdr += struct.pack("<HHIIIIIHHHHHH", 3, 40, 1, 0, 52, 0, 0, 52, 32, 1, 0, 0, 0)
    ph = struct.pack("<IIIIIIII", 1, 0, 0, 0, 0x100, 0x100, 5, align)
    return hdr + ph + b"\x00" * 256


def _write_aligned(z: zipfile.ZipFile, name: str, data: bytes, align: int) -> None:
    info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    off = z.fp.tell() + 30 + len(name.encode())
    pad = (-off) % align
    if pad and pad < 4:
        pad += align
    if pad:
        info.extra = struct.pack("<HH", 0xD935, pad - 4) + b"\x00" * (pad - 4)
    z.writestr(info, data)


def make_apk(path: Path, *, bad_align: bool = False, misaligned_zip: bool = False) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(zipfile.ZipInfo("AndroidManifest.xml", (2026, 1, 1, 0, 0, 0)), b"manifest" * 10,
                   compress_type=zipfile.ZIP_DEFLATED)
        _write_aligned(z, "resources.arsc", b"res" * 7, 4)
        if misaligned_zip:
            info = zipfile.ZipInfo("lib/arm64-v8a/libstored.so", (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            z.writestr(info, elf64(0x4000))
        else:
            _write_aligned(z, "lib/arm64-v8a/libstored.so", elf64(0x4000), 16384)
        for name, data in (("lib/arm64-v8a/libil2cpp.so", elf64(0x4000) + b"\x01" * 2_000_000),
                           ("lib/arm64-v8a/libsdk.so", elf64(0x1000 if bad_align else 0x4000)),
                           ("lib/armeabi-v7a/libold.so", elf32(0x1000)),
                           ("assets/aa/Android/group.bundle", b"\x02" * 50_000)):
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return path


def make_aab(path: Path) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name, data in (("BundleConfig.pb", b"\x00"), ("base/manifest/AndroidManifest.xml", b"base"),
                           ("base/lib/arm64-v8a/libil2cpp.so", elf64(0x4000) + b"\x03" * 1_000_000),
                           ("base/lib/armeabi-v7a/libil2cpp.so", elf32(0x4000) + b"\x04" * 1_000_000),
                           ("base/assets/data.bin", b"\x05" * 10_000),
                           ("pack1/manifest/AndroidManifest.xml", b"\x0a\x0cinstall-time\x00"),
                           ("pack1/assets/big.bin", bytes(range(256)) * 4000),
                           ("pack2/manifest/AndroidManifest.xml", b"\x0a\x09on-demand"),
                           ("pack2/assets/x.bin", b"\x06" * 1000)):
            z.writestr(zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0)), data)
    return path


SHADER_LOG = """Some line
Compiling shader "Custom/A"
  Pass "Forward" (vp)
    Target graphics API: gles3
    Full variant space:         1.024
    After settings filtering:   64
    After built-in stripping:   32
    After scriptable stripping: 16
    Processed in 0.01 seconds
    Target graphics API: vulkan
    Full variant space:         1.024
    After settings filtering:   64
    After built-in stripping:   32
    After scriptable stripping: 12
  Pass "Shadow" (vp)
    Target graphics API: vulkan
    Full variant space:         8
    After settings filtering:   8
    After built-in stripping:   4
    After scriptable stripping: 2
Compiling shader "Custom/A"
  Pass "Forward" (vp)
    Target graphics API: gles3
    Full variant space:         1.024
    After settings filtering:   64
    After built-in stripping:   32
    After scriptable stripping: 16
Compiling shader "Old/B" pass "P0" (fp)
    Full variant space:         4
    After settings filtering:   4
    After built-in stripping:   4
    After scriptable stripping: 3
"""


def layout_json() -> dict:
    def ref(rid, cls, data):
        return {"rid": rid, "type": {"class": f"BuildLayout/{cls}"}, "data": data}

    def obj(lid, name, t, ser, streamed=0):
        return {"LocalIdentifierInFile": lid, "ObjectName": name, "AssetType": t, "SerializedSize": ser,
                "StreamedSize": streamed}

    refs = [
        ref(1, "Bundle", {"Name": "group_assets_all_0123456789abcdef0123456789abcdef.bundle", "FileSize": 3_000_000,
                          "Compression": "LZ4", "AssetCount": 3}),
        ref(2, "ExplicitAsset", {"Guid": "g1", "AssetPath": "Assets/Tex/big.png", "MainAssetType": 13,
                                 "Objects": [obj(1, "big", 13, 200, 2_000_000)]}),
        ref(3, "ExplicitAsset", {"Guid": "g2", "AssetPath": "Assets/Mdl/m.fbx", "MainAssetType": 18,
                                 "Objects": [obj(2, "m", 11, 500_000), obj(3, "Take", 3, 100_000)]}),
        ref(4, "ExplicitAsset", {"Guid": "g3", "AssetPath": "Assets/Font/F SDF.asset", "MainAssetType": 16,
                                 "Objects": [obj(4, "F SDF", 16, 10_000), obj(5, "F SDF Atlas", 13, 100, 1_000_000)]}),
        ref(5, "DataFromOtherAsset", {"AssetGuid": "g4", "AssetPath": "Assets/Mat/shared.mat", "MainAssetType": 19,
                                      "Objects": [obj(6, "shared", 19, 300_000)]}),
    ]
    return {"BuildTarget": 13, "UnityVersion": "6000.0.0f1", "PackageVersion": "com.unity.addressables: 2.0.0",
            "BuildStartTime": "2026/01/01 00:00:00",
            "DuplicatedAssets": [{"AssetGuid": "g4", "DuplicatedObjects": [
                {"LocalIdentifierInFile": 6, "IncludedInBundleFiles": [{"rid": 10}, {"rid": 11}, {"rid": 12}]}]}],
            "references": {"version": 2, "RefIds": refs}}
