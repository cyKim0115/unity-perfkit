# perfkit

> 뼈대 README다. 공개용 설명은 나중에 다시 쓴다.

Unity 모바일 게임(Android, URP)의 성능을 **항상 같은 기준**으로 재고, 기준 빌드·커밋 대비 얼마나 나아졌는지 판정하는 도구. 점검 항목 123개를 매니페스트 파일 하나(`manifest/checks.json`)에 고정하고, 층(정적·빌드·에디터·에뮬레이터·실기기)마다 잡음 등급과 판정 방식을 따로 둔다.

- Python 3.11 이상, **표준 라이브러리만**(설치할 패키지 없음). 개발 확인: Python 3.12.10, Windows 10.
- 프로젝트 고유 값(패키지 이름·기기·시나리오·결과 위치·기준값 덮어쓰기)은 저장소 밖 **프로젝트 프로필**에, PC 고유 값(도구 경로·기기 serial)은 **사용자 설정**에 둔다. 코드에는 넣지 않는다.
- 라이선스: MIT

## 새 PC 설정

1. 저장소를 받는다. 저장소 루트에서 `python -m perfkit --help`가 돌면 된다(설치 불필요).
2. 사용자 설정을 만든다: `python -m perfkit config init` → `~/.perfkit/config.toml`(Windows `%USERPROFILE%\.perfkit\config.toml`)이 생긴다. 다른 곳에 두려면 환경 변수 `PERFKIT_CONFIG`.
3. 그 파일에 적는다.
   - `profile`: 프로젝트 프로필 경로(프로젝트 쪽에 둔 TOML. 없으면 `config/profile.example.toml`을 복사해 만든다)
   - `[tools]`: `adb`가 PATH에 없으면 경로, Android SDK 폴더, 에뮬레이터 임대 CLI 등
   - `[devices.<이름>] serial`: 이 PC에 연결한 기기 serial(기기 정의는 프로필에)
4. `python -m perfkit config show`로 값과 출처(env·user·profile·default)를 본다. `python -m perfkit config keys`가 키와 환경 변수 표다.
5. `python -m perfkit manifest --profile check`와 `python -m unittest discover -s tests -t .`가 통과하면 끝.

값을 찾는 순서: 환경 변수(`PERFKIT_*`) → 사용자 설정 → 프로젝트 프로필 → 기본값. 설정 파일의 상대 경로는 그 파일이 있는 폴더 기준이다.

| 키 | 환경 변수 | 어디에 |
|---|---|---|
| `profile` | `PERFKIT_PROFILE` | 사용자 설정 |
| `paths.results` | `PERFKIT_RESULTS` | 프로필(또는 사용자 설정) |
| `paths.manifest` | `PERFKIT_MANIFEST` | 기본: 저장소 `manifest/checks.json` |
| `paths.checklist` | `PERFKIT_CHECKLIST` | (선택) 항목표 원본 md, 테스트의 ID 대조 |
| `tools.adb`·`tools.android_sdk`·`tools.emu_lab`·`tools.unity_slot`·`tools.build_script` | `PERFKIT_ADB` 등 | 사용자 설정 |
| `app.package`·`app.dev_suffix` | `PERFKIT_PACKAGE`·`PERFKIT_PACKAGE_DEV_SUFFIX` | 프로필 |
| `build.slot` | `PERFKIT_BUILD_SLOT` | 사용자 설정 |
| `devices.<이름>.serial` | `PERFKIT_DEVICE_<이름>_SERIAL` | 사용자 설정 |

## 층과 등급

| 층 | 무엇 | 판정 |
|---|---|---|
| L0 | 정적(에디터 정적 검사) | 절대 기준 + 변화(결정적) |
| L1 | 빌드 산출물 | 절대 기준 + 변화(결정적) |
| L2 | 에디터 Play | 변화만(추세용, 같은 날 재측정) |
| L3-E | 에뮬레이터 회차(`device_class=emulator`) | **변화만**. 절대 기준 금지. 개선이면 「실기기 확인 대기」 |
| L3-D | 실기기 회차(`device_class=device`) | 절대 기준 + 변화 |
| console | 스토어 콘솔 지표 | 절대 기준 + 변화 |

