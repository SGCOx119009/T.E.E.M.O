"""
tests/test_interface_drawer.py — the drawer of the controls, measured.

WHAT WAS WRONG
    The rail of the groups lay down: seven names in a row across three
    hundred pixels. Seven names need about five hundred and forty, so Qt
    gave each one thirty-six and every name came out cut in half — the
    index of the drawer was unreadable ("IDIOMA" read as "I]OM",
    "HERRAMIENTAS" as "4MIE").

    The drawer also landed on the face of the assistant, the quick access
    was cut in half by a column that was too short, and the panel of the
    hands painted a third row below its own edge.

WHAT IS BEING PROVED
    1. EVERY NAME OF THE RAIL IS WRITTEN WHOLE.
    2. THE DRAWER STANDS IN THE EMPTY ROOM UNDER THE HEAD, and it goes
       back to hanging from the button when that room is taken.
    3. THE QUICK ACCESS IS NOT CUT.
    4. THE PANEL OF THE HANDS IS AS TALL AS ITS ROWS.
    5. THE STATE OF THE CAMERA IS IN THE LANGUAGE OF THE USER.
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
    """A real MainWindow, with a config of our own."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config_manager, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_manager, "CONFIG_FILE", config_dir / "api_keys.json")

    from gui.window import MainWindow

    win = MainWindow(str(BASE / "core" / "face_model.obj"))
    win.show()
    qt_app.processEvents()
    boot = getattr(win, "_boot", None)
    if boot is not None:
        boot.skip()
    qt_app.processEvents()
    yield win
    try:
        service = getattr(win, "_gesture", None)
        if service is not None:
            service.stop()
    except Exception:
        pass
    try:
        viewer = getattr(win, "_viewer", None)
        if viewer is not None:
            viewer.stop()
    except Exception:
        pass
    win._quitting = True
    try:
        win.close()
    except Exception:
        pass


@pytest.fixture()
def drawer(window, qt_app):
    """The drawer, open, so that the layout has really been carried out.

    A widget that was never shown has no geometry: the measurements of a
    closed drawer would be measurements of nothing.
    """
    window._ctrl_btn.setChecked(True)
    window._toggle_controls(True)
    qt_app.processEvents()
    return window._ctrl_drawer


# ── 1. the rail ────────────────────────────────────────────────────────────

def test_every_name_of_the_rail_is_written_whole(window, drawer):
    """A name that is cut in half is not a name."""
    from PyQt6.QtGui import QFontMetrics

    rail = window._group_rail
    buttons = list(rail._buttons)
    assert len(buttons) == len(window._GROUPS)
    for btn in buttons:
        text = btn.text()
        needed = QFontMetrics(btn.font()).horizontalAdvance(text)
        assert btn.width() >= needed, (
            f"the name {text!r} is cut: it has {btn.width()} pixels and "
            f"it needs {needed}")
        assert btn.height() >= btn.sizeHint().height()


def test_the_rail_stands_up(window, drawer):
    """A column of names, not a row."""
    rail = window._group_rail
    assert rail.height() > rail.width(), "the rail is lying down again"


def test_the_drawer_scrolls_instead_of_clipping(window):
    """A group that does not fit scrolls; it is not cut in half."""
    from PyQt6.QtWidgets import QScrollArea

    assert isinstance(window._group_scroll, QScrollArea)
    assert window._group_scroll.widget() is window._group_stack
    assert window._group_scroll.widgetResizable()


# ── 2. where the drawer stands ─────────────────────────────────────────────

def test_the_drawer_stays_out_of_the_face(window, qt_app):
    """The drawer does not land on the assistant."""
    window._ctrl_btn.setChecked(True)
    window._toggle_controls(True)
    qt_app.processEvents()
    drawer = window._ctrl_drawer
    assert drawer.isVisible()

    head = window._hud_cam_stack
    d = drawer.geometry()
    h = head.geometry()
    overlaps = not (d.right() <= h.left() or d.left() >= h.right()
                    or d.bottom() <= h.top() or d.top() >= h.bottom())
    assert not overlaps, (
        f"the drawer is drawn on the face of the assistant: "
        f"drawer={d} head={h}")


