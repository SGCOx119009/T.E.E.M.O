"""Capability negotiation: what the engine can do AND what the client accepts.

The rule under test is one sentence long: a client that cannot play audio
does not receive audio. The rest is the machinery around it — the
intersection is symmetric, its order is stable, and the facts dict every
provider already declares keeps working exactly as it did.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from providers import base
from providers.base import (
    CAPABILITIES,
    CAP_AUDIO_INPUT,
    CAP_AUDIO_OUTPUT,
    CAP_BARGE_IN,
    CAP_INTERRUPT,
    CAP_TEXT_STREAM,
    CAP_TOOLS,
    CAP_VIDEO_OUTPUT,
    CLIENT_CAPS_FULL,
    DEFAULT_CAPS,
    NegotiatedCaps,
    allows_audio,
    capabilities_of,
    client_caps,
    negotiate,
    negotiate_capabilities,
    supports,
)


class _DummyLLM:
    caps = {"tools": False, "tool_format": None, "vision": False}


class _DummyTTS:
    caps = {"audio_out": True}


class CapabilityNegotiationTests(unittest.TestCase):
    def test_the_intersection_is_the_answer(self) -> None:
        server = client_caps(CAP_AUDIO_OUTPUT, CAP_TEXT_STREAM, CAP_TOOLS)
        client = client_caps(CAP_AUDIO_OUTPUT, CAP_TOOLS, CAP_BARGE_IN)
        self.assertEqual(
            negotiate_capabilities(server, client),
            [CAP_AUDIO_OUTPUT, CAP_TOOLS])

    def test_the_intersection_is_symmetric(self) -> None:
        server = client_caps(CAP_AUDIO_OUTPUT, CAP_TEXT_STREAM, CAP_TOOLS)
        client = client_caps(CAP_AUDIO_OUTPUT, CAP_TOOLS, CAP_BARGE_IN)
        self.assertEqual(negotiate_capabilities(server, client),
                         negotiate_capabilities(client, server))

    def test_the_order_is_stable(self) -> None:
        server = client_caps(CAP_TOOLS, CAP_AUDIO_OUTPUT, CAP_TEXT_STREAM)
        client = client_caps(CAP_TEXT_STREAM, CAP_TOOLS, CAP_AUDIO_OUTPUT)
        once = negotiate_capabilities(server, client)
        twice = negotiate_capabilities(server, client)
        self.assertEqual(once, twice)
        # The canonical order, not the order the names were written in.
        self.assertEqual(once,
                         [c for c in CAPABILITIES if c in set(once)])

    def test_a_client_without_audio_output_gets_no_audio(self) -> None:
        """The rule: no speaker, no voice."""
        engine_caps = {"audio_out": True, "tools": True, "vision": False}
        client = client_caps(CAP_AUDIO_INPUT, CAP_TEXT_STREAM, CAP_TOOLS)
        allowed = negotiate_capabilities(engine_caps, client)
        self.assertNotIn(CAP_AUDIO_OUTPUT, allowed)
        self.assertFalse(allows_audio(allowed))

    def test_a_client_with_audio_output_gets_audio(self) -> None:
        engine_caps = {"audio_out": True, "tools": True, "vision": False}
        client = client_caps(CAP_AUDIO_OUTPUT, CAP_TEXT_STREAM)
        allowed = negotiate_capabilities(engine_caps, client)
        self.assertIn(CAP_AUDIO_OUTPUT, allowed)
        self.assertTrue(allows_audio(allowed))

    def test_an_engine_without_audio_cannot_give_audio_either(self) -> None:
        engine_caps = {"audio_out": False, "tools": True}
        client = client_caps(CAP_AUDIO_OUTPUT, CAP_TEXT_STREAM)
        allowed = negotiate_capabilities(engine_caps, client)
        self.assertNotIn(CAP_AUDIO_OUTPUT, allowed)


class CapabilityFactsTests(unittest.TestCase):
    """The facts dict the providers already declare keeps its meaning."""

    def test_the_facts_map_onto_names(self) -> None:
        names = capabilities_of({"audio_out": True, "tools": True,
                                  "vision": True, "barge_in": True})
        self.assertIn(CAP_AUDIO_OUTPUT, names)
        self.assertIn(CAP_TOOLS, names)
        self.assertIn(CAP_VIDEO_OUTPUT, names)
        self.assertIn(CAP_BARGE_IN, names)

    def test_a_fact_that_is_false_grants_nothing(self) -> None:
        names = capabilities_of({"audio_out": False, "tools": False})
        self.assertEqual(names, ())

    def test_supports_works_with_the_engines_caps(self) -> None:
        engine_caps = {"audio_out": True, "tools": True, "vision": False}
        self.assertTrue(supports(engine_caps, CAP_AUDIO_OUTPUT))
        self.assertTrue(supports(engine_caps, CAP_TOOLS))
        self.assertFalse(supports(engine_caps, CAP_VIDEO_OUTPUT))

    def test_supports_works_with_a_negotiated_list(self) -> None:
        allowed = negotiate_capabilities({"audio_out": True},
                                         client_caps(CAP_AUDIO_OUTPUT))
        self.assertTrue(supports(allowed, CAP_AUDIO_OUTPUT))
        self.assertFalse(supports(allowed, CAP_INTERRUPT))

    def test_the_caps_fact_dict_is_untouched(self) -> None:
        """`caps` stays what the providers declare today."""
        decl = {"id": "x", "kind": "llm", "label": "X",
                "caps": {"tools": True, "tool_format": "openai"}}
        caps = base.caps_of(decl)
        for key in DEFAULT_CAPS:
            self.assertIn(key, caps)
        self.assertTrue(caps["tools"])
        self.assertEqual(caps["tool_format"], "openai")


class CapabilityDeclarationTests(unittest.TestCase):
    def test_a_client_declares_its_capabilities(self) -> None:
        declared = client_caps(CAP_AUDIO_INPUT, CAP_TEXT_STREAM)
        self.assertEqual(declared, (CAP_AUDIO_INPUT, CAP_TEXT_STREAM))

    def test_an_unknown_capability_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            client_caps("telepathy")

    def test_the_desktop_client_supports_everything(self) -> None:
        self.assertEqual(set(CLIENT_CAPS_FULL), set(CAPABILITIES))

    def test_the_negotiated_object_answers_the_same_questions(self) -> None:
        neg = negotiate({"audio_out": True, "tools": True},
                        client_caps(CAP_AUDIO_OUTPUT, CAP_TEXT_STREAM))
        self.assertIsInstance(neg, NegotiatedCaps)
        self.assertTrue(neg.supports(CAP_AUDIO_OUTPUT))
        self.assertFalse(neg.supports(CAP_INTERRUPT))
        self.assertTrue(neg.allows_audio())
        self.assertEqual(neg.as_list(), list(neg.allowed))
        self.assertEqual(neg.as_list(), negotiate_capabilities(
            {"audio_out": True, "tools": True},
            client_caps(CAP_AUDIO_OUTPUT, CAP_TEXT_STREAM)))


class BaseEngineCapabilityTests(unittest.TestCase):
    """The base engine declares `caps`, and it is a real dict."""

    def test_the_engine_contract_declares_caps(self) -> None:
        self.assertIn("caps", base.Engine.__annotations__)
        self.assertIn("caps", base.LLM.__annotations__)
        self.assertIn("caps", base.STT.__annotations__)
        self.assertIn("caps", base.TTS.__annotations__)

    def test_the_base_engine_declares_caps(self) -> None:
        from providers.engine_pipeline import PipelineEngine

        engine = PipelineEngine(_DummyLLM(), stt=None, tts=_DummyTTS())
        self.assertIsNotNone(engine.caps)
        self.assertIsInstance(engine.caps, dict)
        for key in DEFAULT_CAPS:
            self.assertIn(key, engine.caps)
        self.assertTrue(engine.caps["audio_out"])
        self.assertIn(CAP_AUDIO_OUTPUT, capabilities_of(engine.caps))

    def test_an_engine_without_a_tts_declares_no_audio(self) -> None:
        from providers.engine_pipeline import PipelineEngine

        engine = PipelineEngine(_DummyLLM(), stt=None, tts=None)
        self.assertFalse(engine.caps["audio_out"])
        self.assertNotIn(CAP_AUDIO_OUTPUT, capabilities_of(engine.caps))
        self.assertFalse(allows_audio(engine.caps))


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
