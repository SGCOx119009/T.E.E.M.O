"""core/camera_worker.py — the camera, in a process of its own.

WHY THIS FILE EXISTS
    The camera was opened in the process that draws the interface, and
    so was the hand tracker. That combination killed the program, again
    and again, with «Windows fatal exception: access violation» and no
    clue: the tracker opens a native graph with a pool of threads of its
    own, the camera opens a device with a pool of its own, and what one
    of them leaves behind is found later by the other — in a repaint, in
    a thread of COM, wherever that memory is touched next.

    The camera is therefore NOT in this process any more. It is in a
    small process of its own, and it does the only thing it is good at:
    read the device and send the pictures.

THE CONTRACT WITH THE PARENT
    The pictures travel as JPEG, one after the other, each one with its
    length in front. JPEG because it is a quarter of the pixels and the
    tracker does not need more: a hand is a hand at 85% of quality.

    The worker watches the parent: the parent passes its process id, and
    when that process is no longer there, the worker leaves. Without
    that, a program that is killed would leave the camera claimed by a
    ghost, and the next program to ask for it would find the device
    taken.

THE PROBE IS HERE TOO
    Finding out which camera is the good one means opening the cameras
    and looking at a frame. That was done in the process that draws the
    interface, and it was the last door left open to the same crash:
    the program was touching the device again, with the tracker loaded.

    So the probe is another run of THIS process, in a mode that opens the
    devices, looks at one frame of each and prints the index that gave
    a usable picture. The parent asks; it never touches the device.

THE ARGUMENTS ARE NAMED
    They were read by position once, and the order the parent gave them
    in was not the order the worker expected. With the first camera it
    worked by luck; with any other one the worker opened the wrong
    camera and watched the wrong process for ever.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from typing import NamedTuple

# The exit code Windows gives to a process that is still running.
_STILL_ACTIVE = 259

# How many cameras are looked at when the user has not chosen one.
_PROBE_COUNT = 6


class Args(NamedTuple):
    """What the parent said."""

    index: int
    parent: int
    probe: bool
    count: int = _PROBE_COUNT
    only: int = -1


def _is_number(text: str) -> bool:
    """Is this a number and not a flag? -1 is a number; -probe is not."""
    try:
        int(str(text))
        return True
    except ValueError:
        return False


def _looks_like_flag(text: str) -> bool:
    """A dash on its own means a flag; a dash and digits mean a number."""
    return str(text).startswith("-") and not _is_number(text)


def parse_args(argv) -> Args:
    """Which camera, which process started us, and whether this is a probe.

    The arguments are NAMED. They were read by position once, and the
    order was not the order the parent gave them in: with a camera other
    than the first, the worker opened the wrong camera and watched the
    wrong process for ever.
    """
    index = 0
    parent = 0
    probe = False
    count = _PROBE_COUNT
    only = -1
    argv = list(argv)
    position = 0
    while position < len(argv):
        raw = str(argv[position])
        key = raw.lstrip("-").lower()
        value = ""
        if "=" in key:
            key, value = key.split("=", 1)
        elif position + 1 < len(argv) and not _looks_like_flag(argv[position + 1]):
            position += 1
            value = str(argv[position])
        if key in ("probe", "find", "detect"):
            probe = True
        else:
            try:
                number = int(value)
            except ValueError:
                position += 1
                continue
            if key in ("index", "i", "camera"):
                index = max(0, number)
            elif key in ("parent", "p", "pid"):
                parent = max(0, number)
            elif key in ("count", "n"):
                count = max(1, number)
            elif key in ("only", "one"):
                only = max(0, number)
        position += 1
    return Args(index=index, parent=parent, probe=probe, count=count, only=only)


def _parent_is_alive(pid: int) -> bool:
    """Is the process that started this one still there?"""
    if pid <= 0:
        return True
    try:
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        code = ctypes.c_ulong()
        alive = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        if not alive:
            return False
        return code.value == _STILL_ACTIVE
    except Exception:
        return True


def _watch_the_parent(pid: int) -> None:
    """Leave when the parent goes.

    The parent passes its process id. A program that is killed does not
    get to clean up, and the camera would stay claimed by a ghost that
    nobody can turn off.
    """
    while True:
        time.sleep(1.0)
        if not _parent_is_alive(pid):
            os._exit(0)


def _usable(cap) -> bool:
    """Does this camera give a picture with something in it?"""
    import numpy as np

    for _ in range(5):
        ok, frame = cap.read()
        if ok and frame is not None:
            try:
                return bool(np.mean(frame) > 8)
            except Exception:
                return True
    return False


def _probe(count: int = _PROBE_COUNT, only: int = -1) -> int:
    """Find the camera that gives a usable frame.

    The answer is printed on stdout and nothing else is written there:
    the parent reads one number. -1 means that none of them was any use.
    """
    try:
        import cv2
    except Exception:                                  # noqa: BLE001
        print("-1", flush=True)
        return -1

    try:
        cv2.setNumThreads(1)
    except Exception:
        pass

    indices = [only] if only >= 0 else list(range(max(1, count)))
    for index in indices:
        for backend in (cv2.CAP_DSHOW, 0):
            try:
                cap = cv2.VideoCapture(index, backend)
            except Exception:
                cap = None
            if cap is None:
                continue
            try:
                if cap.isOpened() and _usable(cap):
                    print(index, flush=True)
                    return index
            except Exception:
                pass
            finally:
                try:
                    cap.release()
                except Exception:
                    pass
    print("-1", flush=True)
    return -1


def main() -> int:
    args = parse_args(sys.argv[1:])

    if args.probe:
        # A probe is a question, not a subscription: it answers and goes.
        return 0 if _probe(count=args.count, only=args.only) >= 0 else 1

    if args.parent > 0:
        threading.Thread(target=_watch_the_parent, args=(args.parent,), daemon=True,
                         name="parent-watch").start()

    try:
        import cv2
    except Exception:                                  # noqa: BLE001
        return 2

    # One thread is enough: the camera gives one picture at a time, and
    # a pool of threads next to the one of tracker is exactly what
    # this process exists to avoid.
    try:
        cv2.setNumThreads(1)
    except Exception:
        pass

    backend = cv2.CAP_DSHOW
    cap = cv2.VideoCapture(args.index, backend)
    if not cap.isOpened():
        cap.release()
        cap = cv2.VideoCapture(args.index)
    if not cap.isOpened():
        cap.release()
        if args.index:
            cap = cv2.VideoCapture(0, backend)
    if not cap.isOpened():
        cap.release()
        cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        sys.stderr.write("[camera] the camera could not be opened\n")
        return 3

    out = sys.stdout.buffer
    # FIFTEEN PICTURES A SECOND.
    #
    # A hand is not faster than that, and a preview does not need more:
    # thirty pictures a second meant thirty JPEGs a second to encode,
    # to move through the pipe and to draw on the screen, for a picture
    # that the eye reads as a picture either way.
    period = 1.0 / 15.0
    last = 0.0
    try:
        while True:
            now = time.time()
            if now - last < period:
                time.sleep(0.002)
                continue
            last = now
            ok, frame = cap.read()
            if not ok or frame is None:
                time.sleep(0.05)
                continue
            good, buf = cv2.imencode(".jpg", frame,
                                     [cv2.IMWRITE_JPEG_QUALITY, 85])
            if not good:
                continue
            data = buf.tobytes()
            out.write(len(data).to_bytes(4, "little"))
            out.write(data)
            out.flush()
    except (BrokenPipeError, KeyboardInterrupt):
        return 0
    finally:
        try:
            cap.release()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
