# 설정과 프로젝트 프로필

[← README](../README.md)

perfkit 코드에는 프로젝트 고유 값도, PC 고유 값도 넣지 않습니다. 값은 세 곳에 나눠 둡니다.

| 어디 | 무엇 | 위치 |
|---|---|---|
| **프로젝트 프로필** | 패키지 이름, 기기 정의, 시나리오, 결과 위치, 빌드 명령, 판정 덮어쓰기, 잡음 등급, 실측 메모 | 게임 프로젝트 쪽(perfkit 저장소 밖). 팀이 함께 씀 |
| **사용자 설정** | 도구 경로(adb·SDK·임대 도구), 기기 serial, 이 PC에서만 다른 경로 | `~/.perfkit/config.toml`(Windows `%USERPROFILE%\.perfkit\config.toml`). 환경 변수 `PERFKIT_CONFIG`로 바꿈 |
| **기기 preset** | SoC·기기 종류별 일반 값(sysfs 경로·배율·식힘·prep 기본) | 저장소 `config/devices/*.json`(공개) |

## 찾는 순서

환경 변수(`PERFKIT_*`) → 사용자 설정 → 프로젝트 프로필 → 기본값. 앞이 이깁니다. 설정 파일 안의 상대 경로는 그 파일이 있는 폴더 기준입니다.

경로 값에는 `~`(홈)와 환경 변수(`$VAR`·`${VAR}`, Windows는 `%VAR%`도)를 쓸 수 있습니다. 대상: 설정 키의 경로, 프로필의 `[static] project`, `[run] saves_dir`·`build_info`, `[baseline] dir`, `[report] css`, `[build]`의 `command` 인자·`state_glob`·`log`·`layout`·`cache`. PC마다 다른 위치는 환경 변수나 프로필 옆 상대 경로로 적으면 같은 프로필을 여러 PC에서 고치지 않고 씁니다. 펼칠 환경 변수가 없으면 글자 그대로 남으므로(예: `${MY_GAME_PROJECT}`) `config show`·오류 문구에서 바로 보입니다.

```
python -m perfkit config init     # 사용자 설정을 예시(config/config.example.toml)로 만든다. 줄은 모두 주석이라 필요한 줄만 푼다
python -m perfkit config show     # 값과 출처(env·user·profile·default)
python -m perfkit config keys     # 키와 환경 변수 표
```

| 키 | 환경 변수 | 보통 두는 곳 | 뜻 |
|---|---|---|---|
| `profile` | `PERFKIT_PROFILE` | 사용자 설정 | 프로젝트 프로필 TOML 경로 |
| `paths.results` | `PERFKIT_RESULTS` | 프로필(또는 사용자 설정) | 결과 폴더 |
| `paths.manifest` | `PERFKIT_MANIFEST` | 기본 | 매니페스트(기본: 저장소 `manifest/checks.json`) |
| `paths.checklist` | `PERFKIT_CHECKLIST` | (선택) | 항목표 원본 md. 테스트가 매니페스트 ID와 대조할 때만 |
| `tools.adb` | `PERFKIT_ADB` | 사용자 설정 | adb(기본: PATH의 adb) |
| `tools.android_sdk` | `PERFKIT_ANDROID_SDK` | 사용자 설정 | Android SDK(기본: `ANDROID_SDK_ROOT`·`ANDROID_HOME`) |
| `tools.emu_lab` | `PERFKIT_EMU_LAB` | 사용자 설정 | 에뮬레이터 임대 CLI(기본: PATH의 `emu-lab`) |
| `tools.unity_slot` | `PERFKIT_UNITY_SLOT` | (선택) | 에디터 슬롯 CLI |
| `tools.build_script` | `PERFKIT_BUILD_SCRIPT` | (선택) | 배치 빌드 스크립트 |
| `app.package` | `PERFKIT_PACKAGE` | 프로필 | Android 패키지 이름 |
| `app.dev_suffix` | `PERFKIT_PACKAGE_DEV_SUFFIX` | (선택) | 개발 빌드 패키지 접미사(예: `.dev`) |
| `build.slot` | `PERFKIT_BUILD_SLOT` | (선택) | 빌드에 쓰는 프로젝트 클론·슬롯 이름 |
| `devices.<이름>.serial` | `PERFKIT_DEVICE_<이름>_SERIAL` | 사용자 설정 | 기기 serial |

