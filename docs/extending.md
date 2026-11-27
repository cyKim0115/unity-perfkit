# 모듈 더하기

[← README](../README.md)

## 저장소 구조

```
perfkit/
  cli.py            서브커맨드 자동 등록(이 파일은 고치지 않습니다)
  config.py         설정(환경 변수·사용자 설정·프로젝트 프로필), config 명령
  paths.py          저장소 안 경로, 설정에서 읽는 경로
  collectors.py     수집기 이름 규칙·등록부
  manifest.py       checks.json 읽기·검증·해시·프로필 덮어쓰기, manifest 명령
  verdict.py        절대 기준·잡음 등급별 변화 판정, 통계 판정기 인터페이스, verdict 명령
  schema/           run-meta·result·comparison·manifest JSON 스키마와 표준 라이브러리 검증기, schema 명령
  static/           L0 정적 점검: Unity YAML 파서, C# 소스 검사, 임포트 규칙, 설정 스냅숏
  build/            L1 빌드 산출물 점검: APK·AAB 구성, 16KB 정렬, 빌드 레이아웃, 셰이더 변형
  diff/             L0·L1·L2 결과 비교, 결과 파일 입출력
  editor/           L2 에디터 Play 결과·프로파일러 캡처 요약·메모리 스냅숏 비교 읽기
  device/           L3 기기 준비·점검: adb·logcat 이어 받기, 기기 설정 합치기, 호스트 유휴 관문, proc·sysfs 샘플러, AVD 대조, 임대 도구 감싸기
  run/              L3 회차 실행·수집: 설치·세이브 주입·실행·끝 신호·수집, 반복·ABAB, 콜드 시작, 측정 모드 로그·CSV 파서, 지표 계산
  compare/          기준 대비 비교·통계 판정(compare), 기준선(baseline), A/A 잡음 바닥(noise), 커밋·APK 파이프라인
  report/           비교·점검 결과 HTML, 기본 테마 default.css
  index/            회차·비교 색인: runs.jsonl·comparisons.jsonl·runs.sqlite
  legacy/           옛 측정 자료 가져오기(import legacy)
manifest/
  checks.json       123항목 + 보조 지표 AUX-01~04
  CHANGES.md        기준값 변경 기록
config/
  config.example.toml    사용자 설정 예시
  profile.example.toml   프로젝트 프로필 예시
  devices/               기기 preset: SoC별 sysfs 경로·식힘·prep 기본값
examples/           스키마 예시
tests/              python -m unittest(가짜 adb·익명화 표본, 기기 없이 돎)
```

패키지 이름은 수집기 이름 앞부분과 같습니다: `static`(L0), `build`(L1), `editor`(L2·프로파일 분석), `device`·`run`(L3), `console`(아직 없음). 비교·기준선·보고·색인은 `compare`·`report`·`index`처럼 따로 둡니다.

## 새 명령·수집기 더하기

1. 자기 폴더를 만듭니다: `perfkit/<패키지>/__init__.py`. 다른 패키지와 `cli.py`는 고치지 않습니다.
2. `__init__.py`에 `register_cli(sub)`를 두면 명령이 자동 등록됩니다. 불러오지 못한 모듈은 건너뛰고 `--help` 끝에 표시됩니다.

   ```python
   def register_cli(sub):
       p = sub.add_parser("mytool", help="새 점검")
       p.add_argument("--input", required=True)
       p.set_defaults(func=_run)        # _run(args) -> 종료 코드(int)

   def _run(args):
       from perfkit.mytool.core import collect   # 무거운 import는 처리 함수 안에서
       ...
       return 0
   ```

3. 수집기는 매니페스트에 적힌 이름으로 등록합니다.

   ```python
   from perfkit.collectors import collector

   @collector("build.apk_size")
   def apk_size(ctx):
       return {"BLD-01": {"status": "ok", "values": {"apk_mb": 100.0}}}
   ```

   결과 파일은 `result` 스키마(항목마다 `status: ok` + `values`, 또는 `status: not_collected` + `reason`)로 쓰고 `python -m perfkit schema validate --manifest`로 검사합니다.
4. PC·프로젝트 값은 `perfkit.config.settings()`에서 읽습니다(`get`·`require`·`device`). 새 값이 필요하면 `config.KEYS`에 키를 더하고 예시 파일에 적습니다. 코드에 경로·패키지 이름을 넣지 않습니다.
5. 통계 판정기는 `perfkit.verdict.register_stat_judge(name, fn)`로 붙입니다. `fn(base_runs, cand_runs, *, better, noise, noise_floor_pct) -> StatResult("same"|"different"|"inconclusive", delta_pct=..., interval=...)`.
6. 테스트는 `tests/test_<패키지>.py`에 표준 라이브러리 `unittest`로 씁니다. 기기가 필요한 코드는 `tests/fakeadb.py`(가짜 adb)와 `tests/data/device/`의 익명화 표본으로 시험합니다.

```
python -m unittest discover -s tests -t .
```

## 지킬 것

- 표준 라이브러리만 씁니다(설치할 패키지 없음).
- 결정적 층의 결과는 같은 입력이면 같은 바이트가 나오게 씁니다(키 정렬, 순서 고정, 실행마다 바뀌는 값은 `<이름>.run.json`).
- 모르는 값은 지어내지 않고 `not_collected` + 이유로 남깁니다.
