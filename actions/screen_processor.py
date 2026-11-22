"""
Screen & webcam capture for the assistant's vision.

Provides the two capture entry points main.py uses — `_capture_screen()` and
`_capture_camera()` — plus their helpers (compression, camera auto-detection,
config access). main.py grabs a frame here on demand, then injects it into the
main Gemini Live session; there is no separate vision session here.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from core.lazyimport import LazyModule, installed

# OpenCV is 224 ms and is only ever touched by the webcam path — see
# core/lazyimport.py. Screen capture, which is what this action does on almost
# every call, never reaches it.
_CV2 = installed("cv2")

cv2 = LazyModule("cv2")

try:
    import mss
    import mss.tools
    _MSS = True
except ImportError:
    _MSS = False

try:
    import PIL.Image
    _PIL = True
except ImportError:
    _PIL = False


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_BASE        = _base_dir()
_CONFIG_PATH = _BASE / "config" / "api_keys.json"


# THE CONFIGURATION IS NOT READ OR WRITTEN HERE.
#
# api_keys.json belongs to memory/config_manager.py and to nobody else.
# This file used to open it, read it, change one key and write it back —
# and every other reader of the same file was reading a version that
# could be a half-written one. So the camera index now goes through the
# manager, like every other setting.
def _save_camera_index(index: int) -> None:
    try:
        from memory.config_manager import save_camera_index
        save_camera_index(index)
    except Exception as e:
        print(f"[Vision] ⚠️  Could not save the camera index: {e}")


def _saved_camera_index():
    """Which camera was picked, or None when nobody has picked one."""
    try:
        from memory.config_manager import get_camera_index
        return get_camera_index()
    except Exception:
        return None


def _get_os() -> str:
    """Windows. Always.

    THE PURGE THIS FINISHES
        This used to read an "os_system" key from the configuration and
        choose the camera backend from it: DirectShow for Windows,
        AVFoundation for macOS, anything else for whatever. AVFoundation
        does not exist on Windows, so a configuration file that still
        carried "mac" from another machine made the camera fail with an
        error that named a framework nobody on this computer has.

        The product is Windows-only. The camera backend is DirectShow.
    """
    return "windows"


_IMG_MAX_W = 1280
_IMG_MAX_H = 720
_JPEG_Q    = 82


def _compress(img_bytes: bytes, source_format: str = "PNG") -> tuple[bytes, str]:
    if not _PIL:
        return img_bytes, f"image/{source_format.lower()}"

    try:
        img = PIL.Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img.thumbnail((_IMG_MAX_W, _IMG_MAX_H), PIL.Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_JPEG_Q, optimize=False)
        return buf.getvalue(), "image/jpeg"
    except Exception as e:
        print(f"[Vision] ⚠️  Image compress failed: {e}")
        return img_bytes, f"image/{source_format.lower()}"


def _capture_screen() -> tuple[bytes, str]:
    """A picture of the screen, or a RuntimeError that says what happened.

    THE CAPTURE CAN BE REFUSED, AND THAT IS NOT A BUG
        Windows puts the desktop on a different surface while a UAC prompt
        is up, and a screen capture on that surface is denied. The denial
        arrives as `BitBlt: Acceso denegado` from the graphics stack, and
        the moment it does is the moment the assistant is most likely to be
        looking at the screen — because the user just clicked "Yes".

        Unhandled, that exception walked all the way up through the tool
        dispatcher and took the turn with it: a traceback on the console
        and no answer to the user. So it is caught here, named, and
        re-raised as something that says what to do about it.
    """
    if not _MSS:
        raise RuntimeError("mss is not installed. Run: pip install mss")

    try:
        with mss.mss() as sct:
            monitors = sct.monitors      # [0] = all combined, [1..n] = real screens
            target   = monitors[1] if len(monitors) > 1 else monitors[0]
            shot     = sct.grab(target)
            png      = mss.tools.to_png(shot.rgb, shot.size)
    except PermissionError as exc:
        raise RuntimeError(
            "Windows refused the screen capture. A security prompt is on "
            "screen, or the session is locked; answer it and try again."
        ) from exc
    except OSError as exc:
        # WinError 5 is "Access denied" and arrives here on the secure
        # desktop; anything else in this family is the same story told
        # with different words.
        raise RuntimeError(
            f"the screen could not be captured ({exc}). A security prompt "
            f"may be on screen, or the session may be locked."
        ) from exc

    return _compress(png, "PNG")


def _probe_camera(index: int, backend: int = 0, warmup: int = 5) -> bool:
    """Does this camera give a picture with something in it?

    THE PROBE DOES NOT TOUCH THE DEVICE IN THIS PROCESS.
        It used to: `cv2.VideoCapture` right here, in the process that
        draws the interface, with the hand tracker loaded. That was the
        last door left open to the crash — and a device that is busy is
        worse than a crash, because `VideoCapture` on a busy DirectShow
        device does not fail, it WAITS.

        So the question is asked of the process that owns the camera:
        `core/camera.probe_index()` runs another worker, which opens the
        devices, looks at a frame of each and answers with a number.
    """
    try:
        from core.camera import probe_index
    except Exception:
        return False
    return probe_index(count=1, only=int(index)) == int(index)


def _detect_camera_index() -> int:
    """Find a camera that gives a usable frame.

    THE PROBE DOES NOT RUN IN THIS PROCESS.
        The camera of the process is opened by the hub of `core/camera.py`
        and by nobody else, so a probe that opened its own handle could
        collide with the hands or with the tile of the viewer — and two
        handles to the same device is what made the whole program die with
        an access violation. Worse, a device that is busy does not fail:
        `VideoCapture` on a busy DirectShow device WAITS, and the program
        stands there waiting for a camera that will never answer.

        So the probe is another process: it opens the devices, looks at one
        frame of each and answers with the index that worked. When the hub
        is busy there is nothing to probe: the camera it is using is the
        camera.
    """
    try:
        from core.camera import hub as _camera_hub
        hub = _camera_hub()
        if hub.readers:
            return hub.index
    except Exception:
        pass

    print("[Vision] 🔍 Auto-detecting camera...")
    try:
        from core.camera import probe_index
        found = probe_index(count=6)
    except Exception:
        found = -1

    if found is not None and found >= 0:
        print(f"[Vision] ✅ Camera found at index {found}")
        _save_camera_index(found)
        return found

    print("[Vision] ⚠️  No camera found — defaulting to index 0")
    _save_camera_index(0)
    return 0


def _get_camera_index() -> int:
    saved = _saved_camera_index()
    if saved is not None:
        return int(saved)
    return _detect_camera_index()


def _one_frame(hub, timeout: float = 4.0):
    """Wait for one frame of the shared camera and give it back.

    The camera may already be open for somebody else — for the hands, for
    the tile of the viewer — and then the frame is simply the next one
    that goes by. It is never opened twice.

    WHAT COMES BACK IS THE PICTURE, IN RGB. What travels is a JPEG, and
    the picture is decoded only by the reader that has to look at it.
    """
    import threading
    import time

    got: list = []

    def reader(ok, jpeg):
        if ok and jpeg and not got:
            got.append(jpeg)

    hub.subscribe(reader)
    deadline = time.time() + timeout
    try:
        while not got and time.time() < deadline:
            time.sleep(0.03)
    finally:
        hub.unsubscribe(reader)
    if not got:
        return None

    from core.camera import decode_jpeg

    return decode_jpeg(got[0])


def _capture_camera() -> tuple[bytes, str]:
    """One picture of the camera, taken from the camera OF THE PROCESS.

    THE CAMERA IS NOT OPENED HERE. It is the one of `core/camera.py`, and
    this is one more reader of it: the hands, the tile of the viewer and
    this tool all want the same device, and a camera opened twice is what
    made the whole program die with an access violation.
    """
    if not _CV2:
        raise RuntimeError("OpenCV (cv2) is not installed. Run: pip install opencv-python")

    from core.camera import hub as _camera_hub

    hub = _camera_hub()
    index = _get_camera_index()
    if index is not None:
        hub.set_index(index)

    frame = _one_frame(hub)
    if frame is None:
        raise RuntimeError(f"Camera index {index} could not be opened "
                           f"or returned no frame.")

    if _PIL:
        # The picture arrives in RGB, which is what PIL wants.
        img = PIL.Image.fromarray(frame)
        img.thumbnail((_IMG_MAX_W, _IMG_MAX_H), PIL.Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_JPEG_Q)
        return buf.getvalue(), "image/jpeg"

    import numpy as np

    _, buf = cv2.imencode(".jpg", cv2.cvtColor(np.ascontiguousarray(frame),
                                                cv2.COLOR_RGB2BGR),
                           [cv2.IMWRITE_JPEG_QUALITY, _JPEG_Q])
    return buf.tobytes(), "image/jpeg"