등급이 다른 수치는 서로 비교하지 않는다(`verdict.ensure_same_class`, 결과·비교 스키마). 에뮬레이터 등급에는 발열·GPU 클럭·GPU 사용량·배터리·전력·GPU ms·GPU 하드웨어 카운터 항목을 넣지 않는다(`emulator_forbidden_tags`). 에뮬레이터 잡음 등급은 처음엔 `pending_aa`(판단 보류)이고, 같은 빌드 A/A 반복으로 잡음 바닥을 잰 뒤 프로젝트 프로필 `[overrides]`에서 채운다.

## 구조

```
perfkit/
  cli.py            서브커맨드 자동 등록(이 파일은 고치지 않는다)
  config.py         설정(환경 변수·사용자 설정·프로젝트 프로필), config 명령
  paths.py          저장소 안 경로, 설정에서 읽는 경로
  collectors.py     수집기 이름 규칙·등록부
  manifest.py       checks.json 읽기·검증·해시·프로필 덮어쓰기, manifest 명령
  verdict.py        절대 기준·잡음 등급별 변화 판정, ABAB 판정기 인터페이스, verdict 명령
  schema/           run-meta·result·comparison·manifest JSON 스키마와 표준 라이브러리 검증기, schema 명령
  static/           L0 정적 점검(static 명령): Unity YAML 파서, C# 소스 검사, 임포트 규칙, 설정 스냅숏
  build/            L1 빌드 산출물 점검(build 명령): APK·AAB 구성, 16KB 정렬, 빌드 레이아웃, 셰이더 변형
  diff/             L0·L1 결과 비교(diff 명령), 결과 파일 입출력
  compare/          기준 대비 비교·통계 판정(compare), 기준선(baseline), A/A 잡음 바닥(noise)
  report/           비교·점검 결과 HTML(report), 기본 테마 default.css
  index/            회차·비교 색인(index): runs.jsonl·comparisons.jsonl·runs.sqlite
manifest/
  checks.json       123항목 + 보조 지표 AUX-01~04
  CHANGES.md        기준값 변경 기록
config/
  config.example.toml    사용자 설정 예시
  profile.example.toml   프로젝트 프로필 예시
examples/           스키마 예시(회차 메타 실기기·에뮬레이터, 결과, 비교). 값은 예시
tests/              python -m unittest
```

더할 패키지(이름 = 수집기 이름 앞부분): `static`(L0), `build`(L1), `editor`(L2·프로파일 분석), `device`·`run`(L3 수집·회차), `console`. 비교·기준선·보고·색인은 `compare`·`report`·`index` 같은 별도 패키지로 둔다.

## 명령

```
python -m perfkit --help                          등록된 명령 목록
python -m perfkit config show | keys | init       설정 값과 출처, 키 표, 사용자 설정 만들기
python -m perfkit manifest check                  항목 수·ID·필수 칸·등급 규칙 검사(통과하면 종료 코드 0)
python -m perfkit manifest --profile check        프로젝트 프로필 덮어쓰기를 적용한 결과로 검사
python -m perfkit manifest hash                   버전과 내용 해시
python -m perfkit manifest list [--layer L3-E] [--set basic] [--area GPU]
python -m perfkit manifest show FRM-01
python -m perfkit manifest collectors [--markdown]
python -m perfkit schema list
python -m perfkit schema validate <파일...> [--kind run-meta|result|comparison|manifest] [--manifest]
python -m perfkit verdict judge --id BLD-01 --layer L1 --metric apk_mb --base 100 --cand 106
python -m perfkit static | build | diff             L0·L1 점검과 비교(아래 절)
python -m perfkit compare | baseline | noise | report | index   기준 대비 비교·판정·보고(아래 절)
python -m unittest discover -s tests -t .
```

`manifest check` 출력: `checks=123 areas=9 FRM=5 CPU=15 SCR=9 GPU=25 MEM=18 AST=20 BLD=13 THM=7 STB=11 basic=73 deep=50 sha256=<64자> aux=4 version=1.0.0`

## L0·L1 점검: static·build·diff

기기 없이 도는 결정적 지표다. 같은 입력이면 같은 바이트의 결과가 나오고(키 정렬, 순서 고정), 기준 결과와 그대로 diff한다.

```
python -m perfkit static [--project <Unity 프로젝트>] [--out <source.json>]
python -m perfkit build --apk <APK|AAB> [--log <빌드 로그>] [--layout <buildlayout.json>] [--stamp <이름>] [--out <l1.json>]
python -m perfkit diff <기준.json> <후보.json> [--out <비교.json>] [--fail-on warn|fail]
python -m perfkit diff <후보.json>                 기준 없이 절대 기준만(16KB 정렬, 목표 API 등)
```

