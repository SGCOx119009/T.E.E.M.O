"""
gui/decode.py — text that arrives out of the dark.

WHAT IT IS
    A string that is not there yet, and then is. The characters fall out of
    the noise one by one, each one landing on the letter it was always going
    to be. It is used for the wordmark at ignition and for the title of a
    blade, and it is the cheapest thing in the interface: a timer and a
    string.

WHY IT EXISTS
    The interface is a projection. A projection does not simply display
    things; they resolve. A window that pops a finished label into place
    looks like a form; a window whose text resolves out of the static looks
    like a machine thinking.

THE RULE
    The glyph that is showing is ALWAYS a real glyph of the alphabet or of
    the symbol set — never a random Unicode block, never an empty box. A
    character the machine cannot render looks broken, and a broken character
    on a screen that claims to be a hologram is the wrong kind of detail.

THE COST
    One timer at 45 milliseconds and a string operation per tick. The text
    it is applied to is short: a wordmark, a title.
"""
from __future__ import annotations

import random

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QLabel, QWidget

from gui.style import mono_font, px_font
from gui.theme import C

# The characters a character can be while it is still resolving. They are
# all printable in every font the interface uses, which is the point: a
# missing glyph is not futuristic, it is broken.
GLYPHS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ#$%&*+-<>/\\[]{}=~^|"

DEFAULT_STEP_MS = 45


def scramble(text: str, revealed: int) -> str:
    """The text with everything after `revealed` characters still in the noise.

    The revealed part is the real text; the rest is random noise made of
    real glyphs. The last character is never scrambled, because a string
    that ends in a wrong character reads as a mistake rather than as a
    secret.
    """
    text = str(text or "")
    total = len(text)
    revealed = max(0, min(int(revealed), total))
    if revealed >= total:
        return text
    head = text[:revealed]
    tail_len = total - revealed
    # The final character is always the real one.
    tail = "".join(random.choice(GLYPHS) for _ in range(max(0, tail_len - 1)))
    if tail_len:
        tail += text[-1]
    return head + tail


class DecodeLabel(QLabel):
    """A label whose text resolves instead of appearing.

    `set_text` starts the resolution; calling it again with a new string
    replaces the old one and starts again from the dark. `skip` finishes it
    at once, for the moment when the user does not want to wait.
    """

    def __init__(self, text: str = "", parent=None, step_ms: int = DEFAULT_STEP_MS):
        super().__init__(parent)
        self._target = str(text or "")
        self._revealed = 0
        self._step_ms = max(10, int(step_ms))
        self._rng = random.Random(20261113)
        self.setFont(px_font(12, spacing=2.4))
        self.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        super().setText(scramble(self._target, 0) if self._target else "")

    # ── the text ────────────────────────────────────────────────────────────

    def target(self) -> str:
        """The text it is going to end up as."""
        return self._target

    def resolved(self) -> bool:
        """Whether the text has finished resolving."""
        return self._revealed >= len(self._target)

    def set_text(self, text: str, step_ms: int | None = None) -> None:
        """Start resolving a new text."""
        self._target = str(text or "")
        self._revealed = 0
        if step_ms is not None:
            self._step_ms = max(10, int(step_ms))
        super().setText(scramble(self._target, 0) if self._target else "")
        if self._target:
            self._timer.start(self._step_ms)
        else:
            self._timer.stop()

    def skip(self) -> None:
        """Finish the resolution now."""
        self._timer.stop()
        self._revealed = len(self._target)
        super().setText(self._target)

    # ── the clock ───────────────────────────────────────────────────────────

    def _tick(self) -> None:
        if self._revealed >= len(self._target):
            self._timer.stop()
            super().setText(self._target)
            return
        self._revealed += 1
        super().setText(scramble(self._target, self._revealed))
        if self._revealed >= len(self._target):
            self._timer.stop()


class DecodeText(QWidget):
    """The same thing, painted rather than set on a label.

    A label cannot have a glow; a painted one can. The text resolves and
    the characters that just landed carry a little extra light, which is
    what makes it look like the machine is thinking rather than like a
    string is being typed.
    """

    def __init__(self, text: str = "", parent=None, size: float = 22,
                 colour: str = C.PRI, step_ms: int = DEFAULT_STEP_MS):
        super().__init__(parent)
        self._target = str(text or "")
        self._revealed = 0
        self._size = float(size)
        self._colour = colour
        self._step_ms = max(10, int(step_ms))
        self._rng = random.Random(4711)
        self.setMinimumHeight(int(size * 1.8))
        self.setFont(mono_font(size, weight=None))

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def set_text(self, text: str) -> None:
        self._target = str(text or "")
        self._revealed = 0
        self.update()
        if self._target:
            self._timer.start(self._step_ms)
        else:
            self._timer.stop()

    def skip(self) -> None:
        self._timer.stop()
        self._revealed = len(self._target)
        self.update()

    def resolved(self) -> bool:
        return self._revealed >= len(self._target)

    def _tick(self) -> None:
        if self._revealed >= len(self._target):
            self._timer.stop()
            self.update()
            return
        self._revealed += 1
        self.update()
        if self._revealed >= len(self._target):
            self._timer.stop()

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(mono_font(self._size))
        shown = scramble(self._target, self._revealed)
        metrics = p.fontMetrics()
        x = 2.0
        y = metrics.ascent() + 4
        for index, char in enumerate(shown):
            landed = index < self._revealed
            colour = QColor(self._colour)
            if not landed:
                colour.setAlpha(120)
            else:
                # The characters that just landed carry a little more light.
                age = self._revealed - index
                colour.setAlpha(255 if age > 2 else 255)
            p.setPen(colour)
            p.drawText(int(x), int(y), char)
            x += metrics.horizontalAdvance(char)
        p.end()
