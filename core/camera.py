"""core/camera.py — ONE camera for the whole process, in a process of its own.

WHY THIS FILE EXISTS
    Three things wanted the camera at the same time: the hands (MediaPipe
    reads frames and finds the bones), the tile of the viewer, and the
    live stream that replaces the face in the HUD.

    Each of them opened the device for itself, and Windows does not like
    that. The camera driver was handed three handles to the same camera
    and the process died with an access violation — not a Python error,
    a hard crash of the whole program, with the message
    «Windows fatal exception: access violation».

    So the camera is opened HERE, once, and the frames are handed to
    whoever asks for them. One device, one handle, many readers.

THE RULE THAT WAS LEARNED THE HARD WAY
    The camera is not in the process that draws the interface.

    It was, and the program died. Not every time: most of the time,
    which is worse. The hand tracker opens a native graph with a group
    of threads of its own, the camera opens a device with a group of
    its own, and the two of them in the same process is what the driver
    and the library between them could not survive. The crash always
    turned up somewhere else — in a repaint, in a thread of COM — and
    never said a word about the camera.

    It was measured, not guessed. The same suite, one variable at a
    time:

        a fake camera ............. no crash
        the camera, no tracker ... no crash
        the camera and the tracker  CRASH

    So the camera moved to a small process of its own
    (`core/camera_worker.py`), and the process that draws the interface
    no longer touches the device at all. It receives pictures.

THE CONTRACT
    A subscriber is a callable that receives (ok, jpeg_bytes). The bytes
    are the picture as it came out of the camera, and nobody decodes
    them unless it needs the pixels. Subscribing starts the camera if
    it is not running; unsubscribing takes one reader away.

    The pictures travel as JPEG. The tracker does not need more: a hand
    is a hand at 85% of quality, and a quarter of the pixels is a
    quarter of the work. The picture is ENCODED in the process that owns
    the device, and decoded —if anybody needs pixels— by the one reader
    that has to look at them.

    The camera is let go only when the program is going away. Letting a
    device go while the program runs tears its graph down under the
    feet of the driver, and the process dies with an access violation a
    few seconds later.
"""
from __future__ import annotations

import atexit
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

# HOW LONG TO WAIT FOR THE PROCESS THAT HOLDS THE CAMERA.
_JOIN_TIMEOUT = 5.0

# THE ROOT OF THE PROGRAM, so that the worker can be started as a module
# no matter where the program was started from.
_ROOT = Path(__file__).resolve().parent.parent

_CV2_LOCKED = False
_CV2_LOCK = threading.Lock()


def cv2_quiet():
    """OpenCV, with its own pool of threads cut down to ONE.

    WHY
        OpenCV reads a picture with a pool of eight threads by default.
        In the process that draws the interface that pool is eight more
        threads with their own idea of what memory belongs to whom,
        sitting next to Qt's idea of the same thing and next to the
        native graph of the hand tracker. The crash that came out of that
        was an access violation with no culprit in sight: the dump
        pointed at a thread that was doing nothing but waiting.

        One picture at a time does not need eight threads, and one thread
        cannot disagree with itself.

    It is asked for the module itself, so the callers keep using it as
    they always did.
    """
    global _CV2_LOCKED
    import cv2
    with _CV2_LOCK:
        if not _CV2_LOCKED:
            _CV2_LOCKED = True
            try:
                cv2.setNumThreads(1)
            except Exception:                           # noqa: BLE001
                pass
    return cv2


