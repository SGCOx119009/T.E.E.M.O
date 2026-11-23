"""
tests/test_gesture_spec.py — the whole vocabulary, three validations each.

WHAT IS BEING PROVED
    The four categories of the hand vocabulary of T.E.E.M.O. — the
    navigation and the mouse, the states of control, the windows and the
    system, and the precision gestures of two hands — with THREE checks
    for every single gesture:

      1. IT FIRES. The hand in the pose makes the machine say the gesture.
      2. IT DOES NOT FIRE BY MISTAKE. The neighbour of that pose — the
         one a hand passes through on its way to something else — does
         NOT say it. A gesture that fires for its neighbour is a gesture
         that fires by accident.
      3. IT FIRES FROM FAR AWAY. The same hand, HALF as big in the picture
         because it is twice as far from the camera, says exactly the
         same thing. That is the normalisation by size: the numbers of
         the vocabulary are ratios, never pixels, and a vocabulary that
         breaks when the user moves their arm is not a vocabulary.

THE HAND
    The canonical hand of `core/hand_geometry.py`: the proportions of the
    hand that MediaPipe's model was trained on, in the twenty-one points
    the tracker returns.

THE CLOCK
    The camera gives fifteen pictures a second, so the pictures are
    fifteen hundredths of a second apart — and the gestures that are
    about TIME (the held pinch, the held palm, the push) only work if
    the test tells the truth about that rhythm.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.gesture_spec import GESTURES, GestureSpec  # noqa: E402
from core.hand_geometry import hand_points, placed  # noqa: E402

# EL RITMO DE LA CÁMARA: quince cuadros por segundo.
CUADRO = 1.0 / 15.0


# ── el mano de mentira ──────────────────────────────────────────────────────

class _Mano:
    """A hand, as the tracker reports it."""

    def __init__(self, points, handedness: str = "Right"):
        self.points = list(points)
        self.handedness = handedness


def _manos(points, handedness: str = "Right"):
    return [_Mano(points, handedness)]


def _dos_manos(primera, segunda):
    """One picture with two hands in it."""
    return [_Mano(primera), _Mano(segunda)]


def _pose(pose: str, dx: float = 0.0, dy: float = 0.0, scale: float = 1.0):
    """The canonical hand in a pose, somewhere, at some size."""
    return placed(hand_points(pose), dx=dx, dy=dy, scale=scale)


def _l_abajo():
    """The L, but turned over: the index pointing at the floor."""
    puntos = hand_points("l_shape")
    muneca = puntos[0]
    return [(x, 2 * muneca[1] - y) for x, y in puntos]


def _dos_ls(scale: float = 1.0):
    """The two opposite L's, at the size asked for.

    THE DISTANCES SCALE WITH THE HAND. A hand twice as far from the
    camera is half as big in the picture and half as far from the other
    one: the frame is the same frame, only smaller.
    """
    arriba = placed(hand_points("l_shape"), dx=-0.20 * scale, scale=scale)
    abajo = placed(_l_abajo(), dx=0.20 * scale, dy=-0.45 * scale, scale=scale)
    return arriba, abajo


def _pasa(spec, cuadros, dt: float = CUADRO, t0: float = 0.0) -> list[str]:
    """Feed the frames to the recogniser and collect what it said."""
    nombres: list[str] = []
    t = t0
    for manos in cuadros:
        for evento in spec.feed(manos, t):
            nombres.append(evento.name)
        t += dt
    return nombres


def _quietos(pose: str, n: int = 8, dx: float = 0.0, dy: float = 0.0,
             scale: float = 1.0):
    """The same pose, held still, frame after frame."""
    return [_manos(_pose(pose, dx, dy, scale)) for _ in range(n)]


def _moviendose(pose: str, n: int = 8, dx: float = 0.0, dy: float = 0.0,
                px: float = 0.0, py: float = 0.0, scale: float = 1.0):
    """The pose, travelling a step each picture.

    THE STEP IS A PART OF THE SIZE OF THE HAND: a hand twice as far
    from the camera covers half as much of the picture for the same
    movement of the arm. That is what makes the third validation of
    every gesture a real test and not a repetition.
    """
    cuadros = []
    for i in range(n):
        cuadros.append(_manos(_pose(pose, dx + px * i * scale,
                                    dy + py * i * scale, scale)))
    return cuadros


def _pellizco(frames: int = 3, dx: float = 0.0, dy: float = 0.0,
              scale: float = 1.0):
    """The fingers closing, and held."""
    return [_manos(_pose("pinch", dx, dy, scale)) for _ in range(frames)]


def _suelta(n: int = 1, dx: float = 0.0, dy: float = 0.0, scale: float = 1.0):
    """The fingers opening again."""
    return [_manos(_pose("open", dx, dy, scale)) for _ in range(n)]


# ── 1. NAVEGACIÓN Y RATÓN ───────────────────────────────────────────────────

class TestNavegacion:
    """One hand: the cursor and its buttons."""

    def test_point_move_fires(self):
        spec = GestureSpec()
        nombres = _pasa(spec, _quietos("point", 4))
        assert "point_move" in nombres

    def test_point_move_does_not_fire_by_mistake(self):
        # THE PALM IS NOT A POINTING FINGER: four fingers out.
        spec = GestureSpec()
        nombres = _pasa(spec, _quietos("open", 8))
        assert "point_move" not in nombres

    def test_point_move_fires_from_far_away(self):
        spec = GestureSpec()
        nombres = _pasa(spec, _quietos("point", 4, scale=0.5))
        assert "point_move" in nombres

    def test_pinch_click_fires(self):
        # A PELLIZCO CORTO: the click lands when the fingers open.
        spec = GestureSpec()
        nombres = _pasa(spec, _pellizco(3) + _suelta(1))
        assert "pinch_click" in nombres

    def test_pinch_click_does_not_fire_by_mistake(self):
        # THE CLOSED HAND IS NOT A PINCH: the thumb is not against the
        # index, so there is no button to press.
        spec = GestureSpec()
        nombres = _pasa(spec, _quietos("fist", 8))
        assert "pinch_click" not in nombres

    def test_pinch_click_fires_from_far_away(self):
        spec = GestureSpec()
        nombres = _pasa(spec, _pellizco(3, scale=0.5) + _suelta(1, scale=0.5))
        assert "pinch_click" in nombres

    def test_pinch_double_click_fires(self):
        # DOS PINCHES SEGUIDOS.
        spec = GestureSpec()
        nombres = _pasa(spec, _pellizco(3) + _suelta(2) + _pellizco(3) + _suelta(1))
        assert "pinch_double_click" in nombres

    def test_pinch_double_click_does_not_fire_by_mistake(self):
        # DOS PINCHES MUY SEPARADOS SON DOS CLICS, no uno doble.
        spec = GestureSpec()
        nombres = _pasa(spec, _pellizco(3) + _suelta(8) + _pellizco(3) + _suelta(1))
        assert "pinch_double_click" not in nombres
        assert nombres.count("pinch_click") == 2

    def test_pinch_double_click_fires_from_far_away(self):
        spec = GestureSpec()
        nombres = _pasa(spec, _pellizco(3, scale=0.5) + _suelta(2, scale=0.5)
                        + _pellizco(3, scale=0.5) + _suelta(1, scale=0.5))
        assert "pinch_double_click" in nombres

    def test_pinch_right_fires(self):
        # EL PELLIZCO SOSTENIDO: seis décimas de segundo quieto.
        spec = GestureSpec()
        nombres = _pasa(spec, _pellizco(12))
        assert "pinch_right" in nombres

    def test_pinch_right_does_not_fire_by_mistake(self):
        # UN PELLIZCO CORTO NO ES EL BOTÓN DERECHO.
        spec = GestureSpec()
        nombres = _pasa(spec, _pellizco(3) + _suelta(1))
        assert "pinch_right" not in nombres

    def test_pinch_right_fires_from_far_away(self):
        spec = GestureSpec()
        nombres = _pasa(spec, _pellizco(12, scale=0.5))
        assert "pinch_right" in nombres

    def test_pinch_drag_fires(self):
        # EL PELLIZCO QUE SE MUEVE: el arrastre.
        spec = GestureSpec()
        cuadros = _pellizco(3) + _moviendose("pinch", 5, px=0.05)
        nombres = _pasa(spec, cuadros)
        assert "pinch_drag" in nombres

    def test_pinch_drag_does_not_fire_by_mistake(self):
        # UN PELLIZCO QUE NO SE MUEVE NO ARRASTRA NADA.
        spec = GestureSpec()
        nombres = _pasa(spec, _pellizco(10))
        assert "pinch_drag" not in nombres

    def test_pinch_drag_fires_from_far_away(self):
        spec = GestureSpec()
        cuadros = _pellizco(3, scale=0.5) + _moviendose("pinch", 5, px=0.05,
                                                         scale=0.5)
        nombres = _pasa(spec, cuadros)
        assert "pinch_drag" in nombres

    def test_two_scroll_fires(self):
        # LOS DOS DEDOS: la rueda.
        spec = GestureSpec()
        nombres = _pasa(spec, _moviendose("two", 6, py=0.02))
        assert "two_scroll" in nombres

    def test_two_scroll_does_not_fire_by_mistake(self):
        # UN DEDO SOLO NO ES LA RUEDA.
        spec = GestureSpec()
        nombres = _pasa(spec, _moviendose("point", 6, py=0.02))
        assert "two_scroll" not in nombres

    def test_two_scroll_fires_from_far_away(self):
        spec = GestureSpec()
        nombres = _pasa(spec, _moviendose("two", 6, py=0.02, scale=0.5))
        assert "two_scroll" in nombres

    def test_two_scroll_fires_with_the_ring_finger_out(self):
        """LA RUEDA NO NECESITA QUE EL ANULAR ESTÉ RECOGIDO.

        THIS IS WHAT THE LIVE SESSION FOUND: on a real hand the ring
        rests half out, and the machine read three fingers where the
        user meant two. The wheel is the index and the middle travelling
        with the hand — not a count of fingers.
        """
        spec = GestureSpec()
        nombres = _pasa(spec, _moviendose("three", 6, py=0.02))
        assert "two_scroll" in nombres


# ── 2. ESTADOS DE CONTROL ───────────────────────────────────────────────────

class TestEstados:
    """One hand: the palm, the fist and the thumb."""

    def test_open_clutch_fires(self):
        # LA PALMA QUIETA: el embrague, el modo seguro.
        spec = GestureSpec()
        nombres = _pasa(spec, _quietos("open", 8))
        assert "open_clutch" in nombres

    def test_open_clutch_does_not_fire_by_mistake(self):
        # EL PUÑO NO ES LA PALMA.
        spec = GestureSpec()
        nombres = _pasa(spec, _quietos("fist", 8))
        assert "open_clutch" not in nombres

    def test_open_clutch_fires_from_far_away(self):
        spec = GestureSpec()
        nombres = _pasa(spec, _quietos("open", 8, scale=0.5))
        assert "open_clutch" in nombres

    def test_fist_confirm_fires(self):
        # EL PUÑO: confirmar o cerrar.
        spec = GestureSpec()
        nombres = _pasa(spec, _quietos("fist", 8))
        assert "fist_confirm" in nombres

    def test_fist_confirm_does_not_fire_by_mistake(self):
        # LA PALMA NO ES EL PUÑO.
        spec = GestureSpec()
        nombres = _pasa(spec, _quietos("open", 8))
        assert "fist_confirm" not in nombres

    def test_fist_confirm_fires_from_far_away(self):
        spec = GestureSpec()
        nombres = _pasa(spec, _quietos("fist", 8, scale=0.5))
        assert "fist_confirm" in nombres

    def test_thumb_up_approve_fires(self):
        # EL PULGAR ARRIBA: aprobar.
        spec = GestureSpec()
        nombres = _pasa(spec, _quietos("thumb_up", 8))
        assert "thumb_up_approve" in nombres

    def test_thumb_up_approve_does_not_fire_by_mistake(self):
        # EL PULGAR ABAJO NO APRUEBA NADA.
        spec = GestureSpec()
        nombres = _pasa(spec, _quietos("thumb_down", 8))
        assert "thumb_up_approve" not in nombres

    def test_thumb_up_approve_fires_from_far_away(self):
        spec = GestureSpec()
        nombres = _pasa(spec, _quietos("thumb_up", 8, scale=0.5))
        assert "thumb_up_approve" in nombres

    def test_thumb_down_reject_fires(self):
        # EL PULGAR ABAJO: rechazar.
        spec = GestureSpec()
        nombres = _pasa(spec, _quietos("thumb_down", 8))
        assert "thumb_down_reject" in nombres

    def test_thumb_down_reject_does_not_fire_by_mistake(self):
        # EL PULGAR ARRIBA NO RECHAZA NADA.
        spec = GestureSpec()
        nombres = _pasa(spec, _quietos("thumb_up", 8))
        assert "thumb_down_reject" not in nombres

    def test_thumb_down_reject_fires_from_far_away(self):
        spec = GestureSpec()
        nombres = _pasa(spec, _quietos("thumb_down", 8, scale=0.5))
        assert "thumb_down_reject" in nombres


# ── 3. VENTANAS Y SISTEMA ───────────────────────────────────────────────────

class TestVentanas:
    """One hand: the windows of Windows."""

    def test_three_switch_fires(self):
        # TRES DEDOS DE LADO: cambiar de ventana.
        spec = GestureSpec()
        nombres = _pasa(spec, _moviendose("three", 8, px=0.05))
        assert "three_switch" in nombres

    def test_three_switch_does_not_fire_by_mistake(self):
        # DOS DEDOS DE LADO SON LA RUEDA, no el cambio de ventana.
        spec = GestureSpec()
        nombres = _pasa(spec, _moviendose("two", 8, px=0.05))
        assert "three_switch" not in nombres

    def test_three_switch_fires_from_far_away(self):
        spec = GestureSpec()
        nombres = _pasa(spec, _moviendose("three", 8, px=0.05, scale=0.5))
        assert "three_switch" in nombres

    def test_palm_maximize_fires(self):
        # LA PALMA EMPUJADA HACIA ARRIBA: maximizar.
        spec = GestureSpec()
        nombres = _pasa(spec, _moviendose("open", 8, py=-0.05))
        assert "palm_maximize" in nombres

    def test_palm_maximize_does_not_fire_by_mistake(self):
        # LA PALMA EMPUJADA HACIA ABAJO NO MAXIMIZA NADA.
        spec = GestureSpec()
        nombres = _pasa(spec, _moviendose("open", 8, py=0.05))
        assert "palm_maximize" not in nombres

    def test_palm_maximize_fires_from_far_away(self):
        spec = GestureSpec()
        nombres = _pasa(spec, _moviendose("open", 8, py=-0.05, scale=0.5))
        assert "palm_maximize" in nombres

    def test_palm_minimize_fires(self):
        # LA PALMA EMPUJADA HACIA ABAJO: minimizar.
        spec = GestureSpec()
        nombres = _pasa(spec, _moviendose("open", 8, py=0.05))
        assert "palm_minimize" in nombres

    def test_palm_minimize_does_not_fire_by_mistake(self):
        # LA PALMA EMPUJADA HACIA ARRIBA NO MINIMIZA NADA.
        spec = GestureSpec()
        nombres = _pasa(spec, _moviendose("open", 8, py=-0.05))
        assert "palm_minimize" not in nombres

    def test_palm_minimize_fires_from_far_away(self):
        spec = GestureSpec()
        nombres = _pasa(spec, _moviendose("open", 8, py=0.05, scale=0.5))
        assert "palm_minimize" in nombres

    def test_l_dictation_fires(self):
        # LA L: despertar a T.E.E.M.O. o dictar.
        spec = GestureSpec()
        nombres = _pasa(spec, _quietos("l_shape", 8))
        assert "l_dictation" in nombres

    def test_l_dictation_does_not_fire_by_mistake(self):
        # EL DEDO SEÑALANDO NO ES LA L: el pulgar va con el índice.
        spec = GestureSpec()
        nombres = _pasa(spec, _quietos("point", 8))
        assert "l_dictation" not in nombres

    def test_l_dictation_fires_from_far_away(self):
        spec = GestureSpec()
        nombres = _pasa(spec, _quietos("l_shape", 8, scale=0.5))
        assert "l_dictation" in nombres


# ── 4. LAS DOS MANOS ────────────────────────────────────────────────────────

class TestDosManos:
    """Two hands: the gestures that need precision."""

    def test_frame_screenshot_fires(self):
        # DOS L's OPUESTAS: el encuadre de la captura.
        spec = GestureSpec()
        arriba, abajo = _dos_ls()
        cuadros = [_dos_manos(arriba, abajo) for _ in range(8)]
        nombres = _pasa(spec, cuadros)
        assert "frame_screenshot" in nombres

    def test_frame_screenshot_does_not_fire_by_mistake(self):
        # UNA MANO QUE NO ES LA L NO ES UN ENCUADRE.
        spec = GestureSpec()
        izquierda = _pose("l_shape", dx=-0.20)
        derecha = _pose("point", dx=0.20)
        cuadros = [_dos_manos(izquierda, derecha) for _ in range(8)]
        nombres = _pasa(spec, cuadros)
        assert "frame_screenshot" not in nombres

    def test_frame_screenshot_fires_from_far_away(self):
        spec = GestureSpec()
        arriba, abajo = _dos_ls(0.5)
        cuadros = [_dos_manos(arriba, abajo) for _ in range(8)]
        nombres = _pasa(spec, cuadros)
        assert "frame_screenshot" in nombres

    def test_two_pinch_area_fires(self):
        # UNA MANO QUIETA Y LA OTRA MOVIÉNDOSE: seleccionar un área.
        spec = GestureSpec()
        quieta = _pose("pinch", dx=-0.20)
        cuadros = []
        for i in range(8):
            movida = _pose("pinch", dx=0.20 + 0.05 * i, dy=0.05 * i)
            cuadros.append(_dos_manos(quieta, movida))
        nombres = _pasa(spec, cuadros)
        assert "two_pinch_area" in nombres

    def test_two_pinch_area_does_not_fire_by_mistake(self):
        # CUANDO LAS DOS SE MUEVEN ES EL ZOOM, no el área.
        spec = GestureSpec()
        cuadros = []
        for i in range(8):
            izquierda = _pose("pinch", dx=-0.20 - 0.04 * i)
            derecha = _pose("pinch", dx=0.20 + 0.04 * i)
            cuadros.append(_dos_manos(izquierda, derecha))
        nombres = _pasa(spec, cuadros)
        assert "two_pinch_area" not in nombres

    def test_two_pinch_area_fires_from_far_away(self):
        spec = GestureSpec()
        quieta = _pose("pinch", dx=-0.20, scale=0.5)
        cuadros = []
        for i in range(8):
            movida = _pose("pinch", dx=0.20 + 0.05 * i, dy=0.05 * i, scale=0.5)
            cuadros.append(_dos_manos(quieta, movida))
        nombres = _pasa(spec, cuadros)
        assert "two_pinch_area" in nombres

    def test_two_pinch_zoom_fires(self):
        # LAS DOS MANOS SEPARÁNDOSE: el zoom.
        spec = GestureSpec()
        cuadros = []
        for i in range(8):
            izquierda = _pose("pinch", dx=-0.20 - 0.04 * i)
            derecha = _pose("pinch", dx=0.20 + 0.04 * i)
            cuadros.append(_dos_manos(izquierda, derecha))
        nombres = _pasa(spec, cuadros)
        assert "two_pinch_zoom" in nombres

    def test_two_pinch_zoom_does_not_fire_by_mistake(self):
        # CON UNA MANO QUIETA ES EL ÁREA, no el zoom.
        spec = GestureSpec()
        quieta = _pose("pinch", dx=-0.20)
        cuadros = []
        for i in range(8):
            movida = _pose("pinch", dx=0.20 + 0.05 * i, dy=0.05 * i)
            cuadros.append(_dos_manos(quieta, movida))
        nombres = _pasa(spec, cuadros)
        assert "two_pinch_zoom" not in nombres

    def test_two_pinch_zoom_fires_from_far_away(self):
        spec = GestureSpec()
        cuadros = []
        for i in range(8):
            izquierda = _pose("pinch", dx=-0.20 - 0.04 * i, scale=0.5)
            derecha = _pose("pinch", dx=0.20 + 0.04 * i, scale=0.5)
            cuadros.append(_dos_manos(izquierda, derecha))
        nombres = _pasa(spec, cuadros)
        assert "two_pinch_zoom" in nombres

    def test_palm_clap_sleep_fires(self):
        # LAS PALMAS JUNTAS: dormir.
        spec = GestureSpec()
        izquierda = _pose("open", dx=-0.06)
        derecha = _pose("open", dx=0.06)
        cuadros = [_dos_manos(izquierda, derecha) for _ in range(8)]
        nombres = _pasa(spec, cuadros)
        assert "palm_clap_sleep" in nombres

    def test_palm_clap_sleep_does_not_fire_by_mistake(self):
        # DOS MANOS LEJOS UNA DE OTRA NO SON UN CHOQUE.
        spec = GestureSpec()
        izquierda = _pose("open", dx=-0.35)
        derecha = _pose("open", dx=0.35)
        cuadros = [_dos_manos(izquierda, derecha) for _ in range(8)]
        nombres = _pasa(spec, cuadros)
        assert "palm_clap_sleep" not in nombres

    def test_palm_clap_sleep_fires_from_far_away(self):
        spec = GestureSpec()
        izquierda = _pose("open", dx=-0.03, scale=0.5)
        derecha = _pose("open", dx=0.03, scale=0.5)
        cuadros = [_dos_manos(izquierda, derecha) for _ in range(8)]
        nombres = _pasa(spec, cuadros)
        assert "palm_clap_sleep" in nombres


# ── the coverage of the whole vocabulary ────────────────────────────────────

def test_every_gesture_of_the_specification_has_three_validations():
    """NOT ONE GESTURE OF THE SPECIFICATION IS LEFT WITHOUT ITS THREE.

    The names are collected from the test file itself, so a gesture that
    is added to the vocabulary without its three checks makes this fail.
    """
    fuente = Path(__file__).read_text(encoding="utf-8")
    sin_comprobar: dict[str, set[str]] = {}
    for gesto in GESTURES:
        faltan = {sufijo for sufijo in
                  ("fires", "does_not_fire_by_mistake", "fires_from_far_away")
                  if f"test_{gesto}_{sufijo}" not in fuente}
        if faltan:
            sin_comprobar[gesto] = faltan
    assert not sin_comprobar, f"gestos sin las tres comprobaciones: {sin_comprobar}"


def test_the_vocabulary_has_the_four_categories():
    from core.gesture_spec import CATEGORIES

    assert set(CATEGORIES) == set(GESTURES)
    assert set(CATEGORIES.values()) == {"navigation", "control", "windows", "precision"}
