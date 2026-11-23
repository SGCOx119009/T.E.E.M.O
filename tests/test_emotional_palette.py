"""
tests/test_emotional_palette.py — the colour of the interface travels.

WHAT IS BEING PROVED
    1. `apply_ui_accent()` still does what it always did: a hue shift that
       keeps the design of the interface, and a `True` for a colour that is
       a colour.
    2. `apply_ui_accent(..., exact=True)` ALSO takes the saturation and the
       value of the target, with the ratios of the family kept.
    3. `animate_accent_to()` works WITHOUT a QApplication (headless): the
       final colour is applied and nothing raises.
    4. `animate_accent_to()` is CONTINUOUS: the accent is walked to the
       target, frame by frame, and it never jumps.
    5. `current_accent()` and `accent_target()` give back valid hex colours.

A NOTE ON THE EXACT MODE AND THE CHOICE OF THE COLOUR
    The exact mode takes the saturation and the value of the TARGET and
    applies them to the family with the family's own ratios kept. The
    default accent of the interface ("#00d4ff") is already fully saturated
    and fully bright, so a target that is as well — "#ff8800" is — cannot
    show the difference: the scale factor is 1.0 and the two modes agree.
    The difference is therefore proved with a DULL target, the colour of the
    SKEPTICAL state, and the "#ff8800" case is proved for what it really
    says: that the hue of the whole family follows the target.
"""
from __future__ import annotations

import colorsys
import os
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def palette_is_restored():
    """Every test leaves the palette where it found it.

    The palette lives on the class `C`, and the class outlives the test: a
    test that left the interface in another colour would tint the tests that
    come after it."""
    from gui import theme

    before = theme.current_palette()
    yield
    theme.apply_ui_accent("#00d4ff")
    for key, value in before.items():
        setattr(theme.C, key, value)
    with theme._accent_lock:
        theme._accent_current = before["PRI"]
        theme._accent_target = before["PRI"]


