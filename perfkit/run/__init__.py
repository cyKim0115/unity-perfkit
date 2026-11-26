"""L3 회차 실행·수집: `perfkit run`.

    perfkit run [once] --device <이름> --apk <APK> --scenario <이름> [--seconds 60] [--save light] [--rounds N]
    perfkit run abab --device <이름> --a <APK> --b <APK> --rounds N [--order-seed 1] ...
    perfkit run cold-start --device <이름> --apk <APK> [--count 10]

회차 폴더는 `<결과>/runs/<run_id>/`, 실행 기록은 `<결과>/sessions/<session>.json`.
종료 코드: 0 끝까지 감(관문 무효 회차가 있어도 0, --fail-on-invalid면 1) · 2 회차 실패(끝 신호 없음 등)·인자 오류 ·
3 기기·설치·세이브 문제 · 4 호스트가 바쁨(배치 빌드 등)·남의 임대 · 5 AVD 설정 다름 · 6 회차 폴더 스키마 오류.
"""

from __future__ import annotations

import perfkit.device  # noqa: F401  device.* 수집기 이름 등록


def register_cli(sub) -> None:
    p = sub.add_parser("run", help="L3 기기 회차 실행·수집(설치·세이브·실행·수집, 반복·ABAB, 콜드 시작)")
    p.add_argument("mode", nargs="?", default="once", choices=["once", "abab", "cold-start"],
                   help="once(기본): 한 APK를 --rounds회, abab: --a·--b 교차, cold-start: 콜드 시작 반복")
    p.add_argument("--device", required=True, help="기기 이름(프로필 [devices.<이름>])")
    p.add_argument("--apk", help="APK(once·cold-start)")
    p.add_argument("--a", help="ABAB 기준 APK")
    p.add_argument("--b", help="ABAB 후보 APK")
    p.add_argument("--scenario", help="시나리오(기본: 프로필 [[scenarios]] 첫 번째)")
    p.add_argument("--seconds", type=int, help="측정 구간 초(기본 프로필 [run] seconds)")
    p.add_argument("--seed", type=int, help="게임 시드(기본 [run] seed)")
    p.add_argument("--save", help="세이브 이름(기본: 프로필 시나리오 save)")
    p.add_argument("--rounds", type=int, default=1)
    p.add_argument("--order-seed", type=int, default=1, help="ABAB 라운드 순서 씨")
    p.add_argument("--kind", default=None, choices=["numeric", "diagnostic", "aa", "smoke"],
                   help="run_kind(기본 numeric, --a와 --b가 같은 파일이면 aa)")
    p.add_argument("--session", help="세션 이름(기본 <시각>-<시나리오>-<종류>)")
    p.add_argument("--package", help="패키지(기본 APK에서 읽음)")
    p.add_argument("--build-info", help="빌드 정보 JSON(commit·sha·version). 기본 프로필 [run] build_info 템플릿")
    p.add_argument("--build-hash", help="run_id·meta build_hash(기본 빌드 정보 sha → APK sha256 앞 8)")
    p.add_argument("--commit")
    p.add_argument("--label")
    p.add_argument("--variant", choices=["release", "development"])
    p.add_argument("--extra-args", default="", help="게임 실행 인자 덧붙이기")
    p.add_argument("--freeze", type=int, choices=[0, 1], help="-perfFreeze(기본 게임 기본)")
    p.add_argument("--quit-after-end", action="store_true", help="게임이 END 뒤 스스로 닫게(meminfo 못 잡음)")
    p.add_argument("--no-install", action="store_true")
    p.add_argument("--no-save", action="store_true", help="세이브를 넣지 않음(-perfSave는 그대로 넘김)")
    p.add_argument("--no-lease", action="store_true", help="에뮬레이터 임대(emu-lab)를 쓰지 않음(이미 떠 있는 기기)")
    p.add_argument("--keep-lease", action="store_true", help="끝나도 임대를 놓지 않음")
    p.add_argument("--headless", action="store_true", help="에뮬레이터 창 없이(임대 시작 때)")
    p.add_argument("--owner", help="임대 주인(기본 emu-lab이 정함)")
    p.add_argument("--serial", help="adb serial 덮기")
    p.add_argument("--avd-mismatch", choices=["refuse", "warn"], help="AVD 설정이 기대와 다를 때(기본 기기 설정)")
    p.add_argument("--gate", action="append", default=[], metavar="KEY=VALUE",
                   help="관문 문턱 덮기(진단·시험): host_busy_during_max=5 등")
    p.add_argument("--count", type=int, help="cold-start 횟수(기본 [run] cold_start_count)")
    p.add_argument("--notes")
    p.add_argument("--fail-on-invalid", action="store_true", help="관문 무효 회차가 있으면 종료 코드 1")
    p.add_argument("--json", action="store_true", help="마지막 줄에 요약 JSON")
    p.set_defaults(func=_cmd)


