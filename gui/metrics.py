"""gui/metrics.py — System telemetry for the HUD: CPU, memory, network, GPU and temperature.

Split out of ui.py in the previous chassis; behaviour unchanged."""
from __future__ import annotations

import json
import math
import os
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

from PyQt6.QtCore import (
    QEasingCurve, QLineF, QMimeData, QObject, QParallelAnimationGroup, QPointF,
    QPoint, QPropertyAnimation, QRect, QRectF, QSize, QSizeF, Qt, QTimer,
    QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QConicalGradient, QDragEnterEvent, QDropEvent, QFont,
    QFontDatabase, QKeySequence, QLinearGradient, QPainter, QPainterPath,
    QPen, QPixmap, QRadialGradient, QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QGraphicsScene, QGraphicsView, QGraphicsPixmapItem,
    QStackedWidget, QTextEdit, QVBoxLayout, QWidget, QProgressBar, QSlider,
)

from gui.theme import (
    _OS,
)


# ── COM: one apartment per thread ───────────────────────────────────────────

_com_state = threading.local()


def _com_ready() -> bool:
    """Prepares the calling thread to talk to WMI.

    WMI is COM, and COM belongs to a thread: a query sent from a thread
    that has not initialised it comes back as a syntax error, which the
    `wmi` package says out loud in its own words ("you are probably
    running inside a thread without first calling
    pythoncom.CoInitialize[Ex]").

    That is why the GPU and the temperature said N/A in this application:
    they are read in a thread of their own, and that thread had never
    opened its COM apartment. The main thread worked, which is why the
    same query succeeded when it was made by hand and failed inside the
    program.

    It is called once per thread: CoInitialize has to be matched by
    CoUninitialize, and this way it is matched exactly once.
    """
    if getattr(_com_state, "ready", False):
        return True
    try:
        import pythoncom
    except Exception:
        return False
    try:
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


