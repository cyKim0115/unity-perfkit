import copy
import json
import tempfile
import unittest
from pathlib import Path

from perfkit.diff import resultio
from perfkit.diff.compare import diff_results
from perfkit.editor.memory import summarize as mem_summarize
from perfkit.editor.play import BASIC, EditorPlayError, items_from_play, merge_runs, to_result
from perfkit.editor.profile import ProfileSummaryError, bottleneck, summary_lines as profile_lines
from perfkit.schema import validate
from tests.fakeproj import isolated_settings
from tests.test_commands import run as cli_run


def stat(v, n=240):
    return {"n": n, "mean": v, "p50": v, "p95": v, "min": v, "max": v}


def profile(gc_p50=128.0, gc_mean=500.0, build=0.009, will=0.05):
    return {
        "schema": "perfkit/editor-profile@1", "source": "x.raw", "frames": 241, "firstFrame": 0,
        "frameMs": {"n": 241, "mean": 16.6, "p50": 16.7, "p95": 19.0, "p99": 25.0, "min": 2.0, "max": 30.0},
        "frameMsHistogram": {"le16_7": 100, "le33_3": 141, "le50": 0, "gt50": 0},
        "gcAllocBytesPerFrame": {"n": 241, "mean": gc_mean, "p50": gc_p50},
        "gcAllocCallsPerFrame": {"n": 241, "mean": 8.0, "p50": 5.0},
        "topSelf": [{"marker": "EditorLoop", "selfMsPerFrame": 13.0, "selfPct": 78.0, "totalMsPerFrame": 13.0,
                     "callsPerFrame": 5.0, "framesPresentPct": 99.0},
                    {"marker": "PlayerLoop", "selfMsPerFrame": 0.5, "selfPct": 3.0, "totalMsPerFrame": 3.0,
                     "callsPerFrame": 1.0, "framesPresentPct": 100.0}],
        "markers": {
            "Canvas.BuildBatch": {"msPerFrame": build, "framesPresentPct": 99.6},
            "Canvas.SendWillRenderCanvases": {"msPerFrame": 0, "framesPresentPct": 0},
            "UIEvents.WillRenderCanvases": {"msPerFrame": will, "framesPresentPct": 99.6},
            "BehaviourUpdate": {"msPerFrame": 0.14, "framesPresentPct": 100},
            "LateBehaviourUpdate": {"msPerFrame": 0.12, "framesPresentPct": 100},
            "PreLateUpdate.DirectorUpdateAnimationBegin": {"msPerFrame": 0.06, "framesPresentPct": 100},
            "PreLateUpdate.DirectorUpdateAnimationEnd": {"msPerFrame": 0.07, "framesPresentPct": 100},
        },
        "bottleneckPct": {"Gfx.WaitForPresentOnGfxThread": 0.0, "Gfx.WaitForCommands": 0.0,
                          "Gfx.WaitForGfxCommandsFromMainThread": 0.0, "WaitForTargetFPS": 12.5},
    }


def window(tris, setpass=60, dc=400):
    return {"frames": 240, "triangles": stat(tris), "vertices": stat(tris * 0.8), "drawCalls": stat(dc),
            "batches": stat(dc - 3), "setPassCalls": stat(setpass), "shadowCasters": stat(200),
            "renderers": {"actors": ["t:a pos=(1,0,1)"]}, "profile": profile()}


