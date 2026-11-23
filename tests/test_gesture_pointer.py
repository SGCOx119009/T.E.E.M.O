"""
tests/test_gesture_pointer.py — the hands, measured instead of assumed.

WHAT IS TESTED
    1. THE POINTER DOES NOT SHAKE. A hand that stands still while the
       model reports it a few pixels away every time must move the
       pointer by a fraction of that. This is the "Parkinson" the owner
       reported.
    2. THE POINTER IS NOT LATE. A hand that walks at a known speed must
       be followed without a delay that a person can feel.
    3. THE PINCH DOES NOT CHATTER. A pinch that trembles around the
       threshold must press once and release once, not twenty times.
    4. THE SELECTION WORKS. A quick pinch is a click, two quick pinches
       are a double click, a pinch held still is the right button — and
       NOT a left click as well.
    5. ONE HAND IS A WHOLE MOUSE. The fingers that are not pinching are
       the modifier keys, and the middle button is a gesture of its own.
    6. A RESTING HAND IS NOT A PINCH. A fist is not a click.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.pointer_filter import PointerSmoother  # noqa: E402


# ── the pieces ──────────────────────────────────────────────────────────────

class _Driver:
    """A pointer that writes down what it was told to do."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.keys: set[int] = set()

    def available(self) -> bool:
        return True

    def move(self, x, y):
        self.calls.append(("move", x, y))

    def click(self):
        self.calls.append(("click",))

    def press(self):
        self.calls.append(("press",))

    def release(self):
        self.calls.append(("release",))

    def press_right(self):
        self.calls.append(("press_right",))

    def release_right(self):
        self.calls.append(("release_right",))

    def right_click(self):
        self.calls.append(("right_click",))

    def middle_click(self):
        self.calls.append(("middle_click",))

    def double_click(self):
        self.calls.append(("double_click",))

    def scroll(self, n):
        self.calls.append(("scroll", n))

    def scroll_sideways(self, n):
        self.calls.append(("scroll_sideways", n))

    def zoom(self, d):
        self.calls.append(("zoom", d))

    def key(self, vk, down):
        self.calls.append(("key", vk, down))
        if down:
            self.keys.add(vk)
        else:
            self.keys.discard(vk)

    def tap(self, vk, alt=False):
        self.calls.append(("tap", vk, alt))

    def kinds(self) -> list[str]:
        return [c[0] for c in self.calls]


def _mano(middle=False, ring=False, pinky=False, index_out=True,
          thumb_gap=0.02, aim=(0.5, 0.5)):
    """A hand made of twenty-one points, as the tracker would read it.

    THE PROPORTIONS ARE THE ONES OF A REAL HAND: the wrist, the knuckles
    of the four fingers, the middle of the palm, and the fingers either
    standing up or folded down onto the palm.
    """
    from gui.gesture import Hand

    pts = [(0.5, 0.85)] * 21
    pts[0] = (0.5, 0.85)          # the wrist
    # The knuckles of the four fingers and their middle joints.
    for _base, _x in ((5, 0.38), (9, 0.47), (13, 0.56), (17, 0.65)):
        pts[_base] = (_x, 0.62)        # the knuckle
        pts[_base + 1] = (_x, 0.50)    # the middle joint
        pts[_base + 2] = (_x, 0.44)
    # The fingers that are up.
    if index_out:
        pts[8] = (0.38, 0.38)          # the tip of the index, in the air
    else:
        pts[8] = (0.38, 0.72)          # the tip of the index, folded down
    pts[12] = (0.47, 0.38) if middle else (0.47, 0.72)
    pts[16] = (0.56, 0.38) if ring else (0.56, 0.72)
    pts[20] = (0.65, 0.38) if pinky else (0.65, 0.72)
    # The thumb: it is next to the tip of the index when they pinch.
    pts[4] = (0.38 + thumb_gap, pts[8][1])
    pts[3] = (0.44, 0.48)
    mano = Hand(pts, "Right")
    mano.measure()
    mano.aim_x, mano.aim_y = aim
    return mano


def _servicio():
    from gui.gesture import GestureService

    servicio = GestureService()
    servicio._driver = _Driver()
    servicio._drive_pointer = True
    return servicio


# ── 1. the pointer does not shake ────────────────────────────────────────────

