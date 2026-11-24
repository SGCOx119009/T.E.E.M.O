"""core/hand_spec.py — what a hand IS, with no native code in it.

WHY THIS FILE EXISTS
    The hand tracker was MediaPipe's, and MediaPipe is a native library with
    threads of its own. It used to run in the process that draws the
    interface, and that combination — a native graph next to a repaint, next
    to the Qt event loop — was the last crash that this product had not yet
    explained: «Windows fatal exception: access violation», in a thread with
    nothing to do with the hands.

    So the tracking moved to the process that already holds the camera
    (`core/camera_worker.py`), and the process that draws the interface
    never loads MediaPipe at all.

    What is left in the interface is THIS file: the shape of a hand, the
    pinch, the pose. It is arithmetic on numbers — pure Python, no threads
    of its own, no native library. It can be imported by anybody, anywhere,
    without opening a door that should stay closed.

THE TWO SIDES OF THE SAME HAND
    The worker measures the hand and sends the numbers; the interface reads
    them and decides what they mean. The numbers travel as JSON, so what
    crosses the line between the processes is data and never a live object:
    a graph that belongs to one process cannot be freed by another.

THE PAYLOAD
    A hand is 21 points in normalised image space, a label ("Left",
    "Right"), and the measurements taken from them: the span of the hand,
    how closed the pinch is, the pose, which fingers are out, and the point
    the pointer is aimed at. All of it is a number or a string, and all of
    it fits in `to_payload()` and comes back in `from_payload()` without
    losing a thing.
"""
from __future__ import annotations

import math

# The 21 landmarks and the bones between them. This is MediaPipe's topology,
# and it is the same list the reference HUD in the AGENTES folder draws.
WRIST = 0
THUMB_TIP = 4
INDEX_TIP = 8
MIDDLE_MCP = 9
TIPS = (4, 8, 12, 16, 20)
BONES = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
)

# ── the pinch, in numbers ────────────────────────────────────────────────────
#
# ONE LINE, THREE NAMES, AND THE REASON IS THE SAME IN ALL THREE.
#
# The pinch is the button of the mouse, and a button that flickers is a
# button that clicks by itself. The fingers close and open around a
# line, and a single line makes the button go down and come up and go
# down again, several times a second, while the user believes they are
# holding it still.
#
# So there are two lines: the button goes DOWN at PINCH_ON and comes UP
# at PINCH_OFF, and in between nothing happens at all. The third number
# is not about the gap: it is about the index. In a PINCH the index is
# out in front, in the air, and its tip is far from the knuckle of the
# middle finger; in a FIST the index is folded onto the palm and its tip
# is right next to that knuckle, and the thumb of a fist is touching the
# index too. Without that third number a resting hand holds the mouse
# button down.
PINCH_ON = 0.78
PINCH_OFF = 0.52
# HOW FAR THE INDEX IS FROM THE KNUCKLE OF THE MIDDLE FINGER, IN SPANS OF
# THE HAND, FOR THE PINCH TO BE A PINCH.
#
# THESE NUMBERS COME FROM THE CANONICAL HAND OF MEDIAPIPE, NOT FROM A
# GUESS. In the canonical hand (the one the model was trained on):
#
#     open hand    the index tip is 0.89 spans from the middle knuckle
#     pinch        the index tip comes down to the thumb: 0.55
#     fist         the index folds onto the palm: 0.26
#
# The threshold used to be 0.85, which is the open hand and NOT the pinch:
# a real pinch was below the line, so a pinch was read as a fist and the
# user saw "LEFT FIST" on the glass while he was pinching. The line is
# now between the pinch and the fist, with room on both sides.
PINCH_REACH = 0.40


