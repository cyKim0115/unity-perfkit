"""이미지·오디오 파일 머리만 읽어 크기·길이를 안다(픽셀·샘플은 읽지 않음, 표준 라이브러리만)."""

from __future__ import annotations

import struct
from pathlib import Path


def image_size(path: Path) -> tuple[int, int] | None:
    """(가로, 세로). 모르는 형식이면 None. PNG·JPEG·TGA·PSD·BMP."""
    try:
        with open(path, "rb") as f:
            head = f.read(64)
            ext = path.suffix.lower()
            if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
                return struct.unpack(">II", head[16:24])
            if head[:4] == b"8BPS":
                h, w = struct.unpack(">II", head[14:22])
                return w, h
            if head[:2] == b"BM" and len(head) >= 26:
                w, h = struct.unpack("<ii", head[18:26])
                return w, abs(h)
            if head[:2] == b"\xff\xd8":
                return _jpeg_size(f)
            if ext == ".tga" and len(head) >= 18:
                w, h = struct.unpack("<HH", head[12:16])
                return w, h
    except OSError:
        return None
    return None


def _jpeg_size(f) -> tuple[int, int] | None:
    f.seek(2)
    while True:
        b = f.read(1)
        while b and b != b"\xff":
            b = f.read(1)
        while b == b"\xff":
            b = f.read(1)
        if not b:
            return None
        marker = b[0]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            continue
        seg = f.read(2)
        if len(seg) < 2:
            return None
        length = struct.unpack(">H", seg)[0]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            data = f.read(5)
            if len(data) < 5:
                return None
            h, w = struct.unpack(">HH", data[1:5])
            return w, h
        f.seek(length - 2, 1)


def audio_info(path: Path) -> dict | None:
    """{"channels", "rate", "seconds"}. WAV·OGG(Vorbis)만. 모르면 None."""
    try:
        with open(path, "rb") as f:
            head = f.read(12)
            if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
                return _wav(f)
            if head[:4] == b"OggS":
                return _ogg(path)
    except (OSError, struct.error):
        return None
    return None


def _wav(f) -> dict | None:
    channels = rate = block = None
    data_len = None
    while True:
        hdr = f.read(8)
        if len(hdr) < 8:
            break
        cid, size = hdr[:4], struct.unpack("<I", hdr[4:8])[0]
        if cid == b"fmt ":
            fmt = f.read(size)
            _, channels, rate, _, block = struct.unpack("<HHIIH", fmt[:14])
        elif cid == b"data":
            data_len = size
            break
        else:
            f.seek(size + (size & 1), 1)
    if not channels or not rate:
        return None
    secs = (data_len / (block or 1) / rate) if data_len is not None and block else None
    return {"channels": channels, "rate": rate, "seconds": round(secs, 3) if secs is not None else None}


def _ogg(path: Path) -> dict | None:
    with open(path, "rb") as f:
        first = f.read(4096)
        k = first.find(b"\x01vorbis")
        if k < 0:
            return None
        channels = first[k + 11]
        rate = struct.unpack("<I", first[k + 12:k + 16])[0]
        size = path.stat().st_size
        f.seek(max(0, size - 65536))
        tail = f.read()
    j = tail.rfind(b"OggS")
    secs = None
    if j >= 0 and j + 14 <= len(tail) and rate:
        granule = struct.unpack("<q", tail[j + 6:j + 14])[0]
        if granule >= 0:
            secs = round(granule / rate, 3)
    return {"channels": channels, "rate": rate, "seconds": secs}
