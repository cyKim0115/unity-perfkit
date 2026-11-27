# 수집 층

[← README](../README.md)

perfkit은 같은 매니페스트 항목을 여러 층에서 모읍니다. 층마다 잡음과 판정 방식이 다르고, 등급이 다른 값은 서로 비교하지 않습니다.

| 층 | 무엇 | 판정 | 명령 |
|---|---|---|---|
| L0 | 정적: 소스·임포트 설정·프로젝트 설정 | 절대 기준 + 변화(결정적) | `static` |
| L1 | 빌드 산출물: APK·AAB·빌드 로그·Addressables 레이아웃 | 절대 기준 + 변화(결정적) | `build` |
| L2 | 에디터 Play(에디터 쪽 도구가 쓴 JSON) | 변화만(추세용, 같은 날 재측정) | `editor play` |
| L3-E | 에뮬레이터 회차(`kind = "emulator"`) | **변화만**. 절대 기준 없음. 개선이면 「실기기 확인 대기」 | `run` |
| L3-D | 실기기 회차(`kind = "device"`) | 절대 기준 + 변화 | `run` |
| console | 스토어 콘솔 지표 | 절대 기준 + 변화 | (수집기 아직 없음) |

## L0·L1: 결정적 층

기기 없이 도는 결정적 지표입니다. 같은 입력이면 같은 바이트의 결과가 나오고(키 정렬, 순서 고정), 기준 결과와 그대로 `diff`합니다.

- 결과는 `result` 스키마입니다. `collected_at`은 입력의 시각(커밋 시각, APK 수정 시각)이고, 실행마다 바뀌는 값(실행 시각·걸린 시간·절대 경로)은 옆 파일 `<이름>.run.json`에 둡니다.
- 기본 저장 위치: 결과 폴더의 `static/<커밋날짜>-<커밋8>[-dirty]/source.json`, `builds/<stamp>/l1.json`.
- 항목마다 `values`(판정에 쓰는 지표)와 `detail`(내역)을 둡니다. 소스 위반은 `findings`(규칙·파일·줄·메서드·발췌·지문), 자산 위반은 `violations`(경로)입니다.
- 에디터가 있어야 아는 값은 지어내지 않습니다. `status: not_collected` + `reason: "에디터 필요: …"`, 또는 `editor_needed` 목록으로 남깁니다.
- `diff`는 항목을 판정하고, 지문·경로 집합으로 **새로 생긴 위반·없어진 위반**을 냅니다. `changes`는 값이나 위반 집합이 바뀐 항목 수입니다(같은 입력을 다시 재면 0). 결과는 `comparison` 스키마(`method=diff`).
- 크기 단위 MB는 10^6 바이트입니다.

### static(L0)이 보는 것

| 분류 | 항목 | 방법 |
|---|---|---|
| 스크립트 규칙 | SCR-01~08, GPU-18, CPU-14, FRM-05의 코드 부분 | C# 소스에서 주석·문자열을 지우고(위치 유지) 정규식으로 검사. 에디터 폴더·에디터 전용 asmdef·꺼지는 전처리 구간(`#if UNITY_EDITOR` 등)·`[Conditional]` 메서드 안 로그는 뺍니다. 메서드 범위를 따라 매 프레임 메서드(Update 등) 안이면 `hot`(Major) |
| 임포트 설정 | AST-01~20 | 텍스처·모델·오디오 `.meta`(플랫폼 덮어쓰기 반영), 이미지·WAV·OGG 머리(크기·길이·채널), TMP 폰트 에셋, 머티리얼 |
| 프로젝트 설정 | FRM-05, CPU-03·06·08·13, MEM-15, BLD-04·06~09·13, THM-04, SCR-09 | ProjectSettings·QualitySettings·DynamicsManager·PresetManager |
| URP | GPU-06·12·17·19·20·21·23 | 쓰이는 URP 에셋·렌더러·렌더러 기능·볼륨 프로필 스냅숏 |