`static` 명령의 프로젝트 폴더는 `--project` → `PERFKIT_STATIC_PROJECT` → 설정 `[static] project` 순으로 찾습니다.

## 프로필 예시

아래 값은 모두 가짜입니다. 저장소의 [config/profile.example.toml](../config/profile.example.toml)이 같은 뼈대입니다. 모든 절은 선택이고, 쓰는 명령에 필요한 절만 채우면 됩니다.

```toml
name = "my-game"                     # 회차 메타 profile_name

[app]
package = "com.example.game"
# dev_suffix = ".dev"

[paths]
results = "perf-results"             # 이 파일 기준 상대 경로

# ---- 기기 정의: kind = "device"(→ L3-D) 또는 "emulator"(→ L3-E)
[devices.phone-a]
kind = "device"
model = "Example Phone"
ram_class = "8GB"                    # 메모리 한도 규칙의 RAM 등급: 4GB·6GB·8GB·12GB
preset = "qualcomm-adreno"           # config/devices/<preset>.json

[devices.phone-a.cool]               # wait-cool 문턱
max_ap = 38.0
max_skin = 35.5
max_status = 0

[devices.emulator]
kind = "emulator"
avd = "Example_API_35"
ram_class = "4GB"
lease = "emu-lab"                    # 임대 도구로 띄우고 놓음("none"이면 쓰지 않음)
avd_mismatch = "refuse"              # AVD가 expect와 다르면 거부("warn"이면 진행하고 무효)

[devices.emulator.expect]            # AVD 기대값
system_image = "system-images;android-35;google_apis;x86_64"
cores = 4
ram_mb = 4096
gpu_mode = "host"
resolution = "1080x2400"
arm_translation = true

# ---- 회차 공통(사용자 설정 [run]이 덮음)
[run]
# package = "com.example.game"       # 없으면 APK의 패키지 → app.package
activity = "com.unity3d.player.UnityPlayerGameActivity"
saves_dir = "saves"                  # <saves_dir>/<세이브 이름>/* 를 기기에 넣음
build_info = "{apk_dir}/build.json"  # 빌드 정보 JSON(commit·sha·version) 템플릿: {apk_dir}·{apk_dir_name}·{apk_stem}
seconds = 60
seed = 1
sample_interval_s = 5.0
probe_wait_s = 120                   # BEGIN을 기다리는 시간(넘으면 호환 모드)
load_timeout_s = 600                 # BEGIN 뒤 END까지 = seconds + 이 값
csv_stable_s = 2.0
logcat_buffer = "16M"
extra_args = ""                      # 게임 실행 인자 덧붙이기
scene_load_phases = ["load_scene_begin", "load_scene_end"]
count_phases = ["near", "far"]       # GPU-03·04·07 근경·원경 단계 이름(지표 키 _near·_far는 그대로)
capture_start_phases = ["near", "far", "popups", "pan-far"]  # 프로파일러 캡처 요약 시작 단계(먼저 보이는 것)
cold_start_count = 10

[host]                               # 호스트 유휴 측정
idle_seconds = 10
editor_images = ["Unity.exe", "Unity"]
# editor_open_cmdline = "(?i)-projectPath"          # 「열린 에디터」로 셀 명령줄(관문 other_editors_max)
# editor_ignore_cmdline = "(?i)AssetImportWorker"   # 빼는 것(에디터의 임포트 워커)
emulator_images = ["qemu-system-x86_64.exe", "qemu-system-aarch64.exe", "qemu-system-x86_64"]
busy_files = []                      # 이 파일(glob)이 있으면 회차를 시작하지 않음(빌드 잠금 등)

# ---- 시나리오: 게임 측정 모드가 받는 이름. run --scenario의 기본은 첫 번째
[[scenarios]]
name = "town-idle"                   # 소문자·숫자·하이픈(run_id에 들어감)
save = "light"

# ---- L0 정적 점검
[static]
project = "../MyGame"                # Unity 프로젝트 폴더. PC마다 다르면 사용자 설정에서 덮음
source_dirs = ["Assets/Scripts"]     # 스크립트 규칙(기본 Assets)
asset_dirs = ["Assets"]              # 임포트 규칙·볼륨 프로필(기본 Assets)
exclude = ["Assets/ThirdParty/**"]

[static.defines]                     # 전처리 기호(기본: UNITY_EDITOR 꺼짐, UNITY_ANDROID 켜짐, 나머지 모름=셈)
DEVELOPMENT_BUILD = false

[static.import]
texture_size_limit = 2048
streaming_min_size = 1024
long_clip_seconds = 10.0
short_clip_seconds = 2.0

[[code_rules]]                       # SCR-08 프로젝트 코드 규칙. id가 결과 by_rule 키
id = "find_object_of_type"
pattern = "FindObjectOfType"
note = "참조를 캐시한다"

# ---- 비교·판정(모두 선택)
[compare]
bootstrap = 10000
seed = 1
alpha = 0.05
min_runs = 3

[compare.equivalence_pct]            # 잡음 등급별 동등 범위(%)
low = 3.0

[compare."L3-E"]
exclude = ["FRM-01.p95", "STB-08"]   # 이 등급에서 판정하지 않을 항목·지표(값만 실음)
# compat_items = ["BLD-10", "MEM-01"] # 호환 모드일 때 비교할 항목만

[compare."L3-E".gate]                # 관문 문턱(회차 메타 값으로 다시 검사)
host_busy_before_max = 15.0          # host.cpu_busy_before
host_busy_during_max = 25.0          # host.cpu_busy_during
other_unity_pct_max = 1.0            # host.other_unity_pct
logcat_restarts_max = 0              # logcat_restarts
# other_editors_max = 0              # host.editors_open. 열린 다른 에디터 수(기본 끔). 넘으면 회차를 시작하지 않음(종료 코드 4)

# [[compare."L3-E".gate.health]]     # 회차 뒤 상태 검사(기본 없음). 밑이면 무효·meta.degraded
# metric = "FRM-03.fps_avg"
# min = 55
# scenario = "light"                 # 이 시나리오만(없으면 모든 시나리오)

[compare."L3-E".equivalence_pct]     # 항목·지표별 동등 범위
"MEM-04" = 2.0

[baseline]
dir = "baselines"                    # 기본 <결과 폴더>/baselines

[report]
css = "theme.css"                    # 보고 테마(기본: 저장소 perfkit/report/default.css)

# ---- 커밋·APK 비교에 쓰는 빌드
[build]
command = ["python", "{profile_dir}/tools/build_android.py", "--commit", "{commit}"]
state_glob = "builds/*/build.json"
timeout_min = 40

# ---- 매니페스트 덮어쓰기와 메모
[overrides."MEM-04".layers."L3-E"]
noise = "low"
noise_hint = "A/A 5회 반복 차 0.0~0.6%(예시)"

[notes]
"FRM-01" = "기준 기기 첫 기준선: <값>"
```

