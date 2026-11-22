"""
tests/test_ignition_truth.py — the ignition reads the machine, and says
what it does not know.

WHAT IS BEING PROVED
    1. The ignition shows the vitals: the processor, the memory, the GPU
       and the temperature, read from the same place the panel reads them.
    2. A number that has not been read yet says "EN ESPERA" and not zero:
       zero is a measurement, and a machine that has not been measured is
       not a machine with nothing to report.
    3. The ignition is timed against the wall clock, not against its own
       ticker, so a busy machine does not stretch the show.
    4. A meter that is still counting says "…" and not "N/A": N/A means
       there is nothing to read, and the GPU counters take seconds.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from gui import boot as boot_mod  # noqa: E402
from gui import metrics as metrics_mod  # noqa: E402


# ── the vitals ──────────────────────────────────────────────────────────────

def test_the_vitals_are_read_from_the_same_place_the_panel_reads_them(monkeypatch):
    monkeypatch.setattr(metrics_mod._metrics, "snapshot",
                        lambda: {"cpu": 12.0, "mem": 34.0, "net": 0.0,
                                 "gpu": 7.0, "tmp": 55.0,
                                 "gpu_state": "ok", "tmp_state": "ok"})
    v = boot_mod.read_vitals()
    assert v["cpu"] == 12.0
    assert v["mem"] == 34.0
    assert v["gpu"] == 7.0
    assert v["tmp"] == 55.0


def test_a_number_that_has_not_been_read_is_not_zero(monkeypatch):
    monkeypatch.setattr(metrics_mod._metrics, "snapshot",
                        lambda: {"cpu": 12.0, "mem": 34.0, "net": 0.0,
                                 "gpu": -1.0, "tmp": -1.0,
                                 "gpu_state": "pending", "tmp_state": "pending"})
    v = boot_mod.read_vitals()
    assert v["gpu"] == -1.0
    assert v["tmp"] == -1.0


def test_the_vitals_survive_a_broken_meter(monkeypatch):
    def _boom():
        raise RuntimeError("no metrics here")

    monkeypatch.setattr(metrics_mod._metrics, "snapshot", _boom)
    v = boot_mod.read_vitals()
    assert v["gpu"] == -1.0
    assert v["tmp"] == -1.0


# ── the meter says what it knows ────────────────────────────────────────────

def test_a_meter_that_is_counting_says_ellipsis_and_not_na():
    """The difference between "not read yet" and "there is nothing"."""
    import threading
    obj = object.__new__(metrics_mod._SysMetrics)
    obj.cpu = obj.mem = obj.net = 0.0
    obj.gpu = obj.tmp = -1.0
    obj._gpu_state = "pending"
    obj._tmp_state = "pending"
    obj._lock = threading.Lock()
    snap = obj.snapshot()
    assert snap["gpu_state"] == "pending"
    assert snap["tmp_state"] == "pending"


def test_a_meter_with_nothing_to_read_says_na():
    import threading
    obj = object.__new__(metrics_mod._SysMetrics)
    obj.cpu = obj.mem = obj.net = 0.0
    obj.gpu = obj.tmp = -1.0
    obj._gpu_state = "na"
    obj._tmp_state = "na"
    obj._lock = threading.Lock()
    snap = obj.snapshot()
    assert snap["gpu_state"] == "na"
    assert snap["tmp_state"] == "na"


# ── the ignition ────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qt_app():
    import os
    previous = os.environ.get("QT_QPA_PLATFORM")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app
    if previous is None:
        os.environ.pop("QT_QPA_PLATFORM", None)
    else:
        os.environ["QT_QPA_PLATFORM"] = previous


def _overlay(qt_app, checks=None):
    from PyQt6.QtWidgets import QWidget
    parent = QWidget()
    return boot_mod.BootOverlay(parent, checks=checks), parent


def test_the_ignition_is_timed_against_the_wall_clock(qt_app):
    """Not against its own ticker: a busy machine would stretch the show."""
    overlay, parent = _overlay(qt_app, checks=[("X", boot_mod.OK, "ok")])
    overlay.start()
    overlay._started_at = time.monotonic() - 3.0
    assert overlay.elapsed_ms() >= 3000
    overlay.skip()
    parent.deleteLater()


def test_the_ignition_ends_when_the_machine_has_spoken(qt_app, monkeypatch):
    overlay, parent = _overlay(qt_app, checks=[("X", boot_mod.OK, "ok")])
    monkeypatch.setattr(boot_mod, "read_vitals",
                        lambda: {"cpu": 1.0, "mem": 2.0, "gpu": 3.0, "tmp": 40.0})
    overlay.start()
    overlay._shown = len(overlay._checks)
    overlay._vitals_asked = overlay.elapsed_ms()
    overlay._read_vitals()
    overlay._maybe_end()
    assert overlay._ending is True
    overlay.skip()
    parent.deleteLater()


def test_the_ignition_waits_for_the_signs_and_gives_up_eventually(qt_app, monkeypatch):
    overlay, parent = _overlay(qt_app, checks=[("X", boot_mod.OK, "ok")])
    monkeypatch.setattr(boot_mod, "read_vitals",
                        lambda: {"cpu": 1.0, "mem": 2.0, "gpu": -1.0, "tmp": -1.0})
    overlay.start()
    overlay._shown = len(overlay._checks)
    overlay._vitals_asked = overlay.elapsed_ms()
    overlay._read_vitals()
    # The signs are not here yet: the ignition keeps waiting.
    overlay._maybe_end()
    assert overlay._ending is False
    # But its patience is not infinite.
    overlay._vitals_asked = overlay.elapsed_ms() - boot_mod.VITALS_WAIT_MS - 10
    overlay._maybe_end()
    assert overlay._ending is True
    overlay.skip()
    parent.deleteLater()


def test_the_ignition_shows_the_vitals(qt_app, monkeypatch):
    """The four numbers are on the screen while it starts."""
    overlay, parent = _overlay(qt_app, checks=[("X", boot_mod.OK, "ok")])
    monkeypatch.setattr(boot_mod, "read_vitals",
                        lambda: {"cpu": 42.0, "mem": 88.0, "gpu": 7.0, "tmp": 66.0})
    overlay.start()
    overlay._read_vitals()
    overlay._shown = 1
    overlay.update()
    assert overlay._vitals["cpu"] == 42.0
    assert overlay._vitals["tmp"] == 66.0
    overlay.skip()
    parent.deleteLater()
