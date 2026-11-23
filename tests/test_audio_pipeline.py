"""
The audio path, tested where it can be tested: the devices, the voice, and the
text that is cut into speakable pieces.

The plan asked for these tests in the suite itself ("Los tests de audio, TTS
y vídeo de la FASE 2 tienen su sitio aquí, no en un sitio nuevo"), and the
reason is not tidiness. Three of the defects this chassis has already been
killed by were audio defects: a device mapping that sent the microphone to
the wrong rate, a voice that blocked the interface while it synthesised, and a
sentence that was cut in the wrong place and said twice.

What is tested here is the part that can be tested WITHOUT a sound card: the
rates, the mapping, the rules of punctuation and the failure paths. The parts
that need a real device (a probe of the room, a tone in the air) are exercised
through their own boundaries — a mocked device list — so that the SUITE does
not need speakers to be plugged in.
"""
from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch

from core import audio_devices
from providers import engine_pipeline, tts_system


def _fake_sounddevice(devices: list[dict], apis: list[dict]):
    """A sounddevice that answers with a list instead of with the hardware."""
    module = types.ModuleType("sounddevice")

    def query_devices():
        return list(devices)

    def query_hostapis():
        return list(apis)

    module.query_devices = query_devices
    module.query_hostapis = query_hostapis
    return module


class AudioDeviceTests(unittest.TestCase):
    """`core/audio_devices.py` — the mapping between names and devices."""

    def test_the_preferred_api_is_directsound_and_it_is_asked_first(self) -> None:
        """DirectSound before MME, because MME truncates the device names."""
        apis = audio_devices._PREFERRED_APIS["Windows"]
        self.assertEqual(apis[0], "directsound")
        self.assertIn("mme", apis)
        self.assertIn("wasapi", apis)

    def test_configure_sets_the_two_rates(self) -> None:
        """The microphone runs at one rate and the speakers at another."""
        audio_devices.configure(16000, 24000)
        self.assertEqual(audio_devices._RATES["input"], 16000)
        self.assertEqual(audio_devices._RATES["output"], 24000)

    def test_resolve_returns_none_for_the_system_default(self) -> None:
        """No device named means 'the system decides', not 'no microphone'."""
        self.assertIsNone(audio_devices.resolve("", "input"))
        self.assertIsNone(audio_devices.resolve("System default", "input"))

    def test_resolve_returns_none_for_a_device_that_is_gone(self) -> None:
        """A headset unplugged since last time must fall back, not fail."""
        fake = _fake_sounddevice(
            [{"name": "Mic A", "hostapi": 0,
              "max_input_channels": 2, "max_output_channels": 0}],
            [{"name": "DirectSound"}],
        )
        with patch.dict(sys.modules, {"sounddevice": fake}):
            self.assertIsNone(audio_devices.resolve("Headset B", "input"))

    def test_resolve_finds_the_device_that_is_there(self) -> None:
        """A device that IS present has to resolve to its index."""
        fake = _fake_sounddevice(
            [{"name": "Mic A", "hostapi": 0,
              "max_input_channels": 2, "max_output_channels": 0},
             {"name": "Speakers B", "hostapi": 0,
              "max_input_channels": 0, "max_output_channels": 2}],
            [{"name": "DirectSound"}],
        )
        with patch.dict(sys.modules, {"sounddevice": fake}), \
             patch.object(audio_devices, "_usable", return_value=True), \
             patch.object(audio_devices, "_transport_works", return_value=True):
            index = audio_devices.resolve("Mic A", "input")
        self.assertEqual(index, 0)

    def test_a_device_of_the_other_direction_is_not_offered(self) -> None:
        """A speaker is not a microphone, whatever the name says."""
        fake = _fake_sounddevice(
            [{"name": "Speakers B", "hostapi": 0,
              "max_input_channels": 0, "max_output_channels": 2}],
            [{"name": "DirectSound"}],
        )
        with patch.dict(sys.modules, {"sounddevice": fake}), \
             patch.object(audio_devices, "_usable", return_value=True), \
             patch.object(audio_devices, "_transport_works", return_value=True):
            index = audio_devices.resolve("Speakers B", "input")
        self.assertIsNone(index)

    def test_pseudo_devices_are_never_offered(self) -> None:
        """'Primary Sound Capture' and friends are not microphones."""
        for pseudo in audio_devices._PSEUDO_DEVICES:
            self.assertTrue(audio_devices._is_pseudo(pseudo), pseudo)

    def test_a_real_device_is_not_pseudo(self) -> None:
        self.assertFalse(
            audio_devices._is_pseudo("Microphone (Realtek High Definition Audio)"))


