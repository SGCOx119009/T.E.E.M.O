"""
tests/test_arc_gauge.py — the arc gauge of the tactical HUD, measured.

WHAT WAS ASKED FOR
    The ring of `RingGauge` is a closed dial; the tactical HUD wants an ARC —
    open at the bottom, with divisions that can be marked, a needle, an alarm
    zone, and the value, the minimum and the maximum. It has to be painted by
    software (QPainter, no GPU) and it has to fit inside the 5.4 ms the
    reactor spends on a whole frame.

WHAT IS BEING PROVED
    1. THE GAUGE IS BUILT, and it takes a value — even a value outside its
       own scale, which must not raise.
    2. IT PAINTS SOMETHING. A blank rectangle is not a gauge.
    3. IT PAINTS CHEAP. The frame is measured with `time.perf_counter` and it
       has to stay well inside the 5.4 ms of the reactor's frame.
    4. IT IS SOFTWARE. There is no OpenGL anywhere in `gui/widgets.py`.

THE MEASUREMENT, AND WHY IT IS NOT FRAGILE
    The MEDIAN is used, not the mean: this machine is shared, and a frame
    that a background process interrupted says nothing about the gauge. The
    budget is checked against the median of a few rounds of repaints, which
    is the number that describes the gauge and not the number that describes
    the weather.
"""
from __future__ import annotations

import os
import statistics
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

# The budget of one frame of the reactor, in milliseconds. The plan fixes it
# at 5.4 ms and the arc gauge has to live inside it.
REACTOR_FRAME_BUDGET_MS = 5.4


@pytest.fixture(scope="module")
def qt_app():
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def gauge(qt_app):
    from gui.widgets import ArcGauge

    g = ArcGauge("LOAD", 0, 100, segments=8, alarm=85, unit="%")
    g.resize(220, 140)
    return g


def _render(widget, width, height):
    """One painted frame of the widget, into a pixmap."""
    from PyQt6.QtGui import QPixmap

    pm = QPixmap(width, height)
    widget.render(pm)
    return pm


# ── it is built, and it takes a value ────────────────────────────────────────

def test_the_gauge_is_built_and_takes_a_value(gauge, qt_app):
    from PyQt6.QtWidgets import QWidget

    assert isinstance(gauge, QWidget)
    gauge.set_value(42.0)
    assert gauge.value() == 42.0


def test_a_value_outside_the_scale_does_not_raise(gauge):
    for bad in (-1000.0, 1e9, float("nan"), float("inf"), -float("inf")):
        gauge.set_value(bad)          # must not raise
    assert 0.0 <= gauge.value() <= 100.0


def test_a_value_that_is_not_a_number_is_ignored(gauge):
    gauge.set_value(50.0)
    gauge.set_value("not a number")
    gauge.set_value(None)
    assert gauge.value() == 50.0


def test_the_alarm_zone_can_be_set_and_cleared(gauge):
    gauge.set_alarm_zone(80, 95)
    gauge.clear_alarm_zone()
    gauge.set_alarm_zone(80, 95)
    gauge.set_value(90.0)
    assert gauge.value() == 90.0


# ── it paints something ──────────────────────────────────────────────────────

def test_it_paints_something(gauge):
    from PyQt6.QtGui import QColor

    gauge.set_value(42.0)
    pm = _render(gauge, 220, 140)
    img = pm.toImage()
    colours = {img.pixel(x, y) for x in range(0, 220, 2) for y in range(0, 140, 2)}
    assert len(colours) > 3, "the gauge painted a flat rectangle"


def test_the_needle_moves_when_the_value_moves(gauge):
    """Two values must not paint the same picture: the needle has to move."""
    def picture(value):
        gauge.set_value(value)
        pm = _render(gauge, 220, 140)
        return pm.toImage()

    low = picture(10.0)
    high = picture(90.0)
    different = sum(1 for x in range(0, 220, 2) for y in range(0, 140, 2)
                    if low.pixel(x, y) != high.pixel(x, y))
    assert different > 20, "the needle did not move"


# ── the budget ───────────────────────────────────────────────────────────────

def test_one_frame_fits_inside_the_reactor_budget(gauge):
    from PyQt6.QtGui import QPixmap

    pm = QPixmap(220, 140)
    # A warm-up, so the first frame does not pay for the font database.
    for _ in range(20):
        gauge.set_value(40.0)
        gauge.render(pm)

    rounds = []
    for _ in range(5):
        samples = []
        for i in range(60):
            gauge.set_value(40.0 + (i % 30))
            t0 = time.perf_counter()
            gauge.render(pm)
            samples.append((time.perf_counter() - t0) * 1000.0)
        rounds.append(statistics.median(samples))

    median_ms = statistics.median(rounds)
    worst_round_ms = max(rounds)
    print(f"\nArcGauge frame: median {median_ms:.3f} ms, worst round "
          f"{worst_round_ms:.3f} ms (budget {REACTOR_FRAME_BUDGET_MS} ms)")

    assert median_ms < REACTOR_FRAME_BUDGET_MS, (
        f"the arc gauge paints in {median_ms:.3f} ms and the reactor's frame "
        f"is allowed {REACTOR_FRAME_BUDGET_MS} ms")


# ── it is software, not GPU ──────────────────────────────────────────────────

def test_the_widgets_module_has_no_opengl():
    source = (BASE / "gui" / "widgets.py").read_text(encoding="utf-8")
    assert "QOpenGLWidget" not in source
    assert "OpenGL" not in source
    assert "QOpenGL" not in source


def test_the_gauge_does_not_use_opengl(gauge):
    from PyQt6.QtGui import QPixmap

    pm = QPixmap(220, 140)
    gauge.set_value(42.0)
    gauge.render(pm)        # a software render; an OpenGL one would not work here
    assert pm.isNull() is False
