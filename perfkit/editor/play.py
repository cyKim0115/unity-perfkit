"""에디터 Play 수집 결과(`perfkit/editor-play@1`)를 L2 결과(result 스키마)로 바꾼다.

에디터 쪽 도구는 Play 한 번(시나리오 다리 여러 개)의 원값을 낸다.

- 다리(`legs`): 카메라 시나리오(근경 `near`·최대 줌아웃 `far` 구도)와 팝업 시나리오(`popup`).
- 구도마다: 그리기 수(draw calls·batches·SetPass·삼각형·정점·그림자 드리우는 수, 프레임별 요약),
  그 구도 동안 뜬 프로파일러 캡처의 요약(`profile`, `perfkit/editor-profile@1`: GC·UI 마커는 게임 루프만).
- 원경 뒤: 설정 스냅숏(`static`: 그림자·후처리 볼륨), Frame Debugger 요약(`frameDebugger`).
- 팝업 다리: 열기 이벤트별 최대 프레임(실제 시간), 두 번째 순환 동안의 프로파일 요약.

항목 대응(매니페스트 지표 키):

    CPU-02 idle_b_per_frame·idle_allocs_per_frame = 근경 프로파일 GC.Alloc 바이트·횟수 프레임 중앙값,
           event_b_per_frame = 팝업 순환 프로파일 GC 바이트 프레임 평균
    CPU-09 build_batch_ms·send_will_render_ms = 근경 프로파일 `Canvas.BuildBatch`·`UIEvents.WillRenderCanvases`
           (6.x 이름, 없으면 `Canvas.SendWillRenderCanvases`) 프레임 평균, rebuild_every_frame = BuildBatch가 95% 넘는 프레임에 있음
    CPU-15 ui_open_max_frame_ms = 팝업 열기 이벤트(연 프레임부터 3프레임) 최대 프레임 시간
    GPU-03 near·far = draw calls 중앙값       GPU-04 setpass_*·batches_* = 중앙값
    GPU-07 verts_*·tris_* = 중앙값            GPU-19 shadow_settings·shadow_casters(근경)
    GPU-05 batch_breaks·srp_batcher_incompatible = Frame Debugger(원경)
    GPU-21 effects·post_passes = 켜진 볼륨 효과 목록, Frame Debugger 후처리 셰이더 그리기 수
    GPU-22(심화) outline_draws·outline_setpass, CPU-01(심화) behaviour_ms, CPU-07(심화) animator_ms

결정성: 결과에는 입력에서 나오는 값만 둔다(`collected_at`은 에디터 쪽 `endedAt`).
"""

from __future__ import annotations

SCHEMA = "perfkit/editor-play@1"
PROFILE_SCHEMA = "perfkit/editor-profile@1"
TOOL = {"name": "perfkit.editor.play", "version": "1.0.0"}

COLLECTORS = {
    "CPU-02": "editor.play_counters", "CPU-09": "editor.play_counters", "CPU-15": "editor.play_events",
    "GPU-03": "editor.play_counters", "GPU-04": "editor.play_counters", "GPU-07": "editor.play_counters",
    "GPU-05": "editor.frame_debugger", "GPU-21": "editor.frame_debugger", "GPU-22": "editor.frame_debugger",
    "GPU-19": "editor.render_debug", "CPU-01": "editor.play_profile", "CPU-07": "editor.play_profile",
}

BASIC = ("CPU-02", "CPU-09", "CPU-15", "GPU-03", "GPU-04", "GPU-05", "GPU-07", "GPU-19", "GPU-21")
DEEP = ("GPU-22", "CPU-01", "CPU-07")


class EditorPlayError(ValueError):
    pass


def _median(summary: dict | None):
    if not isinstance(summary, dict):
        return None
    return summary.get("p50")


def _num(v):
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _round1(v):
    return None if v is None else _num(round(v, 1))


def _marker(profile: dict | None, *names: str):
    """프로파일 요약의 마커 프레임 평균(ms). 처음 있는 이름을 쓴다."""
    markers = (profile or {}).get("markers") or {}
    for n in names:
        m = markers.get(n)
        if isinstance(m, dict) and m.get("framesPresentPct", 0) > 0:
            return n, m
    for n in names:
        if n in markers:
            return n, markers[n]
    return None, None


