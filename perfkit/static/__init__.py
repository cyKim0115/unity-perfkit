"""L0 정적 점검: `perfkit static`.

Unity 프로젝트 폴더를 읽기만 한다(쓰기 없음, git 상태도 바꾸지 않음). 에디터 없이 되는 것만 값을 내고,
에디터가 있어야 아는 항목은 「수집 못 함: 에디터 필요 …」로 남긴다.

설정(프로젝트 프로필 [static], 사용자 설정 [static]이 덮음)::

    [static]
    project = "<Unity 프로젝트 폴더>"        # --project, PERFKIT_STATIC_PROJECT로도
    source_dirs = ["Assets/Scripts"]         # 스크립트 규칙을 볼 폴더(기본 Assets)
    asset_dirs = ["Assets"]                  # 임포트 규칙을 볼 폴더
    exclude = ["Assets/ThirdParty/**"]       # 뺄 glob
    [static.defines]                         # 전처리 기호(기본: 에디터 꺼짐, Android 켜짐)
    DEVELOPMENT_BUILD = false
    [static.import]                          # 임포트 규칙 문턱
    texture_size_limit = 2048
"""

from __future__ import annotations

from perfkit.static import registry as _registry  # noqa: F401  수집기 이름 등록


def register_cli(sub) -> None:
    p = sub.add_parser("static", help="L0 정적 점검(스크립트 규칙·임포트 설정·프로젝트 설정)")
    p.add_argument("--project", help="Unity 프로젝트 폴더(기본: 프로필 [static] project)")
    p.add_argument("--out", help="결과 JSON 경로(기본: 결과 폴더/static/<날짜>-<커밋8>/source.json)")
    p.add_argument("--editor", help="에디터 점검 결과(audit.json)를 합침(perfkit/editor-audit@1)")
    p.add_argument("--no-run-meta", action="store_true", help="옆 파일 <이름>.run.json을 쓰지 않음")
    p.add_argument("--print", action="store_true", help="파일 대신 결과 JSON을 표준 출력으로")
    p.set_defaults(func=_run)


def _run(args) -> int:
    from perfkit.diff import resultio
    from perfkit.schema import validate
    from perfkit.static.collect import default_out, run, static_config, summary_lines

    cfg = static_config(args.project)
    if not cfg.get("project"):
        print("error: Unity 프로젝트 폴더가 없음(--project 또는 프로필 [static] project)")
        return 2
    result, meta = run(cfg["project"], cfg)
    if args.editor:
        from perfkit.static.editor_merge import EditorAuditError, merge
        try:
            result = merge(result, resultio.read(args.editor), layer="L0")
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
    out = args.out or default_out(result)
    p, rp = resultio.write(out, result, None if args.no_run_meta else meta)
    for line in summary_lines(result):
        print(line)
    print(f"seconds={meta['seconds']} out={p}")
    return 0
