"""
The affective state, as the product sees it: the tool that sets it, the prompt
that carries it and the policy that has to know the tool exists.

These are the tests for the SEAM between the pieces, because each piece has
its own tests and the seam is where a machine that works in three places and
is wrong in one of them shows up: a tool the policy refuses, a prompt token
nobody fills, a state that changes the colour but not the voice.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from actions import emotional_spectrum as tool
from core import emotional_spectrum as spectrum
from core import tool_audit, tool_loader

REPO = Path(__file__).resolve().parents[1]


class _FakePlayer:
    """The interface, as the tool is allowed to see it: a method and a call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def set_emotional_state(self, name: str, prompt: str = "", colour: str = "") -> None:
        self.calls.append((name, prompt, colour))


class EmotionalSpectrumToolTests(unittest.TestCase):
    def setUp(self) -> None:
        spectrum.reset_state()
        self.player = _FakePlayer()

    def tearDown(self) -> None:
        spectrum.reset_state()

    # ── the declaration ──────────────────────────────────────────────────────

    def test_the_declaration_is_valid(self) -> None:
        """A tool the loader refuses is a tool the model never sees."""
        record = tool_loader.validate_declaration(
            tool.TOOL, "actions/emotional_spectrum.py", "action",
            handler=tool.TOOL.get("handler"), require_callable=True,
        )
        self.assertTrue(record.valid, record.error)
        self.assertEqual(record.name, "emotional_spectrum")
        self.assertEqual(record.parameters["type"], "OBJECT")
        self.assertEqual(record.parameters["required"], ["state"])

    def test_the_declaration_carries_its_handler(self) -> None:
        """A declaration without a handler is a tool that VANISHES.

        The loader reads the handler out of the TOOL dict. Without that key
        the declaration is still read out of the source and the tool is even
        offered to the model — and then it disappears from the catalogue when
        the modules are warmed up, and the model is told "the requested
        action is not available". A tool that can be asked for and cannot be
        called is worse than one that was never there.
        """
        self.assertIs(tool.TOOL.get("handler"), tool.run)

    def test_the_loader_discovers_the_tool(self) -> None:
        """And it is discovered, bound and callable, from the real directory."""
        from pathlib import Path

        from core.action_loader import discover_actions

        registry = discover_actions(
            Path(__file__).resolve().parents[1] / "actions",
            logger=lambda _message: None,
            eager=False,
        )
        record = registry._actions.get("emotional_spectrum")
        self.assertIsNotNone(record, "the tool was not discovered")
        self.assertTrue(record.valid, record.error)
        self.assertIsNotNone(record.resolve())

    def test_the_description_names_every_state(self) -> None:
        """The model picks the state by name, so the names must be in the text."""
        description = tool.TOOL["description"]
        for name in spectrum.STATE_NAMES:
            self.assertIn(name, description)

    def test_the_tool_is_not_unclassified(self) -> None:
        """An unclassified tool is refused by the policy before it ever runs."""
        risk = tool_audit.classify_risk("emotional_spectrum", {"state": "WITTY"})
        self.assertNotEqual(risk, "unclassified")

    # ── the behaviour ────────────────────────────────────────────────────────

    def test_setting_a_state_changes_it_and_tells_the_interface(self) -> None:
        result = tool.run({"state": "SKEPTICAL"}, player=self.player)
        self.assertIn("status=done", result)
        self.assertEqual(spectrum.current_state().name, "SKEPTICAL")
        self.assertEqual(len(self.player.calls), 1)
        name, prompt, colour = self.player.calls[0]
        self.assertEqual(name, "SKEPTICAL")
        self.assertTrue(prompt.strip())
        self.assertEqual(colour, spectrum.STATES["SKEPTICAL"].color)

    def test_the_state_name_is_read_without_regard_to_case_or_spaces(self) -> None:
        tool.run({"state": "  empathetic "}, player=self.player)
        self.assertEqual(spectrum.current_state().name, "EMPATHETIC")

    def test_an_unknown_state_is_refused_and_changes_nothing(self) -> None:
        before = spectrum.current_state().name
        result = tool.run({"state": "SASSY"}, player=self.player)
        self.assertIn("status=failed", result)
        self.assertEqual(spectrum.current_state().name, before)
        self.assertEqual(self.player.calls, [])

    def test_a_missing_state_is_refused(self) -> None:
        result = tool.run({}, player=self.player)
        self.assertIn("status=failed", result)
        self.assertEqual(self.player.calls, [])

    def test_the_intensity_blends_the_colour(self) -> None:
        """'A little more sceptical' is not the same as 'sceptical'."""
        spectrum.set_state("TACTICAL")
        tool.run({"state": "EMPATHETIC", "intensity": 0.5}, player=self.player)
        _, _, colour = self.player.calls[0]
        self.assertNotEqual(colour, spectrum.STATES["EMPATHETIC"].color)
        self.assertNotEqual(colour, spectrum.STATES["TACTICAL"].color)

    def test_the_tool_works_without_an_interface(self) -> None:
        """Headless and in the tests there is no player, and that is fine."""
        result = tool.run({"state": "WITTY"}, player=None)
        self.assertIn("status=done", result)
        self.assertEqual(spectrum.current_state().name, "WITTY")

    def test_a_broken_interface_does_not_cost_the_state(self) -> None:
        """The mood is the point; the colour is decoration."""
        class _Broken:
            def set_emotional_state(self, *args, **kwargs):
                raise RuntimeError("the interface is gone")

        result = tool.run({"state": "VIGILANT"}, player=_Broken())
        self.assertIn("status=done", result)
        self.assertEqual(spectrum.current_state().name, "VIGILANT")

    # ── the seam with the prompt ─────────────────────────────────────────────

    def test_the_prompt_carries_the_tone_token(self) -> None:
        """A token nobody fills is a tone nobody applies."""
        prompt = (REPO / "core" / "prompt.txt").read_text(encoding="utf-8")
        self.assertIn("{emotional_state}", prompt)

    def test_the_tone_line_is_not_empty(self) -> None:
        import main

        line = main._emotional_state_line()
        self.assertTrue(line.strip())
        self.assertIn(spectrum.current_state().name, line.upper())

    def test_the_prompt_knows_how_to_take_a_new_state(self) -> None:
        """The mid-session message needs the tag the prompt explains."""
        prompt = (REPO / "core" / "prompt.txt").read_text(encoding="utf-8")
        self.assertIn("[TONE_CHANGED]", prompt)

    def test_the_action_never_touches_qt(self) -> None:
        """Tool handlers run on worker threads; a widget there is a crash."""
        source = (REPO / "actions" / "emotional_spectrum.py").read_text(encoding="utf-8")
        self.assertNotIn("PyQt6", source)
        self.assertNotIn("QWidget", source)
        self.assertIsNone(re.search(r"^\s*from PyQt6", source, re.MULTILINE))


if __name__ == "__main__":
    unittest.main()
