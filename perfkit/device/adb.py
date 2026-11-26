"""adb 호출과 logcat 이어 받기.

- `Adb`는 serial 하나에 묶인 adb 래퍼다. 실제 실행은 `runner`(기본 subprocess)가 하므로 시험에서는
  가짜 runner(`tests/fakeadb.py`)로 바꾼다. adb 서버가 다른 프로그램(빌드 도구·다른 adb)에 의해 꺼지면
  「daemon not running」·「device offline」이 잠깐 나오므로 짧게 다시 시도한다.
- `LogcatTail`은 `logcat -v epoch`를 파일로 받는다. 스트림이 끊기면 마지막 줄 시각부터(`-T`) 다시 붙고
  같은 줄은 한 번만 쓴다. 다시 붙은 횟수(`restarts`)는 관문(logcat 재연결)에 쓴다.
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

RETRY_HINTS = ("daemon not running", "device offline", "no devices/emulators found", "cannot connect to daemon",
               "error: closed", "device '", "protocol fault")


@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class AdbError(RuntimeError):
    pass


def subprocess_runner(argv: list[str], timeout: float, binary: bool = False) -> Result:
    try:
        r = subprocess.run(argv, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return Result(124, "", f"timeout after {timeout}s")
    except FileNotFoundError as exc:
        return Result(127, "", str(exc))
    if binary:
        return Result(r.returncode, r.stdout, r.stderr.decode("utf-8", "replace"))  # type: ignore[arg-type]
    return Result(r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace"))


def subprocess_popen(argv: list[str]):
    return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


class Adb:
    """serial 하나에 묶인 adb. `runner(argv, timeout) -> Result`, `popen(argv) -> 프로세스`."""

    def __init__(self, exe: str, serial: str | None = None, *, runner: Callable = subprocess_runner,
                 popen: Callable = subprocess_popen, retries: int = 4, retry_wait: float = 2.0,
                 sleep: Callable[[float], None] = time.sleep):
        self.exe = exe
        self.serial = serial
        self.runner = runner
        self.popen_fn = popen
        self.retries = retries
        self.retry_wait = retry_wait
        self.sleep = sleep
        self.retry_count = 0  # 다시 시도한 횟수(진단용)

    def argv(self, *args: str) -> list[str]:
        base = [self.exe]
        if self.serial:
            base += ["-s", self.serial]
        return base + list(args)

    def run(self, *args: str, timeout: float = 60, check: bool = False, retry: bool = True) -> Result:
        r = Result(1, "", "not run")
        for i in range(self.retries + 1):
            r = self.runner(self.argv(*args), timeout)
            if r.ok or not retry:
                break
            msg = (r.stderr or "") + (r.stdout or "")
            if not any(h in msg for h in RETRY_HINTS) or i == self.retries:
                break
            self.retry_count += 1
            self.sleep(self.retry_wait)
            if "daemon not running" in msg or "cannot connect" in msg:
                self.runner([self.exe, "start-server"], 30)
        if check and not r.ok:
            raise AdbError(f"adb {' '.join(args)} 실패({r.returncode}): {(r.stderr or r.stdout).strip()[:300]}")
        return r

    def shell(self, cmd: str, timeout: float = 60, check: bool = False) -> str:
        return self.run("shell", cmd, timeout=timeout, check=check).stdout

    def global_run(self, *args: str, timeout: float = 30) -> Result:
        """serial 없이(adb devices·version 등)."""
        return self.runner([self.exe, *args], timeout)

    def popen(self, *args: str):
        return self.popen_fn(self.argv(*args))

    # ---- 자주 쓰는 것
    def getprop(self, name: str) -> str:
        return self.shell(f"getprop {name}", timeout=20).strip()

    def pidof(self, package: str) -> int | None:
        out = self.shell(f"pidof {package}", timeout=20).strip()
        m = re.match(r"(\d+)", out)
        return int(m.group(1)) if m else None

    def file_size(self, path: str) -> int | None:
        out = self.shell(f"stat -c %s {path} 2>/dev/null", timeout=20).strip()
        return int(out) if out.isdigit() else None


def parse_devices(text: str) -> list[tuple[str, str]]:
    """`adb devices` → [(serial, state)]."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            out.append((parts[0], parts[1]))
    return out


