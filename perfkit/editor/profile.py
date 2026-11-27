"""프로파일러 캡처 요약(`perfkit/editor-profile@1`) 읽기.

에디터 쪽 도구가 .raw(바이너리 로그)·.data를 불러 메인 스레드만 줄인 요약을 낸다. 기기 개발 빌드 캡처
(`perfkit run --capture profiler`가 받은 `profiler.raw`)와 에디터 Play 구도 캡처가 같은 형식이다.

- `frameMs`: 프레임 시간 분포(n·mean·p50·p95·p99·min·max)와 `frameMsHistogram`(≤16.7·≤33.3·≤50·>50 ms)
- `topSelf`: 자기 시간 상위 마커(프레임 평균 ms·비중·호출 수)
- `gcAllocCallsPerFrame`·`gcAllocBytesPerFrame`: GC.Alloc 샘플 수·바이트
- `markers`: 지켜보는 마커의 포함 시간(병목 판별 FRM-04, UI CPU-09, 스크립트 CPU-01, 애니메이터 CPU-07)
- `bottleneckPct`: FRM-04 마커의 프레임 시간 대비 비중(%)
"""

from __future__ import annotations

SCHEMA = "perfkit/editor-profile@1"

# FRM-04 판별: 비중이 가장 큰 마커가 가리키는 병목.
BOTTLENECK_HINT = {
    "Gfx.WaitForPresentOnGfxThread": "GPU 병목",
    "Gfx.WaitForCommands": "메인 스레드 병목(렌더 스레드가 기다림)",
    "Gfx.WaitForGfxCommandsFromMainThread": "메인 스레드 병목(렌더 스레드가 기다림)",
    "WaitForTargetFPS": "여유(목표 FPS 대기)",
}


class ProfileSummaryError(ValueError):
    pass


def check(summary: dict) -> None:
    if summary.get("schema") != SCHEMA:
        raise ProfileSummaryError(f"프로파일 요약 스키마가 아님: {summary.get('schema')!r} (기대 {SCHEMA})")
    if not summary.get("frames"):
        raise ProfileSummaryError("프로파일 요약에 프레임이 없음")


def bottleneck(summary: dict) -> dict:
    """FRM-04 값: 마커별 비중과 가장 큰 쪽의 판별."""
    pct = summary.get("bottleneckPct") or {}
    vals = {k: float(v or 0) for k, v in pct.items()}
    top = max(vals, key=vals.get) if vals and max(vals.values()) > 0 else None
    return {"pct": vals, "dominant": top, "hint": BOTTLENECK_HINT.get(top) if top else "판별 마커 없음"}


def summary_lines(summary: dict, top: int = 20) -> list[str]:
    check(summary)
    fm = summary.get("frameMs") or {}
    gc = summary.get("gcAllocBytesPerFrame") or {}
    lines = [
        f"source={summary.get('source')} frames={summary['frames']} first={summary.get('firstFrame')} "
        f"elapsed={summary.get('elapsedMs')}ms",
        f"frame ms p50={fm.get('p50')} p95={fm.get('p95')} p99={fm.get('p99')} max={fm.get('max')} "
        f"hist={summary.get('frameMsHistogram')}",
        f"GC.Alloc B/frame p50={gc.get('p50')} mean={gc.get('mean')} calls/frame p50="
        f"{(summary.get('gcAllocCallsPerFrame') or {}).get('p50')}",
    ]
    b = bottleneck(summary)
    lines.append(f"FRM-04 {b['hint']} " + " ".join(f"{k}={v:g}%" for k, v in b["pct"].items()))
    lines.append(f"top {min(top, len(summary.get('topSelf') or []))} self ms/frame:")
    for i, m in enumerate((summary.get("topSelf") or [])[:top], 1):
        lines.append(f"  {i:2d}. {m['selfMsPerFrame']:8.4f} ms {m['selfPct']:6.2f}%  {m['marker']}"[:200])
    return lines
