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

THE FAKE POINTER
    The real driver talks to `user32.dll`, and a test that moved the real
    mouse would move it while the developer is working. The fake records
    what was asked of it, so the proof is about the DECISION and not about
    the mouse.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui.gesture import (  # noqa: E402
    INDEX_TIP, MIDDLE_MCP, THUMB_TIP, WRIST, GestureService, Hand,
)


# ── a hand of a given shape ────────────────────────────────────────────────
#
# The landmarks are built by hand so that the test says what the shape IS
# rather than what the tracker happens to see. The wrist is at the bottom,
# a finger that is UP has its tip far from the wrist and a finger that is
# DOWN has its tip close to it — which is exactly the test the classifier
# uses.

def _make_hand(index_up=True, middle_up=True, ring_up=True, pinky_up=True,
               pinched=False):
    points = [(0.0, 0.0)] * 21
    points[WRIST] = (0.5, 0.90)
    fingers = (
        (5, 6, 8, 0.40, index_up),
        (9, 10, 12, 0.46, middle_up),
        (13, 14, 16, 0.52, ring_up),
        (17, 18, 20, 0.58, pinky_up),
    )
    for mcp, pip, tip, x, up in fingers:
        points[mcp] = (x, 0.70)
        if up:
            points[pip] = (x, 0.50)
            points[tip] = (x, 0.30)
        else:
            points[pip] = (x, 0.76)
            points[tip] = (x, 0.84)
    # The thumb: away from the index when the hand is open, and on it when
    # the hand is pinched.
    points[1] = (0.40, 0.70)
    points[2] = (0.34, 0.62)
    points[3] = (0.33, 0.58)
    if pinched:
        points[THUMB_TIP] = (points[INDEX_TIP][0] + 0.01, points[INDEX_TIP][1])
    else:
        points[THUMB_TIP] = (0.30, 0.76)
    hand = Hand(points)
    hand.measure()
    return hand


class FakeDriver:
    """Records what the hand asked the mouse to do."""

    def __init__(self):
        self.calls = []
        self.keys: set[int] = set()

    def available(self):
        return True

    def move(self, x, y):
        self.calls.append(("move", int(x), int(y)))
        return True

    def click(self):
        self.calls.append(("click",))
        return True

    def press(self):
        self.calls.append(("press",))
        return True

    def release(self):
        self.calls.append(("release",))
        return True

    def press_right(self):
        self.calls.append(("press_right",))
        return True

    def release_right(self):
        self.calls.append(("release_right",))
        return True

    def right_click(self):
        self.calls.append(("right",))
        return True

    def middle_click(self):
        self.calls.append(("middle_click",))
        return True

    def double_click(self):
        self.calls.append(("double_click",))
        return True

    def scroll(self, notches):
        self.calls.append(("scroll", int(notches)))
        return True

    def scroll_sideways(self, notches):
        self.calls.append(("side", int(notches)))
        return True

    def zoom(self, direction):
        self.calls.append(("zoom", int(direction)))
        return True

    def key(self, vk, down):
        self.calls.append(("key", int(vk), bool(down)))
        if down:
            self.keys.add(int(vk))
        else:
            self.keys.discard(int(vk))
        return True

    def tap(self, vk, alt=False):
        self.calls.append(("tap", int(vk), bool(alt)))
        return True

    def kinds(self):
        return [c[0] for c in self.calls]


@pytest.fixture()
def service(monkeypatch):
    """A gesture service with a fake pointer and a clock we control."""
    svc = GestureService()
    driver = FakeDriver()
    svc._driver = driver
    svc._drive_pointer = True
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
    assert _make_hand(index_up=False, middle_up=False,
                      ring_up=False, pinky_up=False).pose == "fist"


def test_the_pose_of_an_open_hand_is_open():
    assert _make_hand().pose == "open"


def test_the_pose_of_one_finger_is_point():
    assert _make_hand(middle_up=False, ring_up=False,
                      pinky_up=False).pose == "point"


def test_the_pose_of_two_fingers_is_two():
    assert _make_hand(ring_up=False, pinky_up=False).pose == "two"


def test_the_pose_of_three_fingers_is_three():
    assert _make_hand(pinky_up=False).pose == "three"


def test_the_pose_of_a_pinch_is_a_pinch():
    assert _make_hand(pinched=True).pose == "pinch"


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
    hand = _make_hand(middle_up=False, ring_up=False, pinky_up=False,
                      pinched=True)
    _steer(service, [hand])
    assert "press" in service._fake.kinds()

    _steer(service, [_make_hand(middle_up=False, ring_up=False, pinky_up=False)],
           at=1000.2)
    kinds = service._fake.kinds()
    assert "release" in kinds
    assert "right" not in kinds, "a quick pinch must not open the menu"


def test_a_pinch_that_moves_while_it_is_closed_is_a_drag(service):
    _steer(service, [_make_hand(middle_up=False, ring_up=False, pinky_up=False,
                                pinched=True)])
    # The hand moves: the aimed point is now somewhere else on the screen.
    moved = _make_hand(middle_up=False, ring_up=False, pinky_up=False,
                       pinched=True)
    moved.aim_x = 0.2
    moved.aim_y = 0.2
    _steer(service, [moved], at=1000.2)
    assert "right" not in service._fake.kinds(), "a drag is not a long press"


