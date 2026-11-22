"""core/metrics_worker.py — the slow numbers of the machine, asked in another process.

WHAT THIS IS
    The GPU load and the temperature of the processor. Both are slow to
    read and both are read rarely: every six seconds, in the background,
    while the interface is doing something else.

WHY A PROCESS OF ITS OWN
    The temperature comes from WMI, and WMI is COM: DCOM, an apartment per
    thread, and native threads of the runtime that the operating system —
    not Python — owns. It was measured on this machine: one WMI question
    adds THREE native threads to the process, and letting go of the objects
    it answered with prints, seven times over:

        Win32 exception occurred releasing IUnknown at 0x000001C21F5A0680

    That is the sound of a COM object being released by a thread that
    does not own it, which is the same family of accident as the
    «Windows fatal exception: code 0x80010108» that used to end the
    program when it was shutting down.

    So the questions are asked HERE, in a process that exists only to ask
    them, and the answers come back as a line of text. The interface
    never opens an apartment, never loads WMI, and never has to release
    anything that belongs to another thread. If the machine is slow to
    answer, the worst that happens is that the meter is late.

THE CONTRACT
    One line of JSON per reading, on stdout:

        {"t": 41.7, "g": 12.5}

    `t` is the temperature in degrees Celsius and `g` the GPU load in
    percent. -1 means "this machine does not say", which is an answer and
    not a failure: the interface shows N/A and moves on.

    The worker leaves when the process that started it is gone, so a
    program that is closed does not leave a reader of WMI behind.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import warnings

# The platform, said here and not imported: `core/` does not reach into
# `gui/`, and this file is a process of its own.
_OS = "Windows"

# How often the machine is asked. Six seconds: slow enough not to be a
# burden, fast enough that the meter is alive.
_PERIOD = 6.0

# The GPU provider enumerates every adapter and every engine the first
# time it is asked, and that enumeration costs eleven seconds. Doing it
# here, before the first answer, is what makes the first number arrive
# in time instead of a quarter of a minute later.
_WARMUP_DELAY = 3.0


# ── COM: one apartment per thread ───────────────────────────────────────────

_com_state = threading.local()


def _com_ready() -> bool:
    """Prepares the calling thread to talk to WMI.

    WMI is COM, and COM belongs to a thread: a query sent from a thread
    that has not initialised it comes back as a syntax error, which the
    `wmi` package says out loud in its own words ("you are probably
    running inside a thread without first calling
    pythoncom.CoInitialize[Ex]").

    THE APARTMENT IS SHARED (MTA), AND THE REASON IS WORTH A PARAGRAPH.
        A thread that is single-threaded (STA) needs a message pump for
        the calls that cross apartments — and WMI crosses apartments: it
        is DCOM, and the answer comes from the WMI service. A worker
        thread with an STA and no pump gets «RPC_E_INVALID_HANDLE», and
        that error does not stay in the thread: it ends the whole
        process with «Windows fatal exception: code 0x80010108». It
        happened: the suite finished fine and died on the way out.

        A shared apartment (MTA) does not need a pump: the calls are
        marshalled by the runtime and answered by whichever thread is
        free. That is exactly what a worker thread that only asks
        questions wants.

    It is called once per thread: CoInitializeEx has to be matched by
    CoUninitialize, and this way it is matched exactly once.
    """
    if getattr(_com_state, "ready", False):
        return True
    try:
        import pythoncom
    except Exception:
        return False
    try:
        try:
            pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
        except AttributeError:
            pythoncom.CoInitialize()
        _com_state.ready = True
        return True
    except Exception as exc:
        # RPC_E_CHANGED_MODE: COM is already open on this thread, but in
        # another model. That is not a problem — it is already ready.
        if getattr(exc, "args", [None])[0] == -2147417850:
            _com_state.ready = True
            return True
        try:
            pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
            _com_state.ready = True
            return True
        except Exception:
            return False


def _release_thread_com() -> None:
    """Let go of the COM of THIS thread: its connections and its apartment."""
    try:
        import pythoncom

        pythoncom.CoUninitialize()
    except Exception:
        pass
    try:
        _com_state.ready = False
    except Exception:
        pass


# ── the packages, loaded once and quietly ───────────────────────────────────

_pynvml_mod = None
_wmi_mod = None


def _load_pynvml():
    """The NVML module, loaded once and quietly.

    Two reasons for the quiet: the package prints a deprecation warning
    that has nothing to do with the user, and — the real one — it is
    loaded HERE, in the thread that owns the program, and not in the
    thread that reads the hardware. A daemon thread that prints while the
    interpreter is shutting down takes it down with it: "Fatal Python
    error: _enter_buffered_busy: could not acquire lock for <stderr>".
    """
    global _pynvml_mod
    if _pynvml_mod is not None:
        return _pynvml_mod
    try:
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import pynvml  # type: ignore
        _pynvml_mod = pynvml
    except Exception:
        _pynvml_mod = None
    return _pynvml_mod


def _load_wmi():
    """The WMI module, loaded once and quietly, in the main thread.

    Same reason as the NVML one: nothing that runs in a daemon thread
    should import or print while the interpreter is shutting down.
    """
    global _wmi_mod
    if _wmi_mod is not None:
        return _wmi_mod
    try:
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import wmi  # type: ignore
        _wmi_mod = wmi
    except Exception:
        _wmi_mod = None
    return _wmi_mod


# ── Windows GPU via NVML DLL (no subprocess, no console window) ──────────────
_nvml_lib: object = None   # cached ctypes DLL
_nvml_ok: object = None    # None=untested, True=works, False=unavailable


def _nvml_gpu_windows() -> float:
    """Return NVIDIA GPU utilisation % using nvml.dll directly — zero subprocess."""
    global _nvml_lib, _nvml_ok
    if _nvml_ok is False:
        return -1.0
    try:
        import ctypes

        class _Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        if _nvml_lib is None:
            for dll_name in ("nvml", r"C:\Windows\System32\nvml.dll"):
                try:
                    lib = ctypes.WinDLL(dll_name)
                    lib.nvmlInit_v2()
                    _nvml_lib = lib
                    break
                except Exception:
                    continue

        if _nvml_lib is None:
            pynvml = _load_pynvml()
            if pynvml is None:
                raise RuntimeError("pynvml is not here")
            pynvml.nvmlInit()
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            _nvml_ok = True
            return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)

        dev = ctypes.c_void_p()
        _nvml_lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
        util = _Util()
        _nvml_lib.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(util))
        _nvml_ok = True
        return float(util.gpu)
    except Exception:
        _nvml_ok = False
        return -1.0


# ── Windows GPU by performance counters (works with any card) ───────────────

def _gpu_windows_counters(conn) -> float:
    """The GPU load from the performance counters of Windows.

    NVML is NVIDIA's library and it only knows about NVIDIA cards: on a
    machine with an AMD or an Intel card it can only ever say "there is
    no NVML here". Windows has had its own GPU counters since Windows 10
    and they work with whatever card is in the machine.

    The counters give one row per process and per engine, so the answer is
    the sum of the 3D engine of everybody, capped at a hundred.

    The query asks for the 3D engines ONLY: the whole table is 288 rows
    and takes 7,1 seconds, while the 3D rows alone are 96 and take 1,8.

    AND THE PROVIDER IS WARMED UP FIRST. The first question about the
    GPU engines in a process costs eleven seconds: Windows enumerates
    every adapter and every engine before it answers, and that work is
    paid once. Asking about the adapters first — two rows, a tenth of a
    second — does it, and the same question then takes two and a half.
    Measured in the same process: 11 s before the warm-up, 2,5 s after.
    """
    try:
        conn.Win32_PerfFormattedData_GPUPerformanceCounters_GPUAdapterMemory()
    except Exception:
        pass
    total = 0
    try:
        rows = conn.query(
            "SELECT Name, UtilizationPercentage FROM "
            "Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine "
            "WHERE Name LIKE '%engtype_3D%'"
        )
    except Exception:
        rows = conn.Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine()
    for row in rows:
        name = str(row.Name)
        if "engtype_3D" not in name:
            continue
        try:
            value = int(row.UtilizationPercentage or 0)
        except Exception:
            continue
        total += value
    return min(100.0, float(total))


def _warm_gpu_provider() -> None:
    """Ask Windows to enumerate the adapters of this machine, once.

    The GPU counters are slow the FIRST time and fast after that, because
    the provider enumerates every adapter and every engine when it is first
    asked. Doing that enumeration here — in the background, while the
    program is still coming up — is the difference between a panel that
    shows the GPU load within seconds and one that says "..." for a quarter
    of a minute.
    """
    try:
        wmi = _load_wmi()
        if wmi is None:
            return
        conn = wmi.WMI(namespace="root/cimv2")
        conn.Win32_PerfFormattedData_GPUPerformanceCounters_GPUAdapterMemory()
    except Exception:
        pass


def _temp_windows(conn) -> float:
    """The temperature of the machine, in degrees Celsius.

    The thermal zone is what the firmware exposes, and on a laptop it is
    usually the zone of the processor. `root/wmi` asks for it the old way
    and is denied on a lot of machines; `root/cimv2` carries the same
    number in the performance counters and is allowed everywhere.
    """
    for row in conn.Win32_PerfFormattedData_Counters_ThermalZoneInformation():
        try:
            kelvin = float(row.Temperature)
        except Exception:
            continue
        if kelvin > 0:
            return kelvin - 273.15
    return -1.0


# ── the probes ──────────────────────────────────────────────────────────────

class _Probes:
    """The slow numbers, with the state that says whether to keep asking.

    Everything COM lives here, in a process that is allowed to have an
    apartment: the connections are one per thread, and the thread that
    opens them is the thread that closes them.
    """

    def __init__(self):
        self._pynvml = None
        self._pynvml_h = None
        self._pynvml_ok = None       # None=untested, False=unavailable here
        self._wmi_conn = None        # cached WMI connection (creating one is slow)
        self._wmi_ok = None          # None=untested, False=unavailable here
        self._wmi_local = threading.local()    # the root/wmi connection, per thread
        self._cimv2_local = threading.local()  # the cimv2 connection, per thread
        self._counters_ok = None      # None=untested, False=unavailable here
        self._counters_fail = 0       # consecutive failures of the counters
        self._wmi_fail = 0            # consecutive failures of the thermal zone
        self._gpu_state = "pending"
        self._tmp_state = "pending"

    # -- the connections -----------------------------------------------------

    def cimv2(self):
        """The connection to root/cimv2, kept open — one per thread.

        Creating a WMI connection is slow and doing it on every poll is
        slower; one connection is enough and it is reused.

        It is one per thread, and not one for the whole program, because
        a COM object belongs to the apartment that created it: a
        connection made in the main thread and used in the one of the
        probes fails.
        """
        conn = getattr(self._cimv2_local, "conn", None)
        if conn is None:
            _com_ready()
            wmi = _load_wmi()
            if wmi is None:
                raise RuntimeError("the wmi package is not here")
            conn = wmi.WMI(namespace="root/cimv2")
            self._cimv2_local.conn = conn
        return conn

    # -- the numbers ---------------------------------------------------------

    def gpu(self) -> float:
        """The GPU load, in percent. -1 when there is nothing to read."""
        # pynvml — subprocess-free; initialise once and reuse the handle.
        # Re-initialising NVML on every poll is slow, so cache it and stop
        # retrying pynvml entirely once it proves unavailable here.
        if self._pynvml_ok is not False:
            try:
                if self._pynvml_h is None:
                    pynvml = _load_pynvml()
                    if pynvml is None:
                        raise RuntimeError("pynvml is not here")
                    pynvml.nvmlInit()
                    self._pynvml = pynvml
                    self._pynvml_h = pynvml.nvmlDeviceGetHandleByIndex(0)
                    self._pynvml_ok = True
                return float(self._pynvml.nvmlDeviceGetUtilizationRates(self._pynvml_h).gpu)
            except Exception:
                self._pynvml_ok = False

        # Windows: nvml.dll via ctypes (already cached in _nvml_gpu_windows)
        if _OS == "Windows":
            value = _nvml_gpu_windows()
            if value >= 0:
                return value

            # Windows: the performance counters. NVML is NVIDIA's, and a
            # machine with any other card can only ever hear "there is no
            # NVML here"; these counters know about every card.
            if self._counters_ok is not False:
                try:
                    value = _gpu_windows_counters(self.cimv2())
                    self._counters_ok = True
                    self._counters_fail = 0
                    self._gpu_state = "ok"
                    return value
                except Exception:
                    # A failure that lasts is a failure that lasts: three in
                    # a row and the counters are given up on. One in a row
                    # is only a hiccup, and giving up after one is how the
                    # GPU ended up saying N/A for ever.
                    self._counters_fail += 1
                    if self._counters_fail >= 3:
                        self._counters_ok = False
                        self._gpu_state = "na"

        return -1.0   # nothing to read — no subprocess at all

    def temp(self) -> float:
        """The temperature of the processor, in degrees Celsius."""
        # psutil — the sensors it can read depend on the driver, so it is a
        # best-effort source and the WMI path below is the fallback.
        try:
            import psutil

            temps = psutil.sensors_temperatures()
            for name in ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                         "cpu-thermal", "zenpower", "it8688"]:
                if name in temps and temps[name]:
                    self._tmp_state = "ok"
                    return float(temps[name][0].current)
            for entries in temps.values():
                if entries:
                    self._tmp_state = "ok"
                    return float(entries[0].current)
        except Exception:
            pass

        # Windows: the thermal zone. The performance counters of cimv2 are
        # allowed everywhere; root/wmi asks for the same number the old
        # way and is denied on a lot of machines.
        if _OS == "Windows":
            try:
                value = _temp_windows(self.cimv2())
                if value > 0:
                    self._tmp_state = "ok"
                    return value
            except Exception:
                pass
            if self._wmi_ok is not False:
                try:
                    conn = getattr(self._wmi_local, "conn", None)
                    if conn is None:
                        wmi = _load_wmi()
                        if wmi is None:
                            raise RuntimeError("the wmi package is not here")
                        conn = wmi.WMI(namespace="root/wmi")
                        self._wmi_local.conn = conn
                    tz = conn.MSAcpi_ThermalZoneTemperature()
                    if tz:
                        self._wmi_fail = 0
                        self._tmp_state = "ok"
                        return (tz[0].CurrentTemperature / 10.0) - 273.15
                except Exception:
                    # Three in a row and the old way is given up on. One in a
                    # row is only a hiccup — and on this machine it was
                    # always "access denied", which is not a hiccup but a
                    # door that stays shut.
                    self._wmi_fail += 1
                    if self._wmi_fail >= 3:
                        self._wmi_ok = False
                    self._wmi_local.conn = None

        return -1.0   # nothing to read — no subprocess at all


# ── the process ─────────────────────────────────────────────────────────────

def _parent_alive(parent_pid: int) -> bool:
    """Is the process that started this one still there?"""
    if parent_pid <= 0:
        return True
    try:
        import psutil

        return psutil.pid_exists(parent_pid)
    except Exception:
        try:
            os.kill(parent_pid, 0)
        except Exception:
            return False
    return True


def read_once(probes: _Probes) -> dict:
    """One reading, as the line of JSON that goes down the pipe."""
    try:
        temp = probes.temp()
    except Exception:
        temp = -1.0
    try:
        gpu = probes.gpu()
    except Exception:
        gpu = -1.0
    return {"t": round(temp, 1), "g": round(gpu, 1)}


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    parent_pid = 0
    if "--parent" in argv:
        try:
            parent_pid = int(argv[argv.index("--parent") + 1])
        except Exception:
            parent_pid = 0

    _com_ready()
    _load_wmi()
    probes = _Probes()

    # The first answer waits a moment: the machine is usually still
    # loading its own libraries when this starts.
    time.sleep(_WARMUP_DELAY)
    _warm_gpu_provider()

    out = sys.stdout
    try:
        while _parent_alive(parent_pid):
            try:
                line = json.dumps(read_once(probes))
            except Exception:
                line = json.dumps({"t": -1.0, "g": -1.0})
            try:
                out.write(line + "\n")
                out.flush()
            except Exception:
                break
            time.sleep(_PERIOD)
    finally:
        _release_thread_com()
    return 0


if __name__ == "__main__":
    sys.exit(main())