def play(tris_near=100000, commit="0123456789abcdef0123456789abcdef01234567", fd_ok=True):
    return {
        "schema": "perfkit/editor-play@1", "commit": commit, "unity": "6000.0.0f1", "scenario": "scene",
        "frames": 240, "warmup": 60, "seconds": 24, "seed": 1, "save": "small", "freeze": True, "captureFps": 60,
        "startedAt": "2026-11-27T03:49:02Z", "endedAt": "2026-11-27T03:50:46Z",
        "legs": {
            "scene": {
                "probeHash": "probe-1-0000abcd", "saveHash": "aaaaaaaaaaaa", "probeVersion": "probe-1",
                "near": window(tris_near), "far": window(300000, 80, 900),
                "static": {"shadow": {"shadowDistance": 100.0, "cascadeCount": 1}, "shadowLights": ["Directional"],
                           "effects": ["global:Bloom", "global:Tonemapping"], "quality": "Mobile", "graphics": "High",
                           "vSync": 0, "targetFps": 60, "screen": "1080x1920"},
                "frameDebugger": ({"ok": True, "events": 90, "batchBreaks": 90,
                                   "batchBreakCauses": {"SRP: Node is not compatible with SRP batcher": 6, "Unknown reason.": 40},
                                   "postDraws": 20, "postShaders": {"Hidden/Universal Render Pipeline/Bloom": 12},
                                   "outlineDraws": 4, "outlineDrawCalls": 30, "outlineStateChanges": 3}
                                  if fd_ok else {"ok": False, "reason": "frame_debugger_types_not_found"}),
            },
            "popup": {"popups": {"events": [{"name": "PopupA", "first": True, "maxFrameMs": 120.0, "callMs": 100.0},
                                            {"name": "PopupA", "first": False, "maxFrameMs": 20.3, "callMs": 3.7}],
                                 "uiOpenMaxFrameMs": 120.0, "firstOpenMaxFrameMs": 120.0, "reopenMaxFrameMs": 20.3,
                                 "profile": profile(gc_mean=1000.0)}},
        },
    }


