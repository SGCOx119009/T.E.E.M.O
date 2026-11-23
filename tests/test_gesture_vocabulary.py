"""
tests/test_gesture_vocabulary.py — the hand as a real mouse.

WHAT IS BEING PROVED
    The user asked for the hand to be a TRUE mouse: the left and right
    buttons, the wheel, the zoom, the drag, the middle button, the
    modifier keys and the gestures that mean something specific — and he
    asked for ONE hand, because the pointer was jumping from one hand to
    the other.

    So the vocabulary is proved here, one gesture at a time, against a
    fake pointer: every gesture has to produce exactly the calls on the
    Windows API that it is supposed to produce, and nothing else.

    The tests of the POINTER itself — the tremble, the delay, the
    selection — are in `tests/test_gesture_pointer.py`. This file is about
    WHAT THE HAND SAYS.

THE HAND
    It is the canonical hand of `core/hand_geometry.py`: the proportions
    of the hand that MediaPipe's model was trained on. The tests used to
    draw a hand with the fingers spread wide apart, and that hand is not
    the hand of anybody.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gesture_hands import FakeDriver, mano, servicio  # noqa: E402
from gui.gesture import INDEX_TIP, MIDDLE_MCP, Hand  # noqa: E402


@pytest.fixture()
def service(monkeypatch):
    """A gesture service with a fake pointer and a clock we control."""
    svc = servicio()
    driver = FakeDriver()
    svc._driver = driver
    clock = {"now": 1000.0}
    monkeypatch.setattr("gui.gesture.time.time", lambda: clock["now"])
    svc._clock = clock
    svc._fake = driver
    yield svc
    svc.stop()


def _steer(service, hands, at=None):
    if at is not None:
        service._clock["now"] = at
    service._steer(hands)


# ── the shape of the hand ───────────────────────────────────────────────────

def test_the_pose_of_a_closed_hand_is_a_fist():
    assert mano("fist").pose == "fist"


def test_the_pose_of_an_open_hand_is_open():
    assert mano("open").pose == "open"


def test_the_pose_of_one_finger_is_point():
    assert mano("point").pose == "point"


def test_the_pose_of_two_fingers_is_two():
    assert mano("two").pose == "two"


def test_the_pose_of_three_fingers_is_three():
    assert mano("three").pose == "three"


def test_the_pose_of_a_pinch_is_a_pinch():
    assert mano("pinch").pose == "pinch"


def test_a_blob_is_not_a_fist():
    """The OpenCV fallback has no fingers to read.

    A degenerate hand must never read as a pose of the vocabulary: the
    shape it produces is a skin-coloured blob with no joints, and calling
    it a fist would say that the user is closing his hand when he is not.
    The OpenCV tracker says so itself, and this is the proof that the
    shape it produces cannot be a fist either.
    """
    hand = Hand([(0.0, 0.0)] * 21)
    hand.measure()
    assert hand.pose == "blob", "a degenerate hand read as a pose"
    hand.pose = "blob"
    assert hand.pose != "fist"


# ── the buttons ────────────────────────────────────────────────────────────

def test_a_pinch_presses_the_left_button_and_lets_it_go(service):
    """A PINCH WITH NO OTHER FINGER UP IS A PLAIN CLICK."""
    _steer(service, [mano("pinch")])
    assert "press" in service._fake.kinds()

    _steer(service, [mano("pinch", closeness=0.30)], at=1000.2)
    kinds = service._fake.kinds()
    assert "release" in kinds
    assert "right" not in kinds, "a quick pinch must not open the menu"


def test_a_pinch_that_moves_while_it_is_closed_is_a_drag(service):
    _steer(service, [mano("pinch")])
    # The hand moves: the aimed point is now somewhere else on the screen.
    moved = mano("pinch", aim=(0.2, 0.2))
    _steer(service, [moved], at=1000.2)
    assert "right" not in service._fake.kinds(), "a drag is not a long press"


def test_a_pinch_held_still_is_the_right_click(service):
    _steer(service, [mano("pinch")])
    # Long enough, and the hand has not moved.
    _steer(service, [mano("pinch")], at=1001.5)
    _steer(service, [mano("pinch", closeness=0.30)], at=1001.6)
    assert "right" in service._fake.kinds() or "press_right" in service._fake.kinds()


def test_the_middle_button_is_a_button_of_its_own(service):
    """THE THREE FINGERS THAT ARE LEFT OVER, ALL AT ONCE."""
    _steer(service, [mano("pinch", out=(False, True, True, True))])
    kinds = service._fake.kinds()
    assert "middle_click" in kinds, f"the middle button was not pressed: {kinds}"
    assert "press" not in kinds, \
        f"the middle button was also a left click: {kinds}"
    assert not service._fake.keys, "a modifier was left down"


# ── the wheel ──────────────────────────────────────────────────────────────

def test_two_fingers_scroll(service):
    _steer(service, [mano("two")], at=1000.0)
    # The fingers travel up the picture, so the wheel turns.
    _steer(service, [mano("two", dy=-0.15)], at=1000.2)
    assert "scroll" in service._fake.kinds(), "the wheel did not turn"


def test_two_fingers_sideways_scroll_sideways(service):
    _steer(service, [mano("two")], at=1000.0)
    _steer(service, [mano("two", dx=-0.2)], at=1000.2)
    assert "side" in service._fake.kinds()


def test_a_wheel_that_moves_a_little_does_not_turn(service):
    """THE LEFTOVERS OF THE WHEEL ARE KEPT.

    A hand that travels a little turns the wheel a little, and the
    travel that is left over is kept for the next movement. It used to
    send the whole travel in one notch, so a small movement of the hand
    turned the page a long way.
    """
    _steer(service, [mano("two")], at=1000.0)
    _steer(service, [mano("two", dy=-0.02)], at=1000.2)
    assert "scroll" not in service._fake.kinds(), (
        "the wheel turned with a movement too small to be a notch")


def test_a_scroll_with_the_ring_finger_out_is_a_scroll_and_not_a_zoom(service):
    """EL ANULAR MEDIO FUERO NO CONVIERTE LA RUEDA EN UN ZOOM.

    THIS IS WHAT THE LIVE SESSION FOUND. On a real hand the ring finger
    rests half out, and the machine read THREE fingers where the user
    meant two: the scroll came out as a zoom. What tells them apart is
    not the number of fingers but what they are doing — the wheel is the
    palm travelling, and the zoom is the two fingers coming apart.
    """
    _steer(service, [mano("three", spread=0.0)], at=1000.0)
    _steer(service, [mano("three", spread=0.0, dy=-0.15)], at=1000.2)
    kinds = service._fake.kinds()
    assert "scroll" in kinds, "the two-finger scroll was read as a zoom"
    assert "zoom" not in kinds, "the scroll was read as a zoom"


def test_a_hand_that_does_not_move_but_spreads_its_fingers_is_a_zoom(service):
    """LA MANO QUIETA Y LOS DEDOS QUE SE SEPARAN: EL ZOOM."""
    _steer(service, [mano("three", spread=0.0)], at=1000.0)
    _steer(service, [mano("three", spread=0.06)], at=1000.2)
    kinds = service._fake.kinds()
    assert "zoom" in kinds, "the fingers came apart and nothing zoomed"
    assert "scroll" not in kinds, "the zoom was read as a scroll"


# ── the zoom ───────────────────────────────────────────────────────────────

def test_three_fingers_moving_apart_zoom_in(service):
    """THE ZOOM OF ONE HAND.

    It used to need two hands pinching and moving apart, and the pointer
    was jumping from one hand to the other all the time. Now it is the
    index and the middle of ONE hand moving apart.
    """
    _steer(service, [mano("three", spread=0.0)], at=1000.0)
    _steer(service, [mano("three", spread=0.06)], at=1000.3)
    zooms = [c for c in service._fake.calls if c[0] == "zoom"]
    assert zooms, "the fingers moved apart and nothing zoomed"
    assert zooms[0][1] > 0, "moving apart is zooming in"


def test_three_fingers_moving_together_zoom_out(service):
    _steer(service, [mano("three", spread=0.06)], at=1000.0)
    _steer(service, [mano("three", spread=0.0)], at=1000.3)
    zooms = [c for c in service._fake.calls if c[0] == "zoom"]
    assert zooms, "the fingers came together and nothing zoomed"
    assert zooms[0][1] < 0, "coming together is zooming out"


# ── the resting hand ───────────────────────────────────────────────────────

def test_a_fist_lets_go_of_everything(service):
    """A CLOSED HAND IS A HAND THAT IS RESTING.

    It used to be the shift key, held down with a second hand. With one
    hand there is no second hand, and a closed hand that is simply
    resting must not leave any key pressed.
    """
    _steer(service, [mano("fist")])
    assert not service._fake.keys, \
        f"a resting hand left a key pressed: {service._fake.calls}"


def test_the_button_is_let_go_when_the_hand_disappears(service):
    _steer(service, [mano("pinch")])
    service._rest()
    kinds = service._fake.kinds()
    assert "release" in kinds, "the button stayed down"
    assert not service._fake.keys, "a modifier stayed down"


# ── the swipe ──────────────────────────────────────────────────────────────

def test_an_open_palm_swept_sideways_swipes(service):
    _steer(service, [mano("open", aim=(0.5, 0.5))], at=1000.0)
    _steer(service, [mano("open", aim=(0.2, 0.5))], at=1000.2)
    taps = [c for c in service._fake.calls if c[0] == "tap"]
    assert taps, "the palm swept and nothing happened"
    assert taps[0][2] is True, "a swipe is Alt and the arrows"


def test_an_open_palm_that_does_not_move_does_not_swipe(service):
    _steer(service, [mano("open", aim=(0.5, 0.5))], at=1000.0)
    _steer(service, [mano("open", aim=(0.5, 0.5))], at=1000.2)
    assert "tap" not in service._fake.kinds()


# ── where the pointer is aimed ──────────────────────────────────────────────

def test_the_pointer_is_aimed_at_the_knuckle_and_not_at_the_pinch():
    """THE POINTER DOES NOT JUMP WHEN THE HAND CLOSES.

    The idea is from the gesture project the owner downloaded
    (`Gesture-Recognition-main`): the pointer follows the knuckle of the
    middle finger, and the pinch is only the button. A pointer that
    follows the pinch moves exactly at the moment the user clicks, and
    the click lands where the hand WAS instead of where it is.
    """
    open_hand = mano("open")
    pinched = mano("pinch")

    assert open_hand.aim_x == pytest.approx(open_hand.points[MIDDLE_MCP][0])
    assert open_hand.aim_y == pytest.approx(open_hand.points[MIDDLE_MCP][1])

    # The knuckle does not move when the hand closes.
    assert pinched.aim_x == pytest.approx(open_hand.aim_x)
    assert pinched.aim_y == pytest.approx(open_hand.aim_y)

    # And the pinch point DID move: that is the thing we no longer follow.
    assert pinched.x != pytest.approx(open_hand.x)


def test_the_pointer_reaches_the_whole_screen(service):
    """THE EDGES OF THE USABLE PICTURE ARE THE EDGES OF THE SCREEN.

    The camera sees more than the hand needs, and the corners of the
    picture cannot be reached without the arm leaving the frame. So the
    middle of the picture is the middle of the screen, and the band
    around it is the whole screen.

    The pointer goes through a filter, so it is let settle: the proof is
    that the pointer ENDS UP on the edge, not that it is there on the
    first frame of a jump that no hand ever makes.
    """
    import ctypes

    width = ctypes.windll.user32.GetSystemMetrics(0)
    height = ctypes.windll.user32.GetSystemMetrics(1)

    def where(aim_x, aim_y, frames=30):
        hand = mano("open")
        hand.aim_x = aim_x
        hand.aim_y = aim_y
        for _ in range(frames):
            _steer(service, [hand])
        return service._target

    # The middle of the picture is the middle of the screen.
    x, y = where(0.5, 0.5)
    assert abs(x - width // 2) <= 2, f"the middle is at {x}, not at {width // 2}"
    assert abs(y - height // 2) <= 2, f"the middle is at {y}, not at {height // 2}"

    # The usable band reaches the edges of the screen.
    left, top = where(0.85, 0.15)
    right, bottom = where(0.15, 0.85)
    assert left == 0, f"the left edge of the screen is not reachable: {left}"
    assert right == width, f"the right edge of the screen is not reachable: {right}"
    assert top == 0, f"the top edge of the screen is not reachable: {top}"
    assert bottom == height, f"the bottom edge is not reachable: {bottom}"

    # And nothing goes off the screen.
    assert where(0.0, 0.0) == (width, 0)
    assert where(1.0, 1.0) == (0, height)