- 결과는 `result` 스키마. `collected_at`은 입력의 시각(커밋 시각, APK 수정 시각)이고, 실행마다 바뀌는 값(실행 시각·걸린 시간·절대 경로)은 옆 파일 `<이름>.run.json`에 둔다.
- 기본 저장 위치: 결과 폴더(`paths.results`)의 `static/<커밋날짜>-<커밋8>[-dirty]/source.json`, `builds/<stamp>/l1.json`.
- 항목마다 `values`(판정에 쓰는 지표), `detail`(내역), 소스 위반은 `findings`(규칙·파일·줄·메서드·발췌·지문), 자산 위반은 `violations`(경로)를 둔다. 에디터가 있어야 아는 값은 지어내지 않고 `status: not_collected` + `reason: "에디터 필요: …"` 또는 `editor_needed` 목록으로 남긴다.
- `diff`는 `verdict.judge_item`으로 항목을 판정하고, 지문·경로 집합으로 **새로 생긴 위반·없어진 위반**을 낸다. `changes`는 값이나 위반 집합이 바뀐 항목 수(다시 재면 0). 결과는 `comparison` 스키마(`method=diff`).
- 크기 단위 MB는 10^6 바이트다.

### static(L0)이 보는 것

| 분류 | 항목 | 방법 |
|---|---|---|
| 스크립트 규칙 | SCR-01~08, GPU-18, CPU-14, FRM-05의 코드 부분 | C# 소스를 주석·문자열을 지우고(위치 유지) 정규식으로 검사. 에디터 폴더·에디터 전용 asmdef·꺼지는 전처리 구간(`#if UNITY_EDITOR` 등)·`[Conditional]` 메서드 안 로그는 뺀다. 메서드 범위를 따라 매 프레임 메서드(Update 등) 안이면 `hot`(Major) |
| 임포트 설정 | AST-01~20 | 텍스처·모델·오디오 `.meta`(플랫폼 덮어쓰기 반영), 이미지·WAV·OGG 머리(크기·길이·채널), TMP 폰트 에셋, 머티리얼 |
| 프로젝트 설정 | FRM-05, CPU-03·06·08·13, MEM-15, BLD-04·06~09·13, THM-04, SCR-09 | ProjectSettings·QualitySettings·DynamicsManager·PresetManager |
| URP | GPU-06·12·17·19·20·21·23 | 쓰이는 URP 에셋·렌더러·렌더러 기능·볼륨 프로필 스냅숏 |

규칙 패키지 없는 Project Auditor 대신 perfkit이 직접 검사하므로 Auditor 결과와 수가 다를 수 있다(`detail.method = perfkit-source-scan`). 호출 그래프는 따라가지 않는다(Update가 부르는 함수 안은 hot이 아님).

에디터가 필요한 것: CPU-10·THM-06(UI 컴포넌트 판정), GPU-05(SRP Batcher 호환), GPU-11·14, MEM-09의 L0(Analyze 규칙), AST-07(아틀라스 빈 공간), AST-13(메시 내용), 그리고 일부 지표(AST-06 단색, AST-10 정점 수, BLD-09 Script Debugging 등은 `editor_needed`).

프로젝트 프로필(또는 사용자 설정이 덮음)의 `[static]`:

```toml
[static]
project = "<Unity 프로젝트 폴더>"     # --project, PERFKIT_STATIC_PROJECT로도
source_dirs = ["Assets/Scripts"]      # 스크립트 규칙(기본 Assets)
asset_dirs = ["Assets/Content"]       # 임포트 규칙·볼륨 프로필(기본 Assets)
exclude = ["Assets/ThirdParty/**"]
[static.defines]                      # 전처리 기호(기본: UNITY_EDITOR 꺼짐, UNITY_ANDROID 켜짐, 나머지 모름=셈)
DEVELOPMENT_BUILD = false
[static.import]
texture_size_limit = 2048
streaming_min_size = 1024
long_clip_seconds = 10.0
short_clip_seconds = 2.0
```

### build(L1)가 보는 것

