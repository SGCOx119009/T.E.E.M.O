"""
tests/test_interface_redesign.py — the interface the user asked for.

THE USER'S OWN WORDS
    "la pestaña news tmb deberia poder cerrarse ya que molesta la vista ...
     y no se va por si solo como una notificacion"
    "el boton controles, esta duplicado y por dentro es un desastre de lista"
    "tmb que los modales no se superpongan unas sobre otras como cartas"
    "para que sirve cuestionario? mejor seria eso donde se puede ver la
     camara y mi mano ... se cree como un OBS"
    "tanto maxximizar/pantalla completa, modo ventana y minimizar sigue
     medio bug"

WHAT IS BEING PROVED
    1. THE CONTROLS ARE IN GROUPS, AND THERE IS ONE OF THEM. One drawer,
       seven groups, and two header buttons that are two doors to it.
    2. THE PANEL UNDER THE HEAD IS A NOTIFICATION. It closes with the
       mouse, it goes on its own, and the clock stops while the pointer is
       on it.
    3. TWO MODALS NEVER LIE ON TOP OF EACH OTHER.
    4. THE VIEWER IS AN OBS, AND THE QUESTIONNAIRE IS A TAB OF IT.
    5. THE THREE THINGS OF THE RIGHT COLUMN ARE THREE PAGES, AND THE
       TASKS ARE ONE OF THEM.
    6. THE FOUR SHAPES OF THE WINDOW KEEP THEIR STATE.
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
    # THE SHORTCUT IS NOT WRITTEN ON THE REAL DESKTOP.
    #
    # A test that presses the button of the shortcut would otherwise leave
    # a `T.E.E.M.O.lnk` on the desktop of whoever is running the tests.
    # The button is still pressed and its handler still runs; what it would
    # write goes to the temporary folder of the test instead.
    written = []
    monkeypatch.setattr(MainWindow, "_create_desktop_shortcut",
                        lambda self: written.append(True))
    win._shortcuts_written = written
    qt_app.processEvents()
    boot = getattr(win, "_boot", None)
    if boot is not None:
        boot.skip()
    qt_app.processEvents()
    yield win
    # THE CAMERA OF THIS WINDOW IS CLOSED BEFORE THE WINDOW GOES.
    #
    # A test that pressed the button of the hands leaves the service
    # watching the camera, and the service holds the camera of the whole
    # process. Ten tests later there were ten services running MediaPipe
    # on the same frames, the machine was burning five cores, and the
    # suite took three times as long as it should. The window is closed
    # first and the camera afterwards: in that order, so that nothing is
    # left reading from a device that is being closed.
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
    win.deleteLater()
    qt_app.processEvents()
    # And the camera of the process, in case somebody was still holding it.
    #
    # THE READERS ARE LET GO, BUT THE DEVICE STAYS OPEN. Closing the
    # camera between tests is what opened and closed the DirectShow
    # device over and over, and the driver did not survive it.
    try:
        from core.camera import hub as _camera_hub
        hub = _camera_hub()
        if hub.readers:
            hub.release_readers()
    except Exception:
        pass


# ── 1. the controls ─────────────────────────────────────────────────────────

def test_there_is_one_drawer_and_not_two(window):
    """The gear and the sliders are two doors to the same panel."""
    assert window._quick_drawer is window._ctrl_drawer, \
        "there are two drawers again, and they look like a copy of each other"


def test_the_drawer_has_eight_groups(window):
    """EIGHT, and the eighth is the Event Stream.

    The drawer had seven groups and the test said seven, because seven was
    the truth. The FASE 12 added one more — the Event Stream, the table
    that says what the assistant did and how long each step took — and the
    truth is now eight. The test is changed to say what the product is,
    not what it was."""
    groups = list(window._GROUPS)
    assert groups == ["voice", "language", "look", "hand", "session",
                      "system", "tools", "events"]
    assert window._group_stack.count() == len(groups)


def test_every_group_opens_and_shows_something(window):
    for key in window._GROUPS:
        window._select_group(key)
        assert window._group_stack.currentWidget() is window._group_pages[key]
        # A group with nothing in it is a group nobody needs.
        page = window._group_pages[key]
        assert page.layout().count() > 1, f"the group {key} is empty"


def test_one_door_opens_the_drawer_on_the_group_that_was_used_last(window):
    """The gear and the sliders opened the same drawer on two pages, and the
    user could not tell the two buttons apart."""
    # The quick access reaches the two pages on purpose.
    window._open_tools()
    assert window._group_rail.current == "tools"
    assert window._ctrl_drawer.isVisible()

    window._open_controls()
    assert window._group_rail.current == "voice"
    assert window._ctrl_drawer.isVisible()

    # And the door of the header opens the group that was open last.
    window._drawer_btn.setChecked(False)
    window._toggle_controls(True)
    assert window._group_rail.current == "voice"
    assert window._ctrl_drawer.isVisible()

    window._select_group("hand")
    window._drawer_btn.setChecked(False)
    window._toggle_controls(True)
    assert window._group_rail.current == "hand"


def test_the_window_modes_are_named_separately(window):
    """One button that toggled full screen left the other wishes unnamed."""
    for name in ("_maximize_btn", "_fullscreen_btn", "_minimize_btn"):
        button = getattr(window, name, None)
        assert button is not None, f"the button {name} does not exist"
        assert button.text(), f"the button {name} says nothing"


def test_no_two_window_buttons_say_the_same_thing(window):
    """Two buttons named VENTANA, side by side, with nothing to tell them apart.

    The maximize button used to turn into "VENTANA" when the window was
    maximized, and the button under it already said "VENTANA".
    """
    names = ("_maximize_btn", "_fullscreen_btn", "_minimize_btn")
    for state in ("normal", "maximized", "fullscreen"):
        if state == "maximized":
            window.showMaximized()
        elif state == "fullscreen":
            window.showFullScreen()
        else:
            window.showNormal()
        window._refresh_window_btns()
        labels = [getattr(window, name).text() for name in names]
        assert len(set(labels)) == len(labels), (
            f"in the {state} window two buttons say the same thing: {labels}")


# ── 2. the notification ─────────────────────────────────────────────────────

def test_the_deck_does_not_cover_the_conversation(window, qt_app):
    """THE DECK IS A COLUMN, NOT A PANEL THAT FLOATS OVER THE OTHER ONE.

    It used to be a floating panel that put itself on top of the
    right-hand column: the conversation, its rail and all, went under
    the cards, and there was no way to get them back without closing the
    deck. Now the deck is a column of its own, between the face and the
    conversation, and the two never touch.
    """
    window._blades.clear()
    window._push_blade("NEWS", "El tiempo en Tacna", "23:14")
    window._push_blade("INFORME", "Tres puntos de hoy", "23:15")
    assert window._blades.is_open(), "the deck did not open with the cards in it"
    qt_app.processEvents()

    deck = window._blades.geometry()
    column = window._right_panel.geometry()
    assert not deck.intersects(column), (
        "the deck is drawn over the conversation column")
    assert window._right_panel.isVisible(), \
        "the conversation column is not on screen"
    window._blades.clear()


def test_the_room_under_the_head_keeps_its_size(window, qt_app):
    """The room where the drawer of the controls stands is always there.

    It used to appear and disappear with the notifications, so the
    drawer had a home at the start and lost it after the first report,
    and the face changed size every time something arrived.
    """
    window._show_content("NEWS — Tacna", "Three things.")
    window._hide_content()
    qt_app.processEvents()
    split = window._center_split
    sizes = split.sizes()
    idx = split.indexOf(window._lower_stack)
    assert idx >= 0
    assert sizes[idx] >= 160, (
        f"the room under the head is {sizes[idx]} pixels tall: it is a hole")


def test_a_notification_opens_and_can_be_closed_with_the_mouse(window):
    window._show_content("NEWS — Tacna", "Three things.")
    assert window._content_panel.isVisible()
    window._hide_content()
    assert not window._content_panel.isVisible()


def test_the_notification_goes_on_its_own(window):
    window._show_content("NEWS — Tacna", "Three things.")
    assert window._content_left > 0, "the clock did not start"
    assert window._content_timer.isActive(), "the clock is not running"
    # The clock is simulated: twenty seconds of the panel's life.
    window._content_left = 0.05
    window._tick_content()
    assert not window._content_panel.isVisible(), \
        "the notification did not go on its own"


def test_the_clock_stops_while_the_pointer_is_on_the_notification(window):
    window._show_content("NEWS — Tacna", "Three things.")
    window._set_content_paused(True)
    left = window._content_left
    window._tick_content()
    assert window._content_left == left, "the clock ran with the pointer on it"
    window._set_content_paused(False)
    window._tick_content()
    assert window._content_left < left, "the clock did not start again"


def test_a_live_job_does_not_time_out(window):
    """A job in progress is not a notification."""
    window._set_content_life(0)
    assert not window._content_timer.isActive()
    window._show_content("NEWS — Tacna", "Three things.")
    assert window._content_timer.isActive(), \
        "a notification has to go on its own"


# ── 3. the modals ───────────────────────────────────────────────────────────

def test_only_one_modal_is_on_screen(window):
    window._open_memory_panel()
    window._open_audio_devices()
    alive = [name for name in window._OVERLAY_ATTRS
             if getattr(window, name, None) is not None
             and getattr(window, name).isVisible()]
    assert len(alive) <= 1, f"two modals are on screen at once: {alive}"


def test_a_confirmation_is_not_covered(window):
    """The question has to be answered before anything else opens."""
    window._show_confirm_banner("Shut down?", "Really?")
    assert window._confirm_pending()
    window._open_memory_panel()
    assert window._confirm_pending(), "the confirmation was covered"
    memory = getattr(window, "_memory_overlay", None)
    assert memory is None or not memory.isVisible(), \
        "the memory panel opened over the question"


# ── 4. the viewer ───────────────────────────────────────────────────────────

def test_the_viewer_opens_and_closes(window):
    window._toggle_viewer()
    assert window._lower_stack.currentWidget() is window._viewer_panel
    window._toggle_viewer()
    assert window._lower_stack.currentWidget() is window._lower_blank


def test_the_viewer_has_the_two_halves(window):
    """The camera on one side, the screen on the other."""
    viewer = window._viewer
    assert viewer._camera is not None
    assert viewer._screen is not None
    assert viewer._hands is not None
    assert viewer._camera is not viewer._screen


def test_the_questionnaire_is_a_tab_of_the_viewer(window):
    viewer = window._viewer
    viewer.show_quiz()
    assert viewer._stack.currentWidget() is window._quiz_panel
    viewer.show_view()
    assert viewer._stack.currentWidget() is not window._quiz_panel


def test_the_quiz_opens_the_viewer(window):
    window._show_quiz("History", [{"question": "Who?", "options": ["A"],
                                    "answer": "A"}])
    assert window._lower_stack.currentWidget() is window._viewer_panel
    assert window._viewer._stack.currentWidget() is window._quiz_panel


# ── 5. the right column ─────────────────────────────────────────────────────

def test_the_right_column_is_three_pages(window):
    assert window._right_stack.count() == 3
    for key, index in (("talk", 0), ("say", 1), ("work", 2)):
        window._select_right_page(key)
        assert window._right_stack.currentIndex() == index


def test_the_tasks_live_in_the_right_column(window):
    """They used to be in the left one, under the gauges."""
    assert window._tasks_panel.parent() is window._right_stack
    assert window._tasks_panel not in window._left_panel.findChildren(
        type(window._tasks_panel))


def test_the_quick_access_is_in_the_left_column(window):
    from PyQt6.QtWidgets import QPushButton

    buttons = window._quick_panel.findChildren(QPushButton)
    assert len(buttons) == 8, "the quick access lost a button"


# ── 6. the four shapes of the window ────────────────────────────────────────

def test_leaving_the_full_screen_returns_to_what_it_was(window):
    """A maximized window that came back small was the bug."""
    window.showMaximized()
    window._toggle_fullscreen()
    assert window.isFullScreen()
    window._toggle_fullscreen()
    assert window.isMaximized(), "the window did not come back maximized"


def test_the_four_named_actions(window):
    window._maximize_window()
    assert window.isMaximized()
    window._restore_window()
    assert not window.isMaximized()
    assert not window.isFullScreen()
    window._minimize_window()
    assert window.isMinimized()
    window._restore_window()


# ── 7. the shortcut, written without COM ────────────────────────────────────

def test_the_shortcut_is_written_without_com(tmp_path):
    """The shortcut is written by wscript.exe, and no COM enters the process.

    `win32com.client.Dispatch` initializes COM in the thread that imports
    it, and the program has threads that are already inside COM (the
    hardware panel talks to WMI). Importing COM in the middle of the
    program is how a process dies with «Windows fatal exception: access
    violation».

    So the shortcut is written from another process, and this test proves
    that it really is written: the file is there afterwards.
    """
    import sys

    from gui.window import MainWindow

    lnk = str(tmp_path / "T.E.E.M.O.lnk")
    MainWindow._create_lnk_windows(
        lnk, sys.executable, "main.py", str(tmp_path), "teemo.ico")
    assert Path(lnk).exists(), "the shortcut was not written"
    assert Path(lnk).stat().st_size > 0, "the shortcut is empty"


def test_the_program_does_not_import_com(tmp_path):
    """No COM in this process: the shell is always another process."""
    source = (BASE / "gui" / "window.py").read_text(encoding="utf-8")
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "import win32com" not in stripped, f"COM is imported here: {stripped}"
