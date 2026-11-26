"""기기 잠금: 한 기기(serial)에는 perfkit 회차 하나만 돈다.

`~/.perfkit/locks/device-<serial>.lock`(PERFKIT_LOCK_DIR로 바꿈)을 배타 생성한다. 내용은 pid·시각·설명.
잠금을 가진 프로세스가 이미 없으면(비정상 종료) 오래된 잠금으로 보고 넘겨받는다.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path


class LockBusy(RuntimeError):
    pass


def lock_dir() -> Path:
    return Path(os.environ.get("PERFKIT_LOCK_DIR") or (Path.home() / ".perfkit" / "locks"))


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class DeviceLock:
    def __init__(self, serial: str, what: str = "", directory: Path | None = None, alive=pid_alive):
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", serial or "default")
        self.path = (directory or lock_dir()) / f"device-{safe}.lock"
        self.what = what
        self.alive = alive
        self.held = False

    def acquire(self) -> "DeviceLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    cur = json.loads(self.path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    cur = {}
                pid = int(cur.get("pid") or 0)
                if pid and self.alive(pid):
                    raise LockBusy(f"기기 잠금이 있음({self.path.name}): pid {pid} {cur.get('what', '')} "
                                   f"since {cur.get('at', '?')}")
                try:
                    self.path.unlink()  # 주인이 없는 오래된 잠금
                except OSError:
                    pass
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "what": self.what, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}, f)
            self.held = True
            return self
        raise LockBusy(f"기기 잠금을 잡지 못함: {self.path}")

    def release(self) -> None:
        if self.held:
            try:
                self.path.unlink()
            except OSError:
                pass
            self.held = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
        return False
