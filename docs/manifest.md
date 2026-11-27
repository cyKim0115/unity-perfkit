# 점검 매니페스트

[← README](../README.md)

`manifest/checks.json` 하나에 점검 항목 123개와 보조 지표 4개를 고정합니다. 모든 수집·비교·판정이 이 파일을 기준으로 하고, 회차 메타·결과·비교에 매니페스트 버전과 해시가 남습니다. 그래서 「무엇을 어떤 기준으로 쟀는가」가 회차마다 같습니다. 항목 제목·설명 문구는 한국어입니다.

```
python -m perfkit manifest check          검사(항목 수·ID·필수 칸·등급 규칙)
python -m perfkit manifest list --area GPU --set basic
python -m perfkit manifest show FRM-01
python -m perfkit manifest collectors --markdown
```

## 영역 9개

| 접두어 | 영역 | 항목 수 |
|---|---|---|
| FRM | 프레임 예산·스레드 | 5 |
| CPU | 런타임 연산 | 15 |
| SCR | 스크립트 정적 점검 | 9 |
| GPU | 렌더링(URP) | 25 |
| MEM | 메모리 | 18 |
| AST | 리소스 임포트 설정 | 20 |
| BLD | 빌드·시작 | 13 |
| THM | 기기 지속성·전력 | 7 |
| STB | 안정성·스토어 지표·로그 | 11 |

보조 지표 `AUX-01`~`AUX-04`(프로세스·스레드 CPU 점유율, 시스템 부하, 로드 구간 시간)는 123개 밖이며 항목 수 검사에 세지 않습니다(`manifest list --no-aux`로 뺍니다).

## 항목 id 읽는 법

`<영역>-<두 자리 번호>`입니다(예: `FRM-01`, `GPU-15`). 지표 하나는 `<항목>.<지표 키>`로 가리킵니다(예: `FRM-01.p95`, `MEM-04.total_used_mb`). 프로필의 판정 제외 목록, 동등 범위, 색인 `runs.jsonl`의 평탄화 칸이 모두 이 꼴을 씁니다. id는 메이저 버전이 바뀔 때만 바뀝니다.

## 항목 하나의 칸

`manifest show FRM-01`의 주요 칸입니다.

| 칸 | 뜻 |
|---|---|
| `id`·`area`·`title`·`unit` | 이름과 단위 |
| `kind` | 값의 종류: `time`·`count`·`amount`·`ratio`·`snapshot`·`bool` |
| `better` | 좋은 방향: `lower`·`higher`·`none`(기록만). 통계 「다름」의 개선·악화를 이걸로 정합니다 |
| `metrics` | 지표 키 목록. 없으면 `value` 하나. `zero_is_hard`(기준 0이 깨지면 실패), `increase_is_fail`(결정적 개수 증가가 실패) |
| `layers`·`by_layer` | 이 항목을 재는 층과, 층마다 잡음 등급(`noise`)·판정 방식(`judge`)·수집기(`collector`)·빌드 종류(`build_config`) |
| `set`·`deep_variant`·`set_basis` | 기본/심화 세트, 기본 세트 항목의 심화 변형 여부, 세트를 정한 근거 |
| `fix_risk`·`fix_risk_text` | 고칠 때의 안전 범위(아래) |
| `auto` | 자동 수집 정도: `auto`(자동)·`partial`(일부)·`manual`(손으로) |
| `criteria` | 기준 설명, 근거(`basis`), 출처 번호(`sources`), 절대 기준 규칙(`absolute`) |
| `tags` | 에뮬레이터에서 빼는 표시(`thermal`·`gpu_clock`·`gpu_usage`·`battery`·`power`·`gpu_time`·`gpu_counter`) |
| `source_text` | 원래 항목표의 위치·자동화·잡음·고치는 법 메모 |

최상위 `sources`에 출처 번호별 URL(Unity Manual·URP·Android Developers·Play 도움말·Arm·Qualcomm 등 공개 문서)이 있습니다.

## 기본/심화 세트

- **기본(`basic`, 73개)**: 매번 보는 항목입니다. 그중 5개는 심화 변형(`deep_variant: true`)이 있습니다.
- **심화(`deep`, 50개)**: 원인을 좁힐 때 보는 항목입니다(프로파일러 마커, GPU 하드웨어 카운터 등).
- `manifest list --set basic`·`--set deep`으로 고릅니다.

