"""
gui/gesture.py — the hands are the mouse.

WHAT THIS IS
    The cursor you steer with. A camera watches the user's hand; the
    skeleton of the hand is drawn on the glass; the fingertip is the
    cursor; pinching closes it, and the pinch is a click.

WHY THE HANDS ARE DRAWN AND NOT JUST USED
    A touchless interface with no visible cursor is a guessing game: you
    cannot see your own hand against the screen, so you are aiming at a
    button whose position relative to your finger you can only infer. A dot
    fixes that much. Drawing the whole skeleton fixes the rest — you can
    watch the pinch closing before it fires, see which finger the cursor is
    riding, and tell at once when the tracking has lost you rather than
    wondering why nothing responds.
    (This is the `Pointer.tsx` of the reference HUD in the AGENTES folder,
    and the reason it is drawn.)

THE THREE BACKENDS, IN ORDER
    1. **MediaPipe's HandLandmarker** — 21 landmarks per hand, accurate.
       The model file is `models/hand_landmarker.task`.
    2. **The legacy MediaPipe solutions API**, when the installed version
       still has it.
    3. **OpenCV alone**: skin segmentation, the convex hull, the fingertips.
       Much worse — two fingers at a time and no depth — but it works with
       nothing installed at all.

THE CURSOR ACTUALLY MOVES
    The aimed point is moved onto the real pointer with the Windows API and
    the pinch sends a real left click. It is the same thing the keyboard
    does, only the hand is the keyboard: the interface does not need to
    know about it.

ONE HAND, NOT TWO
    The tracker reads ONE hand (`num_hands=1`). Two hands were worse, not
    richer: the pointer was aimed at whichever hand the tracker happened to
    report first, so it teleported from one side of the screen to the other
    between frames — a jump no filter can repair, because the jump is in
    the target and not in the noise. One hand is also faster (about 25 ms
    of model instead of 36) and one hand is enough for a full mouse. The
    zoom that used to need two hands is now three fingers of one hand, and
    the SHIFT that used to need a second fist is now a finger of the same
    hand, pressed together with the pinch.

THE POINTER DOES NOT SHAKE
    The raw landmark of the fingertip is noisy — the model says the finger
    moved four pixels when the hand did not move at all — and a pointer
    wired straight to the camera looks like it has Parkinson's disease.
    So the aimed point goes through a ONE-EURO FILTER (`core/pointer_filter.py`):
    a low-pass filter whose cutoff rises with the speed of the hand, which
    smooths the noise while the hand is still and does not smear the
    movement while it is fast. On top of it, a deadband of 100 px/s (the
    speed below which what the model reports is noise, not intention) and a
    forward lead of 20 ms, which cancels most of the delay of the camera.
    The numbers were measured, not guessed: `instrumentos/manos_calibra.py`
    sweeps them and `instrumentos/manos_medida.py` measures the result —
    the tremor of the model (4 px) comes out at 0.5 px, and the latency is
    +15 ms at a slow hand and NEGATIVE (it runs ahead of the hand) at 1500
    px/s.

THE VOCABULARY — WHAT ONE HAND CAN SAY
    A hand is not a pinch. The skeleton carries twenty-one landmarks, and
    they are enough to tell a dozen things apart — so the hand is a full
    mouse, not a single button. The fingers that are NOT making the pinch
    are the modifiers, exactly as in a real mouse:

    * `PINCH`      a click, on the release of the pinch, with the position
                   of the press as the reference. Two pinches close
                   together are a DOUBLE CLICK.
    * `PINCH` held still for three quarters of a second is the RIGHT
                   click — the context menu, the same way a long press is
                   the context menu on a touch screen. It is the right
                   click and NOTHING else: the left button is released
                   first, so a long press does not select a thing AND open
                   its menu, which is two actions out of one gesture.
    * `PINCH` that MOVES while it is closed is a drag: that is what lets
                   the hand pick up the floating panel and put it wherever
                   it belongs.
    * `PINCH`+`MIDDLE`  the SHIFT key, for selecting a group of things.
    * `PINCH`+`RING`    the CTRL key.
    * `PINCH`+`PINKY`   the ALT key.
    * `PINCH`+`THREE`   the MIDDLE CLICK, the wheel of a mouse.
    * `TWO`        index and middle up, the other fingers down: the wheel.
                   Up and down scrolls, sideways scrolls sideways.
    * `THREE`      index, middle and ring up: the ZOOM, the same Ctrl+wheel
                   every drawing program uses, measured on the distance
                   between the index and the middle finger.
    * `OPEN`       an open palm swept sideways is a swipe: back and
                   forward, the way a browser understands them.
    * `FIST`       everything let go: the pointer is released and the
                   modifiers are dropped, so a closed hand is never a
                   pinch that stays pressed by accident.
    * `POINT`      one finger: move the pointer and nothing else, for the
                   moments when a click would be a mistake.

    Every one of them is reported back to the interface, so the user can
    see that the machine read the hand the way the user meant it. A
    gesture that cannot be checked is a guess.

WHAT IS NOT DONE — THE THINGS MARKED WITH A STAR
    * `*` The model file is fetched by hand; the assistant does not
      download it by itself yet.
    * `*` The cursor takes the pointer whenever a hand is visible. It
      should only take it while the assistant is listening, and that rule
      is not written.
    * `*` The skeleton is drawn on the HUD, not on every widget.
"""
from __future__ import annotations

import atexit
import ctypes
import math
import os
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from core.gesture_spec import GestureSpec
from gui import fx
from gui.style import px_font
from gui.theme import C

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

_MODEL = Path(__file__).resolve().parent.parent / "models" / "hand_landmarker.task"

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
        # out in front, in the air, and its tip is far from the knuckle
        # of the middle finger. In a fist the index is folded down onto
        # the palm and its tip is right next to that knuckle — and the
        # thumb of a fist is also touching the index. Without this the
        # fist was read as a pinch, and a hand that was simply resting
        # held the mouse button down.
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


# ── the tracker ────────────────────────────────────────────────────────────

# THE ONE TRACKER OF THE PROCESS.
#
# The MediaPipe tracker is a native object with threads of its own, and it
# sits on a shared library. Two trackers in the same process — two windows,
# two panels, a test and the program — meant that closing one of them took
# the library out from under the other, and the process died with an access
# violation instead of a goodbye.
#
# So there is ONE tracker, opened the first time it is asked for, and it is
# NOT closed while the program runs. The operating system reclaims it when
# the program ends; until then it is the same object for everybody, and
# closing a window can never pull the ground from under another one.
#
# It is also faster: the model is loaded once, not once per window.
_SHARED_LOCK = threading.Lock()
_SHARED_LANDMARKER = None

# The timestamps handed to the tracker must rise, always. Two trackers — or
# two cameras — in the same millisecond would otherwise be told that time
# stood still, and the tracker would refuse the frame.
_STAMP_LOCK = threading.Lock()
_LAST_STAMP = 0


def _next_stamp() -> int:
    """A timestamp that rises, even when the clock does not."""
    global _LAST_STAMP
    with _STAMP_LOCK:
        stamp = int(time.time() * 1000)
        if stamp <= _LAST_STAMP:
            stamp = _LAST_STAMP + 1
        _LAST_STAMP = stamp
        return stamp


