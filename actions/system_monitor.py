"""
System Monitor — background metric checks with voice alert support.

WHAT IT READS
    CPU, RAM, GPU, uptime, process count, the CPU temperature, the three
    processes using the most RAM, the three using the most CPU, and the name
    of the Wi-Fi network this computer is on.

THE RULE THIS FILE FOLLOWS
    A fact that could not be read is reported as UNAVAILABLE, with the reason
    it could not be read. It is never guessed, never copied from a stale
    reading, and never turned into a zero. "0 °C" and "nobody could read the
    temperature" are different facts, and only one of them is true — so a
    reading of zero coming out of a query that failed is refused rather than
    passed on.

    The same holds for the wireless network: "not connected", "this computer
    has no wireless adapter" and "the name could not be read" are three
    different answers, and the user is told which one it is.

READING THE MACHINE
    psutil for the processes, the `wmi` module (pure COM) and, only when
    those fail, `wmic`/PowerShell for the ACPI thermal zone, and `netsh wlan`
    for the wireless network. Every child process runs with its window
    hidden and its output captured — never DEVNULL — and a failure is logged
    rather than swallowed.
"""
import ctypes
import os
import subprocess
import sys
import time

import psutil

_OS = "Windows"

DEFAULT_THRESHOLDS = {
    "cpu":  90.0,
    "ram":  90.0,
    "temp": 85.0,
    "gpu":  95.0,
}

_COOLDOWN   = 300
_CPU_STREAK = 3

# Windows: do not flash a console window at the user for a background read.
_CREATE_NO_WINDOW = 0x08000000

# The temperature sensors psutil may expose, in the order they are trusted.
_SENSOR_NAMES = ("coretemp", "cpu", "k10temp", "cpu_thermal", "acpitz",
                 "cpu-thermal", "zenpower", "it8688")

# The ACPI thermal zone, read through the `wmi` module, then through wmic,
# then through PowerShell. Only one of them is normally needed.
_WMIC_TEMP = ["wmic", r"/namespace:\root\wmi", "PATH",
              "MSAcpi_ThermalZoneTemperature", "GET", "CurrentTemperature"]
_POWERSHELL_TEMP = [
    "powershell", "-NoProfile", "-NonInteractive", "-Command",
    "Get-CimInstance -Namespace root/wmi -ClassName "
    "MSAcpi_ThermalZoneTemperature -ErrorAction Stop | "
    "ForEach-Object { $_.CurrentTemperature }",
]

_NETSH_WLAN = ["netsh", "wlan", "show", "interfaces"]

# ── NVML DLL cache (nvml.dll) ────────────────────────────────────────────────
_nvml_lib: object = None
_nvml_ok:  object = None   # None=untested  True=works  False=unavailable


def _nvml_gpu() -> float:
    """GPU utilisation via NVML — zero subprocess."""
    global _nvml_lib, _nvml_ok
    if _nvml_ok is False:
        return -1.0
    try:
        class _Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        if _nvml_lib is None:
            candidates = ("nvml", r"C:\Windows\System32\nvml.dll")
            _load = ctypes.WinDLL
            for name in candidates:
                try:
                    lib = _load(name)
                    lib.nvmlInit_v2()
                    _nvml_lib = lib
                    break
                except Exception:
                    continue

        if _nvml_lib is None:
            _nvml_ok = False
            return -1.0

        dev = ctypes.c_void_p()
        _nvml_lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
        u = _Util()
        _nvml_lib.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(u))
        _nvml_ok = True
        return float(u.gpu)
    except Exception:
        _nvml_ok = False
        return -1.0


def _get_gpu_usage() -> float:
    # pynvml — subprocess-free, works everywhere if installed
    try:
        import pynvml  # type: ignore
        pynvml.nvmlInit()
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)
    except Exception:
        pass

    return _nvml_gpu()


# ── Running a command without showing it to the user ────────────────────────

