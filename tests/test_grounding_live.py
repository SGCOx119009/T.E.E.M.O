"""FASE 6 — the three gaps the audit found, and the tests that close them.

`tests/test_grounding.py` proves the criteria of the phase. This file
proves the three things the criteria PROMISED but did not check:

  1. "an element hosted by another process inside our window is still
     ours" — the guard that used to be unable to tell the difference,
     because the element was built with no window handle at all.

  2. the coordinate route resolves against a LIVE measurement, and says
     so when it could not take one — the observation is up to a second
     and a half old, and a window that moved in that time is clicked in
     the wrong place.

  3. the model never sees a screen coordinate, which is still true after
     the elements started carrying window handles.

The tree here is a fake one, so that a test does not need a desktop and
a window to be true about.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from core.grounding import base, resolver, uia                       # noqa: E402
from core.grounding.base import Geometry                            # noqa: E402

# A window of the assistant's own — one of the handles the guard knows.
OUR_WINDOW = 987654
# Somebody else's window.
OTHER_WINDOW = 246810


# ─────────────────────────────────────────────────────────────────────────────
#  A fake accessibility tree
# ─────────────────────────────────────────────────────────────────────────────
class _Rect:
    def __init__(self, left, top, right, bottom):
        self.left, self.top = left, top
        self.right, self.bottom = right, bottom


class _Raw:
    """The native element, for the one property UIA answers directly."""

    def __init__(self, password=False, focused=False):
        self.CurrentIsPassword = password
        self.CurrentHasKeyboardFocus = focused


class _Node:
    """One UIA node, with just enough of it to be walked."""

    def __init__(self, name="", control_type="Button", rect=(0, 0, 10, 10),
                 children=(), pid=4242, handle=1, automation_id=""):
        self.name = name
        self.control_type = control_type
        self.rectangle = _Rect(*rect)
        self._children = list(children)
        self.process_id = pid
        self.handle = handle
        self.automation_id = automation_id
        self.visible = True
        self.enabled = True
        self.element = _Raw()

    def children(self):
        return list(self._children)

    def move(self, rect):
        """The window moved: the control is somewhere else now."""
        self.rectangle = _Rect(*rect)


def _tree():
    """A window of ANOTHER process, with a button inside it.

    The button's own handle is 11 and the window's is 10, so that the
    test can decide — with the root window it belongs to — whether the
    button lives in one of OUR windows or in somebody else's.
    """
    button = _Node("Send", "Button", (100, 200, 220, 240), pid=4242,
                   handle=11, automation_id="sendButton")
    root = _Node("Test window", "Window", (0, 0, 800, 600),
                 children=[button], pid=4242, handle=10)
    return root, button


class _FakeAct:
    """What the actuator returns, so that no test clicks anything."""

    ok = True
    detail = "clicked"
    error = ""


# ─────────────────────────────────────────────────────────────────────────────
#  1. "Ours" means where the element LIVES, not who drew it
# ─────────────────────────────────────────────────────────────────────────────
class OwnWindowGuardTests(unittest.TestCase):
    """The self-guard, with the window handle the element actually has.

    The rule: an element that belongs to THIS assistant — our own
    windows, including the controls of other processes hosted inside
    them — is not actionable. Everything else is.
    """

    def _observe(self, root, root_of):
        """Read the tree, with the assistant's own windows known."""
        with patch.object(uia, "_foreground_window",
                          return_value=(root, "Test window", 4242, 10)), \
             patch.object(uia, "own_handles", return_value={OUR_WINDOW}), \
             patch.object(uia, "_root_window", side_effect=root_of), \
             patch("core.screenview.physical_to_logical", return_value=1.0):
            return uia.observe(geometry=Geometry(size_capture=(1920, 1080)),
                               include_own=True)

    def test_a_control_of_another_process_inside_our_window_is_not_actionable(self):
        """The case the guard could not answer before.

        The button is drawn by another process — its pid is not ours —
        but it lives INSIDE one of our windows. Clicking it is clicking
        our own interface, so it is not something the assistant may do.
        """
        root, _button = _tree()

        def root_of(hwnd):
            # The button's handle belongs to a window whose root is
            # one of ours; the window we walked does not.
            return OUR_WINDOW if int(hwnd) == 11 else OTHER_WINDOW

        result = self._observe(root, root_of)
        marks = {m.name: m for m in result.marks}
        self.assertIn("Send", marks, "the button was not even offered")
        self.assertTrue(marks["Send"].own,
                        "a control inside our own window was treated as "
                        "somebody else's")
        self.assertFalse(marks["Send"].actionable,
                         "the assistant may act on its own window")

    def test_a_control_of_another_process_in_another_window_is_actionable(self):
        """The other half of the rule: theirs is theirs."""
        root, _button = _tree()

        def root_of(_hwnd):
            return OTHER_WINDOW

        result = self._observe(root, root_of)
        marks = {m.name: m for m in result.marks}
        self.assertIn("Send", marks)
        self.assertFalse(marks["Send"].own)
        self.assertTrue(marks["Send"].actionable,
                        "a control in somebody else's window is fair game")

    def test_a_control_of_our_own_process_is_still_not_actionable(self):
        """The rule that was already true, and still is."""
        root, button = _tree()
        button.process_id = uia.os.getpid()

        def root_of(_hwnd):
            return OTHER_WINDOW

        result = self._observe(root, root_of)
        marks = {m.name: m for m in result.marks}
        self.assertTrue(marks["Send"].own)
        self.assertFalse(marks["Send"].actionable)

    def test_the_element_carries_the_handle_it_was_decided_by(self):
        """The decision is only possible if the handle is carried."""
        root, _button = _tree()

        def root_of(hwnd):
            return OUR_WINDOW if int(hwnd) == 11 else OTHER_WINDOW

        result = self._observe(root, root_of)
        marks = {m.name: m for m in result.marks}
        self.assertEqual(marks["Send"].hwnd, 11)
        self.assertEqual(marks["Send"].pid, 4242)


