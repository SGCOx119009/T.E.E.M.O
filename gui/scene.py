"""
gui/scene.py — the world the assistant is projected into.

WHAT IT IS
    The background of the interface: a deep field with a grid on it, three
    broken rings turning at different speeds, dust in the air, the falloff
    towards the edges and the corner brackets. It is painted BEHIND the
    panels and the head, so the whole window reads as one projection rather
    than as a collection of widgets.

WHY IT IS A WIDGET AND NOT A STYLESHEET
    Because it moves. A stylesheet can draw a gradient and a border; it
    cannot draw rings that turn or dust that drifts.

THE COST
    The gradient and the grid are cached into one pixmap per size, and only
    the rings, the dust and the sweep are drawn per frame — twenty-odd
    primitives. The scene ticks at 20 frames a second, not 60: the
    background is slow, and the frame budget belongs to the face.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPixmap
from PyQt6.QtWidgets import QWidget

from gui import fx
from gui.theme import C


class SceneCanvas(QWidget):
    """The background of the interface."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._enabled = True
        self._t = 0.0
        self._cache: QPixmap | None = None
        self._cache_key = None

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)          # 20 fps: the background is slow

    # ── the switch ──────────────────────────────────────────────────────────

    def set_scene_enabled(self, enabled: bool) -> None:
        """Turn the scene off.

        In the corner mode the window is transparent and the DESKTOP is the
        background: painting a deep field there would hide the wallpaper
        behind a face that is supposed to float on it.
        """
        enabled = bool(enabled)
        if enabled == self._enabled:
            return
        self._enabled = enabled
        self.update()

    # ── the clock ───────────────────────────────────────────────────────────

    def _tick(self) -> None:
        if not self._enabled or not self.isVisible():
            return
        self._t += 0.05
        self.update()

    # ── the painting ────────────────────────────────────────────────────────

    def _base_pixmap(self, width: int, height: int) -> QPixmap:
        """The gradient and the grid, cached per size."""
        key = (width, height, C.BG, C.PRI)
        if self._cache is not None and self._cache_key == key:
            return self._cache
        pix = QPixmap(max(1, width), max(1, height))
        pix.fill(QColor(C.BG))
        p = QPainter(pix)
        rect = QRectF(0, 0, width, height)
        # The deep field: a colour that is almost black but not black, with
        # the centre a shade warmer so the head has something to sit in.
        grad = QLinearGradient(0, 0, 0, height)
        grad.setColorAt(0.0, QColor(C.DEEP_TOP))
        grad.setColorAt(0.55, QColor(C.BG))
        grad.setColorAt(1.0, QColor(C.DEEP_BOT))
        p.fillRect(rect, QBrush(grad))
        fx.grid(p, rect, C.PRI, spacing=46, alpha=10)
        p.end()
        self._cache = pix
        self._cache_key = key
        return pix

    def paintEvent(self, _):
        if not self._enabled:
            return
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        if W <= 2 or H <= 2:
            p.end()
            return
        rect = QRectF(0, 0, W, H)

        p.drawPixmap(0, 0, self._base_pixmap(W, H))

        accent = fx.accent_for(getattr(self, "state", "") or "")
        fx.orbit_arcs(p, rect, self._t, accent, alpha=70)
        fx.particles(p, rect, self._t, count=64, colour=accent)
        fx.sweep(p, rect, self._t, accent, period=9.0)
        fx.falloff(p, rect, strength=0.5)
        fx.scanlines(p, rect, accent, pitch=3, alpha=7)
        fx.brackets(p, rect, accent, size=46, alpha=170)
        p.end()