def parse_version(text: str) -> str | None:
    m = re.search(r"Version\s+([0-9][^\s]*)", text)
    if m:
        return m.group(1)
    m = re.search(r"Android Debug Bridge version\s+(\S+)", text)
    return m.group(1) if m else None


EPOCH_RE = re.compile(rb"^\s*(\d+\.\d+)\s")


class LogcatTail:
    """`logcat -v epoch`를 파일에 이어 쓴다. 끊기면 마지막 시각부터 다시 붙는다.

    on_line(text, host_time)은 새 줄마다(받는 스레드에서) 불린다. 시험에서는 adb.popen을 가짜로 바꾼다.
    """

    def __init__(self, adb: Adb, path: Path, *, buffers: str = "main,system,crash",
                 on_line: Callable[[str, float], None] | None = None, reconnect_wait: float = 2.0,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
                 max_restarts: int = 50):
        self.adb = adb
        self.path = Path(path)
        self.buffers = buffers
        self.on_line = on_line
        self.reconnect_wait = reconnect_wait
        self.clock = clock
        self.sleep = sleep
        self.max_restarts = max_restarts
        self.restarts = 0
        self.lines = 0
        self.last_epoch: float | None = None
        self._seen_at_last: set[bytes] = set()
        self._stop = threading.Event()
        self._proc = None
        self._f = None
        self._th: threading.Thread | None = None

    def start(self) -> "LogcatTail":
        self._f = self.path.open("ab")
        self._th = threading.Thread(target=self._loop, name="logcat-tail", daemon=True)
        self._th.start()
        return self

    def _argv(self, first: bool) -> list[str]:
        args = ["logcat", "-v", "epoch", "-b", self.buffers]
        if not first and self.last_epoch is not None:
            args += ["-T", f"{self.last_epoch:.3f}"]
        return args

    def _accept(self, raw: bytes) -> bool:
        """다시 붙은 뒤 이미 받은 줄은 버린다(같은 시각의 같은 줄)."""
        m = EPOCH_RE.match(raw)
        if not m:
            return True
        t = float(m.group(1))
        if self.last_epoch is not None:
            if t < self.last_epoch - 0.0005:
                return False
            if abs(t - self.last_epoch) <= 0.0005:
                if raw in self._seen_at_last:
                    return False
                self._seen_at_last.add(raw)
                return True
        self.last_epoch = t
        self._seen_at_last = {raw}
        return True

    def _loop(self) -> None:
        first = True
        while not self._stop.is_set():
            try:
                self._proc = self.adb.popen(*self._argv(first))
            except Exception:
                self._proc = None
            if self._proc is not None:
                try:
                    for raw in self._consume(self._proc.stdout):
                        if self._stop.is_set():
                            break
                        if not self._accept(raw):
                            continue
                        self._f.write(raw if raw.endswith(b"\n") else raw + b"\n")
                        self._f.flush()
                        self.lines += 1
                        if self.on_line:
                            try:
                                self.on_line(raw.decode("utf-8", "replace").rstrip("\r\n"), self.clock())
                            except Exception:
                                pass
                except Exception:
                    pass
                try:
                    self._proc.wait()
                except Exception:
                    pass
            if self._stop.is_set():
                break
            self.restarts += 1
            self._f.write(f"##PERFKIT_LOGCAT_RESTART {self.clock():.3f}\n".encode())
            self._f.flush()
            if self.restarts > self.max_restarts:
                break
            first = False
            self.sleep(self.reconnect_wait)

    @staticmethod
    def _consume(stream) -> Iterable[bytes]:
        if stream is None:
            return []
        return iter(stream.readline, b"")

    def stop(self, wait: float = 5.0) -> None:
        self._stop.set()
        p = self._proc
        if p is not None:
            try:
                p.terminate()
            except Exception:
                pass
        if self._th is not None:
            self._th.join(wait)
        if self._f is not None:
            try:
                self._f.close()
            except Exception:
                pass
