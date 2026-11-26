"""설정 읽기. PC·프로젝트마다 다른 값은 코드에 두지 않고 여기서 읽는다.

값을 찾는 순서(앞이 이김)
1. 환경 변수(`PERFKIT_*`, 아래 KEYS 표)
2. 사용자 설정 파일(이 PC 전용): `PERFKIT_CONFIG` 또는 `~/.perfkit/config.toml`
   (Windows 스토어판 파이썬은 AppData를 앱별로 가상화해 다른 프로그램과 파일이 갈리므로 홈 폴더를 쓴다)
3. 프로젝트 프로필(프로젝트 전용, 저장소 밖): 사용자 설정의 `profile`이나 `PERFKIT_PROFILE`이 가리키는 TOML
4. 기본값(PC·프로젝트와 무관한 것만)

프로필 안의 상대 경로는 프로필 파일 폴더 기준, 사용자 설정 안의 상대 경로는 사용자 설정 파일 폴더 기준이다.
예시 파일: 저장소 `config/config.example.toml`(사용자 설정), `config/profile.example.toml`(프로젝트 프로필).
"""

from __future__ import annotations

import hashlib
import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from perfkit.paths import REPO_ROOT


class ConfigError(Exception):
    """필요한 설정 값이 없거나 설정 파일이 잘못됨."""


@dataclass(frozen=True)
class Key:
    name: str
    env: str
    help: str
    is_path: bool = False
    default: object = None
    command: bool = False  # PATH에서 찾는 명령 이름이면 경로로 풀지 않는다


KEYS: dict[str, Key] = {k.name: k for k in [
    Key("profile", "PERFKIT_PROFILE", "프로젝트 프로필 TOML 경로", True),
    Key("paths.results", "PERFKIT_RESULTS", "측정 결과 저장 폴더(회차·기준선·보고)", True),
    Key("paths.manifest", "PERFKIT_MANIFEST", "매니페스트 파일(기본: 저장소 manifest/checks.json)", True,
        str(REPO_ROOT / "manifest" / "checks.json")),
    Key("paths.checklist", "PERFKIT_CHECKLIST", "(선택) 항목표 원본 md. 테스트의 ID 대조에만 씀", True),
    Key("tools.adb", "PERFKIT_ADB", "adb 실행 파일(기본: PATH의 adb)", True, "adb", True),
    Key("tools.android_sdk", "PERFKIT_ANDROID_SDK", "Android SDK 폴더(기본: ANDROID_SDK_ROOT·ANDROID_HOME)", True),
    Key("tools.emu_lab", "PERFKIT_EMU_LAB", "에뮬레이터 임대 CLI(기본: PATH의 emu-lab)", True, "emu-lab", True),
    Key("tools.unity_slot", "PERFKIT_UNITY_SLOT", "(선택) 에디터 슬롯 CLI", True, None, True),
    Key("tools.build_script", "PERFKIT_BUILD_SCRIPT", "(선택) 배치 빌드 스크립트", True),
    Key("app.package", "PERFKIT_PACKAGE", "Android 패키지 이름(예: com.example.game)"),
    Key("app.dev_suffix", "PERFKIT_PACKAGE_DEV_SUFFIX", "(선택) 개발 빌드 패키지 접미사(예: .dev)"),
    Key("build.slot", "PERFKIT_BUILD_SLOT", "(선택) 빌드에 쓰는 프로젝트 클론·슬롯 이름"),
]}

DEVICE_KEYS = ("kind", "serial", "ram_class", "model", "avd", "note")


def user_config_path() -> Path:
    env = os.environ.get("PERFKIT_CONFIG")
    if env:
        return Path(env)
    return Path.home() / ".perfkit" / "config.toml"


def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"TOML 읽기 실패 {path}: {exc}") from exc


def _dig(d: dict, dotted: str):
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _resolve(value, base: Path | None, is_path: bool, command: bool = False):
    if not is_path or value is None:
        return value
    raw = os.path.expandvars(os.path.expanduser(str(value)))
    if command and not any(sep in raw for sep in ("/", "\\")):
        return raw  # PATH에서 찾을 명령 이름(adb 등)
    p = Path(raw)
    if p.is_absolute() or base is None:
        return str(p)
    return str((base / p).resolve())


def _canon_sha(doc) -> str:
    return hashlib.sha256(json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                                     default=str).encode("utf-8")).hexdigest()


