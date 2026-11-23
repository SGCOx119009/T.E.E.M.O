"""
core/hand_geometry.py — the canonical hand, in numbers.

WHAT THIS IS
    The proportions of the hand that MediaPipe's model was trained on,
    written down as the twenty-one points the tracker returns.

WHY IT IS IN THE PRODUCT
    The thresholds of the vocabulary — when a pinch is a pinch, when a
    fist is a fist — are only meaningful against a real hand. They were
    measured against hands drawn by hand in a test, with the fingers
    spread wide apart, and that is exactly why a real pinch came out as
    a fist: the hand of the test was not the hand of the user.

    The threshold of the reach was 0.85, and the canonical hand says:

        open hand    the index tip is 0.89 spans from the middle knuckle
        pinch        the index comes down to the thumb: 0.58
        fist         the index folds onto the palm: 0.10

    So the line was drawn on the open hand and the pinch was below it.

    The canonical hand lives here, in the product, so that the tests and
    the measuring instruments measure against the same thing, and so that
    the numbers of the vocabulary can be read by whoever changes them.

THE FINGERS
    A finger that is out has its tip far from the wrist; a finger that is
    folded brings its tip closer to the wrist than its own knuckle; a
    finger that is half-curved — the shape a hand has while it pinches
    something — is in between, and the classifier calls it "not out".
    That last one is what makes a natural pinch a plain click and not a
    click with three modifiers.
"""
from __future__ import annotations

__all__ = ["CANONICAL", "POSES", "hand_points", "mirrored_points", "placed"]

# THE CANONICAL HAND, the right one, in the normalised space of the
# picture: x to the right, y downwards, the wrist at the bottom.
CANONICAL: tuple[tuple[float, float], ...] = (
    (0.50, 0.95),   # 0  WRIST
    (0.42, 0.82),   # 1  THUMB_CMC
    (0.35, 0.75),   # 2  THUMB_MCP
    (0.30, 0.68),   # 3  THUMB_IP
    (0.27, 0.62),   # 4  THUMB_TIP
    (0.38, 0.68),   # 5  INDEX_MCP
    (0.36, 0.55),   # 6  INDEX_PIP
    (0.35, 0.47),   # 7  INDEX_DIP
    (0.35, 0.40),   # 8  INDEX_TIP
    (0.45, 0.65),   # 9  MIDDLE_MCP
    (0.45, 0.51),   # 10 MIDDLE_PIP
    (0.45, 0.43),   # 11 MIDDLE_DIP
    (0.45, 0.37),   # 12 MIDDLE_TIP
    (0.52, 0.66),   # 13 RING_MCP
    (0.53, 0.53),   # 14 RING_PIP
    (0.54, 0.46),   # 15 RING_DIP
    (0.54, 0.40),   # 16 RING_TIP
    (0.58, 0.68),   # 17 PINKY_MCP
    (0.60, 0.57),   # 18 PINKY_PIP
    (0.61, 0.51),   # 19 PINKY_DIP
    (0.62, 0.46),   # 20 PINKY_TIP
)

# THE FOUR FINGERS, and where their joint and their tip are.
_FINGERS = (
    # (mcp, pip, dip, tip)
    (5, 6, 7, 8),
    (9, 10, 11, 12),
    (13, 14, 15, 16),
    (17, 18, 19, 20),
)

# THE POSES THE VOCABULARY KNOWS.
POSES = ("open", "pinch", "fist", "point", "two", "three",
         "thumb_up", "thumb_down", "l_shape")

# WHICH FINGERS ARE OUT IN EACH POSE.
_OUT = {
    "open": (True, True, True, True),
    "pinch": (False, False, False, False),   # the index is in the pinch
    "fist": (False, False, False, False),
    "point": (True, False, False, False),
    "two": (True, True, False, False),
    "three": (True, True, True, False),
    # THE THUMB IS A POSE ON ITS OWN: the four fingers are folded and
    # the thumb is the only thing that says something.
    "thumb_up": (False, False, False, False),
    "thumb_down": (False, False, False, False),
    # THE L: the index up and the thumb sideways.
    "l_shape": (True, False, False, False),
}

# THE TIP OF A FINGER THAT IS HALF-CURVED: the shape a hand has while it
# pinches something, and the shape the other fingers have in a real pinch.
_HALF = 0.045
# THE TIP OF A FINGER THAT IS FOLDED ONTO THE PALM.
_FOLDED = 0.115


def _folded_tip(pip: tuple[float, float], amount: float) -> tuple[float, float]:
    """Where the tip of a bent finger ends up: below its own joint."""
    return (pip[0] + 0.012, pip[1] + amount)


