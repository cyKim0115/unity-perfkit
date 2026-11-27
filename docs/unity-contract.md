# Unity 쪽 약속

[← README](../README.md)

perfkit은 Unity를 직접 부르지 않습니다. 기기 회차(L3)에서는 게임이 **측정 모드**를 갖고 있어야 하고, 에디터 층(L2)과 에디터 점검 합치기에서는 프로젝트 안 에디터 도구가 정해진 JSON을 써야 합니다. 이 문서는 perfkit의 파서·검증 코드(`perfkit/run/probe.py`, `perfkit/run/runner.py`, `perfkit/run/metrics.py`, `perfkit/editor/`, `perfkit/static/editor_merge.py`)가 기대하는 형식을 정리한 것입니다. 구현은 게임마다 직접 합니다.

측정 모드가 없는 빌드도 돌릴 수는 있습니다(호환 모드: 기기 밖 지표만). 하지만 프레임 시간·GC·그리기 수·로드 구간·UI 열기 히치는 측정 모드가 있어야 나옵니다.

## 실행 인자

perfkit은 `am start -W -n <패키지>/<액티비티> -e unity '<인자>'`로 게임을 띄웁니다. Unity Android 플레이어는 `unity` 인텐트 값을 명령줄 인자로 넘기므로 게임에서는 `System.Environment.GetCommandLineArgs()`로 읽습니다.

| 인자 | 값 | 뜻 |
|---|---|---|
| `-perfScenario <이름>` | 소문자·숫자·하이픈 | 돌릴 시나리오. 이 인자가 있으면 측정 모드로 들어갑니다 |
| `-perfSeconds <초>` | 정수 | 측정 단계 전체 길이 |
| `-perfSeed <n>` | 정수 | 난수 씨. 시나리오 안의 모든 무작위를 이 씨로 |
| `-perfSave <이름>` | 문자열 | 불러올 세이브. perfkit이 `<files>/perf/saves/<이름>/`에 넣어 둡니다(`current`면 넣지 않음) |
| `-perfRunId <id>` | `<YYYYMMDD-HHMM>-<빌드 8자>-<시나리오>-<n>` | 모든 마커 줄과 CSV 파일 이름에 그대로 씁니다 |
| `-perfQuit 0\|1` | 0 또는 1 | 1이면 END 뒤 스스로 종료. perfkit은 기본 0을 넘기고(END 뒤 `dumpsys meminfo`를 잡으려고) 직접 닫습니다 |
| `-perfFreeze 0\|1` | (선택) 0 또는 1 | 장면 상태 고정(아래 「결정성」). 없으면 게임 기본값 |
| 그 밖 | 프로필 `[run] extra_args`, `run --extra-args` | 게임 쪽 추가 인자 |

개발 빌드에서 `run --capture profiler`를 주면 Unity 표준 프로파일러 인자(`-profiler-enable -profiler-log-file … -profiler-capture-frame-count …`)가 더 붙습니다. 인자에는 작은따옴표를 쓸 수 없습니다.

`<files>`는 기본 `/sdcard/Android/data/<패키지>/files`(`Application.persistentDataPath`와 같은 곳)이고 프로필 `[run] remote_files`로 바꿉니다. 측정 폴더는 `<files>/perf`(`remote_perf_dir`)입니다.

인자를 읽다 문제가 있으면(모르는 값, 범위 밖) `[PerfProbeArgs] …` 줄로 남깁니다. 이 줄은 앱 경고 집계(STB-09)에서 빠지고 `logcat.txt`와 회차 `meta.json`의 `probe_warnings`에 남습니다. 무시한 인자는 BEGIN의 `ignored` 칸에도 적습니다.

## 로그 마커

게임은 `Debug.Log`(logcat 태그 `Unity`)로 아래 줄을 찍습니다. perfkit은 `logcat -v epoch`로 받아 줄의 시각·pid와 함께 읽습니다. `json=` 뒤는 **줄 끝까지 한 줄 JSON**입니다.

