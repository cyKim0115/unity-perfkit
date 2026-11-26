"""`perfkit build`: APK·AAB, 빌드 로그, Addressables 빌드 레이아웃에서 L1 결과(result 스키마)를 만든다."""

from __future__ import annotations

import time
from pathlib import Path

import perfkit
from perfkit.build import layout as LY
from perfkit.build import package as PK
from perfkit.build import shaderlog as SL
from perfkit.diff import resultio


def _ok(values: dict, collector: str, detail: dict | None = None, **extra) -> dict:
    d = {"status": "ok", "values": values, "collector": collector}
    if detail:
        d["detail"] = detail
    d.update(extra)
    return d


def _nc(reason: str, collector: str) -> dict:
    return {"status": "not_collected", "reason": reason, "collector": collector}


def _file_ref(p: Path) -> dict:
    return {"name": p.name, "bytes": p.stat().st_size, "sha256": PK.sha256_file(p)}


def run(apk: str | Path | None, *, log: str | Path | None = None, layout: str | Path | None = None,
        top: int = 20) -> tuple[dict, dict]:
    t0 = time.time()
    timings: dict[str, float] = {}
    items: dict[str, dict] = {}
    inputs: dict[str, dict] = {}
    source: dict = {"kind": "build"}
    collected = None

    if apk:
        ap = Path(apk)
        t = time.time()
        comp = PK.composition(ap, top)
        al = PK.alignment(ap)
        timings["package"] = round(time.time() - t, 2)
        inputs["package"] = {**_file_ref(ap), "kind": comp["kind"]}
        source["ref"] = ap.name
        source["apk_sha"] = inputs["package"]["sha256"]
        collected = resultio.iso_from_mtime(ap)
        il2cpp = next((l for l in comp["libs"].get("arm64-v8a", []) if l["name"] == "libil2cpp.so"), None)
        detail = {k: comp[k] for k in ("kind", "file_bytes", "content_bytes", "entries", "gap_bytes", "gaps", "groups",
                                       "top_files")}
        if comp["kind"] == "apk":
            items["BLD-01"] = _ok({"apk_mb": comp["file_mb"]}, "build.apk_size", detail)
            items["BLD-03"] = {"status": "not_applicable", "reason": "APK 빌드(Play Asset Delivery는 AAB에만)",
                               "collector": "build.pad"}
        else:
            mods = comp.get("modules", {})
            base_mb = mods.get("base", {}).get("compressed_mb", 0.0)
            other_abi = sum(l["compressed"] for abi, ls in comp["libs"].items() if abi != "arm64-v8a" for l in ls)
            packs = comp.get("asset_packs", [])
            install = sum(p["compressed_mb"] for p in packs if p["delivery"] == "install-time")
            items["BLD-01"] = _ok({"aab_base_module_mb": base_mb,
                                   "aab_download_mb": round(base_mb - PK.mb(other_abi) + install, 3)},
                                  "build.apk_size", {**detail, "modules": mods,
                                                     "download_estimate": "base 압축 크기 - arm64 외 ABI 라이브러리 + install-time 팩 "
                                                                          "(언어·화면 밀도 분할은 반영 안 함, bundletool 값과 다를 수 있음)"})
            items["BLD-03"] = _ok({"max_pack_mb": max((p["compressed_mb"] for p in packs), default=0.0),
                                   "install_time_total_mb": round(install, 3),
                                   "allocation": [{"name": p["name"], "delivery": p["delivery"]} for p in packs]},
                                  "build.pad", {"packs": packs})
        so_detail = {"so": al["so"], "zip_checked_entries": al["zip_checked_entries"], "zip_unaligned": al["zip_unaligned"]}
        if al.get("zip_note"):
            so_detail["zip_note"] = al["zip_note"]
        items["BLD-05"] = _ok({"so_unaligned": al["so_unaligned"], "zip_aligned": al["zip_aligned"]}, "build.align16k",
                              so_detail, violations=sorted(al["so_unaligned_names"]))
        if il2cpp:
            v = {"libil2cpp_mb": PK.mb(il2cpp["bytes"])}
            items["BLD-07"] = _ok(v, "build.il2cpp", {"compressed_mb": PK.mb(il2cpp["compressed"])})
        else:
            items["BLD-07"] = _nc("lib/arm64-v8a/libil2cpp.so 없음(Mono 빌드이거나 ABI가 다름)", "build.il2cpp")
        items["BLD-02"] = _ok({"libil2cpp_mb": PK.mb(il2cpp["bytes"])} if il2cpp else {}, "build.layout",
                              {"libs": {abi: [{"name": l["name"], "mb": PK.mb(l["bytes"])} for l in ls]
                                        for abi, ls in comp["libs"].items()}})
    else:
        for iid, col in (("BLD-01", "build.apk_size"), ("BLD-05", "build.align16k"), ("BLD-07", "build.il2cpp"),
                         ("BLD-03", "build.pad")):
            items[iid] = _nc("APK·AAB를 주지 않음(--apk)", col)

    if layout:
        lp = Path(layout)
        t = time.time()
        s = LY.summarize(lp, top)
        timings["layout"] = round(time.time() - t, 2)
        inputs["layout"] = {**_file_ref(lp), "build_started": s["build_started"]}
        collected = collected or resultio.iso_from_mtime(lp)
        source.setdefault("ref", lp.name)
        items["MEM-09"] = _ok({"dup_count": s["dup_count"], "dup_mb": s["dup_mb"]}, "build.layout",
                              {"duplicates": s["duplicates"]})
        items["MEM-11"] = _ok({"texture_total_mb": s["texture_total_mb"], "top": s["texture_top"]}, "build.layout",
                              {"note": "Addressables 번들 안 텍스처(직렬화+스트리밍, 압축 전)"})
        items["MEM-12"] = _ok({"mesh_total_mb": s["mesh_total_mb"], "top": s["mesh_top"]}, "build.layout",
                              {"note": "blendshape_mb는 레이아웃에 따로 없음(메모리 스냅숏 L3-D)"})
        items["MEM-14"] = _ok({"value": s["animation_clip_mb"]}, "build.layout")
        items["MEM-17"] = _ok({"value": s["font_atlas_mb"]}, "build.layout",
                              {"top": s["font_atlas_top"], "rule": "Font 자산 + .asset 안 「<이름> Atlas」 텍스처"})
        b2 = items.get("BLD-02") or _ok({}, "build.layout")
        b2["values"].update({"bundles": [{"name": b["name"], "mb": b["mb"]} for b in s["bundles"]],
                             "top_assets": s["top_assets"]})
        b2.setdefault("detail", {}).update({"by_type_mb": s["by_type_mb"], "layout_total_mb": s["total_mb"],
                                            "bundle_detail": s["bundles"], "layout_build": s["build"]})
        items["BLD-02"] = b2
    else:
        for iid in ("MEM-09", "MEM-11", "MEM-12", "MEM-14", "MEM-17"):
            items[iid] = _nc("빌드 레이아웃을 주지 않음(--layout buildlayout.json)", "build.layout")
    if "BLD-02" in items and not items["BLD-02"].get("values"):
        items["BLD-02"] = _nc("APK·빌드 레이아웃 모두 없음", "build.layout")

    if log:
        lg = Path(log)
        t = time.time()
        sv = SL.parse(lg, top)
        timings["shader_log"] = round(time.time() - t, 2)
        inputs["log"] = _file_ref(lg)
        collected = collected or resultio.iso_from_mtime(lg)
        source.setdefault("ref", lg.name)
        if sv["pass_api_rows"]:
            items["GPU-15"] = _ok({"variants": sv["variants"]}, "build.shader_variants",
                                  {k: sv[k] for k in ("variants_unique", "shader_blocks", "shaders", "pass_api_rows",
                                                      "per_api", "top_shaders")},
                                  editor_needed=["keywords: 빌드 로그에 키워드 수가 없음(에디터 ShaderUtil)"])
        else:
            items["GPU-15"] = _nc("빌드 로그에 「Compiling shader」 블록이 없음(셰이더 캐시만 쓴 빌드이거나 다른 로그)",
                                  "build.shader_variants")
    else:
        items["GPU-15"] = _nc("빌드 로그를 주지 않음(--log)", "build.shader_variants")
    items["GPU-13"] = _nc("Mali Offline Compiler(malioc)가 이 도구에 연결되지 않음", "build.malioc")

    if not inputs:
        raise ValueError("입력이 없음: --apk·--log·--layout 중 하나 이상")
    source["inputs"] = inputs
    result = {"schema": "perfkit/result@1", "layer": "L1", "collected_at": collected, "source": source,
              "tool": {"name": "perfkit build", "version": perfkit.__version__, "mb": "10^6 bytes"},
              "items": dict(sorted(items.items()))}
    resultio.manifest_stamp(result)
    run_ = resultio.run_meta(t0, time.time(), command="perfkit build", timings=timings,
                             inputs={k: str(Path(v)) for k, v in (("apk", apk), ("log", log), ("layout", layout)) if v})
    return result, run_


