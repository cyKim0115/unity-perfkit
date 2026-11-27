"""기준 대비 비교·통계 판정·기준선·A/A 잡음 바닥.

    perfkit compare --base <SPEC> --cand <SPEC> [--noise <기준선|noise.json>] [--out 비교.json] [--report]
    perfkit compare --runs <SPEC> --split arm|alternate|half
    perfkit baseline register --name <이름> [--runs <SPEC>] [--noise-runs <SPEC>] [--l0 결과] [--l1 결과] [--l2 결과] [--force]
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


def _pipeline_sides(args, rd):
    """--base-commit·--cand-commit·--base-apk·--cand-apk → (기준 Side, 후보 Side) 또는 종료 코드."""
    from perfkit.compare import pipeline as PL
    from perfkit.compare import runsets as R
    from perfkit.config import settings

    s = settings()
    if rd is None:
        print("error: 결과 폴더가 없음(--results 또는 설정 paths.results)")
        return EXIT_REFUSED
    if bool(args.base_commit) == bool(args.base_apk) or bool(args.cand_commit) == bool(args.cand_apk):
        print("error: 기준·후보마다 --*-commit과 --*-apk 중 하나만 준다")
        return EXIT_REFUSED
    if args.base or args.cand or args.runs:
        print("error: --*-commit·--*-apk는 --base·--cand·--runs와 함께 쓰지 않음")
        return EXIT_REFUSED
    if args.cand_commit == "HEAD" or (args.cand_commit or "").startswith("HEAD@"):
        print("error: HEAD 대신 커밋 해시를 준다(bisect-step은 --cand-head <저장소>)")
        return EXIT_REFUSED
    try:
        sides = []
        for which, commit, apk in (("base", args.base_commit, args.base_apk), ("cand", args.cand_commit, args.cand_apk)):
            label = getattr(args, f"label_{which}") or (commit[:8] if commit else None)
            if commit:
                b = PL.resolve_commit(commit, s, rd, label or commit[:8], rebuild=args.rebuild, dry_run=args.dry_run)
            else:
                b = PL.from_apk(apk, s, label or "")
                PL.remember_apk(b, s, rd)
            if not b.label:
                b.label = b.short
            if b.probe is False:
                b.label += " (호환 모드)"
            sides.append(b)
        base_b, cand_b = sides
        for b in sides:
            print(f"[{b.label}] apk={b.apk} commit={b.commit} probe={b.probe} built={b.built_now} cached={b.cached}"
                  + (f" notes={b.notes}" if b.notes else ""))
        if not args.no_l1:
            for b in sides:
                PL.l1_for(b, s, rd)
        if args.dry_run:
            print("dry-run: 기기 실행 없이 끝")
            return 0
        out_sides = []
        if args.no_run:
            for b in sides:
                out_sides.append(R.Side(b.label, [], spec=str(b.apk)))
        else:
            if args.sessions:
                names = [x for x in args.sessions.split(",") if x]
            else:
                if any(b.built_now for b in sides):
                    PL.revive_device(args.device, s)
                sess = args.session or PL.session_name(base_b, cand_b, args.scenario)
                names = PL.run_abab(base_b, cand_b, device=args.device, rounds=args.rounds, scenario=args.scenario,
                                    seconds=args.seconds, session=sess, max_reruns=args.max_reruns,
                                    extra=args.run_arg or [], results_dir=rd).sessions
            runs = []
            for name in names:
                runs += R.resolve(f"session:{name}", results_dir=rd).runs
            a_runs, b_runs = R.split(runs, "arm")
            out_sides = [R.Side(base_b.label, a_runs, spec=f"session:{','.join(names)}"),
                         R.Side(cand_b.label, b_runs, spec=f"session:{','.join(names)}")]
        for side, b in zip(out_sides, sides):
            if b.l1 is not None:
                from perfkit.diff import resultio

                side.results.append(resultio.read(b.l1))
                side.result_refs.append(str(b.l1))
        return out_sides[0], out_sides[1]
    except PL.PipelineError as exc:
        print(f"error: {exc}")
        return EXIT_REFUSED


BISECT_CODE = {"worse": 1, "same": 0, "unknown": 125}


def _run_bisect(args) -> int:
    """git bisect run용: 악화 1 · 같음 0 · 판단 보류 125 · 그 밖의 오류 128(bisect 중단)."""
    import subprocess

    from perfkit.compare import core

    if args.cand_head:
        try:
            args.cand_commit = subprocess.run(["git", "-C", args.cand_head, "rev-parse", "HEAD"], capture_output=True,
                                              text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            print(f"error: HEAD를 못 읽음: {exc}")
            return 128
    args.fail_on = None
    try:
        rc = _run_compare(args)
    except Exception as exc:  # bisect가 엉뚱한 커밋을 나쁨으로 찍지 않게 중단
        print(f"error: {type(exc).__name__}: {exc}")
        return 128
    if rc == EXIT_INVALID:
        print("bisect: 비교 무효 → 판단 보류(125)")
        return 125
    if rc != 0 or getattr(args, "_doc", None) is None:
        return 128
    w = core.worst_stat(args._doc)
    print(f"bisect: {w} → {BISECT_CODE[w]}")
    return BISECT_CODE[w]


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

    rd = _results_dir(args.results)
    cfg = _cfg()
    if args.bootstrap:
        cfg.iters = args.bootstrap
    man = load_effective()
    piped = None
    if any(getattr(args, o) for o in ("base_commit", "cand_commit", "base_apk", "cand_apk")):
        piped = _pipeline_sides(args, rd)
        if isinstance(piped, int):
            return piped
    try:
        if piped is not None:
            base_sides, cand_sides = [piped[0]], [piped[1]]
            pairing = args.pairing if args.pairing != "auto" else "round"
        elif args.runs:
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

    args._doc = doc
    if piped is not None and args.report is None:
        args.report = str(rd / "reports" / f"{datetime.now():%Y%m%d-%H%M}-compare-{_slug(base.label)}-vs-{_slug(cand.label)}.html")
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
    if piped is not None and rd is not None:
        from perfkit.index.build import rebuild

        st = rebuild(rd)
        print("index: " + " ".join(f"{k}={v}" for k, v in st.items()))
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
        l2 = Path(args.l2) if getattr(args, "l2", None) else None
        if not runs and not det and l2 is None:
            print("error: --runs나 --l0·--l1·--l2 중 하나는 있어야 함")
            return EXIT_REFUSED
        p, doc = B.register(args.name, runs=runs, noise_runs=noise_runs, deterministic=det, l2=l2,
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
    for layer, d in sorted((doc.get("editor") or {}).items()):
        print(f"  {layer}: {d['ref']} runs={d.get('runs')} dirty={_yn(d.get('dirty'))}")
        if d.get("dirty"):
            print(f"warning: {layer} 결과는 작업 트리 변경이 있는 상태에서 잰 것(dirty)")
    return 0


def _yn(v) -> str:
    return "-" if v is None else ("yes" if v else "no")


def _run_baseline_list(args) -> int:
    from perfkit.compare import baseline as B

    for b in B.list_baselines():
        cls = ", ".join(f"{c}(valid={v['n_valid']}{', noise' if v['noise'] else ''})" for c, v in b["classes"].items())
        print(f"{b['name']:24} {b.get('updated_at') or '-':26} {cls} {' '.join(b['deterministic'] + b.get('editor', []))}")
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
    if doc.get("profile_sha"):
        print(f"profile {doc.get('profile_name')} {doc['profile_sha'][:12]}")
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
    for layer, d in sorted((doc.get("editor") or {}).items()):
        print(f"[{layer} / 에디터 Play] {d['ref']} sha256={d['sha256'][:12]} runs={d.get('runs')} "
              f"commit={(d.get('commit') or '-')[:12]} probe={d.get('probe_hash')} scenario={d.get('scenario')} "
              f"dirty={_yn(d.get('dirty'))}")
    return 0


def _run_noise_aa(args) -> int:
    from perfkit import verdict as V
    from perfkit.compare import baseline as B
    from perfkit.compare import runsets as R
    from perfkit.compare import stats as S
    from perfkit.diff import resultio

    rd = _results_dir(args.results)
    if args.device or args.apk:
        from perfkit.compare import pipeline as PL

        if not (args.device and args.apk):
            print("error: A/A를 직접 돌리려면 --device와 --apk 둘 다")
            return EXIT_REFUSED
        name = args.session or f"{datetime.now():%Y%m%d-%H%M}-aa-{args.scenario or 'default'}"
        try:
            PL.run_aa(Path(args.apk), device=args.device, rounds=args.rounds, scenario=args.scenario,
                      seconds=args.seconds, session=name, extra=args.run_arg or [])
        except PL.PipelineError as exc:
            print(f"error: {exc}")
            return EXIT_REFUSED
        args.runs = f"session:{name}"
        if not args.out and rd is not None:
            args.out = str(rd / "noise" / f"{name}.noise.json")
    if not args.runs:
        print("error: --runs(잰 회차) 또는 --device·--apk(직접 실행)가 필요")
        return EXIT_REFUSED
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
    if args.baseline:
        try:
            p, _ = B.register(args.baseline, runs=runs, noise_runs=runs, gate_of=_gate_of(_cfg()), force=True,
                              results_dir=rd)
        except (ValueError, R.RunsetError) as exc:
            print(f"error: 기준선 등록 실패: {exc}")
            return EXIT_REFUSED
        print(f"baseline: {p}")
    return 0


def register_cli(sub) -> None:
    c = sub.add_parser("compare", help="기준 대비 비교(커밋·APK → 빌드·L1·교차 실행, L0·L1 diff, 회차 묶음 통계 판정)")
    _compare_args(c)
    c.set_defaults(func=_run_compare)
    bs = sub.add_parser("bisect-step", help="git bisect run용 한 단계: 악화 1·같음 0·판단 보류 125(오류 128)")
    _compare_args(bs)
    bs.add_argument("--cand-head", metavar="저장소", help="후보 = 그 저장소의 지금 HEAD 커밋(빌드해서 비교)")
    bs.set_defaults(func=_run_bisect)

    b = sub.add_parser("baseline", help="기준선 등록·목록·보기(등급별)")
    s2 = b.add_subparsers(dest="baseline_cmd", metavar="<sub>")
    r = s2.add_parser("register", help="기준선 등록(같은 이름에 다른 등급을 더함)")
    r.add_argument("--name", required=True)
    r.add_argument("--runs", metavar="SPEC", help="기준 회차 묶음")
    r.add_argument("--noise-runs", metavar="SPEC", help="같은 빌드 A/A 회차(잡음 바닥)")
    r.add_argument("--l0", help="L0 결과 파일")
    r.add_argument("--l1", help="L1 결과 파일")
    r.add_argument("--l2", help="L2 결과 파일(editor play 묶음, 같은 날 추세용. 비교는 diff로)")
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
    aa.add_argument("--runs", metavar="SPEC", help="이미 잰 A/A 회차")
    aa.add_argument("--results")
    aa.add_argument("--target", type=float, default=5.0, help="필요 회차를 계산할 목표 MDE %%(기본 5)")
    aa.add_argument("--out", help="noise JSON")
    aa.add_argument("--device", help="A/A를 직접 돌릴 기기(--apk와 함께). 없으면 --runs의 회차만 분석")
    aa.add_argument("--apk", help="A/A로 돌릴 APK")
    aa.add_argument("--scenario")
    aa.add_argument("--rounds", type=int, default=5)
    aa.add_argument("--seconds", type=int)
    aa.add_argument("--session", help="A/A 세션 이름(기본 <시각>-aa-<시나리오>)")
    aa.add_argument("--run-arg", action="append", metavar="ARG")
    aa.add_argument("--baseline", metavar="이름", help="잡음 바닥을 이 기준선에 등록(같은 회차를 기준 회차로)")
    aa.set_defaults(func=_run_noise_aa)
    n.set_defaults(func=lambda a: (n.print_help(), 2)[1])


def _compare_args(c) -> None:
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
    c.add_argument("--report", nargs="?", const="", help="HTML 보고도 쓴다(경로를 안 주면 비교 JSON 옆, 커밋·APK 비교는 <결과 폴더>/reports/)")
    c.add_argument("--json", action="store_true", help="비교 결과 JSON을 표준 출력으로")
    c.add_argument("--top", type=int, default=40, help="출력할 지표 줄 수(0이면 모두)")
    c.add_argument("--fail-on", choices=("warn", "fail", "worse"), help="이 판정이 있으면 종료 코드 1(worse=통계 악화)")
    g = c.add_argument_group("커밋·APK 비교(빌드 → L1 → 교차 실행 → 판정 → 보고 → 색인)")
    g.add_argument("--base-commit", help="기준 커밋(프로필 [build] 명령으로 빌드, 캐시가 있으면 재사용)")
    g.add_argument("--cand-commit", help="후보 커밋")
    g.add_argument("--base-apk", help="기준 APK")
    g.add_argument("--cand-apk", help="후보 APK")
    g.add_argument("--device", default="emulator", help="교차 실행 기기(프로필 [devices.<이름>], 기본 emulator)")
    g.add_argument("--scenario", help="시나리오(기본 프로필 첫 시나리오)")
    g.add_argument("--rounds", type=int, default=6, help="교차 라운드 수(기본 6: 쌍 6개부터 「다름」이 가능)")
    g.add_argument("--seconds", type=int, help="측정 구간 초(기본 프로필 [run] seconds)")
    g.add_argument("--session", help="교차 실행 세션 이름(기본 <시각>-<기준>-vs-<후보>-<시나리오>)")
    g.add_argument("--max-reruns", type=int, default=1, help="관문 무효 라운드를 다시 돌리는 횟수")
    g.add_argument("--run-arg", action="append", metavar="ARG", help="perfkit run abab에 그대로 넘길 인자(여러 번)")
    g.add_argument("--sessions", metavar="S1,S2", help="기기 실행 대신 이미 잰 교차 세션을 다시 판정(설정·잡음 등급을 바꾼 뒤)")
    g.add_argument("--no-l1", action="store_true", help="L1 점검·diff를 하지 않음")
    g.add_argument("--no-run", action="store_true", help="기기 실행 없이 빌드·L1 diff까지만")
    g.add_argument("--rebuild", action="store_true", help="캐시를 무시하고 다시 빌드")
    g.add_argument("--dry-run", action="store_true", help="무엇을 할지만 출력(빌드·실행 안 함)")


def argparse_suppress():
    import argparse

    return argparse.SUPPRESS