def _profile_ok(window: dict | None) -> dict | None:
    p = (window or {}).get("profile")
    if isinstance(p, dict) and p.get("schema") == PROFILE_SCHEMA and p.get("frames"):
        return p
    return None


def _nc(reason: str, collector: str) -> dict:
    return {"status": "not_collected", "reason": reason, "collector": collector}


def _ok(values: dict, collector: str, detail: dict | None = None, note: str | None = None) -> dict:
    vals = {k: v for k, v in values.items() if v is not None}
    if not vals:
        return _nc("값 없음", collector)
    out = {"status": "ok", "values": vals, "collector": collector}
    if detail:
        out["detail"] = detail
    if note:
        out["note"] = note
    return out


def _check(play: dict) -> None:
    if play.get("schema") != SCHEMA:
        raise EditorPlayError(f"에디터 Play 결과 스키마가 아님: {play.get('schema')!r} (기대 {SCHEMA})")
    if not isinstance(play.get("legs"), dict) or not play["legs"]:
        raise EditorPlayError("에디터 Play 결과에 legs가 없음")


def camera_leg(play: dict) -> tuple[str | None, dict | None]:
    for name, leg in play["legs"].items():
        if name != "popup" and isinstance(leg, dict) and "near" in leg:
            return name, leg
    return None, None


