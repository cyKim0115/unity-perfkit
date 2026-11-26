"""회차·비교 색인 `perfkit index`.

    perfkit index rebuild [--results <결과 폴더>]   runs.jsonl·comparisons.jsonl·runs.sqlite를 처음부터 다시 만든다
    perfkit index check   [--results <결과 폴더>]   회차 폴더 수 = jsonl 줄 수 = sqlite run 수인지

sqlite 표: run · metric(run_id, item, key, value) · comparison(지표 한 줄) · artifact(run_id, kind, path) · baseline.
"""

from __future__ import annotations


def register_cli(sub) -> None:
    p = sub.add_parser("index", help="회차·비교를 runs.jsonl·runs.sqlite에 색인")
    s2 = p.add_subparsers(dest="index_cmd", metavar="<sub>")
    r = s2.add_parser("rebuild", help="색인을 처음부터 다시 만든다")
    r.add_argument("--results", help="결과 폴더(기본: 설정 paths.results)")
    r.set_defaults(func=_rebuild)
    c = s2.add_parser("check", help="색인 수가 회차 폴더 수와 같은지")
    c.add_argument("--results")
    c.set_defaults(func=_check)
    p.set_defaults(func=lambda a: (p.print_help(), 2)[1])


def _dir(args):
    from pathlib import Path

    if args.results:
        return Path(args.results)
    from perfkit.paths import results_dir

    return results_dir()


def _rebuild(args) -> int:
    from perfkit.index.build import rebuild

    try:
        st = rebuild(_dir(args))
    except Exception as exc:  # 설정 없음·쓰기 실패
        print(f"error: {type(exc).__name__}: {exc}")
        return 2
    print(" ".join(f"{k}={v}" for k, v in st.items()))
    return 0


def _check(args) -> int:
    from perfkit.index.build import check

    try:
        st = check(_dir(args))
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}")
        return 2
    print(" ".join(f"{k}={v}" for k, v in st.items()))
    return 0 if st["ok"] else 1
