"""L2 에디터 Play·프로파일러 캡처·메모리 스냅숏 결과 읽기: `perfkit editor`.

에디터 쪽 측정 도구(Unity 프로젝트 안, MCP 등으로 부름)가 쓴 JSON을 perfkit 형식으로 바꾼다.
perfkit은 Unity를 직접 부르지 않는다.

    perfkit editor play <play.json>... [--out <L2 결과.json>] [--tree <작업 트리> [--dirty-ignore 틀]...]
                                                              에디터 Play 원값 → L2 결과(result 스키마).
                                                              여럿이면 회차별 값 목록으로 묶음(잡음 「중간」 항목 판정용).
                                                              --tree: 그 작업 트리의 HEAD·추적 파일 변경을 source.tree·dirty에
    perfkit editor profile <요약.json> [--top 20]              프로파일러 캡처 요약 보기(FRM-04 판별 포함)
    perfkit editor memory <비교.json> [--out <요약.json>]      메모리 스냅숏 비교 요약(MEM-06·07 참고값)

L2 결과 두 개는 `perfkit diff <기준> <후보>`로 비교한다(같은 날 같은 에디터에서 잰 추세용, 기기 수치와 섞지 않음).
"""

from __future__ import annotations

from perfkit.editor import registry as _registry  # noqa: F401  수집기 이름 등록


def register_cli(sub) -> None:
    p = sub.add_parser("editor", help="L2 에디터 Play·프로파일러 캡처·메모리 스냅숏 결과 읽기")
    esub = p.add_subparsers(dest="editor_command", metavar="<editor-command>")

    pp = esub.add_parser("play", help="에디터 Play 원값(perfkit/editor-play@1) → L2 결과")
    pp.add_argument("play", metavar="play.json", nargs="+", help="같은 조건 회차 여럿이면 한 결과로 묶음")
    pp.add_argument("--out", help="L2 결과 경로(기본: 결과 폴더/editor/<날짜>-<커밋8>/<이름>.l2.json)")
    pp.add_argument("--print", action="store_true", help="파일 대신 결과 JSON을 표준 출력으로")
    pp.add_argument("--tree", metavar="경로", help="측정한 작업 트리: HEAD·추적 파일 변경을 기록(dirty). 측정 직후에 부름")
    pp.add_argument("--dirty-ignore", metavar="틀", action="append", default=[],
                    help="dirty 판정에서 뺄 경로 틀(fnmatch, 여러 번). 에디터가 늘 다시 쓰는 파일 등")
    pp.set_defaults(func=_run_play)

    pr = esub.add_parser("profile", help="프로파일러 캡처 요약(perfkit/editor-profile@1) 보기")
    pr.add_argument("summary", metavar="요약.json")
    pr.add_argument("--top", type=int, default=20)
    pr.set_defaults(func=_run_profile)

    pm = esub.add_parser("memory", help="메모리 스냅숏 비교 결과 요약(MEM-06·07 참고값)")
    pm.add_argument("compare", metavar="비교.json")
    pm.add_argument("--out", help="요약 JSON 경로")
    pm.set_defaults(func=_run_memory)

    p.set_defaults(func=lambda args: (p.print_help(), 2)[1])


def default_out(result: dict, play_path: str):
    from pathlib import Path

    from perfkit import paths

    date = (result.get("collected_at") or "")[:10].replace("-", "") or "nodate"
    commit = (result["source"].get("commit") or "nocommit")[:8]
    return paths.results_dir() / "editor" / f"{date}-{commit}" / (Path(play_path).stem + ".l2.json")


def _run_play(args) -> int:
    from perfkit.diff import resultio
    from perfkit.editor.play import merge_runs, summary_lines, to_result, tree_state
    from perfkit.schema import validate

    try:
        refs = [str(p) for p in args.play]
        result = merge_runs([to_result(resultio.read(p), ref=p) for p in refs], refs)
        if args.tree:
            ts = tree_state(args.tree, commit=result["source"].get("commit"), ignore=args.dirty_ignore)
            result["source"]["tree"] = ts
            result["source"]["dirty"] = bool(result["source"].get("dirty")) or ts["dirty"]
    except (OSError, ValueError) as exc:
        print(f"error: {exc}")
        return 2
    errs = validate(result, "result")
    if errs:
        for e in errs[:20]:
            print(f"schema: {e}")
        return 1
    if args.print:
        print(resultio.dumps(result), end="")
        return 0
    out = args.out or default_out(result, args.play[0])
    resultio.write(out, result)
    for line in summary_lines(result):
        print(line)
    print(f"out={out}")
    return 0


def _run_profile(args) -> int:
    from perfkit.diff import resultio
    from perfkit.editor.profile import ProfileSummaryError, summary_lines

    try:
        summary = resultio.read(args.summary)
        lines = summary_lines(summary, args.top)
    except (OSError, ValueError, ProfileSummaryError) as exc:
        print(f"error: {exc}")
        return 2
    for line in lines:
        print(line)
    return 0


def _run_memory(args) -> int:
    from perfkit.diff import resultio
    from perfkit.editor.memory import MemoryCompareError, summarize, summary_lines

    try:
        data = resultio.read(args.compare)
        summary = summarize(data, ref=str(args.compare))
    except (OSError, ValueError, MemoryCompareError) as exc:
        print(f"error: {exc}")
        return 2
    if args.out:
        resultio.write(args.out, summary)
    for line in summary_lines(summary):
        print(line)
    return 0
