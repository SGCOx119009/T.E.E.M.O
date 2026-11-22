"""
gui/appearance.py — the three faces of the assistant.

ONE WINDOW, THREE WAYS OF BEING SEEN
    dashboard : everything, maximised. The conversation, the vitals, the
                tasks, the log — the whole instrument panel, for a person
                who wants to see everything the assistant is doing.
    core     : only the core. The animated head, a little transparent, in
                the corner the user chose. No panels, no captions, no
                buttons — a face floating on the desktop, the size of a
                photograph.
    tray     : nothing on screen at all. Only the icon in the tray, and the
                assistant keeps listening, keeps answering, and says so
                from the icon's tooltip.

    The window is the SAME object in the three of them: the mode changes
    how it is shown, not what it is. A conversation started in the
    dashboard is still running in the corner, and the answer arrives the
    same way in both.

WHY THE MODE IS A SETTING AND NOT A WINDOW
    Two windows would mean two of everything: two heads, two logs, two
    microphones, two of the assistant's attention. One window that changes
    its shape keeps one of each, and the shape is the only thing that
    changes.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from gui.theme import (
    C,
    _DEFAULT_H,
    _DEFAULT_W,
    _MIN_H,
    _MIN_W,
)

MODES = ("dashboard", "core", "tray")
CORNERS = ("bottom-right", "bottom-left", "top-right", "top-left")
DEFAULT_MODE = "dashboard"
DEFAULT_CORNER = "bottom-right"

# The core is a small square. 300 is the HUD's own minimum, so the head
# keeps the room it was drawn for.
CORE_SIZE = (300, 300)
CORE_MARGIN = 24

# The widgets that make up the dashboard. In the core mode they are hidden;
# in the dashboard mode they are shown again.
_DASHBOARD_WIDGETS = (
    "_header", "_left_panel", "_right_panel", "_content_panel",
    "_quiz_panel", "_footer", "_cam_preview", "_clipboard_panel",
)


def _flag(widget, flag, on: bool) -> None:
    """Set a window flag, quietly.

    Changing a window flag on a visible window is ignored by the window
    manager, so the caller hides the window first and shows it again
    afterwards. That is the order `apply()` keeps.
    """
    try:
        widget.setWindowFlag(flag, on)
    except Exception:
        pass


class Appearance:
    """The window's way of being seen, and the setting that remembers it."""

    def __init__(self, window, on_exit=None):
        self._window = window
        self._on_exit = on_exit
        self._mode = ""
        self._corner = DEFAULT_CORNER
        # The mode the window was in before it went to the tray, so that
        # "the window" in the tray menu brings back what was there and not
        # something else.
        self._previous_mode = DEFAULT_MODE

    # ── the settings ────────────────────────────────────────────────────────

    def mode(self) -> str:
        """The mode in force: 'dashboard', 'core' or 'tray'."""
        return self._mode or DEFAULT_MODE

    def corner(self) -> str:
        """The corner the core sits in."""
        return self._corner

    def load(self) -> str:
        """Read the saved appearance and apply it.

        Called once at startup. A mode that is not one of the three is
        ignored rather than guessed at: an unknown value in a config file
        must not decide how the product looks.
        """
        mode, corner = DEFAULT_MODE, DEFAULT_CORNER
        try:
            from memory import config_manager
            mode = config_manager.get_appearance_mode()
            corner = config_manager.get_core_corner()
        except Exception:
            pass
        self._corner = corner if corner in CORNERS else DEFAULT_CORNER
        self.apply(mode, persist=False)
        return self.mode()

    def set_mode(self, mode: str, persist: bool = True) -> str:
        """Change the mode and remember it."""
        wanted = str(mode or "").strip().lower()
        if wanted not in MODES:
            wanted = DEFAULT_MODE
        return self.apply(wanted, persist=persist)

    def set_corner(self, corner: str, persist: bool = True) -> str:
        """Move the core to another corner and remember it."""
        wanted = str(corner or "").strip().lower()
        if wanted not in CORNERS:
            wanted = DEFAULT_CORNER
        self._corner = wanted
        if persist:
            try:
                from memory import config_manager
                config_manager.save_core_corner(wanted)
            except Exception:
                pass
        if self.mode() == "core":
            self._place_core()
        return self._corner

    def toggle(self) -> str:
        """Between the dashboard and the core.

        The tray is not part of the toggle: hiding the assistant entirely
        is something the user asks for, not something that happens because
        they clicked twice.
        """
        return self.set_mode("core" if self.mode() == "dashboard" else "dashboard")

    def restore(self) -> str:
        """Bring the window back from the tray, as it was."""
        previous = self._previous_mode
        if previous not in ("dashboard", "core"):
            previous = DEFAULT_MODE
        return self.apply(previous)

    # ── applying ────────────────────────────────────────────────────────────

    def apply(self, mode: str, persist: bool = True) -> str:
        """Put the window into a mode.

        The mode is remembered, because the remembered mode is what the
        window does at the next start — and a window that shows one face
        now and another one tomorrow is a window the user cannot trust.
        """
        wanted = str(mode or "").strip().lower()
        if wanted not in MODES:
            wanted = DEFAULT_MODE
        window = self._window
        try:
            window.hide()
        except Exception:
            pass
        if wanted == "dashboard":
            self._dashboard()
        elif wanted == "core":
            self._core()
        else:
            self._tray()
        self._mode = wanted
        if persist:
            try:
                from memory import config_manager
                config_manager.save_appearance_mode(wanted)
            except Exception:
                pass
        return self.mode()

    # ---- the three shapes --------------------------------------------------

    def _dashboard(self) -> None:
        window = self._window
        _flag(window, Qt.WindowType.FramelessWindowHint, False)
        _flag(window, Qt.WindowType.WindowStaysOnTopHint, False)
        try:
            window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        except Exception:
            pass
        self._central_background(f"background: {C.BG};")
        self._show_dashboard_widgets(True)
        hud = getattr(window, "hud", None)
        if hud is not None and hasattr(hud, "set_core_only"):
            hud.set_core_only(False)
        stack = getattr(window, "_hud_cam_stack", None)
        if stack is not None:
            try:
                stack.setCurrentIndex(0)
            except Exception:
                pass
        try:
            window.setMinimumSize(_MIN_W, _MIN_H)
            window.setMaximumSize(16777215, 16777215)
            window.resize(_DEFAULT_W, _DEFAULT_H)
        except Exception:
            pass
        try:
            window.showMaximized()
        except Exception:
            window.show()

    def _core(self) -> None:
        window = self._window
        _flag(window, Qt.WindowType.FramelessWindowHint, True)
        _flag(window, Qt.WindowType.WindowStaysOnTopHint, True)
        try:
            window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        except Exception:
            pass
        self._central_background("background: transparent;")
        self._show_dashboard_widgets(False)
        hud = getattr(window, "hud", None)
        if hud is not None and hasattr(hud, "set_core_only"):
            hud.set_core_only(True)
        stack = getattr(window, "_hud_cam_stack", None)
        if stack is not None:
            try:
                stack.setCurrentIndex(0)
            except Exception:
                pass
        try:
            window.setFixedSize(*CORE_SIZE)
        except Exception:
            pass
        self._place_core()
        try:
            window.show()
            window.raise_()
            window.activateWindow()
        except Exception:
            pass

    def _tray(self) -> None:
        """Nothing on screen. The window stays hidden, so the taskbar keeps
        no button for it — the tray icon is the whole of its presence."""
        if self._mode and self._mode != "tray":
            self._previous_mode = self._mode
        try:
            self._window.hide()
        except Exception:
            pass

    # ---- helpers ------------------------------------------------------------

    def _show_dashboard_widgets(self, visible: bool) -> None:
        window = self._window
        for name in _DASHBOARD_WIDGETS:
            widget = getattr(window, name, None)
            if widget is None:
                continue
            try:
                widget.setVisible(bool(visible))
            except Exception:
                pass

    def _central_background(self, style: str) -> None:
        window = self._window
        central = window.centralWidget()
        if central is None:
            return
        try:
            central.setStyleSheet(style)
        except Exception:
            pass

    def _place_core(self) -> None:
        """Put the core in the corner the user chose."""
        window = self._window
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        width = window.width() or CORE_SIZE[0]
        height = window.height() or CORE_SIZE[1]
        if self._corner.endswith("right"):
            x = area.right() - width - CORE_MARGIN + 1
        else:
            x = area.left() + CORE_MARGIN
        if self._corner.startswith("bottom"):
            y = area.bottom() - height - CORE_MARGIN + 1
        else:
            y = area.top() + CORE_MARGIN
        try:
            window.move(int(x), int(y))
        except Exception:
            pass
