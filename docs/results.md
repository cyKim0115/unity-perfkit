# 결과 폴더

[← README](../README.md)

결과 폴더는 설정 `paths.results`(프로필 또는 사용자 설정)입니다. 모든 명령이 이 폴더 하나에 쓰고 읽으며, 회차 폴더만 있으면 색인은 언제든 다시 만들 수 있습니다.

```
<결과 폴더>/
  runs/<run_id>/            기기 회차 하나(아래)
  sessions/<session>.json   여러 회차 실행 기록(라운드별 A·B 순서, 회차, 관문 결과, 임대)
  startup/<시각>-<기기>-cold.json   콜드 시작 반복
  static/<커밋날짜>-<커밋8>[-dirty]/source.json   L0 결과(+ source.run.json)
  builds/<stamp>/l1.json    L1 결과(+ l1.run.json)
  editor/<날짜>-<커밋8>/<이름>.l2.json   L2 결과
  comparisons/<시각>-<기준>-vs-<후보>.json   비교 결과(--report면 옆에 .html)
  reports/                  커밋·APK 비교의 보고 HTML
  baselines/<이름>.json     기준선(프로필 [baseline] dir로 바꿈)
  noise/<이름>.noise.json   A/A 잡음 바닥(noise aa 기본 출력)
  device-state/<기기>.prep.json   실기기 prep 전 값(restore가 지움)
  build-cache.json          커밋 → APK 색인(프로필 [build] cache로 바꿈)
  runs.jsonl                색인: 회차 한 줄
  comparisons.jsonl         색인: 비교 한 줄
  runs.sqlite               색인: run · metric · comparison · artifact · baseline 표
```

## 회차 id

`run_id = <YYYYMMDD-HHMM>-<빌드 8자>-<시나리오>-<n>`(예: `20260101-1200-abc1234d-town-idle-1`).

- 빌드 8자: 빌드 정보 JSON `sha` → 커밋 앞 8자 → APK sha256 앞 8자.
- 시나리오는 소문자·숫자·하이픈. `n`은 같은 앞부분의 회차 번호.

## 회차 폴더 `runs/<run_id>/`

| 파일 | 내용 |
|---|---|
| `meta.json` | run-meta 스키마. 아래 칸 |
| `result.json` | result 스키마(층 L3-E·L3-D, 매니페스트 지표 키). 못 잰 항목은 `not_collected` + 이유 |
| `logcat.txt` | logcat 전체(`-v epoch`, 재연결 표시 `##PERFKIT_LOGCAT_RESTART`) |
| `frames.csv` | 게임이 쓴 프레임 CSV |
| `device_samples.csv` | 샘플: 시각·단계·프로세스·스레드 묶음 점유율·시스템 CPU·loadavg·RSS·Anon·Swap·스레드 수. 실기기는 CPU·GPU 클럭·사용률·상한·열·배터리 |
| `meminfo.txt` | 구간 밖 `dumpsys meminfo` |
| `am_start.txt` | `am start -W` 출력(콜드 시작 시간) |
| `timestats.txt` | SurfaceFlinger timestats(켰을 때) |
| `run.log` | perfkit 진행 기록 |
| `profiler.raw`·`capture.json` | `--capture profiler`일 때 |

`meta.json`의 주요 칸:

| 칸 | 뜻 |
|---|---|
| `session`·`arm`·`round` | 쌍 짓기(ABAB의 A·B와 라운드) |
| `run_kind` | `numeric`·`diagnostic`·`aa`·`smoke` |
| `device_class`·`device_profile`·`layer` | 등급(`emulator`·`device`), 프로필 기기 이름, 층 |
| `build_hash`·`commit`·`version`·`build_config` | 빌드(변형·측정 모드·Frame Timing Stats·품질 설정 해시) |
| `scenario`·`seed`·`save` | 시나리오 조건 |
| `probe_hash`·`quality_hash`·`manifest_version`·`manifest_sha`·`profile_sha` | 비교 일관성 검사에 쓰는 해시 |
| `compat_mode` | 측정 모드 없이 돈 회차 |
| `emu`·`host` | 에뮬레이터: AVD 설정, 호스트 점유(`cpu_busy_before`·`cpu_busy_during`·`other_unity_pct`·`emulator_pct`) |
| `thermal_start`·`ap_c_start`·`batt_c_start`·`batt_pct`·`charging`·`fixed_perf`·`game_mode`·`brightness` | 실기기 시작 상태(에뮬레이터는 `null`) |
| `logcat_restarts`·`end_signal` | logcat 재연결 수, 끝 신호(`end`·`csv`·`compat`·`app_exit`…) |
| `valid`·`invalid_reason` | 관문 결과 |
| `game`·`end` | 게임이 BEGIN·END에 적은 값 |
| `probe_warnings` | 측정 모드가 찍은 경고 줄(`[PerfProbeArgs] …`, 모르는 인자·범위 밖 값). 없으면 칸 없음 |
| `unity_args`·`tool`·`timing` | 넘긴 인자, perfkit·adb 정보, 걸린 시간 |

무효 회차도 폴더는 남습니다. 판정에서 빼는 것은 `compare`가 합니다.

## 스키마

`perfkit/schema/`에 표준 라이브러리로 검사하는 JSON 스키마가 있습니다.

| 스키마 | 파일 |
|---|---|
| `perfkit/run-meta@1` | 회차 `meta.json` |
| `perfkit/result@1` | 모든 층의 결과(`source.json`·`l1.json`·`*.l2.json`·회차 `result.json`) |
| `perfkit/comparison@1` | 비교 결과(`comparison-set@1`은 층별 비교를 묶은 것) |
| `perfkit/manifest@1` | 매니페스트 |

예시는 저장소 `examples/`(회차 메타 실기기·에뮬레이터, 결과, 비교)에 있습니다. 값은 예시입니다.

```
python -m perfkit schema validate examples/*.json
python -m perfkit schema validate --manifest <결과>/runs/<run_id>/result.json
```

기준선은 `perfkit/baseline@1`, 잡음 바닥은 `perfkit/noise@1`입니다.

## 색인

```
python -m perfkit index rebuild     # 처음부터 다시
python -m perfkit index check       # 회차 폴더 수 = jsonl 줄 수 = sqlite run 수
```

- `runs.jsonl`: 회차 하나에 한 줄(메타 요약 + 수치 지표 평탄화 `"<ID>.<지표>": 값`). sqlite 없이 읽을 수 있습니다.
- `comparisons.jsonl`: 비교 파일 하나에 한 줄(요약).
- `runs.sqlite`: `run`·`metric`·`comparison`·`artifact`·`baseline` 표. 매번 임시 파일에 새로 만들어 바꿔 끼웁니다(`commit` 칸은 sqlite 예약어라 `commit_hash`).
- 같은 폴더 내용이면 jsonl은 같은 바이트가 나옵니다.

## 보고 HTML

`report`와 `compare --report`가 씁니다.

- 한 파일, 외부 자원 없음. 표와 인라인 SVG(변화와 95% 구간, 회차 값 분포).
- 라이트·다크(`prefers-color-scheme`, `data-theme`).
- 한 절에는 한 층·한 등급만 둡니다.
- 테마: `--css` → 프로필 `[report] css` → 저장소 `perfkit/report/default.css`. 테마 CSS가 정의할 변수는 `default.css` 머리 주석에 있습니다.