## 기기 정의

기기 정의는 아래 순서로 합칩니다(뒤가 이김).

1. 저장소 `config/devices/<preset>.json`. `preset`이 없으면 `kind`의 기본(`emulator`, `android-device`). 저장소에 `emulator`·`android-device`·`qualcomm-adreno`·`exynos-mali`가 있습니다.
2. 프로필 `[devices.<이름>]`(모든 칸).
3. 사용자 설정 `[devices.<이름>]`(serial·adb 등 PC 고유 값).
4. 환경 변수 `PERFKIT_DEVICE_<이름>_SERIAL`.

| 칸 | 뜻 |
|---|---|
| `kind` | `device`(실기기) · `emulator` |
| `preset` | 저장소 preset 이름 |
| `serial` | adb serial(사용자 설정에) |
| `ram_class` | 메모리 한도 규칙의 RAM 등급 |
| `avd`·`expect`·`avd_mismatch` | 에뮬레이터 AVD 이름, 기대값(system_image·cores·ram_mb·gpu_mode·resolution·emulator_version·arm_translation), 다를 때 처리 |
| `lease` | `emu-lab` 또는 `none` |
| `sysfs` | CPU 클럭 목록·GPU 클럭·GPU 사용률·GPU 최대 클럭 경로와 배율 |
| `thermal.sensors` | 열 센서 이름(ap·skin·bat) |
| `cool`·`prep` | 식힘 문턱, 실기기 고정 설정 |
| `thread_groups` | 스레드 묶음 정규식(AUX-02) |
| `timestats` | SurfaceFlinger timestats 켬 |
| `gate`·`host` | 관문 문턱·호스트 측정 덮기 |