def _shared_landmarker(model_path: Path):
    """The one native tracker of the process, or None."""
    global _SHARED_LANDMARKER
    with _SHARED_LOCK:
        if _SHARED_LANDMARKER is not None:
            return _SHARED_LANDMARKER
        from mediapipe.tasks.python import BaseOptions
        from mediapipe.tasks.python.vision import (
            HandLandmarker, HandLandmarkerOptions, RunningMode,
        )
        options = HandLandmarkerOptions(
            base_options=BaseOptions(
                model_asset_path=str(model_path)),
            running_mode=RunningMode.VIDEO,
            # ONE HAND, AND NOT TWO.
            #
            # The owner asked for it, and it is also the right answer:
            #
            # * WITH TWO HANDS THE POINTER JUMPED FROM ONE HAND TO THE
            #   OTHER. The code picks the hand that is doing something, and
            #   when both were in the picture that choice changed from one
            #   picture to the next: the pointer teleported from one hand
            #   to the other, and no amount of smoothing can hide a jump
            #   that big.
            # * THE MODEL IS FASTER WITH ONE HAND. It looks for one hand
            #   instead of two, and the picture of the hand reaches the
            #   screen sooner.
            # * ONE HAND IS ENOUGH TO BE A WHOLE MOUSE. The fingers that
            #   are not making the pinch are the ones that hold the
            #   modifier keys, exactly as on a real mouse.
            #
            # TWO HANDS ARE READ AGAIN, AND THE POINTER DOES NOT JUMP.
            #
            # The two hands were dropped because the pointer followed
            # "the first one the tracker reported", and that is not
            # always the same hand: the pointer teleported across the
            # screen. The pointer now keeps ONE hand — the nearest one to
            # where it already is — and the other hand is still read:
            # two hands pinching and moving apart is the zoom, which is
            # the gesture that two hands are good for.
            num_hands=2,
            min_hand_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        _SHARED_LANDMARKER = HandLandmarker.create_from_options(options)
        return _SHARED_LANDMARKER


def _close_shared_landmarker() -> None:
    """Let go of the native tracker, in an orderly way.

    THE ORDER IS THE WHOLE POINT.
        The tracker is a native graph with threads of its own, and the
        picture it is looking at arrives on the thread of the camera.
        If the graph is freed while a frame is being tracked inside it,
        the native code is left using memory that no longer exists — and
        the program dies with an access violation in a thread that has
        nothing to do with the hands.

        So: the camera stops first (which waits for the thread that was
        reading), and only then is the tracker closed. And it IS closed:
        the interpreter used to free it on its own, whenever it pleased,
        which is the same accident with worse timing.
    """
    global _SHARED_LANDMARKER
    try:
        from core.camera import stop_all as _stop_camera
        _stop_camera()
    except Exception:                                   # noqa: BLE001
        pass
    with _SHARED_LOCK:
        landmarker = _SHARED_LANDMARKER
        _SHARED_LANDMARKER = None
    if landmarker is None:
        return
    try:
        landmarker.close()
    except Exception:                                   # noqa: BLE001
        pass


# THE TRACKER IS LET GO WHEN THE PROGRAM IS GOING AWAY.
#
# The order matters: this handler closes the tracker, and the camera's
# own handler (which waits for the thread that was reading) has to run
# BEFORE it. Calling the camera's stop from here makes the order the
# same whoever registered first.
atexit.register(_close_shared_landmarker)


class HandTracker:
    """Finds the hands in a frame.

    Tries MediaPipe first, then the legacy solutions API, then falls back
    to OpenCV alone. `backend` says which one it ended up with.
    """

    def __init__(self, model_path: str | os.PathLike | None = None):
        self.model_path = Path(model_path) if model_path else _MODEL
        self.backend = "none"
        self.error = ""
        self._landmarker = None
        self._legacy = None
        self._open()

    def _open(self) -> None:
        try:
            if self.model_path.exists():
                self._landmarker = _shared_landmarker(self.model_path)
                self.backend = "mediapipe"
                return
            self.error = f"the hand model is missing: {self.model_path}"
        except Exception as exc:                       # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}"
        # The legacy API, when the installed MediaPipe still has it.
        try:
            import mediapipe as mp
            self._legacy = mp.solutions.hands.Hands(
                static_image_mode=False,
                max_num_hands=2,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            )
            self.backend = "mediapipe-legacy"
            self.error = ""
        except Exception:
            self._legacy = None
        if self._legacy is None and self.backend == "none":
            self.backend = "opencv"

    def available(self) -> bool:
        return self.backend != "none"

    def track(self, frame_rgb) -> list[Hand]:
        """The hands in an RGB frame."""
        if frame_rgb is None:
            return []
        if self._landmarker is not None:
            return self._track_tasks(frame_rgb)
        if self._legacy is not None:
            return self._track_legacy(frame_rgb)
        return self._track_opencv(frame_rgb)

    # -- MediaPipe, the new API ---------------------------------------------

    def _track_tasks(self, frame_rgb) -> list[Hand]:
        import mediapipe as mp
        import numpy as np
        # The picture arrives in RGB, which is what MediaPipe wants.
        image = mp.Image(image_format=mp.ImageFormat.SRGB,
                         data=np.ascontiguousarray(frame_rgb))
        # The new API wants a monotonically rising timestamp.
        stamp = _next_stamp()
        # The tracker is the one of the whole process, so two cameras
        # cannot be inside it at the same time: it is asked one frame at a
        # time, and the frame that is left out is the frame nobody was
        # looking at anyway.
        with _SHARED_LOCK:
            result = self._landmarker.detect_for_video(image, stamp)
        hands: list[Hand] = []
        landmarks = getattr(result, "hand_landmarks", None) or []
        handedness = getattr(result, "handedness", None) or []
        for index, points in enumerate(landmarks):
            coords = [(lm.x, lm.y) for lm in points]
            if len(coords) < 21:
                continue
            label = ""
            try:
                label = handedness[index][0].category_name
            except Exception:
                label = ""
            hand = Hand(coords, label)
            hand.measure()
            hands.append(hand)
        return hands

    # -- MediaPipe, the legacy API ------------------------------------------

    def _track_legacy(self, frame_rgb) -> list[Hand]:
        import numpy as np

        rgb = np.ascontiguousarray(frame_rgb)
        result = self._legacy.process(rgb)
        hands: list[Hand] = []
        for item in getattr(result, "multi_hand_landmarks", None) or []:
            coords = [(lm.x, lm.y) for lm in item.landmark]
            if len(coords) < 21:
                continue
            label = ""
            try:
                label = result.multi_handedness[0]["classification"][0]["label"]
            except Exception:
                label = ""
            hand = Hand(coords, label)
            hand.measure()
            hands.append(hand)
        return hands

    # -- OpenCV alone -------------------------------------------------------

    def _track_opencv(self, frame_rgb) -> list[Hand]:
        """Skin, hull, fingertips. Crude, but it needs nothing installed.

        The fingertips are the vertices of the convex hull with the largest
        distance from the centre; the pinch is the gap between the two
        smallest of them.
        """
        import numpy as np

        from core.camera import cv2_quiet

        cv2 = cv2_quiet()
        h, w = frame_rgb.shape[:2]
        # The skin detector wants the colours the way the camera gives
        # them, and the camera gives them the other way round.
        bgr = cv2.cvtColor(np.ascontiguousarray(frame_rgb), cv2.COLOR_RGB2BGR)
        ycrcb = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)
        mask = cv2.inRange(ycrcb, np.array([0, 135, 85], dtype=np.uint8),
                           np.array([255, 180, 135], dtype=np.uint8))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        hands: list[Hand] = []
        if not contours:
            return hands
        contour = max(contours, key=cv2.contourArea)
        if cv2.contourArea(contour) < 900:
            return hands
        hull = cv2.convexHull(contour)
        hull_index = cv2.convexHull(contour, returnPoints=False).flatten()
        points = [contour.reshape(-1, 2)[i - 1] for i in hull_index
                  if 0 < i <= len(contour.reshape(-1, 2))]
        if len(points) < 5:
            return hands
        points = np.array(points, dtype=np.float32)
        centre = points.mean(axis=0)
        order = np.argsort(-np.linalg.norm(points - centre, axis=1))
        tips = points[order[:5]]
        # Normalise: the fingers are in the upper part of the blob.
        hand = Hand([(0.0, 0.0)] * 21)
        hand.points = [(0.0, 0.0)] * 21
        wrist = (float(centre[0]), float(centre[1]))
        hand.points[WRIST] = wrist
        # THE KNUCKLE THE POINTER IS AIMED AT. The blob has no fingers to
        # read, but its centre is where the knuckle of the middle finger
        # would be, and that is the point the pointer follows.
        hand.points[MIDDLE_MCP] = (float(centre[0]) / w, float(centre[1]) / h)
        for slot, (px, py) in enumerate(zip(tips, TIPS)):
            hand.points[slot] = (float(px) / w, float(py) / h)
        hand.span = max(1e-4, float(np.linalg.norm(tips[0] - centre)) / w)
        hand.measure()
        # The blob has no fingers to read: it is a skin-coloured shape, and
        # the joints of it are a guess. So it is NOT a pose — calling it
        # "fist" would hold the shift key down for ever.
        hand.pose = "blob"
        hands.append(hand)
        return hands

    def close(self) -> None:
        """Release this tracker. Calling it twice is not a mistake.

        The native tracker is NOT closed here: it is the one of the whole
        process, and closing it would take the library out from under
        anybody else who is still using it. What is closed is the legacy
        OpenCV tracker, which belongs to this object alone, and the
        reference to the shared one, which is dropped so that nobody asks
        for a tracker that has stopped working.
        """
        self._landmarker = None
        legacy = self._legacy
        self._legacy = None
        try:
            if legacy is not None:
                legacy.close()
        except Exception:
            pass


# ── moving the real pointer ────────────────────────────────────────────────

