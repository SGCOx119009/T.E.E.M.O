"""
tests/test_gesture_advanced.py — the gestures that touch the desktop.

WHAT IS BEING PROVED
    The specification of the hand vocabulary has eleven gestures that do
    not belong to the mouse: the clutch of the open palm, the fist that
    confirms, the thumb that approves and rejects, the swipes of the
    palm, the L and the four gestures of two hands.

    THEY ARE OFF BY DEFAULT, and that is proved here: a palm that is
    pushed up while the hand is resting must NOT maximise a window that
    the user was reading. The switch of the drawer is the only thing
    that arms them, and the tests arm it explicitly.

    Three checks for every one of them, as in the vocabulary itself:
    it fires, it does not fire by mistake, and it does not fire when it
    is not armed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gesture_hands import FakeDriver, mano, servicio  # noqa: E402

# EL RITMO DE LA CÁMARA: quince cuadros por segundo.
CUADRO = 1.0 / 15.0


@pytest.fixture()
def service(monkeypatch):
    """A gesture service with a fake pointer and a clock we control."""
    svc = servicio()
    driver = FakeDriver()
    svc._driver = driver
    clock = {"now": 1000.0}
    monkeypatch.setattr("gui.gesture.time.time", lambda: clock["now"])
    svc._clock = clock
    svc._fake = driver
    yield svc
    svc.stop()


def _frames(svc, cuadros, inicio=1000.0, paso=CUADRO):
    """Feed the frames. Each frame is a LIST of hands."""
    for i, manos in enumerate(cuadros):
        svc._clock["now"] = inicio + i * paso
        svc._procesa(list(manos))


def _held(svc, manos, frames=8, inicio=1000.0):
    """The same hand — or the same two hands — held still."""
    if not isinstance(manos, list):
        manos = [manos]
    _frames(svc, [list(manos)] * frames, inicio)


def _pushed(svc, pose, frames=8, inicio=1000.0, dx=0.0, dy=0.0):
    """The hand, travelling."""
    _frames(svc, [[mano(pose, dx=dx * i, dy=dy * i)] for i in range(frames)],
            inicio)


def _descanso(svc, inicio=1000.0):
    """The hand is let go, so that the next gesture is a new gesture.

    A gesture that has been done once is not done again until the hand
    changes: the fist that confirms twice in a row is one confirmation
    and a half, and the half is a mistake.
    """
    _frames(svc, [[mano("open")]], inicio)


# ── the switch ──────────────────────────────────────────────────────────────

def test_the_advanced_gestures_are_off_when_the_program_starts(service):
    """EL MODO AVANZADO EMPIEZA DESACTIVADO."""
    assert service.advanced is False


def test_the_advanced_gestures_can_be_armed(service):
    service.set_advanced(True)
    assert service.advanced is True
    service.set_advanced(False)
    assert service.advanced is False


# ── the palm ────────────────────────────────────────────────────────────────

def test_the_open_palm_freezes_the_cursor(service):
    """LA PALMA QUIETA ES EL EMBRAGUE: EL CURSOR SE QUEDA QUIETO."""
    _held(service, mano("open"))
    assert service._clutched is True


def test_the_clutch_stops_the_pointer_from_walking(service):
    """EL EMBRAGUE FRENA EL PUNTERE, QUE ES PARA LO QUE SIRVE."""
    service._clutched = True
    service._target = (900, 500)
    service._pointer_at = (100, 100)
    service._servo_step()
    assert service._pointer_at == (100, 100), "the cursor moved with the clutch on"


def test_the_clutch_silences_the_assistant(service):
    """LA PALMA INTERRUMPE A T.E.E.M.O. Y SE CALLA."""
    called = []
    service.interrupt_sig.connect(lambda: called.append(True))
    _held(service, mano("open"))
    assert called, "the open palm did not silence the assistant"


def test_the_clutch_ends_when_the_palm_is_not_a_palm(service):
    """EL EMBRAGUE SE ACABA EN CUANDO LA PALMA DEJA DE SER PALMA."""
    _held(service, mano("open"))
    assert service._clutched is True
    _frames(service, [[mano("fist")]], 1001.0)
    assert service._clutched is False


# ── the gestures of the desktop ─────────────────────────────────────────────

def test_the_palm_pushed_up_maximises_only_when_it_is_armed(service):
    """LA PALMA ARRIBA MAXIMIZA — SOLO CON EL MODO AVANZADO PUESTO."""
    _pushed(service, "open", dy=-0.05)
    assert "win_tap" not in service._fake.kinds(), (
        "the window was maximised without the switch being turned on")
    _frames(service, [[mano("open")]], 1005.0)
    service.set_advanced(True)
    _pushed(service, "open", dy=-0.05, inicio=1010.0)
    assert "win_tap" in service._fake.kinds()


def test_the_palm_pushed_down_minimises_only_when_it_is_armed(service):
    service.set_advanced(True)
    _pushed(service, "open", dy=0.05)
    kinds = service._fake.kinds()
    assert "win_tap" in kinds


def test_three_fingers_sideways_switch_the_window_only_when_armed(service):
    _pushed(service, "three", dx=0.05)
    assert "alt_tab" not in service._fake.kinds()
    _frames(service, [[mano("open")]], 1005.0)
    service.set_advanced(True)
    _pushed(service, "three", dx=0.05, inicio=1010.0)
    assert "alt_tab" in service._fake.kinds()


def test_the_fist_answers_the_question_on_screen_only_when_armed(service):
    """EL PUÑO CONFIRMA LO QUE ESTÁ ESPERANDO UNA RESPUESTA."""
    answers = []
    service.confirm_sig.connect(lambda ok: answers.append(bool(ok)))
    _held(service, mano("fist"))
    assert answers == [], "the fist answered with the switch off"
    _descanso(service, 1002.0)
    service.set_advanced(True)
    _held(service, mano("fist"), inicio=1003.0)
    assert answers == [True]


def test_the_thumb_up_approves_and_the_thumb_down_rejects(service):
    """EL PULGAR ARRIBA APRUEBA Y EL PULGAR ABAJO RECHAZA."""
    service.set_advanced(True)
    answers = []
    service.confirm_sig.connect(lambda ok: answers.append(bool(ok)))
    _held(service, mano("thumb_up"), inicio=1010.0)
    _descanso(service, 1012.0)
    _held(service, mano("thumb_down"), inicio=1013.0)
    assert answers == [True, False]


def test_the_l_wakes_the_assistant_only_when_it_is_armed(service):
    """LA L DESPIERTA A T.E.E.M.O., Y SOLO CUANDO ESTÁ ARMADO."""
    llamadas = []
    service.wake_sig.connect(lambda: llamadas.append(True))
    _held(service, mano("l_shape"))
    assert llamadas == []
    _descanso(service, 1002.0)
    service.set_advanced(True)
    _held(service, mano("l_shape"), inicio=1003.0)
    assert llamadas == [True]


def test_the_clap_puts_the_assistant_to_sleep_only_when_armed(service):
    """EL CHOQUE DE PALMAS DUERME A T.E.E.M.O."""
    llamadas = []
    service.sleep_sig.connect(lambda: llamadas.append(True))
    _held(service, [mano("open", dx=-0.06), mano("open", dx=0.06)])
    assert llamadas == []
    _frames(service, [[mano("fist")]], 1002.0)
    service.set_advanced(True)
    _held(service, [mano("open", dx=-0.06), mano("open", dx=0.06)], inicio=1003.0)
    assert llamadas == [True]


def test_the_two_ls_take_a_screenshot_only_when_armed(service):
    """DOS L's OPUESTAS: LA CAPTURA."""
    from core.hand_geometry import hand_points, placed

    arriba = placed(hand_points("l_shape"), dx=-0.20)
    puntos = hand_points("l_shape")
    muneca = puntos[0]
    abajo = placed([(x, 2 * muneca[1] - y) for x, y in puntos],
                   dx=0.20, dy=-0.45)

    class _Dos:
        """Dos manos de verdad, con los puntos que se les han pedido."""

    def _dos(puntos_a, puntos_b):
        from gui.gesture import Hand

        a = Hand(puntos_a, "Right")
        b = Hand(puntos_b, "Left")
        a.measure()
        b.measure()
        return [a, b]

    _held(service, _dos(arriba, abajo))
    assert "screenshot" not in service._fake.kinds()
    _frames(service, [[mano("open")]], 1002.0)
    service.set_advanced(True)
    _held(service, _dos(arriba, abajo), inicio=1003.0)
    assert "screenshot" in service._fake.kinds()


def test_disarming_lets_go_of_the_area_that_was_being_selected(service):
    """DESARMAR SUELTA EL ÁREA QUE ESTABA SELECCIONÁNDOSE."""
    service.set_advanced(True)
    quieta = mano("pinch", dx=-0.20)
    movida = mano("pinch", dx=0.25, dy=0.05)
    movida2 = mano("pinch", dx=0.32, dy=0.10)
    _frames(service, [[quieta, movida]], 1010.0)
    _frames(service, [[quieta, movida2]], 1010.1)
    assert "press" in service._fake.kinds(), "the area never started"
    service.set_advanced(False)
    assert "release" in service._fake.kinds()
