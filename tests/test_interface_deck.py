"""tests/test_interface_deck.py — the deck of cards, measured.

WHAT IS TESTED
    The three things the user asked for, in the deck:

    1. EVERY CARD CAN BE THROWN AWAY. The news of the morning used to
       be three cards in a column with no way to dismiss any of them.
    2. THE DECK CAN BE CLOSED AND OPENED AGAIN. It used to get stuck:
       the button that opened it could not open it any more once it had
       been folded.
    3. THE POINTER FINDS THE CARD IT IS OVER. The mouse used to believe
       that an open card was a strip and a half tall.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui.blades import CLOSE_W, STRIP_H, BladeStack  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    import os

    previous = os.environ.get("QT_QPA_PLATFORM")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
    if previous is None:
        os.environ.pop("QT_QPA_PLATFORM", None)
    else:
        os.environ["QT_QPA_PLATFORM"] = previous


@pytest.fixture
def deck(qt_app):
    stack = BladeStack()
    yield stack
    stack.clear()


def _click(deck, x, y):
    """A click at a point of the deck."""
    from PyQt6.QtCore import QPointF
    from PyQt6.QtCore import QEvent
    from PyQt6.QtGui import QMouseEvent

    event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(x, y),
        QPointF(x, y),
        Qt_MouseButton_Left(),
        Qt_MouseButton_Left(),
        Qt_NoModifier(),
    )
    deck.mousePressEvent(event)


def Qt_MouseButton_Left():
    from PyQt6.QtCore import Qt

    return Qt.MouseButton.LeftButton


def Qt_NoModifier():
    from PyQt6.QtCore import Qt

    return Qt.KeyboardModifier.NoModifier


def test_every_card_can_be_thrown_away(deck):
    """Three news, three crosses, and the news goes."""
    deck.push("NEWS", "one")
    deck.push("NEWS", "two")
    deck.push("NEWS", "three")
    deck.open_deck()
    assert deck.count == 3

    # The cross of the open card is at its right, in the header row.
    box = deck._close_rect(0.0, STRIP_H)
    _click(deck, box.center().x(), box.center().y())
    assert deck.count == 2, "the cross did not throw the card away"


def test_the_cross_of_a_folded_card_throws_it_away(deck):
    deck.push("NEWS", "one")
    deck.push("SEARCH", "two")
    deck.open_deck()
    # The second card is folded: its strip is below the open one.
    positions = deck._positions()
    index, top, height = positions[1]
    box = deck._close_rect(top, height)
    _click(deck, box.center().x(), box.center().y())
    assert deck.count == 1
    assert deck._blades[0].title == "NEWS"


def test_the_deck_can_be_closed_and_opened_again(deck):
    """The button that opens the deck could not open it any more."""
    deck.push("NEWS", "one")
    deck.open_deck()
    assert deck.is_open()

    deck.close_deck()
    assert not deck.is_open()

    deck.open_deck()
    assert deck.is_open(), "the deck could not be opened a second time"


def test_a_card_that_arrives_does_not_wake_a_closed_deck(deck):
    """A panel that opens itself every time is a panel the user learns to
    close — and a card that comes back on its own cannot be thrown away."""
    deck.push("NEWS", "one")
    deck.open_deck()
    deck.close_deck()
    deck.push("NEWS", "two")
    assert not deck.is_open(), "the deck woke itself up"
    assert deck.count == 2, "the card was lost"


def test_an_emptied_deck_opens_again_with_the_next_card(deck):
    deck.push("NEWS", "one")
    deck.open_deck()
    deck.clear()
    assert not deck.is_open()
    deck.push("NEWS", "two")
    deck.open_deck()
    assert deck.is_open()


def test_the_pointer_finds_the_card_it_is_over(deck):
    """The mouse believed an open card was a strip and a half tall."""
    deck.push("NEWS", "one" * 200)
    deck.open_deck()
    deck._anim = 1.0
    positions = deck._positions()
    index, top, height = positions[0]
    assert height > STRIP_H + 20, "the open card is not open"
    # A click in the body of the card, well below its header.
    _click(deck, 20.0, top + height - 4.0)
    # It must not have thrown the card away.
    assert deck.count == 1


def test_the_cross_is_at_the_right_of_the_card(deck):
    deck.push("NEWS", "one")
    deck.open_deck()
    box = deck._close_rect(0.0, STRIP_H)
    assert box.width() == CLOSE_W
    assert box.left() >= deck._deck_width() - CLOSE_W - 1
