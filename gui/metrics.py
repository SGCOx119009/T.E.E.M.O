"""gui/metrics.py — System telemetry for the HUD: CPU, memory, network, GPU and temperature.

Split out of ui.py in the previous chassis; behaviour unchanged."""
from __future__ import annotations

import atexit
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


# ── THE MACHINE IS ASKED IN ANOTHER PROCESS ─────────────────────────────
#
# The GPU load and the temperature come from WMI, and WMI is COM: DCOM, an
# apartment per thread, and native threads that belong to the operating
# system and not to Python. It was measured here: one WMI question adds
# three native threads to the process, and letting go of the objects it
# answered with prints «Win32 exception occurred releasing IUnknown» seven
# times over. That is the sound of a COM object being released by a thread
# that does not own it — the same family of accident as the
# «Windows fatal exception: code 0x80010108» that used to end the program
# when it was shutting down.
#
# So the questions are asked in a process of its own — core/metrics_worker.py
# — and the answers come back as a line of JSON. This file never opens an
# apartment, never loads WMI and never has to release anything that belongs
# to another thread.

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
        # The flag the two threads wait on. An event, and not a sleep, so
        # that a stop is immediate: the program is going away and nobody
        # wants to wait six seconds for a meter that is no longer needed.
        self._stop = threading.Event()
        # The process that asks the machine, and the handle to kill it.
        self._worker = None
        self._cpu_first  = True        # the first reading has no baseline yet
        # Whether a number that is not there is missing because it has not
        # been read yet, or because there is nothing to read.
        self._gpu_state = "pending"
        self._tmp_state = "pending"
        t = threading.Thread(target=self._loop, daemon=True)
        self._t_fast = t
        t.start()
        # THE SLOW NUMBERS ARE ASKED IN A PROCESS OF THEIR OWN.
        #
        # The GPU counters take about five seconds to answer, and the
        # thermal zone needs WMI. Asking for those inside the thread that
        # reads the processor every two seconds means the processor
        # reading stops for five seconds every half minute — and a meter
        # that freezes while another one moves is a meter nobody trusts.
        t_slow = threading.Thread(target=self._slow_loop, daemon=True)
        self._t_slow = t_slow
        t_slow.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            self._stop.wait(2.0)

    def _publish(self, raw_line: str) -> None:
        """One reading of the machine, as the other process wrote it.

        The line is JSON — {"t": 41.7, "g": 12.5} — and a number that is
        -1 is a machine that does not say, which is an answer and not a
        failure: the meter shows N/A and the program carries on.
        """
        try:
            data = json.loads(raw_line)
        except Exception:
            return
        try:
            temp = float(data.get("t", -1.0))
            gpu = float(data.get("g", -1.0))
        except Exception:
            return
        with self._lock:
            self.tmp = temp
            self.gpu = gpu
            # "pending" means it has not been read yet; "na" means there
            # is nothing to read. The difference matters: a meter that
            # says N/A while it is still counting looks broken.
            self._tmp_state = "ok" if temp >= 0 else "na"
            self._gpu_state = "ok" if gpu >= 0 else "na"

    def _spawn_worker(self):
        """The process that asks the machine the slow questions."""
        root = Path(__file__).resolve().parent.parent
        try:
            return subprocess.Popen(
                [sys.executable, "-m", "core.metrics_worker",
                 "--parent", str(os.getpid())],
                cwd=str(root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            return None

    @staticmethod
    def _kill_worker(proc) -> None:
        """Stop the process that holds the question."""
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=3.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            if proc.stdout is not None:
                proc.stdout.close()
        except Exception:
            pass

    def _slow_loop(self):
        """The GPU and the temperature, asked in another process.

        The worker writes one line per reading and this thread reads them
        as they come. If the worker dies, the meters say N/A and the
        thread asks again: a machine that is slow to answer must not take
        the meters down with it.
        """
        try:
            while self._running:
                proc = self._spawn_worker()
                if proc is None:
                    with self._lock:
                        self._gpu_state = "na"
                        self._tmp_state = "na"
                    if self._stop.wait(10.0):
                        break
                    continue
                self._worker = proc
                try:
                    for raw_line in proc.stdout:
                        if self._stop.is_set():
                            break
                        self._publish(raw_line)
                except Exception:
                    pass
                finally:
                    self._kill_worker(proc)
                    self._worker = None
                if not self._running:
                    break
                with self._lock:
                    self._gpu_state = "na"
                    self._tmp_state = "na"
                if self._stop.wait(5.0):
                    break
        finally:
            self._kill_worker(self._worker)
            self._worker = None

    def _update(self):
        if getattr(self, "_cpu_first", False):
            # The first reading has nothing to compare itself with, and
            # psutil answers 0,0 — which is not a measurement but the
            # absence of one. A quarter of a second of counting gives a
            # real number, and the ignition shows it.
            cpu = psutil.cpu_percent(interval=0.4)
            self._cpu_first = False
        else:
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

    def snapshot(self) -> dict:
        """What the machine says, and how sure we are of it.

        `gpu_state` and `tmp_state` say whether a number that is not there
        is missing because it has not been read yet —"pending"— or
        because there is nothing to read —"na". The difference matters: a
        meter that says N/A while it is still counting is a meter that
        looks broken, and the GPU counters take ten seconds on a busy
        machine.
        """
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
                "gpu_state": self._gpu_state,
                "tmp_state": self._tmp_state,
            }


    def stop(self) -> None:
        """Stop the two threads and the process that asks the machine.

        The meters belong to the program, and the program is going away.
        The threads are waited for, and the worker is stopped: a process
        that is still reading WMI when the program leaves is a reader of
        WMI with nobody to answer it.
        """
        self._running = False
        self._stop.set()
        self._kill_worker(self._worker)
        self._worker = None
        for thread in (getattr(self, "_t_fast", None),
                       getattr(self, "_t_slow", None)):
            if thread is None:
                continue
            try:
                if thread.is_alive():
                    thread.join(timeout=3.0)
            except Exception:
                pass


_metrics = _SysMetrics()


def _stop_metrics() -> None:
    """The meters stop when the program stops."""
    try:
        _metrics.stop()
    except Exception:
        pass


atexit.register(_stop_metrics)

