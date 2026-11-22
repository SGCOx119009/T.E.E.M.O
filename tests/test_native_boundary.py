"""
tests/test_native_boundary.py — what the process of the interface may load.

THE RULE
    The interface is a Qt program: Qt, Python and the pieces of this
    project. Everything else is asked in a process of its own — the
    camera, and the slow numbers of the machine.

WHY THE RULE IS WORTH A TEST
    A native library loaded in the process of the interface is a native
    library whose threads live next to the event loop of the interface.
    It was measured on this machine: one WMI question adds three native
    threads to the process, and letting go of the objects it answered
    with prints «Win32 exception occurred releasing IUnknown» seven
    times over — the sound of a COM object being released by a thread
    that does not own it.

    So the boundary is written down here, and it is checked by running a
    process that builds the interface and asking the operating system
    what it loaded.

WHAT IS BEING PROVED
    1. The interface does not load OpenCV: the camera is read elsewhere.
    2. The interface does not load WMI and does not open a COM apartment:
       the GPU and the temperature are asked in another process.
    3. The slow numbers arrive as a line of JSON, and a line that is not
       JSON is ignored instead of taking the meters down.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

# The libraries that must not be in the process of the interface.
FORBIDDEN = ("cv2", "wmi", "pythoncom", "win32com", "comtypes")

# How long the check may take. Building the interface imports a lot, and
# the machine is not always quick.
_TIMEOUT = 120


def _interface_modules() -> dict:
    """What a process that builds the interface has loaded."""
    code = (
        "import sys, json\n"
        "import gui.window\n"
        f"names = {list(FORBIDDEN)!r}\n"
        "print('BOUNDARY ' + json.dumps({n: (n in sys.modules) for n in names}))\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(BASE)
    env["QT_QPA_PLATFORM"] = "offscreen"
    out = subprocess.run([sys.executable, "-c", code], cwd=str(BASE), env=env,
                         capture_output=True, text=True, timeout=_TIMEOUT)
    for line in out.stdout.splitlines():
        if line.startswith("BOUNDARY "):
            return json.loads(line[len("BOUNDARY "):])
    raise AssertionError(
        "the interface process did not answer:\n"
        f"stdout:\n{out.stdout[-2000:]}\nstderr:\n{out.stderr[-2000:]}")


def test_the_interface_does_not_load_opencv():
    """The camera is read in the process that holds it.

    OpenCV has threads of its own and was loaded from a background thread
    for no reason at all; the pictures arrive as JPEG bytes and the
    interface draws them.
    """
    loaded = _interface_modules()
    assert loaded.get("cv2") is False, "the interface loaded OpenCV"


def test_the_interface_does_not_open_a_com_apartment():
    """WMI is COM, and COM belongs to a thread.

    The GPU and the temperature are asked in core/metrics_worker.py, and
    the answers come back as a line of text.
    """
    loaded = _interface_modules()
    for name in ("wmi", "pythoncom", "win32com", "comtypes"):
        assert loaded.get(name) is False, f"the interface loaded {name}"


def test_the_interface_does_not_ask_the_machine_itself():
    """No WMI in the file that shows the meters."""
    source = (BASE / "gui" / "metrics.py").read_text(encoding="utf-8")
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "import wmi" not in stripped, f"WMI is imported here: {stripped}"
        assert "pythoncom" not in stripped, f"COM is imported here: {stripped}"


def test_the_slow_numbers_arrive_as_a_line_of_text():
    """One reading is one line of JSON, and a line that is not JSON is
    ignored instead of taking the meters down."""
    from gui import metrics as metrics_mod

    obj = object.__new__(metrics_mod._SysMetrics)
    obj.cpu = obj.mem = obj.net = 0.0
    obj.gpu = obj.tmp = -1.0
    obj._lock = __import__("threading").Lock()
    obj._worker = None
    obj._gpu_state = "pending"
    obj._tmp_state = "pending"

    obj._publish("this is not json")
    assert obj.snapshot()["gpu"] == -1.0

    obj._publish('{"t": 40.0, "g": 3.0}')
    snap = obj.snapshot()
    assert snap["tmp"] == 40.0
    assert snap["gpu"] == 3.0


def test_the_worker_answers_with_a_line_of_json():
    """The process that asks the machine says what it found."""
    from core import metrics_worker as worker

    class _Probes:
        def temp(self):
            return 41.5

        def gpu(self):
            return 7.5

    reading = worker.read_once(_Probes())
    assert reading == {"t": 41.5, "g": 7.5}


def test_the_worker_leaves_when_the_program_leaves():
    """The worker is told which process started it, and it goes when that
    process is gone: a program that is closed does not leave a reader of
    WMI behind."""
    from core import metrics_worker as worker

    assert worker._parent_alive(0) is True, "no parent means nobody to leave"
    assert worker._parent_alive(os.getpid()) is True
    # A pid that is almost certainly not there.
    assert worker._parent_alive(999999) is False
