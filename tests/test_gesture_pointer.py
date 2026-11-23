"""
tests/test_gesture_pointer.py — the hands, measured instead of assumed.

WHAT IS TESTED
    1. THE POINTER DOES NOT SHAKE. A hand that stands still while the
       model reports it a few pixels away every time must move the
       pointer by a fraction of that. This is the "Parkinson" the owner
       reported.
    2. THE POINTER IS NOT LATE. A hand that walks at a known speed must
       be followed without a delay that a person can feel.
    3. THE POINTER IS WALKED, NOT JUMPED. The camera gives fifteen
       pictures a second; the cursor is written sixty times a second and
       walks towards the point the hand is aiming at.
    4. THE PINCH DOES NOT CHATTER. A pinch that trembles around the
       threshold must press once and release once, not twenty times.
    5. THE SELECTION WORKS. A quick pinch is a click, two quick pinches
       are a double click, a pinch held still is the right button — and
       NOT a left click as well.
    6. ONE HAND IS A WHOLE MOUSE. The fingers that are not pinching are
       the modifier keys, and the middle button is a gesture of its own.
    7. A RESTING HAND IS NOT A PINCH. A fist is not a click.
    8. THE LINE OF THE PINCH IS MEASURED ON A REAL HAND. The thresholds
       are proved against the canonical hand and against the numbers
       that came out of a real camera.

THE HAND OF THE TESTS
    It is the canonical hand of `core/hand_geometry.py`, the same one
    the product measures itself against. The tests used to draw a hand
    with the fingers spread wide apart, and the thresholds were drawn
    on that drawing: a real pinch fell below the line and was read as a
    fist.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.pointer_filter import PointerSmoother  # noqa: E402
from gesture_hands import FakeDriver, mano, servicio  # noqa: E402


# ── 1. the pointer does not shake ────────────────────────────────────────────

def test_a_still_hand_does_not_move_the_pointer():
    """The jitter of the pointer, with the noise a real model makes."""
    import random

    azar = random.Random(11)
    suavizador = PointerSmoother()
    salida = []
    for _ in range(90):
        x = 960.0 + azar.gauss(0.0, 8.0)      # 8 px of noise, as MediaPipe does
        salida.append(suavizador(x, 540.0, 1.0 / 15.0)[0])
    crudo = 8.0
    filtrado = statistics_stdev(salida[20:])
    assert filtrado < crudo / 3.0, (
        f"the pointer still moves {filtrado:.2f} px with a hand that is "
        f"not moving (it was {crudo:.2f} px before the filter)")


def statistics_stdev(values) -> float:
    values = list(values)
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def test_the_pointer_is_not_late():
    """A hand that walks is followed without a delay anybody can feel."""
    suavizador = PointerSmoother()
    retardos = []
    x = 200.0
    for _ in range(60):
        x += 600.0 / 15.0
        fx = suavizador(x, 540.0, 1.0 / 15.0)[0]
        if x > 300:
            retardos.append((x - fx) / 600.0 * 1000.0)
    medio = sum(retardos) / len(retardos)
    assert abs(medio) < 40.0, (
        f"the pointer is {medio:.0f} ms away from the hand")


def test_a_hand_that_comes_back_does_not_jump_the_pointer():
    """The filter forgets the hand that was there before."""
    suavizador = PointerSmoother()
    for _ in range(30):
        suavizador(100.0, 100.0, 1.0 / 15.0)
    suavizador.reset()
    x, _y = suavizador(900.0, 500.0, 1.0 / 15.0)
    assert x == 900.0, "the pointer jumped across the screen when the hand came back"


# ── 2. the line of the pinch, measured on a real hand ───────────────────────

def test_the_canonical_hand_says_where_the_pinch_ends():
    """THE THREE NUMBERS OF A HAND.

    The reach is how far the tip of the index is from the knuckle of the
    middle finger, in sizes of the hand. An open hand reaches far, a
    pinch reaches half, a fist reaches hardly anything: the index is
    folded onto the palm and its tip is right next to that knuckle.

    The line of the product has to sit between the pinch and the fist,
    and it used to be drawn on the open hand, which put it above the
    pinch. That is why a real pinch was read as a fist.
    """
    from gui.gesture import PINCH_REACH

    abierta = mano("open")
    pellizco = mano("pinch")
    puno = mano("fist")

    assert abierta.index_reach > pellizco.index_reach > puno.index_reach, (
        f"the reach does not tell the three apart: open {abierta.index_reach:.2f}, "
        f"pinch {pellizco.index_reach:.2f}, fist {puno.index_reach:.2f}")
    assert pellizco.index_reach > PINCH_REACH, (
        f"a real pinch reaches {pellizco.index_reach:.2f} and the line is at "
        f"{PINCH_REACH:.2f}: the pinch would fall below the line and be read "
        f"as a fist, which is exactly what the user reported")
    assert puno.index_reach < PINCH_REACH, (
        f"a fist reaches {puno.index_reach:.2f} and the line is at "
        f"{PINCH_REACH:.2f}: the resting hand would press the button")


def test_a_pinch_is_a_pinch_and_a_fist_is_not():
    abierta = mano("open")
    pellizco = mano("pinch")
    puno = mano("fist")
    assert pellizco.pinched is True, "a real pinch was not read as a pinch"
    assert abierta.pinched is False, "an open hand was read as a pinch"
    assert puno.pinched is False, "a fist was read as a pinch"


def test_the_line_of_the_pinch_is_where_the_real_hand_says():
    """THE NUMBERS THAT CAME OUT OF A REAL CAMERA.

    These are not made up: they are the reach that the tracker reported
    for the hand of the user, measured over nine hundred pictures
    (`instrumentos/manos_prueba.py`, `instrumentos/linea_pellizco.py`).

    The lowest reach of a real pinch was 0.42 and the highest reach of a
    real fist that looked closed was 0.40. The line of the product is
    at 0.40, so every real pinch is read and no real fist is mistaken
    for one. With the line that was there before, at 0.85, one hundred
    and nineteen of the hundred and fifty-four real pinches were
    missed: the hand was read as a fist, in front of the user.
    """
    from gui.gesture import PINCH_REACH

    alcance_del_pellizco_mas_bajo = 0.42
    alcance_del_puno_mas_alto = 0.40
    assert PINCH_REACH <= alcance_del_pellizco_mas_bajo, (
        f"the line is at {PINCH_REACH} and a real pinch reached "
        f"{alcance_del_pellizco_mas_bajo}: that pinch is lost")
    assert PINCH_REACH >= alcance_del_puno_mas_alto, (
        f"the line is at {PINCH_REACH} and a real fist reached "
        f"{alcance_del_puno_mas_alto}: that fist is read as a click")


# ── 3. the pinch does not chatter ────────────────────────────────────────────

def test_a_trembling_pinch_presses_once_and_releases_once():
    """THE THRESHOLD WITH NO HYSTERESIS.

    The fingers close a little past the line and open a little past it,
    and the button went down and came up and went down again, several
    times a second. The user saw a cursor that moved like it had
    Parkinson's and could not select anything.

    The fingers here tremble right around the line: a closeness of 0.79
    and then of 0.69. With one threshold that is a button that chatters.
    With two, the button goes down once and stays down until the
    fingers really open.
    """
    servicio_ = servicio()
    for i in range(24):
        closeness = 0.79 if i % 2 == 0 else 0.69
        servicio_._steer([mano("pinch", closeness=closeness)])
    kinds = servicio_._driver.kinds()
    assert kinds.count("press") == 1, (
        f"the button was pressed {kinds.count('press')} times in a trembling "
        f"pinch: {kinds}")
    assert kinds.count("release") == 0, (
        f"the button was let go {kinds.count('release')} times in a trembling "
        f"pinch, and every one of them was a click that landed somewhere "
        f"else: {kinds}")


def test_a_pinch_that_opens_wide_releases_the_button():
    servicio_ = servicio()
    servicio_._steer([mano("pinch")])
    assert servicio_._driver.kinds().count("press") == 1
    # The fingers open: the pinch is over.
    for _ in range(4):
        servicio_._steer([mano("pinch", closeness=0.30)])
    assert servicio_._driver.kinds().count("release") == 1, \
        "the button was not let go when the fingers opened"


# ── 4. the selection works ───────────────────────────────────────────────────

def test_a_quick_pinch_is_one_click():
    servicio_ = servicio()
    servicio_._steer([mano("pinch")])
    for _ in range(3):
        servicio_._steer([mano("pinch", closeness=0.30)])
    kinds = servicio_._driver.kinds()
    assert kinds.count("press") == 1
    assert kinds.count("release") == 1
    assert "click" not in kinds, "the click was sent twice"


def test_two_quick_pinches_are_a_double_click():
    servicio_ = servicio()
    # The first pinch.
    servicio_._steer([mano("pinch")])
    for _ in range(3):
        servicio_._steer([mano("pinch", closeness=0.30)])
    # The second one, right away.
    servicio_._steer([mano("pinch")])
    for _ in range(3):
        servicio_._steer([mano("pinch", closeness=0.30)])
    kinds = servicio_._driver.kinds()
    assert "double_click" in kinds, (
        f"two quick pinches were not a double click: {kinds}")


def test_a_pinch_held_still_is_the_right_button_and_not_a_click():
    """ONE GESTURE, ONE ACTION.

    It used to be a left click AND a right click: the thing was
    selected and its menu was opened at the same time.
    """
    servicio_ = servicio()
    servicio_._steer([mano("pinch")])
    # It is held for longer than the long press.
    servicio_._press_time -= 1.0
    servicio_._steer([mano("pinch")])
    for _ in range(3):
        servicio_._steer([mano("pinch", closeness=0.30)])
    kinds = servicio_._driver.kinds()
    assert "press_right" in kinds, f"the right button never went down: {kinds}"
    assert "release_right" in kinds, f"the right button never came up: {kinds}"
    assert "click" not in kinds, (
        f"a long press was also a click, and it selected the thing: {kinds}")


def test_a_pinch_that_moves_is_a_drag_and_not_a_click():
    servicio_ = servicio()
    servicio_._steer([mano("pinch")])
    for i in range(6):
        servicio_._steer([mano("pinch", aim=(0.5 + 0.02 * i, 0.5))])
    for _ in range(3):
        servicio_._steer([mano("pinch", closeness=0.30)])
    kinds = servicio_._driver.kinds()
    assert "click" not in kinds, f"a drag was also a click: {kinds}"
    assert kinds.count("press") == 1 and kinds.count("release") == 1


def test_the_click_lands_where_the_hand_is_aiming():
    """THE CLICK LANDS ON THE POINT, NOT ON THE WAY TO IT.

    The pointer walks towards the point the hand is aiming at, and the
    click has to fall on that point and not on the place the pointer
    was passing through.
    """
    servicio_ = servicio()
    servicio_._steer([mano("pinch", aim=(0.25, 0.75))])
    for _ in range(3):
        servicio_._steer([mano("pinch", aim=(0.25, 0.75), closeness=0.30)])
    movimientos = [c for c in servicio_._driver.calls if c[0] == "move"]
    assert movimientos, "the pointer never moved"
    ultimo = movimientos[-1]
    objetivo = servicio_._target
    assert (ultimo[1], ultimo[2]) == objetivo, (
        f"the click landed on {(ultimo[1], ultimo[2])} and the hand was "
        f"aiming at {objetivo}")


# ── 5. the pointer is walked, not jumped ─────────────────────────────────────

def test_the_pointer_walks_to_the_point_instead_of_jumping(monkeypatch):
    """THE SERVO.

    The camera gives fifteen pictures a second. A pointer that is
    written fifteen times a second is seen fifteen times a second: it
    hops, and the hand feels like it is dragging something sticky. The
    pointer is walked sixty times a second towards the point the hand is
    aiming at.
    """
    reloj = {"t": 1000.0}
    monkeypatch.setattr("gui.gesture.time.monotonic", lambda: reloj["t"])
    servicio_ = servicio()
    servicio_._pointer_at = (100, 100)
    servicio_._driver._pos = (100, 100)
    servicio_._target = (900, 500)

    reloj["t"] += 0.016
    servicio_._servo_step()
    primer_paso = servicio_._pointer_at
    assert primer_paso != (900, 500), (
        "the pointer jumped to the point instead of walking to it")
    assert primer_paso != (100, 100), "the pointer did not move at all"

    for _ in range(60):
        reloj["t"] += 0.016
        servicio_._servo_step()
    dx = abs(servicio_._pointer_at[0] - 900)
    dy = abs(servicio_._pointer_at[1] - 500)
    assert dx <= 2 and dy <= 2, (
        f"the pointer did not arrive: it is {dx} px away in x and {dy} in y")


def test_the_mouse_of_the_user_always_wins(monkeypatch):
    """THE HAND AND THE MOUSE ARE THE SAME CURSOR.

    When the user moves the mouse, the hand lets go for a few seconds.
    Without that, the two fought over the cursor and the screenshots of
    Windows could not be taken, because the selection kept running away
    from the mouse.
    """
    reloj = {"t": 1000.0}
    monkeypatch.setattr("gui.gesture.time.monotonic", lambda: reloj["t"])
    servicio_ = servicio()
    servicio_._pointer_at = (100, 100)
    servicio_._driver._pos = (100, 100)
    servicio_._target = (900, 500)

    # The user takes the mouse and moves it.
    reloj["t"] += 0.016
    servicio_._driver._pos = (400, 400)
    servicio_._servo_step()
    assert servicio_._pointer_at is None, (
        "the hand kept the cursor although the user had moved the mouse")
    assert servicio_._mouse_at == (400, 400), (
        "the hand did not remember where the mouse was")

    # For four seconds the hand does not touch the cursor.
    for _ in range(100):
        reloj["t"] += 0.016
        servicio_._driver._pos = (400, 400)
        servicio_._servo_step()
    assert servicio_._driver._pos == (400, 400), \
        "the hand moved the cursor while the user was using it"

    # And when it takes over, it starts from where the mouse is: it
    # walks from there, it does not jump to the point of the hand.
    reloj["t"] += 5.0
    servicio_._driver._pos = (400, 400)
    servicio_._servo_step()
    x, y = servicio_._pointer_at
    assert 400 < x < 900 and 400 < y < 500, (
        f"the hand took the cursor over from {servicio_._pointer_at} instead "
        f"of from where the mouse was")


# ── 6. one hand is a whole mouse ─────────────────────────────────────────────

def test_the_fingers_that_are_not_pinching_are_the_modifiers():
    """A pinch with the middle finger up is a click with the shift down."""
    from gui.gesture import PointerDriver

    servicio_ = servicio()
    servicio_._steer([mano("pinch", out=(False, True, False, False))])
    assert PointerDriver._VK_SHIFT in servicio_._driver.keys, \
        "the middle finger did not hold the shift"
    for _ in range(3):
        servicio_._steer([mano("pinch", out=(False, True, False, False),
                               closeness=0.30)])
    assert PointerDriver._VK_SHIFT not in servicio_._driver.keys, \
        "the shift was left down after the pinch"


def test_the_ring_finger_holds_the_control():
    from gui.gesture import PointerDriver

    servicio_ = servicio()
    servicio_._steer([mano("pinch", out=(False, False, True, False))])
    assert PointerDriver._VK_CONTROL in servicio_._driver.keys


def test_the_little_finger_holds_the_alt():
    from gui.gesture import PointerDriver

    servicio_ = servicio()
    servicio_._steer([mano("pinch", out=(False, False, False, True))])
    assert PointerDriver._VK_MENU in servicio_._driver.keys


def test_all_three_fingers_are_the_middle_button():
    servicio_ = servicio()
    servicio_._steer([mano("pinch", out=(False, True, True, True))])
    kinds = servicio_._driver.kinds()
    assert "middle_click" in kinds, f"the middle button was not pressed: {kinds}"
    assert "press" not in kinds, \
        f"the middle button was also a left click: {kinds}"
    assert not servicio_._driver.keys, \
        "a modifier was left down after the middle button"


# ── 7. a resting hand is not a pinch ─────────────────────────────────────────

def test_a_fist_is_not_a_pinch():
    """THE HAND THAT IS RESTING IS NOT A CLICK.

    The thumb of a fist is touching the index, and the fingers of a fist
    are close together: without a third measurement a resting hand held
    the mouse button down.
    """
    puno = mano("fist")
    assert puno.pinched is False, (
        "a fist was read as a pinch: the index is folded and the thumb is "
        "next to it, and that is not a button")

    servicio_ = servicio()
    for _ in range(10):
        servicio_._steer([mano("fist")])
    assert servicio_._driver.kinds().count("press") == 0, \
        "a resting hand pressed the mouse button"


def test_the_pinch_needs_the_index_in_the_air():
    """The third measurement of the pinch: where the index is."""
    from gui.gesture import PINCH_ON, PINCH_REACH

    pellizco = mano("pinch")
    assert pellizco.pinched is True
    assert pellizco.index_reach > PINCH_REACH
    assert pellizco.closeness > PINCH_ON


# ── 8. the wheel is smooth ───────────────────────────────────────────────────

def test_the_wheel_turns_by_notches_and_not_by_jumps():
    """THE WHEEL ACCUMULATES.

    It used to send the whole travel of the fingers in one notch, so a
    hand that moved a little turned the page a lot. Now the travel is
    measured in notches of ninety pixels and the leftovers are kept, so
    a slow hand turns the wheel smoothly.
    """
    from gui.gesture import GestureService

    servicio_ = servicio()
    servicio_._screen_w, servicio_._screen_h = 1920, 1080
    # The fingers travel a little: less than a notch.
    servicio_._steer([mano("two")])
    servicio_._steer([mano("two", dy=-0.03)])
    assert "scroll" not in servicio_._driver.kinds(), (
        "the wheel turned with a movement too small to be a notch")

    # And now they travel far enough, with the time between them.
    import time
    time.sleep(0.06)
    servicio_._steer([mano("two", dy=-0.20)])
    scrolls = [c for c in servicio_._driver.calls if c[0] == "scroll"]
    assert scrolls, "the wheel did not turn"
    assert all(abs(c[1]) <= 3 for c in scrolls), (
        f"the wheel jumped: {scrolls}")


# ── 9. two hands ─────────────────────────────────────────────────────────────

def test_two_hands_that_pinching_are_a_zoom_and_not_a_click():
    """TWO HANDS, TWO ROLES.

    Two hands that pinch together and move apart are the zoom, and they
    are NOT a click: a zoom is a gesture of two hands, and a click that
    happens at the same time is a click in the wrong place.
    """
    import time

    servicio_ = servicio()
    servicio_._steer([mano("pinch", dx=-0.15), mano("pinch", dx=0.15)])
    time.sleep(0.10)
    servicio_._steer([mano("pinch", dx=-0.30), mano("pinch", dx=0.30)])
    kinds = servicio_._driver.kinds()
    assert "zoom" in kinds, f"two hands pinching did not zoom: {kinds}"
    assert "press" not in kinds, (
        f"two hands pinching was also a click: {kinds}")


def test_the_pointer_stays_with_the_hand_it_was_following():
    """THE POINTER DOES NOT CHANGE HANDS BY ITSELF.

    The tracker does not always report the hands in the same order, and
    a pointer that follows "the first one" is a pointer that jumps from
    one side of the screen to the other.
    """
    servicio_ = servicio()
    a = mano("open", aim=(0.30, 0.50))
    b = mano("open", aim=(0.70, 0.50))
    servicio_._steer([a, b])
    primero = servicio_._target
    # The tracker reports them the other way round: the pointer must not
    # change hands by itself.
    servicio_._steer([b, a])
    segundo = servicio_._target
    assert abs(segundo[0] - primero[0]) <= 2 and abs(segundo[1] - primero[1]) <= 2, (
        f"the pointer changed hands by itself: it was at {primero} and it is "
        f"now at {segundo}")


def test_the_pointer_takes_the_hand_the_user_pinches_with():
    """A PINCH IS A DELIBERATE CHANGE OF HANDS.

    The pointer stays with the hand it was following, and it only
    changes hands when the user pinches with the other one, because
    that is a thing the user does on purpose.
    """
    servicio_ = servicio()
    a = mano("open", aim=(0.30, 0.50))
    b = mano("open", aim=(0.70, 0.50))
    servicio_._steer([a, b])
    primero = servicio_._target
    # The other hand pinches: the pointer goes with it.
    servicio_._steer([a, mano("pinch", aim=(0.70, 0.50))])
    segundo = servicio_._target
    assert abs(segundo[0] - primero[0]) > 2, (
        f"the pointer did not follow the hand that pinched: it stayed at "
        f"{primero}")
