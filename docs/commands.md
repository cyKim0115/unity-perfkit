# 명령 참조

[← README](../README.md)

모든 명령은 저장소 루트에서 `python -m perfkit <명령>`으로 부릅니다(`pip install -e .`로 설치했다면 `perfkit <명령>`). 옵션 전체는 `--help`가 정본이고, 이 문서는 무엇을 언제 쓰는지만 정리합니다. 도움말 문구는 한국어입니다.

## 한눈에

| 명령 | 하는 일 | 층 | 기기 |
|---|---|---|---|
| `config show`·`keys`·`init` | 설정 값과 출처, 키 표, 사용자 설정 만들기 | – | 없음 |
| `manifest check`·`hash`·`list`·`show`·`collectors` | 매니페스트 검사·해시·목록·항목 보기·수집기 표 | – | 없음 |
| `schema list`·`validate` | 결과·회차 메타·비교 JSON 스키마 검사 | – | 없음 |
| `verdict judge` | 판정 규칙을 값 하나로 시험 | 모든 층 | 없음 |
| `static` | 소스·임포트·프로젝트 설정 정적 점검 | L0 | 없음 |
| `build` | APK·AAB·빌드 로그·Addressables 레이아웃 점검 | L1 | 없음 |
| `diff` | 결정적 결과 두 개 비교(새로 생긴·없어진 위반) | L0·L1·L2 | 없음 |
| `editor play`·`profile`·`memory` | 에디터 쪽 도구가 쓴 JSON → L2 결과, 프로파일러·메모리 요약 | L2 | 없음 |
| `device list`·`doctor`·`avd`·`host-gate`·`prep`·`restore`·`wait-cool` | 기기·에뮬레이터 준비와 점검 | L3 | 필요 |
| `run`(`once`·`abab`·`cold-start`) | 회차 실행·수집(설치·세이브·실행·끝 신호·수집) | L3-E·L3-D | 필요 |
| `compare` | 기준 대비 비교·판정(결과 파일, 회차 묶음, 커밋·APK 파이프라인) | 모든 층 | 회차를 새로 돌릴 때만 |
| `bisect-step` | `git bisect run`용 한 단계 | 모든 층 | 회차를 새로 돌릴 때만 |
| `baseline register`·`list`·`show` | 기준선 등록·보기(등급별) | 모든 층 | 없음 |
| `noise aa` | 같은 빌드 A/A 회차로 잡음 바닥 | L3-E·L3-D | `--device`를 줄 때만 |
| `report` | 비교·점검 결과 → 한 파일 HTML | 모든 층 | 없음 |
| `index rebuild`·`check` | 회차·비교 색인(JSONL·SQLite) | – | 없음 |
| `import legacy` | 옛 `[PerfStats]` logcat 자료 → 회차 폴더 | L3 | 없음 |

## 종료 코드

명령마다 같은 뜻으로 씁니다.

| 코드 | 뜻 | 주로 내는 명령 |
|---|---|---|
| 0 | 정상 | 모두 |
| 1 | 검사 실패 또는 `--fail-on`에 걸림(스키마 불일치, `manifest check` 실패, `run --fail-on-invalid`, `device doctor` 문제) | `diff`·`compare`·`manifest`·`schema`·`static`·`build`·`run`·`device doctor` |
| 2 | 거부·입력 오류(등급 혼용, 파일 없음, 잘못된 run_id, 이미 있는 회차 폴더, 앱이 뜨지 않음) | 모두 |
| 3 | `compare`: 비교 무효(측정 코드·시나리오·매니페스트·품질 설정 해시가 다름). `run`·`device`: 기기 준비 실패(기기 상태, 설치·세이브 실패, 식힘 시간 초과) | `compare`·`run`·`device wait-cool` |
| 4 | 회차를 시작하지 않음: 호스트가 바쁨(배치 빌드 등), 남의 에뮬레이터 임대 | `run` |
| 5 | AVD 설정이 기대값과 다름 | `device avd`·`run` |
| 6 | 회차 폴더가 스키마에 맞지 않음 | `run` |

`compare`·`noise aa`가 안에서 빌드나 `run`을 돌리다 실패하면 2(거부)로 끝납니다.

`bisect-step`만 `git bisect run` 약속을 따릅니다: 악화 1 · 같음 0 · 판단 보류·비교 무효 125(건너뜀) · 오류 128(bisect 중단).

## 설정: config

```
python -m perfkit config init [--path <파일>] [--force]   예시로 사용자 설정 파일을 만든다
python -m perfkit config show                            각 값과 출처(env·user·profile·default)
python -m perfkit config keys                            설정 키와 환경 변수 표
```