class CameraHub:
    """The one camera of the process."""

    def __init__(self):
        self._lock = threading.RLock()
        self._subscribers: list = []
        # THE READERS OF THE HANDS.
        #
        # They are a separate list because they want something different:
        # the pictures are the picture, and the hands are the NUMBERS of a
        # hand measured in the other process. Nobody that wants a picture
        # is interested in a hand, and nobody that wants a hand has to
        # decode a picture to get it.
        self._hands_subscribers: list = []
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._error = ""
        self._index = 0
        self._opens = 0
        # HOW FAST THE CAMERA IS ACTUALLY TALKING.
        #
        # The device is not always ours. When another program — the
        # Windows camera, a call, a recorder — keeps it, the driver
        # hands out one picture a second instead of fifteen, and the
        # hand on the screen trails behind by a whole second with no
        # explanation anywhere. Counting the pictures is how the truth
        # is told: the program can say what is happening instead of
        # leaving the user to guess.
        self._times: list[float] = []
        self._fps = 0.0

    def _deliver_hands(self, data: bytes) -> None:
        """The hands the worker measured, to whoever asked for them.

        The data is plain JSON: the tracker is in the other process and
        what it found travels as numbers. A reader that fails must not
        take the camera down for the others, exactly as with the frames.
        """
        try:
            import json

            payload = json.loads(data.decode("utf-8"))
        except Exception:                               # noqa: BLE001
            return
        if not isinstance(payload, dict):
            return
        with self._lock:
            readers = list(self._hands_subscribers)
        for reader in readers:
            try:
                reader(payload)
            except Exception:
                pass

    # ── the readers ─────────────────────────────────────────────────────────

    def subscribe(self, callback) -> None:
        """Start receiving frames. The callback is called with (ok, frame)."""
        with self._lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)
        self._ensure_running()

    def subscribe_hands(self, callback) -> None:
        """Start receiving the hands. The callback gets the payload.

        The payload is the plain data of the hand: the tracker lives in
        the process that holds the camera, and what crosses the line
        between the processes is a number, never an object."""
        with self._lock:
            if callback not in self._hands_subscribers:
                self._hands_subscribers.append(callback)
        self._ensure_running()

    def unsubscribe_hands(self, callback) -> None:
        """Stop receiving the hands."""
        with self._lock:
            if callback in self._hands_subscribers:
                self._hands_subscribers.remove(callback)

    def unsubscribe(self, callback) -> None:
        """Stop receiving frames.

        THE CAMERA IS NOT CLOSED HERE. It is closed when the program is
        going away, and not before: letting the device go while the
        program runs is what made the process die.
        """
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    def release_readers(self) -> None:
        """Let go of every reader, and keep the camera open.

        This is what a test does at the end of a case: nobody should be
        left reading frames that belong to a window that is gone, and
        the device must not be opened again for the next case.
        """
        with self._lock:
            self._subscribers = []
            self._hands_subscribers = []

    def stop_all(self) -> None:
        """Close the camera, whatever is still asking for frames.

        It is what the program does when it is really going away: a
        camera that nobody asked for must not stay claimed, because a
        claimed camera is a light that stays on and a device that stays
        taken.

        IT WAITS FOR THE THREAD THAT WAS READING, TOO.
            That thread is the one that hands the pictures to the
            readers, and one of those readers is the hand tracker, whose
            native graph is freed when the program goes. A tracker that
            is freed while a frame is being tracked inside it is exactly
            the access violation this whole file exists to avoid — so
            the camera stops first, and the thread is waited for.
        """
        with self._lock:
            self._subscribers = []
            self._hands_subscribers = []
            self._stop.set()
            proc = self._proc
            self._proc = None
            reader = self._reader
            self._reader = None
        if proc is not None:
            self._kill(proc)
        if reader is not None and reader.is_alive():
            reader.join(timeout=_JOIN_TIMEOUT)

    @property
    def running(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None

    @property
    def readers(self) -> int:
        """How many readers the camera has, of either kind.

        The hands are readers of the camera too: the process that holds
        the device is opened for them exactly as it is opened for the
        pictures, and a camera that is running for nobody is a camera
        that is left claimed."""
        with self._lock:
            return len(self._subscribers) + len(self._hands_subscribers)

    @property
    def error(self) -> str:
        return self._error

    @property
    def fps(self) -> float:
        """How many pictures a second the camera is really giving."""
        with self._lock:
            return self._fps

    @property
    def busy(self) -> bool:
        """The camera is being held by somebody else.

        It was measured: with the device free the camera gives fifteen
        pictures a second, and with another program keeping it, one.
        Nothing in the program is slow — the device is busy, and the
        program that knows it can say so.
        """
        with self._lock:
            return len(self._times) >= 4 and self._fps < 6.0

    def _note_frame(self) -> None:
        """Write down that a picture arrived, and how fast they come."""
        now = time.monotonic()
        with self._lock:
            self._times.append(now)
            if len(self._times) > 12:
                del self._times[:-12]
            if len(self._times) >= 2:
                span = self._times[-1] - self._times[0]
                if span > 0.05:
                    self._fps = (len(self._times) - 1) / span

    @property
    def index(self) -> int:
        """Which camera the hub is using."""
        with self._lock:
            return self._index

    @property
    def opens(self) -> int:
        """How many times the camera has been opened. One is enough."""
        with self._lock:
            return self._opens

    def set_index(self, index: int) -> None:
        """Which camera to open. The next reader gets the new one."""
        with self._lock:
            index = max(0, int(index))
            if index == self._index:
                return
            self._index = index
        # The camera that is open is not the one that was asked for: it
        # is stopped, and the next reader opens the right one.
        self._restart()

    # ── the process that holds the camera ───────────────────────────────────

    def _spawn(self):
        """Start the process that holds the camera.

        This is the only place where the program outside is started, and
        it is a seam on purpose: a test can put a camera of its own
        here, without a camera.

        The worker is told which process started it, so that it can
        leave when this one is gone.
        """
        return subprocess.Popen(
            [sys.executable, "-m", "core.camera_worker",
             "--index", str(self._index), "--parent", str(os.getpid())],
            cwd=str(_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def _ensure_running(self) -> None:
        """There is ONE process reading the camera, and there always is one."""
        with self._lock:
            proc = self._proc
            if proc is not None and proc.poll() is None:
                return
            self._stop.clear()
            self._error = ""
            try:
                proc = self._spawn()
            except Exception as exc:                   # noqa: BLE001
                self._error = f"the camera could not be started: {type(exc).__name__}"
                return
            self._proc = proc
            self._opens += 1
            self._reader = threading.Thread(target=self._read_loop, daemon=True,
                                            name="camera-reader")
            self._reader.start()

    def _restart(self) -> None:
        """Stop the camera and open the new one, if somebody is still watching.

        Killing the worker and waiting for the next reader to ask again
        would leave the camera switched off for a reader that is already
        subscribed: the pictures would simply stop arriving, with nothing
        to say why.
        """
        with self._lock:
            proc = self._proc
            self._proc = None
            wanted = bool(self._subscribers)
        if proc is not None:
            self._kill(proc)
        if wanted:
            self._ensure_running()

    @staticmethod
    def _kill(proc) -> None:
        """Stop the process that holds the camera, and wait for it."""
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=_JOIN_TIMEOUT)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        # The writing end of the pipe is closed, so the worker sees the
        # end of it and leaves on its own if it is still alive.
        for stream in (proc.stdin, proc.stdout):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass

    def _read_loop(self) -> None:
        """Read the pictures of the worker and hand them to the readers.

        THE PICTURES ARE HANDED OVER AS THEY ARRIVE: AS JPEG BYTES.

        They used to be decoded here, in this process, and that was the
        last native code in the thread that reads the camera. OpenCV's
        own pool of threads was working on every picture, right next to
        the interface, and the crash that came out of that was an access
        violation with no culprit in sight.

        So the bytes go as they are, and whoever needs PIXELS asks for
        them: the hand tracker, which needs to look for bones, and the
        tool that takes a picture. The two that only need to SHOW a
        picture —the tile of the viewer and the live stream of the
        head— take the bytes as they are and let the interface draw
        them, which is what an interface is for.
        """
        with self._lock:
            proc = self._proc
        if proc is None or proc.stdout is None:
            return
        stream = proc.stdout
        try:
            while not self._stop.is_set():
                kind = stream.read(1)
                if not kind or len(kind) < 1:
                    break
                header = stream.read(4)
                if not header or len(header) < 4:
                    break
                length = int.from_bytes(header, "little")
                if length <= 0 or length > 32 * 1024 * 1024:
                    break
                data = b""
                while len(data) < length:
                    piece = stream.read(length - len(data))
                    if not piece:
                        break
                    data += piece
                if len(data) < length:
                    break
                if kind == b"\x01":
                    self._note_frame()
                    with self._lock:
                        readers = list(self._subscribers)
                    for reader in readers:
                        try:
                            reader(True, data)
                        except Exception:
                            # A reader that fails must not take the camera
                            # down for the others.
                            pass
                elif kind == b"\x02":
                    self._deliver_hands(data)
        except Exception as exc:                       # noqa: BLE001
            self._error = f"the camera stopped: {type(exc).__name__}"
        finally:
            # The worker is gone. The next reader opens it again.
            with self._lock:
                if self._proc is proc:
                    self._proc = None
            if not self._stop.is_set() and not self._error:
                self._error = "the camera stopped"


# THE HUB OF THE PROCESS.
#
# One instance, like the one tracker of the hands: two cameras in the same
# process are two handles to the same device, and that is what crashed it.
_HUB = CameraHub()


def hub() -> CameraHub:
    """The one camera of the process."""
    return _HUB


def subscribe(callback) -> None:
    _HUB.subscribe(callback)


def unsubscribe(callback) -> None:
    _HUB.unsubscribe(callback)


def subscribe_hands(callback) -> None:
    """Start receiving the hands of the camera."""
    _HUB.subscribe_hands(callback)


def unsubscribe_hands(callback) -> None:
    """Stop receiving the hands of the camera."""
    _HUB.unsubscribe_hands(callback)


def release_readers() -> None:
    """Let go of every reader, and keep the camera open."""
    _HUB.release_readers()


def decode_jpeg(data: bytes):
    """The picture, as an RGB array. None when it cannot be read.

    PIL, AND NOT OPENCV, AND THE REASON IS WORTH A PARAGRAPH.
        OpenCV reads a picture with a pool of threads of its own, and
        that pool was the last native thing left in the thread that
        reads the camera: eight threads with their own idea of what
        memory belongs to whom, next to the interface, next to the
        native graph of the hand tracker. The crash that came out of
        that was an access violation with no culprit in sight.

        PIL decodes a picture with one thread and no pool at all, which
        is what a preview and a tracker need. OpenCV is still there for
        the work that is really its: the skin segmentation of the
        fallback tracker, which is only used when MediaPipe is missing.
    """
    try:
        import io

        import numpy as np
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            return np.asarray(image.convert("RGB"))
    except Exception:                                   # noqa: BLE001
        pass
    try:
        cv2 = cv2_quiet()
        import numpy as np

        return cv2.imdecode(np.frombuffer(data, dtype=np.uint8),
                            cv2.IMREAD_COLOR)[:, :, ::-1]
    except Exception:                                   # noqa: BLE001
        return None


def stop_all() -> None:
    """Close the camera for good. The program is going away."""
    _HUB.stop_all()


# HOW LONG THE PROBE IS GIVEN.
#
# A camera that takes longer than this to open is a camera that is not
# going to work, and the question has an answer long before that.
_PROBE_TIMEOUT = 30.0


def probe_index(count: int = 6, only: int = -1) -> int:
    """Which camera gives a usable picture? -1 when none of them does.

    THE PROBE IS ANOTHER PROCESS.
        Finding out which camera is the good one means opening the
        cameras and looking at a frame. That was done in the process
        that draws the interface, and it was the last door left open to
        the crash this file exists to close: the program was touching
        the device again, with the hand tracker loaded.

        So the question is asked of a process of its own, which opens
        the devices, looks at one frame of each and answers with a
        number. The process that draws the interface never touches the
        device — not even to look for it.

    `only` asks about one camera instead of the first that works.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "core.camera_worker", "--probe",
             "--count", str(int(count)), "--only", str(int(only))],
            cwd=str(_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=_PROBE_TIMEOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:                                   # noqa: BLE001
        return -1
    try:
        out = (proc.stdout or b"").decode("utf-8", "replace")
    except Exception:                                   # noqa: BLE001
        return -1
    for line in out.splitlines():
        try:
            return int(str(line).strip())
        except ValueError:
            continue
    return -1


# THE CAMERA IS RELEASED ON THE WAY OUT.
#
# The process that holds the device is a child, and the program does not
# wait for its children: it kills them. A child killed in the middle of a
# read leaves the driver with a half-open graph. So the device is let go
# here, while the program is still alive enough to do it.
atexit.register(stop_all)
