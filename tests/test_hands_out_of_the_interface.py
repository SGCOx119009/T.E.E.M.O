"""The interface process does not hold MediaPipe.

THE PROOF, IN THREE PIECES
    1. The process that draws the interface never imports the library.
       Not even lazily, not even by accident: the module that owns the
       tracker is not imported here at all.
    2. The geometry of the hand — the pinch, the pose, the shape of the
       fingers — is in a module of pure arithmetic, and it can be imported
       by anybody without opening a native door.
    3. What crosses the line between the processes is DATA: the numbers
       of a hand, not a hand and not a native object.

WHY IT MATTERS
    MediaPipe's tracker is a native graph with threads of its own. It
    used to sit in the process that draws the interface, next to the Qt
    event loop and the repaints, and the program died with «Windows
    fatal exception: access violation» — in a repaint, in a thread of
    COM, wherever that memory was touched next. The camera had already
    been moved to a process of its own, and that was not enough: this was
    the last native thing left in the interface.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))


def test_the_interface_never_imports_mediapipe():
    """THE RULE, AS A FACT AND NOT AS A PROMISE.

    The check is done in a process of its own, because this one may have
    imported the library for some other reason (a test of the tracker,
    say), and the fact that has to be proved is about the interface."""
    program = (
        "import sys\n"
        "sys.path.insert(0, r'%s')\n"
        "import gui.gesture\n"          # the module that draws the hands
        "import core.camera\n"          # the hub of the camera
        "import core.hand_spec\n"       # the geometry of the hand
        "loaded = [m for m in sys.modules if m.split('.')[0] == 'mediapipe']\n"
        "print('MEDIAPIPE:' + ','.join(loaded))\n" % BASE
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True, text=True, timeout=120,
        cwd=str(BASE),
    )
    assert result.returncode == 0, (
        f"the interface did not even start: {result.stderr[-400:]}"
    )
    loaded = ""
    for line in result.stdout.splitlines():
        if line.startswith("MEDIAPIPE:"):
            loaded = line.split(":", 1)[1].strip()
    assert loaded == "", (
        f"the process that draws the interface loaded MediaPipe: {loaded}"
    )


def test_the_tracker_is_not_imported_by_the_interface():
    """The module that owns the native graph is not in the interface."""
    source = (BASE / "gui" / "gesture.py").read_text(encoding="utf-8")
    for forbidden in ("import mediapipe", "from mediapipe",
                      "import core.hand_tracker", "from core.hand_tracker"):
        assert forbidden not in source, (
            f"the interface still imports the native one: {forbidden}"
        )

    tracker = (BASE / "core" / "hand_tracker.py").read_text(encoding="utf-8")
    assert "mediapipe" in tracker, (
        "the tracker does not use MediaPipe any more: where did it go?"
    )


def test_the_geometry_of_the_hand_is_pure():
    """`core/hand_spec.py` is arithmetic, and nothing else.

    It is imported by the interface AND by the process that measures the
    hand, so it must not carry a native library with it."""
    source = (BASE / "core" / "hand_spec.py").read_text(encoding="utf-8")
    for forbidden in ("mediapipe", "cv2", "PyQt6", "numpy"):
        assert forbidden not in source, (
            f"the geometry of the hand depends on {forbidden}"
        )


def test_a_hand_survives_the_trip_between_the_processes():
    """The numbers go out and come back without losing a thing."""
    from core.hand_spec import Hand, hands_from_payload, hands_to_payload

    points = [(0.5 + i * 0.01, 0.4 + i * 0.005) for i in range(21)]
    hand = Hand(points, "Left")
    hand.measure()

    payload = hands_to_payload([hand])
    # It has to be JSON: that is what travels.
    text = json.dumps(payload)
    back = hands_from_payload(json.loads(text))

    assert len(back) == 1
    other = back[0]
    assert other.handedness == hand.handedness
    assert other.pose == hand.pose
    assert abs(other.span - hand.span) < 1e-9
    assert abs(other.closeness - hand.closeness) < 1e-9
    assert abs(other.index_reach - hand.index_reach) < 1e-9
    assert other.pinched == hand.pinched
    assert other.fingers == hand.fingers
    assert len(other.points) == 21


def test_the_hands_arrive_through_their_own_door():
    """The hands have their own channel, and the picture has its own."""
    from core.camera import CameraHub

    hub = CameraHub()
    pictures, hands = [], []
    hub.subscribe(lambda ok, frame: pictures.append(frame))
    hub.subscribe_hands(hands.append)

    assert hub._hands_subscribers, "the hands have no readers"
    assert hands == [], "the hands arrived before there was a camera"

    hub._deliver_hands(json.dumps(
        {"backend": "mediapipe", "hands": []}).encode("utf-8"))
    assert len(hands) == 1, "the hands did not arrive"
    assert hands[0]["backend"] == "mediapipe"

    hub.stop_all()


def test_the_worker_measures_the_hands():
    """The tracker runs in the process that holds the camera."""
    worker = (BASE / "core" / "camera_worker.py").read_text(encoding="utf-8")
    assert "hand_tracker" in worker, (
        "the worker does not look for the hands"
    )
    assert "HandTracker" in worker, (
        "the worker does not open a tracker"
    )
    # And it sends them, with the picture.
    assert "hands_to_payload" in worker, (
        "the worker measures the hands and throws them away"
    )
