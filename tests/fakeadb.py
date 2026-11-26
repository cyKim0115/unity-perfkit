"""가짜 Android 기기(adb). perfkit device·run을 기기 없이 시험한다.

- `FakeDevice.runner(argv, timeout)`·`popen(argv)`를 `perfkit.device.adb.Adb`에 넣는다.
- `am start ... -e unity '<인자>'`를 받으면 표본 logcat(tests/data/device/logcat_probe_run.txt)을 시간을 줄여 다시
  내보내고, END 뒤 기기에 회차 CSV(frames_small.csv)를 만든다. 측정 모드가 없는 APK(`probe=False`)는 아무 줄도
  내지 않는다.
- `drop_after`줄을 내보낸 뒤 logcat 스트림을 한 번 끊는다(재연결 시험).
값은 시험용 예시다.
"""

from __future__ import annotations

import re
import shlex
import threading
import time
from pathlib import Path

from perfkit.device.adb import Result

DATA = Path(__file__).resolve().parent / "data" / "device"
LOG_FIXTURE = DATA / "logcat_probe_run.txt"
CSV_FIXTURE = DATA / "frames_small.csv"
FIXTURE_RUN_ID = "20260101-1200-0a1b2c3d-scene-heavy-1"
FIXTURE_PKG = "com.example.game"

MEMINFO = """Applications Memory Usage (in Kilobytes):
Uptime: 1000 Realtime: 1000

** MEMINFO in pid 4242 [com.example.game] **
                   Pss  Private  Private  SwapPss      Rss     Heap     Heap     Heap
                 Total    Dirty    Clean    Dirty    Total     Size    Alloc     Free
                ------   ------   ------   ------   ------   ------   ------   ------
  Native Heap    51200    51000      100        0    52000    60000    55000     5000
  Dalvik Heap     4096     4000        0        0     5000     8000     4000     4000
        Stack     2048     2048        0        0     2100
       Ashmem       10        0        0        0       20
    Other dev       20        0       20        0      300
     .so mmap    30720     2000    20000        0    50000
    .apk mmap     1024        0      900        0     2000
   EGL mtrack     8192     8192        0        0     8192
    GL mtrack     4096     4096        0        0     4096
      Unknown    20480    20400        0        0    21000
        TOTAL   512000   400000    60000        0   600000    68000    59000     9000

 App Summary
                       Pss(KB)                        Rss(KB)
                        ------                         ------
           Java Heap:     4000                           5000
         Native Heap:    51000                          52000
                Code:    92160                         110000
               Stack:     2048                           2100
            Graphics:    12288                          12288
       Private Other:   300000
              System:    51504
             Unknown:                                    20000

           TOTAL PSS:   512000            TOTAL RSS:   600000      TOTAL SWAP PSS:        0
"""

THERMAL = """IsStatusOverride: false
ThermalEventListeners:
Thermal Status: {status}
Cached temperatures:
Current temperatures from HAL:
	Temperature{{mValue={ap}, mType=0, mName=AP, mStatus=0}}
	Temperature{{mValue={skin}, mType=3, mName=SKIN, mStatus=0}}
	Temperature{{mValue={bat}, mType=2, mName=BAT, mStatus=0}}
Current cooling devices from HAL:
	CoolingDevice{{mValue=0, mType=2, mName=battery}}
"""

BATTERY = """Current Battery Service state:
  AC powered: false
  USB powered: true
  Wireless powered: false
  status: 2
  level: 87
  temperature: 312
"""


class _Stream:
    def __init__(self, dev: "FakeDevice", since: float | None):
        self.dev = dev
        self.pos = 0
        if since is not None:
            with dev.lock:
                self.pos = next((i for i, (t, _) in enumerate(dev.log) if t >= since), len(dev.log))
        self.closed = False
        self.sent = 0

    def readline(self) -> bytes:
        while not self.closed:
            with self.dev.lock:
                if self.dev.drop_after is not None and self.dev.total_sent >= self.dev.drop_after and not self.dev.dropped:
                    self.dev.dropped = True
                    return b""
                if self.pos < len(self.dev.log):
                    line = self.dev.log[self.pos][1]
                    self.pos += 1
                    self.sent += 1
                    self.dev.total_sent += 1
                    return line.encode("utf-8") + b"\n"
            time.sleep(0.01)
        return b""