```
[PerfRun] BEGIN runId=<id> json={...}
[PerfRun] PHASE name=<단계> runId=<id> json={"idx":0,"measure":false,"t":2.51,"tGame":0,"frame":0,"scene":"<씬>"}
[PerfRun] EVENT kind=<종류> name=<이름> runId=<id> json={"frame":..,"t":..,"callMs":..,"nextFrameMs":..,"phase":"<단계>","error":null}
[PerfRun] END runId=<id> result=ok|fail json={"result":"ok","reason":null,"frames":..,"measuredFrames":..,"measuredSeconds":..,"csv":{"path":"<기기 경로>"}}
[PerfStats] header key=value …
[PerfStats] t=<초> key=value … phase=<단계>
```

### BEGIN

측정 모드가 시작됐다는 신호입니다. 실행 뒤 `probe_wait_s`(기본 120초) 안에 오지 않으면 perfkit은 호환 모드로 바꿉니다. BEGIN을 찍은 프로세스 pid가 「앱 pid」가 되어 예외·경고 집계(STB-08·09)에 쓰입니다.

| json 칸 | 쓰임 |
|---|---|
| `probeHash` | **측정 코드 판**. 회차 메타 `probe_hash`가 됩니다(없으면 `none`). 아래 「측정 코드 판과 기준선」 |
| `probe` | 측정 코드 이름·판 표시 |
| `scenario`·`seconds`·`seed`·`save` | 받은 인자를 그대로 되돌려 확인 |
| `saveHash`·`saveFiles`·`saveBytes` | 실제로 불러온 세이브의 해시·파일 수·크기 |
| `args`·`argsSource`·`ignored` | 받은 인자 원문, 출처(`cmdline` 등), 무시한 인자 |
| `dev` | 개발 빌드면 `true`(회차 메타 `build_config.variant`) |
| `perfProbe`·`frameTimingStats` | 측정 모드 켜짐, Frame Timing Stats 켜짐 |
| `version`·`buildGuid`·`unity`·`package` | 앱 버전, 빌드 GUID, Unity 버전, 패키지 |
| `device`·`gpu`·`api`·`screen`·`cpuCount`·`memoryMB` | 실행 환경 |
| `csv`·`freeze`·`quit`·`startedAt` | CSV 쓰기 여부, 상태 고정, 끝나면 종료, 시작 시각 |

`probeHash` 말고는 모두 기록용입니다. 그래도 넣어 두면 회차 메타 `game`에 남아 나중에 「같은 조건이었나」를 확인할 수 있습니다.

### PHASE

시나리오의 단계 경계마다 한 줄. `t`는 앱 시작부터의 실제 초, `tGame`은 게임 시간, `frame`은 `Time.frameCount`입니다.

| 단계 | 약속 |
|---|---|
| `load_*` | 로드 단계(예: `load_boot`, `load_scene_begin`, `load_scene_end`, `load_game_ready`). `measure:false`. 마지막 `load_*`의 `t`가 첫 화면까지 시간(BLD-11), 이웃 단계 차가 로드 구간(STB-11·AUX-04). 씬 로드 구간은 프로필 `[run] scene_load_phases`의 두 단계 사이(CPU-15) |
| `settle` | 로딩이 끝나고 장면을 고정한 뒤 한 번. `measure:false`. json에 `graphics`·`targetFps`·`vSync`·`quality`·`screen`을 넣으면 perfkit이 이 다섯 칸으로 **품질 설정 해시**(`quality_hash`)를 만듭니다. 그 밖의 칸(`freeze`, 장면 안 개수, 카메라 위치 등)은 기록용 |
| 측정 단계 | `measure:true`. 지표는 이 단계의 프레임만으로 냅니다. 이름은 자유지만 근경·원경 단계가 있어야 그리기 수 지표(GPU-03·04·07: 가까운·먼 구도)가 나옵니다. 기본 이름은 **`near`·`far`**이고 프로필 `[run] count_phases`로 바꿉니다 |
| `end` | (선택) 측정 끝 |

### EVENT

