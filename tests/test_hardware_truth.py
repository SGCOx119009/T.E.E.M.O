"""
tests/test_hardware_truth.py — the two things the user said were broken:
the API field that would not take the letters, and the GPU and the
temperature that said N/A.

WHAT IS BEING PROVED
    1. The GPU is read from the counters of Windows, which know about every
       card, and not only from NVML, which only knows about NVIDIA's.
    2. The temperature is read from the thermal zone of cimv2, in Kelvin,
       and turned into the degrees a person would say.
    3. The slow probes live in their own thread, so a counter that takes
       five seconds cannot freeze the processor meter.
    4. The ignition never comes up on top of a form, and the field of the
       form has the focus when the window appears.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from core import metrics_worker as worker  # noqa: E402
from gui import metrics as metrics_mod  # noqa: E402


# ── fakes ────────────────────────────────────────────────────────────────────

class _Row:
    def __init__(self, name=None, utilization=None, temperature=None):
        self.Name = name
        self.UtilizationPercentage = utilization
        self.Temperature = temperature


class _FakeConn:
    """Stands in for a WMI connection and hands out whatever it was given."""

    def __init__(self, engines=None, zones=None):
        self._engines = engines or []
        self._zones = zones or []

    def Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine(self):
        return list(self._engines)

    def Win32_PerfFormattedData_Counters_ThermalZoneInformation(self):
        return list(self._zones)


def _bare() -> worker._Probes:
    """A set of probes without the background thread.

    The thread would go and ask the real machine, which makes a test slow
    and makes the answer depend on the hardware of whoever runs it.
    """
    obj = object.__new__(worker._Probes)
    obj._pynvml = obj._pynvml_h = obj._pynvml_ok = None
    obj._wmi_conn = obj._wmi_ok = None
    obj._wmi_local = threading.local()
    obj._cimv2_local = threading.local()
    obj._counters_ok = None
    obj._counters_fail = 0
    obj._wmi_fail = 0
    obj._gpu_state = "pending"
    obj._tmp_state = "pending"
    return obj


def _bare_interface():
    """The meters of the interface, without the two background threads."""
    obj = object.__new__(metrics_mod._SysMetrics)
    obj.cpu = obj.mem = obj.net = 0.0
    obj.gpu = obj.tmp = -1.0
    obj._lock = threading.Lock()
    obj._worker = None
    obj._gpu_state = "pending"
    obj._tmp_state = "pending"
    obj._running = False
    obj._stop = threading.Event()
    return obj


# ── 1. the GPU ───────────────────────────────────────────────────────────────

def test_the_gpu_counters_sum_the_3d_engines():
    conn = _FakeConn(engines=[
        _Row(name="pid_1_engtype_3D", utilization=30),
        _Row(name="pid_2_engtype_3D", utilization=25),
        _Row(name="pid_1_engtype_Compute", utilization=99),
        _Row(name="pid_1_engtype_VideoDecode", utilization=80),
    ])
    assert worker._gpu_windows_counters(conn) == 55.0


def test_the_gpu_never_goes_over_a_hundred():
    conn = _FakeConn(engines=[
        _Row(name="pid_1_engtype_3D", utilization=80),
        _Row(name="pid_2_engtype_3D", utilization=70),
    ])
    assert worker._gpu_windows_counters(conn) == 100.0


def test_a_row_without_a_number_is_skipped_and_not_fatal():
    class _Broken:
        Name = "pid_9_engtype_3D"

        @property
        def UtilizationPercentage(self):
            raise RuntimeError("the counter is not there")

    conn = _FakeConn(engines=[_Broken(), _Row(name="pid_1_engtype_3D", utilization=12)])
    assert worker._gpu_windows_counters(conn) == 12.0


def test_the_gpu_falls_back_to_the_windows_counters_when_there_is_no_nvml(monkeypatch):
    """The machine of the user has an AMD card: NVML can only ever say no."""
    obj = _bare()
    obj._pynvml_ok = False
    monkeypatch.setattr(worker, "_OS", "Windows")
    monkeypatch.setattr(worker, "_nvml_gpu_windows", lambda: -1.0)
    monkeypatch.setattr(worker, "_gpu_windows_counters", lambda conn: 42.0)
    monkeypatch.setattr(obj, "cimv2", lambda: _FakeConn())

    assert obj.gpu() == 42.0
    assert obj._counters_ok is True


def test_the_gpu_says_na_when_there_is_nothing_to_read(monkeypatch):
    obj = _bare()
    obj._pynvml_ok = False
    monkeypatch.setattr(worker, "_OS", "Windows")
    monkeypatch.setattr(worker, "_nvml_gpu_windows", lambda: -1.0)

    def _boom(conn):
        raise RuntimeError("no counters here")

    monkeypatch.setattr(worker, "_gpu_windows_counters", _boom)

    # Three in a row and the door stays shut.
    assert obj.gpu() == -1.0
    obj.gpu()
    obj.gpu()
    assert obj._counters_ok is False
    assert obj.gpu() == -1.0


def test_the_nvml_way_is_tried_first(monkeypatch):
    obj = _bare()
    obj._pynvml_ok = True
    monkeypatch.setattr(worker, "_OS", "Windows")
    called = {"counters": False}

    def _counters(conn):
        called["counters"] = True
        return 11.0

    monkeypatch.setattr(worker, "_gpu_windows_counters", _counters)

    class _Rates:
        gpu = 77.0

    class _FakeNvml:
        @staticmethod
        def nvmlDeviceGetUtilizationRates(handle):
            return _Rates()

    obj._pynvml = _FakeNvml
    obj._pynvml_h = object()

    assert obj.gpu() == 77.0
    assert called["counters"] is False


# ── 2. the temperature ──────────────────────────────────────────────────────

def test_the_thermal_zone_is_read_in_kelvin_and_given_in_celsius():
    conn = _FakeConn(zones=[_Row(temperature=340.0)])
    assert worker._temp_windows(conn) == pytest.approx(66.85, abs=0.01)


def test_a_zone_with_no_reading_does_not_invent_one():
    conn = _FakeConn(zones=[_Row(temperature=0), _Row(temperature=None)])
    assert worker._temp_windows(conn) == -1.0


def test_the_temperature_falls_back_to_cimv2(monkeypatch):
    obj = _bare()
    monkeypatch.setattr(worker, "_OS", "Windows")

    def _no_sensors():
        raise AttributeError("sensors_temperatures does not exist on Windows")

    # `raising=False`: on Windows psutil has no such attribute at all — the
    # test that failed on the first run said so, which is the proof that the
    # old code could never have read a temperature through psutil here.
    monkeypatch.setattr(metrics_mod.psutil, "sensors_temperatures",
                        _no_sensors, raising=False)
    monkeypatch.setattr(worker, "_temp_windows", lambda conn: 68.75)
    monkeypatch.setattr(obj, "cimv2", lambda: _FakeConn())

    assert obj.temp() == 68.75


def test_the_temperature_says_na_when_there_is_nothing_to_read(monkeypatch):
    obj = _bare()
    monkeypatch.setattr(worker, "_OS", "Windows")

    def _no_sensors():
        raise AttributeError("sensors_temperatures does not exist on Windows")

    monkeypatch.setattr(metrics_mod.psutil, "sensors_temperatures",
                        _no_sensors, raising=False)
    monkeypatch.setattr(worker, "_temp_windows", lambda conn: -1.0)
    monkeypatch.setattr(obj, "cimv2", lambda: _FakeConn())
    obj._wmi_ok = False

    assert obj.temp() == -1.0


# ── 3. the slow probes have their own thread ────────────────────────────────

def test_the_thread_that_reads_the_hardware_opens_its_com_apartment():
    """WMI is COM, and COM belongs to a thread.

    This is the whole reason the GPU and the temperature said N/A inside
    the program and worked when the same query was made by hand: the
    probes live in a thread of their own, and that thread had never
    opened its COM apartment.
    """
    import pythoncom
    CO_E_NOTINITIALIZED = -2147221008
    result = {}

    def _ask():
        worker._com_ready()
        try:
            pythoncom.CoCreateInstance(pythoncom.CLSID_StdComponentCategoriesMgr,
                                       None, pythoncom.CLSCTX_INPROC_SERVER,
                                       pythoncom.IID_IDispatch)
            result["error"] = None
        except Exception as exc:      # noqa: BLE001 — the answer is the code
            result["error"] = exc

    thread = threading.Thread(target=_ask)
    thread.start()
    thread.join()

    error = result.get("error")
    assert error is None or error.args[0] != CO_E_NOTINITIALIZED, (
        "the thread never opened its COM apartment")


def test_the_wmi_connection_is_not_shared_between_threads():
    """A COM object belongs to the apartment that created it."""
    obj = _bare()
    obj._cimv2_local.conn = "la del hilo principal"
    seen = {}

    def _other():
        seen["conn"] = getattr(obj._cimv2_local, "conn", None)

    thread = threading.Thread(target=_other)
    thread.start()
    thread.join()
    assert seen["conn"] is None


def test_the_thread_that_asks_opens_the_apartment_before_it_asks(monkeypatch):
    """The apartment is opened by the thread that asks the question.

    Not by another one, and not by the interface: COM belongs to a thread,
    and the thread that opens it is the thread that closes it.
    """
    obj = _bare()
    called = []
    monkeypatch.setattr(worker, "_com_ready", lambda: called.append(True))

    class _FakeWmi:
        @staticmethod
        def WMI(namespace=""):
            return _FakeConn()

    monkeypatch.setattr(worker, "_load_wmi", lambda: _FakeWmi())
    obj.cimv2()
    assert called == [True]


def test_a_hiccup_does_not_disable_the_counters_for_good(monkeypatch):
    """One failure is a hiccup; three in a row is a door that stays shut."""
    obj = _bare()
    obj._pynvml_ok = False
    monkeypatch.setattr(worker, "_OS", "Windows")
    monkeypatch.setattr(worker, "_nvml_gpu_windows", lambda: -1.0)
    monkeypatch.setattr(obj, "cimv2", lambda: _FakeConn())

    def _boom(conn):
        raise RuntimeError("the counters are not there yet")

    monkeypatch.setattr(worker, "_gpu_windows_counters", _boom)

    obj.gpu()
    assert obj._counters_ok is None, "one hiccup must not close the door"
    obj.gpu()
    obj.gpu()
    assert obj._counters_ok is False


def test_the_fast_meters_do_not_wait_for_the_slow_ones():
    """The GPU counters take about five seconds, in another process.

    The processor meter must not wait for them, and the slow numbers
    only appear when the other process has answered.
    """
    obj = _bare_interface()
    obj._last_net = metrics_mod.psutil.net_io_counters()
    obj._last_net_t = time.time()
    obj._update()
    assert obj.snapshot()["gpu"] == -1.0
    assert obj.snapshot()["tmp"] == -1.0

    obj._publish('{"t": 41.5, "g": 12.0}')
    snap = obj.snapshot()
    assert snap["gpu"] == 12.0
    assert snap["tmp"] == 41.5
    assert snap["gpu_state"] == "ok"
    assert snap["tmp_state"] == "ok"


def test_a_line_that_is_not_json_is_ignored():
    """The other process writes lines of JSON. A line that is not one is
    a mistake, and a mistake must not take the meters down."""
    obj = _bare_interface()
    obj._publish("this is not json")
    assert obj.snapshot()["gpu"] == -1.0


def test_a_machine_that_does_not_say_says_na_and_not_pending():
    """-1 is an answer: there is nothing to read. It is not "still
    counting", and a meter that says N/A while it is still counting
    looks broken."""
    obj = _bare_interface()
    obj._publish('{"t": -1.0, "g": -1.0}')
    snap = obj.snapshot()
    assert snap["gpu_state"] == "na"
    assert snap["tmp_state"] == "na"


def test_the_fast_meter_does_not_touch_the_gpu_or_the_temperature():
    obj = _bare_interface()
    obj._last_net = metrics_mod.psutil.net_io_counters()
    obj._last_net_t = time.time()
    obj._update()
    snap = obj.snapshot()
    assert snap["gpu"] == -1.0
    assert snap["tmp"] == -1.0


def test_a_gpu_that_throws_does_not_cost_the_temperature(monkeypatch):
    """They are asked one at a time, so one broken source does not blind
    the other."""
    obj = _bare()

    def _boom():
        raise RuntimeError("the counters are not there")

    monkeypatch.setattr(obj, "gpu", _boom)
    monkeypatch.setattr(obj, "temp", lambda: 42.0)

    reading = worker.read_once(obj)
    assert reading["g"] == -1.0
    assert reading["t"] == 42.0


# ── 4. the form and the ignition ────────────────────────────────────────────

@pytest.fixture(scope="module")
def qt_app():
    import os
    previous = os.environ.get("QT_QPA_PLATFORM")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app
    if previous is None:
        os.environ.pop("QT_QPA_PLATFORM", None)
    else:
        os.environ["QT_QPA_PLATFORM"] = previous


@pytest.fixture(scope="module")
def window(qt_app, tmp_path_factory):
    """One real MainWindow, with the setup form on screen.

    One for the whole module: every window brings its own timers, its own
    media player and its own scene canvas.

    THE LOCKER IS EMPTY ON PURPOSE.
    The key lives in the Windows Credential Locker, which belongs to the
    machine and not to the test: a machine where the owner has already
    saved a key would show NO form at all, and these tests — which are
    about the form — would fail for a reason that has nothing to do with
    the code. So the locker of the test is one of its own, and it is
    empty: the form is there, as the tests expect it.
    """
    config_dir = tmp_path_factory.mktemp("config")
    from memory import config_manager as cm
    original_dir = cm.CONFIG_DIR
    original_file = cm.CONFIG_FILE
    original_read = cm._read_secure_key
    original_store = cm._store_secure_key
    original_remove = cm._remove_secure_key
    cm.CONFIG_DIR = config_dir
    cm.CONFIG_FILE = config_dir / "api_keys.json"
    cm._read_secure_key = lambda: None
    cm._store_secure_key = lambda key: None
    cm._remove_secure_key = lambda: None

    from gui.window import MainWindow
    win = MainWindow(str(BASE / "core" / "face_model.obj"))
    win.show()
    yield win

    # THE TEARDOWN, AND IT IS EXACTLY THE ONE test_appearance.py USES.
    win._quitting = True
    try:
        win.close()
    except Exception:
        pass
    win.deleteLater()
    cm.CONFIG_DIR = original_dir
    cm.CONFIG_FILE = original_file
    cm._read_secure_key = original_read
    cm._store_secure_key = original_store
    cm._remove_secure_key = original_remove


def test_the_form_is_on_screen(window):
    assert window._overlay is not None
    assert window._overlay.isVisible()


def test_the_field_has_the_focus_when_the_window_appears(window):
    from PyQt6.QtWidgets import QApplication
    QApplication.processEvents()
    assert window._overlay._key_input.hasFocus()


def test_letters_land_in_the_field_without_a_click(window):
    from PyQt6.QtTest import QTest
    field = window._overlay._key_input
    field.clear()
    QTest.keyClicks(field, "AIzaPrueba")
    assert field.text() == "AIzaPrueba"


def test_the_form_is_in_the_middle_of_the_panel(window):
    from PyQt6.QtWidgets import QApplication
    QApplication.processEvents()
    cw = window.centralWidget()
    assert window._overlay.geometry().x() == (cw.width() - window._overlay.width()) // 2
    assert window._overlay.geometry().y() == (cw.height() - window._overlay.height()) // 2


def test_the_ignition_never_covers_the_form(window):
    """The ignition is a greeting, not a splash screen over the key field."""
    boot = window._boot
    boot.hide()
    window._start_boot()
    assert boot.isVisible() is False


def test_the_ignition_comes_up_when_the_form_is_gone(window):
    overlay = window._overlay
    window._overlay = None
    try:
        window._start_boot()
        assert window._boot.isVisible() is True
    finally:
        window._boot.hide()
        window._overlay = overlay
