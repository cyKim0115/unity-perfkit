"""기준 대비 비교·통계 판정·기준선·A/A 잡음 바닥.

    perfkit compare --base <SPEC> --cand <SPEC> [--noise <기준선|noise.json>] [--out 비교.json] [--report]
    perfkit compare --runs <SPEC> --split arm|alternate|half
    perfkit baseline register --name <이름> [--runs <SPEC>] [--noise-runs <SPEC>] [--l0 결과] [--l1 결과] [--force]
    perfkit baseline list | show <이름>
    perfkit noise aa --runs <SPEC> [--out noise.json]

SPEC: session:<이름> · baseline:<이름> · run:<id,…> · 회차 폴더 · 회차 폴더들의 부모 폴더 · L0·L1 결과 파일.
커밋·APK를 받아 빌드·실행까지 하는 것(--base-commit 등)은 아직 없다(자리만).
종료 코드: 0 정상 · 1 --fail-on에 걸림 · 2 거부(등급 혼용·입력 오류) · 3 비교 무효(해시가 다름).
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

EXIT_FAIL_ON, EXIT_REFUSED, EXIT_INVALID = 1, 2, 3


def _results_dir(arg):
    if arg:
        return Path(arg)
    from perfkit.config import ConfigError, settings

    try:
        v = settings().get("paths.results")
    except ConfigError:
        return None
    return Path(v) if v else None


def _cfg():
    from perfkit.compare.judge import CompareConfig
    from perfkit.config import settings

    return CompareConfig.from_profile(settings().profile)


def _slug(s: str) -> str:
    s = re.sub(r"^[a-z]+:", "", s)
    s = Path(s).stem if ("/" in s or "\\" in s) else s
    return re.sub(r"[^A-Za-z0-9_-]+", "-", s).strip("-")[:40] or "x"


def _print_part(p: dict, top: int) -> None:
    from perfkit.compare.judge import stat_label

    s = p["summary"]
    counts = " ".join(f"{k}={v}" for k, v in sorted(s["counts"].items()))
    head = f"[{p['layer']}{'/' + p['device_class'] if p.get('device_class') else ''}] method={p['method']} valid={p.get('valid', True)}"
    if p["method"] != "diff":
        head += (f" base={s['n_base']} cand={s['n_cand']} invalid_runs={s['n_invalid_runs']} paired={s['paired']}"
                 f" interleaved={s['interleaved']}")
        sc = " ".join(f"{k}={v}" for k, v in sorted(s.get("stat_counts", {}).items()))
        print(head + f" | {counts} | stat: {sc}")
    else:
        print(head + f" changes={s.get('changes')} | {counts}")
    for r in s.get("invalid_reasons", []):
        print(f"  INVALID: {r}")
    for w in s.get("warnings", []):
        print(f"  warn: {w}")
    if p["method"] == "diff":
        from perfkit.diff.compare import summary_lines

        for line in summary_lines(p, top)[1:]:
            print(line)
        return
    rows = []
    for iid, it in p["items"].items():
        for k, m in it["metrics"].items():
            st = m.get("stat") or {}
            if m.get("excluded"):
                lab = "판정 제외"
            else:
                lab = stat_label(st) + (" (참고)" if st.get("info_only") else "")
            ci = st.get("ci")
            ci_s = f"[{ci[0]:+.2f},{ci[1]:+.2f}]{'%' if st.get('ci_unit') == 'pct' else ''}" if ci and None not in ci else "-"
            hp = st.get("hl_pct")
            rows.append((iid, k, lab, f"{hp:+.2f}%" if hp is not None else "-", ci_s,
                         f"p={st['p']:.3g}" if "p" in st else "", m["outcome"],
                         "실기기 확인 대기" if it.get("needs_device_confirm") and st.get("direction") == "better" else ""))
    for r in rows[: top if top > 0 else None]:
        print("  " + " ".join(x for x in (f"{r[0]}.{r[1]}", r[2], r[3], r[4], r[5], f"→ {r[6]}", r[7]) if x))
    if top > 0 and len(rows) > top:
        print(f"  … {len(rows) - top}개 더(--top 0이면 모두)")


def _run_compare(args) -> int:
    from perfkit import verdict as V
    from perfkit.compare import baseline as B
    from perfkit.compare import core
    from perfkit.compare import runsets as R
    from perfkit.config import settings
    from perfkit.diff import resultio
    from perfkit.manifest import load_effective
    from perfkit.schema import validate

    for opt in ("base_commit", "cand_commit", "base_apk", "cand_apk"):
        if getattr(args, opt):
            print(f"error: --{opt.replace('_', '-')}는 아직 없음(커밋·APK를 빌드해 회차를 재는 단계는 다음 판). "
                  "지금은 이미 잰 회차 묶음(session:·폴더·기준선)을 준다")
            return EXIT_REFUSED
    rd = _results_dir(args.results)
    cfg = _cfg()
    if args.bootstrap:
        cfg.iters = args.bootstrap
    man = load_effective()
    try:
        if args.runs:
            if args.base or args.cand:
                print("error: --runs와 --base·--cand는 함께 쓰지 않음")
                return EXIT_REFUSED
            whole = R.resolve(args.runs, results_dir=rd)
            b_runs, c_runs = R.split(whole.runs, args.split or "arm")
            bl = args.label_base or f"{_slug(args.runs)}:{args.split or 'arm'}-1"
            cl = args.label_cand or f"{_slug(args.runs)}:{args.split or 'arm'}-2"
            base_sides = [R.Side(bl, b_runs, spec=args.runs)]
            cand_sides = [R.Side(cl, c_runs, spec=args.runs)]
            pairing = args.pairing if args.pairing != "auto" else {"arm": "round", "alternate": "order",
                                                                    "half": "none"}[args.split or "arm"]
        else:
            if not args.base or not args.cand:
                print("error: --base와 --cand(또는 --runs)가 필요")
                return EXIT_REFUSED
            base_sides = [R.resolve(s, results_dir=rd, label=args.label_base) for s in args.base]
            cand_sides = [R.resolve(s, results_dir=rd, label=args.label_cand) for s in args.cand]
            pairing = args.pairing
    except (R.RunsetError, OSError, ValueError) as exc:
        print(f"error: {exc}")
        return EXIT_REFUSED

    def merged(sides):
        s = R.Side(" + ".join(x.label for x in sides), spec=" ".join(x.spec for x in sides))
        for x in sides:
            s.runs += x.runs
            s.results += x.results
            s.result_refs += x.result_refs
            s.baseline = s.baseline or x.baseline
        s.runs = R.sort_runs(s.runs)
        return s

    base, cand = merged(base_sides), merged(cand_sides)
    parts = []
    try:
        if cand.results:
            parts += core.compare_results(base, cand, manifest=man)
        if base.runs or cand.runs:
            noise_doc, src = None, None
            if args.noise:
                p = Path(args.noise)
                noise_doc = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else B.load_baseline(args.noise)
                src = args.noise
            elif base.baseline:
                noise_doc, src = base.baseline, f"baseline:{base.baseline['name']}"
            cls = V.ensure_same_class([r.meta for r in base.runs + cand.runs])
            nf = B.noise_for(noise_doc, cls)
            parts.append(core.compare_runsets(base, cand, manifest=man, cfg=cfg, pairing=pairing,
                                              noise_floor=nf, noise_source=src if nf else None, settings=settings(),
                                              include_invalid=args.include_invalid))
    except V.ClassMixError as exc:
        print(f"error: 거부 — {exc}")
        return EXIT_REFUSED
    except (R.RunsetError, ValueError) as exc:
        print(f"error: {exc}")
        return EXIT_REFUSED
    if not parts:
        print("error: 비교할 것이 없음")
        return EXIT_REFUSED
    for p in parts:
        errs = validate(p, "comparison")
        if errs:
            print(f"error: comparison 스키마에 맞지 않음: {errs[0]}")
            return 1
    doc = parts[0] if len(parts) == 1 else core.bundle(parts, base_label=base.label, cand_label=cand.label)

    out = Path(args.out) if args.out else None
    if out is None and not args.no_save and rd is not None:
        cid = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{_slug(base.label)}-vs-{_slug(cand.label)}"
        out = rd / "comparisons" / f"{cid}.json"
    if out is not None:
        resultio.write(out, doc)
    if args.json:
        sys.stdout.write(resultio.dumps(doc))
    else:
        for p in parts:
            _print_part(p, args.top)
        if out is not None:
            print(f"saved: {out}")
    if args.report is not None:
        from perfkit.report.render import render_to

        rp = Path(args.report) if args.report else (out.with_suffix(".html") if out is not None else None)
        if rp is None:
            print("error: --report 경로가 필요(저장하지 않는 비교)")
            return EXIT_REFUSED
        render_to(doc, rp)
        if not args.json:
            print(f"report: {rp}")
    if not doc.get("valid", True):
        return EXIT_INVALID
    if args.fail_on:
        if args.fail_on == "worse":
            if core.worst_stat(doc) == "worse":
                return EXIT_FAIL_ON
        else:
            bad = {"fail"} if args.fail_on == "fail" else {"fail", "warn"}
            if any(it["outcome"] in bad for p in parts for it in p["items"].values()):
                return EXIT_FAIL_ON
    return 0


def _gate_of(cfg):
    return lambda layer: cfg.layer_cfg(layer).get("gate") or {}


def _run_baseline_register(args) -> int:
    from perfkit import verdict as V
    from perfkit.compare import baseline as B
    from perfkit.compare import runsets as R

    rd = _results_dir(args.results)
    try:
        runs = R.resolve(args.runs, results_dir=rd).runs if args.runs else None
        noise_runs = R.resolve(args.noise_runs, results_dir=rd).runs if args.noise_runs else None
        det = {}
        for layer, f in (("L0", args.l0), ("L1", args.l1)):
            if f:
                det[layer] = Path(f)
        if not runs and not det:
            print("error: --runs나 --l0·--l1 중 하나는 있어야 함")
            return EXIT_REFUSED
        p, doc = B.register(args.name, runs=runs, noise_runs=noise_runs, deterministic=det,
                            gate_of=_gate_of(_cfg()), force=args.force, results_dir=rd)
    except V.ClassMixError as exc:
        print(f"error: 거부 — {exc}")
        return EXIT_REFUSED
    except (R.RunsetError, ValueError, OSError) as exc:
        print(f"error: {exc}")
        return EXIT_REFUSED
    print(f"saved: {p}")
    for cls, e in doc["classes"].items():
        print(f"  {cls}({e['layer']}): runs={e['n_runs']} valid={e['n_valid']} invalid={len(e['invalid_runs'])} "
              f"metrics={len(e['metrics'])} noise={'yes' if e.get('noise_floor') else 'no'}")
    for layer, d in sorted(doc.get("deterministic", {}).items()):
        print(f"  {layer}: {d['ref']}")
    return 0


def _run_baseline_list(args) -> int:
    from perfkit.compare import baseline as B

    for b in B.list_baselines():
        cls = ", ".join(f"{c}(valid={v['n_valid']}{', noise' if v['noise'] else ''})" for c, v in b["classes"].items())
        print(f"{b['name']:24} {b.get('updated_at') or '-':26} {cls} {' '.join(b['deterministic'])}")
    return 0


def _run_baseline_show(args) -> int:
    from perfkit.compare import baseline as B
    from perfkit.compare import runsets as R

    try:
        doc = B.load_baseline(args.name)
    except R.RunsetError as exc:
        print(f"error: {exc}")
        return EXIT_REFUSED
    if args.json:
        print(json.dumps(doc, ensure_ascii=False, indent=1))
        return 0
    print(f"{doc['name']}  manifest {doc['manifest_version']} {doc['manifest_sha'][:12]}  updated {doc.get('updated_at')}")
    for cls, e in doc.get("classes", {}).items():
        print(f"[{cls} / {e['layer']}] runs={e['n_runs']} valid={e['n_valid']} probe={e.get('probe_hash')} "
              f"quality={e.get('quality_hash')} scenario={e.get('scenario')}")
        nf = e.get("noise_floor") or {}
        for k, m in e.get("metrics", {}).items():
            n = nf.get(k) or {}
            sd = n.get("sigma_d_pct")
            print(f"  {k:34} median={m['median']:.4g} [{m['min']:.4g}, {m['max']:.4g}] n={m['n']}"
                  + (f"  σd={sd:.2f}% MDE6={n['mde_pct']['6']:.2f}%" if sd is not None and n.get("mde_pct") else ""))
    for layer, d in sorted(doc.get("deterministic", {}).items()):
        print(f"[{layer}] {d['ref']} sha256={d['sha256'][:12]}")
    return 0


def _run_noise_aa(args) -> int:
    from perfkit import verdict as V
    from perfkit.compare import baseline as B
    from perfkit.compare import runsets as R
    from perfkit.compare import stats as S
    from perfkit.diff import resultio

    if args.apk or args.device:
        print("error: --apk·--device로 A/A 회차를 직접 돌리는 것은 다음 판. 지금은 잰 회차(--runs)만")
        return EXIT_REFUSED
    rd = _results_dir(args.results)
    try:
        runs = R.resolve(args.runs, results_dir=rd).runs
        cls = V.ensure_same_class([r.meta for r in runs])
        doc = B.noise_doc(runs, gate=_cfg().layer_cfg(V.layer_for_class(cls)).get("gate") or {}, label=args.runs)
    except V.ClassMixError as exc:
        print(f"error: 거부 — {exc}")
        return EXIT_REFUSED
    except (R.RunsetError, ValueError) as exc:
        print(f"error: {exc}")
        return EXIT_REFUSED
    if args.out:
        resultio.write(args.out, doc)
    print(f"[{doc['device_class']} / {doc['layer']}] valid={doc['n_valid']} invalid={len(doc['invalid_runs'])}")
    for b in doc["invalid_runs"]:
        print(f"  무효 {b['run_id']}: {b['reason']}")
    for w in doc["warnings"]:
        print(f"  warn: {w}")
    print(f"  {'지표':34} {'중앙값':>10} {'CV%':>6} {'σd%':>6} {'MDE5%':>6} {'MDE6%':>6} {'n(MDE=' + str(args.target) + '%)':>12}")
    for k, m in doc["metrics"].items():
        sd = m.get("sigma_d_pct")
        if sd is None:
            print(f"  {k:34} {m['median']:>10.4g}      -")
            continue
        need = S.runs_needed(sd, args.target) if sd > 0 else 2
        print(f"  {k:34} {m['median']:>10.4g} {m['cv_pct']:>6.2f} {sd:>6.2f} {m['mde_pct']['5']:>6.2f} "
              f"{m['mde_pct']['6']:>6.2f} {need:>12}")
    if args.out:
        print(f"saved: {args.out}")
    return 0


def register_cli(sub) -> None:
    c = sub.add_parser("compare", help="기준 대비 비교(L0·L1 diff + 회차 묶음 통계 판정)")
    c.add_argument("--base", action="append", metavar="SPEC", help="기준(여러 번 줄 수 있음: 결과 파일·회차 묶음)")
    c.add_argument("--cand", action="append", metavar="SPEC", help="후보")
    c.add_argument("--runs", metavar="SPEC", help="한 묶음을 --split으로 나눠 비교(ABAB·A/A)")
    c.add_argument("--split", choices=("arm", "alternate", "half"), help="arm=A·B, alternate=번갈아, half=앞·뒤")
    c.add_argument("--pairing", choices=("auto", "round", "order", "none"), default="auto")
    c.add_argument("--noise", metavar="기준선|noise.json", help="A/A 잡음 바닥(없으면 기준선의 것, 그것도 없으면 등급별 기본값)")
    c.add_argument("--results", help="결과 폴더(기본: 설정 paths.results)")
    c.add_argument("--include-invalid", action="store_true", help="진단용: 관문에서 무효인 회차도 통계에 넣음(경고를 남김)")
    c.add_argument("--label-base")
    c.add_argument("--label-cand")
    c.add_argument("--bootstrap", type=int, help="부트스트랩 횟수(기본: 프로필 [compare] bootstrap 또는 10000)")
    c.add_argument("--out", help="비교 결과 JSON(기본: <결과 폴더>/comparisons/<id>.json)")
    c.add_argument("--no-save", action="store_true")
    c.add_argument("--report", nargs="?", const="", help="HTML 보고도 쓴다(경로를 안 주면 비교 JSON 옆)")
    c.add_argument("--json", action="store_true", help="비교 결과 JSON을 표준 출력으로")
    c.add_argument("--top", type=int, default=40, help="출력할 지표 줄 수(0이면 모두)")
    c.add_argument("--fail-on", choices=("warn", "fail", "worse"), help="이 판정이 있으면 종료 코드 1(worse=통계 악화)")
    for side in ("base", "cand"):
        c.add_argument(f"--{side}-commit", help=argparse_suppress())
        c.add_argument(f"--{side}-apk", help=argparse_suppress())
    c.set_defaults(func=_run_compare)

    b = sub.add_parser("baseline", help="기준선 등록·목록·보기(등급별)")
    s2 = b.add_subparsers(dest="baseline_cmd", metavar="<sub>")
    r = s2.add_parser("register", help="기준선 등록(같은 이름에 다른 등급을 더함)")
    r.add_argument("--name", required=True)
    r.add_argument("--runs", metavar="SPEC", help="기준 회차 묶음")
    r.add_argument("--noise-runs", metavar="SPEC", help="같은 빌드 A/A 회차(잡음 바닥)")
    r.add_argument("--l0", help="L0 결과 파일")
    r.add_argument("--l1", help="L1 결과 파일")
    r.add_argument("--results", help="결과 폴더")
    r.add_argument("--force", action="store_true", help="같은 등급이 있으면 바꿈")
    r.set_defaults(func=_run_baseline_register)
    s2.add_parser("list", help="기준선 목록").set_defaults(func=_run_baseline_list)
    sh = s2.add_parser("show", help="기준선 보기")
    sh.add_argument("name")
    sh.add_argument("--json", action="store_true")
    sh.set_defaults(func=_run_baseline_show)
    b.set_defaults(func=lambda a: (b.print_help(), 2)[1])

    n = sub.add_parser("noise", help="같은 빌드 A/A 회차로 잡음 바닥(σd·MDE)")
    s3 = n.add_subparsers(dest="noise_cmd", metavar="<sub>")
    aa = s3.add_parser("aa", help="A/A 회차 묶음 → 지표별 CV·σd·MDE·필요 회차")
    aa.add_argument("--runs", required=True, metavar="SPEC")
    aa.add_argument("--results")
    aa.add_argument("--target", type=float, default=5.0, help="필요 회차를 계산할 목표 MDE %(기본 5)")
    aa.add_argument("--out", help="noise JSON")
    aa.add_argument("--apk", help=argparse_suppress())
    aa.add_argument("--device", help=argparse_suppress())
    aa.set_defaults(func=_run_noise_aa)
    n.set_defaults(func=lambda a: (n.print_help(), 2)[1])


def argparse_suppress():
    import argparse

    return argparse.SUPPRESS