## 안전 범위(`fix_risk`)

이 항목이 나빠졌을 때 고치는 일을 얼마나 맡겨도 되는지 적은 칸입니다. 판정에는 쓰지 않습니다.

| 값 | 뜻 |
|---|---|
| `safe` | 기계적으로 고쳐도 안전(예: 임포트 설정을 규칙대로) |
| `review` | 고칠 수 있지만 사람이 결과를 확인 |
| `human` | 화질·동작이 바뀔 수 있어 사람이 판단 |
| `none` | 고칠 대상이 아님(측정·기록용) |

`fix_risk_text`에 「확인(전체 재임포트 유발)」처럼 이유가 붙습니다.

## 잡음 등급과 판정 방식

층마다 `by_layer.<층>.noise`와 `judge`가 있습니다.

| 잡음 등급 | 뜻 |
|---|---|
| `none` | 결정적(같은 입력이면 같은 값). L0·L1 |
| `low`·`medium`·`high`·`very_high` | 회차마다 흔들리는 정도 |
| `unknown` | 근거 없음. `high`처럼 다룹니다 |
| `pending_aa` | A/A 잡음 바닥을 아직 안 잼. 판정은 보류하고 수치만 싣습니다. 에뮬레이터(L3-E) 칸은 처음에 모두 이 값입니다 |
| `n/a` | 해당 없음 |

| 판정 방식 | 뜻 |
|---|---|
| `absolute+change` | 절대 기준과 기준 대비 변화를 둘 다 봄 |
| `change_only` | 변화만(L2·L3-E) |
| `absolute_only` | 절대 기준만 |
| `record` | 기록만 |

### 판정 규칙(1판 제안값)

| 잡음 | 경고 | 실패 | 개선 |
|---|---|---|---|
| none | 개수 +1건, 크기 +2% | 표시한 개수 증가(새 Major 등), 크기 +5% | 거꾸로 |
| low | +5%, 기준 0에서 새로 생김 | +10%, 평상시 GC 0B/frame 깨짐 | -5% |
| medium | 기준 3회 범위 밖 + 5% | 범위 밖 + 10% | 범위 밖 -5% |
| high·very_high·unknown | 단독 판정 안 함 → 범위 밖이면 「확인 필요」 | 절대 기준을 모든 회차가 넘을 때만 | 「확인 필요」 |
| pending_aa | 판단 보류 | | |

항목 판정은 지표별 (변화, 절대 기준) 중 가장 나쁜 값입니다. 순서: 실패 > 경고 > 확인 필요 > 수집 못 함 > 판단 보류 > 개선 > 통과 > 기록만.

회차 묶음(L2·L3)은 이 규칙 앞에 통계 판정(같음·다름·판단 보류)을 먼저 거칩니다. [판정과 통계](judging.md).

### 절대 기준

`criteria.absolute`의 규칙 하나는 지표·비교·문턱입니다. `op`는 나쁜 쪽 비교입니다(`gt 11` = 11 초과가 나쁨).

```json
{"metric": "p95", "op": "gt", "warn": 11, "fail": 14, "basis": "제안", "sources": ["U1", "U2"]}
```

- 메모리 한도는 `fail_by_ram_class`(RAM 등급별 문턱)입니다. 기기의 RAM 등급은 프로필 기기 정의의 `ram_class`입니다.
- 규칙마다 근거 `basis`를 둡니다: 공식(문서 수치) · 제안(1판 제안값) · 프로젝트가 정함.
- 절대 기준은 L0·L1·L3-D·console에만 씁니다. L2·L3-E에는 쓰지 않습니다.

## 에뮬레이터에서 빼는 항목

`emulator_forbidden_tags`에 든 표시(`thermal`·`gpu_clock`·`gpu_usage`·`battery`·`power`·`gpu_time`·`gpu_counter`)가 붙은 항목은 L3-E 층을 둘 수 없습니다(`manifest check`가 막습니다). 에뮬레이터의 GPU는 호스트 GPU를 거쳐 그리고, 발열·클럭·배터리는 실제 기기의 값이 아니기 때문입니다. L3-E에 남는 것은 프레임 시간·CPU 메인·렌더 시간, GC, 메모리 점유, SetPass·정점, 로드 시간, logcat 예외·종료 사유 같은 「상대 변화는 의미가 있는」 지표입니다.

## 해시와 버전

