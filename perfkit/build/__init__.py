"""L1 빌드 산출물 점검: `perfkit build`.

입력(하나 이상):
- `--apk`: APK 또는 AAB. 크기·구성(BLD-01·02), PAD 팩(BLD-03, AAB만), 16KB 정렬(BLD-05), libil2cpp.so(BLD-07)
- `--log`: Unity 빌드 로그. 셰이더 변형 수(GPU-15)
- `--layout`: Addressables `buildlayout.json`. 번들·자산 종류별 크기, 중복 의존(MEM-09·11·12·14·17, BLD-02)
"""

from __future__ import annotations

from perfkit.build import registry as _registry  # noqa: F401  수집기 이름 등록


def register_cli(sub) -> None:
    p = sub.add_parser("build", help="L1 빌드 산출물 점검(APK·AAB, 빌드 로그, Addressables 레이아웃)")
    p.add_argument("--apk", help="APK 또는 AAB")
    p.add_argument("--log", help="Unity 빌드 로그(셰이더 변형 집계)")
    p.add_argument("--layout", help="Addressables buildlayout.json")
    p.add_argument("--stamp", help="결과 폴더 이름(기본: APK가 든 폴더 이름)")
    p.add_argument("--out", help="결과 JSON 경로(기본: 결과 폴더/builds/<stamp>/l1.json)")
    p.add_argument("--top", type=int, default=20, help="상위 목록 길이")
    p.add_argument("--editor", help="에디터 점검 결과(audit.json)의 L1 값(셰이더 키워드 수 등)을 합침")
    p.add_argument("--no-run-meta", action="store_true", help="옆 파일 <이름>.run.json을 쓰지 않음")
    p.add_argument("--print", action="store_true", help="파일 대신 결과 JSON을 표준 출력으로")
    p.set_defaults(func=_run)


def _run(args) -> int:
    from perfkit.build.collect import default_out, run, summary_lines
    from perfkit.diff import resultio
    from perfkit.schema import validate

    if not (args.apk or args.log or args.layout):
        print("error: --apk·--log·--layout 중 하나 이상")
        return 2
    result, meta = run(args.apk, log=args.log, layout=args.layout, top=args.top)
    if args.editor:
        from perfkit.static.editor_merge import EditorAuditError, merge
        try:
            result = merge(result, resultio.read(args.editor), layer="L1")
        except EditorAuditError as exc:
            print(f"error: {exc}")
            return 2
        meta["editor"] = str(args.editor)
    errs = validate(result, "result")
    if errs:
        for e in errs[:20]:
            print(f"schema: {e}")
        return 1
    if args.print:
        print(resultio.dumps(result), end="")
        return 0
    out = args.out or default_out(result, args.stamp, args.apk)
    p, _ = resultio.write(out, result, None if args.no_run_meta else meta)
    for line in summary_lines(result):
        print(line)
    print(f"seconds={meta['seconds']} out={p}")
    return 0
