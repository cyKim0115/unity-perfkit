"""L3 기기 준비·점검: `perfkit device`.

    perfkit device list                              설정의 기기와 adb에 보이는 기기
    perfkit device doctor --device <이름>             adb·기기·AVD·임대 도구·sysfs·호스트 측정 확인
    perfkit device prep --device <이름> [--package]   실기기 고정(비행기 모드·밝기·화면 켜짐·게임 모드·고정 성능)
    perfkit device restore --device <이름>            prep 전 값으로 되돌리고 같은지 확인
    perfkit device wait-cool --device <이름> [--max-ap 38 --max-skin 35.5 --status 0]
    perfkit device host-gate [--device <이름>] [--seconds 10]   호스트 유휴 관문을 지금 재 봄(에뮬레이터)
    perfkit device avd --device <이름>                AVD 설정을 읽고 기대값(expect)과 대조

수집기(`device.*`)의 계산은 `perfkit.run.metrics`, 회차 실행은 `perfkit run`.
"""

from __future__ import annotations

from perfkit.collectors import collector


def _register_collectors() -> None:
    from perfkit.run import metrics as M

    table = {
        "device.frame_timing": lambda c: M.frame_items(c["log"], c["frames"], c["kind"]),
        "device.gc_alloc": lambda c: {"CPU-02": M.frame_items(c["log"], c["frames"], c["kind"]).get("CPU-02")},
        "device.render_counters": lambda c: M.frame_items(c["log"], c["frames"], c["kind"]),
        "device.unity_memory": lambda c: M.stats_items(c["log"], c["kind"], c.get("samples") or []),
        "device.thermal": lambda c: M.stats_items(c["log"], c["kind"], c.get("samples") or []),
        "device.phase_markers": lambda c: {**M.event_items(c["log"]), **M.load_items(c["log"], c.get("am") or {})},
        "device.startup": lambda c: M.load_items(c["log"], c.get("am") or {}),
        "device.proc_cpu": lambda c: M.sample_items(c["samples"], c.get("names"), c["kind"]),
        "device.proc_mem": lambda c: M.sample_items(c["samples"], c.get("names"), c["kind"]),
        "device.sys_load": lambda c: M.sample_items(c["samples"], c.get("names"), c["kind"]),
        "device.gpu_freq": lambda c: M.sample_items(c["samples"], c.get("names"), c["kind"]),
        "device.meminfo": lambda c: M.meminfo_items(c.get("meminfo")),
        "device.logcat": lambda c: M.log_items(c.get("summary")),
    }
    for name, fn in table.items():
        collector(name)(fn)


_register_collectors()


def register_cli(sub) -> None:
    p = sub.add_parser("device", help="L3 기기 준비·점검(doctor·prep·restore·wait-cool·host-gate·avd)")
    s2 = p.add_subparsers(dest="device_cmd", metavar="<sub>")

    def add(name, help_, func, device_required=True):
        q = s2.add_parser(name, help=help_)
        q.add_argument("--device", required=device_required, help="기기 이름(프로필 [devices.<이름>])")
        q.add_argument("--serial", help="adb serial 덮기")
        q.set_defaults(func=func)
        return q

    s2.add_parser("list", help="설정의 기기와 adb에 보이는 기기").set_defaults(func=_cmd_list)
    add("doctor", "adb·기기·AVD·임대 도구·sysfs·호스트 측정 확인", _cmd_doctor)
    q = add("prep", "실기기 고정(비행기 모드·밝기·화면 켜짐·게임 모드·고정 성능). 소리는 건드리지 않음", _cmd_prep)
    q.add_argument("--package", help="게임 모드를 고정할 패키지(기본 프로필 [run] package)")
    q.add_argument("--allow-emulator", action="store_true", help="에뮬레이터에도 적용(경로 시험용)")
    add("restore", "prep 전 값으로 되돌리고 같은지 확인", _cmd_restore)
    q = add("wait-cool", "열이 문턱 아래로 내려올 때까지 기다림(실기기)", _cmd_wait_cool)
    q.add_argument("--max-ap", type=float)
    q.add_argument("--max-skin", type=float)
    q.add_argument("--status", type=int, dest="max_status")
    q.add_argument("--timeout", type=float)
    q.add_argument("--poll", type=float)
    q = add("host-gate", "호스트 유휴 관문을 지금 재 봄", _cmd_host_gate, device_required=False)
    q.add_argument("--seconds", type=float, help="측정 초(기본 [host] idle_seconds)")
    add("avd", "AVD 설정을 읽고 기대값(expect)과 대조", _cmd_avd)
    p.set_defaults(func=lambda a: (p.print_help(), 2)[1])


def _ctx(args):
    from perfkit.config import settings
    from perfkit.device import devcfg as DC
    from perfkit.device.adb import Adb

    s = settings()
    dev = DC.device_config(args.device, s) if getattr(args, "device", None) else None
    adb = Adb(DC.adb_path(dev, s), (getattr(args, "serial", None) or (dev or {}).get("serial")))
    return s, dev, adb