def default_out(result: dict, stamp: str | None, apk: str | None) -> Path:
    from perfkit import paths

    stamp = stamp or (Path(apk).parent.name if apk else None) or result["collected_at"][:19].replace(":", "")
    return paths.results_dir() / "builds" / stamp / "l1.json"


def summary_lines(result: dict) -> list[str]:
    it = result["items"]
    v = lambda iid: it.get(iid, {}).get("values", {})
    lines = [f"layer=L1 items={len(it)} ok={sum(1 for x in it.values() if x['status'] == 'ok')}"]
    parts = []
    if v("BLD-01"):
        parts.append("BLD-01 " + " ".join(f"{k}={x}" for k, x in v("BLD-01").items()))
    if v("BLD-05"):
        parts.append(f"BLD-05 so_unaligned={v('BLD-05')['so_unaligned']} zip_aligned={v('BLD-05')['zip_aligned']}")
    if v("BLD-07"):
        parts.append(f"BLD-07 libil2cpp_mb={v('BLD-07')['libil2cpp_mb']}")
    if v("GPU-15"):
        parts.append(f"GPU-15 variants={v('GPU-15')['variants']}")
    if v("MEM-09"):
        parts.append(f"MEM-09 dup_count={v('MEM-09')['dup_count']} dup_mb={v('MEM-09')['dup_mb']}")
    lines += parts
    nc = [k for k, x in it.items() if x["status"] != "ok"]
    if nc:
        lines.append("not_collected/na: " + ", ".join(nc))
    return lines
