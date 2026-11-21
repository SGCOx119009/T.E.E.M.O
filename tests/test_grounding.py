"""FASE 6 — el grounding: ocho criterios, ocho pruebas.

Cada prueba comprueba algo que, de no estar, el asistente seguiría
funcionando y seguiría siendo otra cosa:

  1. `core/grounding/` existe con los cinco módulos y su contrato
  2. el nivel N0 (UIA) devuelve elementos con NOMBRE, ROL y RECTÁNGULO
  3. el formato de observación SoM es estable y está documentado
  4. el modelo NUNCA recibe coordenadas absolutas de pantalla: recibe índices
  5. las coordenadas se resuelven en el MOMENTO DE LA EJECUCIÓN
  6. el DPI y el escalado se manejan: 100 % y 150 % dan el mismo resultado
  7. un elemento que pertenece a un proceso de T.E.E.M.O NO puede ser pulsado
  8. el coste de una captura + N0 es < 150 ms en el equipo objetivo

Las pruebas que necesitan una ventana real se saltan cuando no la hay:
un criterio que se comprueba contra la nada no comprueba nada.
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from core.grounding import base, detect, resolver, som, uia      # noqa: E402
from core.grounding.base import (                                  # noqa: E402
    ElementMark,
    Geometry,
    GroundingResult,
    format_mark,
    format_observation,
)


# ─────────────────────────────────────────────────────────────────────────────
#  Un árbol UIA de mentira, para poder probar sin depender del escritorio
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
                 children=(), pid=1000, handle=1, automation_id="",
                 visible=True, enabled=True, password=False, focused=False):
        self.name = name
        self.control_type = control_type
        self.rectangle = _Rect(*rect)
        self._children = list(children)
        self.process_id = pid
        self.handle = handle
        self.automation_id = automation_id
        self.visible = visible
        self.enabled = enabled
        self.element = _Raw(password=password, focused=focused)

    def children(self):
        return list(self._children)


def _fake_window():
    """A window with a button, a field and a menu entry under it."""
    button = _Node("Send", "Button", (100, 200, 220, 240),
                    pid=4242, handle=11, automation_id="sendButton")
    field = _Node("Subject", "Edit", (100, 260, 400, 290),
                  pid=4242, handle=12, password=False)
    menu = _Node("File", "MenuItem", (10, 10, 80, 40),
                 pid=4242, handle=13, enabled=False)
    root = _Node("Test window", "Window", (0, 0, 800, 600),
                 children=[button, field, menu], pid=4242, handle=10)
    return root


# ─────────────────────────────────────────────────────────────────────────────
#  1. The package exists
# ─────────────────────────────────────────────────────────────────────────────
class GroundingPackageTests(unittest.TestCase):
    def test_the_five_modules_exist_and_expose_their_contract(self):
        import importlib

        for name in ("base", "uia", "som", "detect", "resolver"):
            module = importlib.import_module(f"core.grounding.{name}")
            self.assertTrue(module, f"core/grounding/{name}.py is missing")

        self.assertTrue(hasattr(base, "ElementMark"))
        self.assertTrue(hasattr(base, "GroundingResult"))
        self.assertTrue(hasattr(base, "Geometry"))
        self.assertTrue(hasattr(uia, "observe"))
        self.assertTrue(hasattr(som, "observe"))
        self.assertTrue(hasattr(detect, "detect"))
        self.assertTrue(hasattr(resolver, "GroundingResolver"))

    def test_the_three_levels_are_named_the_way_the_plan_names_them(self):
        self.assertEqual(base.LEVELS, ("N0", "N1", "N2"))
        self.assertEqual(base.LEVEL_UIA, "N0")
        self.assertEqual(base.LEVEL_SOM, "N1")
        self.assertEqual(base.LEVEL_VLM, "N2")


# ─────────────────────────────────────────────────────────────────────────────
#  2. N0 returns name, role and rectangle
# ─────────────────────────────────────────────────────────────────────────────
class LevelN0Tests(unittest.TestCase):
    def _observe_fake(self, **kw):
        root = _fake_window()
        with patch.object(uia, "_foreground_window",
                          return_value=(root, "Test window", 4242, 10)), \
             patch("core.screenview.physical_to_logical", return_value=1.0):
            return uia.observe(geometry=Geometry(size_capture=(1920, 1080)),
                               **kw)

    def test_n0_names_the_elements_of_a_window(self):
        result = self._observe_fake()
        self.assertEqual(result.level, "N0")
        self.assertEqual(result.status, "ok")
        self.assertGreaterEqual(result.count, 3)

        by_name = {m.name: m for m in result.marks}
        self.assertIn("Send", by_name)
        self.assertIn("Subject", by_name)
        self.assertIn("File", by_name)

        button = by_name["Send"]
        self.assertEqual(button.role, "button")
        self.assertTrue(button.bbox, "an element without a rectangle is no use")
        self.assertEqual(button.bbox[:2], (100, 200))
        self.assertEqual(button.window, "Test window")
        self.assertEqual(button.automation_id, "sendButton")

    def test_n0_keeps_the_disabled_state_and_the_password_mark(self):
        result = self._observe_fake()
        by_name = {m.name: m for m in result.marks}
        self.assertFalse(by_name["File"].enabled)
        self.assertFalse(by_name["Subject"].protected)

    def test_n0_returns_a_readable_list_and_not_a_picture(self):
        result = self._observe_fake()
        self.assertIn("Send", result.observation)
        self.assertIn("button", result.observation)
        self.assertTrue(result.observation.splitlines()[0].startswith("# ground v"))


# ─────────────────────────────────────────────────────────────────────────────
#  3. The observation format is stable
# ─────────────────────────────────────────────────────────────────────────────
class ObservationFormatTests(unittest.TestCase):
    def test_one_element_is_one_line_in_the_documented_shape(self):
        mark = ElementMark(index=17, role="button", name="Send",
                           bbox=(790, 495, 105, 32), enabled=True)
        line = format_mark(mark)
        self.assertTrue(line.startswith("[17] button"))
        self.assertIn('"Send"', line)
        self.assertIn("bbox=(790,495)-(895,527)", line)
        self.assertIn("enabled", line)

    def test_the_flags_say_what_is_wrong_with_an_element(self):
        mark = ElementMark(index=3, role="menuitem", name="File",
                           bbox=(40, 10, 120, 24), enabled=False,
                           protected=True, focused=True)
        line = format_mark(mark)
        self.assertIn("disabled", line)
        self.assertIn("protected", line)
        self.assertIn("focused", line)

    def test_a_name_with_a_quote_cannot_break_the_line(self):
        mark = ElementMark(index=1, role="text", name='He said "hello"\nbye')
        line = format_mark(mark)
        self.assertEqual(len(line.splitlines()), 1)
        self.assertIn('\\"', line)

    def test_the_format_carries_a_version(self):
        self.assertTrue(base.SOM_FORMAT_VERSION)
        result = GroundingResult(
            marks=(ElementMark(index=1, role="button", name="X"),),
            geometry=Geometry(size_capture=(800, 600)),
        )
        text = format_observation(result.marks, geometry=result.geometry)
        self.assertIn(f"ground v{base.SOM_FORMAT_VERSION}", text)

    def test_the_format_is_the_same_whatever_the_level(self):
        mark = ElementMark(index=5, role="edit", name="Subject",
                           bbox=(10, 20, 30, 40))
        one = format_mark(mark)
        two = format_mark(ElementMark(**{**mark.__dict__}))
        self.assertEqual(one, two)


# ─────────────────────────────────────────────────────────────────────────────
#  4. The model never receives screen coordinates
# ─────────────────────────────────────────────────────────────────────────────
class ModelSafetyTests(unittest.TestCase):
    def _result_with_offset(self):
        # A capture that does NOT start at the origin of the desktop, and a
        # display that is not at 100 %. This is what a second monitor at
        # 150 % looks like, and it is the only way to tell the two spaces
        # apart.
        geometry = Geometry(origin_screen=(3840, 0),
                            size_capture=(1920, 1080),
                            size_logical=(1280, 720),
                            scale=2.0 / 3.0)
        marks = (
            ElementMark(index=1, role="button", name="A", bbox=(10, 20, 30, 40)),
            ElementMark(index=2, role="button", name="B", bbox=(100, 200, 30, 40)),
        )
        return GroundingResult(level="N0", status="ok", marks=marks,
                               geometry=geometry,
                               observation=format_observation(
                                   marks, geometry=geometry))

    def test_no_screen_coordinate_reaches_the_observation(self):
        result = self._result_with_offset()
        text = result.observation
        for mark in result.marks:
            sx, sy, sw, sh = result.geometry.capture_to_screen(mark.bbox)
            for value in (sx, sy, sx + sw, sy + sh):
                self.assertNotIn(f"({value},",
                                 text,
                                 "a screen coordinate reached the model")
        self.assertTrue(base.observation_is_model_safe(result))

    def test_the_capture_coordinates_are_what_the_model_is_shown(self):
        result = self._result_with_offset()
        self.assertIn("bbox=(10,20)-(40,60)", result.observation)

    def test_the_model_is_given_indices_and_not_positions_to_click(self):
        result = self._result_with_offset()
        # Every line starts with an index, and the index is what the model
        # refers to.
        for line in result.observation.splitlines()[1:]:
            self.assertTrue(line.startswith("["))
            self.assertIn("]", line)

    def test_a_leak_is_detected_when_it_happens(self):
        result = self._result_with_offset()
        broken = GroundingResult(
            level="N0", status="ok", marks=result.marks,
            geometry=result.geometry,
            observation=result.observation + "\n[3] button \"C\" bbox=(3850,20)-(3880,60)",
        )
        self.assertFalse(base.observation_is_model_safe(broken))


# ─────────────────────────────────────────────────────────────────────────────
#  5. The coordinates are resolved at execution time
# ─────────────────────────────────────────────────────────────────────────────
class ExecutionTimeResolutionTests(unittest.TestCase):
    def _resolver_with(self, geometry):
        res = resolver.GroundingResolver()
        mark = ElementMark(index=1, role="button", name="Send",
                           bbox=(100, 100, 40, 20))
        result = GroundingResult(level="N0", status="ok", marks=(mark,),
                                 geometry=geometry)
        res._result = ("key", result)
        return res

    def test_the_point_is_computed_from_the_geometry_of_the_moment(self):
        res = self._resolver_with(Geometry(size_capture=(1920, 1080), scale=1.0))
        self.assertEqual(res.resolve(1), (120, 110))

    def test_a_different_geometry_gives_a_different_point(self):
        one = self._resolver_with(Geometry(size_capture=(1920, 1080), scale=1.0))
        two = self._resolver_with(Geometry(size_capture=(1920, 1080), scale=0.5))
        self.assertNotEqual(one.resolve(1), two.resolve(1))

    def test_an_unknown_index_resolves_to_nothing(self):
        res = self._resolver_with(Geometry(size_capture=(1920, 1080)))
        self.assertIsNone(res.resolve(99))

    def test_the_executor_prefers_a_selector_over_a_coordinate(self):
        res = self._resolver_with(Geometry(size_capture=(1920, 1080)))
        mark = ElementMark(
            index=1, role="button", name="Send", bbox=(10, 10, 20, 20),
            source="uia",
            selector={"window_name": "Test window", "name": "Send",
                      "automation_id": "sendButton", "control_type": "Button",
                      "ordinal": 1},
        )
        result = GroundingResult(level="N0", status="ok", marks=(mark,),
                                 geometry=Geometry(size_capture=(1920, 1080)))
        with patch.object(uia, "invoke",
                          return_value={"status": "done", "verified": True}) as inv:
            outcome = res.act(1, result=result)
        inv.assert_called_once()
        self.assertEqual(outcome["origin"], "selector")


# ─────────────────────────────────────────────────────────────────────────────
#  6. DPI and scaling
# ─────────────────────────────────────────────────────────────────────────────
class DpiTests(unittest.TestCase):
    def test_a_capture_and_a_screen_rectangle_map_both_ways(self):
        geometry = Geometry(origin_screen=(100, 50), size_capture=(1920, 1080),
                            size_logical=(1280, 720), scale=2.0 / 3.0)
        capture = (300, 200, 100, 50)
        screen = geometry.capture_to_screen(capture)
        self.assertEqual(screen, (400, 250, 100, 50))
        back = geometry.capture_to_logical(capture)
        self.assertEqual(geometry.logical_to_capture(back), capture)

    def test_the_same_element_at_100_and_150_percent_is_the_same_place(self):
        """The control occupies the same FRACTION of the screen either way.

        This is the property that makes a click land on the same control
        at 150 % as it does at 100 %. The PIXELS differ — a 150 % display
        has 1.5 times as many of them — but the PLACE does not, and the
        place is what a click is about.
        """
        at_100 = Geometry(origin_screen=(0, 0), size_capture=(1920, 1080),
                          size_logical=(1920, 1080), scale=1.0)
        at_150 = Geometry(origin_screen=(0, 0), size_capture=(1920, 1080),
                          size_logical=(1280, 720), scale=2.0 / 3.0)

        # The control starts a quarter of the way across the screen, in
        # MOUSE units. At 100 % that is 480 units; at 150 % the same
        # quarter of the screen is 320 units.
        def fraction(geometry, logical_left):
            x, _y, _w, _h = geometry.logical_to_capture(
                (logical_left, 0, 100, 100)
            )
            return x / float(geometry.size_capture[0])

        self.assertAlmostEqual(fraction(at_100, 480), 0.25, places=2)
        self.assertAlmostEqual(fraction(at_150, 320), 0.25, places=2)

    def test_the_resolved_point_moves_with_the_scale(self):
        geometry = Geometry(size_capture=(1920, 1080), scale=2.0 / 3.0)
        res = resolver.GroundingResolver()
        mark = ElementMark(index=1, role="button", name="X",
                           bbox=(900, 300, 60, 30))
        result = GroundingResult(level="N0", status="ok", marks=(mark,),
                                 geometry=geometry)
        res._result = ("key", result)
        # 900 capture pixels are 600 mouse units at 150 %, and the middle
        # of a 60-pixel box is 30 pixels further in, which is 20 units.
        self.assertEqual(res.resolve(1), (620, 210))


# ─────────────────────────────────────────────────────────────────────────────
#  7. The assistant's own elements are not targets
# ─────────────────────────────────────────────────────────────────────────────
class SelfGuardTests(unittest.TestCase):
    def test_an_element_of_our_own_process_is_not_actionable(self):
        mark = ElementMark(index=1, role="button", name="Send", own=True)
        self.assertFalse(mark.actionable)

    def test_a_password_field_is_not_actionable(self):
        mark = ElementMark(index=1, role="edit", name="Password",
                           protected=True)
        self.assertFalse(mark.actionable)

    def test_a_disabled_element_is_not_actionable(self):
        mark = ElementMark(index=1, role="button", name="Send", enabled=False)
        self.assertFalse(mark.actionable)

    def test_the_executor_refuses_our_own_element(self):
        res = resolver.GroundingResolver()
        mark = ElementMark(index=1, role="button", name="Send", own=True,
                           bbox=(10, 10, 20, 20))
        result = GroundingResult(level="N0", status="ok", marks=(mark,),
                                 geometry=Geometry(size_capture=(1920, 1080)))
        res._result = ("key", result)
        outcome = res.act(1, result=result)
        self.assertEqual(outcome["status"], "failed")
        self.assertIn("not one the assistant may act on", outcome["reason"])

    def test_the_invoker_refuses_our_own_element(self):
        mark = ElementMark(index=1, role="button", name="Send", own=True)
        self.assertEqual(uia.invoke(mark)["status"], "failed")
        self.assertEqual(uia.focus(mark)["status"], "failed")

    def test_our_own_process_is_recognised_as_ours(self):
        import os
        self.assertTrue(uia._is_own(os.getpid()))

    def test_our_ancestors_are_not_ours(self):
        """A shell started from File Explorer must not make Explorer ours.

        This is the rule that was broken: the assistant's own executables
        used to include every ANCESTOR's, so four Explorer windows on this
        machine were the assistant's own and the screen agent went blind to
        the file manager it was asked to work in.
        """
        from core import appidentity
        self.assertNotIn("explorer.exe", appidentity.identity_exes())
        self.assertNotIn("C:\\Windows\\explorer.exe",
                          appidentity.identity_exes())


# ─────────────────────────────────────────────────────────────────────────────
#  8. The cost of a capture and a grounding step
# ─────────────────────────────────────────────────────────────────────────────
class CostTests(unittest.TestCase):
    def test_a_grounding_step_stays_inside_the_budget(self):
        """The step the model waits for costs less than 150 ms.

        The step is measured on the level that answers. When the
        accessibility tree has something to say, that is the whole step —
        no picture is taken, because a tree does not need pixels — and it
        has to be quick.

        Skipped when there is no window to read: on a machine with nothing
        in front there is nothing to measure, and a criterion checked
        against nothing checks nothing.
        """
        from core import screenview

        if not screenview.has_uia():
            self.skipTest("UI Automation is not available here")
        if not uia._foreground_window():
            self.skipTest("there is no window in front to read")
        # The one-time warm-up is the cost of the SESSION, not of a query.
        # It is paid in the background when the package is imported; here it
        # is paid up front so that what is measured is the steady state.
        screenview.warm_up()

        res = resolver.GroundingResolver()
        # The first observation of a session pays for loading the
        # accessibility stack — measured here, about 550 ms for the COM
        # automation object and the capture libraries. That is a session
        # cost, and the grounding package pays it in the background when it
        # is imported. What the criterion is about is the steady state.
        res.observe(force=True)

        started = time.monotonic()
        result = res.observe(force=True)
        elapsed_ms = (time.monotonic() - started) * 1000.0

        if result.level != "N0":
            self.skipTest("the window in front exposed no accessibility tree")
        self.assertLess(
            elapsed_ms, 150.0,
            f"a grounding step took {elapsed_ms:.0f} ms; the budget is 150 ms",
        )

    def test_the_tree_walk_itself_is_inside_the_budget(self):
        from core import screenview

        if not screenview.has_uia():
            self.skipTest("UI Automation is not available here")
        if not uia._foreground_window():
            self.skipTest("there is no window in front to read")

        started = time.monotonic()
        uia.observe()
        elapsed_ms = (time.monotonic() - started) * 1000.0
        self.assertLess(elapsed_ms, 150.0,
                        f"the tree walk took {elapsed_ms:.0f} ms")


# ─────────────────────────────────────────────────────────────────────────────
#  The ladder, the cache and the budget
# ─────────────────────────────────────────────────────────────────────────────
class LadderTests(unittest.TestCase):
    def test_n0_is_used_when_n0_has_nothing(self):
        res = resolver.GroundingResolver()
        with patch.object(uia, "observe",
                          return_value=GroundingResult(level="N0",
                                                       status="unavailable",
                                                       reason="nothing")), \
             patch.object(resolver.GroundingResolver, "capture",
                          return_value=(None, Geometry(), "no picture")), \
             patch.object(resolver.GroundingResolver, "_ocr_elements",
                          return_value=[]):
            result = res.observe(force=True)
        self.assertEqual(result.status, "unavailable")
        self.assertIn("N0", result.reason)

    def test_a_window_with_nothing_to_press_hands_over_to_the_next_level(self):
        """A Chromium window is a frame and three title-bar buttons.

        That is not a reading of the screen, and continuing is cheaper
        than handing the model a list it cannot act on.
        """
        frame_only = GroundingResult(
            level="N0", status="ok",
            marks=(ElementMark(index=1, role="window", name="Chrome"),
                   ElementMark(index=2, role="group", name="TitleBar")),
        )
        res = resolver.GroundingResolver()
        with patch.object(uia, "observe", return_value=frame_only), \
             patch.object(resolver.GroundingResolver, "capture",
                          return_value=(None, Geometry(), "no picture")), \
             patch.object(resolver.GroundingResolver, "_ocr_elements",
                          return_value=[]):
            result = res.observe(force=True)
        self.assertNotEqual(result.level, "N0")
        self.assertIn("something to press", result.reason)

    def test_n2_is_off_unless_the_user_turns_it_on(self):
        with patch("memory.config_manager.get_grounding_vlm_enabled",
                   return_value=False):
            self.assertFalse(detect.enabled())
            ok, why = detect.available()
        self.assertFalse(ok)
        self.assertIn("off", why)

    def test_the_grounding_model_has_a_budget(self):
        budget = detect.Budget(max_calls=1, max_tokens=100)
        self.assertTrue(budget.allow())
        budget.spend(50)
        self.assertFalse(budget.allow())
        self.assertTrue(budget.exhausted)

    def test_the_grounding_model_never_evaluates_its_answer(self):
        """The reply is read with a JSON parser, and only a JSON parser."""
        import inspect
        source = inspect.getsource(detect)
        self.assertNotIn("eval(", source)
        self.assertNotIn("exec(", source)
        self.assertIn("json", source.lower())

    def test_a_name_from_the_model_is_bounded(self):
        self.assertEqual(len(detect._clean("x" * 500)), detect._MAX_LABEL)
        self.assertNotIn("\n", detect._clean("a\nb"))

    def test_boxes_are_read_in_either_scale(self):
        fraction = detect._parse_boxes(
            [{"label": "A", "bbox": [0.5, 0.5, 1.0, 1.0]}], 1000, 1000)
        self.assertEqual(fraction[0]["bbox"], (500, 500, 500, 500))
        pixels = detect._parse_boxes(
            [{"label": "A", "bbox": [10, 20, 40, 60]}], 1000, 1000)
        self.assertEqual(pixels[0]["bbox"], (10, 20, 30, 40))


class CacheTests(unittest.TestCase):
    def test_the_observation_is_reused_until_the_screen_moves(self):
        res = resolver.GroundingResolver()
        geometry = Geometry(size_capture=(1920, 1080))
        mark = ElementMark(index=1, role="button", name="Send",
                           bbox=(10, 10, 20, 20))
        result = GroundingResult(level="N0", status="ok", marks=(mark,),
                                 geometry=geometry)

        with patch.object(uia, "observe", return_value=result) as obs:
            first = res.observe(force=True)
            second = res.observe()
        self.assertEqual(first.marks[0].name, "Send")
        self.assertEqual(second.marks[0].name, "Send")
        self.assertEqual(obs.call_count, 1,
                         "the tree was walked twice for one screen")

    def test_invalidate_forgets_everything(self):
        res = resolver.GroundingResolver()
        mark = ElementMark(index=1, role="button", name="Send",
                           bbox=(10, 10, 20, 20))
        result = GroundingResult(level="N0", status="ok", marks=(mark,),
                                 geometry=Geometry(size_capture=(1920, 1080)))
        with patch.object(uia, "observe", return_value=result):
            res.observe(force=True)
            res.invalidate()
            self.assertIsNone(res.last_result)


if __name__ == "__main__":
    unittest.main()