def hand_points(pose: str = "open",
                out: tuple[bool, bool, bool, bool] | None = None,
                spread: float = 0.0,
                ) -> list[tuple[float, float]]:
    """The twenty-one points of a hand in one of the known poses.

    THE PINCH IS BUILT AS A PINCH. The index comes down to the thumb and
    the two touch: the gap between their tips is a couple of millimetres,
    and the index is still out in front of the knuckle of the middle
    finger, which is what tells a pinch from a fist.

    `out` says which of the four fingers are out (index, middle, ring,
    pinky) when that is not what the pose says by itself. It is how a
    pinch with the middle finger out — the shift — is written down.

    `spread` moves the middle finger away from the index, without moving
    the hand itself. It is the zoom: the index stays where it was and the
    middle one walks away from it, and the distance between the two tips
    is what the zoom is made of.
    """
    pose = str(pose or "open")
    points = [tuple(p) for p in CANONICAL]
    which = out if out is not None else _OUT.get(pose, _OUT["open"])

    # THE FINGERS THAT ARE NOT THE INDEX.
    for index, (mcp, pip, dip, tip) in enumerate(_FINGERS):
        if index == 0:
            continue                      # the index is dealt with below
        if which[index]:
            continue                      # it stays as the canonical hand
        # A finger that is not out: half-curved in a pinch, folded in the
        # other poses.
        amount = _HALF if pose == "pinch" else _FOLDED
        points[pip] = (points[pip][0], points[pip][1])
        points[dip] = _folded_tip(points[pip], amount * 0.55)
        points[tip] = _folded_tip(points[pip], amount)

    if pose == "fist":
        # EVERY FINGER GOES INTO THE PALM, and the thumb is tucked in
        # with them: the thumb of a fist is next to the index, not out
        # in front of it.
        for index, (mcp, pip, dip, tip) in enumerate(_FINGERS):
            points[dip] = _folded_tip(points[pip], _FOLDED * 0.55)
            points[tip] = _folded_tip(points[pip], _FOLDED)
        points[3] = (0.38, 0.70)
        points[4] = (0.43, 0.66)
        return points

    if pose == "pinch":
        # THE INDEX COMES DOWN TO THE THUMB.
        points[6] = (0.355, 0.545)
        points[7] = (0.330, 0.545)
        points[8] = (0.308, 0.560)
        points[3] = (0.335, 0.545)
        points[4] = (0.300, 0.575)
        return points

    if pose == "thumb_up":
        # THE THUMB ALONE, POINTING UP.
        #
        # The four fingers are folded — including the index, which the
        # loop above skips because the index is dealt with by the poses
        # that need it — and the thumb is raised: its tip is high above
        # the knuckles and the chain of the thumb is long enough to be
        # read as a pointing thumb and not as the thumb of a resting hand.
        for index, (mcp, pip, dip, tip) in enumerate(_FINGERS):
            points[dip] = _folded_tip(points[pip], _FOLDED * 0.55)
            points[tip] = _folded_tip(points[pip], _FOLDED)
        points[2] = (0.46, 0.68)
        points[3] = (0.44, 0.56)
        points[4] = (0.42, 0.44)
        return points

    if pose == "thumb_down":
        # THE THUMB ALONE, POINTING DOWN: the tip is BELOW the knuckles
        # and below the palm, which is what says "down" and not "up".
        for index, (mcp, pip, dip, tip) in enumerate(_FINGERS):
            points[dip] = _folded_tip(points[pip], _FOLDED * 0.55)
            points[tip] = _folded_tip(points[pip], _FOLDED)
        points[2] = (0.44, 0.66)
        points[3] = (0.43, 0.76)
        points[4] = (0.42, 0.86)
        return points

    if pose == "l_shape":
        # THE L: THE INDEX UP AND THE THUMB SIDEWAYS.
        #
        # The two of them are perpendicular, which is what makes it an L
        # and not a pointing finger: the angle between the chain of the
        # index and the chain of the thumb is a right angle.
        points[2] = (0.40, 0.70)
        points[3] = (0.31, 0.66)
        points[4] = (0.22, 0.66)
        return points

    if spread:
        # THE MIDDLE FINGER WALKS AWAY FROM THE INDEX: the zoom, without
        # moving the hand itself. Only the chain of the middle finger
        # moves, so the pose stays the pose.
        for idx in (10, 11, 12):
            points[idx] = (points[idx][0] + spread, points[idx][1])

    return points


def mirrored_points(points) -> list[tuple[float, float]]:
    """The same hand, seen in a mirror."""
    return [(1.0 - x, y) for x, y in points]


def placed(points,
           dx: float = 0.0,
           dy: float = 0.0,
           scale: float = 1.0,
           origin: tuple[float, float] | None = None,
           ) -> list[tuple[float, float]]:
    """The same hand, somewhere else, and bigger or smaller.

    WHY THE SIZE IS A PARAMETER AND NOT A LUCKY ACCIDENT

        A hand that is twice as far from the camera is half as big in the
        picture, and every distance measured in pixels changes with it. A
        vocabulary that measures in pixels therefore breaks when the user
        moves their arm: the same pinch stops being a pinch.

        So the numbers of the vocabulary are ratios — the gap of the pinch
        divided by the size of the palm — and this function is how a test
        says "the same gesture, from twice as far away". The hand grows
        about its own wrist, and every measurement made of it must come
        out the same.

        The same function places the second hand of the two-handed
        gestures: one hand here, the other there.
    """
    pts = [tuple(p) for p in points]
    if not pts:
        return pts
    ox, oy = origin if origin is not None else pts[0]
    out: list[tuple[float, float]] = []
    for x, y in pts:
        nx = ox + (float(x) - ox) * float(scale) + float(dx)
        ny = oy + (float(y) - oy) * float(scale) + float(dy)
        out.append((nx, ny))
    return out