| 입력 | 항목 |
|---|---|
| APK·AAB | BLD-01 크기(AAB면 base 모듈·다운로드 추정), BLD-02 구성·libil2cpp.so, BLD-03 PAD 팩(AAB만), BLD-05 16KB 정렬(`.so` PT_LOAD 정렬 ≥ 16KB, 64비트 ABI 필수 / STORED 항목 zip 오프셋 `.so` 16KB·그 밖 4바이트), BLD-07 libil2cpp.so |
| 빌드 로그 | GPU-15 셰이더 변형(`Compiling shader` 블록의 After scriptable stripping 합. 고유 조합 합은 detail) |
| buildlayout.json | MEM-09 중복 의존(건수·MB), MEM-11 텍스처, MEM-12 메시, MEM-14 애니메이션, MEM-17 폰트 아틀라스, BLD-02 번들·상위 자산 |

GPU-13(Mali Offline Compiler)은 아직 연결하지 않았다.

## 비교·판정·보고: compare·baseline·noise·report·index

기준 대비 「얼마나 나아졌나」를 층·등급마다 판정한다. 결정적 층(L0·L1)은 `diff` 그대로, 회차 묶음(L2·L3-E·L3-D)은 회차 단위 통계로 본다.

```
python -m perfkit compare --base <SPEC> --cand <SPEC> [--noise <기준선|noise.json>] [--report] [--fail-on warn|fail|worse]
python -m perfkit compare --runs <SPEC> --split arm|alternate|half      한 묶음을 나눠 비교(ABAB의 A·B, A/A 점검)
python -m perfkit baseline register --name <이름> --runs <SPEC> [--noise-runs <A/A SPEC>] [--l0 결과] [--l1 결과] [--force]
python -m perfkit baseline list | show <이름>
python -m perfkit noise aa --runs <A/A SPEC> [--target 5] [--out noise.json]
python -m perfkit report <비교.json|결과.json> [--out 보고.html] [--css 테마.css]
python -m perfkit index rebuild | check
```

- SPEC: `session:<이름>`(회차 메타 `session`), `baseline:<이름>`, `run:<id,…>`, 회차 폴더나 그 부모 폴더, L0·L1 결과 파일. `--base`·`--cand`는 여러 번 줄 수 있고, 결과 파일과 회차 묶음을 함께 주면 층마다 하나씩 `perfkit/comparison-set@1`로 묶는다(각 부분은 comparison 스키마).
- 회차 폴더: `<결과 폴더>/runs/<run_id>/meta.json`(run-meta) + `result.json`(result, 같은 run_id). 나머지 원자료 파일은 색인의 artifact로만 잡힌다.
- 비교 결과 기본 저장: `<결과 폴더>/comparisons/<시각>-<기준>-vs-<후보>.json`(`--report`면 옆에 .html).
- 종료 코드: 0 정상 · 1 `--fail-on`에 걸림 · 2 거부(등급 혼용, 입력 오류) · 3 비교 무효(해시 다름). 커밋·APK를 받아 빌드·실행하는 `--base-commit`·`--base-apk`는 자리만 있다.

판정(지표 하나)

| 단계 | 내용 |
|---|---|
| 효과 | Hodges-Lehmann 이동량(쌍이면 라운드 쌍 차이의 Walsh 평균 중앙값, 독립이면 median(b−a)), 기준 중앙값 대비 %. 중앙값 비율, Cliff's δ도 싣는다 |
| 구간 | 회차 단위 부트스트랩 백분위 95% 구간(기본 1만 회, 씨 고정 → 같은 입력이면 같은 구간) |
| 검정 | 쌍: Wilcoxon 부호순위, 독립: Mann-Whitney U(동점은 중간 순위, 작은 표본은 정확 분포) |
| 같음 | 구간 전체가 동등 범위 ±eq 안(먼저 본다). 0이 아닌 작은 이동이면 `minor_shift` |
| 다름 | 구간이 0을 품지 않고, p < 0.05이고, \|효과\| > 잡음 바닥. 방향은 매니페스트 `better`로 개선·악화 |
| 판단 보류 | 둘 다 아님, 회차 부족(기본 3회 미만), 또는 드리프트. 잡음 바닥이 있으면 필요한 라운드 수를 권고 |
| 드리프트 | 쌍 5개 이상이면 라운드 번호와 쌍 차이의 Spearman(8개 이하 정확 순열). 유의하면 판단 보류 |

