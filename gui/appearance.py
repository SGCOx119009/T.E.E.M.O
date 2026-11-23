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
    "_quiz_panel", "_footer",
    # The stack under the head: the notification, the viewer and the
    # questionnaire are its pages, and the stack itself is what has to go
    # in the corner mode. Hiding the pages one by one left the stack —
    # and the hole where a page used to be — sitting in the corner.
    "_lower_stack",
)

# THE PANELS THAT ONLY APPEAR WHEN THEY HAVE SOMETHING TO SAY.
#
# The camera preview and the clipboard panel are not furniture of the
# dashboard: they are there when the camera has taken a picture or when
# the user has copied something, and they are gone the rest of the time.
#
# They used to be in the list of the widgets that the dashboard SHOWS,
# and so they came up out of nowhere every time the program started,
# with nothing in them: the user asked what those two windows were.
# Now the appearance switch only ever hides them.
_TRANSIENT_PANELS = ("_cam_preview", "_clipboard_panel")

# The floating overlays — the setup one, the remote one, the customisation
# one. They are only HIDDEN by the core mode, never shown again: a modal
# that was closed must not come back.
_OVERLAYS = ("_overlay", "_remote_overlay", "_customize_overlay")


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
        # THE FULL SCREEN IS LEFT FIRST.
        #
        # A window that is full screen is the size of the screen, and the
        # window manager ignores every resize until the full screen is
        # over. So the corner window would keep the size of the wall — a
        # face as big as the desktop — and the jump would be exactly the
        # kind of thing the user complains about.
        try:
            if window.isFullScreen():
                window.showNormal()
        except Exception:
            pass
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
        self._central_background(transparent=False)
        self._show_dashboard_widgets(True)
        # The deck comes back if the user had it open before going to the
        # corner: the cards were never thrown away.
        deck = getattr(window, "_blades", None)
        if deck is not None:
            try:
                if deck.is_open():
                    deck.show()
                    deck.raise_()
            except Exception:
                pass
        hud = getattr(window, "hud", None)
        if hud is not None and hasattr(hud, "set_core_only"):
            hud.set_core_only(False)
        stack = getattr(window, "_hud_cam_stack", None)
        if stack is not None:
            try:
                stack.setCurrentIndex(0)
            except Exception:
                pass
        hud = getattr(window, "hud", None)
        if hud is not None:
            try:
                hud.setMinimumSize(300, 300)
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
            # If the user had the window on the whole screen, the dashboard
            # is where the whole screen comes back to.
            if getattr(window, "_was_fullscreen", False):
                window.showFullScreen()
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
        self._central_background(transparent=True)
        self._show_dashboard_widgets(False)
        self._hide_overlays()
        self._hide_transient()
        self._hide_deck()
        self._close_drawers()
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
        # The HUD asks for 300×300 of its own, and a fixed window cannot be
        # smaller than the thing inside it: without this the corner window
        # comes out at the HUD's minimum plus the margins, and the face ends
        # up off-centre in a rectangle it does not fill.
        hud = getattr(window, "hud", None)
        if hud is not None:
            try:
                hud.setMinimumSize(40, 40)
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
        self._hide_deck()
        self._close_drawers()
        self._hide_transient()
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

    def _hide_overlays(self) -> None:
        """The floating overlays go away in the corner and in the tray.

        They are only hidden, never shown again: a modal that the user
        closed must not come back on its own.
        """
        window = self._window
        for name in _OVERLAYS:
            widget = getattr(window, name, None)
            if widget is None:
                continue
            try:
                widget.hide()
            except Exception:
                pass

    def _hide_transient(self) -> None:
        """The panels that only appear when they have something to say.

        They are hidden in the corner and in the tray, and they are never
        shown by the appearance switch: a panel with nothing in it is
        worse than no panel at all.
        """
        window = self._window
        for name in _TRANSIENT_PANELS:
            widget = getattr(window, name, None)
            if widget is None:
                continue
            try:
                widget.hide()
            except Exception:
                pass

    def _hide_deck(self) -> None:
        """The deck of cards goes away in the corner and in the tray.

        The deck is part of the dashboard — a column of cards down the
        right of the interface — and in the corner there is no right side
        to put it in. It is only hidden: the cards stay in the deck, and
        the deck comes back with them.
        """
        window = self._window
        deck = getattr(window, "_blades", None)
        if deck is None:
            return
        try:
            deck.hide()
        except Exception:
            pass

    def _close_drawers(self) -> None:
        """The drawers go away with the dashboard.

        A drawer is a panel of glass with a button on it. In the corner
        there is no button to close it with, and a panel left floating over
        the desktop is a panel that eats the clicks of whatever is under
        it. So the drawer is closed here, and the button that opened it is
        un-pressed, so that the two always tell the same story.
        """
        window = self._window
        for name in ("_close_controls",):
            closer = getattr(window, name, None)
            if closer is None:
                continue
            try:
                closer()
            except Exception:
                pass

    def _central_background(self, transparent: bool) -> None:
        """The background of the central widget.

        In the dashboard mode the SCENE paints the background — a deep
        field with rings and dust — so the widget must not paint a flat
        colour over it. In the corner mode the scene is switched off and
        the widget is told to be transparent, so the DESKTOP is the
        background and the face floats on the wallpaper.
        """
        window = self._window
        central = window.centralWidget()
        scene = getattr(window, "_scene", None)
        if scene is not None:
            try:
                scene.set_scene_enabled(not transparent)
            except Exception:
                pass
        if central is None:
            return
        try:
            central.setStyleSheet("background: transparent;" if transparent else "")
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