- 해시: 키 정렬·공백 없는 JSON의 sha256(`manifest hash`). 회차 메타·결과·비교에 `manifest_version`·`manifest_sha`가 남고, 프로필을 썼으면 `profile_name`·`profile_sha`도 남습니다.
- 매니페스트 해시가 다른 결과끼리 비교하면 비교 무효(종료 코드 3)로 표시합니다.
- 버전 규칙(`manifest/CHANGES.md`): 패치는 판정에 영향 없는 설명·메모, 마이너는 잡음 등급·판정 방식·기준값·지표 키·층, 메이저는 항목 추가·삭제·ID 변경.

## 바꾸는 법

- **공개 기준이 바뀌면** `checks.json`을 고치고 → `manifest check` → `CHANGES.md`에 근거 → 버전을 올립니다.
- **한 프로젝트만의 값은** 매니페스트를 고치지 않고 프로필 `[overrides]`·`[notes]`에 둡니다. 덮어쓴 결과는 `manifest --profile check`로 검사합니다.

```toml
# 에뮬레이터 A/A 잡음 바닥을 잰 뒤 L3-E 잡음 등급을 채운다
[overrides."MEM-04".layers."L3-E"]
noise = "low"
noise_hint = "A/A 5회 반복 차 0.0~0.6%(예시)"

# 이 프로젝트에서 에뮬레이터로 못 재는 지표는 층을 뺀다
[overrides."GPU-07"]
drop_layers = ["L3-E"]

# 절대 기준을 이 프로젝트 값으로 바꾼다
[[overrides."CPU-15".absolute]]
metric = "ui_open_max_frame_ms"
op = "gt"
fail = 50
basis = "프로젝트가 정함"

# 항목에 project_note로 붙는 메모(공개 매니페스트에 넣지 않는 실측·설명)
[notes]
"FRM-01" = "기준 기기 첫 기준선: <값>"
```

## 수집기 이름

`by_layer.<층>.collector`의 이름 앞부분이 구현 패키지입니다(`static.*` → `perfkit/static`). 표는 `python -m perfkit manifest collectors --markdown`으로 다시 뽑습니다(매니페스트 1.0.0).