키와 찾는 순서는 [설정과 프로젝트 프로필](profile.md).

## 매니페스트·스키마·판정 규칙

```
python -m perfkit manifest check                  항목 수·ID·필수 칸·등급 규칙 검사(통과하면 0)
python -m perfkit manifest --profile check        프로젝트 프로필 덮어쓰기를 적용한 결과로 검사
python -m perfkit manifest --file <다른 매니페스트> check
python -m perfkit manifest hash                   버전과 내용 해시(sha256)
python -m perfkit manifest list [--layer L3-E] [--set basic|deep] [--area GPU] [--no-aux]
python -m perfkit manifest show FRM-01            항목 하나를 JSON으로
python -m perfkit manifest collectors [--markdown]
python -m perfkit schema list
python -m perfkit schema validate <파일...> [--kind run-meta|result|comparison|manifest] [--manifest]
python -m perfkit verdict judge --id BLD-01 --layer L1 --metric apk_mb --base 100 --cand 106
```

- `manifest check` 출력 끝줄은 `checks=123 areas=9 FRM=5 CPU=15 … basic=73 deep=50 sha256=<64자> aux=4 version=1.0.0` 꼴입니다. 앞의 `warn:` 줄(에뮬레이터 잡음 등급 `pending_aa` 등)은 실패가 아닙니다.
- `schema validate --manifest`는 결과 안의 항목 ID·층·매니페스트 해시를 지금 매니페스트와 대조합니다.
- `verdict judge`는 `--base`·`--cand`에 회차별 값을 여러 개 줄 수 있고, 메모리 한도 규칙은 `--ram-class 8GB`로 시험합니다.

## L0·L1: static·build·diff

```
python -m perfkit static [--project <Unity 프로젝트>] [--out <source.json>] [--editor <audit.json>] [--print]
python -m perfkit build --apk <APK|AAB> [--log <빌드 로그>] [--layout <buildlayout.json>] [--stamp <이름>] [--out <l1.json>] [--editor <audit.json>] [--print]
python -m perfkit diff <기준.json> <후보.json> [--out <비교.json>] [--fail-on warn|fail]
python -m perfkit diff <후보.json>                 기준 없이 절대 기준만(16KB 정렬, 목표 API 등)
```

- `--print`는 파일 대신 표준 출력으로 씁니다. 결과 폴더(`paths.results`)를 정하지 않았다면 `--out`이나 `--print`를 줍니다.
- `--no-run-meta`: 옆 파일 `<이름>.run.json`(실행 시각·걸린 시간·절대 경로)을 쓰지 않습니다.
- 무엇을 보는지는 [수집 층](layers.md).

## L2: editor

```
python -m perfkit editor play <play.json> [<play.json> …] [--out <L2.json>] [--tree <작업 트리>] [--dirty-ignore <틀>]
python -m perfkit editor profile <요약.json> [--top 20]
python -m perfkit editor memory <비교.json> [--out <요약.json>]
python -m perfkit diff <기준 L2.json> <후보 L2.json> [--fail-on warn]
```

