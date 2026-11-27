"""옛 측정 자료 가져오기 `perfkit import legacy`.

옛 형식: 한 폴더에
- logcat 파일(`[PerfStats] t=… scene=… fps=… main=… hist=a,b,c,d,e,f …` 1초 줄, 앞에 `MM-DD HH:MM:SS.mmm`)
- `device_samples.csv`(`t, wall(HH:MM:SS), thermal_status, ap_c, skin_c, bat_c, …, battery_level, pss_mb`)

측정 구간 = device_samples의 첫·끝 wall 사이, 그 안의 `scene`이 고른 장면인 1초 줄만 쓴다.
결과는 회차 폴더 하나(`runs/<run_id>/meta.json·result.json` + 원자료 사본)로 쓰고 `compat_mode=true`, 메타 `legacy` 칸.

    perfkit import legacy <폴더> --device <프로필 기기> --build-hash <8자> [--commit] [--version] [--scene <이름>]
"""

from __future__ import annotations


def register_cli(sub) -> None:
    p = sub.add_parser("import", help="옛 측정 자료 가져오기")
    s2 = p.add_subparsers(dest="import_cmd", metavar="<sub>")
    lg = s2.add_parser("legacy", help="[PerfStats] logcat + device_samples.csv 폴더 → 회차 폴더")
    lg.add_argument("folder")
    lg.add_argument("--device", required=True, help="프로필 기기 이름(등급은 그 기기의 kind)")
    lg.add_argument("--build-hash", required=True, help="빌드 해시 앞 8자(run_id에 들어감)")
    lg.add_argument("--commit")
    lg.add_argument("--version", default="unknown")
    lg.add_argument("--scene", help="쓸 장면 이름(기본: 측정 구간에 가장 많이 나온 장면)")
    lg.add_argument("--scenario", default="legacy")
    lg.add_argument("--save", default="device-current")
    lg.add_argument("--logcat", help="logcat 파일 이름(기본: 폴더에서 [PerfStats] 줄이 있는 .txt)")
    lg.add_argument("--year", type=int, help="logcat 날짜의 해(기본: 파일 수정 시각의 해)")
    lg.add_argument("--results", help="결과 폴더(기본 설정 paths.results)")
    lg.add_argument("--session", help="세션 이름(기본 legacy-<run_id>)")
    lg.add_argument("--print", action="store_true", help="쓰지 않고 요약만")
    lg.set_defaults(func=_run)
    p.set_defaults(func=lambda a: (p.print_help(), 2)[1])


def _run(args) -> int:
    from pathlib import Path

    from perfkit.legacy.perfstats import LegacyError, convert, write

    try:
        meta, result, summ, srcs = convert(Path(args.folder), device=args.device, build_hash=args.build_hash,
                                           commit=args.commit, version=args.version, scene=args.scene,
                                           scenario=args.scenario, save=args.save, logcat=args.logcat,
                                           year=args.year, session=args.session)
    except LegacyError as exc:
        print(f"error: {exc}")
        return 2
    print(" ".join(f"{k}={v}" for k, v in summ.items()))
    if args.print:
        return 0
    if args.results:
        rd = Path(args.results)
    else:
        from perfkit.paths import results_dir

        rd = results_dir()
    try:
        d = write(rd, meta, result, srcs)
    except LegacyError as exc:
        print(f"error: {exc}")
        return 2
    print(f"saved: {d}")
    return 0
