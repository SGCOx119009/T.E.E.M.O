"""
gui/floating.py — the thing that stays on top.

WHAT IT IS
    A small panel of glass that floats over everything else, with no
    window frame, that shows the assistant's state and the last thing it
    said. It is the piece that makes the assistant feel like it belongs to
    the machine and not to the program: it does not go behind the browser
    when you open the browser.

WHY IT EXISTS
    Because the interesting part of the assistant is not the dashboard —
    it is the moment it answers while you are doing something else. A
    dashboard can only be looked at when you go to it. This one comes to
    you.

IT IS DRAGGED BY THE HAND
    The panel follows the pointer while the pointer's button is down, so
    the gesture control can pick it up and put it wherever it belongs: the
    same press-and-move the mouse does, only with a pinch instead of a
    finger on a desk.

THE COST
    One top-level window, drawn with `QPainter`, that ticks only when
    something in it changed. It has no timers of its own; the state and
    the text are pushed into it from outside.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from gui import fx
from gui.style import mono_font, px_font
from gui.theme import C

WIDTH = 330
HEIGHT = 132


class FloatingPanel(QWidget):
    """The overlay that floats over everything."""

    def __init__(self, parent=None):
        super().__init__(None)          # its own top-level window
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(WIDTH, HEIGHT)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

        self._state = "IDLE"
        self._accent = C.PRI
        self._line = ""
        self._detail = ""
        self._t = 0.0
        self._drag_from: tuple[int, int] | None = None
        self._offset = (0, 0)
        self._pulse = 0.0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(80)          # the rings turn slowly

    # ── what it shows ───────────────────────────────────────────────────────

    def set_state(self, state: str) -> None:
        """The state of the assistant, and the colour that goes with it."""
        state = str(state or "IDLE")
        if state == self._state:
            return
        self._state = state
        self._accent = fx.accent_for(state)
        self._pulse = 1.0
        self.update()

    def set_line(self, line: str, detail: str = "") -> None:
        """The last thing the assistant said."""
        line = str(line or "")
        detail = str(detail or "")
        if line == self._line and detail == self._detail:
            return
        self._line = line
        self._detail = detail
        self.update()

    def state_text(self) -> str:
        return self._state

    # ── where it lives ──────────────────────────────────────────────────────

    def place(self, x: int, y: int) -> None:
        """Put it somewhere, without going off the screen."""
        screen = self.screen() or None
        if screen is None:
            from PyQt6.QtWidgets import QApplication
            screen = QApplication.primaryScreen()
        if screen is None:
            self.move(int(x), int(y))
            return
        area = screen.availableGeometry()
        x = max(area.left(), min(int(x), area.right() - self.width()))
        y = max(area.top(), min(int(y), area.bottom() - self.height()))
        self.move(int(x), int(y))

    def move_to_default(self) -> None:
        """The corner it starts in: the bottom right, where nothing else is."""
        from PyQt6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.place(area.right() - self.width() - 24, area.bottom() - self.height() - 24)

    # ── the clock ───────────────────────────────────────────────────────────

    def _tick(self) -> None:
        self._t += 0.08
        if self._pulse > 0.0:
            self._pulse = max(0.0, self._pulse - 0.05)
        self.update()

    # ── the painting ────────────────────────────────────────────────────────

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        try:
            self._paint(p)
        finally:
            # THE PAINTER ALWAYS ENDS. A drawing that raises leaves the
            # painter open, and Qt complains about it once per frame:
            # 'QBackingStore::endPaint() called with active painter'.
            p.end()

    def _paint(self, p: QPainter) -> None:
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        rect = QRectF(0.5, 0.5, W - 1, H - 1)

        path = fx.chamfer(rect, 14)
        p.fillPath(path, fx.qcol(C.DEEP_TOP, 235))
        p.setPen(QPen(fx.qcol(self._accent, 200), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

        # The rings, turning, on the left.
        cx, cy = 34.0, H / 2.0
        for scale, speed, segments in ((1.0, 0.5, 5), (0.7, -0.8, 4)):
            radius = 22.0 * scale
            pen = QPen(fx.qcol(self._accent, 150), 1.1)
            p.setPen(pen)
            span = 360.0 / segments
            for s in range(segments):
                start = (s * span + self._t * 60 * speed) % 360.0
                p.drawArc(QRectF(cx - radius, cy - radius, radius * 2, radius * 2),
                          int(start * 16), int(span * 0.6 * 16))
        # The core, which flares when the state changes.
        flare = 3.0 + self._pulse * 5.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(fx.qcol(self._accent, 220)))
        p.drawEllipse(QRectF(cx - flare, cy - flare, flare * 2, flare * 2))

        # The state, in caps, on the right of the rings.
        p.setFont(px_font(11, spacing=2.6))
        p.setPen(QPen(QColor(self._accent)))
        p.drawText(QRectF(66, 14, W - 80, 18),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self._state[:22])

        # The last line.
        p.setFont(px_font(11))
        p.setPen(QPen(QColor(C.TEXT)))
        line = self._line or "—"
        p.drawText(QRectF(66, 36, W - 80, 40),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                   p.fontMetrics().elidedText(line, Qt.TextElideMode.ElideRight, W - 90))

        # The detail, dimmer, under it.
        if self._detail:
            p.setFont(mono_font(9))
            p.setPen(QPen(QColor(C.TEXT_DIM)))
            p.drawText(QRectF(66, H - 30, W - 80, 16),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       p.fontMetrics().elidedText(self._detail, Qt.TextElideMode.ElideRight,
                                                  W - 90))

        # The scale along the bottom, and the brackets: it is an instrument.
        fx.ruler(p, QRectF(12, H - 8, W - 24, 6), self._accent, count=16, alpha=40)
        fx.brackets(p, QRectF(4, 4, W - 8, H - 8), self._accent, size=12, alpha=120)

        # The close cross, in the corner.
        p.setPen(QPen(fx.qcol(C.TEXT_DIM, 200), 1.2))
        p.drawLine(W - 16, 10, W - 10, 16)
        p.drawLine(W - 16, 16, W - 10, 10)

    # ── the dragging ────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        """Press to pick it up, press on the cross to close it."""
        pos = event.position()
        if pos.x() > self.width() - 22 and pos.y() < 22:
            self.hide()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_from = event.globalPosition().toPoint()
            self._offset = (self.x(), self.y())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_from is None:
            return
        now = event.globalPosition().toPoint()
        dx = now.x() - self._drag_from.x()
        dy = now.y() - self._drag_from.y()
        self.place(self._offset[0] + dx, self._offset[1] + dy)

    def mouseReleaseEvent(self, event):
        self._drag_from = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        """A double click hides it."""
        self.hide()
        super().mouseDoubleClickEvent(event)
