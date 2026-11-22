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

WHAT IS NOT DONE — THE THINGS MARKED WITH A STAR
    * `*` The model file is fetched by hand; the assistant does not
      download it by itself yet.
    * `*` The gesture vocabulary is one gesture: the pinch. No two-finger
      scroll, no three-finger swipe — the skeleton carries the landmarks
      they would need, but nothing reads them yet.
    * `*` The cursor takes the pointer whenever a hand is visible. It
      should only take it while the assistant is listening, and that rule
      is not written.
    * `*` The skeleton is drawn on the HUD, not on every widget.
"""
from __future__ import annotations

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

    __slots__ = ("points", "handedness", "pinched", "closeness", "x", "y", "span")

    def __init__(self, points, handedness: str = ""):
        self.points = points            # 21 x (x, y) in normalised image space
        self.handedness = handedness
        self.pinched = False
        self.closeness = 0.0
        self.x = 0.0
        self.y = 0.0
        self.span = 1.0

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

    def track(self, frame_bgr) -> list[Hand]:
        """The hands in a BGR frame."""
        if frame_bgr is None:
            return []
        if self._landmarker is not None:
            return self._track_tasks(frame_bgr)
        if self._legacy is not None:
            return self._track_legacy(frame_bgr)
        return self._track_opencv(frame_bgr)

    # -- MediaPipe, the new API ---------------------------------------------

    def _track_tasks(self, frame_bgr) -> list[Hand]:
        import mediapipe as mp
        image = mp.Image(image_format=mp.ImageFormat.SRGB,
                         data=frame_bgr[:, :, ::-1].copy())
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

    def _track_legacy(self, frame_bgr) -> list[Hand]:
        rgb = frame_bgr[:, :, ::-1].copy()
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

    def _track_opencv(self, frame_bgr) -> list[Hand]:
        """Skin, hull, fingertips. Crude, but it needs nothing installed.

        The fingertips are the vertices of the convex hull with the largest
        distance from the centre; the pinch is the gap between the two
        smallest of them.
        """
        import cv2
        import numpy as np
        h, w = frame_bgr.shape[:2]
        ycrcb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2YCrCb)
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
        for slot, (px, py) in enumerate(zip(tips, TIPS)):
            hand.points[slot] = (float(px) / w, float(py) / h)
        hand.span = max(1e-4, float(np.linalg.norm(tips[0] - centre)) / w)
        hand.measure()
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
    """

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


# ── the service ────────────────────────────────────────────────────────────

class GestureService(QObject):
    """Watches the camera and reports the hands.

    The camera and the tracking run on their own thread, because both are
    slow; only the result crosses into the Qt thread, through a signal.
    """

    hands_sig = pyqtSignal(object)          # list[Hand]
    state_sig = pyqtSignal(str)             # a sentence for the log

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
            tracker.close()
        return "the camera is no longer being watched"

    def toggle(self) -> str:
        return self.stop() if self._enabled else self.start()

    # -- the loop ------------------------------------------------------------

    def _loop(self) -> None:
        try:
            import cv2
        except Exception as exc:                       # noqa: BLE001
            self.state_sig.emit(f"the camera needs OpenCV: {type(exc).__name__}")
            return
        tracker = HandTracker()
        with self._lock:
            self._tracker = tracker
        cam = None
        try:
            if not tracker.available():
                self.state_sig.emit(f"no hand tracker: {tracker.error}")
                return
            self.state_sig.emit(f"the hands are tracked with {tracker.backend}")

            cam = cv2.VideoCapture(0)
            if not cam.isOpened():
                self.state_sig.emit("the camera could not be opened")
                return
            self._enabled = True
            last_frame = 0
            while not self._stop.is_set():
                ok, frame = cam.read()
                if not ok:
                    time.sleep(0.05)
                    continue
                now = time.time()
                if now - last_frame < 0.03:            # 30 fps is plenty
                    continue
                last_frame = now
                hands = tracker.track(frame)
                with self._lock:
                    self._hands = hands
                self.hands_sig.emit(hands)
                if self._drive_pointer and hands:
                    self._steer(hands)
        except Exception as exc:                       # noqa: BLE001
            self.state_sig.emit(f"the hand tracking stopped: {type(exc).__name__}")
        finally:
            # THE TRACKER IS ALWAYS CLOSED.
            #
            # It used to be closed only at the end of a successful watch,
            # so a machine with no camera — or with no hand model, or with
            # no OpenCV — kept the native library loaded and let the
            # garbage collector free it whenever it pleased. That free
            # landed on memory the library was still using, and the
            # process died with an access violation instead of a goodbye.
            if cam is not None:
                try:
                    cam.release()
                except Exception:
                    pass
            tracker.close()
            with self._lock:
                # The service must forget this tracker: it is closed, and
                # whoever asks for it next must not find a dead one.
                if self._tracker is tracker:
                    self._tracker = None
            self._enabled = False
            # A button left down is a button the user cannot get rid of:
            # if the hand disappeared while it was pinched, the button has
            # to come up here.
            if self._pointer_down:
                self._pointer_down = False
                self._driver.release()
            with self._lock:
                self._hands = []
            self.hands_sig.emit([])

    def _steer(self, hands: list[Hand]) -> None:
        """Move the pointer to the aimed point, and pinch to press it.

        The pinch is not a click that fires and forgets: the fingers
        closing put the button DOWN, and the fingers opening let it UP.
        A quick pinch is therefore a click, and a pinch that moves while
        it is closed is a drag — which is what lets the hand pick up the
        floating panel and put it wherever it belongs.
        """
        if not self._driver.available():
            return
        hand = max(hands, key=lambda h: h.closeness)
        try:
            import ctypes
            screen_w = ctypes.windll.user32.GetSystemMetrics(0)
            screen_h = ctypes.windll.user32.GetSystemMetrics(1)
        except Exception:
            return
        # The camera is mirrored for the user, so the X is mirrored too.
        x = int((1.0 - hand.x) * screen_w)
        y = int(hand.y * screen_h)
        self._driver.move(x, y)
        now = time.time()
        if hand.pinched:
            if not self._pointer_down and now - self._last_click > 0.25:
                self._pointer_down = True
                self._last_click = now
                self._driver.press()
        elif self._pointer_down:
            # The fingers opened: the button comes up, and the click that
            # was inside the pinch is now a click.
            self._pointer_down = False
            self._driver.release()


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
                label = f"{hand.handedness.upper()} · {'PINCH' if hand.pinched else 'OPEN'}"
                p.drawText(QPointF(px[WRIST][0], px[WRIST][1] + 22 * scale),
                           Qt.AlignmentFlag.AlignHCenter, label)
        p.end()
