"""
gui/fx.py — the visual language of the interface, in one place.

WHAT THIS IS
    The look. Everything that draws the futuristic part of the interface
    draws it through this module, so the rules are written once: the accent
    that follows the state, the scanlines, the corner brackets, the
    chamfered panels, the particles, the glitch.

WHY A MODULE OF ITS OWN
    Because the same five rules were about to be written in six places, and
    six copies of a rule is five rules too many. A rule that lives in one
    place can be changed in one place, and can be tested in one place.

THE RULES, AND WHERE EACH ONE COMES FROM
    - **The accent follows the state.** The whole interface is one colour
      that changes with what the assistant is doing, and the HUD, the
      panels and the brackets all inherit it. The reference HUD in the
      AGENTES folder re-declares its `--accent` per phase and the whole
      screen follows it; here it is a function, because a phase is a value,
      not a CSS variable.
    - **Scanlines every three pixels**, barely there. The screen is a
      projection, not a window.
    - **Corner brackets instead of a frame.** The interface is aimed, not
      boxed in.
    - **Chamfered panels.** Glass cut at the corners, with the light on the
      edge — the shape the HUD has been drawing around its own screen since
      the beginning (`gui/hud.py::_make_grid`).
    - **Particles.** The air in the room is not empty.

THE COST
    All of it is `QPainter`: software, no GPU, no OpenGL. The scene's
    background is cached into one pixmap per size, and only the moving
    parts are redrawn per frame.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import (
    QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient,
)

from gui.theme import C

# ── colour ──────────────────────────────────────────────────────────────────

def qcol(colour: str, alpha: int | None = None) -> QColor:
    """A colour from the palette, optionally with an alpha."""
    c = QColor(colour)
    if alpha is not None:
        c.setAlpha(max(0, min(255, int(alpha))))
    return c


# The accent of each state. This is the table that makes the whole interface
# change colour with what the assistant is doing, and it is deliberately
# small: a state with no colour of its own keeps the interface's own.
_STATE_ACCENT = {
    "LISTENING":   C.PRI,
    "SPEAKING":    C.ACC,
    "THINKING":    C.ACC2,
    "PROCESSING":  C.ACC2,
    "IDLE":        C.TEXT_MED,
    "INITIALISING": C.PRI_DIM,
    "MUTED":       C.MUTED_C,
}


def accent_for(state: str) -> str:
    """The accent colour of a state."""
    return _STATE_ACCENT.get(str(state or "").upper(), C.PRI)


# ── the rules ──────────────────────────────────────────────────────────────

def scanlines(p: QPainter, rect: QRectF, colour: str = C.PRI,
              pitch: int = 3, alpha: int = 8) -> None:
    """The faint horizontal lines that make a screen a projection.

    Drawn as one rectangle per line, which is cheap enough to do every
    frame: at a 700-pixel-tall window that is 233 rectangles, and Qt draws
    a filled rectangle faster than it draws a line.
    """
    colour_obj = qcol(colour, alpha)
    y = rect.top()
    step = max(2, int(pitch))
    while y < rect.bottom():
        p.fillRect(QRectF(rect.left(), y, rect.width(), 1), colour_obj)
        y += step


def falloff(p: QPainter, rect: QRectF, strength: float = 0.55) -> None:
    """The darkening towards the edges.

    Without it the interface sits on flat black and reads as a page; with
    it, it reads as something projected into a room.
    """
    grad = QRadialGradient(rect.center(), max(rect.width(), rect.height()) * 0.62)
    grad.setColorAt(0.0, QColor(0, 0, 0, 0))
    grad.setColorAt(0.55, QColor(0, 0, 0, int(90 * strength)))
    grad.setColorAt(1.0, QColor(0, 0, 0, int(255 * strength)))
    p.fillRect(rect, QBrush(grad))


def brackets(p: QPainter, rect: QRectF, colour: str = C.PRI,
              size: float = 46, width: float = 1.0,
              alpha: int = 190) -> None:
    """The four corner brackets.

    They replace a frame. A frame says "this is a window"; brackets say
    "this is aimed at something".
    """
    pen = QPen(qcol(colour, alpha))
    pen.setWidthF(width)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    x, y = rect.left(), rect.top()
    w, h = rect.width(), rect.height()
    s = size
    for corner in range(4):
        if corner in (0, 1):        # top
            p.drawLine(int(x), int(y), int(x + (s if corner == 0 else -s)), int(y))
        if corner in (0, 2):        # left
            p.drawLine(int(x), int(y), int(x), int(y + (s if corner == 0 else -s)))
        if corner in (1, 3):        # bottom
            p.drawLine(int(x), int(y + h), int(x + (s if corner == 1 else -s)), int(y + h))
        if corner in (2, 3):        # right
            p.drawLine(int(x + w), int(y), int(x + w), int(y + (s if corner == 2 else -s)))


def chamfer(rect: QRectF, cut: float = 12.0) -> QPainterPath:
    """A rectangle with its corners cut off.

    The shape of a panel that is glass rather than paper: a chamfer catches
    the light on two edges and leaves the other two in shadow.
    """
    path = QPainterPath()
    x, y = rect.left(), rect.top()
    w, h = rect.width(), rect.height()
    c = min(cut, w / 3.0, h / 3.0)
    path.moveTo(x + c, y)
    path.lineTo(x + w - c, y)
    path.lineTo(x + w, y + c)
    path.lineTo(x + w, y + h - c)
    path.lineTo(x + w - c, y + h)
    path.lineTo(x + c, y + h)
    path.lineTo(x, y + h - c)
    path.lineTo(x, y + c)
    path.closeSubpath()
    return path


def glass_panel(p: QPainter, rect: QRectF, fill: str = C.PANEL,
                edge: str = C.PRI, cut: float = 12.0,
                glow: float = 0.55, alpha: int = 235) -> None:
    """A chamfered plate of glass with a lit edge.

    The fill is nearly opaque so the text on it stays readable; the EDGE is
    what carries the light, and it is brighter on the top-left, the way a
    real bevel catches a light from above.
    """
    path = chamfer(rect, cut)
    p.fillPath(path, qcol(fill, alpha))
    pen = QPen(qcol(edge, int(255 * glow)))
    pen.setWidthF(1.0)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    # The lit bevel: the two edges that face the light.
    p.setPen(QPen(qcol(edge, int(255 * glow * 0.9)), 1.4))
    p.drawLine(int(rect.left() + cut), int(rect.top()),
               int(rect.right() - cut), int(rect.top()))
    p.setPen(QPen(qcol(edge, int(255 * glow * 0.35)), 1.0))
    p.drawLine(int(rect.left()), int(rect.top() + cut),
               int(rect.left()), int(rect.bottom() - cut))


def ruler(p: QPainter, rect: QRectF, colour: str = C.PRI,
          count: int = 24, alpha: int = 70) -> None:
    """The tick marks along an edge — the scale of an instrument."""
    pen = QPen(qcol(colour, alpha))
    pen.setWidthF(1.0)
    p.setPen(pen)
    step = rect.width() / max(1, count)
    for i in range(count + 1):
        x = rect.left() + i * step
        long = (i % 5 == 0)
        p.drawLine(int(x), int(rect.top()),
                   int(x), int(rect.top() + (9 if long else 4)))


def _hash01(i: int, seed: int) -> float:
    """A stable number in [0,1) from an index — the same particles every run."""
    x = math.sin((i + 1) * 12.9898 + seed * 78.233) * 43758.5453
    return x - math.floor(x)


def particles(p: QPainter, rect: QRectF, t: float, count: int = 70,
              colour: str = C.PRI, seed: int = 7) -> None:
    """The dust in the air.

    Deterministic: the same dust every run, drifting upward at different
    speeds, twinkling. It is the cheapest thing in the interface and the
    one that makes it look alive.
    """
    p.setPen(Qt.PenStyle.NoPen)
    for i in range(count):
        px = _hash01(i, seed)
        py = _hash01(i, seed + 11)
        sp = 0.4 + _hash01(i, seed + 23) * 1.4
        size = 0.7 + _hash01(i, seed + 37) * 1.8
        x = rect.left() + px * rect.width()
        y = rect.bottom() - ((py * rect.height()) + t * sp * 26.0) % (rect.height() + 40)
        if y < rect.top() - 6:
            continue
        twinkle = 0.35 + 0.65 * abs(math.sin(t * 0.9 + i))
        p.setBrush(QColor(qcol(colour, int(120 * twinkle)).red(),
                          qcol(colour, int(120 * twinkle)).green(),
                          qcol(colour, int(120 * twinkle)).blue(),
                          int(120 * twinkle)))
        p.drawEllipse(QRectF(x, y, size, size))


def orbit_arcs(p: QPainter, rect: QRectF, t: float, colour: str = C.PRI,
                alpha: int = 90) -> None:
    """Three broken rings turning at different speeds.

    The assistant's rings are not decoration: they say that something is
    running, and the gap in each one says how far through it is.
    """
    cx, cy = rect.center().x(), rect.center().y()
    base = min(rect.width(), rect.height()) * 0.42
    for index, (scale, speed, segments) in enumerate(
            ((1.0, 0.22, 5), (0.82, -0.33, 4), (1.18, 0.13, 6))):
        radius = base * scale
        pen = QPen(qcol(colour, int(alpha * (1.0 - index * 0.22))))
        pen.setWidthF(1.2)
        p.setPen(pen)
        span = 360.0 / segments
        for s in range(segments):
            start = (s * span + t * 40 * speed) % 360.0
            p.drawArc(QRectF(cx - radius, cy - radius, radius * 2, radius * 2),
                      int(start * 16), int(span * 0.62 * 16))


def grid(p: QPainter, rect: QRectF, colour: str = C.PRI,
         spacing: int = 46, alpha: int = 12) -> None:
    """The fine grid the interface is projected on."""
    pen = QPen(qcol(colour, alpha))
    pen.setWidthF(1.0)
    p.setPen(pen)
    x = rect.left()
    while x < rect.right():
        p.drawLine(int(x), int(rect.top()), int(x), int(rect.bottom()))
        x += spacing
    y = rect.top()
    while y < rect.bottom():
        p.drawLine(int(rect.left()), int(y), int(rect.right()), int(y))
        y += spacing


def sweep(p: QPainter, rect: QRectF, t: float, colour: str = C.PRI,
          period: float = 5.0) -> None:
    """The band of light that travels down the projection.

    It is what makes a still image read as a hologram: the light passes
    through it and it is never the same twice.
    """
    if rect.height() <= 4:
        return
    position = (t % period) / period
    y = rect.top() + position * rect.height()
    height = max(24.0, rect.height() * 0.12)
    grad = QLinearGradient(0, y - height, 0, y + height)
    grad.setColorAt(0.0, QColor(0, 0, 0, 0))
    grad.setColorAt(0.5, qcol(colour, 40))
    grad.setColorAt(1.0, QColor(0, 0, 0, 0))
    p.fillRect(rect, QBrush(grad))


def glitch(p: QPainter, rect: QRectF, draw, amount: float = 6.0,
           colour: str = C.PRI) -> None:
    """Draw something three times, in red, green and blue, a few pixels apart.

    The aberration of a camera that cannot quite keep up. Only used when
    something goes wrong, because it costs three draws of whatever it
    wraps.
    """
    if amount <= 0.5:
        draw(p, 0.0)
        return
    for offset, tint in ((-amount, QColor(255, 0, 40, 90)),
                          (0.0, QColor(0, 255, 120, 90)),
                          (amount, QColor(40, 90, 255, 90))):
        p.save()
        p.translate(offset, 0)
        draw(p, tint)
        p.restore()


def label(text: str, colour: str = C.PRI, alpha: int = 200) -> QColor:
    """The colour of a HUD label — the interface's own dim cyan."""
    return qcol(colour, alpha)
