"""
tests/test_camera_hub.py — ONE camera for the whole process.

WHY
    The suite died once with «Windows fatal exception: access violation»,
    with three threads in it: the hands, the tile of the viewer and the
    live stream. All three had opened the camera for itself.

    Two handles to the same DirectShow device is not something the driver
    survives. So the camera is opened once, in `core/camera.py`, and this
    is the proof that the readers share it.

    And then it died AGAIN, the same way, with the same message — and the
    reason was the opposite of the first one. The device was NOT open
    twice at the same time: it was open at the same time as the native
    hand tracker, and the two of them together is what the driver and the
    library could not survive. The crash came a few seconds later, in a
    repaint, in a thread that had nothing to do with either of them.

    So the camera moved to a process of its own (`core/camera_worker.py`)
    and the process that draws the interface no longer touches the
    device at all. These tests are the proof of that.

WHAT IS PROVED
    1. The camera is opened ONCE, whatever the number of readers.
    2. A reader that fails does not take the camera away from the others.
    3. The last reader out does NOT close the camera — and a reader that
       comes back does not open it again.
    4. `stop_all()` closes the camera, and waits for the process.
    5. The camera lives in another process: the interface never touches
       the device.
"""
from __future__ import annotations

import io
import sys
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from core.camera import CameraHub  # noqa: E402


def _fake_frames(count: int = 400) -> bytes:
    """A few pictures, in the way the worker sends them."""
    import cv2
    import numpy as np

    out = io.BytesIO()
    for index in range(count):
        picture = np.zeros((48, 64, 3), dtype=np.uint8)
        picture[:, :, index % 3] = index % 255
        good, buf = cv2.imencode(".jpg", picture)
        if not good:
            continue
        data = buf.tobytes()
        out.write(len(data).to_bytes(4, "little"))
        out.write(data)
    return out.getvalue()


class FakeStream:
    """The pipe of the worker, with the pictures and then a wait.

    A real pipe does not end when the pictures stop: it waits for more.
    A stream that ended would look like a camera that died, and the hub
    would open another one — which is exactly what these tests are
    checking that it does NOT do.
    """

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0
        self._lock = threading.Condition()
        self._closed = False

    def read(self, n: int = -1) -> bytes:
        with self._lock:
            while self._pos >= len(self._data) and not self._closed:
                self._lock.wait(0.05)
            if self._pos >= len(self._data):
                return b""
            size = n if n and n > 0 else len(self._data) - self._pos
            chunk = self._data[self._pos:self._pos + size]
            self._pos += len(chunk)
            if self._pos >= len(self._data):
                # The pictures start again: a real camera does not stop
                # sending them because nobody is looking.
                self._pos = 0
                time.sleep(0.005)
            return chunk

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._lock.notify_all()


class FakeWorker:
    """A camera that is not a camera, dressed as the process that is."""

    def __init__(self, frames: bytes):
        self.stdout = FakeStream(frames)
        self.stdin = io.BytesIO()
        self.terminated = False
        self.killed = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True
        self.stdout.close()

    def kill(self):
        self.killed = True
        self.stdout.close()

    def wait(self, timeout=None):
        return 0


def _hub_with_fake(monkeypatch, frames: bytes = b"") -> CameraHub:
    hub = CameraHub()
    workers = []

    def _spawn():
        worker = FakeWorker(frames if frames else _fake_frames())
        workers.append(worker)
        return worker

    monkeypatch.setattr(hub, "_spawn", _spawn)
    hub._workers = workers
    return hub