측정 중 따로 재는 사건입니다. 지금 perfkit이 읽는 종류는 `kind=ui_open` 하나입니다: UI(팝업) 열기 호출 시간 `callMs`와 그다음 프레임 시간 `nextFrameMs`. 같은 `name`이 처음 나온 것과 다시 나온 것을 따로 봅니다(CPU-15).

### END

시나리오가 끝났다는 신호입니다. `result`가 `ok`가 아니면 회차는 무효입니다(`reason`이 이유로 남음). `csv.path`를 주면 perfkit이 그 경로에서 프레임 CSV를 받고, 없으면 `<files>/perf/<runId>.csv`를 찾습니다. `errors`·`exceptions`·`firstError`를 넣으면 회차 메타 `end`에 남습니다.

END가 오지 않아도 CSV가 생겨 크기가 `csv_stable_s`초 동안 그대로면 끝으로 봅니다.

### [PerfStats]

- `header`: 한 번. `dcRecorder=True/…`면 Draw Calls·Batches 카운터를 읽을 수 있다는 뜻입니다(아니면 GPU-03은 수집 못 함, GPU-04 Batches는 빔).
- 1초마다 한 줄: `t`·`phase`와 `gcCount`·`gcResMB`·`monoMB`·`totalMB`·`sysMB`·`gfxMB`(CPU-03·04, MEM-04·05), 실기기면 `thermal`(열 여유)·`thermStatus`(열 상태)(THM-01). 그 밖의 칸은 기록용입니다.

## 프레임 CSV

측정 모드는 프레임마다 한 줄을 모아 **끝날 때 한 번에** `<files>/perf/<runId>.csv`로 씁니다(측정 중 파일 쓰기가 프레임을 흔들지 않게). 값이 없으면 `-1`.

```
frame,tReal,tGame,phase,dtMs,cpuMs,mainMs,renderMs,waitMs,gpuMs,setpass,dc,batches,tris,verts,gcAllocB,monoAllocB,monoKB
```

| 칸 | 출처(예) | 쓰는 항목 |
|---|---|---|
| `frame`·`tReal`·`tGame`·`phase` | 프레임 번호, 실제·게임 시간, 지금 단계 | 단계 나누기 |
| `dtMs` | 실제 프레임 간격 | FRM-03 |
| `cpuMs`·`mainMs`·`renderMs`·`waitMs`·`gpuMs` | `FrameTimingManager`(Player Settings의 Frame Timing Stats) | FRM-01·02, GPU-01(실기기) |
| `setpass`·`dc`·`batches`·`tris`·`verts` | 렌더 `ProfilerRecorder` 카운터 | GPU-03·04·07 |
| `gcAllocB` | `GC Allocated In Frame`(개발 빌드에서만 나옴) | CPU-02 |
| `monoAllocB`·`monoKB` | Mono 힙 증가분·크기(릴리스 대체) | CPU-02(하한값) |

## 결정성