class EditorPlayTest(unittest.TestCase):
    def test_items_fill_basic_set(self):
        items = items_from_play(play())
        for iid in BASIC:
            self.assertEqual(items[iid]["status"], "ok", iid)
        self.assertEqual(items["CPU-02"]["values"], {"idle_b_per_frame": 128, "idle_allocs_per_frame": 5,
                                                     "event_b_per_frame": 1000.0})
        self.assertEqual(items["CPU-09"]["values"]["send_will_render_ms"], 0.05)   # 6.x 이름으로 대체
        self.assertTrue(items["CPU-09"]["values"]["rebuild_every_frame"])
        self.assertEqual(items["CPU-15"]["values"], {"ui_open_max_frame_ms": 120.0})
        self.assertEqual(items["GPU-03"]["values"], {"near": 400, "far": 900})
        self.assertEqual(items["GPU-04"]["values"]["batches_far"], 897)
        self.assertEqual(items["GPU-05"]["values"], {"batch_breaks": 90, "srp_batcher_incompatible": 6})
        self.assertEqual(items["GPU-07"]["values"]["tris_near"], 100000)
        self.assertEqual(items["GPU-21"]["values"]["post_passes"], 20)
        self.assertEqual(items["GPU-22"]["values"], {"outline_draws": 30, "outline_setpass": 3})
        self.assertAlmostEqual(items["CPU-01"]["values"]["behaviour_ms"], 0.26)

    def test_frame_debugger_failure_is_not_collected(self):
        items = items_from_play(play(fd_ok=False))
        self.assertEqual(items["GPU-05"]["status"], "not_collected")
        self.assertIn("frame_debugger_types_not_found", items["GPU-05"]["reason"])
        self.assertEqual(items["GPU-21"]["status"], "ok")             # 효과 목록은 남음
        self.assertNotIn("post_passes", items["GPU-21"]["values"])

    def test_missing_profile_and_popup(self):
        p = play()
        del p["legs"]["scene"]["near"]["profile"]
        del p["legs"]["popup"]
        items = items_from_play(p)
        self.assertEqual(items["CPU-02"]["status"], "not_collected")
        self.assertEqual(items["CPU-15"]["status"], "not_collected")
        self.assertEqual(items["GPU-03"]["status"], "ok")

    def test_wrong_schema(self):
        with self.assertRaises(EditorPlayError):
            items_from_play({"schema": "x", "legs": {}})

    def test_result_schema_and_diff(self):
        with isolated_settings():
            a = to_result(play(), ref="a.json")
            b = to_result(play(tris_near=100000 * 1.08), ref="b.json")
        self.assertEqual(validate(a, "result"), [])
        self.assertEqual(a["layer"], "L2")
        self.assertEqual(a["source"]["probe_hash"], "probe-1-0000abcd")
        same = diff_results(a, copy.deepcopy(a))
        self.assertEqual(same["summary"]["changes"], 0)
        self.assertEqual(validate(same, "comparison"), [])
        cmp = diff_results(a, b)
        self.assertEqual(cmp["items"]["GPU-07"]["metrics"]["tris_near"]["outcome"], "warn")   # 낮은 잡음 +8%
        self.assertEqual(cmp["items"]["GPU-03"]["outcome"], "pass")

    def test_merge_runs_lists_numbers(self):
        with isolated_settings():
            rs = [to_result(play(tris_near=t), ref=f"r{i}.json") for i, t in enumerate((100, 101, 102))]
        m = merge_runs(rs, ["r0.json", "r1.json", "r2.json"])
        self.assertEqual(m["items"]["GPU-07"]["values"]["tris_near"], [100, 101, 102])
        self.assertEqual(m["items"]["GPU-21"]["values"]["effects"], ["global:Bloom", "global:Tonemapping"])
        self.assertEqual(m["source"]["runs"], 3)
        self.assertEqual(validate(m, "result"), [])
        other = copy.deepcopy(rs[1])
        other["source"]["seed"] = 2
        with self.assertRaises(EditorPlayError):
            merge_runs([rs[0], other], ["a", "b"])

    def test_cli_play_and_diff(self):
        with isolated_settings(), tempfile.TemporaryDirectory() as td:
            pa, pb = Path(td) / "a.json", Path(td) / "b.json"
            pa.write_text(json.dumps(play()), encoding="utf-8")
            pb.write_text(json.dumps(play()), encoding="utf-8")
            oa, ob = Path(td) / "a.l2.json", Path(td) / "b.l2.json"
            rc, out = cli_run(["editor", "play", str(pa), "--out", str(oa)])
            self.assertEqual(rc, 0, out)
            self.assertIn("basic=9/9", out)
            rc, _ = cli_run(["editor", "play", str(pb), "--out", str(ob)])
            self.assertEqual(rc, 0)
            rc, out = cli_run(["diff", str(oa), str(ob), "--fail-on", "warn"])
            self.assertEqual(rc, 0, out)
            self.assertIn("layer=L2 changes=0", out)
            rc, out = cli_run(["editor", "play", str(pa), str(pb), "--out", str(Path(td) / "ab.l2.json")])
            self.assertEqual(rc, 0, out)
            self.assertEqual(resultio.read(Path(td) / "ab.l2.json")["source"]["runs"], 2)