- Project Auditor 대신 perfkit이 직접 검사하므로 Auditor 결과와 수가 다를 수 있습니다(`detail.method = perfkit-source-scan`).
- 호출 그래프는 따라가지 않습니다. Update가 부르는 함수 안은 `hot`이 아닙니다.
- 에디터가 필요한 것: CPU-10·THM-06(UI 컴포넌트 판정), GPU-05(SRP Batcher 호환), GPU-11·14, MEM-09의 L0(Analyze 규칙), AST-07(아틀라스 빈 공간), AST-13(메시 내용), 그리고 일부 지표(AST-06 단색, AST-10 정점 수, BLD-09 Script Debugging 등은 `editor_needed`).
- 범위·전처리 기호·임포트 문턱은 프로필 `[static]`([설정과 프로젝트 프로필](profile.md#static-정적-점검)).

### build(L1)가 보는 것

| 입력 | 항목 |
|---|---|
| APK·AAB | BLD-01 크기(AAB면 base 모듈·다운로드 추정), BLD-02 구성·`libil2cpp.so`, BLD-03 PAD 팩(AAB만), BLD-05 16KB 정렬(`.so` PT_LOAD 정렬 ≥ 16KB, 64비트 ABI 필수 / STORED 항목 zip 오프셋: `.so`는 16KB, 그 밖은 4바이트), BLD-07 `libil2cpp.so` |
| 빌드 로그 | GPU-15 셰이더 변형(`Compiling shader` 블록의 After scriptable stripping 합. 고유 조합 합은 detail) |
| `buildlayout.json` | MEM-09 중복 의존(건수·MB), MEM-11 텍스처, MEM-12 메시, MEM-14 애니메이션, MEM-17 폰트 아틀라스, BLD-02 번들·상위 자산 |

GPU-13(Mali Offline Compiler)은 아직 연결하지 않았습니다.

### 에디터 점검 결과 합치기(`--editor`)

에디터가 있어야 아는 값(Project Auditor 진단, AssetDatabase 질의, 프리팹·모델 내용, Addressables Analyze)은 Unity 쪽 도구가 `perfkit/editor-audit@1` JSON(`audit.json`)으로 쓰고 perfkit이 합칩니다.

```
python -m perfkit static --project <폴더> --editor <audit.json> [--out <source.json>]
python -m perfkit build --apk <APK> … --editor <audit.json>     # L1 몫(extra.l1, 예: GPU-15 keywords)
```

`audit.json` 형식과 `merge` 규칙은 [Unity 쪽 약속](unity-contract.md#에디터-쪽-도구가-쓰는-json).

## L2: 에디터 Play

Unity 에디터 Play에서 같은 시나리오를 돌려 모은 값을 L2 결과로 만듭니다. 측정은 프로젝트 안 에디터 도구가 하고(perfkit은 Unity를 부르지 않습니다), perfkit은 그 JSON을 읽어 매니페스트 항목으로 바꾸고 비교합니다.

- L2는 같은 날 같은 에디터에서 다시 재서 보는 **추세용**입니다. 기기 수치와 한 표에 넣거나 비교하지 않습니다.
- 기본 저장 위치: 결과 폴더의 `editor/<날짜>-<커밋8>/<이름>.l2.json`.
- GC(CPU-02)·UI 마커(CPU-09)는 프로파일러 캡처에서 읽습니다(게임 루프만). 에디터의 `GC Allocated In Frame` 카운터는 에디터 루프 할당이 섞여 쓰지 않습니다.
- 회차 하나끼리 `diff`하면 잡음 「중간」 항목(CPU-15 등)은 기준 회차가 모자라 「판단 보류」입니다. 회차 셋 이상을 `editor play a b c`로 묶어 비교합니다.
- `--tree <작업 트리>`: 측정한 작업 트리의 HEAD와 추적 파일 변경(`git status`, 새 파일 제외)을 `source.tree`에 남깁니다. 변경이 남거나 HEAD가 결과 커밋과 다르면 `source.dirty=true`. 에디터가 늘 다시 쓰는 파일은 `--dirty-ignore`(fnmatch)로 뺍니다. 변환 시점에 읽으므로 측정 직후에 부릅니다.
- Frame Debugger가 실패한 회차는 GPU-05·22와 GPU-21 `post_passes`가 「수집 못 함: 이유」로 남습니다.
- 기준선에 L2를 남길 때는 `baseline register --l2`([판정과 통계](judging.md#기준선)).

### 기기 프로파일러 캡처(`run --capture profiler`)

개발 빌드에 `-profiler-enable -profiler-log-file <기기 perf 폴더>/<run_id>.raw -profiler-capture-frame-count <N> -profiler-maxusedmemory 268435456`을 붙이고, 끝나면 회차 폴더 `profiler.raw`로 받아 기기에서 지웁니다.

- 캡처는 앱 시작부터 N프레임이라(약 170 KB/프레임) 로딩 구간이 앞에 있습니다.
- 요약은 에디터 쪽 도구가 합니다. `capture.json`의 `summarize`(`raw`·`out`·`skip_frames` = 측정 첫 단계 frame·`max_frames`)를 그 도구에 넘깁니다. 요약 형식은 에디터 구도 캡처와 같은 `perfkit/editor-profile@1`이고 `editor profile`로 봅니다.
- 릴리스 빌드에는 프로파일러가 없어 캡처되지 않습니다.

## L3: 기기 회차

기기에서 고정 시나리오를 돌려 지표를 모읍니다. 등급은 두 가지입니다: 에뮬레이터(`kind = "emulator"` → L3-E)와 실기기(`kind = "device"` → L3-D). `run`은 판정하지 않고 회차 폴더만 만듭니다(판정은 `compare`).

### 회차 하나의 순서

1. 거부 조건: 호스트에서 배치 빌드가 돌면(명령줄에 `-batchmode`와 `-executeMethod`·`-buildTarget`이 있는 Unity, 임포트 워커는 제외) 종료 코드 4. 남의 에뮬레이터 임대도 4. AVD 설정이 기대값과 다르면 5(`avd_mismatch = "warn"`이면 진행하고 회차 무효).
2. `am force-stop` → `install -r -d` → 세이브 주입(`<saves_dir>/<세이브>/*` → 기기 `<files>/perf/saves/<세이브>/`).
3. logcat 버퍼 키우기(`logcat -G`, 끝나면 되돌림) → `logcat -c` → (실기기, 켰을 때) SurfaceFlinger timestats.
4. 측정 전 호스트 유휴 측정(`[host] idle_seconds`) → logcat 이어 받기 시작(`-v epoch`, 끊기면 마지막 시각부터 `-T`로 다시 붙고 같은 줄은 버림).
5. `am start -W -n <패키지>/<액티비티> -e unity '<인자>'`(콜드 시작 TotalTime) → 샘플러(기본 5초: proc 공통, 실기기는 sysfs·열·배터리).
6. 끝 신호: logcat `[PerfRun] END` · 기기의 회차 CSV가 생겨 크기가 그대로(`csv_stable_s`) · 앱 종료(실패) · 시간 초과. 측정 모드 `BEGIN`이 `probe_wait_s` 안에 없으면 **호환 모드**: 실행 뒤 `seconds`초 동안 기기 밖 지표(proc·meminfo·시작 시간)만 잽니다.
7. 구간 밖 `dumpsys meminfo`(게임이 END 뒤 닫히지 않게 `-perfQuit 0`을 넘기고 perfkit이 닫음) → force-stop → CSV 받기 → 기기의 회차 CSV·넣은 세이브 지우기.
8. 회차 폴더를 쓰고 스키마로 검사합니다(어긋나면 종료 코드 6).

게임에 넘기는 인자와 게임이 찍어야 하는 줄은 [Unity 쪽 약속](unity-contract.md).

### 관문

관문은 회차가 쓸 만한 조건에서 돌았는지 봅니다. 걸린 회차도 폴더는 남고(`valid=false` + 이유), 판정에서 빼는 것은 `compare`가 합니다.

- 에뮬레이터(L3-E, 호스트 유휴 관문): 측정 전 호스트 CPU ≤ `host_busy_before_max`, 수집 중(첫 측정 단계 ~ 끝 신호) ≤ `host_busy_during_max`, 다른 에디터 프로세스(`[host] editor_images`)의 호스트 점유 ≤ `other_unity_pct_max`, logcat 재연결 ≤ `logcat_restarts_max`. 기본 15 · 25 · 1 · 0(%·회). 문턱 정본은 프로필 `[compare."L3-E".gate]`이고 `compare`가 같은 값으로 다시 봅니다. 호스트 CPU를 못 재는 OS면 「확인 못 함」 경고만 남깁니다.
- 실기기(L3-D): 회차 전 `wait-cool`(열 상태·AP·SKIN 문턱, 화면이 꺼지거나 시간이 넘으면 종료 코드 3), 시작 열 상태가 `max_status`보다 높으면 무효, logcat 재연결.
- 공통: 시나리오가 끝까지 가지 않거나(`END result=fail`, 앱 종료, 시간 초과) 프레임 CSV를 못 받으면 무효.

에뮬레이터는 호스트 PC의 CPU를 나눠 쓰므로, 빌드·에디터·다른 에뮬레이터가 같이 돌면 프레임 시간이 그만큼 흔들립니다. 그래서 측정 전과 측정 중 호스트 점유를 함께 기록하고 문턱을 넘은 회차는 통계에서 뺍니다.

### 반복·ABAB

- `--rounds N`: 같은 APK를 N회(arm A, round 1..N, 같은 session). A/A 잡음 바닥이면 `--kind aa`.
- `run abab --a X --b Y --rounds N`: 라운드마다 A·B 순서를 `Random(order_seed×1000 + 라운드)`로 섞고(같은 명령이면 같은 순서) 회차마다 다시 설치·세이브를 다시 넣습니다. `--a`와 `--b`가 같은 파일이면 `run_kind=aa`.
- 회차 사이 실기기는 `wait-cool`, 에뮬레이터는 회차마다 호스트 유휴 측정.
- 비교: `perfkit compare --runs session:<session> --split arm`.

### 지표(측정 단계만)

| 항목 | 원자료 → 지표 |
|---|---|
| FRM-01·02 | 프레임 CSV FrameTiming main·render ms → p50·p95(·p99) |
| FRM-03 | dtMs → fps_avg(프레임 수/실제 시간), 16.7·33.3·50 ms 넘는 비율, 히치 수(dt > max(50 ms, 3×중앙값)) |
| GPU-01(L3-D) | gpuMs → p50·p95 |
| CPU-02 | gcAllocB(개발 빌드), 없으면 monoAllocB(릴리스 대체, 하한값) 프레임 평균 |
| CPU-03·04, MEM-04·05 | `[PerfStats]` gcCount·gcResMB·monoMB·totalMB·sysMB |
| CPU-15 | `EVENT kind=ui_open` nextFrameMs 최대(처음 열기·다시 열기 따로 detail), 씬 로드 구간(`[run] scene_load_phases`) |
| GPU-03·04·07 | `near`·`far` 단계 평균 Draw Calls·Batches(카운터가 있을 때만)·SetPass·정점·삼각형 |
| MEM-01 | /proc status RssAnon+VmSwap 최고·P90 |
| MEM-03 | 구간 밖 meminfo PSS·Private Dirty·Native·EGL/GL mtrack·Gfx dev·Unknown·Code |
| BLD-10 | `am start -W` TotalTime(콜드) |
| BLD-11·STB-11·AUX-04 | `load_*` 단계 t: 마지막 로드 단계 = 첫 화면까지 시간, 이웃 단계 차 = 로드 구간 |
| STB-08·09 | 앱 pid logcat: 예외 수·서명(숫자·경로를 가린 클래스+메시지), `[클래스]` 경고·오류, AndroidRuntime, 네이티브 크래시, Choreographer 건너뜀, ANR, LMK |
| AUX-01·02·03 | /proc 샘플: 프로세스 CPU %(1코어 = 100), 스레드 묶음(`thread_groups` 정규식), /proc/stat 사용률, loadavg |
| THM-01·GPU-02(L3-D) | 열 상태·AP·배터리 온도 최고, GPU 클럭 평균·상한·(사용률×클럭/상한) p95 |

### 에뮬레이터 임대

기기 설정 `lease = "emu-lab"`이면 실행 앞뒤로 임대 도구 CLI를 부릅니다(`start --json` → 회차마다 `renew` → `release`). 임대 도구는 [unity-emu-lab](https://github.com/cyKim0115/unity-emu-lab)의 `emu-lab`을 기준으로 합니다. `tools.emu_lab`에 CLI 경로(`.js`면 node, `.cmd`·`.ps1`도 됨)를 둡니다.

- `--no-lease`(이미 떠 있는 기기), `--keep-lease`(끝나도 놓지 않음), `--headless`, `--owner`.
- 임대 도구 쪽은 고치지 않고, 수집은 perfkit이 adb로 합니다.
- 임대를 쓰지 않으려면 기기 설정에 `lease = "none"`을 둡니다.

### 기기 쪽 주의

- 한 PC에서 adb를 하나로 고정합니다(사용자 설정 `[tools] adb`). 버전이 다른 adb 둘(예: SDK adb와 엔진 내장 adb)이 섞이면 서버가 다시 떠 logcat이 끊깁니다. Android 빌드와 회차를 겹치지 않습니다.
- 실기기 `prep`은 기기 소리·음량을 건드리지 않습니다. `restore`는 처음 값으로 되돌린 뒤 다시 읽어 같은지 보고, 같으면 상태 파일(`<결과>/device-state/<기기>.prep.json`)을 지웁니다.
- 옛 빌드를 덮어 설치할 때(`install -d`)는 디버그 가능한 기기·앱이어야 합니다. 아니면 기기에서 앱을 지운 뒤 다시 돌립니다.