| 수집기 | 구현 패키지 | 상태 | 층 | 항목 수 | 항목 |
|---|---|---|---|---|---|
| `build.align16k` | perfkit/build | 구현 | L1 | 1 | BLD-05 |
| `build.apk_size` | perfkit/build | 구현 | L1 | 1 | BLD-01 |
| `build.il2cpp` | perfkit/build | 구현 | L1 | 1 | BLD-07 |
| `build.layout` | perfkit/build | 구현 | L1 | 6 | MEM-09, MEM-11, MEM-12, MEM-14, MEM-17, BLD-02 |
| `build.malioc` | perfkit/build | 예정 | L1 | 1 | GPU-13 |
| `build.pad` | perfkit/build | 구현 | L1 | 1 | BLD-03 |
| `build.shader_variants` | perfkit/build | 구현 | L1 | 1 | GPU-15 |
| `console.vitals` | perfkit/console | 예정 | console | 8 | BLD-13, STB-01, STB-02, STB-03, STB-04, STB-05, STB-06, STB-07 |
| `device.exit_info` | perfkit/device | 예정 | L3-E·L3-D | 2 | STB-03, STB-10 |
| `device.frame_timing` | perfkit/device | 구현 | L3-E·L3-D | 6 | FRM-01, FRM-02, FRM-03, GPU-01, GPU-25, BLD-12 |
| `device.gc_alloc` | perfkit/device | 구현 | L3-E·L3-D | 1 | CPU-02 |
| `device.gpu_counters` | perfkit/device | 예정 | L3-D | 5 | GPU-08, GPU-09, GPU-10, GPU-16, GPU-24 |
| `device.gpu_freq` | perfkit/device | 구현 | L3-D | 2 | GPU-02, THM-03 |
| `device.logcat` | perfkit/device | 구현 | L3-E·L3-D | 3 | MEM-18, STB-08, STB-09 |
| `device.meminfo` | perfkit/device | 구현 | L3-E·L3-D | 1 | MEM-03 |
| `device.phase_markers` | perfkit/device | 구현 | L3-E·L3-D | 4 | CPU-15, BLD-11, STB-11, AUX-04 |
| `device.power` | perfkit/device | 예정 | L3-D | 1 | THM-05 |
| `device.probe_state` | perfkit/device | 예정 | L3-D | 2 | CPU-14, MEM-10 |
| `device.proc_cpu` | perfkit/device | 구현 | L3-E·L3-D | 2 | AUX-01, AUX-02 |
| `device.proc_mem` | perfkit/device | 구현 | L3-E·L3-D | 3 | MEM-01, MEM-02, MEM-08 |
| `device.render_counters` | perfkit/device | 구현 | L3-E·L3-D | 4 | CPU-08, GPU-03, GPU-04, GPU-07 |
| `device.startup` | perfkit/device | 구현 | L3-E·L3-D | 1 | BLD-10 |
| `device.sys_load` | perfkit/device | 구현 | L3-E·L3-D | 1 | AUX-03 |
| `device.thermal` | perfkit/device | 구현 | L3-D | 2 | THM-01, THM-02 |
| `device.unity_memory` | perfkit/device | 구현 | L3-E·L3-D | 4 | CPU-03, CPU-04, MEM-04, MEM-05 |
| `editor.device_profile` | perfkit/editor | 예정 | L3-D | 6 | FRM-04, CPU-01, CPU-05, CPU-07, CPU-09, CPU-13 |
| `editor.frame_debugger` | perfkit/editor | 구현 | L2 | 5 | GPU-05, GPU-06, GPU-11, GPU-21, GPU-22 |
| `editor.memory_snapshot` | perfkit/editor | 예정 | L3-D | 9 | MEM-06, MEM-07, MEM-11, MEM-12, MEM-13, MEM-14, MEM-15, MEM-16, MEM-17 |
| `editor.play_counters` | perfkit/editor | 구현 | L2 | 5 | CPU-02, CPU-09, GPU-03, GPU-04, GPU-07 |
| `editor.play_events` | perfkit/editor | 구현 | L2 | 1 | CPU-15 |
| `editor.play_profile` | perfkit/editor | 구현 | L2 | 6 | CPU-01, CPU-07, CPU-11, CPU-12, MEM-10, THM-06 |
| `editor.render_debug` | perfkit/editor | 구현 | L2 | 4 | GPU-09, GPU-18, GPU-19, GPU-24 |
| `run.conditions` | perfkit/run | 예정 | L3-D | 1 | THM-07 |
| `static.addressables_dupes` | perfkit/static | 에디터 합침 | L0 | 1 | MEM-09 |
| `static.auditor_code` | perfkit/static | 구현 | L0 | 6 | SCR-01, SCR-02, SCR-03, SCR-04, SCR-06, SCR-07 |
| `static.auditor_settings` | perfkit/static | 구현 | L0 | 1 | SCR-09 |
| `static.import_rules` | perfkit/static | 구현 | L0 | 20 | AST-01, AST-02, AST-03, AST-04, AST-05, AST-06, AST-07, AST-08, AST-09, AST-10, AST-11, AST-12, AST-13, AST-14, AST-15, AST-16, AST-17, AST-18, AST-19, AST-20 |
| `static.project_settings` | perfkit/static | 구현 | L0 | 14 | FRM-05, CPU-03, CPU-06, CPU-08, CPU-13, CPU-14, MEM-15, BLD-04, BLD-06, BLD-07, BLD-08, BLD-09, BLD-13, THM-04 |
| `static.render_assets` | perfkit/static | 구현 | L0 | 4 | GPU-05, GPU-10, GPU-11, GPU-14 |
| `static.source_scan` | perfkit/static | 구현 | L0 | 3 | SCR-05, SCR-08, GPU-18 |
| `static.ui_scan` | perfkit/static | 에디터 합침 | L0 | 2 | CPU-10, THM-06 |
| `static.urp_settings` | perfkit/static | 구현 | L0 | 7 | GPU-06, GPU-12, GPU-17, GPU-19, GPU-20, GPU-21, GPU-23 |

「상태」 칸: **구현**은 perfkit 안에 수집 코드가 있는 것, **에디터 합침**은 perfkit 혼자 값을 못 내고 프로젝트 안 에디터 도구의 JSON을 `static --editor`가 합치는 것, **예정**은 매니페스트에 이름만 있고 아직 코드가 없는 것입니다. 예정 수집기의 항목은 결과에 `not_collected`로 남습니다. 상태 목록은 `perfkit/collectors.py`의 `EDITOR_MERGED`·`PLANNED`입니다.
