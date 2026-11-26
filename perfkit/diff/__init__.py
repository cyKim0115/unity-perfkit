"""결정적 층(L0·L1) 결과 비교 `perfkit diff`와 결과 파일 입출력(`resultio`).

    perfkit diff <기준.json> <후보.json> [--out 비교.json] [--fail-on warn|fail]
    perfkit diff <후보.json>                      기준 없이 절대 기준만
"""

from __future__ import annotations


def register_cli(sub) -> None:
    p = sub.add_parser("diff", help="L0·L1 결과 두 개 비교(항목 판정, 새로 생긴·없어진 위반)")
    p.add_argument("files", nargs="+", metavar="결과.json", help="기준 후보 순서(하나면 후보만)")
    p.add_argument("--out", help="비교 결과(comparison 스키마) JSON 경로")
    p.add_argument("--fail-on", choices=["warn", "fail"], help="이 판정 이상인 항목이 있으면 종료 코드 1")
    p.add_argument("--list", type=int, default=50, help="항목마다 새로 생긴·없어진 위반을 몇 개까지 적을지")
    p.add_argument("--top", type=int, default=15, help="요약에 보일 항목 수")
    p.set_defaults(func=_run)


def _run(args) -> int:
    from perfkit.diff import resultio
    from perfkit.diff.compare import diff_results, summary_lines
    from perfkit.schema import validate

    if len(args.files) > 2:
        print("error: 결과 파일은 하나(후보) 또는 둘(기준 후보)")
        return 2
    base_p, cand_p = (args.files[0], args.files[1]) if len(args.files) == 2 else (None, args.files[0])
    base = resultio.read(base_p) if base_p else None
    cand = resultio.read(cand_p)
    for name, r in (("기준", base), ("후보", cand)):
        if r is None:
            continue
        errs = validate(r, "result")
        if errs:
            print(f"error: {name} 결과가 result 스키마에 맞지 않음: {errs[0]}")
            return 2
    try:
        cmp = diff_results(base, cand, base_ref=base_p, cand_ref=cand_p, list_max=args.list)
    except ValueError as exc:
        print(f"error: {exc}")
        return 2
    errs = validate(cmp, "comparison")
    if errs:
        print(f"error: 비교 결과가 comparison 스키마에 맞지 않음: {errs[0]}")
        return 1
    if args.out:
        resultio.write(args.out, cmp)
    for line in summary_lines(cmp, args.top):
        print(line)
    if args.fail_on:
        bad = {"fail"} if args.fail_on == "fail" else {"fail", "warn"}
        if any(d["outcome"] in bad for d in cmp["items"].values()):
            return 1
    return 0
