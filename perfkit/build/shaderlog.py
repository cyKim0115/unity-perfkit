"""빌드 로그의 셰이더 변형 집계(GPU-15).

Unity 빌드 로그(Editor.log·배치 빌드 로그)의 다음 블록을 읽는다::

    Compiling shader "<이름>"
      Pass "<패스>" (vp)
        Target graphics API: vulkan
        Full variant space:         144.955.146.240
        After settings filtering:   49.152
        After built-in stripping:   192
        After scriptable stripping: 64

옛 형식 `Compiling shader "<이름>" pass "<패스>" (fp)` 한 줄 머리도 읽는다. 숫자의 자리 구분 기호(. , 공백)는
로캘마다 달라 숫자만 남긴다. 같은 셰이더가 빌드 단계마다(Addressables·플레이어) 여러 번 나오므로
`variants`는 모든 블록의 합(컴파일 작업량), `variants_unique`는 (셰이더, 패스, 단계, API)마다 최댓값의 합이다.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

SHADER_RE = re.compile(r'^\s*Compiling shader "([^"]+)"(?:\s+pass\s+"([^"]*)"\s*\((\w+)\))?\s*$')
PASS_RE = re.compile(r'^\s*Pass\s+"([^"]*)"\s*\((\w+)\)\s*$')
API_RE = re.compile(r"^\s*Target graphics API:\s*(\S+)")
NUM_RE = {
    "full": re.compile(r"^\s*Full variant space:\s*([\d.,'\s]+)$"),
    "settings": re.compile(r"^\s*After settings filtering:\s*([\d.,'\s]+)$"),
    "builtin": re.compile(r"^\s*After built-in stripping:\s*([\d.,'\s]+)$"),
    "scriptable": re.compile(r"^\s*After scriptable stripping:\s*([\d.,'\s]+)$"),
}


def _n(s: str) -> int:
    d = re.sub(r"\D", "", s)
    return int(d) if d else 0


def parse(path: Path, top: int = 20) -> dict:
    rows = []
    shader = pass_ = stage = api = None
    cur: dict | None = None
    blocks = 0

    def flush():
        nonlocal cur
        if cur is not None and "scriptable" in cur:
            rows.append(cur)
        cur = None

    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\r\n")
            m = SHADER_RE.match(line)
            if m:
                flush()
                shader, pass_, stage, api = m.group(1), m.group(2), m.group(3), None
                blocks += 1
                continue
            if shader is None:
                continue
            m = PASS_RE.match(line)
            if m:
                flush()
                pass_, stage, api = m.group(1), m.group(2), None
                continue
            m = API_RE.match(line)
            if m:
                flush()
                api = m.group(1)
                cur = {"shader": shader, "pass": pass_, "stage": stage, "api": api}
                continue
            for key, rx in NUM_RE.items():
                m = rx.match(line)
                if m:
                    if cur is None:  # API 줄 없는 옛 형식
                        cur = {"shader": shader, "pass": pass_, "stage": stage, "api": api}
                    cur[key] = _n(m.group(1))
                    break
        flush()

    total = sum(r["scriptable"] for r in rows)
    uniq: dict[tuple, int] = {}
    for r in rows:
        k = (r["shader"], r["pass"], r["stage"], r["api"])
        uniq[k] = max(uniq.get(k, 0), r["scriptable"])
    per_shader: dict[str, int] = defaultdict(int)
    per_api: dict[str, int] = defaultdict(int)
    for (s, _, _, a), v in uniq.items():
        per_shader[s] += v
        per_api[a or "?"] += v
    return {
        "variants": total,
        "variants_unique": sum(uniq.values()),
        "shader_blocks": blocks,
        "shaders": len({r["shader"] for r in rows}),
        "pass_api_rows": len(rows),
        "per_api": dict(sorted(per_api.items())),
        "top_shaders": [{"shader": s, "variants": v} for s, v in
                        sorted(per_shader.items(), key=lambda x: (-x[1], x[0]))[:top]],
    }