def _hsv(value: str) -> tuple[float, float, float]:
    text = value.lstrip("#")
    r, g, b = (int(text[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return colorsys.rgb_to_hsv(r, g, b)


def _rgb(value: str) -> tuple[int, int, int]:
    text = value.lstrip("#")
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


def _valid_hex(value: str) -> bool:
    text = str(value or "")
    if not (text.startswith("#") and len(text) == 7):
        return False
    try:
        int(text[1:], 16)
    except ValueError:
        return False
    return True


def _pump(app, seconds: float) -> None:
    """Let the animation run: the Qt clock only moves when events are
    processed."""
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.004)


# ── the old behaviour is still the old behaviour ─────────────────────────────

def test_apply_ui_accent_works_as_it_always_did():
    from gui import theme
    from gui.theme import C

    assert theme.apply_ui_accent("#ff8800") is True
    assert C.PRI != "#00d4ff"
    assert C.PRI == "#ff8800"
    # the family followed the hue, and nothing else about the design moved
    assert C.BG.startswith("#") and C.TEXT_DIM.startswith("#")
    assert C.PRI_DIM != C.PRI


def test_apply_ui_accent_refuses_a_colour_that_is_not_a_colour():
    from gui import theme

    before = theme.current_palette()
    for bad in ("", "nonsense", "#12345", "#gggggg", "ff8800", None):
        assert theme.apply_ui_accent(bad) is False
    assert theme.current_palette() == before


def test_the_exact_mode_takes_the_saturation_and_the_value_of_the_target():
    from gui import theme
    from gui.theme import C

    # A DULL target: the dull gold of the SKEPTICAL state. It is not fully
    # saturated and not fully bright, so the exact mode has something to do.
    theme.apply_ui_accent("#00d4ff")
    plain = theme.current_palette()
    theme.apply_ui_accent("#b89b3c")
    hue_only = theme.current_palette()
    theme.apply_ui_accent("#b89b3c", exact=True)
    exact = theme.current_palette()

    # the hue follows the target in both modes
    assert _hsv(hue_only["PRI"])[0] == pytest.approx(_hsv("#b89b3c")[0], abs=0.02)
    assert _hsv(exact["PRI"])[0] == pytest.approx(_hsv("#b89b3c")[0], abs=0.02)

    # and the exact mode ALSO takes the saturation and the value
    assert _hsv(exact["PRI"])[1] == pytest.approx(_hsv("#b89b3c")[1], abs=0.02)
    assert _hsv(exact["PRI"])[2] == pytest.approx(_hsv("#b89b3c")[2], abs=0.02)
    assert _hsv(hue_only["PRI"])[1] > _hsv(exact["PRI"])[1] + 0.1
    assert _hsv(hue_only["PRI"])[2] > _hsv(exact["PRI"])[2] + 0.1

    # the ratios of the family are kept: the dark plate is still dark
    # RELATIVE to the accent, in both modes
    assert (_hsv(plain["BG"])[2] / _hsv(plain["PRI"])[2]) == pytest.approx(
        _hsv(exact["BG"])[2] / _hsv(exact["PRI"])[2], rel=0.06)
    assert C.PRI == exact["PRI"]


# ── without a Qt application there is no clock, and no crash ────────────────

def test_animate_accent_to_without_a_qapplication_applies_the_final_colour():
    """A separate process: this one HAS a QApplication, and the promise is
    about a program that has none."""
    code = (
        "import sys;"
        f"sys.path.insert(0, r'{BASE}');"
        "from gui import theme;"
        "assert theme.QApplication.instance() is None;"
        "theme.animate_accent_to('#9d72ff');"
        "print(theme.current_accent());"
        "print(theme.accent_target());"
        "print(theme.C.PRI)"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    lines = [line.strip() for line in done.stdout.strip().splitlines() if line.strip()]
    assert lines, done.stdout
    assert lines[0] == "#9d72ff"
    assert lines[1] == "#9d72ff"
    assert lines[2] == "#9d72ff"


def test_animate_accent_to_ignores_a_target_that_is_not_a_colour(qt_app):
    from gui import theme

    theme.apply_ui_accent("#00d4ff")
    theme.animate_accent_to("not a colour")
    assert theme.current_accent() == "#00d4ff"


# ── the travel ───────────────────────────────────────────────────────────────
#
# The trip is checked with the CLOCK of the animation, not with the clock of
# the machine: `setCurrentTime` walks a QPropertyAnimation frame by frame,
# with no event loop and no waiting. A test that waits for real time is a
# test that fails when the machine is busy, and a test that fails when the
# machine is busy teaches everybody to ignore it.

def _walk(duration_ms: int, step_ms: int = 30, until_ms: int | None = None):
    """Every colour the accent takes on its way, in order."""
    from gui import theme

    seen = []
    anim = theme._accent_anim
    assert anim is not None, "the trip did not start"
    limit = duration_ms if until_ms is None else until_ms
    for moment in range(0, limit + 1, step_ms):
        anim.setCurrentTime(moment)
        seen.append(theme.current_accent())
    return seen


def test_the_accent_travels_and_lands_exactly_on_the_target(qt_app):
    from gui import theme

    theme.apply_ui_accent("#00d4ff")
    start = theme.current_accent()
    theme.animate_accent_to("#ff4422", duration_ms=1200)

    # the trip has just begun: the accent is neither where it was nor where
    # it is going
    seen = _walk(1200)
    midway = seen[len(seen) // 4]
    assert midway != start
    assert midway != "#ff4422"

    # the TARGET of the trip is the destination from the first moment, even
    # while the accent is still on its way
    assert theme.accent_target() == "#ff4422"

    # and it is BETWEEN the two: no channel may overshoot
    for a, b, c in zip(_rgb(start), _rgb(midway), _rgb("#ff4422")):
        lo, hi = sorted((a, c))
        assert lo <= b <= hi, f"the accent overshot on the way: {midway}"

    # the trip ends exactly on the target
    assert seen[-1] == "#ff4422"
    assert theme.current_accent() == "#ff4422"
    assert theme.accent_target() == "#ff4422"


def test_the_accent_never_jumps(qt_app):
    from gui import theme

    theme.apply_ui_accent("#00d4ff")
    theme.animate_accent_to("#ff4422", duration_ms=900)
    seen = _walk(900)

    assert len(set(seen)) > 5, "the accent did not move at all"
    for first, second in zip(seen, seen[1:]):
        for a, b in zip(_rgb(first), _rgb(second)):
            assert abs(a - b) < 60, f"the accent jumped: {first} -> {second}"


def test_a_new_trip_starts_from_where_the_last_one_got_to(qt_app):
    from gui import theme

    theme.apply_ui_accent("#00d4ff")
    theme.animate_accent_to("#ff4422", duration_ms=900)
    # the trip is still on its way: a third of the way, and no more
    seen = _walk(900, step_ms=60, until_ms=300)
    where = seen[-1]
    assert where not in ("#00d4ff", "#ff4422")

    # a new destination, mid-travel: the colour must not jump back to the
    # beginning, and must not jump to the new destination either
    theme.animate_accent_to("#9d72ff", duration_ms=600)
    after = _walk(600, step_ms=30)[0]
    for a, b in zip(_rgb(where), _rgb(after)):
        assert abs(a - b) < 60, f"the accent jumped: {where} -> {after}"

    assert theme.current_accent() == "#9d72ff"


def test_current_accent_and_accent_target_are_valid_hex(qt_app):
    from gui import theme

    assert _valid_hex(theme.current_accent())
    assert _valid_hex(theme.accent_target())
    theme.apply_ui_accent("#00d4ff")
    assert _valid_hex(theme.current_accent())
    assert theme.current_accent() == theme.accent_target()


# ── the HUD listens to the state ────────────────────────────────────────────

def test_the_hud_takes_the_state_and_plays_its_animation(qt_app):
    from gui.hud import HudCanvas

    hud = HudCanvas("")
    hud.resize(400, 300)
    try:
        for name, animation in (("MOTIVATIONAL", "pulse"),
                                 ("EMPATHETIC", "halo"),
                                 ("CHALLENGING", "grid_tilt"),
                                 ("WITTY", "stutter"),
                                 ("SOLEMN", "still")):
            hud.set_emotional_state(name)
            assert hud.emotional_animation() == animation
    finally:
        hud.close()


def test_the_hud_frame_interval_follows_the_regulator(qt_app):
    from core.hardware_equilibrium import ResourceState
    from gui.hud import HudCanvas

    hud = HudCanvas("")
    try:
        hud.set_frame_interval(33)
        assert hud.frame_interval() == 33
        # a value that is not one is ignored, and nothing raises
        for bad in (0, -5, "abc", None):
            hud.set_frame_interval(bad)
        assert hud.frame_interval() == 33

        state = ResourceState(level="THROTTLED", load_percent=80.0, cpu_percent=80.0,
                              ram_percent=78.0, gpu_percent=-1.0, vram_free_mb=-1.0,
                              fps=30, frame_interval_ms=66, memory_purged=False, at=0.0)
        hud.apply_resource_state(state)
        assert hud.frame_interval() == 66
        assert hud.resource_level() == "THROTTLED"
    finally:
        hud.close()