def _wait(predicate, timeout=3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_two_readers_share_one_camera(monkeypatch):
    hub = _hub_with_fake(monkeypatch)
    got_a, got_b = [], []
    reader_a = lambda ok, frame: got_a.append(frame)        # noqa: E731
    reader_b = lambda ok, frame: got_b.append(frame)        # noqa: E731

    hub.subscribe(reader_a)
    hub.subscribe(reader_b)

    assert _wait(lambda: got_a and got_b), "the readers did not get the frame"
    assert len(hub._workers) == 1, "the camera was opened more than once"
    hub.stop_all()


def test_a_reader_that_fails_does_not_stop_the_others(monkeypatch):
    hub = _hub_with_fake(monkeypatch)
    good = []

    def broken(ok, frame):
        raise RuntimeError("this reader is broken")

    reader_good = lambda ok, frame: good.append(frame)     # noqa: E731

    hub.subscribe(broken)
    hub.subscribe(reader_good)
    assert _wait(lambda: good), "the broken reader took the camera down"
    hub.stop_all()


def test_the_last_reader_out_does_not_close_the_camera(monkeypatch):
    """THE RULE THAT KILLS THE DRIVER.

    Letting the device go while the program is running tears the graph
    of DirectShow down under the feet of the driver, and the process
    dies with «Windows fatal exception: access violation» a few seconds
    later, in a thread that has nothing to do with the camera. It was
    measured: the same suite, with a fake camera, with the camera kept
    open and with the camera let go. Only the third one died.

    So the camera stays. The reader goes; the camera stays.
    """
    hub = _hub_with_fake(monkeypatch)
    got = []

    def reader(ok, frame):
        got.append(frame)

    hub.subscribe(reader)
    assert _wait(lambda: got), "the camera was never opened"
    worker = hub._workers[0]
    hub.unsubscribe(reader)

    # The reader is gone. The camera is NOT.
    time.sleep(0.4)
    assert not worker.terminated, "the camera was closed the moment nobody asked"
    assert hub.running, "the process that holds the camera went away"
    hub.stop_all()


def test_a_reader_that_comes_back_does_not_open_the_camera_again(monkeypatch):
    """THE REGRESSION OF THE CRASH.

    Open, close, open, close — the camera was opened four times in thirty
    seconds and the process died. The camera that is already open is the
    camera that is used again.
    """
    hub = _hub_with_fake(monkeypatch)

    for _round in range(4):
        got = []

        def reader(ok, frame):
            got.append(frame)

        hub.subscribe(reader)
        assert _wait(lambda: got), "the reader did not get the frame"
        hub.unsubscribe(reader)
        time.sleep(0.2)

    assert len(hub._workers) == 1, (
        f"the camera was opened {len(hub._workers)} times: the device is being "
        f"opened and closed over and over"
    )
    hub.stop_all()


def test_stop_all_closes_the_camera_and_waits_for_the_process(monkeypatch):
    """The program is going away: the device is released, properly."""
    hub = _hub_with_fake(monkeypatch)

    def reader(ok, frame):
        pass

    hub.subscribe(reader)
    assert _wait(lambda: hub._workers), "the camera was never opened"
    worker = hub._workers[0]

    hub.stop_all()

    assert worker.terminated, "the camera was left open"
    assert not hub.running, "the process that holds the camera is still alive"
    assert hub.readers == 0, "somebody is still reading frames of a closed camera"


def test_no_readers_means_no_camera(monkeypatch):
    hub = _hub_with_fake(monkeypatch)
    assert hub.readers == 0
    assert not hub.running


def test_the_index_is_readable(monkeypatch):
    hub = _hub_with_fake(monkeypatch)
    hub.set_index(2)
    assert hub.index == 2
    hub.set_index(-5)
    assert hub.index == 0, "a negative camera index is not a camera"


def test_release_readers_keeps_the_camera_open(monkeypatch):
    """What a test does at the end of a case: nobody reads, the camera stays."""
    hub = _hub_with_fake(monkeypatch)

    def reader(ok, frame):
        pass

    hub.subscribe(reader)
    assert _wait(lambda: hub._workers), "the camera was never opened"
    worker = hub._workers[0]

    hub.release_readers()
    time.sleep(0.3)
    assert hub.readers == 0, "the readers were not let go"
    assert not worker.terminated, "the camera was closed when it did not have to be"
    hub.stop_all()


def test_only_one_process_reads_the_camera(monkeypatch):
    """There is one process, and a reader that comes and goes does not make
    a second one."""
    hub = _hub_with_fake(monkeypatch)
    threads = []

    def reader(ok, frame):
        threads.append(threading.current_thread().name)

    hub.subscribe(reader)
    assert _wait(lambda: threads), "the reader did not get the frame"
    hub.unsubscribe(reader)
    hub.subscribe(reader)
    time.sleep(0.3)
    hub.stop_all()

    assert len(set(threads)) == 1, (
        f"more than one thread read the camera: {sorted(set(threads))}"
    )


def test_the_camera_is_in_another_process():
    """THE INTERFACE NEVER TOUCHES THE DEVICE.

    The camera is opened in `core/camera_worker.py`, in a process of its
    own, and the process that draws the interface only receives
    pictures. That is what keeps the driver and the hand tracker from
    meeting in the same process.
    """
    source = (BASE / "core" / "camera.py").read_text(encoding="utf-8")
    assert "camera_worker" in source, "the camera is not in a process of its own"

    worker = BASE / "core" / "camera_worker.py"
    assert worker.exists(), "the process that holds the camera does not exist"
    text = worker.read_text(encoding="utf-8")
    assert "VideoCapture" in text, "the worker does not open the camera"
    assert "_watch_the_parent" in text, (
        "the worker would outlive the program and leave the camera claimed"
    )


def test_the_worker_is_told_which_camera_and_which_parent(monkeypatch):
    """THE ARGUMENTS ARE NAMED, BECAUSE THE ORDER WAS WRONG.

    The worker read its two numbers by position, and the parent gave them
    in the other order. With the first camera it worked by luck; with any
    other one the worker opened the wrong camera and watched PID 1 — the
    idle process, which never dies — so it never left on its own.
    """
    from core.camera_worker import parse_args

    args = parse_args(["--index", "1", "--parent", "42"])
    assert args.index == 1, f"the camera is not the one that was asked for: {args}"
    assert args.parent == 42, f"the parent is not the one that started it: {args}"

    same = parse_args(["--index=1", "--parent=42"])
    assert (same.index, same.parent) == (1, 42), "the other form is not read"

    empty = parse_args([])
    assert (empty.index, empty.parent) == (0, 0), "no arguments means the first camera"

    # And what the parent really writes on the command line.
    import core.camera as camera_module

    captured = {}

    class _FakePopen:
        def __init__(self, args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

        def poll(self):
            return 0

    monkeypatch.setattr(camera_module.subprocess, "Popen", _FakePopen)
    hub = CameraHub()
    hub.set_index(3)
    hub._spawn()

    args = captured.get("args", [])
    assert "--index" in args, f"the camera is not named on the command line: {args}"
    assert "3" in args, f"the camera that was asked for is not in {args}"
    assert "--parent" in args, f"the parent is not named on the command line: {args}"
    assert str(camera_module.os.getpid()) in args, (
        f"the worker is not told who started it: {args}"
    )


def test_changing_the_camera_reopens_it_for_the_readers(monkeypatch):
    """Changing the camera must not switch it off.

    The worker was killed and the camera left closed, so the pictures
    simply stopped arriving, with nothing to say why.
    """
    hub = _hub_with_fake(monkeypatch)
    got = []

    def reader(ok, frame):
        got.append(frame)

    hub.subscribe(reader)
    assert _wait(lambda: got), "the camera was never opened"
    first = len(hub._workers)

    hub.set_index(1)

    assert len(hub._workers) == first + 1, (
        "the camera was left switched off after changing it"
    )
    assert hub.running, "the new camera is not running"
    hub.stop_all()


def test_the_probe_does_not_open_the_camera_in_this_process():
    """THE LAST DOOR LEFT OPEN TO THE CRASH.

    Finding out which camera is the good one means opening the cameras
    and looking at a frame. That was done in the process that draws the
    interface, with the hand tracker loaded — and a device that is busy
    does not fail, it WAITS: `VideoCapture` on a busy DirectShow device
    blocks, and the program stands there waiting for a camera that will
    never answer.
    """
    import core.camera as camera_module

    assert hasattr(camera_module, "probe_index"), (
        "the camera has no way of asking which one is the good one"
    )

    # The product opens the device in ONE place: the worker.
    offenders = []
    for path in BASE.rglob("*.py"):
        if "tests" in path.parts or ".git" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        if "VideoCapture(" in text and path.name != "camera_worker.py":
            offenders.append(str(path.relative_to(BASE)))
    assert not offenders, (
        f"the device is opened outside the worker: {offenders}"
    )