class PointerDriver:
    """Moves the real pointer and clicks it.

    The interface does not need to know about any of this: the hand is the
    keyboard, and the keyboard is the pointer.

    It is the whole mouse, not one button: the wheel, the right button,
    the zoom, and the modifier keys that the group selection needs.
    """

    # The flags of `mouse_event`. They are the constants of the Windows API
    # and they do not change.
    _LEFTDOWN, _LEFTUP = 0x0002, 0x0004
    _RIGHTDOWN, _RIGHTUP = 0x0008, 0x0010
    _MIDDLEDOWN, _MIDDLEUP = 0x0020, 0x0040
    _WHEEL, _HWHEEL = 0x0800, 0x1000

    # The virtual keys of the Windows keyboard.
    _VK_SHIFT = 0x10
    _VK_CONTROL = 0x11
    _VK_MENU = 0x12
    _VK_LEFT, _VK_RIGHT = 0x25, 0x27
    _VK_UP, _VK_DOWN = 0x26, 0x28
    # THE WINDOWS KEY AND THE TAB.
    #
    # These two are the combinations that a hand cannot do with a mouse:
    # the windows of Windows, and the maximise and the minimise.
    _VK_WIN = 0x5B
    _VK_TAB = 0x09
    # THE MIDDLE BUTTON IS NOT A KEY, BUT IT IS USED AS A MARKER.
    #
    # It is the button under the wheel of a real mouse, and it is the one
    # a gesture cannot press any other way: three fingers together make
    # the pinch, and the two that are left over are the modifiers. When
    # all three of them are up the pinch is not a modifier at all — it is
    # the middle button.
    _VK_MBUTTON = 0x1000

    def __init__(self):
        self._user32 = None
        try:
            self._user32 = ctypes.windll.user32
        except Exception:
            self._user32 = None

    def available(self) -> bool:
        return self._user32 is not None

    def move(self, x: int, y: int) -> bool:
        if self._user32 is None:
            return False
        try:
            self._user32.SetCursorPos(int(x), int(y))
            return True
        except Exception:
            return False

    def cursor_pos(self) -> tuple[int, int] | None:
        """Where the real pointer is right now, or None when it cannot be read.

        It is how the hand knows that the mouse of the user has moved:
        the pointer that the hand writes and the pointer that the mouse
        writes are the same cursor, and the one that is in the wrong
        place is the one that somebody else moved.
        """
        if self._user32 is None:
            return None
        try:
            class _POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

            point = _POINT()
            if not self._user32.GetCursorPos(ctypes.byref(point)):
                return None
            return int(point.x), int(point.y)
        except Exception:
            return None

    def click(self) -> bool:
        if self._user32 is None:
            return False
        try:
            down = 0x0002
            up = 0x0004
            self._user32.mouse_event(down, 0, 0, 0, 0)
            time.sleep(0.04)
            self._user32.mouse_event(up, 0, 0, 0, 0)
            return True
        except Exception:
            return False

    def press(self) -> bool:
        """Put the left button down, and leave it down.

        This is what turns a pinch into a drag: the button goes down when
        the fingers close and comes up when they open, so a pinch that
        moves while it is closed carries whatever was under it.
        """
        if self._user32 is None:
            return False
        try:
            self._user32.mouse_event(0x0002, 0, 0, 0, 0)
            return True
        except Exception:
            return False

    def release(self) -> bool:
        """Let the left button up."""
        if self._user32 is None:
            return False
        try:
            self._user32.mouse_event(0x0004, 0, 0, 0, 0)
            return True
        except Exception:
            return False

    def right_click(self) -> bool:
        """The other button: the context menu.

        The pinch held still for a moment is this one, exactly as a long
        press on a touch screen opens the menu of whatever is under it.
        """
        if self._user32 is None:
            return False
        try:
            self._user32.mouse_event(self._RIGHTDOWN, 0, 0, 0, 0)
            time.sleep(0.04)
            self._user32.mouse_event(self._RIGHTUP, 0, 0, 0, 0)
            return True
        except Exception:
            return False

    def press_right(self) -> bool:
        """The other button goes DOWN and stays down.

        It is what a long press becomes: the right button is pressed and
        held, and the menu opens when it comes up — the way a mouse does
        it, and not as a right click after a left click.
        """
        if self._user32 is None:
            return False
        try:
            self._user32.mouse_event(self._RIGHTDOWN, 0, 0, 0, 0)
            return True
        except Exception:
            return False

    def release_right(self) -> bool:
        """The other button comes up, and the menu opens."""
        if self._user32 is None:
            return False
        try:
            self._user32.mouse_event(self._RIGHTUP, 0, 0, 0, 0)
            return True
        except Exception:
            return False

    def middle_click(self) -> bool:
        """The button under the wheel: the one that opens things in a tab."""
        if self._user32 is None:
            return False
        try:
            self._user32.mouse_event(self._MIDDLEDOWN, 0, 0, 0, 0)
            time.sleep(0.04)
            self._user32.mouse_event(self._MIDDLEUP, 0, 0, 0, 0)
            return True
        except Exception:
            return False

    def double_click(self) -> bool:
        """Two clicks, one after the other, in the same place.

        The second click of a double click is sent here, after the first
        one has already gone out: the system sees two clicks in a row
        and calls it a double click, the way it does with a mouse.
        """
        if self._user32 is None:
            return False
        try:
            self._user32.mouse_event(self._LEFTDOWN, 0, 0, 0, 0)
            time.sleep(0.03)
            self._user32.mouse_event(self._LEFTUP, 0, 0, 0, 0)
            return True
        except Exception:
            return False

    def scroll(self, notches: int) -> bool:
        """The wheel, up and down."""
        if self._user32 is None or not notches:
            return False
        try:
            # A notch of the wheel is 120; the counter is signed, so the
            # wheel has to be given a value it can be negative about.
            delta = int(notches) * 120
            if delta > 32767 or delta < -32768:
                delta = max(-32768, min(32767, delta))
            self._user32.mouse_event(self._WHEEL, 0, 0, delta, 0)
            return True
        except Exception:
            return False

    def scroll_sideways(self, notches: int) -> bool:
        """The wheel, sideways. Few programs use it; the ones that do are
        the ones with wide documents and the ones with timelines."""
        if self._user32 is None or not notches:
            return False
        try:
            delta = int(notches) * 120
            if delta > 32767 or delta < -32768:
                delta = max(-32768, min(32767, delta))
            self._user32.mouse_event(self._HWHEEL, 0, 0, delta, 0)
            return True
        except Exception:
            return False

    def zoom(self, direction: int) -> bool:
        """The zoom: Ctrl and the wheel, which is what every program means
        by zooming."""
        if self._user32 is None or not direction:
            return False
        try:
            self._user32.keybd_event(self._VK_CONTROL, 0, 0, 0)
            time.sleep(0.02)
            self.scroll(int(direction))
            time.sleep(0.02)
            self._user32.keybd_event(self._VK_CONTROL, 0, 2, 0)
            return True
        except Exception:
            return False

    def key(self, vk: int, down: bool) -> bool:
        """Hold a key down or let it up.

        This is the shift of the group selection: a fist of one hand holds
        it down while the other hand clicks.
        """
        if self._user32 is None:
            return False
        try:
            self._user32.keybd_event(int(vk), 0, 0 if down else 2, 0)
            return True
        except Exception:
            return False

    # ── the combinations that a mouse cannot do ────────────────────────────

    def alt_tab(self) -> bool:
        """The windows of Windows, one after the other.

        Alt and the tab together, the way every keyboard has done it for
        forty years. The tab is pressed and let go between the two
        presses of the alt, so the system sees one combination and not
        two.
        """
        if self._user32 is None:
            return False
        try:
            self._user32.keybd_event(self._VK_MENU, 0, 0, 0)
            time.sleep(0.05)
            self._user32.keybd_event(self._VK_TAB, 0, 0, 0)
            time.sleep(0.04)
            self._user32.keybd_event(self._VK_TAB, 0, 2, 0)
            time.sleep(0.04)
            self._user32.keybd_event(self._VK_MENU, 0, 2, 0)
            return True
        except Exception:
            return False

    def win_tap(self, vk: int) -> bool:
        """The Windows key with an arrow: maximise, minimise, restore."""
        if self._user32 is None:
            return False
        try:
            self._user32.keybd_event(self._VK_WIN, 0, 0, 0)
            time.sleep(0.04)
            self._user32.keybd_event(int(vk), 0, 0, 0)
            time.sleep(0.04)
            self._user32.keybd_event(int(vk), 0, 2, 0)
            time.sleep(0.04)
            self._user32.keybd_event(self._VK_WIN, 0, 2, 0)
            return True
        except Exception:
            return False

    def screenshot(self) -> bool:
        """The snipping tool: the Windows key, the shift and the S."""
        if self._user32 is None:
            return False
        try:
            self._user32.keybd_event(self._VK_WIN, 0, 0, 0)
            time.sleep(0.03)
            self._user32.keybd_event(self._VK_SHIFT, 0, 0, 0)
            time.sleep(0.03)
            self._user32.keybd_event(0x53, 0, 0, 0)
            time.sleep(0.03)
            self._user32.keybd_event(0x53, 0, 2, 0)
            time.sleep(0.03)
            self._user32.keybd_event(self._VK_SHIFT, 0, 2, 0)
            time.sleep(0.03)
            self._user32.keybd_event(self._VK_WIN, 0, 2, 0)
            return True
        except Exception:
            return False

    def tap(self, vk: int, alt: bool = False) -> bool:
        """Press a key and let it go, with Alt held down while it does.

        Alt and the arrows are how every browser and every file manager
        means "back" and "forward".
        """
        if self._user32 is None:
            return False
        try:
            if alt:
                self._user32.keybd_event(self._VK_MENU, 0, 0, 0)
            self._user32.keybd_event(int(vk), 0, 0, 0)
            time.sleep(0.03)
            self._user32.keybd_event(int(vk), 0, 2, 0)
            if alt:
                self._user32.keybd_event(self._VK_MENU, 0, 2, 0)
            return True
        except Exception:
            return False


# ── the service ────────────────────────────────────────────────────────────

