"""저장소 안 경로와, 설정에서 읽는 경로.

PC·프로젝트마다 다른 경로는 여기에 두지 않는다. `perfkit.config`가 환경 변수·사용자 설정·
프로젝트 프로필에서 읽는다(`python -m perfkit config keys`).
"""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
SCHEMA_DIR = PACKAGE_DIR / "schema"


def expand_path(value, base: Path | None = None) -> Path:
    """설정 파일 안 경로 하나를 푼다: `~`·환경 변수(`$VAR`·`${VAR}`, Windows는 `%VAR%`도)를 펼치고,
    상대 경로면 base(보통 그 설정 파일의 폴더) 기준으로 바꾼다. base가 없으면 현재 폴더 기준."""
    p = Path(os.path.expandvars(os.path.expanduser(str(value))))
    if p.is_absolute():
        return p
    return ((base or Path.cwd()) / p).resolve()


def expand_text(value: str, **fields) -> str:
    """경로가 아닐 수도 있는 문자열(빌드 명령 인자·템플릿)에서 `~`(맨 앞)과 환경 변수를 펼친다.
    fields가 있으면 그다음 `{이름}` 자리를 채운다. 환경 변수를 먼저 펼쳐 `${VAR}`의 중괄호가 자리로 읽히지 않는다."""
    s = os.path.expandvars(os.path.expanduser(str(value)))
    return s.format(**fields) if fields else s


def manifest_path() -> Path:
    from perfkit.config import settings

    return Path(settings().get("paths.manifest"))


def results_dir() -> Path:
    """측정 결과 저장 폴더. 설정이 없으면 ConfigError."""
    from perfkit.config import settings

    return Path(settings().require("paths.results"))


def checklist_path() -> Path | None:
    """(선택) 항목표 원본. 없으면 None."""
    from perfkit.config import settings

    v = settings().get("paths.checklist")
    return Path(v) if v else None