- 잡음 바닥: `noise aa`나 기준선 `--noise-runs`로 같은 빌드 A/A 회차에서 잰 σd = √2·CV(%). 검출 가능한 최소 변화 MDE(n) = 2.8·σd/√n, 필요한 라운드 n = (2.8·σd/MDE)². 없으면 잡음 등급별 기본 바닥(none 0·low 1·medium 3·high 8%).
- 동등 범위 기본: none 1·low 3·medium 5·high 10%. 쌍 5개 이하에서는 Wilcoxon p가 0.05 아래로 못 내려가 「다름」이 나오지 않는다(6쌍 이상 권장).
- 통계 결과를 `verdict.judge_change`의 판정기로 넘겨 항목 판정(통과·개선·경고·실패·확인 필요)도 만든다(경고·실패 문턱은 판정 규칙 표). 매니페스트 잡음 등급이 `pending_aa`면 판정은 보류하고 수치만 참고로 싣는다.
- 등급 규칙: 등급이 다른 회차를 섞으면 거부(종료 코드 2). 에뮬레이터(L3-E)는 절대 기준 없이 변화만 보고, 통계로 개선이면 「실기기 확인 대기」(`needs_device_confirm`). 관문에서 무효인 회차(`valid=false` 또는 프로필 문턱 초과)는 통계에서 빼고 수·이유를 남긴다(`--include-invalid`는 진단용). 측정 코드(`probe_hash`)·시나리오·매니페스트(`manifest_sha`)·품질 설정(`quality_hash`)이 다르면 비교를 무효로 표시한다. 두 묶음의 측정 시각이 겹치지 않으면(교차 아님) 경고하고 독립 비교로 본다.

프로젝트 프로필 `[compare]`(모두 선택)

```toml
[compare]
bootstrap = 10000
seed = 1
alpha = 0.05
min_runs = 3
[compare.equivalence_pct]          # 잡음 등급별 동등 범위
low = 3.0
[compare."L3-E"]
exclude = ["FRM-01.p95", "STB-08"]  # 이 등급에서 판정하지 않을 항목·지표(값만 실음)
[compare."L3-E".gate]              # 관문 문턱(회차 메타 값으로 다시 검사)
host_busy_before_max = 15.0         # host.cpu_busy_before
host_busy_during_max = 25.0         # host.cpu_busy_during
other_unity_pct_max = 1.0           # host.other_unity_pct
logcat_restarts_max = 0             # logcat_restarts
[compare."L3-E".equivalence_pct]   # 항목·지표별 동등 범위
"MEM-04" = 2.0
[baseline]
dir = "baselines"                   # 기준선 위치(기본 <결과 폴더>/baselines)
[report]
css = "theme.css"                   # 보고 테마(기본 perfkit/report/default.css)
```

- 기준선(`perfkit/baseline@1`): 등급마다 회차 목록·유효 수·무효 회차·측정 코드·품질 설정 해시·환경·지표 요약·잡음 바닥, L0·L1 결과 참조. 같은 이름에 다른 등급을 더할 수 있다.
- 보고 HTML: 외부 자원 없음, 표 + 인라인 SVG(변화와 95% 구간, 회차 값 분포), 라이트·다크(`prefers-color-scheme`, `data-theme`). 한 절에는 한 층·한 등급만 둔다. 테마 CSS가 정의할 변수는 `default.css` 머리 주석에 있다.
- 색인: `runs.jsonl`(회차 한 줄, 수치 지표 평탄화), `comparisons.jsonl`(비교 한 줄), `runs.sqlite`(run·metric·comparison·artifact·baseline). `index check`는 회차 폴더 수 = jsonl 줄 수 = sqlite run 수인지 본다.

## 매니페스트 항목

- `layers`·`by_layer`: 층마다 잡음 등급(`none·low·medium·high·very_high·unknown·pending_aa·n/a`), 판정 방식(`absolute+change·change_only·absolute_only·record`), 수집기 이름.
- `criteria.absolute`: 절대 기준 규칙. `op`는 나쁜 쪽 비교(`gt 11` = 11 초과가 나쁨). 메모리 한도는 `fail_by_ram_class`(RAM 등급은 프로필 기기 정의). 규칙마다 근거 `basis`(공식·제안·프로젝트가 정함)와 출처 번호 `sources`(최상위 `sources` 칸에 URL).
- `metrics`: 지표 키. 없으면 `value` 하나. `zero_is_hard`(기준 0이 깨지면 실패), `increase_is_fail`(결정적 개수 증가가 실패).
- 해시: 키 정렬·공백 없는 JSON의 sha256. 회차 메타·결과·비교에 `manifest_version`·`manifest_sha`(프로필을 썼으면 `profile_name`·`profile_sha`도)를 남긴다.
- 바꾸는 법: 공개 기준이 바뀌면 `checks.json` → `manifest check` → `CHANGES.md` → 버전. 한 프로젝트만의 값은 프로필 `[overrides]`(잡음 등급·판정·층 빼기·절대 기준 교체)와 `[notes]`로.