def test_a_still_hand_does_not_move_the_pointer():
    """The jitter of the pointer, with the noise a real model makes."""
    import random

    azar = random.Random(11)
    suavizador = PointerSmoother()
    salida = []
    for _ in range(90):
        x = 960.0 + azar.gauss(0.0, 8.0)      # 8 px of noise, as MediaPipe does
        salida.append(suavizador(x, 540.0, 1.0 / 15.0)[0])
    crudo = 8.0
    filtrado = statistics_stdev(salida[20:])
    assert filtrado < crudo / 3.0, (
        f"the pointer still moves {filtrado:.2f} px with a hand that is "
        f"not moving (it was {crudo:.2f} px before the filter)")


def statistics_stdev(values) -> float:
    values = list(values)
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def test_the_pointer_is_not_late():
    """A hand that walks is followed without a delay anybody can feel."""
    suavizador = PointerSmoother()
    retardos = []
    x = 200.0
    for _ in range(60):
        x += 600.0 / 15.0
        fx = suavizador(x, 540.0, 1.0 / 15.0)[0]
        if x > 300:
            retardos.append((x - fx) / 600.0 * 1000.0)
    medio = sum(retardos) / len(retardos)
    assert abs(medio) < 40.0, (
        f"the pointer is {medio:.0f} ms away from the hand")


def test_a_hand_that_comes_back_does_not_jump_the_pointer():
    """The filter forgets the hand that was there before."""
    suavizador = PointerSmoother()
    for _ in range(30):
        suavizador(100.0, 100.0, 1.0 / 15.0)
    suavizador.reset()
    x, _y = suavizador(900.0, 500.0, 1.0 / 15.0)
    assert x == 900.0, "the pointer jumped across the screen when the hand came back"


# ── 2. the pinch does not chatter ────────────────────────────────────────────

def test_a_trembling_pinch_presses_once_and_releases_once():
    """THE THRESHOLD WITH NO HYSTERESIS.

    The fingers close a little past the line and open a little past it,
    and the button went down and came up and went down again, several
    times a second. The user saw a cursor that moved like it had
    Parkinson's and could not select anything.

    The fingers here tremble right around the line: a closeness of 0.79
    and then of 0.69. With one threshold that is a button that chatters.
    With two, the button goes down once and stays down until the
    fingers really open.
    """
    servicio = _servicio()
    for i in range(24):
        gap = 0.040 if i % 2 == 0 else 0.060
        servicio._steer([_mano(thumb_gap=gap)])
    kinds = servicio._driver.kinds()
    assert kinds.count("press") == 1, (
        f"the button was pressed {kinds.count('press')} times in a trembling "
        f"pinch: {kinds}")
    assert kinds.count("release") == 0, (
        f"the button was let go {kinds.count('release')} times in a trembling "
        f"pinch, and every one of them was a click that landed somewhere "
        f"else: {kinds}")


def test_a_pinch_that_opens_wide_releases_the_button():
    servicio = _servicio()
    servicio._steer([_mano(thumb_gap=0.02)])
    assert servicio._driver.kinds().count("press") == 1
    # The fingers open: the gap grows and the pinch is over.
    for _ in range(4):
        servicio._steer([_mano(thumb_gap=0.16)])
    assert servicio._driver.kinds().count("release") == 1, \
        "the button was not let go when the fingers opened"


# ── 3. the selection works ───────────────────────────────────────────────────

def test_a_quick_pinch_is_one_click():
    servicio = _servicio()
    servicio._steer([_mano(thumb_gap=0.02)])
    for _ in range(3):
        servicio._steer([_mano(thumb_gap=0.16)])
    kinds = servicio._driver.kinds()
    assert kinds.count("press") == 1
    assert kinds.count("release") == 1
    assert "click" not in kinds, "the click was sent twice"


def test_two_quick_pinches_are_a_double_click():
    servicio = _servicio()
    # The first pinch.
    servicio._steer([_mano(thumb_gap=0.02)])
    for _ in range(3):
        servicio._steer([_mano(thumb_gap=0.16)])
    # The second one, right away.
    servicio._steer([_mano(thumb_gap=0.02)])
    for _ in range(3):
        servicio._steer([_mano(thumb_gap=0.16)])
    kinds = servicio._driver.kinds()
    assert "double_click" in kinds, (
        f"two quick pinches were not a double click: {kinds}")


