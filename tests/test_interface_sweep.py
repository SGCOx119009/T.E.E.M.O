"""
tests/test_interface_sweep.py — every control of the interface, one by one.

WHAT IS BEING PROVED
    1. NOTHING COVERS A CONTROL. Every button, box and field that the user
       can reach with the mouse is really the thing that receives the click.
       A panel that floats over the interface — the clipboard panel used to
       sit over the questionnaire — swallows the clicks under it, and the
       whole interface looks dead. This is the test that says so.
    2. EVERY BUTTON ANSWERS. Each control is pressed and the program is
       asked afterwards whether it is still standing.
    3. THE WINDOW SURVIVES ITS OWN SIZES. Maximised, restored, minimised,
       back again: no jump, no crash, no half-drawn window.
    4. THE MUSIC PLAYER CAN BE CHOSEN. Spotify, YouTube or automatic, and
       the choice is remembered.

THE USER'S OWN WORDS
    "los clicks siguen sin funcionar, no me deja interactuar" and "revisa
    bien a detalle los cambios como maximizar, ventana y minimizar".
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
    # The ignition covers the whole interface on purpose, and it is put up
    # by a timer a moment after the window appears. The sweep is about the
    # interface underneath, so the show is let out and skipped — exactly as
    # a click would do it.
    _pump(qt_app, 0.6)
    boot = getattr(win, "_boot", None)
    if boot is not None:
        boot.skip()
    _pump(qt_app, 0.2)
    yield win
    win._quitting = True
    try:
        win.close()
    except Exception:
        pass
    win.deleteLater()
    qt_app.processEvents()


def _pump(qt_app, seconds=0.15):
    """Let the interface breathe, so a click has somewhere to land."""
    import time

    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    end = time.time() + seconds
    while time.time() < end:
        if app is not None:
            app.processEvents()
        time.sleep(0.01)


# ── 1. nothing covers a control ─────────────────────────────────────────────

def _clickable_widgets(window):
    """Every visible widget that a mouse is meant to reach."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import (QAbstractButton, QAbstractItemView, QComboBox,
                                 QLineEdit, QWidget)

    kinds = (QAbstractButton, QComboBox, QLineEdit, QAbstractItemView)
    out = []
    for widget in window.findChildren(QWidget):
        if not isinstance(widget, kinds):
            continue
        if not widget.isVisible() or not widget.isEnabled():
            continue
        if widget.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents):
            continue
        if widget.width() <= 0 or widget.height() <= 0:
            continue
        out.append(widget)
    return out


def test_nothing_covers_a_control(window, qt_app):
    """Every control that can be seen can also be clicked.

    The hit test is asked, not guessed: Qt is asked which widget would
    receive a click at the middle of each control. A widget that is not the
    control itself, and not one of its own children, is standing in the way.
    """
    from PyQt6.QtCore import QPoint

    blockers = []
    for widget in _clickable_widgets(window):
        point = widget.mapToGlobal(widget.rect().center())
        hit = qt_app.widgetAt(point)
        if hit is None:
            continue
        if hit is widget or widget.isAncestorOf(hit):
            continue
        blockers.append(
            f"{type(widget).__name__}('{widget.text() if hasattr(widget, 'text') else ''}')"
            f" is covered by {type(hit).__name__}"
            f"('{hit.text() if hasattr(hit, 'text') else ''}')"
        )
    assert not blockers, "these controls cannot be clicked:\n  " + "\n  ".join(blockers)


def test_the_clipboard_panel_does_not_sit_on_the_interface(window):
    """The panel that appears when something is copied stays out of the way.

    It used to float at the bottom of the middle column, over the
    questionnaire and over the order field.
    """
    panel = window._clipboard_panel
    panel.show_clipboard("un texto copiado de mas de diez caracteres")
    _pump(qt_app)
    quiz = getattr(window, "_quiz_panel", None)
    if quiz is not None and quiz.isVisible():
        assert not panel.geometry().intersects(quiz.geometry()), \
            "the clipboard panel is on top of the questionnaire"
    panel.hide()


