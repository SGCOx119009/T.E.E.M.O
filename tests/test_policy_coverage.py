"""La cobertura de la puerta, probada.

El plan dice, en sus criterios de aceptación de la FASE 9:

    "**La cobertura se prueba**: una suite verifica que *toda* acción de
    clase `EXTERNAL`, `IRREVERSIBLE`, `CREDENTIAL` o `PRIVACY` pasa por
    la puerta. La suite falla si alguna no lo hace."

Esta es esa suite, y es DINÁMICA: no enumera a mano las acciones que
deben estar vigiladas, sino que las recorre todas desde el catálogo de
`core/policy.py`. Añadir una acción nueva al catálogo la mete en la
prueba sin que nadie tenga que acordarse de añadirla.

Lo que se comprueba, acción por acción:

  1. Que la clase existe y es una de las siete.
  2. Que la acción se conoce: una acción que nadie ha clasificado no es
     una acción segura, es una acción desconocida, y la puerta se cierra
     sobre ella.
  3. Que la puerta se abre de verdad: el ejecutor NO la ejecuta, y una
     confirmación queda en la pantalla.
  4. Que el modo read-only la rechaza con un mensaje ACCIONABLE, y no con
     silencio ni con un error genérico.
  5. Que una concesión del usuario sí la deja pasar (sin volver a
     preguntar: preguntar otra vez es lo que entrena al usuario a decir
     "sí" sin leer).
  6. Que las tres clases silenciosas (READ, WRITE_LOCAL, REVERSIBLE_SYS)
     no abren ningún banner: el silencio es parte del contrato.
  7. Que la lista de Delegaciones a la puerta del manejador es VERDADERA:
     cada manejador al que se delega tiene realmente su propia puerta, y
     si no la tuviera, el ejecutor se tragaría la acción sin preguntar.
  8. Que toda herramienta del producto tiene clase: el catálogo no puede
     quedarse corto sin que la suite lo diga.
  9. Que una etiqueta de riesgo escrita por el modelo no cambia nada.
"""

from __future__ import annotations

import ast
import inspect
import unittest
from pathlib import Path
from unittest.mock import patch

from core import action_policy, confirm, policy

#: The four classes the plan watches.
GATED = frozenset({
    policy.RISK_EXTERNAL,
    policy.RISK_IRREVERSIBLE,
    policy.RISK_CREDENTIAL,
    policy.RISK_PRIVACY,
})

#: The three that are silent by design.
SILENT = frozenset({
    policy.RISK_READ,
    policy.RISK_WRITE_LOCAL,
    policy.RISK_REVERSIBLE_SYS,
})

#: The tools the product offers from `main.py`, in line.
INLINE_TOOLS = (
    "assistant_state", "close_camera", "manage_monitor", "recall_memory",
    "save_memory", "screen_process", "shutdown_teemo", "system_status",
    "undo",
)

#: The plugins that ship with the product.
PLUGIN_TOOLS = ("calendar_control", "gmail_control", "spotify_control")


def catalog() -> list[tuple[str, str, str]]:
    """Every (tool, action, class) the fine table declares.

    The empty action is the class of the REST of the tool. When the table
    also names the actions one by one, the empty action adds nothing new
    and is not a row of its own: the rows are the actions.
    """
    rows: list[tuple[str, str, str]] = []
    for tool, table in policy._ACTION_CLASS.items():
        others = [action for action in table if action]
        for action, klass in table.items():
            if action == "" and others:
                continue
            rows.append((tool, action, klass))
    for tool, klass in policy._TOOL_DEFAULT.items():
        if tool not in policy._ACTION_CLASS:
            rows.append((tool, "", klass))
    return rows


def gated_rows() -> list[tuple[str, str, str]]:
    return [row for row in catalog() if row[2] in GATED]


