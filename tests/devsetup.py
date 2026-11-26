"""기기 시험용 설정(임시 프로필·사용자 설정·세이브·AVD 폴더)과 가짜 호스트 측정. 값은 시험용 예시다."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from perfkit import config as C
from perfkit.device import host as H

PROFILE = """name = "test-device-profile"

[app]
package = "com.example.game"

[paths]
results = "results"

[devices.emu]
kind = "emulator"
avd = "test-avd"
lease = "none"
serial = "emulator-5554"

[devices.emu.expect]
system_image = "system-images;android-35;google_apis;x86_64"
cores = 4
ram_mb = 4096
gpu_mode = "host"
resolution = "1080x2400"
arm_translation = true

[devices.phone]
kind = "device"
preset = "exynos-mali"
serial = "SERIAL-1"

[devices.phone.cool]
max_ap = 38.0
max_skin = 35.5
max_status = 0
timeout_s = 1.0
poll_s = 0.05

[run]
saves_dir = "saves"
seconds = 5
sample_interval_s = 0.15
probe_wait_s = 1.5
csv_stable_s = 0.2
load_timeout_s = 20

[host]
idle_seconds = 0.05

[compare."L3-E".gate]
host_busy_before_max = 15.0
host_busy_during_max = 25.0
other_unity_pct_max = 1.0
logcat_restarts_max = 0
"""

AVD_CONFIG = """abi.type=x86_64
hw.cpu.ncore=4
hw.gpu.mode=host
hw.lcd.width=1080
hw.lcd.height=2400
hw.lcd.density=420
hw.ramSize=4096
image.sysdir.1=system-images\\android-35\\google_apis\\x86_64\\
"""


class Sandbox:
    """임시 폴더에 프로필·세이브·AVD를 만든다. `with Sandbox() as sb:`"""

    def __init__(self, profile: str = PROFILE, avd: str = AVD_CONFIG):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "profile.toml").write_text(profile, encoding="utf-8")
        (self.root / "user.toml").write_text(f'profile = "{(self.root / "profile.toml").as_posix()}"\n', encoding="utf-8")
        save = self.root / "saves" / "light"
        save.mkdir(parents=True)
        (save / "A.bytes").write_bytes(b"\x01\x02")
        (save / "B.bytes").write_bytes(b"\x03")
        heavy = self.root / "saves" / "heavy"
        heavy.mkdir(parents=True)
        (heavy / "A.bytes").write_bytes(b"\x05")
        avd_dir = self.root / "avd" / "test-avd.avd"
        avd_dir.mkdir(parents=True)
        (avd_dir / "config.ini").write_text(avd, encoding="utf-8")
        self.avd_home = self.root / "avd"
        self.results = self.root / "results"
        self.apk = self.root / "game.apk"
        self.apk.write_bytes(b"PK\x05\x06" + b"\x00" * 18)  # 빈 zip(패키지는 프로필에서)

    def settings(self) -> C.Settings:
        return C.load(self.root / "user.toml", env={})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._tmp.cleanup()


class FakeProbe(H.HostProbe):
    """호스트 CPU 사용률(busy %)을 정해 둔 가짜. editor_pct는 에디터 프로세스 점유 %(전체 코어 기준)."""

    ncpu = 10

    def __init__(self, busy: float = 10.0, editor_pct: float = 0.0, busy_cmd: str | None = None):
        self.busy = busy
        self.editor_pct = editor_pct
        self.busy_cmd = busy_cmd
        self.t0 = time.perf_counter()

    def system_times(self):
        t = (time.perf_counter() - self.t0) * 1e6
        return int(t * (100 - self.busy) / 100), int(t)

    def pids(self, image):
        if image == "Unity.exe" and (self.editor_pct > 0 or self.busy_cmd):
            return [100]
        if image.startswith("qemu"):
            return [200]
        return []

    def proc_time_s(self, pid):
        el = time.perf_counter() - self.t0
        if pid == 100:
            return el * self.ncpu * self.editor_pct / 100
        return el * self.ncpu * 0.12

    def cmdlines(self, image):
        if image == "Unity.exe" and self.busy_cmd:
            return [(100, self.busy_cmd)]
        return []

    def info(self):
        return {"cpu": "Test CPU", "os": "Test OS", "gpu": "Test GPU", "name": "test-host"}


def fast_sleep(x: float) -> None:
    time.sleep(min(x, 0.03))
