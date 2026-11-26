"""Unity 프로젝트 폴더 읽기(읽기 전용): 파일 목록, guid → 경로, 범위(포함·제외) 고르기."""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

GUID_RE = re.compile(r"^guid:\s*([0-9a-f]{32})\s*$", re.M)


@dataclass
class Project:
    root: Path
    files: list[str] = field(default_factory=list)       # Assets/ 아래 상대 경로(/ 구분, 정렬)
    guid_to_path: dict[str, str] = field(default_factory=dict)
    path_to_guid: dict[str, str] = field(default_factory=dict)

    def abs(self, rel: str) -> Path:
        return self.root / rel

    def read_text(self, rel: str) -> str:
        with open(self.root / rel, encoding="utf-8-sig", errors="replace") as f:
            return f.read()

    def path_of_guid(self, guid: str | None) -> str | None:
        return self.guid_to_path.get(guid or "")


def _unity_hidden(name: str) -> bool:
    # Unity가 임포트하지 않는 이름: 점으로 시작, ~로 끝남, cvs·tmp 확장자 등
    return name.startswith(".") or name.endswith("~") or name.lower() in ("cvs",) or name.lower().endswith(".tmp")


def load(root: str | Path) -> Project:
    root = Path(root).resolve()
    if not (root / "Assets").is_dir() or not (root / "ProjectSettings").is_dir():
        raise FileNotFoundError(f"Unity 프로젝트 폴더가 아님(Assets·ProjectSettings 없음): {root}")
    p = Project(root)
    out = []
    for dirpath, dirnames, filenames in os.walk(root / "Assets"):
        dirnames[:] = sorted(d for d in dirnames if not _unity_hidden(d))
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        for fn in filenames:
            if _unity_hidden(fn):
                continue
            out.append(f"{rel_dir}/{fn}")
    p.files = sorted(out)
    for rel in p.files:
        if rel.endswith(".meta"):
            try:
                with open(root / rel, encoding="utf-8", errors="replace") as f:
                    head = f.read(200)
            except OSError:
                continue
            m = GUID_RE.search(head)
            if m:
                target = rel[:-5]
                p.guid_to_path[m.group(1)] = target
                p.path_to_guid[target] = m.group(1)
    return p


@dataclass
class Scope:
    """검사 범위. include는 폴더 접두어 목록, exclude는 glob 목록(** 지원)."""
    include: list[str] = field(default_factory=lambda: ["Assets"])
    exclude: list[str] = field(default_factory=list)

    def __post_init__(self):
        self._ex = [re.compile(_glob_to_re(g)) for g in self.exclude]

    def contains(self, rel: str) -> bool:
        if not any(rel == d.rstrip("/") or rel.startswith(d.rstrip("/") + "/") for d in self.include):
            return False
        return not any(r.match(rel) for r in self._ex)


def _glob_to_re(g: str) -> str:
    g = g.replace("\\", "/")
    out, i = [], 0
    while i < len(g):
        if g[i:i + 3] == "**/":
            out.append("(?:.*/)?")
            i += 3
        elif g[i:i + 2] == "**":
            out.append(".*")
            i += 2
        elif g[i] == "*":
            out.append("[^/]*")
            i += 1
        elif g[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(g[i]))
            i += 1
    return "^" + "".join(out) + "$"


def matches(rel: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(rel, pattern)


# --------------------------------------------------------------------------- git(있으면)

def git_info(root: Path) -> dict:
    """{"commit", "commit_time", "dirty", "dirty_count"}. git이 없으면 빈 dict."""
    def run(*args):
        r = subprocess.run(["git", "--no-optional-locks", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip())
        return r.stdout.strip()

    try:
        commit = run("rev-parse", "HEAD")
        ctime = run("log", "-1", "--format=%cI", "HEAD")
        status = run("status", "--porcelain", "--untracked-files=no", "--", "Assets", "ProjectSettings", "Packages")
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return {}
    lines = [l for l in status.splitlines() if l.strip()]
    return {"commit": commit, "commit_time": ctime, "dirty": bool(lines), "dirty_count": len(lines)}
