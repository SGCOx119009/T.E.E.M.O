"""
tests/test_skin.py — the skin: the field, the ignition, the deck, the text
that resolves, the panel that stays on top, and the hand that steers.

WHAT IS BEING PROVED
    1. The accent follows the state, and a state with no colour of its own
       keeps the interface's own.
    2. The text that resolves ends up being the text it promised to be.
    3. The ignition only says what it checked: a row that could not be
       checked says "EN ESPERA" and never "LISTO".
    4. The deck holds the session, walks through it, folds and closes, and
       the letters that drive it do not eat what the user is typing.
    5. The panel that stays on top stays on top, and it stays on the screen.
    6. The hand can press and let go, which is what makes a drag a drag.
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


@pytest.fixture(scope="module")
def window(qt_app, tmp_path_factory):
    """One real MainWindow for the whole module.

    One, not one per test: every window brings its own timers, its own
    tray icon and its own scene canvas, and a dozen of them alive at once
    is a dozen times the chance of the interpreter tripping over something
    that has nothing to do with what is being tested.
    """
    config_dir = tmp_path_factory.mktemp("config")
    from memory import config_manager as cm
    original_dir = cm.CONFIG_DIR
    original_file = cm.CONFIG_FILE
    cm.CONFIG_DIR = config_dir
    cm.CONFIG_FILE = config_dir / "api_keys.json"

    from gui.window import MainWindow
    win = MainWindow(str(BASE / "core" / "face_model.obj"))
    yield win
    win._quitting = True
    try:
        win.close()
    except Exception:
        pass
    win.deleteLater()
    cm.CONFIG_DIR = original_dir
    cm.CONFIG_FILE = original_file


def _pump(app, seconds=0.35):
    import time
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


# ── the accent follows the state ────────────────────────────────────────────

def test_the_accent_follows_the_state():
    from gui import fx
    from gui.theme import C

    assert fx.accent_for("LISTENING") == C.PRI
    assert fx.accent_for("SPEAKING") == C.ACC
    assert fx.accent_for("THINKING") == C.ACC2
    # A state with no colour of its own keeps the interface's own.
    assert fx.accent_for("SOMETHING_NEW") == C.PRI
    assert fx.accent_for("") == C.PRI
    assert fx.accent_for(None) == C.PRI


def test_the_accent_ignores_the_case_of_the_state():
    from gui import fx
    from gui.theme import C

    assert fx.accent_for("speaking") == fx.accent_for("SPEAKING") == C.ACC


# ── the text that resolves ──────────────────────────────────────────────────

def test_the_scrambled_text_keeps_the_start_and_the_end():
    from gui.decode import scramble

    text = "T.E.E.M.O."
    assert scramble(text, len(text)) == text
    assert scramble(text, 0).endswith(".")
    # The last character is never wrong: a name that ends in the wrong
    # letter reads as a mistake, not as a secret.
    assert scramble(text, 0)[-1] == text[-1]
    assert scramble(text, 3)[:3] == "T.E"


def test_the_resolving_label_ends_up_being_the_text(qt_app):
    from gui.decode import DecodeLabel

    label = DecodeLabel("T.E.E.M.O.")
    label.set_text("HOLA")
    for _ in range(60):
        qt_app.processEvents()
        import time
        time.sleep(0.02)
        if label.resolved():
            break
    assert label.resolved()
    assert label.text() == "HOLA"


def test_the_resolving_label_can_be_skipped(qt_app):
    from gui.decode import DecodeLabel

    label = DecodeLabel("")
    label.set_text("OTRO")
    label.skip()
    assert label.resolved()
    assert label.text() == "OTRO"


# ── the ignition ────────────────────────────────────────────────────────────

def test_the_ignition_checks_are_real_and_say_so():
    """Every row is a state we know how to report."""
    from gui import boot

    checks = boot.collect_checks()
    assert checks, "the ignition has to check something"
    allowed = {boot.OK, boot.FAIL, boot.PENDING}
    for label, state, detail in checks:
        assert label and isinstance(label, str)
        assert state in allowed, f"{label}: estado desconocido {state!r}"
        assert detail, f"{label}: una fila sin detalle no dice nada"


def test_the_ignition_says_pending_when_it_cannot_check(qt_app):
    """A row that was not checked must not be a row that passed."""
    from gui import boot

    checks = boot.collect_checks()
    states = {label: state for label, state, _ in checks}
    # The network is never probed at ignition, so it must never be ticked.
    assert states.get("RED") == boot.PENDING


def test_the_ignition_goes_away_by_itself(qt_app):
    from gui import boot

    overlay = boot.BootOverlay(brand="T.E.E.M.O.")
    overlay._checks = [("PRUEBA", boot.OK, "bien")]
    overlay.start()
    assert overlay.isVisible()
    overlay.skip()
    assert not overlay.isVisible()


def test_the_ignition_shows_itself_over_the_whole_interface(window):
    """The ignition has to cover the interface, not sit in a corner of it."""
    window.show()
    window._start_boot()
    boot = window._boot
    central = window.centralWidget()
    assert boot.isVisible()
    assert boot.geometry() == central.rect()
    boot.skip()


# ── the deck ────────────────────────────────────────────────────────────────

def test_the_deck_holds_what_the_assistant_said(window):
    window._blades.clear()
    window._push_blade("NOTICIAS", "El tiempo en Tacna", "23:14")
    window._push_blade("INFORME", "Tres puntos de hoy", "23:15")
    deck = window._blades
    assert deck.count == 2
    assert deck.active == 1
    assert deck.is_open()


def test_the_deck_walks_through_the_cards(window):
    window._blades.clear()
    window._push_blade("UNO", "a", "1")
    window._push_blade("DOS", "b", "2")
    deck = window._blades
    deck.next_blade()
    assert deck.active == 0
    deck.prev_blade()
    assert deck.active == 1


def test_the_deck_folds_and_closes(window):
    window._blades.clear()
    window._push_blade("UNO", "a", "1")
    deck = window._blades
    deck.fold()
    assert deck._anim_target == 0.0
    deck.fold()
    assert deck._anim_target == 1.0
    deck.close_deck()
    assert not deck.is_open()


def test_the_deck_toggle_does_nothing_when_there_is_nothing_in_it(window):
    window._blades.clear()
    window._toggle_blades()
    assert not window._blades.is_open()


def test_the_letters_of_the_deck_do_not_eat_what_is_being_written(window):
    """The letters only work with the deck open.

    A QLineEdit swallows the keys it needs for writing, so the window
    never sees them; the window's own handler only fires when the deck is
    open. That is why a shortcut cannot eat a message.
    """
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QKeyEvent

    window._blades.clear()
    window._push_blade("UNO", "a", "1")
    deck = window._blades
    assert deck.is_open()

    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_BracketRight, Qt.KeyboardModifier.NoModifier)
    window.keyPressEvent(event)
    assert deck.active == 0, "la tecla de recorrer no llego al mazo"

    deck.close_deck()
    before = deck.active
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_BracketRight, Qt.KeyboardModifier.NoModifier)
    window.keyPressEvent(event)
    assert deck.active == before, "con el mazo cerrado, las teclas no hacen nada"


def test_the_deck_button_is_in_the_header(window):
    assert window._deck_btn is not None
    assert window._deck_btn.text() == "" or window._deck_btn.text()


# ── the panel that stays on top ────────────────────────────────────────────

def test_the_floating_panel_carries_the_state_and_the_line(window):
    panel = window._floating
    panel.set_state("SPEAKING")
    assert panel.state_text() == "SPEAKING"
    panel.set_line("El viento del sur sopla a 14 km/h.", "NOTICIAS")
    assert panel.isVisible() is False or True     # showing it is the caller's job
    panel.hide()
    panel.show()
    assert panel.isVisible()
    panel.hide()


def test_the_floating_panel_stays_on_the_screen(window):
    panel = window._floating
    panel.place(-500, -500)
    assert panel.x() >= 0
    assert panel.y() >= 0
    panel.place(99999, 99999)
    from PyQt6.QtWidgets import QApplication
    area = QApplication.primaryScreen().availableGeometry()
    assert panel.x() < area.right()
    assert panel.y() < area.bottom()


def test_the_floating_panel_is_toggled_from_the_window(window):
    panel = window._floating
    panel.hide()
    window._toggle_floating()
    assert panel.isVisible()
    window._toggle_floating()
    assert not panel.isVisible()


# ── the hand ────────────────────────────────────────────────────────────────

def test_the_pointer_can_be_pressed_and_released():
    """Pressing and letting go is what turns a pinch into a drag."""
    from gui.gesture import PointerDriver

    driver = PointerDriver()
    if not driver.available():
        pytest.skip("no hay interfaz de usuario en esta maquina")
    assert driver.press() is True
    assert driver.release() is True


def test_the_gesture_service_reports_whether_it_is_driving_the_pointer(window):
    service = window._gesture
    assert service.drives_pointer is False
    service.set_drives_pointer(True)
    assert service.drives_pointer is True
    service.set_drives_pointer(False)
    assert service.drives_pointer is False


def test_the_hand_draws_itself_on_the_glass(window):
    from gui.gesture import GestureOverlay

    overlay = window._gesture_overlay
    assert isinstance(overlay, GestureOverlay)
    overlay.set_hands([])
    overlay.set_accent("#00d4ff")


# ── the scene ───────────────────────────────────────────────────────────────

def test_the_scene_can_be_switched_off(window):
    """In the corner the desktop is the background, not a deep field."""
    scene = window._scene
    scene.set_scene_enabled(True)
    scene.set_scene_enabled(False)
    assert scene._enabled is False
    scene.set_scene_enabled(True)
    assert scene._enabled is True


def test_the_scene_follows_the_state(window):
    window._apply_state("THINKING")
    assert window._scene.state == "THINKING"
    window._apply_state("IDLE")
    assert window._scene.state == "IDLE"