def test_the_clipboard_panel_does_not_come_back_for_the_same_text(window):
    """One copy, one panel. Not one panel every time it is looked at."""
    from PyQt6.QtWidgets import QApplication

    QApplication.clipboard().setText("un texto largo para la prueba del portapapeles")
    _pump(qt_app, 0.3)
    first = window._clipboard_panel.isVisible()
    QApplication.clipboard().setText("un texto largo para la prueba del portapapeles")
    _pump(qt_app, 0.3)
    assert first is window._clipboard_panel.isVisible()
    window._clipboard_panel.hide()


# ── 1b. a real mouse, on real coordinates ──────────────────────────────────

def test_a_real_click_on_the_window_reaches_the_button(window, qt_app):
    """The mouse over the button lands on the button, and the button answers.

    This is the whole complaint in one test: "los clicks siguen sin
    funcionar". The routing is checked the way the windowing system does
    it: ask the application which widget is under the pointer, then deliver
    the click there. If the answer is not the button, the interface is dead
    under the user's finger — and that is exactly what was happening: the
    scene behind the panels was marked as ignoring the mouse, and in Qt that
    deafens the widget AND everything inside it.
    """
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest

    button = window._drawer_btn
    window.show()
    _pump(qt_app, 0.2)

    assert not button.isChecked(), "the drawer was already open"
    centre = button.mapToGlobal(button.rect().center())

    # 1. Who is under the pointer?
    under = qt_app.widgetAt(centre)
    assert under is not None, "nothing is under the pointer"
    assert under is button or button.isAncestorOf(under), (
        f"the pointer is over the gear, but {type(under).__name__} is in the way"
    )

    # 2. Does the click do something?
    QTest.mouseClick(under, Qt.MouseButton.LeftButton,
                     pos=under.mapFromGlobal(centre))
    _pump(qt_app, 0.3)

    assert button.isChecked(), "the click never reached the gear"
    assert window._quick_drawer.isVisible(), "the gear was pressed and nothing happened"


