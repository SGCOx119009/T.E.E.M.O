"""
tests/test_hardware_equilibrium.py — la escalera que se baja cuando la
máquina está cargada.

LO QUE COMPRUEBAN ESTAS PRUEBAS
    El regulador adaptativo de hardware: cuatro niveles, y en cada uno de
    ellos un frame rate y un intervalo de frame. La escalera es EXACTA —
    <60 OPTIMAL, 60-75 BALANCED, 75-85 THROTTLED, >85 CRITICAL_RECOVERY — y
    los límites importan, porque un HUD que baja un escalón antes de tiempo
    se ve lento en una máquina que no lo está, y uno que baja después se
    congela en una que sí lo está.

    Y las tres cosas que un regulador adaptativo puede hacer mal de verdad:

      1. MEDIR MAL. La lectura tiene que ser un `ResourceState` con los
         tipos correctos, incluso en una máquina sin GPU: lo que no se sabe
         se dice con -1.0, que es una respuesta y no un fallo.
      2. TOCAR LA MEMORIA DE OTRO. La compactación del conjunto de trabajo
         es SÓLO sobre este proceso, y en un sistema sin psapi tiene que
         devolver False sin reventar.
      3. GRITAR. Los oyentes se avisan cuando el NIVEL cambia, no en cada
         muestra: un aviso por segundo es un repintado por segundo de un
         HUD que ya se repinta solo.

LO QUE NO COMPRUEBAN ESTAS PRUEBAS
    Que la máquina esté tranquila. Estas pruebas no miden la carga real: no
    hay forma de tener la máquina sobrecargada a propósito sin ser un mal
    vecino. Lo que se comprueba es la aritmética de la escalera, que es la
    parte que decide.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from core import hardware_equilibrium as he  # noqa: E402


class LadderTests(unittest.TestCase):
    """Los cuatro niveles, y los límites exactos de cada uno."""

    # load, level, fps, frame interval in ms
    LADDER = (
        (0.0, "OPTIMAL", 60, 16),
        (59.9, "OPTIMAL", 60, 16),
        (60.0, "BALANCED", 45, 22),
        (74.9, "BALANCED", 45, 22),
        (75.0, "BALANCED", 45, 22),
        (75.1, "THROTTLED", 30, 33),
        (85.0, "THROTTLED", 30, 33),
        (85.1, "CRITICAL_RECOVERY", 15, 66),
        (100.0, "CRITICAL_RECOVERY", 15, 66),
    )

    def test_the_ladder_has_four_steps(self) -> None:
        self.assertEqual(
            he.LEVELS, ("OPTIMAL", "BALANCED", "THROTTLED", "CRITICAL_RECOVERY")
        )

    def test_every_limit_lands_on_its_level(self) -> None:
        for load, level, _fps, _interval in self.LADDER:
            with self.subTest(load=load):
                self.assertEqual(he.level_for(load), level)

    def test_every_level_has_its_frame_rate_and_its_interval(self) -> None:
        for _load, level, fps, interval in self.LADDER:
            with self.subTest(level=level):
                self.assertEqual(he.fps_for(level), fps)
                self.assertEqual(he.frame_interval_for(level), interval)

    def test_the_interval_is_the_budget_of_the_frame_rate(self) -> None:
        for level in he.LEVELS:
            with self.subTest(level=level):
                interval = he.frame_interval_for(level)
                fps = he.fps_for(level)
                # 1000 / fps rounded to the millisecond the timer takes.
                self.assertLessEqual(abs(interval - round(1000 / fps)), 1)

    def test_an_unknown_level_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            he.fps_for("PANICKING")
        with self.assertRaises(ValueError):
            he.frame_interval_for("PANICKING")

    def test_the_combined_load_is_a_weighted_average(self) -> None:
        # cpu 50, ram 100, gpu 0 -> 50*0.5 + 100*0.3 + 0*0.2 = 55
        self.assertAlmostEqual(he.load_from(50.0, 100.0, 0.0), 55.0, places=6)
        # The same load with no GPU at all: the weights of the two that
        # remain are renormalised (0.5 and 0.3, so 0.8 in total), and the
        # load does not halve for having no video card:
        #   (50*0.5 + 100*0.3) / 0.8 = 55 / 0.8 = 68.75
        self.assertAlmostEqual(he.load_from(50.0, 100.0, -1.0), 68.75, places=6)

    def test_a_load_that_nobody_can_read_is_zero(self) -> None:
        self.assertEqual(he.load_from(-1.0, -1.0, -1.0), 0.0)

    def test_the_combined_load_stays_inside_the_scale(self) -> None:
        self.assertLessEqual(he.load_from(100.0, 100.0, 100.0), 100.0)
        self.assertGreaterEqual(he.load_from(0.0, 0.0, 0.0), 0.0)


class RegulatorTests(unittest.TestCase):
    """El regulador: lo que mide, lo que dice y lo que toca."""

    def test_a_reading_is_a_resource_state_with_the_right_types(self) -> None:
        regulator = he.HardwareRegulator()
        state = regulator.sample()
        self.assertIsInstance(state, he.ResourceState)
        self.assertIn(state.level, he.LEVELS)
        self.assertIsInstance(state.load_percent, float)
        self.assertIsInstance(state.cpu_percent, float)
        self.assertIsInstance(state.ram_percent, float)
        self.assertIsInstance(state.gpu_percent, float)
        self.assertIsInstance(state.vram_free_mb, float)
        self.assertIsInstance(state.fps, int)
        self.assertIsInstance(state.frame_interval_ms, int)
        self.assertIsInstance(state.memory_purged, bool)
        self.assertIsInstance(state.at, float)
        # The frame rate and the interval belong to the level of the
        # reading, always.
        self.assertEqual(state.fps, he.fps_for(state.level))
        self.assertEqual(state.frame_interval_ms, he.frame_interval_for(state.level))

    def test_the_levels_are_the_ones_of_the_plan(self) -> None:
        self.assertEqual(
            he.LEVELS, ("OPTIMAL", "BALANCED", "THROTTLED", "CRITICAL_RECOVERY")
        )
        self.assertEqual(he.fps_for("OPTIMAL"), 60)
        self.assertEqual(he.fps_for("BALANCED"), 45)
        self.assertEqual(he.fps_for("THROTTLED"), 30)
        self.assertEqual(he.fps_for("CRITICAL_RECOVERY"), 15)
        self.assertEqual(he.frame_interval_for("OPTIMAL"), 16)
        self.assertEqual(he.frame_interval_for("BALANCED"), 22)
        self.assertEqual(he.frame_interval_for("THROTTLED"), 33)
        self.assertEqual(he.frame_interval_for("CRITICAL_RECOVERY"), 66)

    def test_a_listening_earns_only_the_changes_of_level(self) -> None:
        regulator = he.HardwareRegulator()
        heard: list[he.ResourceState] = []
        regulator.add_listener(heard.append)

        # Two readings of the same level: one message, not two.
        same = _state_at("BALANCED")
        with patch.object(regulator, "sample", return_value=same):
            regulator.publish()
            regulator.publish()
        self.assertEqual(len(heard), 1)

        # A different level: the listener is told.
        with patch.object(regulator, "sample", return_value=_state_at("THROTTLED")):
            regulator.publish()
        self.assertEqual(len(heard), 2)
        self.assertEqual(heard[-1].level, "THROTTLED")

    def test_a_listener_that_raises_does_not_break_the_regulator(self) -> None:
        regulator = he.HardwareRegulator()
        heard: list[he.ResourceState] = []

        def broken(_state: he.ResourceState) -> None:
            raise RuntimeError("this listener is broken")

        regulator.add_listener(broken)
        regulator.add_listener(heard.append)
        with patch.object(regulator, "sample", return_value=_state_at("OPTIMAL")):
            regulator.publish()
        self.assertEqual(len(heard), 1)

    def test_a_listener_can_be_removed(self) -> None:
        regulator = he.HardwareRegulator()
        heard: list[he.ResourceState] = []
        regulator.add_listener(heard.append)
        regulator.remove_listener(heard.append)
        with patch.object(regulator, "sample", return_value=_state_at("OPTIMAL")):
            regulator.publish()
        self.assertEqual(heard, [])
        # Removing one that was never there is not an error.
        regulator.remove_listener(heard.append)

    def test_stopping_twice_is_not_an_error(self) -> None:
        regulator = he.HardwareRegulator()
        regulator.start()
        regulator.stop()
        regulator.stop()

    def test_the_thread_of_the_regulator_is_a_daemon(self) -> None:
        regulator = he.HardwareRegulator(interval_s=0.05)
        regulator.start()
        try:
            self.assertIsNotNone(regulator._thread)
            self.assertTrue(regulator._thread.daemon)
        finally:
            regulator.stop()


class PurgingTests(unittest.TestCase):
    """La compactación del conjunto de trabajo: sólo el de este proceso."""

    def test_the_working_set_is_compacted_without_raising(self) -> None:
        regulator = he.HardwareRegulator()
        # On Windows this is True; on a system without psapi it is False.
        # Either way it must not raise, because a failed compaction is an
        # optimisation that did not happen and not a reason to fall over.
        answer = regulator.purge_working_set()
        self.assertIsInstance(answer, bool)

    def test_a_system_without_psapi_answers_false(self) -> None:
        regulator = he.HardwareRegulator()
        with patch("ctypes.windll", None):
            self.assertFalse(regulator.purge_working_set())

    def test_a_broken_api_answers_false_and_does_not_raise(self) -> None:
        class _Broken:
            def __getattr__(self, _name):
                raise OSError("no hay psapi aquí")

        regulator = he.HardwareRegulator()
        with patch("ctypes.windll", _Broken()):
            self.assertFalse(regulator.purge_working_set())

    def test_the_purge_is_only_asked_for_from_throttled_upwards(self) -> None:
        regulator = he.HardwareRegulator()
        self.assertFalse(regulator.should_purge(_state_at("OPTIMAL")))
        self.assertFalse(regulator.should_purge(_state_at("BALANCED")))
        self.assertTrue(regulator.should_purge(_state_at("THROTTLED")))
        self.assertTrue(regulator.should_purge(_state_at("CRITICAL_RECOVERY")))


def _state_at(level: str) -> he.ResourceState:
    """A reading of a given level, for the tests that do not need a machine."""
    return he.ResourceState(
        level=level,
        load_percent=0.0,
        cpu_percent=0.0,
        ram_percent=0.0,
        gpu_percent=-1.0,
        vram_free_mb=-1.0,
        fps=he.fps_for(level),
        frame_interval_ms=he.frame_interval_for(level),
        memory_purged=False,
        at=0.0,
    )


if __name__ == "__main__":
    unittest.main()