통계 판정은 「기준과 후보가 같은 일을 했다」는 전제 위에 섭니다. 회차마다 장면 상태가 달라지면 그 차이가 그대로 잡음이 되고, 심하면 [상태 섞임](judging.md#상태-섞임-검사)으로 판단 보류가 납니다. 측정 모드는 다음을 지켜야 합니다.

- **같은 세이브에서 시작**: `-perfSave`의 세이브를 불러옵니다. perfkit이 회차마다 세이브를 다시 넣습니다. 세이브에 시간·날짜 값이 있으면 측정 모드에서 고정합니다.
- **같은 씨**: 시나리오 안의 모든 무작위(배회 경로, 스폰, 연출 선택)를 `-perfSeed`로 만든 난수에서 뽑습니다.
- **장면 상태 고정(`-perfFreeze 1`)**: 캐릭터가 제멋대로 돌아다니거나, 시간이 지나면 생기는 사건(보상 수령, 알림, 광고, 날씨)이 회차마다 다른 때에 일어나면 화면에 그려지는 것이 달라집니다. 이동 잠금처럼 「측정 중에 저절로 바뀌는 것」을 멈추거나 정해진 순서로만 일어나게 합니다.
- **정해진 카메라 동선**: 단계마다 카메라 위치·줌을 코드로 정해 둡니다(`near`·`far` 구도). 손 입력은 받지 않습니다.
- **로딩이 끝난 뒤 측정**: `settle` 단계에서 비동기 로드·셰이더 준비·첫 프레임 스파이크를 기다린 뒤 측정 단계에 들어갑니다.
- **품질 설정 고정**: 그래픽 등급·목표 FPS·vSync·해상도를 측정 모드가 정하고 `settle`에 적습니다. 이게 다르면 비교가 무효가 됩니다.
- **외부 요인 끄기**: 네트워크 응답, 서버 시각, 푸시 같은 기기 밖 입력에 기대는 동작은 측정 모드에서 끄거나 고정 값을 씁니다.

## 측정 코드 판과 기준선

`probeHash`는 「측정 모드 코드와 시나리오 정의의 판」입니다. 시나리오 단계·카메라 동선·고정 규칙·지표를 내는 방식을 바꾸면 이 값을 바꿉니다(예: 측정 코드 파일 내용의 해시).

- 측정 코드 판이 다른 회차끼리는 비교가 **무효**입니다(종료 코드 3). 같은 게임 코드라도 측정 방식이 바뀌면 숫자가 달라지기 때문입니다.
- 측정 코드를 바꾸면 기준 빌드도 새 측정 코드로 다시 빌드해 기준선을 다시 등록합니다. 옛 기준 APK에 측정 코드가 없으면 호환 모드로만 비교됩니다.
- 시나리오 이름(`scenario`)과 품질 설정 해시(`quality_hash`), 매니페스트 해시도 같은 이유로 맞아야 합니다.

## 의사 코드

약속을 보여 주는 최소 꼴입니다. 실제 구현은 프로젝트에 맞게 씁니다.

```csharp
// 측정 모드 진입점(첫 씬에서 한 번)
if (!Args.TryGet("-perfScenario", out var scenario)) return;   // 인자가 없으면 일반 실행
var runId   = Args.Get("-perfRunId");
var seconds = Args.GetInt("-perfSeconds", 60);
var rng     = new System.Random(Args.GetInt("-perfSeed", 1));
var freeze  = Args.GetInt("-perfFreeze", 1) == 1;
var quit    = Args.GetInt("-perfQuit", 0) == 1;

Log($"[PerfRun] BEGIN runId={runId} json={Json(new { probeHash = PROBE_HASH, scenario, seconds, dev = Debug.isDebugBuild, perfProbe = true })}");

Phase("load_boot");
LoadSave(Path.Combine(Application.persistentDataPath, "perf/saves", Args.Get("-perfSave")));
Phase("load_scene_begin");  await LoadMainScene();  Phase("load_scene_end");

ApplyFixedQuality();                      // 그래픽 등급·목표 FPS·vSync·해상도
if (freeze) FreezeWorld(rng);             // 이동 잠금, 시간 사건 멈춤
await WaitUntilSettled();
Phase("settle", new { graphics, targetFps, vSync, quality, screen });

foreach (var step in Scenario(scenario, seconds))   // 예: near → far
{
    Phase(step.Name, measure: true);
    await step.Run();                    // 카메라 동선, UI 열기(EVENT kind=ui_open)
}

var csv = Path.Combine(Application.persistentDataPath, "perf", runId + ".csv");
WriteFramesCsv(csv);                     // 모아 둔 프레임 행을 한 번에
Log($"[PerfRun] END runId={runId} result=ok json={Json(new { result = "ok", csv = new { path = csv } })}");
if (quit) Application.Quit();
```

`Phase(name)`은 `[PerfRun] PHASE name=<name> runId=<id> json={"idx":..,"measure":..,"t":Time.realtimeSinceStartup,"tGame":Time.time,"frame":Time.frameCount,"scene":..}`를 찍고, 프레임 기록기는 매 프레임 `phase` 칸에 지금 단계를 적습니다.

## 빌드 정보 JSON

APK 옆(`[run] build_info` 템플릿, 기본 없음)에 빌드 정보 JSON을 두면 회차 메타에 커밋·판이 남고 run_id의 빌드 8자가 정해집니다.

```json
{"commit": "abc1234def5678", "sha": "abc1234d", "version": "1.0.0", "build": "42", "probeCode": "yes"}
```

빌드 8자는 `sha` → `commit` 앞 8자 → APK sha256 앞 8자 순으로 정합니다. `probeCode`(이름은 `[build] probe_key`)는 커밋·APK 비교 파이프라인이 측정 모드 빌드인지 판단할 때 씁니다.

## 에디터 쪽 도구가 쓰는 JSON

L2와 에디터 점검 합치기는 프로젝트 안 에디터 도구가 쓴 JSON을 perfkit이 읽습니다. perfkit은 에디터를 띄우지 않습니다.

### `perfkit/editor-play@1`(→ `editor play`)

- `legs`: 카메라 시나리오 다리 `near`·`far` 구도, 팝업 다리 `popup`.
- 구도마다 그리기 수 요약 `drawCalls`·`batches`·`setPassCalls`·`triangles`·`vertices`·`shadowCasters`(각각 n·mean·p50·p95·min·max).
- 구도 동안 뜬 프로파일러 캡처 요약 `profile`(아래 형식).
- 원경 뒤 `static`(그림자·후처리 설정)·`frameDebugger`(이벤트별 배치 끊김 사유·후처리·아웃라인 패스).
- 팝업 열기 이벤트 `events`(이벤트 프레임부터 3프레임 최대).
- 칸과 항목의 대응표는 `perfkit/editor/play.py` 머리말에 있습니다.

### `perfkit/editor-profile@1`(→ `editor profile`, L2 `profile`)

`frameMs`(분포)·`frameMsHistogram`, `topSelf`(자기 시간 상위 마커), `gcAllocCallsPerFrame`·`gcAllocBytesPerFrame`, `markers`(지켜보는 마커의 포함 시간), `bottleneckPct`(FRM-04). 기기 개발 빌드 캡처(`run --capture profiler`)를 요약한 것과 에디터 구도 캡처가 같은 형식입니다.

### `perfkit/editor-audit@1`(→ `static --editor`, `build --editor`)

에디터가 있어야 아는 값(Project Auditor 진단, AssetDatabase 질의, 프리팹·모델 내용, Addressables Analyze)입니다.

- 칸: `schema`, `layer: "L0"`, `tool`, `source`(`kind: editor`, `commit`, `scope`), `items`, `auditor`(진단 요약), `extra`(`samples`, `build_report`, `l1`), `errors`.
- 항목의 `merge`가 합치는 법입니다.

| merge | 뜻 |
|---|---|
| `replace`(기본) | 에디터 항목으로 바꿉니다(정적 점검이 「수집 못 함」이던 항목) |
| `values` | 값에 에디터 값을 덮고(둘 다 dict면 얕게 합침) `resolves`에 적힌 `editor_needed`를 지웁니다. `violations`는 합집합 |
| `detail` | 값은 그대로, `detail.editor`에 내역만 붙입니다(예: Auditor 코드 진단 수) |

합친 항목에는 `merged_from: "editor"`, 결과 `tool.editor`에 에디터 도구·커밋·범위·Auditor 요약이 남습니다. 에디터 결과의 커밋이나 범위가 정적 점검과 다르면 `tool.warnings`에 적습니다. `build --editor`는 `extra.l1`의 L1 몫(예: GPU-15 키워드 수)을 합칩니다.

## 확인하는 법

- 저장소 `tests/data/device/logcat_probe_run.txt`와 `frames_small.csv`가 익명화한 표본입니다. 게임이 찍는 줄을 이것과 맞대 봅니다.
- 실제 기기에서 한 회차를 돌린 뒤 `perfkit schema validate --manifest <결과>/runs/<run_id>/meta.json <결과>/runs/<run_id>/result.json`.
- `result.json`에서 `not_collected` 항목의 `reason`이 빠진 칸을 알려 줍니다(예: 「near·far 단계 없음」, 「Draw Calls 카운터를 이 빌드에서 못 읽음」).