def items_from_play(play: dict) -> dict:
    """에디터 Play 원값 → 항목별 결과(L2)."""
    _check(play)
    legname, cam = camera_leg(play)
    popup = (play["legs"].get("popup") or {}).get("popups")
    near = (cam or {}).get("near")
    far = (cam or {}).get("far")
    pn, pf = _profile_ok(near), _profile_ok(far)
    fd = (cam or {}).get("frameDebugger") or {}
    static = (cam or {}).get("static") or {}
    items: dict[str, dict] = {}

    # CPU-02 GC
    c = COLLECTORS["CPU-02"]
    pp = _profile_ok(popup)
    if pn:
        items["CPU-02"] = _ok({
            "idle_b_per_frame": _num(_median(pn.get("gcAllocBytesPerFrame"))),
            "idle_allocs_per_frame": _num(_median(pn.get("gcAllocCallsPerFrame"))),
            "event_b_per_frame": _round1(((pp or {}).get("gcAllocBytesPerFrame") or {}).get("mean")),
        }, c, detail={
            "near_mean_b": (pn.get("gcAllocBytesPerFrame") or {}).get("mean"),
            "far_p50_b": _median((pf or {}).get("gcAllocBytesPerFrame")),
            "popup_frames": (pp or {}).get("frames"),
            "source": "프로파일러 캡처(게임 루프만). 에디터 카운터 GC Allocated In Frame은 에디터 루프 할당이 섞여 쓰지 않음",
        })
    else:
        items["CPU-02"] = _nc("근경 프로파일 캡처 없음", c)

    # CPU-09 UI
    c = COLLECTORS["CPU-09"]
    if pn:
        bn, bb = _marker(pn, "Canvas.BuildBatch")
        sn, sw = _marker(pn, "Canvas.SendWillRenderCanvases", "UIEvents.WillRenderCanvases")
        items["CPU-09"] = _ok({
            "build_batch_ms": (bb or {}).get("msPerFrame"),
            "send_will_render_ms": (sw or {}).get("msPerFrame"),
            "rebuild_every_frame": bool(bb and bb.get("framesPresentPct", 0) >= 95) if bb else None,
        }, c, detail={"build_batch_marker": bn, "send_will_render_marker": sn,
                      "build_batch_frames_pct": (bb or {}).get("framesPresentPct"),
                      "player_update_canvases_ms": ((pn.get("markers") or {}).get("PostLateUpdate.PlayerUpdateCanvases") or {}).get("msPerFrame")})
    else:
        items["CPU-09"] = _nc("근경 프로파일 캡처 없음", c)

    # CPU-15 팝업
    c = COLLECTORS["CPU-15"]
    if popup and popup.get("events"):
        ev = popup["events"]
        items["CPU-15"] = _ok({"ui_open_max_frame_ms": popup.get("uiOpenMaxFrameMs")}, c, detail={
            "first_open_max_frame_ms": popup.get("firstOpenMaxFrameMs"),
            "reopen_max_frame_ms": popup.get("reopenMaxFrameMs"),
            "events": [{"name": e.get("name"), "first": e.get("first"), "max_frame_ms": e.get("maxFrameMs"),
                        "call_ms": e.get("callMs")} for e in ev],
        }, note="열기 이벤트의 연 프레임부터 3프레임 중 최대(실제 시간). 처음 열기(풀 생성)가 최대를 정함")
    else:
        items["CPU-15"] = _nc("팝업 다리 없음(popups=false로 돌렸거나 실패)", c)

    # 그리기 수
    if near and far:
        items["GPU-03"] = _ok({"near": _num(_median(near.get("drawCalls"))), "far": _num(_median(far.get("drawCalls")))},
                              COLLECTORS["GPU-03"], note="UnityEditor.UnityStats(Game 뷰)")
        items["GPU-04"] = _ok({
            "setpass_near": _num(_median(near.get("setPassCalls"))), "setpass_far": _num(_median(far.get("setPassCalls"))),
            "batches_near": _num(_median(near.get("batches"))), "batches_far": _num(_median(far.get("batches"))),
        }, COLLECTORS["GPU-04"], note="batches = draw calls − 배칭으로 줄인 수(UnityStats에 Batches 없음)")
        items["GPU-07"] = _ok({
            "verts_near": _num(_median(near.get("vertices"))), "verts_far": _num(_median(far.get("vertices"))),
            "tris_near": _num(_median(near.get("triangles"))), "tris_far": _num(_median(far.get("triangles"))),
        }, COLLECTORS["GPU-07"], detail={"actors_near": (near.get("renderers") or {}).get("actors"),
                                          "actors_far": (far.get("renderers") or {}).get("actors")})
    else:
        for iid in ("GPU-03", "GPU-04", "GPU-07"):
            items[iid] = _nc("카메라 시나리오 다리 없음", COLLECTORS[iid])

    # GPU-19 그림자
    c = COLLECTORS["GPU-19"]
    if static.get("shadow") and near:
        items["GPU-19"] = _ok({"shadow_settings": static["shadow"],
                               "shadow_casters": _num(_median(near.get("shadowCasters")))}, c,
                              detail={"shadow_casters_far": _num(_median((far or {}).get("shadowCasters"))),
                                      "shadow_lights": static.get("shadowLights")})
    else:
        items["GPU-19"] = _nc("설정 스냅숏 없음", c)

    # Frame Debugger
    fd_ok = bool(fd.get("ok"))
    why = fd.get("reason") or "Frame Debugger를 돌리지 않음"
    c = COLLECTORS["GPU-05"]
    if fd_ok:
        causes = fd.get("batchBreakCauses") or {}
        incompatible = sum(v for k, v in causes.items() if "not compatible with SRP batcher" in k)
        items["GPU-05"] = _ok({"batch_breaks": fd.get("batchBreaks"), "srp_batcher_incompatible": incompatible}, c,
                              detail={"causes": causes, "events": fd.get("events"), "event_types": fd.get("eventTypes")},
                              note="원경 한 프레임. srp_batcher_incompatible는 그 프레임의 「SRP 배처 비호환 노드」 끊김 수(L0는 셰이더 수)")
    else:
        items["GPU-05"] = _nc("Frame Debugger 실패: " + why, c)
    c = COLLECTORS["GPU-21"]
    effects = static.get("effects")
    if effects is not None or fd_ok:
        items["GPU-21"] = _ok({"effects": effects, "post_passes": fd.get("postDraws") if fd_ok else None}, c,
                              detail={"post_shaders": fd.get("postShaders")} if fd_ok else None)
    else:
        items["GPU-21"] = _nc("설정 스냅숏·Frame Debugger 없음", c)
    c = COLLECTORS["GPU-22"]
    if fd_ok:
        items["GPU-22"] = _ok({"outline_draws": fd.get("outlineDrawCalls", fd.get("outlineDraws")),
                               "outline_setpass": fd.get("outlineStateChanges")}, c,
                              detail={"outline_events": fd.get("outlineDraws"), "outline_shaders": fd.get("outlineShaders")},
                              note="outline_setpass는 셰이더·패스·키워드 조합이 앞 그리기와 달라지는 수(SetPass 근사)")
    else:
        items["GPU-22"] = _nc("Frame Debugger 실패: " + why, c)

    # 심화: 스크립트·애니메이터
    if pn:
        mk = pn.get("markers") or {}
        def ms(*names):
            vals = [(mk.get(n) or {}).get("msPerFrame") for n in names]
            vals = [v for v in vals if v is not None]
            return round(sum(vals), 4) if vals else None
        items["CPU-01"] = _ok({"behaviour_ms": ms("BehaviourUpdate", "LateBehaviourUpdate")}, COLLECTORS["CPU-01"])
        items["CPU-07"] = _ok({"animator_ms": ms("PreLateUpdate.DirectorUpdateAnimationBegin",
                                                 "PreLateUpdate.DirectorUpdateAnimationEnd", "Animators.WriteTransforms")},
                              COLLECTORS["CPU-07"])
    else:
        items["CPU-01"] = _nc("근경 프로파일 캡처 없음", COLLECTORS["CPU-01"])
        items["CPU-07"] = _nc("근경 프로파일 캡처 없음", COLLECTORS["CPU-07"])
    return items