@dataclass
class Settings:
    user: dict = field(default_factory=dict)
    user_path: Path | None = None
    profile: dict = field(default_factory=dict)
    profile_path: Path | None = None
    env: dict = field(default_factory=dict)

    def lookup(self, name: str) -> tuple[object, str]:
        """(값, 출처). 출처: env:<이름> · user · profile · default · unset"""
        key = KEYS.get(name)
        if key is None:
            raise KeyError(f"모르는 설정 키: {name}")
        if key.env in self.env and self.env[key.env] != "":
            return _resolve(self.env[key.env], Path.cwd(), key.is_path, key.command), f"env:{key.env}"
        v = _dig(self.user, name)
        if v is not None:
            return _resolve(v, self.user_path.parent if self.user_path else None, key.is_path, key.command), "user"
        v = _dig(self.profile, name)
        if v is not None:
            return _resolve(v, self.profile_path.parent if self.profile_path else None, key.is_path, key.command), "profile"
        if name == "tools.android_sdk":
            for e in ("ANDROID_SDK_ROOT", "ANDROID_HOME"):
                if self.env.get(e):
                    return self.env[e], f"env:{e}"
        if key.default is not None:
            return key.default, "default"
        return None, "unset"

    def get(self, name: str, default=None):
        v, _ = self.lookup(name)
        return default if v is None else v

    def require(self, name: str):
        v, _ = self.lookup(name)
        if v is None:
            key = KEYS[name]
            raise ConfigError(
                f"설정 {name}이 없음: 환경 변수 {key.env}나 사용자 설정 {user_config_path()} "
                f"(또는 프로젝트 프로필)에 넣는다. 도움말: python -m perfkit config keys"
            )
        return v

    # ---- 기기
    def devices(self) -> dict[str, dict]:
        """프로필의 기기 정의 + 사용자 설정의 기기 값(serial 등) + 환경 변수 PERFKIT_DEVICE_<NAME>_SERIAL."""
        out: dict[str, dict] = {}
        for src in (self.profile.get("devices", {}), self.user.get("devices", {})):
            for name, d in src.items():
                out.setdefault(name, {}).update({k: v for k, v in d.items() if k in DEVICE_KEYS})
        for name, d in out.items():
            env = self.env.get(f"PERFKIT_DEVICE_{name.upper().replace('-', '_')}_SERIAL")
            if env:
                d["serial"] = env
        return out

    def device(self, name: str) -> dict:
        devs = self.devices()
        if name not in devs:
            raise ConfigError(f"기기 {name}이 프로필·사용자 설정에 없음(있는 것: {sorted(devs)})")
        return devs[name]

    @property
    def profile_name(self) -> str | None:
        return self.profile.get("name")

    @property
    def profile_sha(self) -> str | None:
        return _canon_sha(self.profile) if self.profile else None


def load(user_path: str | Path | None = None, profile_path: str | Path | None = None,
         env: dict | None = None) -> Settings:
    env = dict(os.environ if env is None else env)
    s = Settings(env=env)
    up = Path(user_path) if user_path else user_config_path()
    if up.exists():
        s.user, s.user_path = _read_toml(up), up
    pp = profile_path or env.get("PERFKIT_PROFILE") or s.user.get("profile")
    if pp:
        pp = Path(_resolve(pp, up.parent if s.user_path else Path.cwd(), True))
        if not pp.exists():
            raise ConfigError(f"프로젝트 프로필이 없음: {pp}")
        s.profile, s.profile_path = _read_toml(pp), pp
    return s


_CACHE: Settings | None = None


def settings(reload: bool = False) -> Settings:
    global _CACHE
    if _CACHE is None or reload:
        _CACHE = load()
    return _CACHE


# --------------------------------------------------------------------------- CLI

def _cmd_show(args) -> int:
    try:
        s = load()
    except ConfigError as exc:
        print(f"error: {exc}")
        return 1
    print(f"user config : {s.user_path or '(없음) ' + str(user_config_path())}")
    print(f"profile     : {s.profile_path or '(없음)'}" + (f"  name={s.profile_name}" if s.profile_name else ""))
    for name in KEYS:
        v, src = s.lookup(name)
        print(f"{name:20} {src:22} {v if v is not None else '-'}")
    for name, d in s.devices().items():
        print(f"device {name:13} " + " ".join(f"{k}={v}" for k, v in d.items()))
    return 0


def _cmd_keys(args) -> int:
    for k in KEYS.values():
        print(f"{k.name:20} {k.env:28} {k.help}")
    print(f"{'devices.<이름>.serial':20} {'PERFKIT_DEVICE_<이름>_SERIAL':28} 기기 serial(사용자 설정)")
    return 0


def _cmd_init(args) -> int:
    dst = Path(args.path) if args.path else user_config_path()
    if dst.exists() and not args.force:
        print(f"이미 있음: {dst} (덮어쓰려면 --force)")
        return 1
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text((REPO_ROOT / "config" / "config.example.toml").read_text(encoding="utf-8"), encoding="utf-8")
    print(f"만듦: {dst}  → 값을 채운 뒤 python -m perfkit config show")
    return 0


def register_cli(sub) -> None:
    p = sub.add_parser("config", help="설정(사용자 설정·프로젝트 프로필·환경 변수) 보기·만들기")
    s2 = p.add_subparsers(dest="config_cmd", metavar="<sub>")
    s2.add_parser("show", help="각 값과 출처").set_defaults(func=_cmd_show)
    s2.add_parser("keys", help="설정 키와 환경 변수 표").set_defaults(func=_cmd_keys)
    i = s2.add_parser("init", help="예시로 사용자 설정 파일을 만든다")
    i.add_argument("--path", help="기본: 사용자 설정 위치")
    i.add_argument("--force", action="store_true")
    i.set_defaults(func=_cmd_init)
    p.set_defaults(func=lambda a: (p.print_help(), 2)[1])