## 판정 규칙(1판 제안값)

| 잡음 | 경고 | 실패 | 개선 |
|---|---|---|---|
| none | 개수 +1건, 크기 +2% | 표시한 개수 증가(새 Major 등), 크기 +5% | 거꾸로 |
| low | +5%, 기준 0에서 새로 생김 | +10%, 평상시 GC 0B/frame 깨짐 | -5% |
| medium | 기준 3회 범위 밖 + 5% | 범위 밖 + 10% | 범위 밖 -5% |
| high·very_high·unknown | 단독 판정 안 함 → 범위 밖이면 「확인 필요」 | 절대 기준을 모든 회차가 넘을 때만 | 「확인 필요」 |
| pending_aa | 판단 보류 | | |

항목 판정은 지표별 (변화, 절대 기준) 중 가장 나쁜 값이다. 순서: 실패 > 경고 > 확인 필요 > 수집 못 함 > 판단 보류 > 개선 > 통과 > 기록만.

## 모듈을 더하는 법

1. 자기 폴더를 만든다: `perfkit/<패키지>/__init__.py`. 다른 패키지와 `cli.py`는 고치지 않는다.
2. `__init__.py`에 `register_cli(sub)`를 두면 명령이 자동 등록된다. 불러오지 못한 모듈은 건너뛰고 `--help` 끝에 표시된다.

   ```python
   def register_cli(sub):
       p = sub.add_parser("build", help="L1 빌드 산출물 점검")
       p.add_argument("--apk", required=True)
       p.set_defaults(func=_run)        # _run(args) -> 종료 코드(int)

   def _run(args):
       from perfkit.build.apk import collect   # 무거운 import는 처리 함수 안에서
       ...
   ```

3. 수집기는 매니페스트에 적힌 이름으로 등록한다.

   ```python
   from perfkit.collectors import collector

   @collector("build.apk_size")
   def apk_size(ctx):
       return {"BLD-01": {"status": "ok", "values": {"apk_mb": 100.0}}}
   ```

   결과 파일은 `result` 스키마(항목마다 `status: ok + values` 또는 `status: not_collected + reason`)로 쓰고 `python -m perfkit schema validate --manifest`로 검사한다.
4. PC·프로젝트 값은 `perfkit.config.settings()`에서 읽는다(`get`·`require`·`device`). 새 값이 필요하면 `config.KEYS`에 키를 더하고 예시 파일에 적는다.
5. ABAB 통계 판정은 `perfkit.verdict.register_stat_judge(name, fn)`로 붙인다. `fn(base_runs, cand_runs, *, better, noise, noise_floor_pct) -> StatResult("same"|"different"|"inconclusive", delta_pct=..., interval=...)`.
6. 테스트는 `tests/test_<패키지>.py`에 표준 라이브러리 `unittest`로 쓴다.

## 수집기 이름

이름 앞부분이 구현 패키지다. `python -m perfkit manifest collectors --markdown`으로 다시 뽑는다(매니페스트 1.0.0).

