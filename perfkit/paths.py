"""저장소 안 경로와, 설정에서 읽는 경로.

PC·프로젝트마다 다른 경로는 여기에 두지 않는다. `perfkit.config`가 환경 변수·사용자 설정·
프로젝트 프로필에서 읽는다(`python -m perfkit config keys`).
"""

from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
SCHEMA_DIR = PACKAGE_DIR / "schema"


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
