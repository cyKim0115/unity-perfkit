"""기기 설정 합치기. 기기마다 다른 값(serial·adb·sysfs 경로·식힘 문턱·AVD 기대값)은 코드에 두지 않는다.

찾는 순서(뒤가 이김)
1. 저장소 `config/devices/<preset>.json` — SoC·기기 종류별 일반 값(공개). preset은 기기 정의의 `preset`,
   없으면 kind(`emulator`·`device`)의 기본 preset(`emulator`, `android-device`)
2. 프로젝트 프로필 `[devices.<이름>]`(모든 칸: kind·preset·sysfs·thermal·cool·prep·expect·host_gate …)
3. 사용자 설정 `[devices.<이름>]`(serial·adb 등 PC 고유 값)
4. 환경 변수 `PERFKIT_DEVICE_<이름>_SERIAL`

`perfkit.config.Settings.devices()`는 공용 칸만 돌려주므로 여기서는 원본 dict를 직접 합친다.
회차 공통 값(패키지·액티비티·세이브 폴더·샘플 간격 등)은 프로필 `[run]`(사용자 설정 `[run]`이 덮음).
"""

from __future__ import annotations

import json
from pathlib import Path

from perfkit.config import ConfigError, Settings, settings as get_settings
from perfkit.paths import REPO_ROOT

PRESET_DIR = REPO_ROOT / "config" / "devices"
DEFAULT_PRESET = {"emulator": "emulator", "device": "android-device"}

# [run] 기본값(프로젝트와 무관한 것만)
RUN_DEFAULTS = {
    "activity": "com.unity3d.player.UnityPlayerGameActivity",
    "seconds": 60,
    "seed": 1,
    "sample_interval_s": 5.0,
    "probe_wait_s": 120.0,        # 실행 뒤 측정 모드 BEGIN을 기다리는 시간. 넘으면 호환 모드
    "load_timeout_s": 600.0,      # BEGIN 뒤 END까지 허용 = seconds + 이 값
    "after_end_wait_s": 20.0,     # END 뒤 앱이 스스로 닫히길 기다리는 시간(quit 켬일 때)
    "csv_stable_s": 2.0,          # 기기 CSV 크기가 이만큼 그대로면 끝으로 봄
    "quit_after_end": False,      # False면 END 뒤 meminfo를 잡고 perfkit이 앱을 닫는다
    "logcat_buffer": "16M",
    "logcat_buffers": "main,system,crash",
    "remote_files": "/sdcard/Android/data/{package}/files",
    "remote_perf_dir": "{files}/perf",
    "remote_saves_dir": "{files}/perf/saves",
    "install_timeout_s": 900,
    # -d: 버전 코드가 낮은 기준 APK(ABAB의 옛 빌드)도 덮어 설치(디버그 가능 기기·앱에서만 됨)
    "install_args": ["-r", "-d"],
    "extra_args": "",
    "cold_start_count": 10,
}

BATCH_BUILD_RE = r"(?i)^(?=.*-batchmode)(?=.*(-executeMethod|-buildTarget))(?!.*AssetImportWorker)"

# 호스트 유휴 관문 기본(에뮬레이터). 문턱 정본은 프로필 [compare."L3-E".gate](비교와 같은 값)
GATE_DEFAULTS = {
    "host_busy_before_max": 15.0,
    "host_busy_during_max": 25.0,
    "other_unity_pct_max": 1.0,
    "logcat_restarts_max": 0,
}
HOST_DEFAULTS = {
    "idle_seconds": 10.0,
    "emulator_images": ["qemu-system-x86_64.exe", "qemu-system-aarch64.exe", "qemu-system-x86_64"],
    "editor_images": ["Unity.exe", "Unity"],
    # 이 프로세스가 이 명령줄 정규식으로 돌고 있으면 회차를 시작하지 않는다(배치 빌드 등)
    # 에디터의 임포트 워커(AssetImportWorker)도 -batchMode로 뜨므로 빌드 메서드·빌드 대상이 있는 것만 본다
    "busy": [{"image": img, "cmdline": BATCH_BUILD_RE} for img in ("Unity.exe", "Unity")],
    # 이 파일(glob)이 있으면 시작하지 않는다(빌드 도구의 잠금 파일 등). 프로필에서 정한다
    "busy_files": [],
}


def deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_preset(name: str) -> dict:
    p = PRESET_DIR / f"{name}.json"
    if not p.exists():
        raise ConfigError(f"기기 preset이 없음: {p} (있는 것: {sorted(x.stem for x in PRESET_DIR.glob('*.json'))})")
    doc = json.loads(p.read_text(encoding="utf-8"))
    parent = doc.pop("extends", None)
    if parent:
        doc = deep_merge(load_preset(parent), doc)
    doc.pop("$comment", None)
    return doc


def device_names(s: Settings | None = None) -> list[str]:
    s = s or get_settings()
    return sorted(set((s.profile.get("devices") or {})) | set((s.user.get("devices") or {})))


def device_config(name: str, s: Settings | None = None) -> dict:
    """기기 하나의 합친 설정. `name` 칸과 `kind`(device·emulator)가 늘 있다."""
    s = s or get_settings()
    prof = (s.profile.get("devices") or {}).get(name)
    user = (s.user.get("devices") or {}).get(name)
    if prof is None and user is None:
        raise ConfigError(f"기기 {name}이 프로필·사용자 설정에 없음(있는 것: {device_names(s)})")
    merged_raw = deep_merge(prof or {}, user or {})
    kind = merged_raw.get("kind") or "device"
    if kind not in ("device", "emulator"):
        raise ConfigError(f"기기 {name}: kind는 device 또는 emulator ({kind})")
    preset = merged_raw.get("preset") or DEFAULT_PRESET[kind]
    cfg = deep_merge(load_preset(preset), merged_raw)
    env = s.env.get(f"PERFKIT_DEVICE_{name.upper().replace('-', '_')}_SERIAL")
    if env:
        cfg["serial"] = env
    cfg["name"] = name
    cfg["kind"] = kind
    cfg["preset"] = preset
    return cfg


def run_config(s: Settings | None = None) -> dict:
    s = s or get_settings()
    cfg = deep_merge(RUN_DEFAULTS, s.profile.get("run") or {})
    cfg = deep_merge(cfg, s.user.get("run") or {})
    return cfg


def gate_config(layer: str, s: Settings | None = None, dev: dict | None = None) -> dict:
    """관문 문턱: 기본 ← 프로필 [compare."<층>".gate] ← 기기 설정 gate."""
    s = s or get_settings()
    cmp = (s.profile.get("compare") or {}).get(layer) or {}
    base = dict(GATE_DEFAULTS) if layer == "L3-E" else {"logcat_restarts_max": 0}
    base.update(cmp.get("gate") or {})
    base.update((dev or {}).get("gate") or {})
    return base


def host_config(s: Settings | None = None, dev: dict | None = None) -> dict:
    s = s or get_settings()
    cfg = deep_merge(HOST_DEFAULTS, s.profile.get("host") or {})
    cfg = deep_merge(cfg, s.user.get("host") or {})
    return deep_merge(cfg, (dev or {}).get("host") or {})


def resolve_path(value: str | None, s: Settings | None = None) -> Path | None:
    """프로필 [run]의 상대 경로는 프로필 폴더 기준."""
    if not value:
        return None
    p = Path(value).expanduser()
    if p.is_absolute():
        return p
    s = s or get_settings()
    base = s.profile_path.parent if s.profile_path else Path.cwd()
    return (base / p).resolve()


def adb_path(dev: dict | None = None, s: Settings | None = None) -> str:
    """기기 설정 adb → 사용자 설정 tools.adb → Android SDK의 platform-tools → PATH의 adb."""
    s = s or get_settings()
    if dev and dev.get("adb"):
        return str(dev["adb"])
    v, src = s.lookup("tools.adb")
    if src != "default" and v:
        return str(v)
    sdk = s.get("tools.android_sdk")
    if sdk:
        for name in ("adb.exe", "adb"):
            p = Path(sdk) / "platform-tools" / name
            if p.exists():
                return str(p)
    return str(v or "adb")


def package_name(run_cfg: dict, s: Settings | None = None, apk_package: str | None = None,
                 override: str | None = None) -> str:
    """--package → APK의 패키지 → [run] package → app.package(+ [run] use_dev_suffix면 app.dev_suffix)."""
    if override:
        return override
    if apk_package:
        return apk_package
    if run_cfg.get("package"):
        return str(run_cfg["package"])
    s = s or get_settings()
    pkg = s.get("app.package")
    if not pkg:
        raise ConfigError("패키지 이름이 없음: --package, APK, 프로필 [run] package 또는 app.package")
    if run_cfg.get("use_dev_suffix") and s.get("app.dev_suffix"):
        pkg += s.get("app.dev_suffix")
    return pkg
