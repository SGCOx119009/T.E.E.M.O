"""
tests/test_window_modes.py — the three faces of the window, and the jumps.

WHAT IS BEING PROVED
    The user asked for it by name: "revisa bien a detalle los cambios como
    maximizar, ventana y minimizar". Those three are the three appearances:
    the dashboard (everything, maximised), the core (a small window in the
    corner) and the tray (nothing on screen but the icon).

    A window that changes its shape must not:
      · leave a drawer open on top of the corner,
      · keep the full screen it was in,
      · lose its size on the way back,
      · forget where it was put,
      · or die on the way.

WHAT THIS IS NOT
    A test of the monitor. It checks what the program does with its own
    window; whether the screen is really 1920 pixels wide is another story.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from memory import config_manager  # noqa: E402


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


@pytest.fixture()
def window(qt_app, tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config_manager, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_manager, "CONFIG_FILE", config_dir / "api_keys.json")

    from gui.window import MainWindow

    win = MainWindow(str(BASE / "core" / "face_model.obj"))
    win.show()
    yield win
    win._quitting = True
    try:
        win.close()
    except Exception:
        pass
    win.deleteLater()
    qt_app.processEvents()


def _pump(qt_app, seconds=0.25):
    import time

    end = time.time() + seconds
    while time.time() < end:
        qt_app.processEvents()
        time.sleep(0.01)


# ── the three shapes ───────────────────────────────────────────────────────

def test_the_dashboard_is_the_window_maximised(window, qt_app):
    """The first face: everything, as big as the screen."""
    window.apply_appearance("dashboard")
    _pump(qt_app, 0.4)
    assert window.isMaximized(), (
        f"the dashboard is not maximised: {window.width()}x{window.height()}"
    )
    assert window._header.isVisible(), "the header is gone in the dashboard"
    assert window._left_panel.isVisible(), "the left panel is gone in the dashboard"


def test_the_core_is_a_small_window_in_the_corner(window, qt_app):
    """The second face: only the head, in the corner the user chose."""
    window.apply_appearance("core")
    _pump(qt_app, 0.4)

    assert window.width() <= 400 and window.height() <= 400, (
        f"the core is not small: {window.width()}x{window.height()}"
    )
    assert not window._header.isVisible(), "the header is still there in the corner"
    assert not window._left_panel.isVisible(), "the left panel is still there in the corner"
    assert window.hud.isVisible(), "the face is not there in the corner"


def test_the_core_goes_to_the_corner_that_was_chosen(window, qt_app):
    """Bottom-right by default: the face is in the bottom-right."""
    from PyQt6.QtWidgets import QApplication

    window.apply_appearance("core")
    _pump(qt_app, 0.4)
    screen = QApplication.primaryScreen()
    area = screen.availableGeometry()
    assert window.x() + window.width() <= area.right() + 40, (
        f"the core is not on the right: x={window.x()}"
    )
    assert window.y() + window.height() <= area.bottom() + 40, (
        f"the core is not at the bottom: y={window.y()}"
    )


def test_the_tray_is_nothing_on_screen(window, qt_app):
    """The third face: no window at all, only the icon."""
    window.apply_appearance("tray")
    _pump(qt_app, 0.3)
    assert not window.isVisible(), "the window is still on screen in the tray mode"


def test_coming_back_from_the_tray_brings_the_window_back(window, qt_app):
    """The icon in the tray is the way back."""
    window.apply_appearance("dashboard")
    _pump(qt_app, 0.3)
    window.apply_appearance("tray")
    _pump(qt_app, 0.3)
    window.restore_window()
    _pump(qt_app, 0.4)
    assert window.isVisible(), "the window did not come back from the tray"
    assert window.isMaximized(), (
        "it came back as a small window instead of the dashboard"
    )


def test_the_three_faces_can_be_walked_through_without_dying(window, qt_app):
    """Dashboard, corner, tray, dashboard — three times over.

    The window is the same object in the three of them, so walking the
    cycle is the honest test of the change: nothing may be left broken
    behind.
    """
    for round_number in range(3):
        window.apply_appearance("dashboard")
        _pump(qt_app, 0.2)
        window.apply_appearance("core")
        _pump(qt_app, 0.2)
        window.apply_appearance("tray")
        _pump(qt_app, 0.2)
        window.apply_appearance("dashboard")
        _pump(qt_app, 0.2)
        assert window.isVisible(), f"the window died on round {round_number}"
        assert not window._quitting, f"a button closed the program on round {round_number}"
    assert window._header.isVisible(), "the header did not come back"


# ── the jumps ──────────────────────────────────────────────────────────────

def test_leaving_the_full_screen_clears_it(window, qt_app):
    """A full screen window that becomes a corner window is a jump.

    The size of a full screen window is the screen's, and the window
    manager ignores a resize until the full screen is left. So the corner
    window would keep the size of the screen — a face the size of a wall.
    """
    window.showFullScreen()
    _pump(qt_app, 0.3)
    assert window.isFullScreen()

    window.apply_appearance("core")
    _pump(qt_app, 0.4)

    assert not window.isFullScreen(), "the window is still full screen in the corner"
    assert window.width() <= 400, (
        f"the corner window kept the size of the screen: {window.width()}"
    )


def test_going_back_to_the_dashboard_undoes_the_corner_size(window, qt_app):
    """The window does not stay 300 pixels wide for ever.

    The size of the corner window is 300. If the window came back at 300,
    the dashboard would be a corner window with a header on it — and the
    panels inside it would have nowhere to go.
    """
    window.apply_appearance("core")
    _pump(qt_app, 0.3)
    assert window.width() <= 400

    window.apply_appearance("dashboard")
    _pump(qt_app, 0.4)
    assert window.width() > 400, (
        f"the window stayed small: {window.width()}x{window.height()}"
    )
    assert not window.isFullScreen(), "the full screen was left behind"
    assert window.isMaximized(), "the dashboard did not come back maximised"


def test_the_drawers_are_closed_when_the_window_goes_to_the_corner(window, qt_app):
    """A drawer left open on top of the corner window is a panel of glass
    in the wrong place, and it swallows the clicks of the desktop."""
    window.apply_appearance("dashboard")
    _pump(qt_app, 0.2)
    window._ctrl_btn.setChecked(True)
    window._toggle_controls(True)
    _pump(qt_app, 0.3)
    assert window._ctrl_drawer.isVisible()

    window.apply_appearance("core")
    _pump(qt_app, 0.4)
    assert not window._ctrl_drawer.isVisible(), "the controls drawer stayed open"
    assert not window._ctrl_btn.isChecked(), "the button still says it is open"


def test_the_window_comes_back_bigger_than_the_corner_window(window, qt_app):
    """The change of face must not leave the window at the corner's size.

    The user resized the window by hand, went to the corner, and came
    back: the dashboard is a dashboard, not a 300-pixel hole with a header
    on it.
    """
    window.apply_appearance("dashboard")
    _pump(qt_app, 0.3)
    window.showNormal()
    window.resize(1100, 720)
    _pump(qt_app, 0.3)

    window.apply_appearance("core")
    _pump(qt_app, 0.3)
    window.apply_appearance("dashboard")
    _pump(qt_app, 0.4)

    assert window.width() > 400, (
        f"the window came back at {window.width()} pixels wide"
    )


def test_the_window_survives_being_minimised_and_restored(window, qt_app):
    """Minimised is not the tray: the window comes back on its own."""
    window.apply_appearance("dashboard")
    _pump(qt_app, 0.3)
    window.showMinimized()
    _pump(qt_app, 0.3)
    assert window.isMinimized()
    window.showNormal()
    _pump(qt_app, 0.3)
    assert not window.isMinimized()
    assert window.width() > 0 and window.height() > 0
    assert window.isVisible()