class Hand:
    """One hand, as the tracker sees it."""

    __slots__ = ("points", "handedness", "pinched", "closeness", "x", "y",
                 "span", "pose", "aim_x", "aim_y", "fingers", "index_reach")

    def __init__(self, points, handedness: str = ""):
        self.points = points            # 21 x (x, y) in normalised image space
        self.handedness = handedness
        self.pinched = False
        self.closeness = 0.0
        self.x = 0.0
        self.y = 0.0
        # THE POINT THE POINTER GOES TO.
        #
        # Not the pinch. The pinch is where the fingers meet, and the
        # fingers MEET: closing them moves the point, so the pointer
        # jumps at the exact moment of the click, and the click lands
        # where the hand was instead of where it is.
        #
        # The knuckle of the middle finger does not move when the hand
        # closes: that is where the pointer is aimed, and the pinch is
        # only the button. (The idea is from the gesture project the
        # owner downloaded: `Gesture-Recognition-main`, which aims with
        # the same knuckle.)
        self.aim_x = 0.0
        self.aim_y = 0.0
        self.span = 1.0
        self.pose = "open"
        # WHICH FINGERS ARE OUT, ONE BY ONE.
        #
        # The shape of the hand is not one word: it is five answers,
        # and the fingers that are not making the pinch are the ones
        # that hold the modifier keys. A pinch with the middle finger
        # up is a click with the shift down; the same pinch with the
        # ring finger up is a click with the control down. This is how
        # a real mouse is held, and it is the reason one hand is enough
        # to be a whole mouse.
        self.fingers = {"thumb": False, "index": False, "middle": False,
                        "ring": False, "pinky": False}
        # HOW FAR THE INDEX IS FROM THE KNUCKLE OF THE MIDDLE FINGER, IN
        # SPANS OF THE HAND.
        #
        # It is what tells a PINCH from a FIST. In a pinch the index is
        # out in front, in the air, and its tip is far from that knuckle.
        # In a fist the index is folded down onto the palm and its tip
        # is right next to it — and the thumb of a fist is also touching
        # the index. Without this the fist was read as a pinch, and a
        # hand that was simply resting held the mouse button down.
        self.index_reach = 1.0

    def measure(self) -> None:
        """Work out the span, the aimed point and how closed the pinch is.

        The aimed point is NOT the raw fingertip: it is the middle of the
        thumb and the index, because that is where a press actually lands.
        """
        if len(self.points) < 21:
            return
        wrist = self.points[WRIST]
        middle = self.points[MIDDLE_MCP]
        self.span = max(1e-4, math.hypot(middle[0] - wrist[0], middle[1] - wrist[1]))
        thumb = self.points[THUMB_TIP]
        index = self.points[INDEX_TIP]
        gap = math.hypot(thumb[0] - index[0], thumb[1] - index[1])
        self.closeness = max(0.0, min(1.0, 1.0 - gap / (self.span * 0.85)))
        # THE INDEX HAS TO BE OUT FOR THE PINCH TO BE A PINCH.
        self.index_reach = (math.hypot(index[0] - middle[0], index[1] - middle[1])
                            / max(1e-4, self.span))
        self.fingers["thumb"] = self._extended(2, THUMB_TIP)
        self.fingers["index"] = self._extended(6, INDEX_TIP)
        self.fingers["middle"] = self._extended(10, 12)
        self.fingers["ring"] = self._extended(14, 16)
        self.fingers["pinky"] = self._extended(18, 20)
        self.pinched = (self.closeness > PINCH_ON
                        and self.index_reach > PINCH_REACH)
        self.x = (thumb[0] + index[0]) / 2.0
        self.y = (thumb[1] + index[1]) / 2.0
        self.aim_x = float(middle[0])
        self.aim_y = float(middle[1])
        self.pose = self._classify()

    # ── the pose ─────────────────────────────────────────────────────────────
    #
    # A pose is WHICH FINGERS ARE OUT. It is not a guess about intention:
    # it is the shape of the hand, read from the landmarks, and every
    # action of the mouse is attached to one of these shapes. Reading the
    # shape and reading the intention are different things, and only the
    # first one can be done without asking the user.

    def _extended(self, pip: int, tip: int) -> bool:
        """Is the finger standing up?

        The tip has to be further from the wrist than the knuckle in front
        of it. A finger that is curled towards the palm brings its tip
        CLOSER to the wrist than its own knuckle, so the test says which
        way the finger is pointing without needing an angle.
        """
        if len(self.points) < 21:
            return False
        wrist = self.points[WRIST]
        a = math.hypot(self.points[tip][0] - wrist[0],
                       self.points[tip][1] - wrist[1])
        b = math.hypot(self.points[pip][0] - wrist[0],
                       self.points[pip][1] - wrist[1])
        return a > b * 1.12

    def _classify(self) -> str:
        """The name of the shape."""
        f = self.fingers
        if self.span < 1e-3:
            # A shape with no size at all is not a hand: it is the
            # tracker's answer without any information in it. It is
            # called a blob so that it is never mistaken for a pose.
            return "blob"
        if self.pinched:
            # The pinch wins over everything: the fingers that are not
            # making it are the modifiers, and the modifiers are said
            # apart when the press begins, not here.
            return "pinch"
        index = bool(f["index"])
        middle = bool(f["middle"])
        ring = bool(f["ring"])
        pinky = bool(f["pinky"])
        out = index + middle + ring + pinky
        if out == 0:
            return "fist"
        if out == 4:
            return "open"
        if index and middle and not ring and not pinky:
            return "two"
        if index and not middle and not ring and not pinky:
            return "point"
        if out >= 3:
            return "three"
        return "hand"

    # ── across the process boundary ──────────────────────────────────────────

    def to_payload(self) -> dict:
        """This hand as plain data, to be sent to the other process."""
        return {
            "points": [[float(x), float(y)] for x, y in self.points],
            "handedness": str(self.handedness or ""),
            "pinched": bool(self.pinched),
            "closeness": float(self.closeness),
            "x": float(self.x),
            "y": float(self.y),
            "span": float(self.span),
            "pose": str(self.pose or ""),
            "aim_x": float(self.aim_x),
            "aim_y": float(self.aim_y),
            "fingers": {str(k): bool(v) for k, v in self.fingers.items()},
            "index_reach": float(self.index_reach),
        }

    @classmethod
    def from_payload(cls, payload: dict) -> "Hand":
        """A hand rebuilt from the data the other process sent.

        The numbers are taken as they came: the hand was measured over
        there, and measuring it again here would be measuring something
        else."""
        hand = cls([], str(payload.get("handedness") or ""))
        points = []
        for point in payload.get("points") or []:
            try:
                points.append((float(point[0]), float(point[1])))
            except (TypeError, ValueError, IndexError):
                continue
        hand.points = points
        hand.pinched = bool(payload.get("pinched"))
        hand.closeness = float(payload.get("closeness") or 0.0)
        hand.x = float(payload.get("x") or 0.0)
        hand.y = float(payload.get("y") or 0.0)
        hand.span = float(payload.get("span") or 1.0)
        hand.pose = str(payload.get("pose") or "open")
        hand.aim_x = float(payload.get("aim_x") or 0.0)
        hand.aim_y = float(payload.get("aim_y") or 0.0)
        hand.index_reach = float(payload.get("index_reach") or 1.0)
        fingers = payload.get("fingers")
        if isinstance(fingers, dict):
            for name in ("thumb", "index", "middle", "ring", "pinky"):
                hand.fingers[name] = bool(fingers.get(name))
        return hand


def hands_to_payload(hands) -> list[dict]:
    """A list of hands as plain data."""
    return [hand.to_payload() for hand in hands or []]


def hands_from_payload(payloads) -> list[Hand]:
    """A list of hands rebuilt from plain data."""
    hands = []
    for item in payloads or []:
        if not isinstance(item, dict):
            continue
        hands.append(Hand.from_payload(item))
    return hands
