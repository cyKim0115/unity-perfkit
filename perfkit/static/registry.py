"""매니페스트 수집기 이름 등록(L0). 수집은 한 번에 하고(`collect.run`), 이름별로 그 몫의 항목만 돌려준다.

ctx: {"project": <폴더>, "cfg": <static_config 결과>} 또는 {"result": <이미 만든 L0 결과>}.
"""

from __future__ import annotations

from perfkit.collectors import collector

# 에디터가 있어야 값을 내는 수집기는 등록하지 않는다(에디터 도구 몫): static.ui_scan, static.addressables_dupes
NAMES = ("static.source_scan", "static.auditor_code", "static.auditor_settings", "static.import_rules",
         "static.project_settings", "static.urp_settings", "static.render_assets")


def _items_of(name: str):
    def fn(ctx: dict) -> dict:
        res = ctx.get("result")
        if res is None:
            from perfkit.static.collect import run
            res, _ = run(ctx["project"], ctx.get("cfg") or {})
        return {iid: v for iid, v in res["items"].items() if v.get("collector") == name}
    fn.__name__ = name.replace(".", "_")
    return fn


for _n in NAMES:
    collector(_n)(_items_of(_n))