# ─────────────────────────────────────────────────────────────────────────────
#  2. The coordinate route measures the element AGAIN
# ─────────────────────────────────────────────────────────────────────────────
class LiveResolutionTests(unittest.TestCase):
    """The point is computed at the moment of the action, not before.

    The observation is cached for up to a second and a half. A window
    that moved in that time must not be clicked where it USED to be —
    and when the element cannot be measured again, the answer has to
    say that it fell back.
    """

    def _observed(self, root):
        with patch.object(uia, "_foreground_window",
                          return_value=(root, "Test window", 4242, 10)), \
             patch.object(uia, "own_handles", return_value=set()), \
             patch.object(uia, "_root_window", return_value=OTHER_WINDOW), \
             patch("core.screenview.physical_to_logical", return_value=1.0):
            return uia.observe(geometry=Geometry(size_capture=(1920, 1080)))

    def _resolver(self, result):
        res = resolver.GroundingResolver()
        res._result = ("key", result)
        return res

    def _index_of(self, result, name):
        """The number the model was given for this element."""
        for mark in result.marks:
            if mark.name == name:
                return mark.index
        self.fail(f"there is no element named {name}")

    def test_the_route_measures_the_element_again(self):
        """The window moves between the observation and the action."""
        root, button = _tree()
        result = self._observed(root)
        res = self._resolver(result)
        index = self._index_of(result, "Send")

        # The model was shown a button at (100,200). The window moved.
        button.move((500, 600, 620, 640))

        with patch.object(uia, "_foreground_window",
                          return_value=(root, "Test window", 4242, 10)), \
             patch("core.screenview.physical_to_logical", return_value=1.0):
            detail = res.resolve_detail(index)

        self.assertEqual(detail["source"], "live",
                         "the point was taken from the stale observation")
        self.assertEqual(detail["point"], (560, 620),
                         "the click would land where the button USED to be")
        self.assertNotEqual(detail["point"], (160, 220))

    def test_the_route_says_when_it_could_not_measure_the_element(self):
        """No live measurement: the old rectangle, and the truth about it."""
        root, _button = _tree()
        result = self._observed(root)
        res = self._resolver(result)
        index = self._index_of(result, "Send")

        with patch.object(uia, "live_rect",
                          return_value=(None, "the control is no longer "
                                             "in the tree")):
            detail = res.resolve_detail(index)

        self.assertEqual(detail["source"], "cached")
        self.assertEqual(detail["point"], (160, 220))
        self.assertIn("no longer in the tree", detail["reason"])

    def test_the_action_says_where_its_point_came_from(self):
        """The answer travels back with the action."""
        root, _button = _tree()
        result = self._observed(root)
        res = self._resolver(result)
        index = self._index_of(result, "Send")

        with patch.object(uia, "invoke",
                          return_value={"status": "failed", "verified": False,
                                        "reason": "no"}), \
             patch.object(uia, "live_rect",
                          return_value=((500, 600, 620, 640),
                                        "measured just now")), \
             patch("core.screenview.physical_to_logical", return_value=1.0), \
             patch("core.screenact.click", return_value=_FakeAct()):
            outcome = res.act(index)

        self.assertEqual(outcome["origin"], "point")
        self.assertEqual(outcome["point_source"], "live")
        self.assertEqual(outcome["point"], [560, 620])

    def test_an_element_without_a_selector_is_reported_as_measured_or_not(self):
        """The flag is there even when there is nothing to measure."""
        root, _button = _tree()
        result = self._observed(root)
        res = self._resolver(result)
        index = self._index_of(result, "Send")

        with patch.object(uia, "live_rect",
                          return_value=(None, "this element has no selector "
                                             "to look it up with")):
            detail = res.resolve_detail(index)

        self.assertEqual(detail["source"], "cached")
        self.assertIn("no selector", detail["reason"])

    def test_an_unknown_index_resolves_to_nothing(self):
        root, _button = _tree()
        result = self._observed(root)
        res = self._resolver(result)
        detail = res.resolve_detail(99)
        self.assertIsNone(detail["point"])
        self.assertEqual(detail["source"], "none")


# ─────────────────────────────────────────────────────────────────────────────
#  3. The model still never receives a screen coordinate
# ─────────────────────────────────────────────────────────────────────────────
class ModelSafetyTests(unittest.TestCase):
    def test_the_window_handle_never_reaches_the_model(self):
        """The elements carry handles now. The model must not see them."""
        root, _button = _tree()
        with patch.object(uia, "_foreground_window",
                          return_value=(root, "Test window", 4242, 10)), \
             patch.object(uia, "own_handles", return_value={OUR_WINDOW}), \
             patch.object(uia, "_root_window", return_value=OTHER_WINDOW), \
             patch("core.screenview.physical_to_logical", return_value=1.0):
            result = uia.observe(geometry=Geometry(size_capture=(1920, 1080)),
                                 include_own=True)

        self.assertNotIn(str(OUR_WINDOW), result.observation)
        self.assertTrue(base.observation_is_model_safe(result))


if __name__ == "__main__":
    unittest.main()