def product_tools() -> set[str]:
    """Every tool the model can be offered, from the product itself.

    The actions and the plugins are read with the loader that discovers
    them (`core/tool_loader.py`), which parses the declaration without
    executing the module, and the inline tools are read from `main.py`
    with the AST — also without importing it, because importing `main.py`
    would wake the whole application up in the middle of a test.
    """
    from core.tool_loader import declaration_from_source

    root = Path(__file__).resolve().parent.parent
    names: set[str] = set()

    for folder in ("actions", "plugins"):
        for path in sorted((root / folder).glob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                declaration, _origin = declaration_from_source(path)
            except Exception:
                continue
            if isinstance(declaration, dict):
                name = declaration.get("name")
                if isinstance(name, str):
                    names.add(name)

    inline = root / "main.py"
    if inline.exists():
        tree = ast.parse(inline.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            targets = [
                target.id for target in node.targets
                if isinstance(target, ast.Name)
            ]
            if "TOOL_DECLARATIONS" not in targets:
                continue
            for element in ast.walk(node.value):
                if not isinstance(element, ast.Dict):
                    continue
                for key, value in zip(element.keys, element.values):
                    if (
                        isinstance(key, ast.Constant)
                        and key.value == "name"
                        and isinstance(value, ast.Constant)
                        and isinstance(value.value, str)
                    ):
                        names.add(value.value)
    return names


class PolicyCoverageTests(unittest.TestCase):
    """The door, checked against the whole catalogue."""

    def setUp(self) -> None:
        with confirm._lock:
            confirm._pending = None
        self.prompts: list[tuple[str, str]] = []
        confirm.bind(
            lambda title, detail: self.prompts.append((title, detail)),
            lambda: None,
        )

    def tearDown(self) -> None:
        with confirm._lock:
            confirm._pending = None
        confirm.bind(None, None)

    @staticmethod
    def _arguments(tool: str, action: str) -> dict:
        """The arguments the executor itself would build for this action.

        Two tools carry their action in `mode` instead of `action`, and
        the executor reads it from there: the test has to speak the same
        language or it would be testing something else.
        """
        if not action:
            return {}
        if tool in ("task_planner", "web_search", "dev_agent"):
            return {"mode": action}
        return {"action": action}

    def _dispatch(self, tool: str, action: str, run, *,
                 read_only: bool = False, granted: bool = False) -> str:
        arguments = self._arguments(tool, action)
        with (
            patch.object(policy, "read_only_enabled", return_value=read_only),
            patch.object(policy, "is_granted", return_value=granted),
        ):
            return action_policy.dispatch(tool, arguments, run)

    # ── 1 and 2: the catalogue itself ──────────────────────────────────────

    def test_every_declared_action_has_one_of_the_seven_classes(self) -> None:
        for tool, action, klass in catalog():
            with self.subTest(tool=tool, action=action):
                self.assertIn(klass, policy.RISK_CATALOG)
                self.assertTrue(
                    policy.class_is_known(tool, action),
                    f"{tool}.{action} has no known class",
                )

    def test_each_watched_class_has_at_least_one_real_action(self) -> None:
        # CREDENTIAL has no tool of its own in this product: the assistant
        # never stores a key by itself. It reaches that class the way it
        # really happens — typing a password into a login form — so the
        # probe for it is a form with a secret in it, not a table row.
        probes = {
            policy.RISK_EXTERNAL: ("send_message", "send", {}),
            policy.RISK_IRREVERSIBLE: ("file_controller", "delete", {}),
            policy.RISK_PRIVACY: ("camera", "", {}),
            policy.RISK_CREDENTIAL: (
                "browser_control", "fill_form",
                {"action": "fill_form",
                 "fields": {'input[name="password"]': "synthetic"}},
            ),
        }
        for klass, (tool, action, extra) in probes.items():
            with self.subTest(class_=klass):
                arguments = dict(extra)
                if action and "action" not in arguments:
                    arguments["action"] = action
                self.assertEqual(
                    policy.risk_class_of(tool, action, arguments), klass,
                )
                self.assertTrue(
                    action_policy.requires_confirmation(
                        tool, action, arguments,
                    ),
                    f"{klass} is watched by the plan, and it does not ask",
                )

    def test_the_declared_class_is_the_class_that_is_applied(self) -> None:
        for tool, action, klass in catalog():
            with self.subTest(tool=tool, action=action):
                self.assertEqual(policy.risk_class_of(tool, action), klass)

    # ── 3: the door opens ───────────────────────────────────────────────────

    def test_every_watched_action_goes_through_the_door(self) -> None:
        rows = gated_rows()
        self.assertTrue(rows, "there is nothing to watch")
        for tool, action, klass in rows:
            if action in action_policy._HANDLER_OWN_GATE.get(tool, set()):
                # The handler asks in its own words; that is checked by its
                # own test, further down.
                continue
            with self.subTest(tool=tool, action=action, class_=klass):
                ran: list[bool] = []
                result = self._dispatch(
                    tool, action,
                    lambda: ran.append(True) or "status=done",
                )
                self.assertFalse(
                    ran,
                    f"{tool}.{action} ({klass}) ran without the door",
                )
                self.assertIn("[CONFIRMATION_PENDING]", result)
                self.assertEqual(len(self.prompts), 1)

    # ── 4: read-only refuses, and says what to do ──────────────────────────

    def test_read_only_refuses_every_watched_action_with_a_way_out(self) -> None:
        for tool, action, klass in gated_rows():
            if action in action_policy._HANDLER_OWN_GATE.get(tool, set()):
                continue
            with self.subTest(tool=tool, action=action, class_=klass):
                ran: list[bool] = []
                result = self._dispatch(
                    tool, action,
                    lambda: ran.append(True) or "status=done",
                    read_only=True,
                )
                self.assertFalse(ran)
                self.assertIn("status=failed", result)
                self.assertIn("read-only", result)
                self.assertIn("Permissions", result)

    # ── 5: a concession lets it through ─────────────────────────────────────

    def test_a_concession_opens_the_door_without_asking_again(self) -> None:
        for tool, action, klass in gated_rows():
            if action in action_policy._HANDLER_OWN_GATE.get(tool, set()):
                continue
            with self.subTest(tool=tool, action=action, class_=klass):
                ran: list[bool] = []
                self._dispatch(
                    tool, action,
                    lambda: ran.append(True) or "status=done",
                    granted=True,
                )
                self.assertTrue(
                    ran,
                    f"{tool}.{action} did not run with the user's concession",
                )
                self.assertEqual(self.prompts, [])

    # ── 6: the silent classes are silent ───────────────────────────────────

    def test_the_silent_classes_never_open_the_door(self) -> None:
        for tool, action, klass in catalog():
            if klass not in SILENT:
                continue
            if action in action_policy._HANDLER_OWN_GATE.get(tool, set()):
                continue
            with self.subTest(tool=tool, action=action, class_=klass):
                ran: list[bool] = []
                self._dispatch(
                    tool, action,
                    lambda: ran.append(True) or "status=done",
                )
                self.assertTrue(ran)
                self.assertEqual(
                    self.prompts, [],
                    f"{tool}.{action} ({klass}) asked, and it must not",
                )

    # ── 7: the delegations are true ─────────────────────────────────────────

    def test_every_delegated_gate_really_exists_in_its_handler(self) -> None:
        for tool, actions in action_policy._HANDLER_OWN_GATE.items():
            with self.subTest(tool=tool):
                module = self._handler_module(tool)
                self.assertIsNotNone(
                    module,
                    f"{tool} is delegated to, and there is no such handler",
                )
                source = inspect.getsource(module)
                self.assertTrue(
                    "confirm.request" in source
                    or "request_confirmation" in source
                    or "ask_once" in source
                    or "_IRREVERSIBLE" in source,
                    f"{tool} is delegated to, and it has no gate of its own",
                )
                if tool == "computer_settings":
                    for action in actions:
                        self.assertIn(action, module._IRREVERSIBLE)

    def test_no_delegated_action_is_left_without_a_gate(self) -> None:
        # The list is short on purpose: a delegation to a handler that does
        # not ask is a hole, and the hole is how "delete the file" ended up
        # running with nobody seeing it.
        self.assertEqual(
            set(action_policy._HANDLER_OWN_GATE),
            {"computer_settings", "meet_control", "game_updater", "dev_agent"},
        )

    @staticmethod
    def _handler_module(tool: str):
        import importlib
        try:
            return importlib.import_module(f"actions.{tool}")
        except Exception:
            return None

    # ── 8: the catalogue covers the product ─────────────────────────────────

    def test_every_tool_of_the_product_has_a_class(self) -> None:
        declared = set(policy._ACTION_CLASS) | set(policy._TOOL_DEFAULT)
        for tool in sorted(product_tools()):
            with self.subTest(tool=tool):
                self.assertIn(
                    tool, declared,
                    f"{tool} is offered to the model and has no class",
                )
                self.assertTrue(policy.is_reviewed(tool))

    # ── 9: a label from the model changes nothing ───────────────────────────

    def test_a_risk_label_written_by_the_model_changes_nothing(self) -> None:
        arguments = {
            "action": "delete",
            "risk": "READ",
            "risk_class": "READ",
            "severity": "low",
            "confirm": True,
        }
        self.assertEqual(
            policy.risk_class_of("file_controller", "delete", arguments),
            policy.RISK_IRREVERSIBLE,
        )
        self.assertTrue(action_policy.requires_confirmation(
            "file_controller", "delete", arguments,
        ))

    def test_the_target_of_a_control_action_can_raise_its_class(self) -> None:
        # A click is not a class by itself: what it is aimed at is.
        self.assertEqual(
            policy.risk_class_of(
                "computer_control", "click",
                {"action": "click", "description": "Buy now"},
            ),
            policy.RISK_EXTERNAL,
        )
        self.assertEqual(
            policy.risk_class_of(
                "computer_control", "click",
                {"action": "click", "description": "Next page"},
            ),
            policy.RISK_REVERSIBLE_SYS,
        )
        self.assertEqual(
            policy.risk_class_of(
                "browser_control", "fill_form",
                {"action": "fill_form", "fields": {'input[name="password"]': "x"}},
            ),
            policy.RISK_CREDENTIAL,
        )


if __name__ == "__main__":
    unittest.main()
