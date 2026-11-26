"""APK·AAB 크기·구성(BLD-01·02·03·07)과 16KB 페이지 정렬(BLD-05). zipfile·struct만 쓴다.

- 크기 단위 MB는 10^6 바이트(스토어 한도 표기와 같게). 바이트 값도 함께 남긴다.
- 「파일 크기 ≫ 항목 합계」이면 증분 패키징이 남긴 빈 구간(gap)이다. 256KiB 넘는 빈 구간을 센다.
- 16KB 정렬: `.so`의 PT_LOAD p_align ≥ 16384(64비트 ABI만 필수), 압축 안 한(STORED) 항목의 zip 오프셋이
  `.so`는 16384, 그 밖은 4의 배수(`zipalign -c -P 16 4`와 같은 규칙).
"""

from __future__ import annotations

import hashlib
import struct
import zipfile
from collections import defaultdict
from pathlib import Path

MB = 1_000_000
PAGE_16K = 16384
GAP_MIN = 256 * 1024
ABI_64 = {"arm64-v8a", "x86_64"}
PT_LOAD = 1


def mb(n: int | float) -> float:
    return round(n / MB, 3)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _data_offset(f, info: zipfile.ZipInfo) -> int:
    f.seek(info.header_offset)
    hdr = f.read(30)
    nl, el = struct.unpack("<HH", hdr[26:30])
    return info.header_offset + 30 + nl + el


def kind_of(z: zipfile.ZipFile) -> str:
    names = set(z.namelist())
    if "BundleConfig.pb" in names or "base/manifest/AndroidManifest.xml" in names:
        return "aab"
    return "apk"


def _group_key(name: str, aab: bool) -> str:
    parts = name.split("/")
    if aab and len(parts) > 1:
        parts = parts[1:] if parts[0] == "base" else parts  # base/assets/... → assets/...
    if parts[0] == "assets" and len(parts) > 2 and parts[1] == "aa":
        return "/".join(parts[:3])
    if parts[0] == "lib" and len(parts) > 1:
        return "/".join(parts[:2])
    return "/".join(parts[:2]) if len(parts) > 1 else parts[0]


def composition(path: Path, top: int = 20) -> dict:
    size = path.stat().st_size
    with zipfile.ZipFile(path) as z, open(path, "rb") as f:
        aab = kind_of(z) == "aab"
        infos = sorted(z.infolist(), key=lambda i: i.header_offset)
        content = sum(i.compress_size for i in infos)
        gaps = []
        for idx, i in enumerate(infos):
            end = _data_offset(f, i) + i.compress_size
            nxt = infos[idx + 1].header_offset if idx + 1 < len(infos) else z.start_dir
            if nxt - end > GAP_MIN:
                gaps.append({"offset": end, "bytes": nxt - end, "after": i.filename})
        groups: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
        for i in infos:
            g = groups[_group_key(i.filename, aab)]
            g[0] += i.compress_size
            g[1] += i.file_size
            g[2] += 1
        libs = {}
        for i in infos:
            parts = i.filename.split("/")
            if aab and parts[0] != "base":
                continue
            if aab:
                parts = parts[1:]
            if len(parts) == 3 and parts[0] == "lib" and parts[2].endswith(".so"):
                libs.setdefault(parts[1], []).append({"name": parts[2], "bytes": i.file_size,
                                                      "compressed": i.compress_size,
                                                      "stored": i.compress_type == zipfile.ZIP_STORED})
        modules = {}
        if aab:
            for i in infos:
                top_dir = i.filename.split("/", 1)[0]
                if top_dir in ("BUNDLE-METADATA", "META-INF") or "/" not in i.filename:
                    continue
                modules.setdefault(top_dir, [0, 0])
                modules[top_dir][0] += i.compress_size
                modules[top_dir][1] += 1
        out = {
            "kind": "aab" if aab else "apk",
            "file_bytes": size, "file_mb": mb(size),
            "content_bytes": content, "entries": len(infos),
            "gap_bytes": sum(g["bytes"] for g in gaps), "gaps": gaps[:10],
            "groups": [{"group": k, "compressed_mb": mb(v[0]), "uncompressed_mb": mb(v[1]), "files": v[2]}
                       for k, v in sorted(groups.items(), key=lambda x: (-x[1][0], x[0]))],
            "top_files": [{"name": i.filename, "compressed_mb": mb(i.compress_size), "uncompressed_mb": mb(i.file_size)}
                          for i in sorted(infos, key=lambda i: (-i.compress_size, i.filename))[:top]],
            "libs": {abi: sorted(v, key=lambda x: x["name"]) for abi, v in sorted(libs.items())},
        }
        if aab:
            out["modules"] = {k: {"compressed_mb": mb(v[0]), "files": v[1]} for k, v in sorted(modules.items())}
            out["asset_packs"] = _asset_packs(z, modules)
        return out


