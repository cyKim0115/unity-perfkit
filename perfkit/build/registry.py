"""매니페스트 수집기 이름 등록(L1). ctx: {"apk", "log", "layout"} 또는 {"result": <이미 만든 L1 결과>}."""

from __future__ import annotations

from perfkit.collectors import collector

# build.malioc(Mali Offline Compiler)은 아직 연결하지 않았다.
NAMES = ("build.apk_size", "build.align16k", "build.il2cpp", "build.layout", "build.shader_variants", "build.pad")


def _items_of(name: str):
    def fn(ctx: dict) -> dict:
        res = ctx.get("result")
        if res is None:
            from perfkit.build.collect import run
            res, _ = run(ctx.get("apk"), log=ctx.get("log"), layout=ctx.get("layout"))
        return {iid: v for iid, v in res["items"].items() if v.get("collector") == name}
    fn.__name__ = name.replace(".", "_")
    return fn


for _n in NAMES:
    collector(_n)(_items_of(_n))