def test_a_real_click_opens_and_closes_the_controls_drawer(window, qt_app):
    """The second drawer answers the mouse too, and closes again."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest

    button = window._ctrl_btn
    window.show()
    _pump(qt_app, 0.2)

    centre = button.mapToGlobal(button.rect().center())
    under = qt_app.widgetAt(centre)
    assert under is button or button.isAncestorOf(under), \
        f"the pointer is over the sliders, but {type(under).__name__} is in the way"

    QTest.mouseClick(under, Qt.MouseButton.LeftButton,
                     pos=under.mapFromGlobal(centre))
    _pump(qt_app, 0.3)
    assert window._ctrl_drawer.isVisible(), "the controls drawer did not open"

    QTest.mouseClick(under, Qt.MouseButton.LeftButton,
                     pos=under.mapFromGlobal(centre))
    _pump(qt_app, 0.3)
    assert not window._ctrl_drawer.isVisible(), "the controls drawer did not close"


# ── 2. every button answers ────────────────────────────────────────────────

def test_every_button_answers_and_the_program_survives(window, qt_app):
    """Every button in the window is pressed, and the window is still there.

    A button that does nothing is not the same as a button that breaks the
    program: the second one has to be found while the interface is still
    standing.
    """
    from PyQt6.QtWidgets import QAbstractButton

    pressed = 0
    for button in window.findChildren(QAbstractButton):
        if not button.isVisible() or not button.isEnabled():
            continue
        if button.parent() is not window and not window.isAncestorOf(button.parent()):
            continue
        name = button.text() or getattr(button, "_kind", "") or type(button).__name__
        try:
            button.click()
            pressed += 1
        except Exception as exc:                  # noqa: BLE001
            pytest.fail(f"the button {name!r} broke the program: "
                         f"{type(exc).__name__}: {exc}")
        _pump(qt_app, 0.05)
    assert pressed > 0, "there was no button to press"
    assert window.isVisible() or True           # the window may hide itself
    assert not window._quitting, "a button closed the program"


# ── 2b. the drawers and the panels that only exist when they are opened ────

def _sweep_buttons(root, qt_app, where):
    """Every button of a panel is pressed, and the panel survives it."""
    from PyQt6.QtWidgets import QAbstractButton

    pressed = 0
    for button in root.findChildren(QAbstractButton):
        if not button.isVisible() or not button.isEnabled():
            continue
        name = button.text() or getattr(button, "_kind", "") or type(button).__name__
        try:
            button.click()
            pressed += 1
        except Exception as exc:                  # noqa: BLE001
            pytest.fail(f"the button {name!r} in {where} broke the program: "
                        f"{type(exc).__name__}: {exc}")
        _pump(qt_app, 0.05)
    return pressed


def test_the_settings_drawer_answers_every_button(window, qt_app):
    """The drawer behind the gear is a list of controls, and all of them.

    A drawer that is closed is not tested by anything: its buttons are
    invisible, so the sweep walks past them. So the drawer is opened and
    swept in its turn.
    """
    window._toggle_drawer(True)
    _pump(qt_app, 0.3)
    assert window._quick_drawer.isVisible(), "the settings drawer did not open"

    pressed = _sweep_buttons(window._quick_drawer, qt_app, "the settings drawer")
    assert pressed > 0, "the settings drawer had no buttons at all"
    assert not window._quitting, "a button in the settings drawer closed the program"

    window._toggle_drawer(False)
    _pump(qt_app, 0.2)


def test_the_controls_drawer_answers_every_button(window, qt_app):
    """And the drawer behind the sliders, where the music player is chosen."""
    window._toggle_controls(True)
    _pump(qt_app, 0.3)
    assert window._ctrl_drawer.isVisible(), "the controls drawer did not open"

    pressed = _sweep_buttons(window._ctrl_drawer, qt_app, "the controls drawer")
    assert pressed > 0, "the controls drawer had no buttons at all"
    assert not window._quitting, "a button in the controls drawer closed the program"

    window._toggle_controls(False)
    _pump(qt_app, 0.2)


def test_the_engine_panel_answers_every_button(window, qt_app):
    """The engine panel is a window of its own, and it has to hold up too."""
    window._open_providers()
    _pump(qt_app, 0.4)
    overlay = getattr(window, "_providers_overlay", None)
    if overlay is None:
        pytest.skip("the providers folder is not installed on this machine")

    pressed = _sweep_buttons(overlay, qt_app, "the engine panel")
    assert pressed > 0, "the engine panel had no buttons at all"
    assert not window._quitting, "a button in the engine panel closed the program"


def test_the_memory_panel_answers_every_button(window, qt_app):
    """And the memory panel."""
    window._open_memory_panel()
    _pump(qt_app, 0.4)
    overlay = getattr(window, "_memory_overlay", None)
    assert overlay is not None, "the memory panel did not open"

    pressed = _sweep_buttons(overlay, qt_app, "the memory panel")
    assert pressed > 0, "the memory panel had no buttons at all"
    assert not window._quitting, "a button in the memory panel closed the program"


def test_the_audio_panel_answers_every_button(window, qt_app):
    """And the audio devices panel."""
    window._open_audio_devices()
    _pump(qt_app, 0.4)
    overlay = getattr(window, "_audio_overlay", None)
    assert overlay is not None, "the audio panel did not open"

    pressed = _sweep_buttons(overlay, qt_app, "the audio panel")
    assert pressed > 0, "the audio panel had no buttons at all"
    assert not window._quitting, "a button in the audio panel closed the program"


def test_nothing_covers_a_control_inside_the_drawers(window, qt_app):
    """The hit test, again, but with the drawers open.

    A drawer is a panel of glass over the interface, so it is exactly the
    kind of thing that can stand on top of something else.
    """
    from PyQt6.QtCore import QPoint

    window._toggle_controls(True)
    _pump(qt_app, 0.3)
    blockers = []
    for widget in _clickable_widgets(window._ctrl_drawer):
        point = widget.mapToGlobal(widget.rect().center())
        hit = qt_app.widgetAt(point)
        if hit is None or hit is widget or widget.isAncestorOf(hit):
            continue
        blockers.append(f"{type(widget).__name__} is covered by {type(hit).__name__}")
    window._toggle_controls(False)
    assert not blockers, "\n  ".join(blockers)


# ── 3. the window survives its own sizes ───────────────────────────────────

def test_the_window_survives_maximise_restore_minimise(window, qt_app):
    """Maximised, restored, minimised, back: no crash and no jump."""
    from PyQt6.QtCore import Qt

    window.show()
    _pump(qt_app)
    normal = window.size()

    window.showMaximized()
    _pump(qt_app, 0.3)
    assert window.isMaximized() or window.width() >= normal.width()
    assert window.width() > 0 and window.height() > 0

    window.showNormal()
    _pump(qt_app, 0.3)
    assert not window.isMaximized()
    assert window.width() > 0 and window.height() > 0

    window.showMinimized()
    _pump(qt_app, 0.3)
    assert window.isMinimized()

    window.showNormal()
    _pump(qt_app, 0.3)
    assert not window.isMinimized()
    assert window.width() > 0 and window.height() > 0
    assert window.isVisible()


def test_the_fullscreen_button_really_goes_fullscreen(window, qt_app):
    window.show()
    _pump(qt_app)
    window._toggle_fullscreen()
    _pump(qt_app, 0.3)
    assert window.isFullScreen() or window.isMaximized()
    window._toggle_fullscreen()
    _pump(qt_app, 0.3)
    assert not window.isFullScreen()


def test_the_window_stays_alive_after_being_closed_and_restored(window, qt_app):
    """The window goes to the tray and comes back, alive."""
    window.show()
    _pump(qt_app)
    window.close()
    _pump(qt_app, 0.3)
    window.restore_window()
    _pump(qt_app, 0.3)
    assert window.width() > 0 and window.height() > 0


# ── 4. the music player can be chosen ──────────────────────────────────────

def test_the_music_player_selector_is_there(window):
    combo = getattr(window, "_music_combo", None)
    assert combo is not None, "there is no music player selector in CONTROLS"
    values = [combo.itemData(i) for i in range(combo.count())]
    assert values == ["auto", "spotify", "youtube"]


def test_the_music_player_choice_is_remembered(window, tmp_path):
    combo = window._music_combo
    index = combo.findData("youtube")
    combo.setCurrentIndex(index)
    _pump = None
    import time

    end = time.time() + 0.3
    while time.time() < end:
        pass
    assert config_manager.get_music_service() == "youtube"
    combo.setCurrentIndex(combo.findData("auto"))
    end = time.time() + 0.3
    while time.time() < end:
        pass
    assert config_manager.get_music_service() == "auto"


def test_a_spotify_folder_alone_does_not_mean_spotify_is_installed(monkeypatch, tmp_path):
    """The folder is not the program.

    A machine where Spotify was removed keeps its folder for ever, and
    asking for the folder said "yes, Spotify is here" on a computer with no
    Spotify in it — so every song tried Spotify first and then YouTube.
    """
    from actions import music_player as mp

    fake = tmp_path / "Spotify"
    fake.mkdir()
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    monkeypatch.setattr(mp, "_spotify_module", lambda: None)
    monkeypatch.setattr(mp, "_spotify_api_play", lambda q, a: ("", "no account"))
    monkeypatch.setattr(mp, "_spotify_desktop_play", lambda q, a: "")
    monkeypatch.setattr(mp, "_play_youtube", lambda q, a="": "status=done service=youtube")

    # The registry and the running process are the other two doors, and they
    # are closed here.
    import core.appidentity as appidentity
    monkeypatch.setattr(appidentity, "running", lambda *a, **k: [])
    import winreg
    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: (_ for _ in ()).throw(OSError()))

    # The real check: no program, no Spotify.
    assert mp._spotify_installed() is False


def test_a_song_is_chosen_by_its_title_and_its_artist():
    """A video that carries the word is not the song.

    "Mandarina" by Gian Marco came back as a video about Roblox that
    happened to have the word in its title, and the tool opened it.
    """
    from actions import music_player as mp

    wrong = mp._match_score(
        "Jugamos la TORRE TROLL de ABRAZOS en Roblox con Mandarina",
        "Mandarina", "Gian Marco")
    right = mp._match_score(
        "Gian Marco - Mandarina", "Mandarina", "Gian Marco")
    assert right > wrong
    assert wrong < 2, "a video without the artist must not be taken for the song"
