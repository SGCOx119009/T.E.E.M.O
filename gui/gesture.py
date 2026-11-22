"""
gui/gesture.py — the hands are the mouse.

WHAT THIS IS
    The cursor you steer with. A camera watches the user's hands; the
    skeleton of each hand is drawn on the glass; the fingertip is the
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

THE VOCABULARY — WHAT THE HAND CAN SAY
    A hand is not a pinch. The skeleton carries twenty-one landmarks, and
    they are enough to tell a dozen things apart — so the hand is a full
    mouse, not a single button:

    * `PINCH`      a click. Closed and opened quickly is a left click; a
                   pinch that MOVES while it is closed is a drag, and that
                   is what lets the hand pick up the floating panel and
                   put it wherever it belongs.
    * `PINCH` held still for three quarters of a second is the RIGHT
                   click — the context menu, the same way a long press is
                   the context menu on a touch screen.
    * `TWO`        index and middle up, the other fingers down: the wheel.
                   Up and down scrolls, sideways scrolls sideways.
    * `TWO HANDS`  both hands pinching and moving apart or together is the
                   ZOOM: the same Ctrl+wheel every drawing program uses.
    * `FIST`       one hand closed while the other works is the SHIFT key:
                   that is how a group of things is selected instead of
                   one thing at a time.
    * `OPEN`       an open palm swept sideways is a swipe: back and
                   forward, the way a browser understands them.
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

from PyQt6.QtCore import QObject, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QWidget

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


class Hand:
    """One hand, as the tracker sees it."""

    __slots__ = ("points", "handedness", "pinched", "closeness", "x", "y",
                 "span", "pose", "aim_x", "aim_y")

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
        self.pinched = self.closeness > 0.72
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
        if self.pinched:
            return "pinch"
        index = self._extended(6, 8)
        middle = self._extended(10, 12)
        ring = self._extended(14, 16)
        pinky = self._extended(18, 20)
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
    _WHEEL, _HWHEEL = 0x0800, 0x1000

    # The virtual keys of the Windows keyboard.
    _VK_SHIFT = 0x10
    _VK_CONTROL = 0x11
    _VK_MENU = 0x12
    _VK_LEFT, _VK_RIGHT = 0x25, 0x27

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
    _SCROLL_PX = 18
    _SWIPE_SECONDS = 0.45
    _SWIPE_PX = 220
    _CLICK_DEBOUNCE = 0.25
    # HOW MUCH OF THE PICTURE IS LEFT UNUSED AT THE EDGES.
    #
    # The camera sees more than the hand needs, and the corners of the
    # picture cannot be reached without the arm leaving the frame. So
    # the middle of the picture is the middle of the screen.
    _AIM_MARGIN = 0.15

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
        self._last_click = 0.0
        self._pointer_down = False
        # The state of the hand as a pointer.
        self._press_at = (0, 0)
        self._press_time = 0.0
        self._press_moved = False
        self._shift_down = False
        self._scroll_anchor = None
        self._swipe_anchor = None
        self._zoom_last = None
        self._screen_w = 1920
        self._screen_h = 1080
        # The tracker is used by the thread of the camera and closed by
        # the thread of the service: the two must not meet.
        self._frame_lock = threading.Lock()
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

    def start(self) -> str:
        """Start watching. Returns a sentence saying what happened."""
        if self._thread is not None:
            return "the camera is already being watched"
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="gesture")
        self._thread.start()
        return "watching the camera for hands"

    def stop(self) -> str:
        """Stop watching — and WAIT until the thread is really gone.

        The thread holds a tracker, and the tracker holds native memory
        that the tracking library frees when the Python object dies. If
        the window goes away while the thread is still inside
        `detect_for_video`, that free lands on memory the native code is
        still using, and the process dies with an access violation instead
        of a goodbye.

        So the order is fixed and it is not negotiable: the thread is
        asked to stop, the thread is WAITED for, and only then is the
        tracker released.
        """
        self._stop.set()
        thread = self._thread
        if thread is not None:
            if thread.is_alive():
                thread.join(timeout=5.0)
            self._thread = None
        with self._lock:
            tracker = self._tracker
            self._tracker = None
        if tracker is not None:
            # Under the frame lock: a frame that is being tracked right now
            # must be finished before the native memory is freed.
            with self._frame_lock:
                tracker.close()
        # The button and the modifier are let go here as well: a stop that
        # leaves the shift held down is a stop that turns every later click
        # into a group selection.
        if self._pointer_down:
            self._pointer_down = False
            self._driver.release()
        if self._shift_down:
            self._shift_down = False
            self._driver.key(PointerDriver._VK_SHIFT, False)
        return "the camera is no longer being watched"

    def toggle(self) -> str:
        return self.stop() if self._enabled else self.start()

    # -- the loop ------------------------------------------------------------

    def _loop(self) -> None:
        """Wait for the camera, and track the hands in every frame it gives.

        THE CAMERA IS NOT OPENED HERE. It is the one of the whole process
        (core/camera.py), and this service is one of its readers. Two
        readers that opened the device each were two handles to the same
        camera, and the driver did not survive it: the whole program died
        with an access violation.

        THE TRACKER IS NOT CREATED HERE EITHER. It is created in the
        thread that receives the frames, which is the thread of the
        camera: the native tracker and the frames belong to the same
        thread, and a tracker built in one thread and used in another is
        not a thing the native library promises anything about.
        """
        tracker = None
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
            # THE TRACKER IS ALWAYS CLOSED, AND NEVER UNDER A FRAME.
            #
            # It used to be closed only at the end of a successful watch,
            # so a machine with no camera — or with no hand model, or with
            # no OpenCV — kept the native library loaded and let the
            # garbage collector free it whenever it pleased. That free
            # landed on memory the library was still using, and the
            # process died with an access violation instead of a goodbye.
            #
            # AND THE CLOSE WAITS FOR THE FRAME THAT IS BEING TRACKED. The
            # camera thread may be inside the tracker at this very moment,
            # and a close that lands in the middle of that frees the
            # native memory under the library's feet. The lock of the
            # frame is the same one the camera thread takes.
            # The camera is NOT closed here: it is the one of the whole
            # process (core/camera.py), and this service is only one of
            # its readers. Closing it here would take the camera away
            # from the hands of the viewer and of the live stream.
            with self._frame_lock:
                tracker = self._tracker
                with self._lock:
                    self._tracker = None
                if tracker is not None:
                    tracker.close()
            self._enabled = False
            # A button left down is a button the user cannot get rid of:
            # if the hand disappeared while it was pinched, the button has
            # to come up here.
            if self._pointer_down:
                self._pointer_down = False
                self._driver.release()
            # And so does a modifier that is still held: a shift left down
            # turns every click afterwards into a group selection.
            if self._shift_down:
                self._shift_down = False
                self._driver.key(PointerDriver._VK_SHIFT, False)
            with self._lock:
                self._hands = []
            self.hands_sig.emit([])

    def _on_frame(self, ok: bool, jpeg: bytes) -> None:
        """One frame of the camera, and the hands in it.

        It runs on the thread of the camera, and the tracker is CREATED
        here as well: the native tracker belongs to the thread that asks
        it for frames, and the camera and the tracker are exactly the two
        pieces that, together, could take the program down.

        The tracker is behind a lock, because the thread of the service
        closes it when the watch stops, and a close that lands while a
        frame is being tracked frees the native memory under the
        library's feet.

        THE PICTURE IS DECODED HERE, AND ONLY HERE. What arrives is a
        JPEG; what the tracker needs are pixels. The two that only show
        the picture draw the JPEG as it is, and this is the one reader
        that has to look inside it.
        """
        if not ok or not jpeg:
            return
        with self._frame_lock:
            tracker = self._tracker
            if tracker is None:
                try:
                    tracker = HandTracker()
                except Exception:                       # noqa: BLE001
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
                self.state_sig.emit(f"the hands are tracked with {tracker.backend}")
            from core.camera import decode_jpeg

            frame = decode_jpeg(jpeg)
            if frame is None:
                return
            try:
                hands = tracker.track(frame)
            except Exception:
                return
        with self._lock:
            self._hands = hands
        self.hands_sig.emit(hands)
        if self._drive_pointer and hands:
            self._steer(hands)

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

    def _steer(self, hands: list[Hand]) -> None:
        """Move the pointer to the aimed point, and do what the hand says.

        The pinch is not a click that fires and forgets: the fingers
        closing put the button DOWN, and the fingers opening let it UP.
        A quick pinch is therefore a click, and a pinch that moves while it
        is closed is a drag — which is what lets the hand pick up the
        floating panel and put it wherever it belongs.

        And the pinch is not the only thing the hand can say. The whole
        vocabulary is decided here, from the shape of the hand:

            PINCH          left click, or a drag while it is closed
            PINCH, held    the right click
            TWO FINGERS    the wheel
            TWO HANDS      the zoom
            FIST           the shift: the group selection
            OPEN PALM      a swipe: back and forward
        """
        if not self._driver.available():
            return
        try:
            import ctypes
            self._screen_w = ctypes.windll.user32.GetSystemMetrics(0)
            self._screen_h = ctypes.windll.user32.GetSystemMetrics(1)
        except Exception:
            return
        if not hands:
            self._rest()
            return

        # THE FIST OF ONE HAND IS THE MODIFIER OF THE OTHER.
        #
        # A closed hand while the other hand works is the shift key: that
        # is how a group of things is selected instead of one thing at a
        # time. It has to be read across hands, because the hand that is
        # closed is the hand that is NOT clicking.
        #
        # THE POSE IS READ WITH A DEFENSE. A hand that comes from the
        # tracker has one; a hand that comes from anywhere else may not,
        # and a hand without a pose is a hand that is not a fist.
        poses = [str(getattr(h, "pose", "") or "") for h in hands]
        self._hold_shift("fist" in poses)

        # TWO HANDS BOTH PINCHING IS THE ZOOM.
        pinched = [h for h in hands if getattr(h, "pinched", False)]
        if len(pinched) >= 2:
            self._zoom(pinched)
            return
        self._zoom_last = None

        # THE HAND THAT IS WORKING.
        #
        # The pinched one when there is one — that is the hand that means
        # something — and otherwise the nearest, which is the biggest one
        # on the picture.
        hand = pinched[0] if pinched else max(
            hands, key=lambda h: float(getattr(h, "span", 0.0) or 0.0))
        pose = str(getattr(hand, "pose", "") or "")

        # THE CAMERA IS MIRRORED FOR THE USER, SO THE X IS MIRRORED TOO.
        #
        # AND THE POINTER IS AIMED AT THE KNUCKLE, NOT AT THE PINCH.
        #
        # The pinch is where the fingers meet, and the fingers MOVE when
        # they meet: aiming at the pinch makes the pointer jump at the
        # exact moment of the click, and the click lands a little away
        # from where the user was looking. The knuckle of the middle
        # finger does not move when the hand closes, so the pointer stays
        # where it was put and the pinch is only the button.
        #
        # The idea is from the gesture project the owner downloaded
        # (`Gesture-Recognition-main`), which aims with the same knuckle.
        x = self._map_x(getattr(hand, "aim_x", hand.x))
        y = self._map_y(getattr(hand, "aim_y", hand.y))
        self._driver.move(x, y)
        now = time.time()

        # AN OPEN PALM IS A SWEEP.
        if pose == "open":
            # Opening the fingers after a long pinch is the END of a long
            # press, so the press is judged here and not only on the way
            # out of the pinch: the hand that releases a long pinch is
            # usually an open one.
            self._end_press(now)
            self._swipe(hand, now)
            self._set_gesture("open", "swipe")
            return
        self._swipe_anchor = None

        # A CLOSED HAND IS THE SHIFT, AND NOTHING ELSE.
        if pose == "fist":
            self._release_pointer()
            self._set_gesture("fist", "group")
            return

        # TWO FINGERS ARE THE WHEEL.
        if pose == "two":
            self._release_pointer()
            self._scroll(hand, now)
            self._set_gesture("two", "scroll")
            return

        # ONE FINGER ONLY: MOVE, AND NOTHING ELSE.
        if pose == "point" and not getattr(hand, "pinched", False):
            self._release_pointer()
            self._set_gesture("point", "move")
            return

        # THE PINCH.
        if getattr(hand, "pinched", False):
            if not self._pointer_down:
                if now - self._last_click < self._CLICK_DEBOUNCE:
                    return
                self._pointer_down = True
                self._press_at = (x, y)
                self._press_time = now
                self._press_moved = False
                self._last_click = now
                self._driver.press()
            else:
                drift = math.hypot(x - self._press_at[0], y - self._press_at[1])
                if drift > self._STILL_PX:
                    self._press_moved = True
            self._set_gesture("pinch", "drag" if self._press_moved else "press")
            return

        # THE FINGERS OPENED.
        if self._pointer_down:
            self._end_press(now)
            return
        self._set_gesture(pose, "move")

    # ── the pieces of the vocabulary ─────────────────────────────────────────

    def _end_press(self, now: float) -> None:
        """Let the button up, and say what the press was.

        A press that was held still for long enough is the OTHER button:
        the context menu, the way a long press opens it on a touch screen.
        A press that moved is a drag, and a drag is not a click.
        """
        if not self._pointer_down:
            return
        held = now - self._press_time
        moved = self._press_moved
        self._pointer_down = False
        self._driver.release()
        if held >= self._LONG_PRESS and not moved:
            self._driver.right_click()
            self._set_gesture("pinch", "right click")
        else:
            self._set_gesture("pinch", "click")

    def _set_gesture(self, pose: str, action: str) -> None:
        """Say what the hand is doing, so the interface can show it."""
        try:
            self.gesture_sig.emit(str(pose), str(action))
        except Exception:
            pass

    def _hold_shift(self, on: bool) -> None:
        """The group-selection modifier."""
        on = bool(on)
        if on == self._shift_down:
            return
        self._shift_down = on
        self._driver.key(PointerDriver._VK_SHIFT, on)

    def _release_pointer(self) -> None:
        """Let the button up, whatever the hand is doing now.

        A button left down is a button the user cannot get rid of: the
        hand changed its mind about the pinch and the mouse is still
        held.
        """
        if self._pointer_down:
            self._pointer_down = False
            self._driver.release()

    def _rest(self) -> None:
        """No hand: everything is let go."""
        self._release_pointer()
        self._hold_shift(False)
        self._scroll_anchor = None
        self._swipe_anchor = None
        self._zoom_last = None

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
            self._driver.scroll(int(-dy / self._SCROLL_PX))
        else:
            self._driver.scroll_sideways(int(dx / self._SCROLL_PX))

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

    def _zoom(self, pinched: list[Hand]) -> None:
        """Two hands pinching, moving apart or together: the zoom."""
        points = []
        for hand in pinched[:2]:
            points.append(((1.0 - hand.x) * self._screen_w,
                           hand.y * self._screen_h))
        if len(points) < 2:
            return
        distance = math.hypot(points[0][0] - points[1][0],
                              points[0][1] - points[1][1])
        now = time.time()
        if self._zoom_last is None:
            self._zoom_last = (distance, now)
            self._set_gesture("zoom", "zoom")
            return
        last_distance, t0 = self._zoom_last
        if now - t0 < 0.08:
            return
        self._zoom_last = (distance, now)
        delta = distance - last_distance
        if abs(delta) < 4:
            return
        self._driver.zoom(1 if delta > 0 else -1)
        self._set_gesture("zoom", "zoom in" if delta > 0 else "zoom out")


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

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        if W <= 2 or H <= 2:
            p.end()
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
                label = f"{hand.handedness.upper()} · {hand.pose.upper()}"
                p.drawText(QPointF(px[WRIST][0], px[WRIST][1] + 22 * scale),
                           Qt.AlignmentFlag.AlignHCenter, label)
        p.end()
