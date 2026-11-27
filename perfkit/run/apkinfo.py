"""APK에서 패키지 이름·버전을 읽는다(바이너리 AndroidManifest.xml, aapt 없이).

AXML: 청크 머리(type u16, header u16, size u32). 문자열 풀(0x0001) → 시작 태그(0x0102)의 속성.
첫 `manifest` 태그의 `package`·`versionName`·`versionCode` 속성만 읽는다.
"""

from __future__ import annotations

import hashlib
import json
import struct
import zipfile
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _strings(buf: bytes, off: int) -> list[str]:
    _, hsize, size = struct.unpack_from("<HHI", buf, off)
    count, _styles, flags, str_start, _style_start = struct.unpack_from("<IIIII", buf, off + 8)
    utf8 = bool(flags & 0x100)
    offs = struct.unpack_from(f"<{count}I", buf, off + hsize)
    base = off + str_start
    out = []
    for o in offs:
        p = base + o
        if utf8:
            n = buf[p]
            p += 2 if n & 0x80 else 1
            ln = buf[p]
            if ln & 0x80:
                ln = ((ln & 0x7F) << 8) | buf[p + 1]
                p += 2
            else:
                p += 1
            out.append(buf[p:p + ln].decode("utf-8", "replace"))
        else:
            ln = struct.unpack_from("<H", buf, p)[0]
            p += 2
            if ln & 0x8000:
                ln = ((ln & 0x7FFF) << 16) | struct.unpack_from("<H", buf, p)[0]
                p += 2
            out.append(buf[p:p + ln * 2].decode("utf-16-le", "replace"))
    return out


def parse_axml_manifest(buf: bytes) -> dict:
    off = 8  # 파일 머리
    strings: list[str] = []
    while off + 8 <= len(buf):
        ctype, hsize, size = struct.unpack_from("<HHI", buf, off)
        if size <= 0:
            break
        if ctype == 0x0001:
            strings = _strings(buf, off)
        elif ctype == 0x0102:
            # 시작 태그: 머리 16바이트 + ns u32, name u32, attrStart u16, attrSize u16, attrCount u16 …
            name_idx = struct.unpack_from("<I", buf, off + 20)[0]
            attr_start, attr_size, attr_count = struct.unpack_from("<HHH", buf, off + 24)
            if strings and strings[name_idx] == "manifest":
                out = {}
                a = off + 16 + attr_start
                for i in range(attr_count):
                    _ns, aname, raw, _vsize, _res0, dtype, data = struct.unpack_from("<IIIHBBI", buf, a + i * attr_size)
                    key = strings[aname] if aname < len(strings) else ""
                    if raw != 0xFFFFFFFF and raw < len(strings):
                        val = strings[raw]
                    elif dtype == 0x03 and data < len(strings):
                        val = strings[data]
                    elif dtype in (0x10, 0x11):
                        val = data
                    else:
                        val = data
                    if key in ("package", "versionName", "versionCode"):
                        out[key] = val
                return out
        off += size
    return {}


def apk_info(path: Path) -> dict:
    """{package, version_name, version_code, sha256, size}. 못 읽는 칸은 None."""
    p = Path(path)
    d = {"package": None, "version_name": None, "version_code": None, "size": p.stat().st_size,
         "sha256": sha256_file(p)}
    try:
        with zipfile.ZipFile(p) as z:
            m = parse_axml_manifest(z.read("AndroidManifest.xml"))
        d["package"] = m.get("package")
        d["version_name"] = m.get("versionName")
        d["version_code"] = m.get("versionCode")
    except (KeyError, zipfile.BadZipFile, struct.error, IndexError):
        pass
    return d


def build_info(apk: Path, template: str | None, explicit: str | None = None) -> dict:
    """빌드 정보 JSON(commit·sha·version·label 등)을 찾는다.

    explicit(--build-info) → 프로필 [run] build_info 템플릿. 템플릿 자리: {apk_dir}·{apk_dir_name}·{apk_stem}.
    """
    cands = []
    if explicit:
        cands.append(Path(explicit))
    if template:
        apk = Path(apk).resolve()
        from perfkit.paths import expand_text

        cands.append(Path(expand_text(template, apk_dir=str(apk.parent), apk_dir_name=apk.parent.name,
                                      apk_stem=apk.stem)))
    for c in cands:
        if c.exists():
            try:
                doc = json.loads(c.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError:
                continue
            doc["_path"] = str(c)
            return doc
    return {}