def _cmd_list(args) -> int:
    from perfkit.config import settings
    from perfkit.device import devcfg as DC
    from perfkit.device.adb import Adb, parse_devices

    s = settings()
    for name in DC.device_names(s):
        try:
            d = DC.device_config(name, s)
            print(f"{name:14} kind={d['kind']:8} preset={d['preset']:16} serial={d.get('serial') or '-'}"
                  + (f" avd={d.get('avd')}" if d.get("avd") else ""))
        except Exception as exc:
            print(f"{name:14} error: {exc}")
    adb = Adb(DC.adb_path(None, s))
    r = adb.global_run("devices")
    print(f"adb {adb.exe}:")
    for serial, state in parse_devices(r.stdout):
        print(f"  {serial} {state}")
    return 0


def _cmd_doctor(args) -> int:
    from perfkit.device.doctor import doctor

    s, dev, adb = _ctx(args)
    rows = doctor(s, dev, adb)
    bad = 0
    for level, what, detail in rows:
        print(f"[{level:4}] {what}: {detail}")
        bad += level == "fail"
    return 1 if bad else 0


def _cmd_prep(args) -> int:
    from perfkit.device import devcfg as DC
    from perfkit.device import prep as PR
    from perfkit.paths import results_dir

    s, dev, adb = _ctx(args)
    if dev["kind"] != "device" and not args.allow_emulator:
        print("prep은 실기기용(에뮬레이터는 호스트 유휴 관문). 경로만 시험하려면 --allow-emulator")
        return 2
    if dev["kind"] != "device":
        dev = DC.deep_merge(DC.load_preset("android-device"), dev)
    pkg = args.package or DC.run_config(s).get("package")
    doc = PR.prep(adb, dev, pkg, PR.state_path(results_dir(), args.device))
    print(f"prep {args.device}: applied={doc['applied']} original={doc['original']}")
    return 0


def _cmd_restore(args) -> int:
    from perfkit.device import prep as PR
    from perfkit.paths import results_dir

    s, dev, adb = _ctx(args)
    f = PR.state_path(results_dir(), args.device)
    if not f.exists():
        print(f"prep 기록이 없음: {f}")
        return 2
    res = PR.restore(adb, f)
    print(f"restore {args.device}: restored={res['restored']} same={res['same']}" + (f" diff={res['diff']}" if res["diff"] else ""))
    return 0 if res["same"] else 1


def _cmd_wait_cool(args) -> int:
    from perfkit.device import prep as PR

    s, dev, adb = _ctx(args)
    if dev["kind"] != "device":
        print("wait-cool은 실기기용(에뮬레이터는 호스트 유휴 관문: perfkit device host-gate)")
        return 2
    res = PR.wait_cool(adb, dev, max_ap=args.max_ap, max_skin=args.max_skin, max_status=args.max_status,
                       timeout_s=args.timeout, poll_s=args.poll)
    print(f"wait-cool ok={res.ok} waited={res.waited_s}s last={res.last}" + (f" reason={res.reason}" if res.reason else ""))
    return 0 if res.ok else 3


def _cmd_host_gate(args) -> int:
    from perfkit.config import settings
    from perfkit.device import devcfg as DC
    from perfkit.device import host as H

    s = settings()
    dev = DC.device_config(args.device, s) if args.device else None
    hc = DC.host_config(s, dev)
    gate = DC.gate_config("L3-E", s, dev)
    probe = H.default_probe()
    busy = H.busy_reasons(probe, hc)
    secs = args.seconds or float(hc.get("idle_seconds", 10))
    ed = hc.get("editor_images") or []
    emu = hc.get("emulator_images") or []
    w = H.measure_idle(probe, ed + emu, secs)
    eds = H.open_editors(probe, hc)
    lim = gate.get("other_editors_max")
    ok = w.busy_pct is not None and w.busy_pct <= float(gate["host_busy_before_max"]) and \
        w.pct(ed) <= float(gate["other_unity_pct_max"]) and not busy and \
        (lim is None or len(eds) <= int(lim))
    print(f"host {secs:.0f}s: busy={w.busy_pct}% (≤{gate['host_busy_before_max']}) editors={w.count(ed)} "
          f"open_editors={len(eds)}" + (f" (≤{lim})" if lim is not None else " (관문 끔)") +
          f" editor_cpu={w.pct(ed)}% (≤{gate['other_unity_pct_max']}) emulator_cpu={w.pct(emu)}% → {'pass' if ok else 'fail'}")
    for b in busy:
        print(f"busy: {b}")
    for pid, cmd in eds:
        print(f"open editor: pid {pid} {cmd[:160]}")
    return 0 if ok else 1


def _cmd_avd(args) -> int:
    from perfkit.device import emulator as EM

    s, dev, adb = _ctx(args)
    if dev["kind"] != "emulator" or not dev.get("avd"):
        print("에뮬레이터 기기가 아님(또는 avd 없음)")
        return 2
    cfg = EM.read_avd_config(dev["avd"])
    from perfkit.device import parse as P

    summ = P.avd_summary(cfg)
    summ["emulator_version"] = EM.emulator_version(s.get("tools.android_sdk")) or "unknown"
    print(f"avd {dev['avd']}: {summ}")
    mm = EM.mismatches(summ, dev.get("expect") or {}, skip_missing=True)
    for m in mm:
        print(f"mismatch: {m}")
    if not dev.get("expect"):
        print("warn: 기대값(expect)이 기기 설정에 없음 → 회차마다 대조하지 못함")
    return 5 if mm else 0