class GestureService(QObject):
    """Watches the camera and reports the hands.

    The camera and the tracking run on their own thread, because both are
    slow; only the result crosses into the Qt thread, through a signal.
    """

    hands_sig = pyqtSignal(object)          # list[Hand]
    state_sig = pyqtSignal(str)             # a sentence for the log
    gesture_sig = pyqtSignal(str, str)      # (pose, action) for the viewer
    # WHAT THE HAND SAYS THAT IS NOT ABOUT THE MOUSE.
    #
    # These four cross to the Qt thread, because they touch the interface
    # and the voice of the assistant, and neither may be touched from the
    # thread that reads the camera.
    confirm_sig = pyqtSignal(bool)          # True approves, False rejects
    interrupt_sig = pyqtSignal()            # stop the assistant mid-speech
    wake_sig = pyqtSignal()                 # wake T.E.E.M.O. up
    sleep_sig = pyqtSignal()                # sleep, and lock the HUD

    # ── the vocabulary, in numbers ───────────────────────────────────────────
    #
    # A pinch held still for three quarters of a second is the right
    # button; a pinch that drifts more than fourteen pixels in that time
    # is a drag and not a long press. The wheel turns once per eighteen
    # pixels of travel of the two fingers, which is roughly what a real
    # wheel does under a finger. A swipe is a quarter of a second and two
    # hundred and twenty pixels: fast enough to be a swipe and slow enough
    # not to be one by accident.
    _LONG_PRESS = 0.75
    _STILL_PX = 14
    # HOW MANY PIXELS OF TRAVEL ARE ONE NOTCH OF THE WHEEL.
    #
    # It was eighteen, which is one notch every eighteen pixels of hand:
    # a hand that moved two centimetres turned the wheel fifteen notches
    # in a single picture, and the page flew away. The user said the
    # scroll was "muy fuerte" and that there was no stable control over
    # it. A notch is a hundred and twenty pixels in Windows, and here a
    # notch is ninety: the wheel turns when the hand says so, and not
    # before. The leftovers are kept, so a slow hand turns the wheel
    # smoothly instead of in jumps.
    _SCROLL_PX = 90
    _SWIPE_SECONDS = 0.45
    _SWIPE_PX = 220
    # HOW MUCH OF THE PICTURE IS LEFT UNUSED AT THE EDGES.
    #
    # The camera sees more than the hand needs, and the corners of the
    # picture cannot be reached without the arm leaving the frame. So
    # the middle of the picture is the middle of the screen.
    _AIM_MARGIN = 0.15

    # THE PINCH HAS TWO THRESHOLDS, NOT ONE.
    #
    # With a single threshold the pinch flickers: the fingers close a
    # little past the line and open a little past it, and the button
    # goes down and comes up and goes down again, several times a
    # second. A button that chatters is a button that clicks by itself,
    # and a click that is swallowed by the debounce is a click that never
    # arrives — which is what made "I cannot select anything" true.
    #
    # So the button goes DOWN at 0.78 and comes UP at 0.52: between the
    # two nothing happens, and the pinch has to really open to let go.
    _PINCH_ON = 0.78
    _PINCH_OFF = 0.52
    # TWO PINCHES THIS CLOSE TOGETHER ARE A DOUBLE CLICK.
    _DOUBLE_CLICK = 0.35
    # THE SCREEN IS MEASURED EVERY SO OFTEN, NOT EVERY PICTURE.
    _SCREEN_EVERY = 1.0

    # THE GESTURES THAT ARE ARMED ONLY IN THE ADVANCED MODE.
    #
    # These touch the desktop of the user: they change the window, they
    # take a screenshot, they approve a thing that is waiting for an
    # answer. A gesture that is armed by accident is a gesture that
    # closes a window that was not meant to be closed — so these stay
    # off until the user asks for them, with the switch of the drawer.
    _ADVANCED = frozenset({
        "fist_confirm", "thumb_up_approve", "thumb_down_reject",
        "three_switch", "palm_maximize", "palm_minimize", "l_dictation",
        "frame_screenshot", "two_pinch_area", "two_pinch_zoom",
        "palm_clap_sleep",
    })

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._tracker: HandTracker | None = None
        self._driver = PointerDriver()
        self._lock = threading.Lock()
        self._hands: list[Hand] = []
        self._enabled = False
        self._drive_pointer = False
        self._last_click_end = 0.0
        self._pointer_down = False
        # THE RECOGNISER OF THE WHOLE VOCABULARY.
        #
        # The mouse is decided by the measured implementation of this
        # file, because the button has to be HELD DOWN while the fingers
        # are closed, and a recogniser that only says what it saw cannot
        # hold anything down. The rest of the vocabulary — the clutch of
        # the open palm, the fist that confirms, the thumb that approves,
        # the swipes of the palm, the L and the gestures of two hands —
        # is decided by `core/gesture_spec.py`, which has the whole
        # specification written down and three checks for every gesture
        # of it.
        self._spec = GestureSpec()
        # THE ADVANCED MODE: the gestures that touch the desktop.
        self._advanced = False
        # THE CLUTCH: the open palm freezes the cursor where it is, so
        # that the arm can be moved without moving the mouse.
        self._clutched = False
        # THE TWO-HANDED GESTURES ARE IN THE MIDDLE OF SOMETHING.
        self._two_active = False
        self._area_anchor: tuple[int, int] | None = None
        self._area_last: tuple[int, int] | None = None
        self._zoom_spread = None
        # THE WHEEL OR THE ZOOM: WHERE THE GESTURE STARTED, AND WHICH OF
        # THE TWO THE HAND IS DOING.
        self._rueda_anchor = None
        self._rueda_modo = ""
        # THE STATE OF THE HAND AS A POINTER.
        self._press_at = (0, 0)
        self._press_time = 0.0
        self._press_moved = False
        # THE PINCH IS LATCHED, WITH TWO THRESHOLDS.
        #
        # The latch is what stops the button from chattering: it goes down
        # when the fingers close and comes up when they really open, and
        # nothing that happens in between can change it.
        self._pinch_latched = False
        # THE PRESS THAT BECAME THE OTHER BUTTON.
        #
        # A press that is held still for long enough is the right click,
        # and it must never ALSO be a left click: the two together select
        # the thing and open its menu, which is the opposite of what was
        # asked for.
        self._press_is_right = False
        # WHEN THE LAST CLICK ENDED, for the double click.
        self._last_click_end = 0.0
        # THE MODIFIER KEYS THAT ARE HELD DOWN RIGHT NOW.
        self._held_keys: set[int] = set()
        self._scroll_anchor = None
        self._swipe_anchor = None
        self._zoom_anchor = None
        self._screen_w = 1920
        self._screen_h = 1080
        self._screen_checked = 0.0
        # THE POINTER IS SMOOTHED, AND IT IS PUT A LITTLE AHEAD.
        #
        # Without this the pointer is exactly where the model thought the
        # hand was, and the model is a few pixels wrong every time: the
        # pointer trembled like a shaking hand and could not be held still
        # on a button. With it, a hand that is still does not move the
        # pointer at all, and a hand that moves arrives almost at once.
        from core.pointer_filter import PointerSmoother

        self._smoother = PointerSmoother()
        self._pointer_at = (0, 0)
        self._last_steer = 0.0
        # THE POINT THE HAND IS AIMING AT.
        #
        # The pointer is not written here: it is walked to this point by
        # the servo, sixty times a second, so that a hand that is read
        # fifteen times a second still moves the cursor smoothly.
        self._target: tuple[int, int] | None = None
        self._servo_last = 0.0
        # UNTIL WHEN THE HAND LEAVES THE MOUSE ALONE.
        #
        # The hand and the mouse are the same cursor. When the user
        # moves the mouse, the hand lets go for a few seconds: without
        # that, the two fought over the cursor and the screenshots of
        # Windows could not be taken, because the selection kept running
        # away from the mouse.
        self._mouse_until = 0.0
        # WHERE THE MOUSE REALLY WAS WHEN THE HAND LET GO OF IT, so that
        # the hand takes the cursor over from there and not from where it
        # left it.
        self._mouse_at: tuple[int, int] | None = None
        # THE LEFTOVERS OF THE WHEEL, so that a slow hand turns it
        # smoothly instead of in jumps.
        self._scroll_acc = 0.0
        self._scroll_side_acc = 0.0
        # THE DISTANCE BETWEEN TWO HANDS THAT ARE PINCHING: the zoom.
        self._zoom_pair_anchor = None
        # THE SERVO. It belongs to the thread of the interface, because a
        # timer of Qt has to live there.
        self._servo = QTimer(self)
        self._servo.setInterval(16)
        self._servo.timeout.connect(self._servo_step)
        # The tracker is used by the thread of the camera and closed by
        # the thread of the service: the two must not meet.
        self._frame_lock = threading.Lock()
        # THE LAST PICTURE, AND NOTHING ELSE.
        #
        # The camera hands its frames to every reader one after the other,
        # in the thread that reads the device. Decoding a JPEG and asking
        # the tracker about it takes longer than the camera takes to
        # produce the next picture, so doing it there held the other
        # readers — the live picture of the head, the tile of the viewer —
        # behind the tracker, and the picture on screen fell behind the
        # hand by whole frames.
        #
        # So the camera thread only LEAVES the picture here, overwriting
        # the one before it. The tracker thread takes the newest picture
        # and ignores the ones that piled up while it was busy: a picture
        # that is already old when it is looked at is worth nothing, and
        # throwing it away is what makes the hand feel close to the
        # screen instead of trailing behind it.
        self._latest_lock = threading.Lock()
        self._latest_jpeg: bytes | None = None
        self._latest_seq = 0
        self._tracked_seq = 0
        self._track_thread: threading.Thread | None = None
        # THE WINDOW IS GOING AWAY.
        #
        # The service is a child of the window, so when the window dies the
        # service dies with it — and the thread that is watching the camera
        # would be left holding a tracker whose native memory is about to be
        # freed underneath it. The `destroyed` signal is the last moment at
        # which the thread can still be stopped in an orderly way.
        self.destroyed.connect(self._shutdown)

    def _shutdown(self) -> None:
        """Stop the thread before the tracker is freed."""
        try:
            self.stop()
        except Exception:
            pass

    # -- the switches --------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def backend(self) -> str:
        tracker = self._tracker
        return tracker.backend if tracker else "none"

    @property
    def drives_pointer(self) -> bool:
        return self._drive_pointer

    @property
    def hands_count(self) -> int:
        """How many hands the camera can see right now."""
        with self._lock:
            return len(self._hands)

    def set_drives_pointer(self, on: bool) -> None:
        """Whether the hand takes the real pointer.

        Off by default: a hand that moves the pointer while the user is
        typing is a worse experience than no gesture control at all.
        """
        self._drive_pointer = bool(on)
        if self._drive_pointer:
            self._servo.start()
        else:
            self._servo.stop()
            self._target = None

    # -- the advanced mode ---------------------------------------------------

    @property
    def advanced(self) -> bool:
        """Whether the gestures that touch the desktop are armed."""
        return self._advanced

    def set_advanced(self, on: bool) -> None:
        """Arm the gestures that change the desktop, or disarm them.

        THE SWITCH IS OFF BY DEFAULT, and it stays off until the user
        turns it on: a palm that is pushed up while the hand is resting
        should not maximise a window that the user was reading.
        """
        self._advanced = bool(on)
        if not self._advanced:
            # DISARMING LETS GO OF EVERYTHING THAT WAS STILL HELD.
            self._clutched = False
            self._two_active = False
            if self._area_anchor is not None:
                self._area_anchor = None
                self._driver.release()
            self._zoom_spread = None

    # -- what the hand says --------------------------------------------------

    def _procesa(self, hands: list[Hand], now: float | None = None) -> None:
        """Read the hands, say what they are doing, and do it.

        THE RECOGNISER IS FED ON EVERY PICTURE, whether the hand is
        driving the pointer or not: the fist that confirms and the thumb
        that approves are useful even when the mouse is left alone.
        """
        if now is None:
            now = time.time()
        eventos = self._spec.feed(hands or [], now)
        if eventos:
            self._ejecuta(eventos, hands or [])
        # THE CLUTCH ENDS WHEN THE PALM IS NOT A PALM ANY MORE.
        if self._clutched:
            abierta = any(str(getattr(h, "pose", "")) == "open" for h in (hands or []))
            if not abierta:
                self._clutched = False
        if self._drive_pointer and hands:
            self._steer(hands, now)

    def _ejecuta(self, eventos, hands: list[Hand]) -> None:
        """Do what the hand said."""
        for evento in eventos:
            nombre = str(evento.name)
            if nombre in self._ADVANCED and not self._advanced:
                # IT IS NOT ARMED. The gesture is not even said out loud:
                # a gesture that is not armed must not be seen, or the
                # user would think it had failed.
                continue
            if nombre == "open_clutch":
                # LA PALMA QUIETA: EL EMBRAGUE.
                #
                # The cursor freezes where it is, so the arm can be moved
                # without moving the mouse — and T.E.E.M.O. goes quiet,
                # because a hand that is resting is a hand that is not
                # asking for anything.
                self._clutched = True
                self.interrupt_sig.emit()
                self._set_gesture("open", "clutch")
            elif nombre == "fist_confirm":
                self.confirm_sig.emit(True)
                self._set_gesture("fist", "confirm")
            elif nombre == "thumb_up_approve":
                self.confirm_sig.emit(True)
                self._set_gesture("thumb_up", "approve")
            elif nombre == "thumb_down_reject":
                self.confirm_sig.emit(False)
                self._set_gesture("thumb_down", "reject")
            elif nombre == "three_switch":
                self._driver.alt_tab()
                self._set_gesture("three", "switch window")
            elif nombre == "palm_maximize":
                self._driver.win_tap(PointerDriver._VK_UP)
                self._set_gesture("open", "maximize")
            elif nombre == "palm_minimize":
                self._driver.win_tap(PointerDriver._VK_DOWN)
                self._set_gesture("open", "minimize")
            elif nombre == "l_dictation":
                self.wake_sig.emit()
                self._set_gesture("l", "dictation")
            elif nombre == "frame_screenshot":
                self._driver.screenshot()
                self._set_gesture("two", "screenshot")
            elif nombre == "two_pinch_area":
                self._area(hands, evento)
            elif nombre == "two_pinch_zoom":
                self._zoom_dos(evento)
            elif nombre == "two_pinch_end":
                self._area_fin()
                self._zoom_spread = None
                self._two_active = False
            elif nombre == "palm_clap_sleep":
                self.sleep_sig.emit()
                self._set_gesture("open", "sleep")

    def _area(self, hands: list[Hand], evento) -> None:
        """THE SELECTION OF AN AREA: one hand holds still, the other moves.

        The still hand is the corner, and the moving hand is the other
        corner: the mouse goes down at the first one and is carried to
        the second one, and it comes up when the hands let go.

        THE CENTRE OF THE PALM IS WHAT IS COMPARED, because that is what
        the recogniser measured: the ninth point, the knuckle of the
        middle finger. Comparing the ancla with any other point of the
        hand would be comparing two different things.
        """
        self._two_active = True
        datos = evento.data or {}
        ancla = datos.get("anchor")
        quieta = None
        movida = None
        for mano in hands:
            puntos = getattr(mano, "points", None) or []
            if len(puntos) < 10:
                continue
            centro = (float(puntos[9][0]), float(puntos[9][1]))
            if (ancla is not None
                    and abs(centro[0] - ancla[0]) < 1e-6
                    and abs(centro[1] - ancla[1]) < 1e-6):
                quieta = centro
            else:
                movida = centro
        if quieta is None or movida is None:
            return
        if self._area_anchor is None:
            self._area_anchor = (self._map_x(quieta[0]), self._map_y(quieta[1]))
            self._area_last = self._area_anchor
            self._driver.press()
            return
        punto = (self._map_x(movida[0]), self._map_y(movida[1]))
        if punto != self._area_last:
            self._area_last = punto
            self._driver.move(punto[0], punto[1])

    def _area_fin(self) -> None:
        """The selection is over: the button comes up."""
        if self._area_anchor is not None:
            self._area_anchor = None
            self._area_last = None
            self._driver.release()

    def _zoom_dos(self, evento) -> None:
        """THE ZOOM OF TWO HANDS: how far apart they are."""
        self._two_active = True
        separacion = float((evento.data or {}).get("spread", 0.0) or 0.0)
        if self._zoom_spread is None:
            self._zoom_spread = separacion
            return
        antes = self._zoom_spread
        self._zoom_spread = separacion
        if abs(separacion - antes) < 0.01:
            return
        self._driver.zoom(1 if separacion > antes else -1)
        self._set_gesture("two", "zoom in" if separacion > antes else "zoom out")

    def start(self) -> str:
        """Start watching. Returns a sentence saying what happened."""
        if self._thread is not None:
            return "the camera is already being watched"
        self._stop.clear()
        with self._latest_lock:
            self._latest_jpeg = None
            self._latest_seq = 0
            self._tracked_seq = 0
        # TWO THREADS, ON PURPOSE.
        #
        # One waits for the camera and leaves the pictures in the slot;
        # the other looks for the hands in the newest of them. The tracker
        # is created, used and closed in that second thread, which is what
        # the native library asks for, and the camera is never held
        # waiting for the tracker.
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="gesture")
        self._thread.start()
        self._track_thread = threading.Thread(target=self._track_loop,
                                               daemon=True, name="gesture-track")
        self._track_thread.start()
        # THE SERVO RUNS ONLY WHILE THE HAND IS DRIVING THE POINTER.
        if self._drive_pointer:
            self._servo.start()
        return "watching the camera for hands"

    def stop(self) -> str:
        """Stop watching — and WAIT until the threads are really gone.

        The tracker holds native memory that the tracking library frees
        when the Python object dies. If the window goes away while the
        thread is still inside `detect_for_video`, that free lands on
        memory the native code is still using, and the process dies with
        an access violation instead of a goodbye.

        So the order is fixed and it is not negotiable: the threads are
        asked to stop, the threads are WAITED for, and only then is the
        tracker released — and the tracker closes itself in the thread
        that owns it, once that thread is really gone.
        """
        self._stop.set()
        thread = self._thread
        if thread is not None:
            if thread.is_alive():
                thread.join(timeout=5.0)
            self._thread = None
        tracker_thread = self._track_thread
        if tracker_thread is not None:
            if tracker_thread.is_alive():
                tracker_thread.join(timeout=5.0)
            self._track_thread = None
        with self._lock:
            tracker = self._tracker
            self._tracker = None
        if tracker is not None:
            # Under the frame lock: a picture that is being tracked right
            # now must be finished before the native memory is freed.
            with self._frame_lock:
                tracker.close()
        # The button and the modifiers are let go here as well: a stop that
        # leaves the shift held down is a stop that turns every later click
        # into a group selection.
        if self._pointer_down:
            self._pointer_down = False
            self._press_is_right = False
            self._driver.release()
        self._set_modifiers(set())
        self._pinch_latched = False
        self._servo.stop()
        self._target = None
        return "the camera is no longer being watched"

    def toggle(self) -> str:
        return self.stop() if self._enabled else self.start()

    # -- the loop ------------------------------------------------------------

    def _loop(self) -> None:
        """Wait for the camera, and leave every picture it gives to the tracker.

        THE CAMERA IS NOT OPENED HERE. It is the one of the whole process
        (core/camera.py), and this service is one of its readers. Two
        readers that opened the device each were two handles to the same
        camera, and the driver did not survive it: the whole program died
        with an access violation.

        THE TRACKER IS NOT CREATED HERE EITHER. It lives in the thread
        that looks for the hands (see `_track_loop`), and the native
        tracker and the pictures belong to the same thread: a tracker
        built in one thread and used in another is not a thing the native
        library promises anything about.
        """
        try:
            self.state_sig.emit("the hands are being looked for")
            self._enabled = True
            from core.camera import hub as _camera_hub
            _camera_hub().subscribe(self._on_frame)
            # The thread of the service waits here and does nothing else:
            # the frames arrive in the callback, on the thread of the
            # camera.
            while not self._stop.is_set():
                time.sleep(0.05)
        except Exception as exc:                       # noqa: BLE001
            self.state_sig.emit(f"the hand tracking stopped: {type(exc).__name__}")
        finally:
            try:
                from core.camera import unsubscribe as _unsubscribe
                _unsubscribe(self._on_frame)
            except Exception:
                pass
            self._enabled = False

    # -- the thread that looks for the hands ---------------------------------

    def _track_loop(self) -> None:
        """Look for the hands in the newest picture, and only in that one.

        This thread owns the tracker: it creates it, it uses it, and it
        closes it. The pictures arrive through the slot that `_on_frame`
        fills, and a picture that is already waiting when this thread
        comes back for it is thrown away — it is old, and an old picture
        of a hand is a hand that has moved.
        """
        tracker = None
        try:
            while not self._stop.is_set():
                with self._latest_lock:
                    jpeg = self._latest_jpeg
                    seq = self._latest_seq
                    self._latest_jpeg = None
                if not jpeg:
                    time.sleep(0.004)
                    continue
                if tracker is None:
                    try:
                        tracker = HandTracker()
                    except Exception:                   # noqa: BLE001
                        return
                    with self._lock:
                        self._tracker = tracker
                    if not tracker.available():
                        self.state_sig.emit(f"no hand tracker: {tracker.error}")
                        with self._lock:
                            self._tracker = None
                        try:
                            tracker.close()
                        except Exception:
                            pass
                        return
                    self.state_sig.emit(
                        f"the hands are tracked with {tracker.backend}")
                from core.camera import decode_jpeg

                frame = decode_jpeg(jpeg)
                if frame is None:
                    continue
                try:
                    hands = tracker.track(frame)
                except Exception:
                    continue
                with self._lock:
                    self._tracked_seq = seq
                with self._lock:
                    self._hands = hands
                self.hands_sig.emit(hands)
                # THE HAND IS READ, AND WHAT IT SAYS IS DONE — WHETHER IT
                # IS DRIVING THE POINTER OR NOT.
                self._procesa(hands)
        finally:
            # THE TRACKER IS ALWAYS CLOSED, AND NEVER UNDER A PICTURE.
            #
            # It used to be closed only at the end of a successful watch,
            # so a machine with no camera — or with no hand model, or with
            # no OpenCV — kept the native library loaded and let the
            # garbage collector free it whenever it pleased. That free
            # landed on memory the library was still using, and the
            # process died with an access violation instead of a goodbye.
            #
            # The close happens HERE, in the thread that owns the
            # tracker, and never while a picture is being tracked.
            #
            # The camera is NOT closed here: it is the one of the whole
            # process (core/camera.py), and this service is only one of
            # its readers. Closing it here would take the camera away
            # from the hands of the viewer and of the live stream.
            with self._frame_lock:
                with self._lock:
                    self._tracker = None
                if tracker is not None:
                    try:
                        tracker.close()
                    except Exception:
                        pass
            # A button left down is a button the user cannot get rid of:
            # if the hand disappeared while it was pinched, the button has
            # to come up here.
            if self._pointer_down:
                self._pointer_down = False
                self._press_is_right = False
                self._driver.release()
            # And so does a modifier that is still held: a shift left down
            # turns every click afterwards into a group selection.
            self._set_modifiers(set())
            self._pinch_latched = False
            with self._lock:
                self._hands = []
            self.hands_sig.emit([])

    def _on_frame(self, ok: bool, jpeg: bytes) -> None:
        """One picture of the camera, left for the tracker.

        It runs on the thread of the camera, and it does ONE thing: it
        leaves the picture in the slot. Decoding it and asking the
        tracker about it happens in the thread of the tracker, so the
        camera is never held waiting for the tracker, and the other
        readers of the camera get their pictures on time.
        """
        if not ok or not jpeg:
            return
        with self._latest_lock:
            self._latest_jpeg = jpeg
            self._latest_seq += 1

    def _map_x(self, value: float) -> int:
        """Where the hand is on the X of the screen.

        THE MARGIN IS THE PART THAT MAKES IT USABLE. Without it, the
        pointer only reaches the screen when the hand is at the very
        edge of the picture, and the picture is not the screen: the
        camera sees more than the hand needs, and the corners of the
        picture are unreachable in practice. So the middle of the
        picture is the middle of the screen, and the edges of the
        picture are the edges of the screen.
        """
        mirrored = 1.0 - float(value)
        return int(self._scale(mirrored) * self._screen_w)

    def _map_y(self, value: float) -> int:
        """Where the hand is on the Y of the screen."""
        return int(self._scale(float(value)) * self._screen_h)

    def _scale(self, value: float) -> float:
        """One coordinate of the hand, between 0 and 1 of the screen."""
        span = max(0.01, 1.0 - 2.0 * self._AIM_MARGIN)
        scaled = (float(value) - self._AIM_MARGIN) / span
        return max(0.0, min(1.0, scaled))

    def _measure_screen(self) -> None:
        """How big is the screen. Asked once a second, not once a picture."""
        now = time.monotonic()
        if now - self._screen_checked < self._SCREEN_EVERY:
            return
        self._screen_checked = now
        try:
            self._screen_w = int(ctypes.windll.user32.GetSystemMetrics(0))
            self._screen_h = int(ctypes.windll.user32.GetSystemMetrics(1))
        except Exception:
            return
        # A SCREEN THAT CHANGED SIZE INVALIDATES THE FILTER.
        if self._pointer_at:
            self._smoother.reset()

    def _steer(self, hands: list[Hand], now: float | None = None) -> None:
        """Move the pointer to the aimed point, and do what the hand says.

        ONE HAND IS ENOUGH FOR THE WHOLE MOUSE.

            PINCH                   the left button: click, double click,
                                   right click when it is held, drag when
                                   it moves
            PINCH + MIDDLE          the shift, while the button is down
            PINCH + RING            the control, while the button is down
            PINCH + PINKY           the alt, while the button is down
            PINCH + ALL THREE       the middle button: the wheel pressed
            TWO FINGERS             the wheel, up and down or sideways
            THREE FINGERS           the zoom, by how far the index and the
                                   middle are apart
            OPEN PALM, swept        back and forward
            FIST                    let go of everything and rest

        THE POINTER IS SMOOTHED, AND IT IS PUT A LITTLE AHEAD.

        The pointer used to be exactly where the model thought the hand
        was, and the model is a few pixels wrong every single time: the
        pointer trembled like a hand with Parkinson's and could not be
        held still on a button. Now the point goes through a filter that
        smooths a still hand hard and a moving hand hardly at all, and
        the pointer is put where the hand is going to be instead of where
        it was — which is what takes the lag of the camera out.
        """
        if not self._driver.available():
            return
        self._measure_screen()
        if not hands:
            self._rest()
            return

        now = time.time() if now is None else float(now)

        # ── WHICH HAND THE POINTER FOLLOWS ────────────────────────────────
        #
        # TWO HANDS ARE READ, AND THE POINTER KEEPS ONE OF THEM.
        #
        # With two hands the pointer followed "the first one the tracker
        # reported", and the tracker does not always report the same one
        # first: the pointer teleported from one side of the screen to
        # the other, and no filter can repair a jump that is in the
        # target. Now the pointer stays with the hand it was already
        # following — the nearest one — and it only changes hands when
        # the user pinches with the other one, which is a deliberate
        # thing to do.
        pinched = [h for h in hands if bool(getattr(h, "pinched", False))]
        if len(pinched) == 1:
            hand = pinched[0]
        else:
            hand = self._nearest_hand(hands)
        if hand is None:
            self._rest()
            return
        pose = str(getattr(hand, "pose", "") or "")

        # ── THE POINTER ────────────────────────────────────────────────────
        raw_x = self._map_x(getattr(hand, "aim_x", hand.x))
        raw_y = self._map_y(getattr(hand, "aim_y", hand.y))
        dt = 1.0 / 15.0
        if self._last_steer:
            dt = max(0.005, min(0.25, now - self._last_steer))
        self._last_steer = now
        fx, fy = self._smoother(raw_x, raw_y, dt)
        # AND IT NEVER LEAVES THE SCREEN.
        #
        # The prediction puts the pointer a little ahead of the hand, and
        # at the edge of the screen "a little ahead" is outside of it. A
        # pointer that can leave the screen is a pointer that can click on
        # the taskbar of another thing, so the edges are the edges.
        x = max(0, min(self._screen_w, int(round(fx))))
        y = max(0, min(self._screen_h, int(round(fy))))
        # THE AIMED POINT IS THE TARGET. The pointer itself is walked to
        # it sixty times a second by the servo, so the movement is
        # continuous even though the hand is read fifteen times a second.
        self._target = (x, y)

        # ── TWO HANDS PINCHING: THE ZOOM ─────────────────────────────────
        #
        # Two hands that pinch together and move apart are the zoom, and
        # they are NOT a click: a zoom is a gesture of two hands, and a
        # click that happens at the same time is a click in the wrong
        # place. The pointer stays where it is.
        if len(pinched) >= 2:
            self._release_pointer()
            self._set_modifiers(set())
            if self._advanced:
                # IN THE ADVANCED MODE THE TWO HANDS BELONG TO THE
                # SPECIFICATION: the area, the zoom, and nothing else.
                # The mouse is left alone, because a click that happens
                # at the same time as a zoom is a click in the wrong
                # place.
                return
            self._zoom_pair(pinched[:2])
            return

        # ── THE PINCH, LATCHED ─────────────────────────────────────────────
        closeness = float(getattr(hand, "closeness", 0.0) or 0.0)
        reach = float(getattr(hand, "index_reach", 1.0) or 1.0)
        can_pinch = reach > PINCH_REACH
        if self._pinch_latched:
            if (not can_pinch) or closeness < PINCH_OFF:
                self._pinch_latched = False
                self._end_press(now)
        elif can_pinch and closeness > PINCH_ON:
            self._pinch_latched = True
            self._begin_press(hand, x, y, now)

        # ── THE REST OF THE VOCABULARY ────────────────────────────────────
        if self._pinch_latched:
            self._during_press(hand, x, y, now)
            return

        # THE REST POSE LETS GO OF EVERYTHING.
        #
        # A closed hand is not a modifier any more: with one hand there
        # is no second hand to hold the shift while the first one works.
        # A closed hand is the pose of a hand that is resting, and it
        # releases the buttons and the modifiers.
        if pose == "fist":
            self._release_pointer()
            self._set_modifiers(set())
            self._set_gesture("fist", "rest")
            return

        # THE INDEX AND THE MIDDLE: THE WHEEL OR THE ZOOM.
        #
        # THE RING FINGER DOES NOT DECIDE, AND THAT IS A MEASUREMENT.
        #
        # In the live session of the real hand, a two-finger scroll was
        # read as THREE fingers seventy-two times out of a hundred: the
        # ring rests half out on a relaxed hand, and the machine was
        # counting fingers. The scroll became a zoom, and the zoom
        # became nothing at all.
        #
        # What DOES tell them apart is the index and the middle: the
        # wheel is those two travelling together with the hand, and the
        # zoom is those two getting further apart from each other.
        fingers = getattr(hand, "fingers", None) or {}
        if (fingers.get("index") and fingers.get("middle")
                and not (fingers.get("ring") and fingers.get("pinky"))):
            self._release_pointer()
            self._rueda_o_zoom(hand, now)
            return
        # THE HAND IS NOT IN THE WHEEL OR THE ZOOM: THE NEXT ONE STARTS
        # FROM SCRATCH.
        self._rueda_anchor = None
        self._rueda_modo = ""

        # AN OPEN PALM IS A SWEEP.
        if pose == "open":
            self._swipe(hand, now)
            self._set_gesture("open", "swipe")
            return

        # ONE FINGER ONLY: MOVE, AND NOTHING ELSE.
        self._set_gesture(pose or "point", "move")

    # ── the pointer that is walked, not jumped ──────────────────────────────

    def _nearest_hand(self, hands: list[Hand]) -> Hand | None:
        """The hand the pointer keeps following.

        IT IS THE NEAREST ONE TO WHERE THE POINTER ALREADY IS. The
        tracker does not always report the hands in the same order, and
        the pointer that follows "the first one" is the pointer that
        jumps. The hand that is closest to where the pointer already is
        is the hand that was already the pointer, and the pointer stays
        with it until the user says otherwise with a pinch.

        AND IT IS COMPARED WITH THE POINT THE HAND WAS AIMING AT, not
        with the place the pointer has arrived at: the pointer walks
        towards the hand, so it is always a little behind it, and asking
        "which hand is the pointer nearest to" would ask about the
        pointer instead of about the hand.
        """
        if not hands:
            return None
        if len(hands) == 1:
            return hands[0]
        if self._last_steer == 0.0:
            # THE FIRST HAND THAT APPEARS IS THE ONE THE POINTER FOLLOWS.
            #
            # There is no pointer to be near yet, so the pointer would
            # choose the hand that is closest to the corner of the screen
            # — and that is the hand that happens to be reported first.
            return hands[0]
        last = self._target
        if last is None:
            last = self._pointer_at
        best = None
        best_distance = None
        for hand in hands:
            try:
                x = self._map_x(getattr(hand, "aim_x", hand.x))
                y = self._map_y(getattr(hand, "aim_y", hand.y))
            except Exception:
                continue
            if last is None:
                distance = 0.0
            else:
                distance = math.hypot(x - last[0], y - last[1])
            if best_distance is None or distance < best_distance:
                best, best_distance = hand, distance
        return best

    def _servo_step(self) -> None:
        """Walk the pointer one step towards the aimed point.

        THE POINTER IS NOT A STEP FUNCTION. The camera gives fifteen
        pictures a second, and a pointer that is written fifteen times a
        second is seen fifteen times a second: it hops, and the hand
        feels like it is dragging something sticky. This runs sixty
        times a second and walks the pointer towards the place the hand
        is aiming at, so the movement is continuous even though the hand
        is read slowly.

        AND THE MOUSE ALWAYS WINS. If the real cursor is not where the
        hand put it, the user moved it — and the hand lets go for a few
        seconds. Without this the two fought over the same cursor: the
        screenshots of Windows could not be taken, because the selection
        kept running away from the mouse.
        """
        target = self._target
        if target is None:
            return
        # THE CLUTCH: THE CURSOR STAYS WHERE IT IS.
        #
        # The open palm is the brake: the hand can be moved, lowered, or
        # rested on the table without the cursor going anywhere, which is
        # what stops the arm from getting tired.
        if self._clutched:
            return
        now = time.monotonic()
        # THE MOUSE OF THE USER, IF HE MOVED IT.
        if self._mouse_until > now:
            return
        real = self._driver.cursor_pos()
        if real is not None and self._pointer_at is not None:
            if math.hypot(real[0] - self._pointer_at[0],
                          real[1] - self._pointer_at[1]) > 3.0:
                # Somebody else moved the cursor. The hand lets go.
                self._mouse_until = now + 4.0
                self._mouse_at = real
                self._pointer_at = None
                return
        current = self._pointer_at
        if current is None:
            # THE HAND TAKES OVER WHERE THE MOUSE IS, NOT WHERE IT LEFT IT.
            #
            # When the user moved the mouse, the hand let go and the
            # pointer was left where the mouse was. When the hand takes
            # over again it walks from there, so the cursor does not jump
            # across the screen in the middle of a click.
            current = self._mouse_at
            if current is None:
                self._pointer_at = target
                self._driver.move(target[0], target[1])
                return
            self._pointer_at = current
        if current == target:
            return
        # THE STEP IS PROPORTIONAL TO THE TIME, so the speed of the
        # pointer does not depend on how often the timer happened to fire.
        dt = 0.016
        if self._servo_last:
            dt = max(0.005, min(0.1, now - self._servo_last))
        self._servo_last = now
        alpha = 1.0 - math.exp(-dt / 0.030)
        nx = int(round(current[0] + (target[0] - current[0]) * alpha))
        ny = int(round(current[1] + (target[1] - current[1]) * alpha))
        if (nx, ny) == current:
            return
        self._pointer_at = (nx, ny)
        self._driver.move(nx, ny)

    def _snap_pointer(self) -> None:
        """Put the pointer exactly where the hand is aiming, right now.

        The servo walks the pointer towards the aimed point, and a click
        has to land on the aimed point and not on the place the pointer
        was passing through.
        """
        target = self._target
        if target is None:
            return
        if self._pointer_at != target:
            self._pointer_at = target
            self._driver.move(target[0], target[1])

    def _zoom_pair(self, hands: list[Hand]) -> None:
        """Two hands pinching: the zoom, by how far apart they are."""
        if len(hands) < 2:
            return
        try:
            a = hands[0].points[THUMB_TIP]
            b = hands[1].points[THUMB_TIP]
        except Exception:
            return
        distance = math.hypot((a[0] - b[0]) * self._screen_w,
                              (a[1] - b[1]) * self._screen_h)
        now = time.time()
        if self._zoom_pair_anchor is None:
            self._zoom_pair_anchor = (distance, now)
            self._set_gesture("two", "zoom")
            return
        last_distance, t0 = self._zoom_pair_anchor
        if now - t0 < 0.08:
            return
        self._zoom_pair_anchor = (distance, now)
        delta = distance - last_distance
        if abs(delta) < 8:
            return
        self._driver.zoom(1 if delta > 0 else -1)
        self._set_gesture("two", "zoom in" if delta > 0 else "zoom out")

    # ── the press ───────────────────────────────────────────────────────────

    def _modifiers_of(self, hand: Hand) -> set[int]:
        """Which modifier keys this pinch is holding.

        THE FINGERS THAT ARE NOT PINCHING ARE THE MODIFIERS, exactly as
        on a real mouse: the hand that holds the button is the hand that
        works, and the fingers that are left over hold the keys.

            middle   the shift: a range of things
            ring     the control: many things at once
            pinky    the alt
        """
        fingers = getattr(hand, "fingers", None) or {}
        keys: set[int] = set()
        if fingers.get("middle"):
            keys.add(PointerDriver._VK_SHIFT)
        if fingers.get("ring"):
            keys.add(PointerDriver._VK_CONTROL)
        if fingers.get("pinky"):
            keys.add(PointerDriver._VK_MENU)
        return keys

    def _is_middle_click(self, hand: Hand) -> bool:
        """The middle button: the one under the wheel of a real mouse.

        ALL THREE OF THE FINGERS THAT ARE LEFT OVER, AT ONCE. It is not
        three modifiers: it is a button that no other gesture reaches,
        and a button that only exists when nobody else is holding a key.
        """
        fingers = getattr(hand, "fingers", None) or {}
        return bool(fingers.get("middle") and fingers.get("ring")
                    and fingers.get("pinky"))

    def _set_modifiers(self, keys: set[int]) -> None:
        """Hold down the modifier keys that are wanted, and let go of the rest."""
        keys = set(int(k) for k in keys)
        for vk in list(self._held_keys - keys):
            self._driver.key(vk, False)
        for vk in list(keys - self._held_keys):
            self._driver.key(vk, True)
        self._held_keys = keys

    def _begin_press(self, hand: Hand, x: int, y: int, now: float) -> None:
        """The button goes down."""
        # THE CLICK LANDS WHERE THE HAND IS AIMING.
        #
        # The servo walks the pointer towards the aimed point, and the
        # click has to fall on the aimed point and not on the place the
        # pointer was passing through.
        self._snap_pointer()
        # THE MIDDLE BUTTON IS A BUTTON OF ITS OWN.
        if self._is_middle_click(hand):
            self._set_modifiers(set())
            self._driver.middle_click()
            self._set_gesture("pinch", "middle click")
            return
        self._set_modifiers(self._modifiers_of(hand))
        self._press_at = (x, y)
        self._press_time = now
        self._press_moved = False
        self._press_is_right = False
        self._pointer_down = True
        self._driver.press()
        self._set_gesture("pinch", "press")

    def _during_press(self, hand: Hand, x: int, y: int, now: float) -> None:
        """The button is down: what is happening while it is down."""
        if not self._pointer_down:
            # THE MIDDLE BUTTON IS A COMPLETE GESTURE BY ITSELF: there is
            # no button to hold down and no key to hold with it, and the
            # three fingers that made it are not three modifiers.
            return
        # THE MODIFIERS CAN CHANGE WHILE THE BUTTON IS DOWN.
        self._set_modifiers(self._modifiers_of(hand))
        drift = math.hypot(x - self._press_at[0], y - self._press_at[1])
        if drift > self._STILL_PX:
            self._press_moved = True
        # A PRESS HELD STILL FOR LONG ENOUGH IS THE OTHER BUTTON.
        #
        # The left button comes up and the right button goes down, so
        # the thing is NOT selected and its menu is NOT opened: one
        # gesture, one action. Holding the left button down for three
        # quarters of a second and then letting it go was a click AND a
        # right click, which is two actions out of one gesture.
        if (not self._press_moved and not self._press_is_right
                and now - self._press_time >= self._LONG_PRESS):
            self._press_is_right = True
            self._driver.release()
            self._driver.press_right()
            self._set_gesture("pinch", "right click")
            return
        self._set_gesture("pinch", "drag" if self._press_moved else "press")

    def _end_press(self, now: float) -> None:
        """Let the button up, and say what the press was.

        A press that was held still for long enough is the OTHER button:
        the context menu, the way a long press opens it on a touch screen.
        A press that moved is a drag, and a drag is not a click.
        """
        if not self._pointer_down:
            self._set_modifiers(set())
            return
        # THE CLICK LANDS WHERE THE HAND WAS AIMING.
        self._snap_pointer()
        moved = self._press_moved
        was_right = self._press_is_right
        self._pointer_down = False
        self._press_is_right = False
        if was_right:
            # THE RIGHT BUTTON COMES UP: the menu opens now.
            self._driver.release_right()
            self._set_modifiers(set())
            self._set_gesture("pinch", "right click")
            return
        self._driver.release()
        if moved:
            self._set_modifiers(set())
            self._set_gesture("pinch", "drag")
            return
        # TWO PINCHES CLOSE TOGETHER ARE A DOUBLE CLICK.
        #
        # The debounce used to swallow the second one, so a double click
        # could not be done at all: the mouse of the world does it with
        # two quick presses and so does this one.
        if now - self._last_click_end < self._DOUBLE_CLICK:
            self._last_click_end = 0.0
            self._driver.double_click()
            self._set_modifiers(set())
            self._set_gesture("pinch", "double click")
            return
        self._last_click_end = now
        self._set_modifiers(set())
        self._set_gesture("pinch", "click")

    # ── the pieces of the vocabulary ─────────────────────────────────────────

    def _set_gesture(self, pose: str, action: str) -> None:
        """Say what the hand is doing, so the interface can show it."""
        try:
            self.gesture_sig.emit(str(pose), str(action))
        except Exception:
            pass

    def _release_pointer(self) -> None:
        """Let the button up, whatever the hand is doing now.

        A button left down is a button the user cannot get rid of: the
        hand changed its mind about the pinch and the mouse is still
        held.
        """
        if self._pointer_down:
            self._pointer_down = False
            self._press_is_right = False
            self._driver.release()

    def _rest(self) -> None:
        """No hand: everything is let go.

        AND THE FILTER FORGETS THE HAND. The next hand that appears
        starts from where it is, not from where the last one was: a
        pointer that jumps across the screen when the hand comes back
        is the most frightening thing a camera cursor can do.
        """
        self._release_pointer()
        self._set_modifiers(set())
        self._scroll_anchor = None
        self._swipe_anchor = None
        self._zoom_anchor = None
        self._zoom_pair_anchor = None
        self._scroll_acc = 0.0
        self._scroll_side_acc = 0.0
        self._pinch_latched = False
        self._smoother.reset()

    # HOW FAR THE HAND HAS TO GO, IN PIXELS, TO BE A WHEEL OR A ZOOM.
    #
    # Twenty-five pixels is a movement that a hand makes on purpose and
    # that a hand at rest never makes. It is measured from where the
    # gesture started, not from the last picture, so a slow scroll
    # accumulates and a trembling hand does not.
    _RUEDA_PX = 25

    def _rueda_o_zoom(self, hand: Hand, now: float) -> None:
        """The wheel or the zoom: the index and the middle, and what they do.

        THE WHEEL is the two fingers travelling with the hand.
        THE ZOOM is the two fingers getting further apart from each other.

        Whichever of the two the hand starts is the one it is doing, and
        it stays that one until the hand leaves the position: a scroll
        that turns into a zoom halfway is neither of them.
        """
        try:
            a = hand.points[INDEX_TIP]
            b = hand.points[12]
            mid_x = (hand.points[8][0] + hand.points[12][0]) / 2.0
            mid_y = (hand.points[8][1] + hand.points[12][1]) / 2.0
        except Exception:
            return
        distancia = math.hypot((a[0] - b[0]) * self._screen_w,
                               (a[1] - b[1]) * self._screen_h)
        # THE MOVEMENT IS MEASURED ON THE PALM, NOT ON THE FINGERTIPS.
        #
        # Separating the fingers moves the middle of the fingertips too,
        # so a zoom looked like a scroll: the hand had "moved" thirty
        # pixels when all that had happened is that the two fingers had
        # come apart. The palm does not move when the fingers spread,
        # and that is the difference between the wheel and the zoom.
        px, py = self._map_x(hand.aim_x), self._map_y(hand.aim_y)
        if self._rueda_anchor is None:
            self._rueda_anchor = (distancia, mid_x, mid_y, px, py, now)
            self._rueda_modo = ""
            return
        d0, mx0, my0, px0, py0, t0 = self._rueda_anchor
        if self._rueda_modo == "":
            if math.hypot(px - px0, py - py0) > self._RUEDA_PX:
                # LA MANO SE MUEVE: LA RUEDA.
                self._rueda_modo = "rueda"
                self._scroll_anchor = (mx0, my0, t0)
            elif abs(distancia - d0) > self._RUEDA_PX:
                # LOS DEDOS SE SEPARAN: EL ZOOM.
                self._rueda_modo = "zoom"
                self._zoom_anchor = (d0, t0)
            else:
                return
        self._rueda_anchor = (distancia, mid_x, mid_y, px, py, now)
        if self._rueda_modo == "rueda":
            self._scroll(hand, now)
            self._set_gesture("two", "scroll")
        else:
            self._zoom(hand)

    def _scroll(self, hand: Hand, now: float) -> None:
        """The wheel, under the two fingers.

        The middle of the two fingertips is the wheel's place, and the
        distance it travels since the last turn is the turn. Up and down
        scroll vertically; sideways scrolls sideways.
        """
        try:
            mid_x = (hand.points[8][0] + hand.points[12][0]) / 2.0
            mid_y = (hand.points[8][1] + hand.points[12][1]) / 2.0
        except Exception:
            return
        if self._scroll_anchor is None:
            self._scroll_anchor = (mid_x, mid_y, now)
            return
        ax, ay, t0 = self._scroll_anchor
        if now - t0 < 0.05:
            return
        self._scroll_anchor = (mid_x, mid_y, now)
        # The screen axis, not the camera one: the camera is mirrored, so
        # the hand moving to the right is the picture moving to the left.
        dx = -(mid_x - ax) * self._screen_w
        dy = (mid_y - ay) * self._screen_h
        if abs(dy) >= abs(dx):
            # The fingers up push the page up, the way a touchpad does.
            self._scroll_acc += -dy / self._SCROLL_PX
            notches = int(self._scroll_acc)
            if notches:
                self._scroll_acc -= notches
                self._driver.scroll(notches)
        else:
            self._scroll_side_acc += dx / self._SCROLL_PX
            notches = int(self._scroll_side_acc)
            if notches:
                self._scroll_side_acc -= notches
                self._driver.scroll_sideways(notches)

    def _swipe(self, hand: Hand, now: float) -> None:
        """An open palm swept sideways: back and forward."""
        # The knuckle, like the pointer: a sweep is measured from where
        # the hand IS, not from where the fingers meet.
        sx = float(getattr(hand, "aim_x", hand.x))
        sy = float(getattr(hand, "aim_y", hand.y))
        if self._swipe_anchor is None:
            self._swipe_anchor = (sx, sy, now)
            return
        ax, ay, t0 = self._swipe_anchor
        if now - t0 > self._SWIPE_SECONDS:
            # Too slow to be a swipe: the hand is starting again.
            self._swipe_anchor = (sx, sy, now)
            return
        dx = (ax - sx) * self._screen_w
        dy = (ay - sy) * self._screen_h
        if abs(dx) > self._SWIPE_PX and abs(dx) > abs(dy):
            self._swipe_anchor = None
            # The hand to the right is the picture to the left, and the
            # picture to the left means "back".
            if dx > 0:
                self._driver.tap(PointerDriver._VK_RIGHT, alt=True)
            else:
                self._driver.tap(PointerDriver._VK_LEFT, alt=True)

    def _zoom(self, hand: Hand) -> None:
        """Three fingers: the zoom, by how far apart the index and the middle are.

        IT USED TO BE TWO HANDS. Two hands pinching and moving apart or
        together is a lovely gesture and it is also unusable: it needs
        two hands, and the pointer was jumping from one of them to the
        other the whole time. Now it is one hand and the same idea: the
        index and the middle of one hand moving apart is the zoom, and
        the zoom is still the control and the wheel that every program
        means by zooming.
        """
        fingers = getattr(hand, "fingers", None) or {}
        if not (fingers.get("index") and fingers.get("middle")):
            return
        try:
            a = hand.points[INDEX_TIP]
            b = hand.points[12]
        except Exception:
            return
        distance = math.hypot((a[0] - b[0]) * self._screen_w,
                              (a[1] - b[1]) * self._screen_h)
        now = time.time()
        if self._zoom_anchor is None:
            self._zoom_anchor = (distance, now)
            self._set_gesture("three", "zoom")
            return
        last_distance, t0 = self._zoom_anchor
        if now - t0 < 0.08:
            return
        self._zoom_anchor = (distance, now)
        delta = distance - last_distance
        if abs(delta) < 8:
            return
        self._driver.zoom(1 if delta > 0 else -1)
        self._set_gesture("three", "zoom in" if delta > 0 else "zoom out")