def _cmd(args) -> int:
    import json
    from pathlib import Path

    from perfkit.config import ConfigError
    from perfkit.device import devcfg as DC
    from perfkit.device.adb import Adb
    from perfkit.run.runner import Env, Refused, RunSpec
    from perfkit.run.series import Lease, cold_start, run_series

    try:
        env = Env()
        dev = DC.device_config(args.device, env.s)
        run_cfg = DC.run_config(env.s)
    except ConfigError as exc:
        print(f"error: {exc}")
        return 2
    if not args.scenario:
        sc = env.s.profile.get("scenarios") or []
        if not sc:
            print("error: --scenario가 없고 프로필 [[scenarios]]도 없음")
            return 2
        args.scenario = sc[0]["name"]
    serial = args.serial or dev.get("serial")
    env.adb = Adb(DC.adb_path(dev, env.s), serial)
    save = args.save
    if not save:
        for sc in env.s.profile.get("scenarios") or []:
            if sc.get("name") == args.scenario:
                save = sc.get("save")
        save = save or "light"
    gate = {}
    for kv in args.gate:
        k, _, v = kv.partition("=")
        try:
            gate[k.strip()] = float(v)
        except ValueError:
            print(f"error: --gate {kv}")
            return 2
    apk = args.apk or args.a
    if args.mode == "abab" and not (args.a and args.b):
        print("error: abab는 --a와 --b가 필요")
        return 2
    if args.mode != "abab" and not apk and not args.no_install:
        print("error: --apk가 필요(설치 없이 돌리려면 --no-install)")
        return 2
    kind = args.kind or ("aa" if args.mode == "abab" and Path(args.a).resolve() == Path(args.b).resolve() else "numeric")
    spec = RunSpec(device=args.device, apk=Path(apk) if apk else None, scenario=args.scenario,
                   seconds=args.seconds or int(run_cfg["seconds"]), seed=args.seed if args.seed is not None else int(run_cfg["seed"]),
                   save=save, session=args.session, kind=kind, install=not args.no_install, inject_save=not args.no_save,
                   extra_args=args.extra_args, package=args.package, build_hash=args.build_hash, commit=args.commit,
                   label=args.label, build_info=args.build_info, variant=args.variant,
                   quit_after_end=True if args.quit_after_end else None,
                   freeze=None if args.freeze is None else bool(args.freeze), notes=args.notes,
                   gate=gate or None, avd_mismatch=args.avd_mismatch)
    try:
        if args.mode == "cold-start":
            with Lease(env, dev, enabled=not args.no_lease, keep=args.keep_lease, headless=args.headless,
                       owner=args.owner):
                out = cold_start(env, spec, args.count or int(run_cfg.get("cold_start_count", 10)))
            print(f"cold-start {out.get('total_ms')} → {out['_path']}")
            return 0
        doc = run_series(spec, env, rounds=max(1, args.rounds), apk_b=Path(args.b) if args.mode == "abab" else None,
                         order_seed=args.order_seed, lease=not args.no_lease, keep_lease=args.keep_lease,
                         headless=args.headless, owner=args.owner)
    except Refused as exc:
        print(f"refused: {exc}")
        return exc.code
    outs = doc.pop("_outcomes")
    for o in outs:
        print(f"{o.run_id}  valid={o.valid}  end={o.end_signal}{'  compat' if o.compat else ''}"
              + (f"  invalid: {'; '.join(o.reasons)}" if o.reasons else "")
              + (f"  SCHEMA: {o.schema_errors[:2]}" if o.schema_errors else ""))
    s = doc["summary"]
    print(f"session {doc['session']}: runs={s['runs']} valid={s['valid']} invalid={s['invalid']}"
          + (f" lease released={doc['lease'].get('released')}" if doc.get("lease") else ""))
    if args.json:
        print(json.dumps({"session": doc["session"], **s, "runs_ids": [o.run_id for o in outs]}, ensure_ascii=False))
    codes = [o.exit_code for o in outs]
    if any(c == 6 for c in codes):
        return 6
    if any(c == 2 for c in codes):
        return 2
    if args.fail_on_invalid and s["invalid"]:
        return 1
    return 0