같은 조건 회차 여럿을 `editor play a b c`로 묶으면 지표가 회차별 값 목록이 됩니다. 입력 JSON 형식은 [Unity 쪽 약속](unity-contract.md#에디터-쪽-도구가-쓰는-json).

## L3: device·run

```
python -m perfkit device list                          설정의 기기와 adb에 보이는 기기
python -m perfkit device doctor --device <이름>        adb·기기·AVD·임대 도구·sysfs·호스트 측정 확인
python -m perfkit device avd --device <이름>           AVD 설정을 기대값과 대조(다르면 5)
python -m perfkit device host-gate [--seconds 10]      호스트 유휴 관문을 지금 재 봄
python -m perfkit device prep --device <이름>          실기기 고정(비행기 모드·밝기·화면 켜짐·게임 모드·고정 성능)
python -m perfkit device restore --device <이름>       prep 전 값으로 되돌리고 같은지 확인
python -m perfkit device wait-cool --device <이름> [--max-ap 38 --max-skin 35.5 --status 0]

python -m perfkit run --device <이름> --apk <APK> --scenario <이름> [--seconds 60] [--save <세이브>] [--rounds N] [--kind aa]
python -m perfkit run abab --device <이름> --a <기준 APK> --b <후보 APK> --rounds N [--order-seed 1]
python -m perfkit run cold-start --device <이름> --apk <APK> [--count 10]
python -m perfkit run --device <이름> --apk <개발 빌드 APK> --scenario <이름> --capture profiler [--capture-frames 1800]
```

자주 쓰는 `run` 옵션:

| 옵션 | 뜻 |
|---|---|
| `--kind numeric\|diagnostic\|aa\|smoke` | 회차 종류(`--a`와 `--b`가 같은 파일이면 자동으로 `aa`) |
| `--seed`·`--save`·`--freeze 0\|1`·`--extra-args` | 게임에 넘길 값(→ [실행 인자](unity-contract.md#실행-인자)) |
| `--no-save` | 세이브를 넣지 않음(`-perfSave`는 그대로 넘김) |
| `--no-lease`·`--keep-lease`·`--headless`·`--owner` | 에뮬레이터 임대 도구 쓰기 |
| `--avd-mismatch refuse\|warn` | AVD 설정이 다를 때 거부할지, 진행하고 회차를 무효로 할지 |
| `--gate KEY=VALUE` | 관문 문턱 덮기(진단·시험용) |
| `--fail-on-invalid` | 관문 무효 회차가 있으면 종료 코드 1 |
| `--json` | 마지막 줄에 요약 JSON |

회차 한 번의 순서와 관문은 [수집 층](layers.md#l3-기기-회차), 회차 폴더는 [결과 폴더](results.md).

## 비교·판정·보고

```
python -m perfkit compare --base <SPEC> --cand <SPEC> [--noise <기준선|noise.json>] [--report [경로]] [--fail-on warn|fail|worse]
python -m perfkit compare --runs <SPEC> --split arm|alternate|half      한 묶음을 나눠 비교(ABAB의 A·B, A/A 점검)
python -m perfkit compare --base-apk <기준 APK> --cand-commit <커밋> [--device emulator] [--scenario <이름>] [--rounds 6]
python -m perfkit compare --base-commit <커밋> --cand-commit <커밋> --no-run      빌드·L1 diff까지만
python -m perfkit compare --sessions S1,S2 …                                    이미 잰 교차 세션을 다시 판정
python -m perfkit bisect-step --base-apk <좋은 빌드 APK> --cand-head <프로젝트 저장소>
python -m perfkit baseline register --name <이름> --runs <SPEC> [--noise-runs <A/A SPEC>] [--l0 결과] [--l1 결과] [--l2 결과] [--force]
python -m perfkit baseline list | show <이름> [--json]
python -m perfkit noise aa --runs <A/A SPEC> [--target 5] [--out noise.json]
python -m perfkit noise aa --device <이름> --apk <APK> --scenario <이름> --rounds 5 [--baseline <이름>]
python -m perfkit report <비교.json|결과.json> [--out 보고.html] [--css 테마.css] [--title 제목]
python -m perfkit index rebuild | check
python -m perfkit import legacy <옛 자료 폴더> --device <기기> --build-hash <8자> [--print]
```

SPEC(비교 한쪽을 가리키는 말):

| SPEC | 뜻 |
|---|---|
| `session:<이름>` | 회차 메타의 `session`이 같은 회차 |
| `baseline:<이름>` | 등록한 기준선 |
| `run:<id,…>` | 회차 id 목록 |
| 폴더 | 회차 폴더 하나, 또는 회차 폴더들을 담은 부모 폴더 |
| 파일 | L0·L1 결과 파일 |

- `--base`·`--cand`는 여러 번 줄 수 있습니다. 결과 파일과 회차 묶음을 함께 주면 층마다 하나씩 `perfkit/comparison-set@1`로 묶습니다(각 부분은 comparison 스키마).
- `--fail-on worse`는 통계 판정이 「다름(악화)」인 지표가 있으면 1입니다.
- `--include-invalid`는 진단용입니다. 관문에서 무효인 회차도 통계에 넣고 경고를 남깁니다.
- `--dry-run`은 커밋·APK 파이프라인에서 무엇을 할지만 출력합니다.
- 커밋·APK 파이프라인은 빌드(캐시) → L1 점검 → 기기 교차 실행 → 판정 → 보고 HTML → 색인까지 갑니다. 빌드 명령은 프로필 `[build]`([설정과 프로젝트 프로필](profile.md#build-커밋을-빌드하는-법)).
- `bisect-step`: 「악화」는 통계 다름·악화, 또는 L0·L1 경고·실패입니다.
- `import legacy`: 측정 모드 이전 형식(`[PerfStats]` 줄 + `device_samples.csv`)을 회차 폴더로 옮깁니다. `--print`는 쓰지 않고 요약만 봅니다.

판정이 어떻게 나오는지는 [판정과 통계](judging.md).
