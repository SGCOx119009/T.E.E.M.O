"""core/hardware_equilibrium.py — the ladder the machine walks down when it
is loaded.

WHAT THIS IS
    An adaptive regulator. It asks the machine how it is doing, decides how
    much of the presentation it can afford, and says so — four times a
    second is too often, once a second is right.

    The scheme is the one of the reference implementation, and the ladder is
    exactly this:

        OPTIMAL           < 60 %   60 fps   16 ms
        BALANCED          60-75 %  45 fps   22 ms
        THROTTLED         75-85 %  30 fps   33 ms
        CRITICAL_RECOVERY > 85 %   15 fps   66 ms

    Why the ladder degrades the PRESENTATION before the CAPACITY: a machine
    that is busy should show a smaller picture, not a slower answer. The
    frame interval of the HUD is the thing that gives (16 -> 22 -> 33 ->
    66 ms), so the interface degrades before the machine is asked to do
    more work than it can.

    The four interventions of the scheme, and what this file does with them:

        1. THROTTLING OF FPS — the ladder above. `fps_for` and
           `frame_interval_for` give the numbers; the HUD's QTimer (which
           today starts at a fixed 16 ms) is what applies them.
        2. COMPACTION OF RAM — `purge_working_set()` calls
           `EmptyWorkingSet` on THIS PROCESS and on nothing else. The
           reference implementation compacted more broadly; that is a
           behaviour of the wide scope
           that has to be narrowed, because the memory of another
           application is not ours to touch.
        3. RELIEVING THE GPU — the free VRAM of this reading is published in
           the `ResourceState`, which makes this module the source of truth
           for the VRAM that phase 7 needs.
        4. FIXING THE PRIORITY — not here: it belongs to the war mode of
           the HUD, and a thread's priority is not something a core module
           should set behind the user's back.

WHY THERE IS NO Qt IN HERE
    The plan asks for a QTimer of 1 Hz. A timer is a Qt thing, and `core/`
    does not import Qt: instead this module publishes through a LIST OF
    CALLBACKS, and whoever owns the Qt clock (the GUI) subscribes to it and
    calls `publish()` once a second. The same regulator can also run on its
    own daemon thread (`start()`), which is what the tests and the phone
    dashboard use.

WHY THE LISTENERS ARE TOLD ONLY WHEN THE LEVEL CHANGES
    A reading every second that also notifies every second is noise: the
    HUD repaints on every message it gets, and a repaint per second of a
    HUD that already repaints on its own timer is a repaint that buys
    nothing. So `publish()` notifies only when the LEVEL is different from
    the last one published. A caller that wants every reading calls
    `sample()`.

WHY THE NUMBERS MAY SAY "I DO NOT KNOW"
    There is a machine without an NVIDIA card, and a package that is not
    installed. The GPU load and the free VRAM are then -1.0, which is an
    answer ("this machine does not say") and not a failure. The regulator
    reads what it can and does not break over what it cannot.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace

__all__ = [
    "LEVELS",
    "ResourceState",
    "HardwareRegulator",
    "level_for",
    "fps_for",
    "frame_interval_for",
    "load_from",
]


# ── the ladder ────────────────────────────────────────────────────────────────

LEVELS: tuple[str, ...] = ("OPTIMAL", "BALANCED", "THROTTLED", "CRITICAL_RECOVERY")

# The frame rate and the frame interval of each level. The interval is what
# the HUD's QTimer is given, and it is the number that matters: 60 fps is
# 16 ms of budget per frame, 15 fps is 66.
_FPS: dict[str, int] = {
    "OPTIMAL": 60,
    "BALANCED": 45,
    "THROTTLED": 30,
    "CRITICAL_RECOVERY": 15,
}

_FRAME_INTERVAL_MS: dict[str, int] = {
    "OPTIMAL": 16,
    "BALANCED": 22,
    "THROTTLED": 33,
    "CRITICAL_RECOVERY": 66,
}

# The limits of the ladder, in percent of combined load. The upper bound
# of a level belongs to it and the lower bound does not: a load of exactly
# 60.0 is BALANCED, a load of exactly 75.0 is BALANCED too, and a load of
# exactly 85.0 is THROTTLED.
_OPTIMAL_MAX = 60.0
_BALANCED_MAX = 75.0
_THROTTLED_MAX = 85.0

# How much each reading counts in the combined load. The processor is the
# thing that runs the interface, the memory is the thing that stalls it,
# and the GPU is the thing that renders: they are not equal, and a machine
# with no GPU must not have its load divided by a number that is not there.
_CPU_WEIGHT = 0.50
_RAM_WEIGHT = 0.30
_GPU_WEIGHT = 0.20

# How often the working set may be compacted at most. EmptyWorkingSet
# throws away the pages of this process, and the pages come back the
# moment they are touched: compacting it every second would buy nothing
# and would cost a fault storm. Once every ten seconds is enough to give
# the memory back without turning the app into a page-thrashing machine.
_PURGE_MIN_INTERVAL_S = 10.0


@dataclass(frozen=True)
class ResourceState:
    """One reading of the machine.

    `level` is the step of the ladder the reading lands on, and `fps` and
    `frame_interval_ms` are what that step means for the HUD.

    `gpu_percent` and `vram_free_mb` are -1.0 when the machine does not
    say: a computer with no NVIDIA card, or a machine where NVML could not
    be initialised. The free VRAM is the number phase 7 needs, so it is
    published here and not computed anywhere else.

    `memory_purged` says whether this reading ended with a working-set
    compaction of THIS process.
    """

    level: str
    load_percent: float
    cpu_percent: float
    ram_percent: float
    gpu_percent: float
    vram_free_mb: float
    fps: int
    frame_interval_ms: int
    memory_purged: bool
    at: float


# ── the ladder, asked one question at a time ─────────────────────────────────

def level_for(load_percent: float) -> str:
    """The step of the ladder for this load, in percent.

    The limits are the ones of the plan: under 60 is OPTIMAL, 60 to 75 is
    BALANCED, 75 to 85 is THROTTLED and over 85 is CRITICAL_RECOVERY. The
    upper bound of a level belongs to it and the lower bound does not, so
    60.0 is BALANCED, 75.0 is BALANCED and 85.0 is THROTTLED.
    """
    load = float(load_percent)
    if load < _OPTIMAL_MAX:
        return "OPTIMAL"
    if load <= _BALANCED_MAX:
        return "BALANCED"
    if load <= _THROTTLED_MAX:
        return "THROTTLED"
    return "CRITICAL_RECOVERY"


def fps_for(level: str) -> int:
    """The frame rate of a level: 60, 45, 30 or 15."""
    try:
        return _FPS[level]
    except KeyError:
        raise ValueError(
            f"unknown level {level!r}; the levels are: " + ", ".join(LEVELS)
        ) from None


def frame_interval_for(level: str) -> int:
    """The frame interval of a level in milliseconds: 16, 22, 33 or 66."""
    try:
        return _FRAME_INTERVAL_MS[level]
    except KeyError:
        raise ValueError(
            f"unknown level {level!r}; the levels are: " + ", ".join(LEVELS)
        ) from None


def load_from(cpu_percent: float, ram_percent: float, gpu_percent: float) -> float:
    """The combined load that decides the step of the ladder.

    It is the weighted average of the three readings: the processor counts
    half, the memory three tenths and the GPU a fifth. A reading of -1.0
    means "this machine does not say" and it is LEFT OUT of the average,
    with the weights of the others renormalised: a computer with no GPU
    must not be judged as a machine that is half idle.
    """
    parts: list[tuple[float, float]] = []
    if cpu_percent is not None and cpu_percent >= 0.0:
        parts.append((float(cpu_percent), _CPU_WEIGHT))
    if ram_percent is not None and ram_percent >= 0.0:
        parts.append((float(ram_percent), _RAM_WEIGHT))
    if gpu_percent is not None and gpu_percent >= 0.0:
        parts.append((float(gpu_percent), _GPU_WEIGHT))
    if not parts:
        return 0.0
    total_weight = sum(weight for _, weight in parts)
    if total_weight <= 0.0:
        return 0.0
    total = sum(value * weight for value, weight in parts)
    return max(0.0, min(100.0, total / total_weight))


# ── the GPU, when the machine has one to say ────────────────────────────────

_nvml_module = None      # the imported pynvml, once
_nvml_ready = None      # None = untested, True = initialised, False = no GPU
_nvml_lock = threading.Lock()


def _nvml():
    """The NVML module, or None when this machine cannot answer.

    NVML is NVIDIA's library and it only knows NVIDIA cards: a machine
    with an AMD or an Intel card, or a machine where the driver is not
    installed, can only ever say "there is no NVML here". That is an
    answer, and it is cached: asking again would cost the same nothing
    every second.
    """
    global _nvml_module, _nvml_ready
    with _nvml_lock:
        if _nvml_ready is False:
            return None
        if _nvml_module is None:
            try:
                import warnings

                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    import pynvml  # type: ignore
            except Exception:
                _nvml_module = None
                _nvml_ready = False
                return None
            try:
                pynvml.nvmlInit()
            except Exception:
                _nvml_module = None
                _nvml_ready = False
                return None
            _nvml_module = pynvml
            _nvml_ready = True
        return _nvml_module


def _gpu_reading() -> tuple[float, float]:
    """The GPU load in percent and the free VRAM in MB.

    (-1.0, -1.0) when the machine does not say.
    """
    nvml = _nvml()
    if nvml is None:
        return (-1.0, -1.0)
    try:
        handle = nvml.nvmlDeviceGetHandleByIndex(0)
        load = float(nvml.nvmlDeviceGetUtilizationRates(handle).gpu)
        free = float(nvml.nvmlDeviceGetMemoryInfo(handle).free) / (1024.0 * 1024.0)
    except Exception:
        return (-1.0, -1.0)
    return (load, free)


# ── the regulator ────────────────────────────────────────────────────────────

class HardwareRegulator:
    """Reads the machine and publishes what it says.

    It is built with a warm-up call: `psutil.cpu_percent(interval=None)`
    answers 0.0 the first time it is asked, because there is nothing to
    compare the reading against yet. Without the warm-up the FIRST reading
    of the regulator would always say "the machine is idle", and the first
    reading is the one the interface shows when it comes up.

    The listeners are called with a `ResourceState`. A listener that raises
    does not stop the others and does not stop the regulator: a broken
    listener must not be able to freeze the machine's own watchdog.
    """

    def __init__(self, *, interval_s: float = 1.0) -> None:
        self.interval_s = float(interval_s)
        self.last_state: ResourceState | None = None
        self._listeners: list = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_purge = 0.0
        self._last_published_level: str | None = None
        self._warm_up()

    # -- the listeners -------------------------------------------------------

    def add_listener(self, fn) -> None:
        """Subscribe to the changes of level. `fn(ResourceState)`."""
        with self._lock:
            if fn not in self._listeners:
                self._listeners.append(fn)

    def remove_listener(self, fn) -> None:
        """Unsubscribe. Removing a listener that was never there is fine."""
        with self._lock:
            if fn in self._listeners:
                self._listeners.remove(fn)

    def _notify(self, state: ResourceState) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(state)
            except Exception:
                pass   # a broken listener is not the regulator's problem

    # -- the measurements ----------------------------------------------------

    def _warm_up(self) -> None:
        """Ask the processor once so that the next reading is a real one."""
        try:
            import psutil

            psutil.cpu_percent(interval=None)
        except Exception:
            pass

    def sample(self) -> ResourceState:
        """Measure the machine NOW and answer with what it says.

        It is a measurement and nothing more: it does not notify anybody.
        It may compact the working set of this process when the machine is
        loaded, because that is one of the four interventions of the scheme.
        """
        cpu = -1.0
        ram = -1.0
        try:
            import psutil

            cpu = float(psutil.cpu_percent(interval=None))
            ram = float(psutil.virtual_memory().percent)
        except Exception:
            pass
        gpu, vram_free = _gpu_reading()
        load = load_from(cpu, ram, gpu)
        level = level_for(load)
        state = ResourceState(
            level=level,
            load_percent=load,
            cpu_percent=cpu,
            ram_percent=ram,
            gpu_percent=gpu,
            vram_free_mb=vram_free,
            fps=fps_for(level),
            frame_interval_ms=frame_interval_for(level),
            memory_purged=False,
            at=time.monotonic(),
        )
        if self.should_purge(state):
            purged = self._maybe_purge()
            if purged:
                state = replace(state, memory_purged=True)
        self.last_state = state
        return state

    def publish(self) -> ResourceState:
        """Measure the machine and tell the listeners if the level changed.

        The listeners are told only when the LEVEL is different from the
        one published before: a message per second would be a repaint per
        second of a HUD that already repaints on its own timer.
        """
        previous_level = self._last_published_level
        state = self.sample()
        if previous_level is None or previous_level != state.level:
            self._last_published_level = state.level
            self._notify(state)
        return state

    # -- the compaction of the working set -----------------------------------

    def purge_working_set(self) -> bool:
        """Compact the working set of THIS process, and only this one.

        `EmptyWorkingSet` is called on the handle of the current process:
        the memory of another application is not ours to touch, and the
        scheme of the reference implementation compacts more broadly than
        that.

        It answers False when the API is not there (a system without
        psapi) and it never raises: a failed compaction is a memory
        optimisation that did not happen, not a reason to take the
        assistant down.
        """
        try:
            import ctypes

            handle = ctypes.windll.kernel32.GetCurrentProcess()
            ctypes.windll.psapi.EmptyWorkingSet(handle)
            return True
        except Exception:
            return False

    def should_purge(self, state: ResourceState) -> bool:
        """Is this state bad enough to compact the working set?

        From THROTTLED upwards: at THROTTLED the working set is trimmed and
        the particles are fewer, and at CRITICAL_RECOVERY the compaction is
        aggressive. Below that the machine is not short of memory and the
        compaction would only cost time.
        """
        return state.level in ("THROTTLED", "CRITICAL_RECOVERY")

    def _maybe_purge(self) -> bool:
        """Compact the working set, but not more often than it is worth.

        The compaction throws the pages of this process away and they come
        back on the next touch, so doing it every second would cost more
        than it gives. Once every ten seconds is the compromise.
        """
        now = time.monotonic()
        if now - self._last_purge < _PURGE_MIN_INTERVAL_S:
            return False
        if not self.purge_working_set():
            return False
        self._last_purge = now
        return True

    # -- the thread ----------------------------------------------------------

    def start(self) -> None:
        """Run the regulator on its own daemon thread, publishing at 1 Hz.

        Starting a regulator that is already running does nothing.
        """
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="teemo-regulator", daemon=True
            )
            thread = self._thread
        thread.start()

    def stop(self) -> None:
        """Stop the thread of the regulator.

        It can be called as many times as wanted: stopping a regulator that
        is not running is not an error.
        """
        with self._lock:
            thread = self._thread
            self._thread = None
        self._stop.set()
        if thread is not None and thread.is_alive():
            if thread is not threading.current_thread():
                thread.join(timeout=2.0)

    def _run(self) -> None:
        """The loop of the thread: measure, publish, wait, repeat."""
        while not self._stop.is_set():
            try:
                self.publish()
            except Exception:
                pass   # the watchdog does not die because of one bad reading
            if self._stop.wait(self.interval_s):
                break


# ── the one regulator of the process ─────────────────────────────────────────
#
# The plan says the regulator is "the source of truth of the free VRAM that
# phase 7 needs", so there has to be ONE regulator: two of them would read
# the machine twice a second and the router could be answered by the one
# that is not running.

_REGULATOR: "HardwareRegulator | None" = None
_REGULATOR_LOCK = threading.Lock()


def regulator() -> "HardwareRegulator":
    """The regulator of this process, built the first time it is asked for."""
    global _REGULATOR
    with _REGULATOR_LOCK:
        if _REGULATOR is None:
            _REGULATOR = HardwareRegulator()
        return _REGULATOR


def free_vram_mb() -> float:
    """The free VRAM of the last reading, or -1.0 when nobody knows.

    -1.0 means "this machine does not say", and it is not the same as 0:
    0 MB of VRAM is a card with no room, -1 is a machine with no card. The
    router asks for what it knows and falls back to its own reading when
    the regulator has none.
    """
    try:
        state = regulator().last_state
    except Exception:
        return -1.0
    if state is None:
        return -1.0
    try:
        return float(state.vram_free_mb)
    except Exception:
        return -1.0