class _Proc:
    def __init__(self, dev, since):
        self.stdout = _Stream(dev, since)

    def terminate(self):
        self.stdout.closed = True

    def wait(self, timeout=None):
        return 0


class FakeDevice:
    def __init__(self, serial: str = "emulator-5554", *, probe: bool = True, speed: float = 0.02,
                 drop_after: int | None = None, package: str = FIXTURE_PKG, emulator: bool = True,
                 thermal=(0, 35.0, 33.0, 31.2), result: str = "ok", app_dies: bool = False):
        self.serial = serial
        self.probe = probe
        self.speed = speed
        self.drop_after = drop_after
        self.dropped = False
        self.total_sent = 0
        self.package = package
        self.emulator = emulator
        self.thermal = thermal
        self.result = result
        self.app_dies = app_dies
        self.lock = threading.Lock()
        self.log: list[tuple[float, str]] = []
        self.files: dict[str, bytes] = {}
        self.installed: set[str] = set()
        self.pid: int | None = None
        self.ticks = 0
        self.calls: list[list[str]] = []
        self.settings = {"system screen_brightness_mode": "1", "system screen_brightness": "80",
                         "global airplane_mode_on": "0", "global stay_on_while_plugged_in": "0",
                         "system screen_off_timeout": "60000"}
        self.logcat_size = "256K"
        self.props = {"ro.dalvik.vm.native.bridge": "libndk_translation.so" if emulator else "0",
                      "ro.product.cpu.abilist": "x86_64,arm64-v8a" if emulator else "arm64-v8a",
                      "ro.build.fingerprint": "example/sdk/emu:15/TEST/1:userdebug/test-keys",
                      "ro.product.model": "Example", "ro.build.version.release": "15"}
        self._threads: list[threading.Thread] = []

    # ---------------------------------------------------------------- adb 진입점
    def runner(self, argv: list[str], timeout: float) -> Result:
        args = list(argv[1:])
        if args[:1] == ["-s"]:
            args = args[2:]
        self.calls.append(args)
        if not args:
            return Result(1, "", "no args")
        c = args[0]
        if c == "version":
            return Result(0, "Android Debug Bridge version 1.0.41\nVersion 37.0.1-test\n")
        if c == "devices":
            return Result(0, f"List of devices attached\n{self.serial}\tdevice\n\n")
        if c == "start-server":
            return Result(0, "")
        if c == "get-state":
            return Result(0, "device\n")
        if c == "install":
            self.installed.add(self.package)
            return Result(0, "Performing Streamed Install\nSuccess\n")
        if c == "push":
            self.files[args[2]] = Path(args[1]).read_bytes()
            return Result(0, "1 file pushed\n")
        if c == "pull":
            data = self.files.get(self._norm(args[1]))
            if data is None:
                return Result(1, "", f"adb: error: remote object '{args[1]}' does not exist")
            Path(args[2]).write_bytes(data)
            return Result(0, "1 file pulled\n")
        if c == "logcat":
            if "-g" in args:
                return Result(0, f"main: ring buffer is {self.logcat_size[:-1]} KiB (8 KiB consumed)\n")
            if "-G" in args:
                self.logcat_size = args[args.index("-G") + 1]
                return Result(0, "")
            if "-c" in args:
                with self.lock:
                    self.log.clear()
                return Result(0, "")
        if c == "shell":
            return Result(0, self.shell(" ".join(args[1:])))
        return Result(1, "", f"fake adb: unknown {args}")

    def popen(self, argv: list[str]):
        args = list(argv[1:])
        if args[:1] == ["-s"]:
            args = args[2:]
        since = float(args[args.index("-T") + 1]) if "-T" in args else None
        return _Proc(self, since)

    # ---------------------------------------------------------------- shell
    @staticmethod
    def _norm(path: str) -> str:
        return path.replace("/storage/emulated/0/", "/sdcard/")

    def shell(self, cmd: str) -> str:
        out = []
        for part in cmd.split(" ; echo @@PK@@ ; "):
            out.append(self._shell1(part))
        return "@@PK@@\n".join(out) if " ; echo @@PK@@ ; " in cmd else out[0]

    def _shell1(self, cmd: str) -> str:
        cmd = cmd.strip()
        if cmd.startswith("getprop "):
            return self.props.get(cmd.split()[1], "") + "\n"
        if cmd.startswith("pidof "):
            return f"{self.pid}\n" if self.pid and cmd.split()[1] == self.package else ""
        if cmd.startswith("am force-stop"):
            self.pid = None
            return ""
        if cmd.startswith("am start"):
            return self._am_start(cmd)
        if cmd.startswith("stat -c %s"):
            p = self._norm(cmd.split()[3])
            return f"{len(self.files[p])}\n" if p in self.files else ""
        if cmd.startswith("rm -rf"):  # rm -rf X[; mkdir -p X]
            target = self._norm(cmd.split()[2].rstrip(";"))
            for k in [k for k in self.files if k == target or k.startswith(target + "/")]:
                self.files.pop(k)
            return ""
        if cmd.startswith(("rm ", "mkdir")):
            if cmd.startswith("rm -f"):
                for p in cmd.split()[2:]:
                    self.files.pop(self._norm(p.rstrip(";")), None)
            return ""
        if cmd.startswith("mkdir -p") or "; rm -f" in cmd:
            return ""
        if cmd == "getconf CLK_TCK":
            return "100\n"
        if cmd.startswith("wm size"):
            return "Physical size: 1080x2400\n"
        if cmd.startswith("dumpsys meminfo"):
            return MEMINFO if self.pid else "No process found for: x\n"
        if cmd.startswith("dumpsys thermalservice"):
            st, ap, skin, bat = self.thermal
            return THERMAL.format(status=st, ap=ap, skin=skin, bat=bat)
        if cmd.startswith("dumpsys battery"):
            return BATTERY
        if cmd.startswith("dumpsys power"):
            return "  mWakefulness=Awake\n"
        if cmd.startswith("dumpsys SurfaceFlinger"):
            return "SurfaceFlinger TimeStats\n"
        if cmd.startswith("settings get"):
            _, _, ns, name = cmd.split()[:4]
            return self.settings.get(f"{ns} {name}", "null") + "\n"
        if cmd.startswith("settings put"):
            _, _, ns, name, val = cmd.split()[:5]
            self.settings[f"{ns} {name}"] = val
            return ""
        if cmd.startswith("settings delete"):
            _, _, ns, name = cmd.split()[:4]
            self.settings.pop(f"{ns} {name}", None)
            return ""
        if cmd.startswith("cmd connectivity airplane-mode"):
            self.settings["global airplane_mode_on"] = "1" if "enable" in cmd else "0"
            return ""
        if cmd.startswith("svc power stayon"):
            self.settings["global stay_on_while_plugged_in"] = "7" if "true" in cmd else "0"
            return ""
        if cmd.startswith(("cmd game", "cmd power", "am broadcast")):
            return ""
        if cmd.startswith("cat /proc/") and cmd.endswith("/stat") and "task" not in cmd:
            return self._proc_stat()
        if cmd.startswith("head -1 /proc/stat"):
            t = self.ticks
            return f"cpu  {t * 3} 0 {t} {t * 2} 0 0 0 0 0 0\n"
        if cmd.startswith("cat /proc/loadavg"):
            return "3.50 3.20 3.00 2/900 1234\n"
        if cmd.startswith("grep -E '^(VmRSS"):
            return ("VmHWM:\t 1300000 kB\nVmRSS:\t 1200000 kB\nRssAnon:\t  900000 kB\nRssFile:\t  280000 kB\n"
                    "VmSwap:\t       0 kB\nThreads:\t120\n") if self.pid else ""
        if cmd.startswith("cat /proc/") and "/task/" in cmd:
            if not self.pid:
                return ""
            t = self.ticks
            return (f"{self.pid} (UnityMain) S 1 2 3 4 5 6 7 8 9 10 {t * 55 // 100} 0 0\n"
                    f"{self.pid + 1} (UnityGfxDeviceW) S 1 2 3 4 5 6 7 8 9 10 {t * 75 // 100} 0 0\n"
                    f"{self.pid + 2} (Job.Worker 0) S 1 2 3 4 5 6 7 8 9 10 {t * 13 // 100} 0 0\n"
                    f"{self.pid + 3} (Binder:4242_1) S 1 2 3 4 5 6 7 8 9 10 {t * 2 // 100} 0 0\n")
        if cmd.startswith("cat /sys/") or "cat /sys/" in cmd:
            n = cmd.count("cat /sys/")
            if "gpu_clock" in cmd:
                return "585\n"
            if "gpu_busy" in cmd:
                return "40\n"
            if "gpu_max_clock" in cmd:
                return "858\n"
            return "\n".join(["1800000"] * n) + "\n"
        if cmd == "echo" or cmd == "echo -":
            return "\n"
        if cmd.startswith("pm path"):
            return f"package:/data/app/x/base.apk\n" if self.package in self.installed else ""
        if cmd.startswith("cat ") or cmd.startswith("ls "):
            return ""
        return ""

    def _proc_stat(self) -> str:
        if not self.pid:
            return "cat: /proc/x/stat: No such file or directory\n"
        self.ticks += 80  # 샘플마다 틱 증가(점유율 계산용)
        return f"{self.pid} (com.example.game) S 1 2 3 4 5 6 7 8 9 10 {self.ticks} {self.ticks // 4} 0 0\n"

    def _am_start(self, cmd: str) -> str:
        parts = shlex.split(cmd)
        run_id = None
        if "-e" in parts:
            uargs = parts[parts.index("-e") + 2]
            m = re.search(r"-perfRunId (\S+)", uargs)
            run_id = m.group(1) if m else None
        self.pid = 4242
        th = threading.Thread(target=self._emit, args=(run_id,), daemon=True)
        th.start()
        self._threads.append(th)
        return "Starting: Intent { cmp=x }\nStatus: ok\nLaunchState: COLD\nActivity: x\nTotalTime: 812\nWaitTime: 815\nComplete\n"

    def _emit(self, run_id: str | None) -> None:
        lines = LOG_FIXTURE.read_text(encoding="utf-8").splitlines()
        if not self.probe:
            lines = [l for l in lines if "[PerfRun]" not in l and "[PerfStats]" not in l]
        t_first = float(re.match(r"\s*(\d+\.\d+)", lines[0]).group(1))
        base = time.time()
        for raw in lines:
            if self.pid is None:
                return
            m = re.match(r"(\s*)(\d+\.\d+)(.*)", raw)
            t = base + (float(m.group(2)) - t_first) * self.speed
            while time.time() < t:
                time.sleep(0.005)
            line = f"{m.group(1)}{t:.3f}{m.group(3)}".replace(FIXTURE_RUN_ID, run_id or FIXTURE_RUN_ID)
            line = line.replace(" 21203 ", f" {self.pid} ").replace("proc 21203:", f"proc {self.pid}:")
            if "[PerfRun] END" in line and self.result != "ok":
                line = line.replace("result=ok", f"result={self.result}").replace('"result":"ok"', f'"result":"{self.result}"')
            if self.app_dies and "PHASE name=pan" in line:
                with self.lock:
                    self.log.append((t, line))
                self.pid = None
                return
            with self.lock:
                self.log.append((t, line))
            if "[PerfRun] END" in line and run_id:
                csv = CSV_FIXTURE.read_bytes()
                self.files[f"/sdcard/Android/data/{self.package}/files/perf/{run_id}.csv"] = csv