| 수집기 | 구현 패키지 | 층 | 항목 수 | 항목 |
|---|---|---|---|---|
| `build.align16k` | perfkit/build | L1 | 1 | BLD-05 |
| `build.apk_size` | perfkit/build | L1 | 1 | BLD-01 |
| `build.il2cpp` | perfkit/build | L1 | 1 | BLD-07 |
| `build.layout` | perfkit/build | L1 | 6 | MEM-09, MEM-11, MEM-12, MEM-14, MEM-17, BLD-02 |
| `build.malioc` | perfkit/build | L1 | 1 | GPU-13 |
| `build.pad` | perfkit/build | L1 | 1 | BLD-03 |
| `build.shader_variants` | perfkit/build | L1 | 1 | GPU-15 |
| `console.vitals` | perfkit/console | console | 8 | BLD-13, STB-01, STB-02, STB-03, STB-04, STB-05, STB-06, STB-07 |
| `device.exit_info` | perfkit/device | L3-E·L3-D | 2 | STB-03, STB-10 |
| `device.frame_timing` | perfkit/device | L3-E·L3-D | 6 | FRM-01, FRM-02, FRM-03, GPU-01, GPU-25, BLD-12 |
| `device.gc_alloc` | perfkit/device | L3-E·L3-D | 1 | CPU-02 |
| `device.gpu_counters` | perfkit/device | L3-D | 5 | GPU-08, GPU-09, GPU-10, GPU-16, GPU-24 |
| `device.gpu_freq` | perfkit/device | L3-D | 2 | GPU-02, THM-03 |
| `device.logcat` | perfkit/device | L3-E·L3-D | 3 | MEM-18, STB-08, STB-09 |
| `device.meminfo` | perfkit/device | L3-E·L3-D | 1 | MEM-03 |
| `device.phase_markers` | perfkit/device | L3-E·L3-D | 4 | CPU-15, BLD-11, STB-11, AUX-04 |
| `device.power` | perfkit/device | L3-D | 1 | THM-05 |
| `device.probe_state` | perfkit/device | L3-D | 2 | CPU-14, MEM-10 |
| `device.proc_cpu` | perfkit/device | L3-E·L3-D | 2 | AUX-01, AUX-02 |
| `device.proc_mem` | perfkit/device | L3-E·L3-D | 3 | MEM-01, MEM-02, MEM-08 |
| `device.render_counters` | perfkit/device | L3-E·L3-D | 4 | CPU-08, GPU-03, GPU-04, GPU-07 |
| `device.startup` | perfkit/device | L3-E·L3-D | 1 | BLD-10 |
| `device.sys_load` | perfkit/device | L3-E·L3-D | 1 | AUX-03 |
| `device.thermal` | perfkit/device | L3-D | 2 | THM-01, THM-02 |
| `device.unity_memory` | perfkit/device | L3-E·L3-D | 4 | CPU-03, CPU-04, MEM-04, MEM-05 |
| `editor.device_profile` | perfkit/editor | L3-D | 6 | FRM-04, CPU-01, CPU-05, CPU-07, CPU-09, CPU-13 |
| `editor.frame_debugger` | perfkit/editor | L2 | 5 | GPU-05, GPU-06, GPU-11, GPU-21, GPU-22 |
| `editor.memory_snapshot` | perfkit/editor | L3-D | 9 | MEM-06, MEM-07, MEM-11, MEM-12, MEM-13, MEM-14, MEM-15, MEM-16, MEM-17 |
| `editor.play_counters` | perfkit/editor | L2 | 5 | CPU-02, CPU-09, GPU-03, GPU-04, GPU-07 |
| `editor.play_events` | perfkit/editor | L2 | 1 | CPU-15 |
| `editor.play_profile` | perfkit/editor | L2 | 6 | CPU-01, CPU-07, CPU-11, CPU-12, MEM-10, THM-06 |
| `editor.render_debug` | perfkit/editor | L2 | 4 | GPU-09, GPU-18, GPU-19, GPU-24 |
| `run.conditions` | perfkit/run | L3-D | 1 | THM-07 |
| `static.addressables_dupes` | perfkit/static | L0 | 1 | MEM-09 |
| `static.auditor_code` | perfkit/static | L0 | 6 | SCR-01, SCR-02, SCR-03, SCR-04, SCR-06, SCR-07 |
| `static.auditor_settings` | perfkit/static | L0 | 1 | SCR-09 |
| `static.import_rules` | perfkit/static | L0 | 20 | AST-01, AST-02, AST-03, AST-04, AST-05, AST-06, AST-07, AST-08, AST-09, AST-10, AST-11, AST-12, AST-13, AST-14, AST-15, AST-16, AST-17, AST-18, AST-19, AST-20 |
| `static.project_settings` | perfkit/static | L0 | 14 | FRM-05, CPU-03, CPU-06, CPU-08, CPU-13, CPU-14, MEM-15, BLD-04, BLD-06, BLD-07, BLD-08, BLD-09, BLD-13, THM-04 |
| `static.render_assets` | perfkit/static | L0 | 4 | GPU-05, GPU-10, GPU-11, GPU-14 |
| `static.source_scan` | perfkit/static | L0 | 3 | SCR-05, SCR-08, GPU-18 |
| `static.ui_scan` | perfkit/static | L0 | 2 | CPU-10, THM-06 |
| `static.urp_settings` | perfkit/static | L0 | 7 | GPU-06, GPU-12, GPU-17, GPU-19, GPU-20, GPU-21, GPU-23 |