class EditorPlayDirtyTest(unittest.TestCase):
    def _repo(self, td):
        import subprocess

        def git(*a):
            subprocess.run(["git", "-C", str(td), *a], check=True, capture_output=True)
        git("init", "-q")
        git("config", "user.email", "t@example.invalid")
        git("config", "user.name", "t")
        (Path(td) / "a.txt").write_text("1", encoding="utf-8")
        (Path(td) / "noise.meta").write_text("1", encoding="utf-8")
        git("add", "a.txt", "noise.meta")
        git("commit", "-q", "-m", "init")
        return subprocess.run(["git", "-C", str(td), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

    def test_tree_state(self):
        from perfkit.editor.play import tree_state

        with tempfile.TemporaryDirectory() as td:
            head = self._repo(td)
            self.assertFalse(tree_state(td, commit=head)["dirty"])
            (Path(td) / "new.txt").write_text("x", encoding="utf-8")          # 새 파일은 안 봄
            self.assertFalse(tree_state(td, commit=head)["dirty"])
            (Path(td) / "noise.meta").write_text("2", encoding="utf-8")
            st = tree_state(td, commit=head)
            self.assertTrue(st["dirty"])
            self.assertEqual(st["paths"], ["noise.meta"])
            st = tree_state(td, commit=head, ignore=["*.meta"])
            self.assertFalse(st["dirty"])
            self.assertEqual((st["changed"], st["ignored"]), (1, 1))
            st = tree_state(td, commit="0" * 40, ignore=["*.meta"])           # HEAD가 결과 커밋과 다름
            self.assertTrue(st["dirty"])
            self.assertTrue(st["head_mismatch"])

    def test_cli_tree_and_merge(self):
        with isolated_settings(), tempfile.TemporaryDirectory() as td:
            head = self._repo(td)
            pa = Path(td) / "a.json"
            pa.write_text(json.dumps(play(commit=head)), encoding="utf-8")
            out = Path(td) / "a.l2.json"
            rc, text = cli_run(["editor", "play", str(pa), "--out", str(out), "--tree", td])
            self.assertEqual(rc, 0, text)
            self.assertIn("dirty=no", text)
            src = resultio.read(out)["source"]
            self.assertEqual((src["dirty"], src["tree"]["head"]), (False, head))
            (Path(td) / "a.txt").write_text("2", encoding="utf-8")
            rc, text = cli_run(["editor", "play", str(pa), "--out", str(out), "--tree", td])
            self.assertIn("dirty=yes", text)
            self.assertEqual(resultio.read(out)["source"]["tree"]["paths"], ["a.txt"])
        rs = [to_result(dict(play(), dirty=d), ref=f"r{i}.json") for i, d in enumerate((False, True))]
        self.assertTrue(merge_runs(rs, ["r0.json", "r1.json"])["source"]["dirty"])
        self.assertNotIn("dirty", to_result(play(), ref="r.json")["source"])


class EditorProfileTest(unittest.TestCase):
    def test_summary_and_bottleneck(self):
        p = profile()
        self.assertEqual(bottleneck(p)["dominant"], "WaitForTargetFPS")
        lines = profile_lines(p, top=20)
        self.assertTrue(any("EditorLoop" in ln for ln in lines))
        self.assertTrue(any("FRM-04" in ln for ln in lines))
        with self.assertRaises(ProfileSummaryError):
            profile_lines({"schema": "x"})

    def test_cli_profile(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "p.json"
            path.write_text(json.dumps(profile()), encoding="utf-8")
            rc, out = cli_run(["editor", "profile", str(path), "--top", "1"])
            self.assertEqual(rc, 0)
            self.assertIn("EditorLoop", out)
            self.assertNotIn("PlayerLoop", out.split("top")[1])


class EditorMemoryTest(unittest.TestCase):
    def test_summarize_picks_sizes_and_deltas(self):
        data = {"snapshot_a": {"path": "a.snap", "size_bytes": 10 * 1024 * 1024},
                "snapshot_b": {"path": "b.snap", "size_bytes": 12 * 1024 * 1024},
                "size_delta_bytes": 2 * 1024 * 1024}
        s = mem_summarize(data, ref="c.json")
        self.assertEqual(s["snapshots"], ["a.snap", "b.snap"])
        self.assertEqual(s["sizes_mb"]["snapshot_a.size_bytes"], 10.0)
        self.assertEqual(s["deltas"]["size_delta_bytes"], 2 * 1024 * 1024)

    def test_cli_memory(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "c.json"
            path.write_text(json.dumps({"a": {"path": "x.snap", "size_bytes": 1}}), encoding="utf-8")
            rc, out = cli_run(["editor", "memory", str(path), "--out", str(Path(td) / "m.json")])
            self.assertEqual(rc, 0, out)
            self.assertTrue((Path(td) / "m.json").exists())


if __name__ == "__main__":
    unittest.main()