def test_the_drawer_stands_in_the_empty_room_under_the_head(window, qt_app):
    """While the room under the head is empty, the drawer stands in it."""
    window._ctrl_btn.setChecked(True)
    window._toggle_controls(True)
    qt_app.processEvents()
    assert window._ctrl_in_zone, "the drawer did not go to the empty room"
    zone = window._drawer_zone()
    assert zone is not None
    d = window._ctrl_drawer.geometry()
    assert zone.top() <= d.top() <= zone.bottom()


def test_the_drawer_gets_out_of_the_way_when_the_room_is_taken(window, qt_app):
    """When something else moves into that room, the drawer gets out."""
    window._ctrl_btn.setChecked(True)
    window._toggle_controls(True)
    qt_app.processEvents()
    assert window._ctrl_in_zone
    window._show_content("NOTICIA", "Algo que pasó.")
    qt_app.processEvents()
    assert not window._ctrl_in_zone, (
        "the drawer is still standing on top of the notification")
    assert window._ctrl_drawer.isVisible(), "the drawer disappeared"


# ── 3. the quick access ────────────────────────────────────────────────────

def test_the_quick_access_is_not_cut(window):
    """Ten switches, and all ten inside the panel."""
    from PyQt6.QtWidgets import QPushButton

    panel = window._quick_panel
    buttons = panel.findChildren(QPushButton)
    assert len(buttons) == 10, "the quick access lost a button"
    assert panel.height() >= panel.sizeHint().height(), (
        f"the quick access is cut: it has {panel.height()} pixels and it "
        f"needs {panel.sizeHint().height()}")
    for btn in buttons:
        g = btn.geometry()
        assert g.top() + g.height() <= panel.height(), (
            f"the switch {btn.text()!r} hangs out of the panel")


# ── 4. the panel of the hands ──────────────────────────────────────────────

def test_the_hands_panel_is_as_tall_as_its_rows(qt_app):
    """The third row was painted below the edge of the panel."""
    from PyQt6.QtWidgets import QWidget
    from gui.widgets import GestureReadout

    readout = GestureReadout()
    readout.set_rows([("Rastreador", "--", "TEXT_DIM"),
                      ("Postura", "--", "TEXT_DIM"),
                      ("Accion", "--", "GREEN")])
    assert readout.minimumHeight() >= 24 + 18 * 3, (
        "the panel of the hands is shorter than the rows it paints")
    assert isinstance(readout, QWidget)


# ── 5. the state of the camera ─────────────────────────────────────────────

def test_the_state_of_the_camera_is_translated(window, qt_app):
    """It used to say 'the camera is open' in English, in any language."""
    from core.localization import t

    viewer = window._viewer_panel
    viewer._on_camera_state("viewer.camera_open")
    qt_app.processEvents()
    shown = viewer._camera._placeholder
    assert shown == t("viewer.camera_open"), (
        f"the state of the camera is not translated: {shown!r}")
    assert shown != "viewer.camera_open"


def test_the_state_of_the_camera_keeps_the_detail(window, qt_app):
    """'La cámara se detuvo · OSError' — the detail is not thrown away."""
    from core.localization import t

    viewer = window._viewer_panel
    viewer._on_camera_state("viewer.camera_stopped:OSError")
    qt_app.processEvents()
    shown = viewer._camera._placeholder
    assert t("viewer.camera_stopped") in shown
    assert "OSError" in shown


# ── 6. the size of the window ──────────────────────────────────────────────

def test_the_window_opens_wide_enough(window):
    """Three columns need more than nine hundred and eighty pixels."""
    assert window.minimumWidth() >= 1100
    assert window.width() >= 1100