def to_result(play: dict, *, ref: str) -> dict:
    """result 스키마(L2) 한 개."""
    from perfkit.diff import resultio

    items = items_from_play(play)
    legname, cam = camera_leg(play)
    src = {"kind": "editor", "ref": ref, "scenario": play.get("scenario"), "save": play.get("save"),
           "seed": play.get("seed"), "frames": play.get("frames"), "warmup": play.get("warmup"),
           "capture_fps": play.get("captureFps"), "freeze": play.get("freeze"), "unity": play.get("unity")}
    commit = play.get("commit")
    if commit and all(ch in "0123456789abcdef" for ch in commit.lower()) and 7 <= len(commit) <= 40:
        src["commit"] = commit.lower()
    if isinstance(play.get("dirty"), bool):
        src["dirty"] = play["dirty"]
    begin = ((cam or {}).get("begin") or {})
    for k, out_k in (("probeHash", "probe_hash"), ("saveHash", "save_hash"), ("probeVersion", "probe_version")):
        v = (cam or {}).get(k) or begin.get(k)
        if v:
            src[out_k] = v
    stat = (cam or {}).get("static") or {}
    if stat:
        src["quality"] = {k: stat.get(k) for k in ("quality", "graphics", "vSync", "targetFps", "screen")}
    result = {
        "schema": "perfkit/result@1",
        "layer": "L2",
        "collected_at": play.get("endedAt") or play.get("startedAt"),
        "source": {k: v for k, v in src.items() if v is not None},
        "tool": dict(TOOL),
        "items": items,
    }
    resultio.manifest_stamp(result)
    return result


