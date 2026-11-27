"""수집기 이름 규칙과 등록부.

매니페스트의 각 항목·층은 `collector` 이름을 가진다. 이름은 `<패키지>.<수집기>` 형식이고,
앞부분이 그 수집기를 구현할 perfkit 패키지를 가리킨다.

    static.*  → perfkit/static/  (L0 정적 점검)
    build.*   → perfkit/build/   (L1 빌드 산출물)
    editor.*  → perfkit/editor/  (L2 에디터 Play, 개발 빌드 프로파일·메모리 스냅샷 분석)
    device.*  → perfkit/device/  (L3-E·L3-D 기기 수집)
    run.*     → perfkit/run/     (회차 실행·조건)
    console.* → perfkit/console/ (스토어 콘솔 지표)

구현하는 쪽은 자기 패키지에서 데코레이터로 등록한다::

    from perfkit.collectors import collector

    @collector("build.apk_size")
    def apk_size(ctx) -> dict:
        ...  # {"BLD-01": {"status": "ok", "values": {...}}} 처럼 항목별 결과를 돌려준다

수집기 함수의 인자·반환 모양은 구현 패키지가 정한다. 여기서는 이름과 등록·조회만 정한다.
"""

from __future__ import annotations

import importlib
import re
from typing import Callable

NAME_RE = re.compile(r"^(static|build|editor|device|run|console)\.[a-z][a-z0-9_]*$")

OWNERS = {
    "static": "perfkit/static",
    "build": "perfkit/build",
    "editor": "perfkit/editor",
    "device": "perfkit/device",
    "run": "perfkit/run",
    "console": "perfkit/console",
}

# 층별로 허용하는 수집기 패키지. 매니페스트 검사가 쓴다.
LAYER_PACKAGES = {
    "L0": {"static"},
    "L1": {"build"},
    "L2": {"editor"},
    "L3-E": {"device", "run", "editor"},
    "L3-D": {"device", "run", "editor"},
    "console": {"console"},
}

# 등록되지 않은 이름의 상태. `manifest collectors`가 「구현」 대신 이 표시를 쓴다.
# - 에디터 합침: perfkit 혼자서는 값을 못 내고, 프로젝트 안 에디터 도구의 JSON을 `static --editor`가 합친다.
# - 예정: 매니페스트에 이름만 있고 아직 코드가 없다. 그 항목은 결과에 「수집 못 함」으로 남는다.
EDITOR_MERGED = frozenset({"static.ui_scan", "static.addressables_dupes"})
PLANNED = frozenset({
    "build.malioc", "console.vitals", "device.exit_info", "device.gpu_counters", "device.power",
    "device.probe_state", "editor.device_profile", "editor.memory_snapshot", "run.conditions",
})

_REGISTRY: dict[str, Callable] = {}


def collector(name: str):
    """수집기 함수를 이름으로 등록하는 데코레이터."""
    if not NAME_RE.match(name):
        raise ValueError(f"수집기 이름 형식이 아님: {name}")

    def deco(fn: Callable) -> Callable:
        if name in _REGISTRY and _REGISTRY[name] is not fn:
            raise ValueError(f"수집기 이름 중복 등록: {name}")
        _REGISTRY[name] = fn
        return fn

    return deco


def package_of(name: str) -> str:
    return name.split(".", 1)[0]


def owner_of(name: str) -> str:
    return OWNERS.get(package_of(name), "?")


def load_packages() -> dict[str, str]:
    """구현 패키지를 불러 등록을 채운다. 없는 패키지는 건너뛰고 상태를 돌려준다."""
    status = {}
    for pkg in OWNERS:
        try:
            importlib.import_module(f"perfkit.{pkg}")
            status[pkg] = "loaded"
        except ModuleNotFoundError as exc:
            status[pkg] = "absent" if exc.name == f"perfkit.{pkg}" else f"error: {exc}"
        except Exception as exc:
            status[pkg] = f"error: {type(exc).__name__}: {exc}"
    return status


def get(name: str) -> Callable | None:
    if name not in _REGISTRY:
        load_packages()
    return _REGISTRY.get(name)


def status_of(name: str, registry: dict | None = None) -> str:
    """구현 · 에디터 합침 · 예정 · 미구현(이름도 목록에도 없음)."""
    reg = registered() if registry is None else registry
    if name in reg:
        return "구현"
    if name in EDITOR_MERGED:
        return "에디터 합침"
    if name in PLANNED:
        return "예정"
    return "미구현"


def registered() -> dict[str, Callable]:
    load_packages()
    return dict(_REGISTRY)
