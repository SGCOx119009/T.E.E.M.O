"""
tests/test_emotional_spectrum.py — las doce estados de ánimo y el color que
se travels.

LO QUE COMPRUEBAN ESTAS PRUEBAS
    El espectro emocional es una máquina de estados de doce estados, y cada
    estado es un objeto que alimenta TRES salidas a la vez: el prompt, la
    paleta y las micro-animaciones. Lo que puede romperse es peor que en
    un módulo corriente, porque un estado mal escrito no da error: da un
    assistant que escribe en serio mientras el HUD parpadea en violeta.

    Las pruebas de aquí cubren las cuatro cosas que importan:

      1. LOS DOCE ESTADOS EXISTEN, con su color y su familia. Seis
         constructivos y seis negativos: el plan los cuenta así.
      2. EL ESTADO ACTIVO SE MUEVE — y un nombre que no existe NO lo mueve:
         una herramienta de voz que se equivoca de nombre no puede tumbar
         el estado de ánimo del assistant.
      3. EL COLOR ES UNA LÍNEA, NO UN SALTO. El punto medio entre el cian
         y el violeta es un color de verdad, y no es ninguno de los dos
         extremos. Si el punto medio devolviera uno de los extremos, el
         HUD estaría cambiando de paleta de golpe, que es exactamente lo
         que este módulo existe para evitar.
      4. LA MEZCLA PESA. Los pesos que no suman uno se normalizan, y una
         mezcla de 3 y 1 es la misma mezcla de 0,75 y 0,25.

Y LO QUE NO COMPRUEBAN
    Que el prompt sea bueno. Eso no se puede medir con una aserción: se
    comprueba que el estado tiene prompt, tono, tempo, estilo, animación y
    parámetros, y que esos datos llegan enteros al que los use.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from core import emotional_spectrum as es  # noqa: E402


_HEX = re.compile(r"^#[0-9a-f]{6}$")

_CONSTRUCTIVE = {
    "EMPATHETIC", "TACTICAL", "WITTY", "MOTIVATIONAL", "CHALLENGING", "VIGILANT",
}
_NEGATIVE = {
    "FRUSTRATED", "SKEPTICAL", "SOLEMN", "CONCERNED", "INDIGNANT", "COLD",
}


class SpectrumContentTests(unittest.TestCase):
    """Los doce estados, con todo lo que cada uno tiene que traer."""

    def test_there_are_twelve_states(self) -> None:
        self.assertEqual(len(es.STATE_NAMES), 12)
        self.assertEqual(len(set(es.STATE_NAMES)), 12)
        self.assertEqual(set(es.STATES), set(es.STATE_NAMES))

    def test_every_state_has_a_valid_hex_colour(self) -> None:
        for name in es.STATE_NAMES:
            with self.subTest(state=name):
                colour = es.STATES[name].color
                self.assertTrue(_HEX.match(colour), f"{name} has colour {colour!r}")
                # The colour has to survive the trip to the screen intact.
                self.assertEqual(es.rgb_to_hex(es.hex_to_rgb(colour)), colour)

    def test_the_families_are_six_and_six(self) -> None:
        constructive = {n for n in es.STATE_NAMES if es.STATES[n].family == "constructive"}
        negative = {n for n in es.STATE_NAMES if es.STATES[n].family == "negative"}
        self.assertEqual(constructive, _CONSTRUCTIVE)
        self.assertEqual(negative, _NEGATIVE)
        self.assertEqual(constructive | negative, set(es.STATE_NAMES))

    def test_every_state_carries_the_three_outputs(self) -> None:
        for name in es.STATE_NAMES:
            with self.subTest(state=name):
                state = es.STATES[name]
                # 1. the prompt: an instruction the model can obey.
                self.assertTrue(state.prompt.strip())
                self.assertGreater(len(state.prompt.split()), 5)
                # the tone, the tempo and the style that go with it.
                self.assertTrue(state.tone.strip())
                self.assertTrue(state.style.strip())
                self.assertGreaterEqual(state.tempo, 0.85)
                self.assertLessEqual(state.tempo, 1.15)
                # 3. the physical expression: a class and its numbers.
                self.assertTrue(state.animation.strip())
                self.assertTrue(state.animation_params)
                for key, value in state.animation_params.items():
                    self.assertIsInstance(key, str)
                    self.assertIsInstance(value, float)

    def test_the_four_colours_the_plan_fixes_are_the_ones_written(self) -> None:
        self.assertEqual(es.STATES["EMPATHETIC"].color, "#9d72ff")
        self.assertEqual(es.STATES["TACTICAL"].color, "#00f0ff")
        self.assertEqual(es.STATES["WITTY"].color, "#ffaa00")
        self.assertEqual(es.STATES["MOTIVATIONAL"].color, "#ff4422")


class SpectrumLookupTests(unittest.TestCase):
    """El nombre del estado, que llega de una voz y de un argumento."""

    def setUp(self) -> None:
        es.reset_state()

    def tearDown(self) -> None:
        es.reset_state()

    def test_lookup_accepts_lower_case_and_spaces(self) -> None:
        expected = es.STATES["EMPATHETIC"]
        self.assertIs(es.state_by_name("empathetic"), expected)
        self.assertIs(es.state_by_name("EMPATHETIC"), expected)
        self.assertIs(es.state_by_name("  Empathetic  "), expected)
        self.assertIs(es.state_by_name("Tactical"), es.STATES["TACTICAL"])

    def test_an_unknown_name_is_refused_with_a_message(self) -> None:
        with self.assertRaises((KeyError, ValueError)) as caught:
            es.state_by_name("grumpy")
        message = str(caught.exception)
        self.assertIn("grumpy", message)
        self.assertIn("TACTICAL", message)

    def test_set_state_moves_the_machine_and_returns_the_new_state(self) -> None:
        self.assertIs(es.current_state(), es.STATES["TACTICAL"])
        moved = es.set_state("witty")
        self.assertIs(moved, es.STATES["WITTY"])
        self.assertIs(es.current_state(), es.STATES["WITTY"])

    def test_reset_state_goes_back_to_the_first_one(self) -> None:
        es.set_state("COLD")
        back = es.reset_state()
        self.assertIs(back, es.STATES["TACTICAL"])
        self.assertIs(es.current_state(), es.STATES["TACTICAL"])

    def test_a_bad_name_changes_nothing(self) -> None:
        es.set_state("SKEPTICAL")
        with self.assertRaises(ValueError) as caught:
            es.set_state("not-a-state")
        message = str(caught.exception)
        self.assertIn("not-a-state", message)
        self.assertIn("EMPATHETIC", message)
        # The machine is exactly as it was.
        self.assertIs(es.current_state(), es.STATES["SKEPTICAL"])

    def test_the_state_is_readable_from_another_thread(self) -> None:
        import threading

        seen: list[object] = []

        def reader() -> None:
            seen.append(es.current_state())

        thread = threading.Thread(target=reader)
        thread.start()
        thread.join(timeout=5.0)
        self.assertEqual(seen, [es.STATES["TACTICAL"]])


class SpectrumColourTests(unittest.TestCase):
    """El color: una línea, no un salto."""

    def test_the_ends_of_the_path_are_exact(self) -> None:
        self.assertEqual(es.blend_path("TACTICAL", "EMPATHETIC", 0.0), "#00f0ff")
        self.assertEqual(es.blend_path("TACTICAL", "EMPATHETIC", 1.0), "#9d72ff")

    def test_the_middle_of_the_path_is_the_arithmetic_middle(self) -> None:
        # TACTICAL is #00f0ff -> (0, 240, 255)
        # EMPATHETIC is #9d72ff -> (157, 114, 255)
        # The middle of the two is ((0+157)/2, (240+114)/2, (255+255)/2)
        #   = (78.5, 177, 255) -> (78, 177, 255) -> #4eb1ff
        middle = es.blend_path("TACTICAL", "EMPATHETIC", 0.5)
        self.assertEqual(middle, "#4eb1ff")
        self.assertEqual(es.hex_to_rgb(middle), (78, 177, 255))

    def test_the_middle_is_neither_of_the_ends(self) -> None:
        middle = es.blend_path("TACTICAL", "EMPATHETIC", 0.5)
        self.assertNotEqual(middle, "#00f0ff")
        self.assertNotEqual(middle, "#9d72ff")

    def test_the_path_has_no_jump_anywhere_along_it(self) -> None:
        previous = es.blend_path("TACTICAL", "EMPATHETIC", 0.0)
        for step in range(1, 101):
            current = es.blend_path("TACTICAL", "EMPATHETIC", step / 100.0)
            before = es.hex_to_rgb(previous)
            after = es.hex_to_rgb(current)
            # A jump would be a big step in one of the channels.
            for channel_before, channel_after in zip(before, after):
                self.assertLessEqual(abs(channel_after - channel_before), 4)
            previous = current

    def test_the_path_outside_the_unit_interval_is_clamped(self) -> None:
        self.assertEqual(es.blend_path("TACTICAL", "EMPATHETIC", -3.0), "#00f0ff")
        self.assertEqual(es.blend_path("TACTICAL", "EMPATHETIC", 4.0), "#9d72ff")

    def test_lerp_works_on_plain_colours(self) -> None:
        self.assertEqual(es.lerp_rgb((0, 0, 0), (10, 20, 30), 0.5), (5, 10, 15))
        self.assertEqual(es.lerp_rgb((0, 0, 0), (10, 20, 30), 0.0), (0, 0, 0))
        self.assertEqual(es.lerp_rgb((0, 0, 0), (10, 20, 30), 1.0), (10, 20, 30))

    def test_a_blend_normalises_the_weights(self) -> None:
        heavy = es.blend([(es.STATES["TACTICAL"], 3.0), (es.STATES["EMPATHETIC"], 1.0)])
        light = es.blend([(es.STATES["TACTICAL"], 0.75), (es.STATES["EMPATHETIC"], 0.25)])
        self.assertEqual(heavy, light)
        # By hand: (0*3 + 157*1)/4 = 39.25 -> 39
        #         (240*3 + 114*1)/4 = 208.5 -> 208
        #         (255*3 + 255*1)/4 = 255
        self.assertEqual(heavy, "#27d0ff")

    def test_a_blend_of_one_state_is_that_state(self) -> None:
        self.assertEqual(
            es.blend([(es.STATES["COLD"], 5.0)]), es.STATES["COLD"].color
        )

    def test_a_blend_with_nothing_to_blend_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            es.blend([])
        with self.assertRaises(ValueError):
            es.blend([(es.STATES["COLD"], 0.0)])

    def test_hex_and_rgb_survive_the_round_trip(self) -> None:
        for name in es.STATE_NAMES:
            colour = es.STATES[name].color
            with self.subTest(state=name):
                self.assertEqual(es.rgb_to_hex(es.hex_to_rgb(colour)), colour)

    def test_a_broken_colour_is_visible(self) -> None:
        with self.assertRaises(ValueError):
            es.hex_to_rgb("#12345")
        with self.assertRaises(ValueError):
            es.hex_to_rgb("not a colour")


class SpectrumPromptTests(unittest.TestCase):
    """El prompt: la parte del estado que llega a la respuesta."""

    def setUp(self) -> None:
        es.reset_state()

    def tearDown(self) -> None:
        es.reset_state()

    def test_the_fragment_follows_the_active_state(self) -> None:
        es.set_state("SOLEMN")
        fragment = es.prompt_fragment()
        self.assertIn(es.STATES["SOLEMN"].prompt, fragment)
        self.assertIn("0.85", fragment)

    def test_the_fragment_of_a_given_state(self) -> None:
        fragment = es.prompt_fragment(es.STATES["WITTY"])
        self.assertIn(es.STATES["WITTY"].prompt, fragment)
        self.assertNotIn(es.STATES["COLD"].prompt, fragment)

    def test_the_mix_mentions_every_state_with_its_share(self) -> None:
        mixed = es.mix_prompt([(es.STATES["TACTICAL"], 0.7), (es.STATES["WITTY"], 0.3)])
        self.assertIn("TACTICAL", mixed)
        self.assertIn("WITTY", mixed)
        self.assertIn("70%", mixed)
        self.assertIn("30%", mixed)

    def test_a_mix_with_nothing_to_mix_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            es.mix_prompt([])


if __name__ == "__main__":
    unittest.main()
