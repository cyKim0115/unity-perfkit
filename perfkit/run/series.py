"""여러 회차: 같은 조건 반복(--rounds)과 기준·후보 교차(ABAB), 에뮬레이터 임대 감싸기, 콜드 시작 반복.

- ABAB: 라운드마다 A·B 순서를 `random.Random(order_seed + 라운드)`로 섞는다(씨 고정 → 같은 명령이면 같은 순서).
  회차마다 다시 설치하고 세이브를 다시 넣는다. 한 실행의 회차는 같은 `session`을 가진다.
- 회차 사이: 실기기는 식힘 관문(wait-cool), 에뮬레이터는 회차 안의 호스트 유휴 관문이 매번 잰다.
- 판정은 하지 않는다. `perfkit compare --runs session:<이름> --split arm`이 한다.
- 실행 기록: `<결과>/sessions/<session>.json`(라운드별 순서·회차·관문 결과).
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

from perfkit.device import devcfg as DC
from perfkit.device import emulator as EM
from perfkit.device import prep as PR
from perfkit.device.adb import Adb
from perfkit.device.lock import DeviceLock, LockBusy
from perfkit.run.runner import Env, Outcome, Refused, RunSpec, run_once


def abab_order(rounds: int, seed: int) -> list[list[str]]:
    out = []
    for r in range(1, rounds + 1):
        o = ["A", "B"]
        random.Random(seed * 1000 + r).shuffle(o)
        out.append(o)
    return out


class Lease:
    """에뮬레이터 임대(emu-lab). 기기 설정 lease가 emu-lab이 아니거나 --no-lease면 아무것도 안 한다."""

    def __init__(self, env: Env, dev: dict, *, enabled: bool, keep: bool = False, hours: float = 4.0,
                 headless: bool = False, owner: str | None = None):
        self.env = env
        self.dev = dev
        self.enabled = enabled and dev.get("kind") == "emulator" and dev.get("lease") == "emu-lab"
        self.keep = keep
        self.hours = hours
        self.headless = headless
        self.info: dict = {}
        self.tool = None
        if self.enabled:
            tool = env.emulab or EM.EmuLab(str(env.s.get("tools.emu_lab")), avd=dev.get("avd"),
                                           owner=owner or dev.get("lease_owner"), log=env.log)
            self.tool = tool

    def __enter__(self):
        if not self.enabled:
            return self
        st = self.tool.status()
        why = EM.busy_by_other_lease(st)
        if why:
            raise Refused(why, 4)
        self.info = self.tool.start(hours=self.hours, headless=self.headless)
        self.env.log(f"lease: {self.info.get('action')} avd={self.info.get('avd')} serial={self.info.get('serial')} "
                     f"launched={self.info.get('launched')} until={self.info.get('expiresAt')}")
        if self.env.adb is not None and self.info.get("serial"):
            self.env.adb.serial = self.info["serial"]
        return self

    def renew(self):
        if self.enabled:
            try:
                self.tool.renew(self.hours)
            except Exception as exc:
                self.env.log(f"warn: 임대 연장 실패: {exc}")

    def __exit__(self, *exc):
        if not self.enabled:
            return False
        if self.keep:
            self.env.log("lease: --keep-lease → 놓지 않음")
            return False
        try:
            doc = self.tool.release()
            self.info["released"] = True
            self.env.log(f"lease: released {doc.get('avd') if doc else ''}")
        except Exception as exc:
            self.info["released"] = False
            self.env.log(f"warn: 임대 반납 실패: {exc}")
        return False


def _session_file(env: Env, session: str) -> Path:
    return env.results / "sessions" / f"{session}.json"


def _write_session(env: Env, doc: dict) -> None:
    p = _session_file(env, doc["session"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")


def _between(env: Env, dev: dict, adb: Adb, args_cool: dict | None) -> dict | None:
    if dev.get("kind") != "device":
        return None
    res = PR.wait_cool(adb, dev, **(args_cool or {}), clock=env.clock, sleep=env.sleep, log=env.log)
    return {"ok": res.ok, "waited_s": res.waited_s, "reason": res.reason, "last": res.last}


def run_series(base: RunSpec, env: Env, *, rounds: int = 1, apk_b: Path | None = None, order_seed: int = 1,
               lease: bool = True, keep_lease: bool = False, headless: bool = False, cool: dict | None = None,
               owner: str | None = None, stop_on_refuse: bool = True) -> dict:
    """rounds회 반복. apk_b가 있으면 ABAB(arm A=base.apk, B=apk_b)."""
    dev = DC.device_config(base.device, env.s)
    abab = apk_b is not None
    session = base.session or f"{datetime.now():%Y%m%d-%H%M}-{base.scenario}-{'abab' if abab else base.kind}"
    order = abab_order(rounds, order_seed) if abab else [["A"] for _ in range(rounds)]
    doc = {"schema": "perfkit/session@1", "session": session, "mode": "abab" if abab else "rounds",
           "device": base.device, "device_class": dev["kind"], "scenario": base.scenario, "seconds": base.seconds,
           "save": base.save, "seed": base.seed, "order_seed": order_seed if abab else None,
           "apk_a": str(base.apk) if base.apk else None, "apk_b": str(apk_b) if apk_b else None,
           "kind": base.kind, "started": datetime.now().astimezone().isoformat(timespec="seconds"),
           "rounds": [], "lease": None}
    outcomes: list[Outcome] = []
    lk = DeviceLock(env.adb.serial or dev.get("serial") or base.device, f"perfkit run {session}", env.lock_dir)
    try:
        lk.acquire()
    except LockBusy as exc:
        raise Refused(str(exc), 4)
    try:
        return _series_body(base, env, dev, doc, order, outcomes, lease, keep_lease, headless, owner, cool,
                            stop_on_refuse)
    finally:
        lk.release()


def _series_body(base, env, dev, doc, order, outcomes, lease, keep_lease, headless, owner, cool, stop_on_refuse):
    session = doc["session"]
    abab = doc["mode"] == "abab"
    rounds = len(order)
    apk_b = Path(doc["apk_b"]) if doc["apk_b"] else None
    with Lease(env, dev, enabled=lease, keep=keep_lease, headless=headless, owner=owner) as ls:
        doc["lease"] = {k: ls.info.get(k) for k in ("action", "avd", "serial", "launched", "expiresAt")} if ls.enabled else None
        for r, arms in enumerate(order, start=1):
            rd = {"round": r, "order": arms, "runs": []}
            doc["rounds"].append(rd)
            for arm in arms:
                spec = replace(base, session=session, arm=arm, round=r,
                               apk=(apk_b if arm == "B" else base.apk), run_id=None if (rounds > 1 or abab) else base.run_id)
                cool_res = _between(env, dev, env.adb, cool) if (outcomes or dev.get("kind") == "device") else None
                if cool_res and not cool_res["ok"]:
                    rd["runs"].append({"arm": arm, "refused": f"식힘 관문: {cool_res['reason']}"})
                    _write_session(env, doc)
                    raise Refused(f"식힘 관문 실패: {cool_res['reason']}", 3)
                try:
                    t0 = time.time()
                    out = run_once(spec, env)
                except Refused as exc:
                    rd["runs"].append({"arm": arm, "refused": str(exc), "code": exc.code})
                    _write_session(env, doc)
                    if stop_on_refuse:
                        raise
                    continue
                outcomes.append(out)
                rd["runs"].append({"arm": arm, "run_id": out.run_id, "valid": out.valid, "reasons": out.reasons,
                                   "end_signal": out.end_signal, "compat": out.compat,
                                   "wall_s": round(time.time() - t0, 1), "cool": cool_res,
                                   "schema_errors": out.schema_errors})
                _write_session(env, doc)
                ls.renew()
    if ls.enabled:
        doc["lease"]["released"] = ls.info.get("released", False)
    doc["ended"] = datetime.now().astimezone().isoformat(timespec="seconds")
    doc["summary"] = {"runs": len(outcomes), "valid": sum(1 for o in outcomes if o.valid),
                      "invalid": sum(1 for o in outcomes if not o.valid),
                      "schema_errors": sum(1 for o in outcomes if o.schema_errors)}
    _write_session(env, doc)
    doc["_outcomes"] = outcomes
    return doc


def cold_start(env: Env, base: RunSpec, count: int) -> dict:
    """콜드 시작 반복(BLD-10): 설치 한 번 → (force-stop → am start -S -W) × count. 측정 인자 없이 띄운다."""
    from perfkit.device import parse as P
    from perfkit.run.apkinfo import apk_info

    dev = DC.device_config(base.device, env.s)
    run_cfg = DC.run_config(env.s)
    adb = env.adb
    ai = apk_info(base.apk) if base.apk else {}
    package = DC.package_name(run_cfg, env.s, ai.get("package"), base.package)
    activity = run_cfg["activity"]
    out = {"schema": "perfkit/cold-start@1", "device": base.device, "device_class": dev["kind"], "package": package,
           "apk_sha": ai.get("sha256"), "version": ai.get("version_name"), "count": count, "runs": [],
           "started": datetime.now().astimezone().isoformat(timespec="seconds")}
    lk = DeviceLock(adb.serial or dev.get("serial") or base.device, "perfkit run cold-start", env.lock_dir)
    try:
        lk.acquire()
    except LockBusy as exc:
        raise Refused(str(exc), 4)
    try:
        return _cold_body(env, base, count, adb, run_cfg, package, activity, out)
    finally:
        lk.release()


def _cold_body(env, base, count, adb, run_cfg, package, activity, out):
    from perfkit.device import parse as P

    if base.apk and base.install:
        r = adb.run("install", "-r", str(base.apk), timeout=float(run_cfg["install_timeout_s"]))
        if "Success" not in r.stdout + r.stderr:
            raise Refused("설치 실패: " + (r.stdout + r.stderr)[-300:], 3)
    for i in range(count):
        adb.shell(f"am force-stop {package}", timeout=30)
        env.sleep(2.0)
        res = adb.run("shell", f"am start -S -W -n {package}/{activity}", timeout=180)
        d = P.am_start(res.stdout)
        out["runs"].append({"i": i + 1, **d})
        env.sleep(float(run_cfg.get("cold_start_settle_s", 5.0)))
    adb.shell(f"am force-stop {package}", timeout=30)
    vals = sorted(x["total_ms"] for x in out["runs"] if x.get("total_ms") is not None)
    if vals:
        out["total_ms"] = {"min": vals[0], "median": vals[len(vals) // 2], "max": vals[-1], "n": len(vals)}
    out["ended"] = datetime.now().astimezone().isoformat(timespec="seconds")
    d = env.results / "startup"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{datetime.now():%Y%m%d-%H%M%S}-{base.device}-cold.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    out["_path"] = str(p)
    return out