def tree_state(tree, *, commit: str | None = None, ignore: list[str] | None = None) -> dict:
    """측정한 작업 트리의 지금 상태: HEAD와 추적 파일 변경(새 파일은 보지 않음).

    `dirty`는 `ignore`(fnmatch 틀)에 걸리지 않는 변경이 있거나 HEAD가 결과의 커밋과 다를 때 참이다.
    변환 시점에 읽으므로 측정 직후(작업 트리를 바꾸기 전)에 부른다."""
    import fnmatch
    import subprocess

    def git(*a):
        r = subprocess.run(["git", "-C", str(tree), *a], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
        if r.returncode != 0:
            raise EditorPlayError(f"git {' '.join(a)} 실패({tree}): {r.stderr.strip()[:200]}")
        return r.stdout

    head = git("rev-parse", "HEAD").strip().lower()
    changed = []
    for line in git("status", "--porcelain", "--untracked-files=no").splitlines():
        path = line[3:].strip().strip('"')
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path:
            changed.append(path)
    pats = list(ignore or [])
    kept = [c for c in changed if not any(fnmatch.fnmatch(c, pt) for pt in pats)]
    mismatch = bool(commit) and not head.startswith(commit.lower()) and not commit.lower().startswith(head)
    out = {"head": head, "dirty": bool(kept) or mismatch, "changed": len(changed), "ignored": len(changed) - len(kept),
           "paths": kept[:50]}
    if pats:
        out["ignore"] = pats
    if mismatch:
        out["head_mismatch"] = True
    return out


def summary_lines(result: dict) -> list[str]:
    it = result["items"]
    ok = [k for k, v in it.items() if v["status"] == "ok"]
    nc = {k: v.get("reason") for k, v in it.items() if v["status"] != "ok"}
    basic_ok = [k for k in BASIC if k in ok]
    lines = [f"layer=L2 items={len(it)} ok={len(ok)} basic={len(basic_ok)}/{len(BASIC)} "
             f"commit={(result['source'].get('commit') or '-')[:12]} probe={result['source'].get('probe_hash', '-')}"
             + (f" dirty={'yes' if result['source']['dirty'] else 'no'}" if 'dirty' in result['source'] else "")]
    for k in BASIC + DEEP:
        v = it.get(k)
        if not v:
            continue
        if v["status"] == "ok":
            vals = ", ".join(f"{mk}={mv}" for mk, mv in v["values"].items()
                             if not isinstance(mv, dict) and not (isinstance(mv, list) and mv and isinstance(mv[0], str)))
            lines.append(f"  {k} {vals}"[:200])
        else:
            lines.append(f"  {k} 수집 못 함: {nc[k]}"[:200])
    return lines


def merge_runs(results: list[dict], refs: list[str]) -> dict:
    """같은 조건의 L2 결과 여러 개를 하나로: 숫자 지표는 회차별 값 목록(판정은 중앙값·잡음 등급 규칙),
    설정 묶음(dict·list)은 모두 같을 때만 값, 다르면 첫 회차 값 + `runs_differ`."""
    if not results:
        raise EditorPlayError("결과가 없음")
    if len(results) == 1:
        return results[0]
    keys = ("scenario", "save", "seed", "frames", "warmup", "capture_fps", "freeze", "probe_hash")
    first = results[0]
    for r, ref in zip(results[1:], refs[1:]):
        diff = [k for k in keys if r["source"].get(k) != first["source"].get(k)]
        if diff:
            raise EditorPlayError(f"조건이 다른 회차를 묶을 수 없음({ref}: {', '.join(diff)})")
    out = {k: v for k, v in first.items() if k != "items"}
    out["source"] = dict(first["source"])
    out["source"]["ref"] = refs[0]
    out["source"]["refs"] = list(refs)
    out["source"]["runs"] = len(results)
    commits = sorted({r["source"].get("commit") for r in results if r["source"].get("commit")})
    if len(commits) > 1:
        raise EditorPlayError("커밋이 다른 회차를 묶을 수 없음: " + ", ".join(commits))
    dirt = [r["source"]["dirty"] for r in results if isinstance(r["source"].get("dirty"), bool)]
    if dirt:
        out["source"]["dirty"] = any(dirt)
    out["collected_at"] = max(r["collected_at"] for r in results if r.get("collected_at"))
    items = {}
    for iid in first["items"]:
        entries = [r["items"].get(iid) for r in results]
        oks = [e for e in entries if e and e["status"] == "ok"]
        if len(oks) != len(entries):
            bad = next(e for e in entries if not e or e["status"] != "ok")
            items[iid] = {"status": "not_collected", "collector": first["items"][iid].get("collector"),
                          "reason": "일부 회차 수집 못 함: " + ((bad or {}).get("reason") or "없음")}
            continue
        vals, differ = {}, []
        for k in oks[0]["values"]:
            series = [e["values"].get(k) for e in oks]
            if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in series):
                vals[k] = series
            else:
                vals[k] = series[0]
                if any(v != series[0] for v in series[1:]):
                    differ.append(k)
        entry = {"status": "ok", "values": vals, "collector": oks[0].get("collector"), "samples": len(oks),
                 "runs": [e["values"] for e in oks]}
        if differ:
            entry["runs_differ"] = differ
        for extra in ("note",):
            if oks[0].get(extra):
                entry[extra] = oks[0][extra]
        items[iid] = entry
    out["items"] = items
    return out