sysfs 경로는 기기마다 다릅니다. `perfkit device doctor --device <이름>`으로 확인합니다.

## build: 커밋을 빌드하는 법

`compare --base-commit`·`--cand-commit`·`bisect-step --cand-head`는 프로필 `[build]`로 빌드합니다. 빌드 도구는 프로젝트마다 다르므로 perfkit은 명령을 부르고 결과 JSON만 읽습니다.

| 칸 | 뜻 |
|---|---|
| `command` | 빌드 명령(목록). 자리: `{commit}` `{commit8}` `{label}` `{profile_dir}`(프로필 폴더). 인자마다 `~`·환경 변수를 펼침. 명령은 현재 폴더에서 돌므로 프로필 옆 스크립트는 `{profile_dir}`로 가리킴 |
| `state_glob` | 빌드가 남기는 결과 JSON glob. 필수 칸 `commit`, `file`(APK 경로) |
| `log`·`layout` | (선택) L1에 쓸 빌드 로그·Addressables 레이아웃. 자리 `{state_dir}` |
| `cache` | 커밋 → APK 색인(기본 `<결과 폴더>/build-cache.json`) |
| `timeout_min` | 빌드 시간 한도 |
| `probe_key` | 결과 JSON에서 측정 모드 여부 칸(기본 `probeCode`, 값이 yes·true면 측정 모드) |

- 색인에 있으면 다시 빌드하지 않습니다(파일 크기·sha256 확인, `--rebuild`로 무시).
- APK는 빌드가 둔 곳에 그대로 둡니다(복사하면 빌드 정보 JSON과의 연결이 끊김).
- 빌드 뒤에는 adb 서버를 다시 띄우고 `device doctor`를 먼저 돕니다.
- 빌드와 기기 회차를 겹치지 않습니다. 회차는 호스트에서 배치 빌드가 돌면 시작하지 않습니다(종료 코드 4).

## static: 정적 점검

`[static]`의 범위·전처리 기호·임포트 문턱은 위 예시대로입니다. 프로젝트 폴더가 PC마다 다르면 `project = "${MY_GAME_PROJECT}"`처럼 환경 변수로 적거나, 사용자 설정 `[static] project`·환경 변수 `PERFKIT_STATIC_PROJECT`·`static --project`로 덮습니다.

## 이 PC 전용 도구 없이 쓰기

perfkit은 임대 도구·빌드 도구를 부르기만 하고 없어도 돕니다.

| 없는 것 | 대신 |
|---|---|
| 에뮬레이터 임대 도구(`emu-lab`) | 기기 정의 `lease = "none"` 또는 `run`에 `--no-lease`. 에뮬레이터는 직접 띄워 두고 `device doctor`로 확인 |
| 빌드 명령(`[build]`) | APK를 직접 뽑아 `run --apk`, `run abab --a … --b …`, `compare --base-apk … --cand-apk …`로 줌. 커밋 메타는 APK 옆 빌드 정보 JSON(`[run] build_info`)에서 읽고, 없으면 비어 있음 |
| 에디터 쪽 도구 | `static`은 에디터 없이 소스·설정만 보고, 에디터가 필요한 항목은 「수집 못 함」. L2(`editor`)는 쓰지 않음 |
| 결과 폴더 설정 | 명령마다 `--out`. 기준선·비교 색인을 쓰려면 `paths.results`가 필요 |

## 어디에 두나

- 프로필은 게임 프로젝트 저장소 안(예: `perf/profile.toml`)이나 그 옆에 둡니다. perfkit 저장소에는 넣지 않습니다.
- 세이브 폴더(`saves_dir`)와 결과 폴더(`paths.results`)는 프로필 기준 상대 경로로 둘 수 있습니다. 결과 폴더는 커지므로 보통 저장소 밖 큰 디스크에 둡니다.
- 사용자 설정은 PC마다 하나이고 저장소에 넣지 않습니다.
