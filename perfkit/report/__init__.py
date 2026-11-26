"""비교·점검 결과 HTML 보고 `perfkit report`.

    perfkit report <비교.json|결과.json> [--out 보고.html] [--css 테마.css] [--title 제목]

기본 출력: 입력 옆 같은 이름 .html. 테마: --css → 프로젝트 프로필 [report] css → 저장소 기본 테마.
"""

from __future__ import annotations


def register_cli(sub) -> None:
    p = sub.add_parser("report", help="비교·점검 결과를 한 파일 HTML로")
    p.add_argument("file", help="comparison·comparison-set·result JSON")
    p.add_argument("--out", help="HTML 경로(기본: 입력 옆 .html)")
    p.add_argument("--css", help="테마 CSS 파일(기본: 프로필 [report] css, 없으면 저장소 기본)")
    p.add_argument("--title")
    p.set_defaults(func=_run)


def _run(args) -> int:
    import json
    from pathlib import Path

    from perfkit.report.render import render_to

    src = Path(args.file)
    try:
        doc = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: 읽기 실패 {src}: {exc}")
        return 2
    out = Path(args.out) if args.out else src.with_suffix(".html")
    try:
        render_to(doc, out, css_path=args.css, title=args.title)
    except (ValueError, KeyError) as exc:
        print(f"error: {exc}")
        return 2
    print(f"report: {out}")
    return 0
