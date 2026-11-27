"""editor.* 수집기 이름 등록. 값은 `perfkit editor play`가 한 번에 만든다(수집기별 함수는 이름표)."""

from __future__ import annotations

from perfkit.collectors import collector


def _from_play(name: str):
    def fn(ctx) -> dict:
        from perfkit.editor.play import items_from_play

        return {k: v for k, v in items_from_play(ctx["play"]).items() if v.get("collector") == name}

    fn.__name__ = name.replace(".", "_")
    return fn


for _name in ("editor.play_counters", "editor.play_events", "editor.frame_debugger", "editor.render_debug",
              "editor.play_profile"):
    collector(_name)(_from_play(_name))
