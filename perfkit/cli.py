"""perfkit CLI 진입점.

서브커맨드는 여기서 직접 등록하지 않는다. `perfkit/` 바로 아래의 모듈이나 패키지가
`register_cli(subparsers)`를 두면 이름순으로 불러 등록한다. 새 패키지는 자기 폴더
(`perfkit/static/` 등)의 `__init__.py`에 `register_cli`를 두기만 하면 되고 이 파일은 고치지 않는다.

- 처리 함수는 `parser.set_defaults(func=...)`로 건다. 함수는 `args`를 받아 종료 코드(int)를 돌려준다.
- 한 모듈을 불러오지 못해도 나머지 명령은 돈다. 실패한 모듈은 `--help` 끝과 stderr에 적는다.
- 모듈 안의 무거운 import는 처리 함수 안에서 한다(등록은 가볍게).
"""

from __future__ import annotations

import argparse
import importlib
import pkgutil
import sys
from dataclasses import dataclass, field
from typing import Callable

import perfkit

# 자동 등록에서 빼는 모듈(진입점·내부 도구).
_SKIP = {"cli", "__main__"}


@dataclass
class Discovery:
    registrars: list[tuple[str, Callable]] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)


def discover(package=perfkit) -> Discovery:
    """`package.__path__` 바로 아래에서 `register_cli`를 가진 모듈을 찾는다."""
    found = Discovery()
    names = sorted({m.name for m in pkgutil.iter_modules(package.__path__)})
    for name in names:
        if name.startswith("_") or name in _SKIP:
            continue
        try:
            mod = importlib.import_module(f"{package.__name__}.{name}")
        except Exception as exc:  # 한 모듈 실패가 CLI 전체를 막지 않게
            found.failures.append((name, f"{type(exc).__name__}: {exc}"))
            continue
        reg = getattr(mod, "register_cli", None)
        if callable(reg):
            found.registrars.append((name, reg))
    return found


def build_parser(found: Discovery | None = None) -> argparse.ArgumentParser:
    found = found or discover()
    parser = argparse.ArgumentParser(
        prog="perfkit",
        description="Unity 모바일 게임 성능 점검 도구. 매니페스트(manifest/checks.json) 기준으로 재고 비교한다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"perfkit {perfkit.__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    for name, reg in found.registrars:
        try:
            reg(sub)
        except Exception as exc:
            found.failures.append((name, f"register_cli 실패 {type(exc).__name__}: {exc}"))
    lines = []
    if sub.choices:
        lines.append("등록된 명령: " + ", ".join(sorted(sub.choices)))
    if found.failures:
        lines.append("불러오지 못한 모듈:")
        lines += [f"  {n}: {err}" for n, err in found.failures]
    parser.epilog = "\n".join(lines)
    return parser


def _utf8_pipes() -> None:
    """파이프로 읽을 때(에이전트 셸) 한글이 깨지지 않게 UTF-8로 쓴다.

    콘솔에 바로 쓸 때는 파이썬이 콘솔 API를 쓰므로 건드리지 않는다.
    PYTHONIOENCODING을 주면 그 값을 따른다.
    """
    import os

    if os.environ.get("PYTHONIOENCODING"):
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            if not stream.isatty() and (stream.encoding or "").lower() not in ("utf-8", "utf8"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _utf8_pipes()
    found = discover()
    parser = build_parser(found)
    args = parser.parse_args(argv)
    for name, err in found.failures:
        print(f"warn: 모듈 {name} 등록 실패: {err}", file=sys.stderr)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 0 if args.command is None else 2
    rc = func(args)
    return int(rc or 0)


if __name__ == "__main__":
    sys.exit(main())
