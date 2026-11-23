"""The barge-in detector: the dead zone, the echo guard and the cooldown.

These are the criteria of FASE 10 of the plan, tested without a microphone:
    * a loud block that is really our own echo never cuts the answer;
    * a block the echo guard could not classify never cuts the answer either;
    * a cough (below the dead zone) never cuts the answer;
    * a sustained voice does, and does it in well under 300 ms;
    * the second interruption waits for the cooldown;
    * the interruption bar sits ABOVE the end-of-turn bar;
    * and none of it needs a model.
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from core import bargein
from core.bargein import (
    ABSOLUTE_MIN,
    COOLDOWN_SECONDS,
    DEAD_ZONE_MARGIN,
    TURN_END_MARGIN,
    BargeInDecision,
    BargeInDetector,
)

# The room this suite pretends to be in: a desk, a quiet fan, and a noise
# floor of 0.02. Everything is computed from it, so the numbers below say
# what the detector does rather than what it happens to do.
NOISE = 0.02
BLOCK_MS = 64
BLOCK_S = BLOCK_MS / 1000.0

# A voice, unmistakably above the bar.
VOICE = 0.5
# A cough: well above the noise, but below the dead zone.
COUGH = 0.06


def detector(**kwargs) -> BargeInDetector:
    kwargs.setdefault("min_blocks", 2)
    return BargeInDetector(**kwargs)


class BargeInEchoTests(unittest.TestCase):
    """The echo guard is consulted FIRST, before any level."""

    def test_our_own_voice_never_interrupts_however_loud(self) -> None:
        det = detector()
        decision = det.feed(level=0.95, now=0.0, speaking=True,
                             is_user_speech=False, noise_floor=NOISE)
        self.assertFalse(decision.interrupted)
        self.assertEqual(decision.reason, bargein.REASON_ECHO)

    def test_a_loud_echo_block_does_not_start_a_run_either(self) -> None:
        det = detector()
        det.feed(level=0.95, now=0.0, speaking=True,
                 is_user_speech=False, noise_floor=NOISE)
        decision = det.feed(level=0.95, now=BLOCK_S, speaking=True,
                            is_user_speech=False, noise_floor=NOISE)
        self.assertFalse(decision.interrupted)
        self.assertEqual(decision.reason, bargein.REASON_ECHO)
        self.assertEqual(det.blocks, 0)

    def test_an_unclassifiable_block_does_not_interrupt(self) -> None:
        """The guard could not be asked. Prudence: do not cut the answer."""
        det = detector()
        decision = det.feed(level=0.9, now=0.0, speaking=True,
                            is_user_speech=None, noise_floor=NOISE)
        self.assertFalse(decision.interrupted)
        self.assertEqual(decision.reason, bargein.REASON_WARMING_UP)

        second = det.feed(level=0.9, now=BLOCK_S, speaking=True,
                          is_user_speech=None, noise_floor=NOISE)
        self.assertFalse(second.interrupted)
        self.assertEqual(second.reason, bargein.REASON_WARMING_UP)

    def test_the_guard_is_consulted_before_the_level(self) -> None:
        """A block above the bar that is our echo stays an echo."""
        det = detector()
        decision = det.feed(level=0.9, now=0.0, speaking=True,
                            is_user_speech=False, noise_floor=NOISE)
        self.assertGreater(decision.level, decision.threshold)
        self.assertFalse(decision.interrupted)


class BargeInDeadZoneTests(unittest.TestCase):
    """The dead zone: a cough is not an interruption."""

    def test_a_level_below_the_zone_does_not_interrupt(self) -> None:
        det = detector()
        decision = det.feed(level=COUGH, now=0.0, speaking=True,
                             is_user_speech=True, noise_floor=NOISE)
        self.assertFalse(decision.interrupted)
        self.assertEqual(decision.reason, bargein.REASON_TOO_QUIET)

    def test_the_interruption_bar_is_above_the_end_of_turn_bar(self) -> None:
        det = detector()
        decision = det.feed(level=VOICE, now=0.0, speaking=True,
                             is_user_speech=True, noise_floor=NOISE)
        turn_end = NOISE * TURN_END_MARGIN
        self.assertGreater(decision.threshold, turn_end)
        self.assertGreaterEqual(decision.threshold, ABSOLUTE_MIN)
        self.assertAlmostEqual(det.turn_end_threshold, turn_end, places=9)

    def test_a_single_loud_block_does_not_interrupt(self) -> None:
        """A click, a cough, a door. One block is never enough."""
        det = detector(min_blocks=2)
        decision = det.feed(level=VOICE, now=0.0, speaking=True,
                            is_user_speech=True, noise_floor=NOISE)
        self.assertFalse(decision.interrupted)
        self.assertEqual(decision.reason, bargein.REASON_VOICE)

    def test_a_run_broken_by_a_quiet_block_restarts(self) -> None:
        det = detector(min_blocks=2)
        det.feed(level=VOICE, now=0.0, speaking=True,
                 is_user_speech=True, noise_floor=NOISE)
        det.feed(level=COUGH, now=BLOCK_S, speaking=True,
                 is_user_speech=True, noise_floor=NOISE)
        decision = det.feed(level=VOICE, now=2 * BLOCK_S, speaking=True,
                            is_user_speech=True, noise_floor=NOISE)
        self.assertFalse(decision.interrupted)
        self.assertEqual(det.blocks, 1)


class BargeInVoiceTests(unittest.TestCase):
    """A sustained voice interrupts, and fast."""

    def test_a_sustained_voice_interrupts_under_300ms(self) -> None:
        det = detector(min_blocks=2)
        first = det.feed(level=VOICE, now=0.0, speaking=True,
                         is_user_speech=True, noise_floor=NOISE)
        self.assertFalse(first.interrupted)
        self.assertEqual(first.since_voice_ms, 0.0)

        second = det.feed(level=VOICE, now=BLOCK_S, speaking=True,
                          is_user_speech=True, noise_floor=NOISE)
        self.assertTrue(second.interrupted)
        self.assertEqual(second.reason, bargein.REASON_VOICE)
        # 64 ms of voice, from the first block above the bar to the cut.
        self.assertAlmostEqual(second.since_voice_ms, BLOCK_MS, places=6)
        self.assertLess(second.since_voice_ms, 300.0)

    def test_min_voice_ms_holds_the_interruption_back(self) -> None:
        det = detector(min_blocks=2, min_voice_ms=200.0)
        det.feed(level=VOICE, now=0.0, speaking=True,
                 is_user_speech=True, noise_floor=NOISE)
        decision = det.feed(level=VOICE, now=BLOCK_S, speaking=True,
                            is_user_speech=True, noise_floor=NOISE)
        self.assertFalse(decision.interrupted)
        self.assertEqual(decision.reason, bargein.REASON_VOICE)

        later = det.feed(level=VOICE, now=0.25, speaking=True,
                         is_user_speech=True, noise_floor=NOISE)
        self.assertTrue(later.interrupted)
        self.assertAlmostEqual(later.since_voice_ms, 250.0, places=6)

    def test_speaking_false_is_not_speaking(self) -> None:
        det = detector()
        decision = det.feed(level=VOICE, now=0.0, speaking=False,
                             is_user_speech=True, noise_floor=NOISE)
        self.assertFalse(decision.interrupted)
        self.assertEqual(decision.reason, bargein.REASON_NOT_SPEAKING)
        self.assertEqual(det.blocks, 0)

    def test_not_speaking_clears_a_run_in_progress(self) -> None:
        det = detector(min_blocks=2)
        det.feed(level=VOICE, now=0.0, speaking=True,
                 is_user_speech=True, noise_floor=NOISE)
        det.feed(level=VOICE, now=BLOCK_S, speaking=False,
                 is_user_speech=True, noise_floor=NOISE)
        decision = det.feed(level=VOICE, now=2 * BLOCK_S, speaking=True,
                            is_user_speech=True, noise_floor=NOISE)
        self.assertFalse(decision.interrupted)
        self.assertEqual(det.blocks, 1)


class BargeInCooldownTests(unittest.TestCase):
    """After an interruption, the room is given a moment."""

    def _interrupt(self, det: BargeInDetector, start: float = 0.0) -> float:
        det.feed(level=VOICE, now=start, speaking=True,
                 is_user_speech=True, noise_floor=NOISE)
        decision = det.feed(level=VOICE, now=start + BLOCK_S, speaking=True,
                            is_user_speech=True, noise_floor=NOISE)
        self.assertTrue(decision.interrupted)
        return start + BLOCK_S

    def test_a_second_interruption_waits_for_the_cooldown(self) -> None:
        det = detector(min_blocks=2)
        at = self._interrupt(det)

        during = det.feed(level=VOICE, now=at + 0.1, speaking=True,
                          is_user_speech=True, noise_floor=NOISE)
        self.assertFalse(during.interrupted)
        self.assertEqual(during.reason, bargein.REASON_COOLDOWN)

        edge = det.feed(level=VOICE, now=at + COOLDOWN_SECONDS - 0.001,
                        speaking=True, is_user_speech=True, noise_floor=NOISE)
        self.assertFalse(edge.interrupted)
        self.assertEqual(edge.reason, bargein.REASON_COOLDOWN)

    def test_after_the_cooldown_the_next_voice_interrupts(self) -> None:
        det = detector(min_blocks=2)
        at = self._interrupt(det)

        after = at + COOLDOWN_SECONDS + 0.01
        first = det.feed(level=VOICE, now=after, speaking=True,
                         is_user_speech=True, noise_floor=NOISE)
        self.assertFalse(first.interrupted)
        second = det.feed(level=VOICE, now=after + BLOCK_S, speaking=True,
                          is_user_speech=True, noise_floor=NOISE)
        self.assertTrue(second.interrupted)
        self.assertEqual(second.reason, bargein.REASON_VOICE)

    def test_the_cooldown_survives_the_assistant_stopping(self) -> None:
        """The point of the cooldown is to ignore the tail of the reply that
        was just cut off, so it outlives the assistant going quiet."""
        det = detector(min_blocks=2)
        at = self._interrupt(det)
        quiet = det.feed(level=VOICE, now=at + 0.05, speaking=False,
                         is_user_speech=None, noise_floor=NOISE)
        self.assertEqual(quiet.reason, bargein.REASON_NOT_SPEAKING)
        still = det.feed(level=VOICE, now=at + 0.1, speaking=True,
                         is_user_speech=True, noise_floor=NOISE)
        self.assertEqual(still.reason, bargein.REASON_COOLDOWN)


class BargeInNoiseFloorTests(unittest.TestCase):
    """The floor is measured, not configured — but it can be handed in."""

    def test_the_floor_follows_the_quiet_blocks_down(self) -> None:
        det = detector()
        det.feed(level=VOICE, now=0.0, speaking=True,
                 is_user_speech=True, noise_floor=NOISE)
        det.feed(level=0.001, now=BLOCK_S, speaking=True,
                 is_user_speech=True, noise_floor=NOISE)
        self.assertLess(det.noise_floor, NOISE)

    def test_our_own_echo_never_lifts_the_floor(self) -> None:
        det = detector()
        det.feed(level=0.9, now=0.0, speaking=True,
                 is_user_speech=False, noise_floor=NOISE)
        self.assertAlmostEqual(det.noise_floor, NOISE, places=9)

    def test_the_floor_is_used_when_none_is_passed(self) -> None:
        det = detector()
        decision = det.feed(level=VOICE, now=0.0, speaking=True,
                             is_user_speech=True)
        self.assertGreater(decision.threshold, 0.0)
        self.assertAlmostEqual(det.noise_floor, ABSOLUTE_MIN, places=9)

    def test_reset_forgets_the_run_but_keeps_the_room(self) -> None:
        det = detector()
        det.feed(level=VOICE, now=0.0, speaking=True,
                 is_user_speech=True, noise_floor=NOISE)
        floor = det.noise_floor
        det.reset()
        self.assertEqual(det.blocks, 0)
        self.assertAlmostEqual(det.noise_floor, floor, places=9)


class BargeInShapeTests(unittest.TestCase):
    """The shape of the answer the rest of the app will read."""

    def test_the_decision_carries_what_the_log_needs(self) -> None:
        det = detector()
        decision = det.feed(level=VOICE, now=0.0, speaking=True,
                             is_user_speech=True, noise_floor=NOISE)
        self.assertIsInstance(decision, BargeInDecision)
        self.assertIsInstance(decision.interrupted, bool)
        self.assertIsInstance(decision.reason, str)
        self.assertIsInstance(decision.level, float)
        self.assertIsInstance(decision.threshold, float)

    def test_the_defaults_are_the_documented_ones(self) -> None:
        self.assertEqual(DEAD_ZONE_MARGIN, 2.5)
        self.assertEqual(COOLDOWN_SECONDS, 0.30)
        self.assertEqual(TURN_END_MARGIN, 3.5)


class BargeInNoModelTests(unittest.TestCase):
    """The VAD stays energy plus silence: nothing here loads a model."""

    FORBIDDEN = {
        "numpy", "torch", "torchaudio", "onnx", "onnxruntime", "whisper",
        "faster_whisper", "pynvml", "transformers", "tensorflow", "scipy",
        "sklearn", "soundfile", "librosa", "silero", "onnx2torch",
    }

    def test_the_module_imports_no_ml_library(self) -> None:
        source = (BASE / "core" / "bargein.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0].lower())
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0].lower())
        self.assertEqual(
            imported & self.FORBIDDEN, set(),
            f"core/bargein.py must not import {imported & self.FORBIDDEN}")

    def test_the_module_only_imports_the_standard_library(self) -> None:
        source = (BASE / "core" / "bargein.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        self.assertLessEqual(imported, {"dataclasses", "typing", "__future__"})


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