def test_a_pinch_held_still_is_the_right_button_and_not_a_click():
    """ONE GESTURE, ONE ACTION.

    It used to be a left click AND a right click: the thing was
    selected and its menu was opened at the same time.
    """
    servicio = _servicio()
    servicio._steer([_mano(thumb_gap=0.02)])
    # It is held for longer than the long press.
    servicio._press_time -= 1.0
    servicio._steer([_mano(thumb_gap=0.02)])
    for _ in range(3):
        servicio._steer([_mano(thumb_gap=0.16)])
    kinds = servicio._driver.kinds()
    assert "press_right" in kinds, f"the right button never went down: {kinds}"
    assert "release_right" in kinds, f"the right button never came up: {kinds}"
    assert "click" not in kinds, (
        f"a long press was also a click, and it selected the thing: {kinds}")


def test_a_pinch_that_moves_is_a_drag_and_not_a_click():
    servicio = _servicio()
    servicio._steer([_mano(thumb_gap=0.02)])
    for i in range(6):
        servicio._steer([_mano(thumb_gap=0.02, aim=(0.5 + 0.02 * i, 0.5))])
    for _ in range(3):
        servicio._steer([_mano(thumb_gap=0.16)])
    kinds = servicio._driver.kinds()
    assert "click" not in kinds, f"a drag was also a click: {kinds}"
    assert kinds.count("press") == 1 and kinds.count("release") == 1


# ── 4. one hand is a whole mouse ─────────────────────────────────────────────

def test_the_fingers_that_are_not_pinching_are_the_modifiers():
    """A pinch with the middle finger up is a click with the shift down."""
    from gui.gesture import PointerDriver

    servicio = _servicio()
    servicio._steer([_mano(thumb_gap=0.02, middle=True)])
    assert PointerDriver._VK_SHIFT in servicio._driver.keys, \
        "the middle finger did not hold the shift"
    for _ in range(3):
        servicio._steer([_mano(thumb_gap=0.16, middle=True)])
    assert PointerDriver._VK_SHIFT not in servicio._driver.keys, \
        "the shift was left down after the pinch"


def test_the_ring_finger_holds_the_control():
    from gui.gesture import PointerDriver

    servicio = _servicio()
    servicio._steer([_mano(thumb_gap=0.02, ring=True)])
    assert PointerDriver._VK_CONTROL in servicio._driver.keys


def test_the_little_finger_holds_the_alt():
    from gui.gesture import PointerDriver

    servicio = _servicio()
    servicio._steer([_mano(thumb_gap=0.02, pinky=True)])
    assert PointerDriver._VK_MENU in servicio._driver.keys


def test_all_three_fingers_are_the_middle_button():
    from gui.gesture import PointerDriver

    servicio = _servicio()
    servicio._steer([_mano(thumb_gap=0.02, middle=True, ring=True, pinky=True)])
    kinds = servicio._driver.kinds()
    assert "middle_click" in kinds, f"the middle button was not pressed: {kinds}"
    assert "press" not in kinds, \
        f"the middle button was also a left click: {kinds}"
    assert not servicio._driver.keys, \
        "a modifier was left down after the middle button"


# ── 5. a resting hand is not a pinch ─────────────────────────────────────────

def test_a_fist_is_not_a_pinch():
    """THE HAND THAT IS RESTING IS NOT A CLICK.

    The thumb of a fist is touching the index, and the fingers of a fist
    are close together: without a third measurement a resting hand held
    the mouse button down.
    """
    mano = _mano(index_out=False, thumb_gap=0.02)
    assert mano.pinched is False, (
        "a fist was read as a pinch: the index is folded and the thumb is "
        "next to it, and that is not a button")

    servicio = _servicio()
    for _ in range(10):
        servicio._steer([_mano(index_out=False, thumb_gap=0.02)])
    assert servicio._driver.kinds().count("press") == 0, \
        "a resting hand pressed the mouse button"


def test_the_pinch_needs_the_index_in_the_air():
    """The third measurement of the pinch: where the index is."""
    from gui.gesture import PINCH_ON, PINCH_REACH

    mano = _mano(thumb_gap=0.02)
    assert mano.pinched is True
    assert mano.index_reach > PINCH_REACH
    assert mano.closeness > PINCH_ON
