"""결과 파일 쓰기·읽기(L0·L1 공통). 같은 입력이면 같은 바이트가 나오게 쓴다.

- 결과(result 스키마)에는 입력에서 나오는 값만 둔다. `collected_at`은 입력의 시각(커밋 시각·파일 수정 시각)이다.
- 실행마다 바뀌는 값(실행 시각, 걸린 시간, 절대 경로, 파이썬 버전)은 옆 파일 `<이름>.run.json`에 둔다.
- 키 정렬, 들여쓰기 1, 한글 그대로, 끝 줄바꿈 하나.
"""

from __future__ import annotations

import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1) + "\n"


def write(path: str | Path, result: dict, run: dict | None = None) -> tuple[Path, Path | None]:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dumps(result), encoding="utf-8", newline="\n")
    rp = None
    if run is not None:
        rp = p.with_name(p.stem + ".run.json")
        rp.write_text(dumps(run), encoding="utf-8", newline="\n")
    return p, rp


def read(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_meta(started: float, finished: float, **extra) -> dict:
    return {
        "started_at": datetime.fromtimestamp(started, timezone.utc).isoformat(timespec="seconds"),
        "seconds": round(finished - started, 2),
        "python": platform.python_version(),
        "platform": sys.platform,
        **extra,
    }


def iso_from_mtime(path: str | Path) -> str:
    return datetime.fromtimestamp(Path(path).stat().st_mtime, timezone.utc).isoformat(timespec="seconds")


def manifest_stamp(result: dict, *, effective=None) -> dict:
    """결과에 매니페스트·프로필 표식을 붙인다(manifest_sha는 공개 매니페스트 해시)."""
    from perfkit.config import settings
    from perfkit.manifest import load

    m = load()
    result["manifest_version"] = m.version
    result["manifest_sha"] = m.sha256
    try:
        s = settings()
    except Exception:
        s = None
    if s is not None and s.profile:
        result["profile_name"] = s.profile_name or "profile"
        result["profile_sha"] = s.profile_sha
    return result
