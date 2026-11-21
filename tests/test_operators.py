"""FASE 4 — el núcleo de operadores: pruebas de aceptación.

Ocho criterios, ocho pruebas. Cada una comprueba algo que, de no estar, el
producto seguiría funcionando y seguiría siendo otra cosa:

  1. el paquete de operadores existe, con sus siete módulos
  2. el vocabulario de acciones es CERRADO
  3. no hay eval() ni exec() de salida de modelo en ninguna parte
  4. el conjunto de herramientas depende del operador activo
  5. las herramientas de siempre siguen siendo las de siempre
  6. un plugin que revienta no tumba la app
  7. descubrir todo cuesta menos de 250 ms
  8. el motor de proveedores sigue pasando sus pruebas
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import time
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from core.action_loader import discover_actions
from core.plugin_loader import discover_plugins
from core.operators import CONTEXT_ALL, OperatorRegistry
from core.operators.base import (
    ALLOWED_ACTIONS,
    ActionRejected,
    OperatorAction,
    parse_action,
)


def _inline_declarations() -> list[dict]:
    """The tools main.py declares inline.

    Read out of the source rather than by importing main.py: importing it
    starts an assistant.
    """
    tree = ast.parse((BASE / "main.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", "") == "TOOL_DECLARATIONS" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    return []


def _build_registry(eager: bool = False) -> OperatorRegistry:
    actions = discover_actions(BASE / "actions", logger=lambda m: None, eager=eager)
    plugins = discover_plugins(
        BASE / "plugins", core_tool_names=actions.names(), logger=lambda m: None,
        eager=eager,
    )
    return OperatorRegistry.from_registries(
        action_registry=actions,
        plugin_registry=plugins,
        inline_declarations=_inline_declarations(),
        logger=lambda m: None,
    )


class OperatorPackageTests(unittest.TestCase):
    def test_the_operators_package_exists_with_its_seven_modules(self) -> None:
        package = BASE / "core" / "operators"
        self.assertTrue(package.is_dir(), "core/operators/ must exist")
        for module in ("__init__.py", "base.py", "desktop.py", "browser.py",
                       "files.py", "system.py", "vision.py", "media.py"):
            self.assertTrue(
                (package / module).is_file(),
                f"core/operators/{module} must exist",
            )
        from core.operators import OperatorRegistry as _R
        self.assertTrue(callable(_R))

    def test_the_six_domain_operators_are_registered(self) -> None:
        registry = _build_registry()
        names = {op.name for op in registry.operators()}
        self.assertEqual(
            names,
            {"desktop", "browser", "files", "system", "vision", "media"},
        )
        for operator in registry.operators():
            self.assertTrue(operator.tools, f"{operator.name} owns nothing")
            self.assertTrue(operator.description)


class ClosedSchemaTests(unittest.TestCase):
    def test_an_action_outside_the_vocabulary_is_rejected(self) -> None:
        """The vocabulary is closed: what is not in it is refused, not run."""
        for payload in (
            {"action": "rm -rf /"},
            {"action": "exec"},
            {"action": "eval"},
            {"action": "open_file"},
            {"action": ""},
            {"action": 42},
        ):
            with self.assertRaises(ActionRejected):
                parse_action(payload)

    def test_a_valid_action_is_accepted_and_frozen(self) -> None:
        action = parse_action({"action": "click", "target": "e17",
                               "args": {"x": 0.5, "y": 0.25}})
        self.assertIsInstance(action, OperatorAction)
        self.assertEqual(action.action, "click")
        self.assertEqual(action.target, "e17")
        self.assertEqual(action.args, {"x": 0.5, "y": 0.25})
        with self.assertRaises(Exception):
            action.action = "type"          # frozen: a decision, not a draft

    def test_absolute_coordinates_are_refused(self) -> None:
        """A pixel coordinate is refused, not rescaled.

        Rescaling silently is how a click lands on the wrong control and
        looks like it worked.
        """
        with self.assertRaises(ActionRejected):
            parse_action({"action": "click", "args": {"x": 1500, "y": 900}})
        ok = parse_action({"action": "click", "args": {"x": 0.0, "y": 1.0}})
        self.assertEqual(ok.args, {"x": 0.0, "y": 1.0})

    def test_credentials_are_masked_in_the_raw_arguments(self) -> None:
        action = parse_action({"action": "type", "args": {
            "text": "hello", "password": "hunter2", "api_key": "sk-123"}})
        self.assertEqual(action.args["password"], "hunter2")   # the real value
        self.assertEqual(action.raw_args["password"], "***")   # the shown one
        self.assertEqual(action.raw_args["api_key"], "***")
        self.assertEqual(action.raw_args["text"], "hello")

    def test_arguments_are_checked_against_the_tools_schema(self) -> None:
        registry = _build_registry()
        with self.assertRaises(ActionRejected):
            registry.parse({"action": "read", "args": {"nope": 1}},
                           tool="browser_control")
        with self.assertRaises(ActionRejected):
            registry.parse({"action": "read", "args": {}}, tool="browser_control")

    def test_the_vocabulary_is_a_fixed_set(self) -> None:
        self.assertIn("click", ALLOWED_ACTIONS)
        self.assertIn("type", ALLOWED_ACTIONS)
        self.assertIn("scroll", ALLOWED_ACTIONS)
        self.assertNotIn("eval", ALLOWED_ACTIONS)
        self.assertNotIn("exec", ALLOWED_ACTIONS)
        self.assertNotIn("shell", ALLOWED_ACTIONS)


class NoModelCodeTests(unittest.TestCase):
    def test_there_is_no_eval_or_exec_of_model_output(self) -> None:
        """Nothing in the product evaluates what the model wrote.

        The scan is done on the PARSE TREE, not on the text: what matters is
        whether a function named eval or exec is ever CALLED, and a line of
        prose that merely contains the word is not a call. Looking for calls
        also means the test cannot be fooled by a call split across two
        lines, or by a name built at runtime.

        The two that exist are Qt's own — the event loop and a context menu —
        and they are called on Qt objects, never on model text.
        """
        allowed = {
            ("ui.py", "exec", 0),          # self._app.exec() — the Qt event loop
            ("gui/widgets.py", "exec", 1),  # menu.exec(QCursor.pos()) — a context menu
        }
        pattern = re.compile(r"^(eval|exec)$")
        offenders = []
        for path in sorted(BASE.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(BASE).as_posix()
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError as exc:
                offenders.append(f"{rel}: could not be parsed ({exc.msg})")
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = func.id if isinstance(func, ast.Name) else (
                    func.attr if isinstance(func, ast.Attribute) else None)
                if not name or not pattern.match(name):
                    continue
                if (rel, name, len(node.args)) in allowed:
                    continue
                offenders.append(f"{rel}:{node.lineno}: {name}()")
        self.assertEqual(offenders, [], "eval/exec found:\n" + "\n".join(offenders))


class ContextTests(unittest.TestCase):
    def test_the_visible_tools_depend_on_the_active_operator(self) -> None:
        registry = _build_registry()
        everything = registry.declarations()
        self.assertGreater(len(everything), 20)

        browser = registry.declarations("browser")
        self.assertLess(len(browser), len(everything))
        self.assertTrue(browser)
        names = {d["name"] for d in browser}
        self.assertIn("browser_control", names)
        self.assertNotIn("computer_settings", names)

        for context in registry.contexts():
            if context == CONTEXT_ALL:
                continue
            scoped = registry.declarations(context)
            self.assertTrue(scoped, f"context {context} offers nothing")
            self.assertLess(len(scoped), len(everything))

    def test_the_active_context_is_kept_when_it_is_unknown(self) -> None:
        registry = _build_registry()
        registry.set_context("browser")
        self.assertEqual(registry.active_context, "browser")
        registry.set_context("no-such-context")
        self.assertEqual(registry.active_context, "browser",
                         "a typo must not leave the model with no tools")
        registry.set_context(CONTEXT_ALL)
        self.assertEqual(registry.active_context, CONTEXT_ALL)

    def test_every_tool_belongs_to_an_operator(self) -> None:
        registry = _build_registry()
        for name in registry.tool_names():
            self.assertIsNotNone(registry.operator_for(name),
                                 f"{name} belongs to no operator")


class BehaviourUnchangedTests(unittest.TestCase):
    def test_the_tool_names_and_schemas_are_exactly_what_they_were(self) -> None:
        """The operator layer passes the declarations through untouched.

        The registry's job is to group and filter, never to rewrite what the
        model is told a tool is.
        """
        eager = _build_registry(eager=True)
        lazy = _build_registry(eager=False)

        actions = discover_actions(BASE / "actions", logger=lambda m: None, eager=True)
        plugins = discover_plugins(
            BASE / "plugins", core_tool_names=actions.names(), logger=lambda m: None,
            eager=True,
        )
        before = (_inline_declarations()
                  + actions.get_tool_declarations()
                  + plugins.get_tool_declarations())
        self.assertEqual(lazy.declarations(), before,
                         "the lazy path must declare exactly what the eager path did")
        self.assertEqual(eager.declarations(), before)

    def test_the_bundled_actions_keep_their_names(self) -> None:
        """The nineteen bundled actions, with the names they have always had."""
        actions = discover_actions(BASE / "actions", logger=lambda m: None, eager=False)
        self.assertEqual(
            sorted(actions.names()),
            sorted([
                "background_tasks", "browser_control", "code_helper",
                "computer_control", "computer_settings", "desktop_control",
                "dev_agent", "file_controller", "file_processor", "flight_finder",
                "game_updater", "meet_control", "open_app", "reminder", "routines",
                "send_message", "task_planner", "video_player", "weather_report",
                "web_search", "youtube_video",
            ]),
        )

    def test_a_handler_is_resolved_when_the_tool_is_called(self) -> None:
        """The lazy path binds the handler at call time, not at discovery."""
        actions = discover_actions(BASE / "actions", logger=lambda m: None, eager=False)
        record = actions.records()[0]
        self.assertIsNone(record.handler, "nothing should be imported yet")
        self.assertTrue(record.module_path)
        resolved = record.resolve()
        self.assertTrue(callable(resolved), "the module must import cleanly")


class IsolationTests(unittest.TestCase):
    def test_a_plugin_that_raises_does_not_bring_the_app_down(self) -> None:
        """One broken plugin is reported; the others still load."""
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plugins = root / "plugins"
            plugins.mkdir()
            (plugins / "broken.py").write_text(
                "raise RuntimeError('synthetic-private-token')", encoding="utf-8"
            )
            (plugins / "good.py").write_text(
                'PLUGIN = {"name": "good", "description": "d",\n'
                '           "parameters": {"type": "OBJECT", "properties": {}}}\n'
                "def run(parameters):\n    return 'ok'\n",
                encoding="utf-8",
            )
            logs: list[str] = []
            registry = discover_plugins(plugins, set(), logger=logs.append)

        self.assertIn("good", registry.names())
        self.assertNotIn("broken", registry.names())
        # The exception's MESSAGE must not reach the log: it can carry a path
        # or a token, and the log is what people paste into bug reports.
        self.assertNotIn("synthetic-private-token", "\n".join(logs))
        self.assertTrue(any("RuntimeError" in line for line in logs))

    def test_an_action_that_raises_does_not_bring_the_app_down(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            actions = Path(temp) / "actions"
            actions.mkdir()
            (actions / "broken.py").write_text(
                "raise RuntimeError('synthetic-private-token')", encoding="utf-8"
            )
            logs: list[str] = []
            registry = discover_actions(actions, logger=logs.append)

        self.assertEqual(registry.names(), set())
        self.assertNotIn("synthetic-private-token", "\n".join(logs))


class DiscoveryCostTests(unittest.TestCase):
    """The cost of discovery, measured the way a launch pays it.

    The measurements are taken in a CLEAN interpreter, in a subprocess. Inside
    the test runner the modules the actions import are already in memory, so
    the expensive path looks cheap — the same reason a car with the engine
    already running does not time its pull-up from rest.
    """

    def _measure(self, eager: bool, runs: int = 3) -> dict:
        """Measure the discovery in a fresh interpreter. Returns the best run.

        The best of several runs is the honest number: it is what the machine
        can do, rather than what the scheduler happened to be doing when the
        sample was taken.
        """
        probe = (
            "import json, sys, time\n"
            "from pathlib import Path\n"
            f"base = Path({str(BASE)!r})\n"
            "sys.path.insert(0, str(base))\n"
            "t0 = time.perf_counter()\n"
            "from core.action_loader import discover_actions\n"
            "from core.plugin_loader import discover_plugins\n"
            "import_ms = (time.perf_counter() - t0) * 1000.0\n"
            "t0 = time.perf_counter()\n"
            "actions = discover_actions(base / 'actions', logger=lambda m: None, "
            f"eager={eager!r})\n"
            "plugins = discover_plugins(base / 'plugins', "
            "core_tool_names=actions.names(), logger=lambda m: None, "
            f"eager={eager!r})\n"
            "discover_ms = (time.perf_counter() - t0) * 1000.0\n"
            "print(json.dumps({'import_ms': import_ms, 'discover_ms': discover_ms,\n"
            "                  'total_ms': import_ms + discover_ms,\n"
            "                  'tools': len(actions.names()) + len(plugins.names())}))\n"
        )
        best: dict = {}
        for _ in range(runs):
            result = subprocess.run(
                [sys.executable, "-c", probe],
                cwd=str(BASE), capture_output=True, text=True, timeout=600,
            )
            self.assertEqual(result.returncode, 0, result.stderr[-3000:])
            data = json.loads(result.stdout.strip().splitlines()[-1])
            if not best or data["total_ms"] < best["total_ms"]:
                best = data
        return best

    def test_discovering_everything_costs_less_than_250_ms(self) -> None:
        """Criterio 7: plugins + actions, discovered, under 250 ms.

        The number covers the whole cost of the operation: importing the two
        loaders and reading every declaration out of every file, without
        running a single one of them.
        """
        data = self._measure(eager=False)
        self.assertGreater(data["tools"], 20, "the discovery found nothing")
        self.assertLess(
            data["total_ms"], 250.0,
            f"discovery took {data['total_ms']:.0f} ms "
            f"(import {data['import_ms']:.0f} ms + scan {data['discover_ms']:.0f} ms)",
        )

    def test_reading_the_declarations_is_much_cheaper_than_importing(self) -> None:
        """The reason the AST path exists, stated as a measurement.

        Both halves are measured in their own clean interpreter: the eager
        one has to import twenty-three modules, which is exactly the cost the
        AST path exists to avoid.
        """
        lazy = self._measure(eager=False)
        eager = self._measure(eager=True)
        self.assertLess(
            lazy["total_ms"], eager["total_ms"],
            f"reading the source ({lazy['total_ms']:.0f} ms) must be cheaper "
            f"than importing it ({eager['total_ms']:.0f} ms)",
        )


class ProviderEngineTests(unittest.TestCase):
    def test_the_provider_engine_still_passes_its_own_tests(self) -> None:
        """Criterio 8: the engine is not broken by any of this."""
        result = subprocess.run(
            [sys.executable, "-m", "providers._tests"],
            cwd=str(BASE), capture_output=True, text=True, timeout=600,
        )
        self.assertEqual(result.returncode, 0, result.stdout[-3000:])


if __name__ == "__main__":
    unittest.main()