# ── Windows GPU via NVML DLL (no subprocess, no console window) ──────────────
_nvml_lib: object = None   # cached ctypes DLL
_nvml_ok:  object = None   # None=untested, True=works, False=unavailable


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
            import pynvml  # type: ignore
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

    It is not cheap — about five seconds — which is why it runs in its own
    thread and not in the one that reads the processor.
    """
    total = 0
    for row in conn.Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine():
        name = str(row.Name)
        if "engtype_3D" not in name:
            continue
        try:
            value = int(row.UtilizationPercentage or 0)
        except Exception:
            continue
        total += value
    return min(100.0, float(total))


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


class _SysMetrics:
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = 0.0   
        self.gpu  = -1.0  
        self.tmp  = -1.0  
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._running = True
        # Probe caches — GPU (NVML) and temperature (WMI) are the expensive
        # queries; initialise their handles once and reuse them instead of
        # rebuilding a connection on every poll.
        self._slow_tick = 0            # gpu/temp refreshed every 3rd cycle
        self._pynvml    = None         # cached pynvml module + device handle
        self._pynvml_h  = None
        self._pynvml_ok = None         # None=untested, False=unavailable here
        self._wmi_conn  = None         # cached WMI connection (creating one is slow)
        self._wmi_ok    = None         # None=untested, False=unavailable here
        self._wmi_local = threading.local()   # the root/wmi connection, per thread
        self._cimv2_local = threading.local()   # the cimv2 connection, per thread
        self._counters_ok = None       # None=untested, False=unavailable here
        self._counters_fail = 0        # consecutive failures of the counters
        self._wmi_fail   = 0           # consecutive failures of the thermal zone
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()
        # THE SLOW PROBES GET THEIR OWN THREAD.
        #
        # The GPU counters take about five seconds to answer, and the
        # thermal zone needs WMI. Asking for those inside the thread that
        # reads the processor every two seconds means the processor
        # reading stops for five seconds every half minute — and a meter
        # that freezes while another one moves is a meter nobody trusts.
        t_slow = threading.Thread(target=self._slow_loop, daemon=True)
        t_slow.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(2.0)

    def _read_slow(self):
        """The GPU and the temperature, each one on its own.

        Each is asked separately: a GPU counter that throws must not cost
        the machine its temperature.
        """
        try:
            gpu = self._get_gpu()
        except Exception:
            gpu = -1.0
        try:
            tmp = self._get_temp()
        except Exception:
            tmp = -1.0
        with self._lock:
            self.gpu = gpu
            self.tmp = tmp
        return gpu, tmp

    def _slow_loop(self):
        """The GPU and the temperature, on their own clock."""
        # This thread talks to WMI, and WMI is COM, and COM belongs to a
        # thread. Without this the queries come back as a syntax error and
        # both meters say N/A for ever.
        _com_ready()
        while self._running:
            self._read_slow()
            time.sleep(6.0)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        nc  = psutil.net_io_counters()
        now = time.time()
        dt  = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net  = (sent + recv) / (1024 * 1024)
        else:
            net = 0.0
        self._last_net   = nc
        self._last_net_t = now

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net

    def _get_gpu(self) -> float:
        # pynvml — subprocess-free; initialise once and reuse the handle.
        # Re-initialising NVML on every poll is slow, so cache it and stop
        # retrying pynvml entirely once it proves unavailable here.
        if self._pynvml_ok is not False:
            try:
                if self._pynvml_h is None:
                    import pynvml  # type: ignore
                    pynvml.nvmlInit()
                    self._pynvml    = pynvml
                    self._pynvml_h  = pynvml.nvmlDeviceGetHandleByIndex(0)
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
                    value = _gpu_windows_counters(self._cimv2())
                    self._counters_ok = True
                    self._counters_fail = 0
                    return value
                except Exception:
                    # A failure that lasts is a failure that lasts: three in
                    # a row and the counters are given up on. One in a row
                    # is only a hiccup, and giving up after one is how the
                    # GPU ended up saying N/A for ever.
                    self._counters_fail += 1
                    if self._counters_fail >= 3:
                        self._counters_ok = False

        return -1.0   # N/A — no subprocess at all

    def _cimv2(self):
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
            import wmi  # type: ignore
            conn = wmi.WMI(namespace="root/cimv2")
            self._cimv2_local.conn = conn
        return conn

    def _get_temp(self) -> float:
        # psutil — the sensors it can read depend on the driver, so it is a
        # best-effort source and the WMI path below is the fallback.
        try:
            temps = psutil.sensors_temperatures()
            for name in ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                         "cpu-thermal", "zenpower", "it8688"]:
                if name in temps and temps[name]:
                    return temps[name][0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            pass

        # Windows: the thermal zone. The performance counters of cimv2 are
        # allowed everywhere; root/wmi asks for the same number the old
        # way and is denied on a lot of machines.
        if _OS == "Windows":
            try:
                value = _temp_windows(self._cimv2())
                if value > 0:
                    return value
            except Exception:
                pass
            if self._wmi_ok is not False:
                try:
                    conn = getattr(self._wmi_local, "conn", None)
                    if conn is None:
                        import wmi  # type: ignore
                        conn = wmi.WMI(namespace="root/wmi")
                        self._wmi_local.conn = conn
                    tz = conn.MSAcpi_ThermalZoneTemperature()
                    if tz:
                        self._wmi_fail = 0
                        return (tz[0].CurrentTemperature / 10.0) - 273.15
                except Exception:
                    # Three in a row and the old way is given up on. One in a
                    # row is only a hiccup — and on this machine it was
                    # always "access denied", which is not a hiccup but a
                    # door that stays shut.
                    self._wmi_fail += 1
                    if self._wmi_fail >= 3:
                        self._wmi_ok   = False
                    self._wmi_local.conn = None

        return -1.0   # N/A — no subprocess at all

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
            }


_metrics = _SysMetrics()

