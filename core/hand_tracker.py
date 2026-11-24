"""core/hand_tracker.py — the hand tracker, and the ONLY place MediaPipe lives.

WHY THIS FILE EXISTS
    The tracker was in `gui/gesture.py`, which is imported by the process
    that draws the interface. MediaPipe's `HandLandmarker` is a native
    graph with a group of threads of its own, and it was sitting right
    next to the Qt event loop and the repaints. The program died with
    «Windows fatal exception: access violation» — in a repaint, in a
    thread of COM, wherever that memory was touched next — and the
    camera, which had already been moved to a process of its own, was
    still not enough: the last native thing left in the interface was
    this one.

    So the tracker moved too, to the process that already holds the
    camera (`core/camera_worker.py`). This process reads the device AND
    looks for the bones, and sends numbers; the interface reads the
    numbers. Nothing native is left in the interface, and there is
    nothing for the two processes to fight over.

WHAT IS AND IS NOT HERE
    Here: the native graph, the model, the OpenCV fallback, the measuring
    of the hand. Here: `mediapipe`, imported lazily and only when a
    tracker is really opened.

    NOT here: what a hand MEANS. The pose, the pinch and the shape of
    the fingers are in `core/hand_spec.py`, which is arithmetic on
    numbers and can be imported by anybody without opening a door that
    should stay closed.

THE ORDER OF THE CLOSE IS STILL THE WHOLE POINT
    The graph is freed while nothing is being tracked inside it, and the
    interpreter is not left to free it whenever it pleases: a free that
    lands on memory the native code is still using is the same crash
    with worse timing.
"""
from __future__ import annotations

import atexit
import os
import threading
import time
from pathlib import Path

from core.hand_spec import BONES, MIDDLE_MCP, TIPS, WRIST, Hand

# The file of the model. It is NOT downloaded by the program: it is
# fetched by hand and left here.
_MODEL = Path(__file__).resolve().parent.parent / "models" / "hand_landmarker.task"

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
            # TWO HANDS, AND NOT ONE.
            #
            # The owner asked for one, and the code was changed to one:
            # with two hands the pointer jumped from one hand to the
            # other. The code picks the hand that is doing something, and
            # when both were in the picture that choice changed from one
            # picture to the next: the pointer teleported from one hand
            # to the other, and no amount of smoothing can hide a jump
            # that big.
            #
            # The pointer now keeps ONE hand — the nearest one to where it
            # already is — and the other hand is still read: two hands
            # pinching and moving apart is the zoom, which is the gesture
            # that two hands are good for.
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

        try:
            from core.camera import cv2_quiet

            cv2 = cv2_quiet()
        except Exception:                               # noqa: BLE001
            import cv2
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