def _asset_packs(z: zipfile.ZipFile, modules: dict) -> list[dict]:
    packs = []
    for name in sorted(modules):
        if name == "base":
            continue
        try:
            man = z.read(f"{name}/manifest/AndroidManifest.xml")
        except KeyError:
            man = b""
        # 매니페스트는 protobuf XML이지만 배달 방식 요소 이름은 문자열로 들어 있다
        delivery = next((d for d in ("install-time", "fast-follow", "on-demand")
                         if d.encode() in man or d.replace("-", "").encode() in man.replace(b"-", b"")), "unknown")
        packs.append({"name": name, "delivery": delivery, "compressed_mb": mb(modules[name][0])})
    return packs


# --------------------------------------------------------------------------- 16KB 정렬

def elf_load_align(data: bytes) -> dict:
    """ELF 머리에서 PT_LOAD들의 최소 p_align. {"bits", "min_align", "loads"} 또는 {"error"}."""
    if data[:4] != b"\x7fELF":
        return {"error": "ELF 아님"}
    cls, endian = data[4], data[5]
    e = "<" if endian == 1 else ">"
    try:
        if cls == 2:
            phoff = struct.unpack_from(e + "Q", data, 0x20)[0]
            phentsize, phnum = struct.unpack_from(e + "HH", data, 0x36)
            fmt, align_off = e + "IIQQQQQQ", 7
        elif cls == 1:
            phoff = struct.unpack_from(e + "I", data, 0x1C)[0]
            phentsize, phnum = struct.unpack_from(e + "HH", data, 0x2A)
            fmt, align_off = e + "IIIIIIII", 7
        else:
            return {"error": f"ELF 클래스 {cls}"}
        aligns = []
        for k in range(phnum):
            off = phoff + k * phentsize
            if off + struct.calcsize(fmt) > len(data):
                return {"error": "프로그램 헤더가 잘림"}
            ph = struct.unpack_from(fmt, data, off)
            if ph[0] == PT_LOAD:
                aligns.append(ph[align_off])
    except struct.error as exc:
        return {"error": str(exc)}
    return {"bits": 64 if cls == 2 else 32, "min_align": min(aligns) if aligns else None, "loads": len(aligns)}


def _read_head(z: zipfile.ZipFile, info: zipfile.ZipInfo, n: int = 65536) -> bytes:
    with z.open(info) as fp:
        head = fp.read(n)
    # 프로그램 헤더가 앞 64KiB 밖이면 더 읽는다(드묾)
    return head


def alignment(path: Path) -> dict:
    with zipfile.ZipFile(path) as z, open(path, "rb") as f:
        aab = kind_of(z) == "aab"
        so_rows = []
        stored_bad = []
        stored_total = 0
        for info in sorted(z.infolist(), key=lambda i: i.filename):
            name = info.filename
            is_so = name.endswith(".so") and "/lib/" in "/" + name
            if info.compress_type == zipfile.ZIP_STORED and not name.endswith("/"):
                stored_total += 1
                need = PAGE_16K if is_so else 4
                off = _data_offset(f, info)
                if off % need:
                    stored_bad.append({"name": name, "offset": off, "need": need})
            if is_so:
                parts = name.split("/")
                abi = parts[-2] if len(parts) >= 2 else "?"
                head = _read_head(z, info)
                el = elf_load_align(head)
                if "error" not in el and el.get("min_align") is None:
                    el = {**el, "error": "PT_LOAD 없음"}
                ok = "error" not in el and el["min_align"] >= PAGE_16K
                so_rows.append({"name": name, "abi": abi, "min_align": el.get("min_align"), "bits": el.get("bits"),
                                "aligned_16k": ok, "required": abi in ABI_64,
                                "stored": info.compress_type == zipfile.ZIP_STORED,
                                **({"error": el["error"]} if "error" in el else {})})
        unaligned = [r for r in so_rows if r["required"] and not r["aligned_16k"]]
        return {
            "zip_aligned": not stored_bad,
            "zip_checked_entries": stored_total,
            "zip_unaligned": stored_bad[:50],
            "zip_note": "AAB는 스토어가 APK를 다시 만들어 zip 정렬은 참고값" if aab else None,
            "so": so_rows,
            "so_unaligned": len(unaligned),
            "so_unaligned_names": [r["name"] for r in unaligned],
        }
