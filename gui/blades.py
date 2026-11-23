"""
gui/blades.py — the deck.

WHAT IT IS
    A column of cards down the right-hand side of the interface. Every
    time the assistant produces something — a search, a briefing, a review,
    a finished task — a card goes into the deck. The deck is always there,
    one key away, and it is the answer to the question a chat interface
    never quite answers: what did it say to me, and what did it say before
    that?

WHY A DECK AND NOT A LOG
    A log is a wall of text you scroll. A deck is a stack of cards: only
    one is open, the rest are folded down to their titles, and the whole
    history of the session fits on one screen. The interface stops being a
    conversation you have to remember and becomes an instrument you can
    look at.

THE KEYS
    `Ctrl+Alt+E` opens and closes the deck. With it open, `E` folds and
    unfolds the card in front, `X` closes it, and `[` and `]` walk through
    the cards.

    The letters on their own are NOT bound while the deck is closed, and
    they are not bound while the user is typing in a field. A shortcut that
    fires while somebody is writing a message is a shortcut that eats
    letters, and a shortcut that eats letters is a bug the user blames on
    the assistant.

THE COST
    One widget that paints its own cards. No child widgets per card, so
    there is nothing to keep in sync when the deck grows: the cards are
    data, and the data is drawn.
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from gui import fx
from gui.style import mono_font, px_font
from gui.theme import C

STRIP_H = 26        # the height of a folded card
GAP = 5             # the space between two cards
PAD = 10            # the padding inside an open card
ANIM_MS = 180       # how long a card takes to open or close
CLOSE_W = 22        # the width of the cross that closes a card


class Blade:
    """One card of the deck."""

    __slots__ = ("title", "text", "code", "kind")

    def __init__(self, title: str, text: str = "", code: str = "", kind: str = "info"):
        self.title = str(title or "")
        self.text = str(text or "")
        self.code = str(code or "")
        self.kind = str(kind or "info")


class BladeStack(QWidget):
    """The deck of cards."""

    def __init__(self, parent=None, width: int = 380):
        super().__init__(parent)
        self._blades: list[Blade] = []
        self._active = -1
        self._anim = 0.0            # 0 = folded, 1 = open
        self._anim_target = 0.0
        self._accent = C.PRI
        self._width = int(width)
        self._lines_cache: dict[int, list[str]] = {}
        # WHETHER THE DECK IS ON SCREEN.
        #
        # It used to be guessed from the open card: "the deck is open"
        # meant "there is an open card", and those are not the same
        # thing. After the user folded the deck away, the next card
        # that arrived put an open card back, and the deck said it was
        # open — so the button that opens it could not open it any
        # more, and the button that closes it did nothing. The screen
        # and the card are two different questions and now they have two
        # different answers.
        self._shown = False
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setMouseTracking(True)
        self.hide()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    # ── the deck ────────────────────────────────────────────────────────────

    @property
    def count(self) -> int:
        return len(self._blades)

    @property
    def active(self) -> int:
        """The index of the open card, or -1 when the deck is closed."""
        return self._active

    def is_open(self) -> bool:
        """Whether the deck is on screen.

        It is the screen that says it, and not the card: a deck that is
        folded away is a deck that is closed, whatever card it had open.
        """
        return self._shown

    def push(self, title: str, text: str = "", code: str = "", kind: str = "info") -> int:
        """Put a card into the deck.

        IT DOES NOT WAKE THE DECK UP. A card that arrives while the user
        has folded the deck away stays out of the way: the deck opens
        the first time there is something to show, and after that it
        stays where the user left it.
        """
        self._blades.append(Blade(title, text, code, kind))
        index = len(self._blades) - 1
        self._lines_cache.clear()
        if self._shown:
            # The user is looking at the deck: the newest card is the
            # one that opens.
            self._active = index
            self._anim = 0.0
            self._anim_target = 1.0
            self._timer.start(16)
        self._ensure_visible()
        self.update()
        return index

    def remove_blade(self, index: int) -> bool:
        """Throw one card away for good.

        The cross on the card. A card that cannot be thrown away is a
        card the user has to live with: the news of the morning stayed
        on the screen all day, three of them, and there was no way to
        say "that one is done".
        """
        if not (0 <= index < len(self._blades)):
            return False
        self._blades.pop(index)
        self._lines_cache.clear()
        if self._active >= len(self._blades):
            self._active = len(self._blades) - 1
        if self._active < 0:
            self._anim = 0.0
            self._anim_target = 0.0
            self._timer.stop()
            if not self._blades:
                self._shown = False
                self.hide()
        else:
            self._anim = 0.0
            self._anim_target = 1.0
            self._timer.start(16)
        self._ensure_visible()
        self.update()
        return True

    def clear(self) -> None:
        """Empty the deck."""
        self._blades.clear()
        self._active = -1
        self._anim = 0.0
        self._anim_target = 0.0
        self._shown = False
        self._timer.stop()
        self.update()

    # ── the keys ────────────────────────────────────────────────────────────

    def open_deck(self) -> None:
        """Open the deck on the last card."""
        if not self._blades:
            return
        self._shown = True
        self._active = len(self._blades) - 1
        self._anim_target = 1.0
        self._timer.start(16)
        self._ensure_visible()
        self.show()
        self.raise_()
        self.update()

    def close_deck(self) -> None:
        """Fold the deck away."""
        self._shown = False
        self._active = -1
        self._anim_target = 0.0
        self._timer.start(16)
        self.update()

    def toggle(self) -> None:
        """Open it or close it."""
        if self.is_open():
            self.close_deck()
        else:
            self.open_deck()

    def fold(self) -> None:
        """Fold or unfold the card in front."""
        self._anim_target = 0.0 if self._anim_target > 0.5 else 1.0
        self._timer.start(16)

    def next_blade(self) -> None:
        """The card after this one."""
        if not self._blades:
            return
        self._shown = True
        self._active = (self._active + 1) % len(self._blades)
        self._anim = 0.0
        self._anim_target = 1.0
        self._lines_cache.clear()
        self._timer.start(16)
        self._ensure_visible()
        self.show()
        self.raise_()
        self.update()

    def prev_blade(self) -> None:
        """The card before this one."""
        if not self._blades:
            return
        self._shown = True
        self._active = (self._active - 1) % len(self._blades)
        self._anim = 0.0
        self._anim_target = 1.0
        self._lines_cache.clear()
        self._timer.start(16)
        self._ensure_visible()
        self.show()
        self.raise_()
        self.update()

    def set_accent(self, colour: str) -> None:
        """The colour of the deck follows the state of the assistant."""
        self._accent = colour or C.PRI
        self.update()

    # ── the geometry ────────────────────────────────────────────────────────

    def _deck_width(self) -> int:
        """How wide the column of cards is.

        THE LAYOUT DECIDES. The deck used to be a floating panel that
        measured itself against the window and put itself on top of the
        right-hand column: the conversation, its rail and all, went
        under the cards and the user could not get it back. Now the deck
        is a column of the interface, between the face and the
        conversation, and nobody is covered by anybody.
        """
        return max(220, int(self.width() or self._width))

    def _ensure_visible(self) -> None:
        """Nothing to place: the layout owns the geometry now.

        It used to be called after every card that arrived and it put
        the deck back on top of the conversation. Now the only thing
        left to do when the width changes is to forget the lines that
        were wrapped for the old width.
        """
        self._lines_cache.clear()
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # THE LINES BELONG TO THE WIDTH THEY WERE WRAPPED FOR. The column
        # is as wide as the layout says it is, and the layout says it is
        # a different width when the window changes. A text wrapped for
        # the old width is cut in the new one.
        old = getattr(self, "_last_width", None)
        if old is not None and old != self.width():
            self._lines_cache.clear()
        self._last_width = self.width()
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._ensure_visible()

    # ── the clock ───────────────────────────────────────────────────────────

    def _tick(self) -> None:
        step = 16.0 / ANIM_MS
        if self._anim < self._anim_target:
            self._anim = min(self._anim_target, self._anim + step)
        elif self._anim > self._anim_target:
            self._anim = max(self._anim_target, self._anim - step)
        else:
            self._timer.stop()
            if self._anim_target == 0.0 and not self._shown:
                self.hide()
        self.update()

    # ── where the cards are ─────────────────────────────────────────────────

    def _positions(self) -> list[tuple[int, float, float]]:
        """Where every card starts and how tall it is.

        ONE LIST FOR THE PAINTING AND FOR THE MOUSE. They used to be
        computed twice, and the two answers were not the same: the mouse
        believed that the open card was a strip and a half tall, so
        clicking on the text of a card did nothing at all.
        """
        y = 0.0
        positions: list[tuple[int, float, float]] = []
        for index in range(len(self._blades)):
            height = self._card_height(index)
            positions.append((index, y, height))
            y += height + GAP
        return positions

    def _close_rect(self, top: float, height: float) -> QRectF:
        """Where the cross that closes a card is."""
        width = self._deck_width()
        return QRectF(width - CLOSE_W, top, CLOSE_W, height)

    def _card_at(self, x: float, y: float) -> tuple[int, bool]:
        """Which card is under the pointer, and whether it is the cross."""
        for index, top, height in self._positions():
            if y < top or y > top + height:
                continue
            close = self._close_rect(top, height)
            return index, bool(close.contains(x, y))
        return -1, False

    # ── the painting ────────────────────────────────────────────────────────

    def _wrap(self, text: str, width: int, index: int) -> list[str]:
        """The lines of a text, wrapped to a width and remembered."""
        cached = self._lines_cache.get(index)
        if cached is not None:
            return cached
        from PyQt6.QtGui import QFontMetrics
        metrics = QFontMetrics(px_font(11))
        lines: list[str] = []
        for paragraph in str(text or "").splitlines() or [""]:
            if not paragraph:
                lines.append("")
                continue
            current = ""
            for word in paragraph.split(" "):
                probe = word if not current else current + " " + word
                if metrics.horizontalAdvance(probe) <= width:
                    current = probe
                    continue
                if current:
                    lines.append(current)
                # A word longer than the line is cut: nothing else can be
                # done with it without a word-wrap algorithm.
                while metrics.horizontalAdvance(word) > width and len(word) > 1:
                    cut = len(word)
                    while cut > 1 and metrics.horizontalAdvance(word[:cut]) > width:
                        cut -= 1
                    lines.append(word[:cut])
                    word = word[cut:]
                current = word
            lines.append(current)
        self._lines_cache[index] = lines
        return lines

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

        if not self._blades:
            return

        # THE BACKDROP.
        #
        # The deck has its own darkness, with a soft edge on the left so
        # it does not look like a box. It is drawn only as far down as
        # the cards go: a dark column that goes all the way to the
        # bottom of the window is a hole, and a hole is not a column of
        # cards.
        positions = self._positions()
        content_h = 0.0
        if positions:
            _i, _t, _h = positions[-1]
            content_h = _t + _h
        content_h = max(0.0, min(content_h, H))
        shade = QLinearGradient(0, 0, W, 0)
        shade.setColorAt(0.0, fx.qcol(C.DEEP_TOP, 0))
        shade.setColorAt(0.12, fx.qcol(C.DEEP_TOP, 225))
        shade.setColorAt(1.0, fx.qcol(C.DEEP_TOP, 245))
        p.fillRect(QRectF(0, 0, W, content_h), QBrush(shade))
        p.setPen(QPen(fx.qcol(self._accent, 70), 1))
        p.drawLine(QPointF(0, 0), QPointF(0, content_h))

        # Where each card starts. The open one is tall; the folded ones are
        # a strip each, and they stack downwards from the top.

        # The cards behind, so the open one draws over them.
        for index, top, height in positions:
            if index == self._active:
                continue
            self._draw_strip(p, index, top, height)
        for index, top, height in positions:
            if index != self._active:
                continue
            self._draw_card(p, index, top, height)

    def _body_height(self, blade: Blade, index: int, width: int | None = None) -> float:
        """How tall the body of a card would be if it were fully open.

        THE FIRST LINE STARTS TWELVE PIXELS DOWN. The text is not drawn
        against the top of the body but a line lower, and a body that
        was measured without that left the card with a title and no
        words under it: the open card showed its title and nothing
        else, and the text was cut by the card's own edge.
        """
        if width is None:
            width = self._deck_width()
        lines = self._wrap(blade.text, width - 2 * PAD - 4, index)
        return 12.0 + max(0.0, len(lines) * 16.0)

    def _card_height(self, index: int) -> float:
        """How tall a card is right now.

        ONE ANSWER FOR THE PAINTING AND FOR THE MEASUREMENT. The card
        needs its header, the gap that follows it, the body with the
        first line twelve pixels down, and the padding at the bottom.
        """
        blade = self._blades[index]
        if index != self._active:
            return float(STRIP_H)
        body = self._body_height(blade, index)
        return STRIP_H + 2 * PAD - 4 + body * self._anim

    def _draw_close(self, p: QPainter, top: float, height: float) -> None:
        """The cross that throws a card away.

        IT IS DRAWN ON EVERY CARD, FOLDED OR OPEN. The news of the
        morning was three cards in a column with no way to dismiss any
        of them: the user asked for a way to close them and there was
        none.
        """
        box = self._close_rect(top, height)
        p.setPen(QPen(QColor(C.TEXT_DIM), 1.4))
        cx = box.center().x()
        cy = box.center().y()
        r = 4.0
        p.drawLine(QPointF(cx - r, cy - r), QPointF(cx + r, cy + r))
        p.drawLine(QPointF(cx - r, cy + r), QPointF(cx + r, cy - r))

    def _draw_strip(self, p: QPainter, index: int, top: float, height: float) -> None:
        blade = self._blades[index]
        rect = QRectF(0, top, self._deck_width(), height)
        path = fx.chamfer(rect, 9)
        p.fillPath(path, fx.qcol(C.PANEL, 248))
        p.setPen(QPen(fx.qcol(self._accent, 90), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        # A tick at the left: the card is there, it is just folded.
        p.setPen(QPen(fx.qcol(self._accent, 150), 2))
        p.drawLine(int(rect.left() + 5), int(rect.top() + 6),
                   int(rect.left() + 5), int(rect.bottom() - 6))
        p.setFont(px_font(10, spacing=1.4))
        p.setPen(QPen(QColor(C.TEXT_MED)))
        p.drawText(QRectF(rect.left() + 14, rect.top(),
                          rect.width() - 20 - CLOSE_W, height),
                   Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(blade.title, Qt.TextElideMode.ElideRight,
                                              int(rect.width() - 30 - CLOSE_W)))
        if blade.code:
            p.setFont(mono_font(9))
            p.setPen(QPen(QColor(C.TEXT_DIM)))
            p.drawText(QRectF(rect.left(), rect.top(),
                              rect.width() - 10 - CLOSE_W, height),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       blade.code)
        self._draw_close(p, top, height)

    def _draw_card(self, p: QPainter, index: int, top: float, height: float) -> None:
        blade = self._blades[index]
        rect = QRectF(0, top, self._deck_width(), max(STRIP_H, height))
        path = fx.chamfer(rect, 12)
        p.fillPath(path, fx.qcol(C.PANEL, 252))
        p.setPen(QPen(fx.qcol(self._accent, 190), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        # The lit edge along the top, the way a bevel catches the light.
        p.setPen(QPen(fx.qcol(self._accent, 220), 1.6))
        p.drawLine(int(rect.left() + 12), int(rect.top()),
                   int(rect.right() - 12), int(rect.top()))

        # The header.
        p.setFont(px_font(11, spacing=1.8))
        p.setPen(QPen(QColor(C.PRI)))
        p.drawText(QRectF(rect.left() + PAD, rect.top(),
                          rect.width() - 2 * PAD - CLOSE_W, STRIP_H),
                   Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(blade.title, Qt.TextElideMode.ElideRight,
                                              int(rect.width() - 2 * PAD - 60)))
        if blade.code:
            p.setFont(mono_font(9))
            p.setPen(QPen(QColor(C.TEXT_DIM)))
            p.drawText(QRectF(rect.left(), rect.top(),
                              rect.width() - PAD - CLOSE_W, STRIP_H),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       blade.code)
        self._draw_close(p, top, height)

        # The body, clipped to what the card is actually showing.
        if self._anim > 0.02 and blade.text:
            body_top = rect.top() + STRIP_H + PAD - 4
            body_h = rect.bottom() - PAD - body_top
            if body_h > 4:
                p.save()
                p.setClipRect(QRectF(rect.left() + 2, body_top, rect.width() - 4, body_h))
                width = self._deck_width() - 2 * PAD - 4
                lines = self._wrap(blade.text, width, index)
                p.setFont(px_font(11))
                p.setPen(QPen(QColor(C.TEXT)))
                y = body_top + 12
                for line in lines:
                    if y > rect.bottom() - PAD:
                        p.setPen(QPen(QColor(C.TEXT_DIM)))
                        p.drawText(int(rect.left() + PAD), int(y), "…")
                        break
                    p.drawText(int(rect.left() + PAD), int(y), line)
                    y += 16
                p.restore()

        # The scale along the bottom of the card: it is an instrument.
        fx.ruler(p, QRectF(rect.left() + PAD, rect.bottom() - 6,
                           rect.width() - 2 * PAD, 6), self._accent, count=10, alpha=45)

    # ── the mouse ───────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        """The cross throws the card away; the card itself opens."""
        pos = event.position()
        index, on_cross = self._card_at(pos.x(), pos.y())
        if index < 0:
            super().mousePressEvent(event)
            return
        if on_cross:
            self.remove_blade(index)
            return
        if index != self._active:
            self._shown = True
            self._active = index
            self._anim = 0.0
            self._anim_target = 1.0
            self._lines_cache.clear()
            self._timer.start(16)
            self._ensure_visible()
            self.show()
            self.raise_()
            self.update()

    def sizeHint(self) -> QSize:
        return QSize(self._deck_width(), 320)