class SentenceChunkingTests(unittest.TestCase):
    """`providers/engine_pipeline.py` — the text that is said, piece by piece.

    The TTS is troceado por frases: each sentence is synthesised and played on
    its own, so the first one sounds before the whole answer has been
    generated. The punctuation rules are the delicate part, because they are
    not the same in every language.

    HOW THE BUFFER BEHAVES, and it is not an accident: a sentence is only
    released when the text AFTER it has arrived, because until then the model
    may still be writing it. The last piece therefore stays in the buffer,
    and the flush at the end of the turn speaks it.
    """

    def test_a_latin_sentence_needs_the_space_after_the_point(self) -> None:
        """After `.!?` the sentence only ends if a space follows."""
        done, rest = engine_pipeline._take_sentences("One. Two. Three.")
        self.assertEqual(done, ["One.", "Two."])
        self.assertEqual(rest, "Three.")

    def test_a_decimal_number_is_not_cut(self) -> None:
        """'3.14' has a point in it, and the space that follows is not after it."""
        done, rest = engine_pipeline._take_sentences("Pi is 3.14 and e is 2.718.")
        self.assertEqual(done, [])
        self.assertEqual(rest, "Pi is 3.14 and e is 2.718.")

    def test_a_point_with_no_space_after_it_does_not_end_anything(self) -> None:
        """The rule is about what FOLLOWS the point, not about the point."""
        done, rest = engine_pipeline._take_sentences("Version 2.0 is out.")
        self.assertEqual(done, [])
        self.assertEqual(rest, "Version 2.0 is out.")

    def test_the_point_needs_no_space_in_the_languages_that_write_it_without_one(self) -> None:
        """`。！？؟۔।` end the sentence on their own."""
        done, rest = engine_pipeline._take_sentences("第一句。第二句。")
        self.assertEqual(done, ["第一句。", "第二句。"])
        self.assertEqual(rest, "")

        done, rest = engine_pipeline._take_sentences("पहला।दूसरा।")
        self.assertEqual(done, ["पहला।", "दूसरा।"])
        self.assertEqual(rest, "")

    def test_an_ellipsis_is_a_boundary(self) -> None:
        """The ellipsis ends a sentence too."""
        done, rest = engine_pipeline._take_sentences("Wait... what?")
        self.assertEqual(done, ["Wait..."])
        self.assertEqual(rest, "what?")

    def test_a_long_buffer_without_punctuation_is_released_at_the_last_space(self) -> None:
        """A language that writes without points must not hold the text for ever."""
        text = "palabra " * 80
        done, rest = engine_pipeline._take_sentences(text)
        self.assertTrue(done)
        self.assertLessEqual(len(rest), 320)
        self.assertTrue(text.startswith(done[0]))

    def test_an_empty_buffer_yields_nothing(self) -> None:
        self.assertEqual(engine_pipeline._take_sentences(""), ([], ""))

    def test_the_unspeakable_is_removed_and_the_gaps_are_closed(self) -> None:
        """An emoji cannot be said, and it must not leave a hole in the sentence."""
        cleaned = engine_pipeline._speakable("Done \U0001f600 now")
        self.assertNotIn("\U0001f600", cleaned)
        self.assertIn("Done", cleaned)
        self.assertIn("now", cleaned)


class SystemVoiceTests(unittest.TestCase):
    """`providers/tts_system.py` — the voice that does not need the network."""

    def test_a_long_text_is_split_into_pieces_that_can_be_synthesised(self) -> None:
        """A very long paragraph cannot be handed to the voice in one go."""
        pieces = tts_system._split("word " * 400)
        self.assertGreater(len(pieces), 1)
        self.assertTrue(all(piece.strip() for piece in pieces))
        self.assertEqual(" ".join(pieces).split(), ("word " * 400).split())

    def test_a_short_text_is_left_alone(self) -> None:
        pieces = tts_system._split("A short line.")
        self.assertEqual(len(pieces), 1)
        self.assertIn("A short line.", pieces[0])

    def test_the_script_of_the_text_is_recognised(self) -> None:
        """The script decides the voice, and a silence is what a wrong one gives."""
        self.assertEqual(tts_system._script_of("Hello there"), "Latin")
        self.assertEqual(tts_system._script_of("Привет"), "Cyrillic")
        self.assertEqual(tts_system._script_of("你好"), "Han")
        self.assertEqual(tts_system._script_of("مرحبا"), "Arabic")
        self.assertEqual(tts_system._script_of(""), "Latin")

    def test_the_voice_declares_that_it_can_play_audio(self) -> None:
        """A voice that cannot be heard must say so in its capabilities."""
        voice = tts_system.SystemTTS({"voice": "", "rate": 0})
        self.assertTrue(voice.caps.get("audio_out"))


if __name__ == "__main__":
    unittest.main()