def _run_hidden(command: list[str], timeout: float = 12.0) -> str:
    """Run a command, return its output. "" when it could not be run.

    The output is CAPTURED, never sent to DEVNULL: a command that fails has
    to say so somewhere, and AGENTS.md is explicit about it.
    """
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            command, capture_output=True, timeout=timeout, creationflags=flags,
        )
    except FileNotFoundError:
        print(f"[SystemMonitor] '{command[0]}' is not available on this computer.")
        return ""
    except Exception as exc:
        print(f"[SystemMonitor] '{command[0]}' could not be run "
              f"({type(exc).__name__}).")
        return ""
    if completed.returncode != 0:
        print(f"[SystemMonitor] '{command[0]}' finished with code "
              f"{completed.returncode}.")
    return (completed.stdout or b"").decode("utf-8", "replace")


def _last_number(text: str) -> float | None:
    """The last number in a block of output.

    wmic prints a header line and then the value; PowerShell prints only the
    value. Reading from the end covers both without parsing the header.
    """
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            return float(line)
        except ValueError:
            continue
    return None


# ── Temperature ─────────────────────────────────────────────────────────────

def _kelvin_tenths_to_celsius(raw) -> float | None:
    """Convert what the thermal zone reports into degrees Celsius.

    WMI reports tenths of a degree Kelvin: 3231 -> 49.95 °C.

    A raw value of zero — or anything below it — is NOT zero degrees. It is
    the query failing, and a failed query must never reach the user as a
    number, so it is refused here.
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return round((value / 10.0) - 273.15, 2)


def _temp_from_sensors() -> tuple[float | None, str]:
    """psutil's own sensors, when the driver exposes any.

    On Windows psutil usually has none at all, which is not a failure of
    this function — it is the reason the WMI path below exists.
    """
    try:
        temps = psutil.sensors_temperatures()
    except Exception as exc:
        return None, (f"psutil cannot read the temperature sensors "
                      f"({type(exc).__name__})")
    if not temps:
        return None, "psutil reported no temperature sensor"
    for name in _SENSOR_NAMES:
        entries = temps.get(name) or []
        if entries:
            value = _sensor_value(entries[0])
            if value is not None:
                return value, ""
    for entries in temps.values():
        if entries:
            value = _sensor_value(entries[0])
            if value is not None:
                return value, ""
    return None, "psutil reported no readable temperature sensor"


def _sensor_value(entry) -> float | None:
    try:
        current = float(entry.current)
    except Exception:
        return None
    if current <= 0:
        return None
    return round(current, 1)


def _temp_from_wmi() -> tuple[float | None, str]:
    """The ACPI thermal zone, read through WMI.

    Three ways in, because Windows does not always give one: the `wmi`
    module (no subprocess), then wmic, then PowerShell. The first that
    produces a real value wins.
    """
    reasons: list[str] = []
    try:
        import wmi  # type: ignore
        zones = wmi.WMI(namespace="root/wmi").MSAcpi_ThermalZoneTemperature()
        for zone in zones:
            celsius = _kelvin_tenths_to_celsius(
                getattr(zone, "CurrentTemperature", None))
            if celsius is not None:
                return celsius, ""
        reasons.append("the thermal zone reported no usable reading")
    except Exception as exc:
        reasons.append(f"the WMI thermal zone query failed ({type(exc).__name__})")

    for command in (_WMIC_TEMP, _POWERSHELL_TEMP):
        raw = _last_number(_run_hidden(command))
        celsius = _kelvin_tenths_to_celsius(raw)
        if celsius is not None:
            return celsius, ""
        reasons.append(f"'{command[0]}' returned no usable reading")

    return None, "; ".join(reasons)


def _read_cpu_temp() -> tuple[float | None, str]:
    """(celsius, reason). The reason is "" when the value was really read."""
    value, sensor_reason = _temp_from_sensors()
    if value is not None:
        return value, ""
    value, wmi_reason = _temp_from_wmi()
    if value is not None:
        return value, ""
    return None, (wmi_reason or sensor_reason)


def _get_cpu_temp() -> float | None:
    """The CPU temperature in degrees Celsius, or None when nobody can say.

    None means "not readable", which is a different fact from 0 °C. The
    callers below check for it explicitly.
    """
    value, _reason = _read_cpu_temp()
    return value


# ── The processes using the most memory and the most CPU ────────────────────

def _own_process_names() -> set[str]:
    """The name of the executable this assistant is running under."""
    names: set[str] = set()
    try:
        exe = os.path.basename(sys.executable or "")
        if exe:
            names.add(exe.lower())
    except Exception:
        pass
    return names


def _is_own_process(pid: int, name: str, own_names: set[str]) -> bool:
    """Is this the assistant itself?

    The user is not interested in the assistant eating its own memory, so
    this process and the one that started it are left out of the list.
    """
    try:
        if pid in (os.getpid(), os.getppid()):
            return True
    except Exception:
        pass
    return (name or "").lower() in own_names


# Windows keeps two pseudo-processes that are not programs at all.
#
# "System Idle Process" is the accounting entry for CPU that was NOT used:
# on a machine sitting at 6% load it is always at the top of a CPU list,
# because it is the sum of the idle time of every core. Telling the user
# that the idle process is the program using the most CPU is technically
# true and practically useless — it is what they already knew.
#
# "System" is the kernel, and the kernel's own time is not a program the
# user can close.
_PSEUDO_PROCESSES = {"system idle process", "system", "idle", "registry",
                     "memory compression", "memcompression"}


def _is_pseudo_process(name: str) -> bool:
    return (name or "").strip().lower() in _PSEUDO_PROCESSES


def _process_snapshot() -> tuple[list[list], str]:
    """Every readable process, as [process, name, rss_bytes, cpu_percent].

    A process can die between the listing and the query, and some of them
    are protected, so every access is guarded: a dead or unreadable process
    is skipped, never raised.

    `cpu_percent` without an interval returns the value since the last call,
    and the FIRST call of a process always reads 0.0 — so when every process
    reports zero, the sample is taken again after a short pause.
    """
    own = _own_process_names()
    rows: list[list] = []
    try:
        processes = psutil.process_iter()
    except Exception as exc:
        return [], f"the process list could not be read ({type(exc).__name__})"

    for proc in processes:
        try:
            name = proc.name()
            rss = int(proc.memory_info().rss)
            cpu = float(proc.cpu_percent(interval=None))
            pid = int(proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        except Exception:
            continue
        if _is_own_process(pid, name, own):
            continue
        if _is_pseudo_process(name):
            continue
        rows.append([proc, name, rss, cpu])

    if not rows:
        return [], "no process could be read"

    if all(row[3] == 0.0 for row in rows):
        time.sleep(0.4)
        for row in rows:
            try:
                row[3] = float(row[0].cpu_percent(interval=None))
            except Exception:
                row[3] = 0.0

    return rows, ""


def _as_process_entry(row: list) -> dict:
    """One process, in the shape the tool reports it."""
    return {
        "name":        str(row[1]),
        "memory_mb":   round(float(row[2]) / (1024 ** 2), 1),
        "cpu_percent": round(float(row[3]), 1),
    }


def _dedupe_by_name(rows: list[list]) -> list[list]:
    """One row per PROGRAM, keeping the heaviest of each.

    Windows runs several instances of the same program — svchost.exe above
    all — and the user asked which PROGRAMS are heavy, not which processes.
    Three lines that all say svchost.exe are one answer repeated three times.
    """
    seen: dict[str, list] = {}
    for row in rows:
        name = str(row[1] if len(row) > 1 else "").strip().lower()
        if name not in seen:
            seen[name] = row
    return list(seen.values())


def _top_ram_processes(limit: int = 3,
                       snapshot: list[list] | None = None) -> list[dict]:
    """The `limit` programs using the most memory."""
    rows = _process_snapshot()[0] if snapshot is None else snapshot
    ordered = sorted(rows, key=lambda row: row[2], reverse=True)
    return [_as_process_entry(row) for row in _dedupe_by_name(ordered)[:limit]]


def _top_cpu_processes(limit: int = 3,
                       snapshot: list[list] | None = None) -> list[dict]:
    """The `limit` programs using the most CPU."""
    rows = _process_snapshot()[0] if snapshot is None else snapshot
    ordered = sorted(rows, key=lambda row: row[3], reverse=True)
    return [_as_process_entry(row) for row in _dedupe_by_name(ordered)[:limit]]


# ── The wireless network this computer is on ───────────────────────────────

def _parse_wifi_ssid(output: str) -> tuple[str | None, str]:
    """(ssid, reason) out of the output of `netsh wlan show interfaces`.

    The SSID is the NAME of the network; the BSSID is the MAC address of
    the access point, and the two are different things. The BSSID line's
    label contains "SSID" inside it, so it is excluded on purpose — a
    network called "90:f6:52:d7:6b:ce" would be nonsense.

    The label is matched by structure rather than by language, because
    Windows answers in the language of the machine it runs on.
    """
    if not (output or "").strip():
        return None, "netsh said nothing about the wireless adapter"

    low = output.lower()
    if _has_no_adapter(low):
        return None, "this computer has no wireless adapter"
    if _is_disconnected(low):
        return None, "this computer is not connected to a wireless network"

    for line in output.splitlines():
        label, separator, value = line.partition(":")
        if not separator:
            continue
        label = label.strip().upper()
        value = value.strip()
        if not value:
            continue
        if "BSSID" in label:
            continue          # the access point's address, not the network name
        if "SSID" in label:
            return value, ""

    return None, "netsh did not report the name of a wireless network"


def _has_no_adapter(low: str) -> bool:
    """Does the output say there is no wireless adapter at all?"""
    if "no wireless interface" in low:
        return True
    if "wireless" in low and ("no hay" in low or "no existe" in low):
        return True
    return False


def _is_disconnected(low: str) -> bool:
    """Is the adapter there but not connected?

    "desconectado" contains "conectado", so the long word is checked and
    the short one is not — otherwise a connected machine would look
    disconnected on a Spanish Windows.
    """
    return ("disconnected" in low) or ("desconectado" in low)


def _read_wifi_ssid() -> tuple[str | None, str]:
    """(ssid, reason). The reason is "" when the name was really read."""
    output = _run_hidden(_NETSH_WLAN)
    if not output.strip():
        return None, "netsh wlan is not available on this computer"
    return _parse_wifi_ssid(output)


# ── The tool ────────────────────────────────────────────────────────────────

def get_system_status() -> dict:
    """Snapshot of current system metrics for the system_status tool.

    Every fact is either a value that was really read, or None together
    with the reason it could not be read. Nothing is guessed, and the
    `unavailable` list says which facts are missing so the model can tell
    the user what it does not know.
    """
    cpu  = psutil.cpu_percent(interval=0.2)
    ram  = psutil.virtual_memory()
    temp, temp_reason = _read_cpu_temp()
    gpu  = _get_gpu_usage()

    boot_time   = psutil.boot_time()
    uptime_secs = time.time() - boot_time
    uptime_h    = int(uptime_secs // 3600)
    uptime_m    = int((uptime_secs % 3600) // 60)

    snapshot, procs_reason = _process_snapshot()
    top_ram = _top_ram_processes(3, snapshot)
    top_cpu = _top_cpu_processes(3, snapshot)
    if procs_reason:
        top_ram = []
        top_cpu = []

    ssid, wifi_reason = _read_wifi_ssid()

    unavailable: list[str] = []
    if temp is None:
        unavailable.append("temperature")
    if procs_reason:
        unavailable.extend(["top_cpu_processes", "top_ram_processes"])
    if ssid is None:
        unavailable.append("wifi_ssid")

    return {
        "status":  "done",
        "verified": True,
        "action":  "system_status",
        "cpu_percent":   round(cpu, 1),
        "ram_percent":   round(ram.percent, 1),
        "ram_used_gb":   round(ram.used   / 1024 ** 3, 1),
        "ram_total_gb":  round(ram.total  / 1024 ** 3, 1),
        "cpu_temp_c":    round(temp, 1) if temp is not None else None,
        "temperature_c": round(temp, 1) if temp is not None else None,
        "temperature_note": temp_reason,
        "gpu_percent":   round(gpu,  1) if gpu  >= 0 else None,
        "uptime":        f"{uptime_h}h {uptime_m}m",
        "process_count": len(psutil.pids()),
        "top_cpu_processes": top_cpu,
        "top_cpu_note":      procs_reason,
        "top_ram_processes": top_ram,
        "top_ram_note":      procs_reason,
        "wifi_ssid":     ssid,
        "wifi_note":    wifi_reason,
        "unavailable":   unavailable,
    }


# The description the MODEL reads before it decides to call this tool.
#
# main.py declares this tool inline — its result is woven into the live
# session — so the text there is the one the model actually sees. This
# constant is the truth about what the tool reports; main.py should use
# it, because a description that does not mention the temperature, the top
# processes or the wireless network is a description that keeps the model
# from asking for them.
TOOL_DESCRIPTION = (
    "Returns real-time system metrics: CPU usage, RAM, GPU load, uptime and "
    "process count, plus the CPU temperature, the three processes using the "
    "most RAM, the three processes using the most CPU, and the name of the "
    "Wi-Fi network this computer is connected to. Use it when the user asks "
    "how the computer is doing, how hot it is, which programs are eating "
    "memory or CPU, or which Wi-Fi network it is on. A fact that could not "
    "be read is reported as unavailable with a reason — never guessed."
)


class SystemMonitor:
    """
    Stateful monitor — cooldown state persists across session reconnections.
    Call check() periodically; returns a [SYSTEM_ALERT] string or None.
    """

    def __init__(self, thresholds: dict | None = None):
        self.thresholds   = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
        self._last_alert: dict[str, float] = {}
        self._cpu_streak  = 0

    def _can_alert(self, key: str) -> bool:
        return (time.monotonic() - self._last_alert.get(key, 0)) > _COOLDOWN

    def _record(self, key: str):
        self._last_alert[key] = time.monotonic()

    def check(self) -> str | None:
        try:
            cpu  = psutil.cpu_percent(interval=None)
            ram  = psutil.virtual_memory().percent
            temp = _get_cpu_temp()
            gpu  = _get_gpu_usage()
        except Exception:
            return None

        alerts: list[str] = []

        if cpu >= self.thresholds["cpu"]:
            self._cpu_streak += 1
            if self._cpu_streak >= _CPU_STREAK and self._can_alert("cpu"):
                alerts.append(
                    f"[SYSTEM_ALERT] CPU usage has been critically high ({cpu:.0f}%) "
                    "for several seconds. Warn the user in their language and suggest "
                    "closing heavy applications."
                )
                self._record("cpu")
                self._cpu_streak = 0
        else:
            self._cpu_streak = 0

        if ram >= self.thresholds["ram"] and self._can_alert("ram"):
            alerts.append(
                f"[SYSTEM_ALERT] RAM is at {ram:.0f}% — nearly exhausted. "
                "Warn the user in their language and suggest freeing memory."
            )
            self._record("ram")

        if (temp is not None and temp > 0
                and temp >= self.thresholds["temp"] and self._can_alert("temp")):
            alerts.append(
                f"[SYSTEM_ALERT] CPU temperature is {temp:.0f}°C — above the safe limit. "
                "Warn the user in their language and advise reducing system load "
                "or checking cooling."
            )
            self._record("temp")

        if gpu >= 0 and gpu >= self.thresholds["gpu"] and self._can_alert("gpu"):
            alerts.append(
                f"[SYSTEM_ALERT] GPU load is at {gpu:.0f}%. "
                "Briefly inform the user in their language."
            )
            self._record("gpu")

        return " ".join(alerts) if alerts else None