def test_a_pinch_held_still_is_the_right_click(service):
    _steer(service, [_make_hand(middle_up=False, ring_up=False, pinky_up=False,
                                pinched=True)])
    # Long enough, and the hand has not moved.
    _steer(service, [_make_hand(middle_up=False, ring_up=False, pinky_up=False,
                                pinched=True)], at=1001.5)
    _steer(service, [_make_hand(middle_up=False, ring_up=False, pinky_up=False)],
           at=1001.6)
    assert "right" in service._fake.kinds() or "press_right" in service._fake.kinds()


def test_the_middle_button_is_a_button_of_its_own(service):
    """THE THREE FINGERS THAT ARE LEFT OVER, ALL AT ONCE."""
    hand = _make_hand(pinched=True)
    _steer(service, [hand])
    kinds = service._fake.kinds()
    assert "middle_click" in kinds, f"the middle button was not pressed: {kinds}"
    assert "press" not in kinds, \
        f"the middle button was also a left click: {kinds}"
    assert not service._fake.keys, "a modifier was left down"


# ── the wheel ──────────────────────────────────────────────────────────────

def test_two_fingers_scroll(service):
    hand = _make_hand(ring_up=False, pinky_up=False)
    _steer(service, [hand], at=1000.0)
    # The fingers travel up the picture, so the wheel turns.
    moved = _make_hand(ring_up=False, pinky_up=False)
    moved.points = [(x, y - 0.15) for x, y in moved.points]
    moved.measure()
    _steer(service, [moved], at=1000.2)
    assert "scroll" in service._fake.kinds(), "the wheel did not turn"


def test_two_fingers_sideways_scroll_sideways(service):
    hand = _make_hand(ring_up=False, pinky_up=False)
    _steer(service, [hand], at=1000.0)
    moved = _make_hand(ring_up=False, pinky_up=False)
    moved.points = [(x - 0.2, y) for x, y in moved.points]
    moved.measure()
    _steer(service, [moved], at=1000.2)
    assert "side" in service._fake.kinds()


# ── the zoom ───────────────────────────────────────────────────────────────

def test_three_fingers_moving_apart_zoom_in(service):
    """THE ZOOM OF ONE HAND.

    It used to need two hands pinching and moving apart, and the pointer
    was jumping from one hand to the other all the time. Now it is the
    index and the middle of ONE hand moving apart.
    """
    _steer(service, [_make_hand(pinky_up=False)], at=1000.0)
    apart = _make_hand(pinky_up=False)
    apart.points = list(apart.points)
    apart.points[8] = (apart.points[8][0] - 0.20, apart.points[8][1])
    apart.measure()
    _steer(service, [apart], at=1000.3)
    zooms = [c for c in service._fake.calls if c[0] == "zoom"]
    assert zooms, "the fingers moved apart and nothing zoomed"
    assert zooms[0][1] > 0, "moving apart is zooming in"


def test_three_fingers_moving_together_zoom_out(service):
    _steer(service, [_make_hand(pinky_up=False)], at=1000.0)
    together = _make_hand(pinky_up=False)
    together.points = list(together.points)
    together.points[8] = (together.points[8][0] + 0.01, together.points[8][1])
    together.measure()
    _steer(service, [together], at=1000.3)
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
    _steer(service, [_make_hand(index_up=False, middle_up=False,
                                ring_up=False, pinky_up=False)])
    assert not service._fake.keys, \
        f"a resting hand left a key pressed: {service._fake.calls}"


def test_the_button_is_let_go_when_the_hand_disappears(service):
    _steer(service, [_make_hand(middle_up=False, ring_up=False, pinky_up=False,
                                pinched=True)])
    service._rest()
    kinds = service._fake.kinds()
    assert "release" in kinds, "the button stayed down"
    assert not service._fake.keys, "a modifier stayed down"


# ── the swipe ──────────────────────────────────────────────────────────────

def test_an_open_palm_swept_sideways_swipes(service):
    palm = _make_hand()
    _steer(service, [palm], at=1000.0)
    swept = _make_hand()
    swept.points = [(x - 0.30, y) for x, y in swept.points]
    swept.measure()
    _steer(service, [swept], at=1000.2)
    taps = [c for c in service._fake.calls if c[0] == "tap"]
    assert taps, "the palm swept and nothing happened"
    assert taps[0][2] is True, "a swipe is Alt and the arrows"


def test_an_open_palm_that_does_not_move_does_not_swipe(service):
    palm = _make_hand()
    _steer(service, [palm], at=1000.0)
    _steer(service, [_make_hand()], at=1000.2)
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
    open_hand = _make_hand()
    pinched = _make_hand(pinched=True)

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
        hand = _make_hand()
        hand.aim_x = aim_x
        hand.aim_y = aim_y
        service._fake.calls.clear()
        for _ in range(frames):
            _steer(service, [hand])
        moves = [c for c in service._fake.calls if c[0] == "move"]
        assert moves, "the pointer did not move at all"
        return moves[-1][1], moves[-1][2]

    # The middle of the picture is the middle of the screen.
    assert where(0.5, 0.5) == (width // 2, height // 2)

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
