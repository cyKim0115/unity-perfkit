"""메모리 스냅숏 비교 결과 요약(MEM-06·07 참고값).

에디터 쪽에서 Memory Profiler 패키지로 같은 상태의 스냅숏 두 장을 찍고 비교한 결과 JSON을 받는다.
비교 결과 모양은 도구마다 달라, 알려진 칸만 골라 읽고 나머지는 그대로 `raw`에 둔다.

읽는 칸(있으면):
- 스냅숏별 크기(`size_bytes`·`file_size`)와 비교 차이(`delta`·`diff`·`size_delta_bytes`)
- 분류별 메모리(`native`·`managed`·`graphics`·`untracked`·`android_runtime`, MB 또는 바이트)
- Unity Object 수 차이(`object_delta`·`objects_delta`), 누수 의심 관리 객체(`leaked_shells`)

MEM-06·07은 매니페스트에서 실기기(L3-D) 층이다. 에디터 스냅숏 값은 그 층의 기준·판정에 쓰지 않고
같은 에디터에서의 추세 확인용으로만 남긴다.
"""

from __future__ import annotations

SCHEMA = "perfkit/editor-memory@1"
MB = 1024 * 1024


class MemoryCompareError(ValueError):
    pass


def _walk(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{prefix}[{i}]")
    else:
        yield prefix, obj


def summarize(data: dict, *, ref: str) -> dict:
    if not isinstance(data, dict) or not data:
        raise MemoryCompareError("비교 결과가 비었거나 객체가 아님")
    flat = dict(_walk(data))
    picked: dict[str, float] = {}
    keys = ("size", "delta", "diff", "native", "managed", "graphics", "untracked", "android", "object", "leak",
            "shell", "count", "bytes")
    for path, v in flat.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        leaf = path.rsplit(".", 1)[-1].lower()
        if any(k in leaf for k in keys):
            picked[path] = v

    def mb(path, v):
        leaf = path.rsplit(".", 1)[-1].lower()
        return round(v, 3) if leaf.endswith("_mb") or leaf == "mb" else round(v / MB, 3)

    sizes = {p: mb(p, v) for p, v in picked.items()
             if "size" in p.lower() and "delta" not in p.lower() and "byte" in p.lower()}
    deltas = {p: v for p, v in picked.items() if "delta" in p.lower() or "diff" in p.lower()}
    snaps = [v for k, v in flat.items() if isinstance(v, str) and v.endswith(".snap")]
    return {
        "schema": SCHEMA,
        "ref": ref,
        "snapshots": sorted(set(snaps)),
        "sizes_mb": sizes,
        "deltas": deltas,
        "picked": picked,
        "raw": data,
        "note": "MEM-06·07 참고값(에디터 스냅숏). L3-D 기준·판정에 쓰지 않음",
    }


def summary_lines(summary: dict) -> list[str]:
    lines = [f"snapshots={len(summary['snapshots'])} picked={len(summary['picked'])}"]
    for s in summary["snapshots"]:
        lines.append(f"  snap {s}")
    for p, v in summary["sizes_mb"].items():
        lines.append(f"  size {p} = {v} MB")
    for p, v in summary["deltas"].items():
        lines.append(f"  delta {p} = {v}")
    return lines
