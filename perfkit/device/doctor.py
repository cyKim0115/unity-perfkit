"""`perfkit device doctor`: 회차 전에 확인할 것. (수준, 무엇, 내용) 목록을 돌려준다. 수준: ok·warn·fail."""

from __future__ import annotations

import subprocess

from perfkit.device import devcfg as DC
from perfkit.device import emulator as EM
from perfkit.device import host as H
from perfkit.device import parse as P
from perfkit.device.adb import parse_devices, parse_version


def _adb_servers() -> list[str]:
    """Windows에서 떠 있는 adb 프로세스 경로(서로 다른 adb가 섞이면 logcat이 끊긴다)."""
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-Process adb -ErrorAction SilentlyContinue | ForEach-Object { $_.Path }"],
                             capture_output=True, text=True, errors="replace", timeout=30).stdout
    except Exception:
        return []
    return sorted({l.strip() for l in out.splitlines() if l.strip()})


def doctor(s, dev: dict, adb, probe: H.HostProbe | None = None, servers=_adb_servers) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    add = lambda lv, w, d: rows.append((lv, w, d))  # noqa: E731
    run_cfg = DC.run_config(s)

    v = adb.global_run("version")
    if v.returncode != 0:
        add("fail", "adb", f"실행 못 함: {adb.exe} ({v.stderr.strip()[:120]})")
        return rows
    add("ok", "adb", f"{adb.exe} {parse_version(v.stdout)}")
    paths = servers() if callable(servers) else []
    if len(paths) > 1:
        add("warn", "adb 서버", f"서로 다른 adb가 떠 있음 → logcat 끊김 위험: {paths}")
    elif paths and paths[0].lower() != str(adb.exe).lower():
        add("warn", "adb 서버", f"떠 있는 adb가 설정과 다름: {paths[0]}")

    devs = dict(parse_devices(adb.global_run("devices").stdout))
    state = devs.get(adb.serial)
    if dev["kind"] == "emulator":
        if state == "device":
            add("ok", "기기", f"{adb.serial} device")
        else:
            add("warn", "기기", f"{adb.serial} 안 보임(임대 시작 때 perfkit run이 띄움). adb devices={devs}")
        tool = s.get("tools.emu_lab")
        try:
            st = EM.EmuLab(str(tool), avd=dev.get("avd")).status()
            if st is None:
                add("fail", "임대 도구", f"emu-lab status JSON 없음: tools.emu_lab={tool}")
            else:
                why = EM.busy_by_other_lease(st)
                add("warn" if why else "ok", "임대", why or f"avd={st.get('avd')} owner={st.get('owner')} lease={'있음' if st.get('lease') else '없음'}")
        except Exception as exc:
            add("fail", "임대 도구", f"{tool}: {exc}")
        try:
            cfg = EM.read_avd_config(dev.get("avd") or "")
            summ = P.avd_summary(cfg)
            summ["emulator_version"] = EM.emulator_version(s.get("tools.android_sdk")) or "unknown"
            mm = EM.mismatches(summ, dev.get("expect") or {}, skip_missing=True)
            if not dev.get("expect"):
                add("warn", "AVD 기대값", "기기 설정 expect가 없음(회차마다 대조하지 못함)")
            add("fail" if mm else "ok", "AVD 설정", "; ".join(mm) if mm else str(summ))
        except Exception as exc:
            add("fail", "AVD 설정", str(exc))
        probe = probe or H.default_probe()
        w = H.HostWindow(probe, [])
        add("ok" if probe.system_times() else "warn", "호스트 CPU 측정",
            type(probe).__name__ + ("" if probe.system_times() else " (이 OS에서 못 잼 → 관문 확인 못 함)"))
        busy = H.busy_reasons(probe, DC.host_config(s, dev))
        add("warn" if busy else "ok", "호스트 바쁜 작업", "; ".join(busy) if busy else "없음")
        ed = DC.host_config(s, dev).get("editor_images") or []
        n = sum(len(probe.pids(i)) for i in ed)
        add("warn" if n else "ok", "에디터 프로세스", f"{n}개(임포트 워커 포함)" + (" → 수집 중 점유가 문턱을 넘으면 회차 무효" if n else ""))
        del w
    else:
        if state != "device":
            add("fail", "기기", f"serial {adb.serial or '(설정 없음)'} 상태 {state or '없음'} — 기기 대기(연결·USB 디버깅 허용)")
            return rows
        add("ok", "기기", f"{adb.serial} {adb.getprop('ro.product.model')} Android {adb.getprop('ro.build.version.release')}")
        sysfs = dev.get("sysfs") or {}
        for key in ("gpu_clock", "gpu_busy", "gpu_max_clock"):
            pth = sysfs.get(key)
            if not pth:
                add("warn", f"sysfs {key}", "경로 없음(기기 설정 preset·sysfs)")
                continue
            out = adb.shell(f"cat {pth} 2>&1", timeout=20).strip()
            ok = bool(out) and "No such file" not in out and "denied" not in out
            add("ok" if ok else "warn", f"sysfs {key}", f"{pth} → {out[:60]}")
        for pth in sysfs.get("cpu_freq") or []:
            out = adb.shell(f"cat {pth} 2>&1", timeout=20).strip()
            add("ok" if out.isdigit() else "warn", "sysfs cpu_freq", f"{pth} → {out[:40]}")
        from perfkit.device import prep as PR

        try:
            snap = PR.thermal_snapshot(adb, dev)
            add("ok" if snap["status"] is not None else "warn", "열", str(snap))
        except Exception as exc:
            add("warn", "열", str(exc))
    pkg = run_cfg.get("package") or s.get("app.package")
    if pkg and state == "device":
        out = adb.shell(f"pm path {pkg}", timeout=20).strip()
        add("ok" if out.startswith("package:") else "warn", "패키지", f"{pkg} {'설치됨' if out else '없음(설치는 perfkit run이 함)'}")
    sd = DC.resolve_path(run_cfg.get("saves_dir"), s)
    add("ok" if sd and sd.is_dir() else "warn", "세이브 폴더", str(sd) if sd else "프로필 [run] saves_dir 없음")
    gate = DC.gate_config("L3-E" if dev["kind"] == "emulator" else "L3-D", s, dev)
    add("ok", "관문 문턱", str(gate))
    return rows