# ── the overlay ────────────────────────────────────────────────────────────

class GestureOverlay(QWidget):
    """The hands, drawn on the glass.

    Two passes for the bones — a wide soft one for the glow and a tight
    bright one on top for the line itself. One pass with a big shadow reads
    as fog.
    """

    GLOW_WIDTH = 7
    LINE_WIDTH = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._hands: list[Hand] = []
        self._accent = C.PRI

    def set_hands(self, hands) -> None:
        self._hands = list(hands or [])
        self.update()

    def set_accent(self, colour: str) -> None:
        self._accent = colour
        self.update()

    @staticmethod
    def _hand_label(hand) -> str:
        """What the hand is, in the language of the interface.

        IT SAID "LEFT FIST" IN ENGLISH while the user was pinching: the
        hand had been read as a fist — the threshold of the pinch was the
        one of an open hand — and the label was in English on top of it.
        Now it says "IZQUIERDA · PELLIZCO", in the language of the
        interface, and the pose it names is the one the machine read.
        """
        from core.localization import gesture_label, t as _t

        side = str(getattr(hand, "handedness", "") or "").strip().lower()
        side_text = {
            "left": _t("hand.left"),
            "right": _t("hand.right"),
        }.get(side, side.upper())
        pose = gesture_label("pose", str(getattr(hand, "pose", "") or ""))
        return f"{side_text} · {str(pose).upper()}"

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        # The painter MUST be closed even if the drawing raises: an open
        # painter makes Qt complain "endPaint() called with active painter"
        # on every single frame and ruins the paint pipeline.
        try:
            self._paint(p)
        finally:
            p.end()

    def _paint(self, p: QPainter) -> None:
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        if W <= 2 or H <= 2:
            return
        accent = QColor(self._accent)
        for hand in self._hands:
            points = hand.points
            if not points or len(points) < 21:
                continue
            scale = max(0.6, min(2.2, hand.span * 900.0))
            lit = 1.0 if hand.pinched else 0.82 + hand.closeness * 0.18
            px = [(p_ := (1.0 - pt[0]) * W, pt[1] * H) for pt in points]

            p.setBrush(Qt.BrushStyle.NoBrush)
            # the glow
            pen = QPen(QColor(accent.red(), accent.green(), accent.blue(),
                              int(110 * lit)))
            pen.setWidthF(self.GLOW_WIDTH * scale)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            for a, b in BONES:
                p.drawLine(QPointF(*px[a]), QPointF(*px[b]))
            # the line
            pen = QPen(QColor(accent.red(), accent.green(), accent.blue(),
                              int(235 * lit)))
            pen.setWidthF(self.LINE_WIDTH * scale)
            p.setPen(pen)
            for a, b in BONES:
                p.drawLine(QPointF(*px[a]), QPointF(*px[b]))
            # the joints
            p.setPen(Qt.PenStyle.NoPen)
            for index, (x, y) in enumerate(px):
                if index == INDEX_TIP:
                    continue
                radius = (3.6 if index in TIPS else (4.0 if index == WRIST else 2.2)) * scale
                p.setBrush(QColor(accent.red(), accent.green(), accent.blue(),
                                  int(235 * lit)))
                p.drawEllipse(QRectF(x - radius, y - radius, radius * 2, radius * 2))

            # THE PINCH: the gap closing between the thumb and the index.
            # This is the single most useful thing on screen — it is the
            # press, visible before it happens.
            thumb = px[THUMB_TIP]
            index_tip = px[INDEX_TIP]
            pen = QPen(QColor(255, 255, 255, 255) if hand.pinched else accent)
            pen.setWidthF((0.8 + hand.closeness * 1.6) * scale)
            if not hand.pinched:
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawLine(QPointF(*thumb), QPointF(*index_tip))

            # THE CURSOR: at the aimed point, not at the raw fingertip.
            cx = (1.0 - hand.x) * W
            cy = hand.y * H
            ring = (17.0 - hand.closeness * 8.0) * scale
            pen = QPen(QColor(255, 255, 255, 255) if hand.pinched else accent)
            pen.setWidthF(1.5 * scale)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - ring, cy - ring, ring * 2, ring * 2))
            if hand.pinched:
                p.setBrush(QColor(255, 255, 255, 70))
                p.drawEllipse(QRectF(cx - ring, cy - ring, ring * 2, ring * 2))
            p.setBrush(QColor(255, 255, 255, 255) if hand.pinched else accent)
            p.drawEllipse(QRectF(cx - 3 * scale, cy - 3 * scale, 6 * scale, 6 * scale))

            # WHAT IT THINKS YOU ARE DOING: below the wrist, quiet. Worth
            # showing, because when a gesture is misread this is the only
            # way to see that it was read at all.
            if hand.handedness:
                p.setPen(QColor(accent.red(), accent.green(), accent.blue(), 180))
                p.setFont(px_font(9, mono=True))
                label = self._hand_label(hand)
                # PyQt6 has no drawText(QPointF, flags, text): the aligned
                # overload wants a rectangle. Centre the label under the
                # wrist in a box of its own.
                w, h = 160 * scale, 14 * scale
                x0 = px[WRIST][0] - w / 2.0
                y0 = px[WRIST][1] + 22 * scale
                p.drawText(QRectF(x0, y0, w, h),
                           Qt.AlignmentFlag.AlignHCenter
                           | Qt.AlignmentFlag.AlignTop, label)
