"""
The video path, and the risk classification that covers it.

The plan left a note about this: "`test_tool_audit.py` ya avisa de que las
acciones de `video_player` aún no tienen clasificación de riesgo". They do now
— the transport is a local state change, not a destructive act — and these
tests hold that line, because an action with no classification is an action
the policy REFUSES before it ever runs, and twenty plan steps were dying
that way once already.

The rest of the file tests the part that decides what the user meant: a path
is a path, a name is a name, and a point in a film is a point and not a
number of minutes.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from actions import video_player
from core import tool_audit


class VideoSourceTests(unittest.TestCase):
    """`actions/video_player.py` — telling a file from a search."""

    def test_an_address_is_an_address(self) -> None:
        self.assertTrue(video_player._is_url("https://example.test/clip.mp4"))
        self.assertTrue(video_player._is_url("HTTP://EXAMPLE.TEST/clip.mp4"))
        self.assertTrue(video_player._is_url("file:///C:/clip.mp4"))
        self.assertFalse(video_player._is_url("holiday.mp4"))

    def test_a_file_that_exists_is_a_path(self) -> None:
        with TemporaryDirectory() as temp:
            clip = Path(temp) / "clip.mp4"
            clip.write_bytes(b"\x00")
            resolved = video_player._local_path(str(clip))
            self.assertTrue(resolved)
            self.assertTrue(Path(resolved).is_file())

    def test_a_file_that_does_not_exist_is_not_a_path(self) -> None:
        """Nothing is invented: a file that is not there is not there."""
        self.assertEqual(video_player._local_path("C:/nothing/here.mp4"), "")

    def test_a_name_with_a_media_extension_looks_like_a_file(self) -> None:
        """'holiday.mp4' is a file even when it is not on this disk yet."""
        self.assertTrue(video_player._looks_like_file("holiday.mp4"))
        self.assertTrue(video_player._looks_like_file("C:\\videos\\clip.MKV"))
        self.assertTrue(video_player._looks_like_file("photos/shot.jpeg"))

    def test_a_phrase_is_a_search_and_not_a_file(self) -> None:
        """Words are what YouTube is for; a separator is what a file has."""
        self.assertFalse(video_player._looks_like_file("how to bake bread"))
        self.assertFalse(video_player._looks_like_file("https://example.test/x"))

    def test_a_url_is_not_a_local_file(self) -> None:
        self.assertFalse(video_player._looks_like_file("https://example.test/clip.mp4"))


class VideoTimeTests(unittest.TestCase):
    """A point in a film, written the way people write it."""

    def test_the_ways_of_writing_a_point(self) -> None:
        self.assertEqual(video_player._seconds(90), 90.0)
        self.assertEqual(video_player._seconds("90"), 90.0)
        self.assertEqual(video_player._seconds("1:30"), 90.0)
        self.assertEqual(video_player._seconds("01:02:03"), 3723.0)
        self.assertEqual(video_player._seconds("90 seconds"), 90.0)

    def test_something_that_is_not_a_point_is_refused(self) -> None:
        """A point that cannot be read is no point at all."""
        self.assertIsNone(video_player._seconds(None))
        self.assertIsNone(video_player._seconds(True))
        self.assertIsNone(video_player._seconds("somewhere"))
        self.assertIsNone(video_player._seconds("a:b:c"))
        self.assertIsNone(video_player._seconds("at 1:30"))

    def test_the_clock_reads_back_what_the_point_wrote(self) -> None:
        self.assertEqual(video_player._clock(90), "1:30")
        self.assertEqual(video_player._clock(3723), "1:02:03")
        self.assertEqual(video_player._clock(-5), "0:00")


class VideoRiskTests(unittest.TestCase):
    """The classification the policy reads. An unclassified action is refused."""

    def test_every_video_action_is_classified(self) -> None:
        for action in ("play", "stop", "pause", "resume", "mute", "unmute",
                       "forward", "back", "seek_to"):
            risk = tool_audit.classify_risk("video_player", {"action": action})
            self.assertNotEqual(risk, "unclassified", action)

    def test_the_transport_is_a_local_state_change(self) -> None:
        """Playing a film changes nothing outside this machine."""
        for action in ("play", "pause", "seek_to", "forward"):
            risk = tool_audit.classify_risk("video_player", {"action": action})
            self.assertEqual(risk, "state_change", action)

    def test_the_tool_is_reviewed(self) -> None:
        self.assertTrue(tool_audit.is_reviewed_tool("video_player"))


if __name__ == "__main__":
    unittest.main()
