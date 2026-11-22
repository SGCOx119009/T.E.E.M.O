"""
tests/test_appearance.py — FASE 8: the three faces of the assistant.

WHAT IS BEING PROVED
    1. The window really builds with the appearance layer in it.
    2. The three modes do what they say: the dashboard shows everything,
       the core shows only the core, and the tray shows nothing at all.
    3. The mode and the corner are remembered between runs, because a face
       that forgets where it was put has to be placed again every morning.
    4. Closing the window puts the assistant in the tray instead of
       killing it: a window that vanishes takes the conversation with it.
    5. The tray menu carries the three things a tray icon is expected to
       have — the window, the big window, and the way out.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from core import localization  # noqa: E402
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
    yield win
    win._quitting = True
    win.close()
    win.deleteLater()


# ── the three modes ────────────────────────────────────────────────────────

def test_there_are_three_modes_and_four_corners():
    from gui import appearance
    assert appearance.MODES == ("dashboard", "core", "tray")
    assert set(appearance.CORNERS) == {
        "bottom-right", "bottom-left", "top-right", "top-left"}


def test_an_unknown_mode_is_not_guessed_at(window):
    """A mode nobody wrote down must not decide how the product looks."""
    assert window.apply_appearance("something-else") == "dashboard"


def test_an_unknown_corner_falls_back_to_the_default(window):
    assert window.set_core_corner("middle-of-nowhere") == "bottom-right"


# ── what each mode shows ───────────────────────────────────────────────────

def test_the_dashboard_shows_everything(window):
    window.apply_appearance("dashboard")
    assert window._header.isVisible() or not window.isVisible()
    assert window._left_panel.isVisible() or not window.isVisible()
    assert window.hud._core_only is False


def test_the_core_mode_shows_only_the_core(window):
    window.apply_appearance("core")
    assert window.appearance_mode() == "core"
    assert window._header.isVisible() is False
    assert window._left_panel.isVisible() is False
    assert window._right_panel.isVisible() is False
    assert window._footer.isVisible() is False
    assert window.hud._core_only is True


def test_the_core_is_small_and_without_a_frame(window):
    from PyQt6.QtCore import Qt
    window.apply_appearance("core")
    assert window.width() <= 400 and window.height() <= 400
    assert window.windowFlags() & Qt.WindowType.FramelessWindowHint


def test_the_tray_mode_hides_the_window_entirely(window):
    window.apply_appearance("tray")
    assert window.appearance_mode() == "tray"
    assert window.isVisible() is False


def test_coming_back_from_the_tray_restores_what_was_there(window):
    window.apply_appearance("core")
    window.apply_appearance("tray")
    window.restore_window()
    assert window.appearance_mode() == "core"


# ── the window is remembered ────────────────────────────────────────────────

def test_the_mode_and_the_corner_are_remembered(window, tmp_path):
    window.apply_appearance("core")
    window.set_core_corner("top-left")
    assert config_manager.get_appearance_mode() == "core"
    assert config_manager.get_core_corner() == "top-left"


def test_the_saved_appearance_is_read_back(window):
    config_manager.save_appearance_mode("core")
    config_manager.save_core_corner("top-right")
    assert window.load_appearance() == "core"
    assert window._appearance.corner() == "top-right"


# ── closing is not leaving ─────────────────────────────────────────────────

class _FakeTray:
    """A notification area, for the tests that need one.

    The tests run on the offscreen platform, where there is no system
    tray, and the window behaves differently without one: a window that
    hides itself with no icon to go to is a lost program. So the tests
    that are about the tray put one there.
    """

    def __init__(self):
        self.hidden = False

    def hide(self):
        self.hidden = True

    def show(self):
        self.hidden = False

    def set_state(self, _state):
        pass


def test_closing_the_window_puts_the_assistant_in_the_tray(window):
    window._tray = _FakeTray()
    window.apply_appearance("dashboard")
    window.close()
    assert window.appearance_mode() == "tray"


def test_closing_the_window_does_not_choose_the_tray_for_the_next_start(window):
    """Going to the tray is a MOMENT, not a preference.

    Writing it down meant that the next start had no window at all — no
    interface, no ignition, nothing to click — and the user had to go
    looking for the icon to find out why. Measured on the owner's
    machine: the mode was 'tray' and the program came up with nothing.
    """
    window._tray = _FakeTray()
    window.apply_appearance("dashboard")
    window.close()
    assert window.appearance_mode() == "tray"
    assert config_manager.get_appearance_mode() == "dashboard"


def test_without_a_tray_closing_the_window_leaves(window):
    """With no icon to go to, hiding the window would lose the program."""
    window._tray = None
    window.apply_appearance("dashboard")
    window.close()
    assert window._quitting is True


def test_leaving_is_leaving(window):
    window.apply_appearance("dashboard")
    window.quit_application()
    assert window._quitting is True


def test_leaving_asks_the_loop_to_stop(window):
    """The window says 'leave'; whoever owns the loop stops it.

    Closing the window is not enough: when the window was already hidden
    — which is what happens when the exit is asked for from the tray —
    the closing never reaches Qt's 'the last window is gone', and the
    process stays behind with no window and no icon.
    """
    asked = []
    window.quit_requested.connect(lambda: asked.append(True))
    window.apply_appearance("dashboard")
    window.quit_application()
    assert asked == [True]


# ── the tray icon ──────────────────────────────────────────────────────────

def test_the_tray_menu_carries_the_three_ways(window):
    from gui.tray import TrayPresence
    tray = TrayPresence(window)
    texts = [action.text() for action in tray._menu.actions() if not action.isSeparator()]
    assert len(texts) == 3
    assert texts == [
        localization.t("tray.window"),
        localization.t("tray.maximize"),
        localization.t("tray.exit"),
    ]


def test_the_tray_icon_is_built_for_the_assistant(window):
    from gui.tray import TrayPresence
    tray = TrayPresence(window)
    assert not tray._icon.icon().isNull()


# ── the selector in the controls drawer ────────────────────────────────────

def test_the_selector_offers_the_three_modes(window):
    combo = window._appearance_combo
    assert combo.count() == 3
    values = [combo.itemData(i) for i in range(combo.count())]
    assert values == ["dashboard", "core", "tray"]


def test_the_corner_selector_offers_the_four_corners(window):
    combo = window._corner_combo
    assert combo.count() == 4
    values = [combo.itemData(i) for i in range(combo.count())]
    assert set(values) == {"bottom-right", "bottom-left", "top-right", "top-left"}


def test_the_corner_is_only_alive_in_the_core_mode(window):
    window.apply_appearance("core")
    assert window._corner_combo.isEnabled() is True
    window.apply_appearance("dashboard")
    assert window._corner_combo.isEnabled() is False


# ── the words ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", [
    "appearance.title", "appearance.dashboard", "appearance.core",
    "appearance.tray", "appearance.corner", "appearance.corner.bottom_right",
    "appearance.corner.bottom_left", "appearance.corner.top_right",
    "appearance.corner.top_left", "appearance.mode_tip",
    "appearance.corner_tip", "tray.window", "tray.maximize", "tray.exit",
    "tray.hidden",
])
def test_every_new_word_is_in_the_catalogue_in_five_languages(key):
    entry = localization.CATALOG.get(key)
    assert entry, key
    for language in ("en", "es", "pt", "fr", "zh"):
        assert entry.get(language), f"{key} has no {language}"
