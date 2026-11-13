from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from core import action_policy, confirm


class ActionPolicyTests(unittest.TestCase):
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

    def test_requires_confirmation_only_for_high_and_external_risks(self) -> None:
        self.assertTrue(action_policy.requires_confirmation(
            "file_controller", "write"
        ))
        self.assertTrue(action_policy.requires_confirmation(
            "send_message", "send"
        ))
        self.assertFalse(action_policy.requires_confirmation(
            "file_controller", "read"
        ))
        self.assertFalse(action_policy.requires_confirmation(
            "computer_control", "unknown_action"
        ))

    def test_gate_fails_closed_for_an_unclassified_action(self) -> None:
        ran = threading.Event()

        result = action_policy.request_confirmation(
            "computer_control",
            "unknown_action",
            "Run unknown action?",
            "Synthetic details",
            lambda: ran.set() or "done",
        )

        self.assertIn("status=failed", result)
        self.assertFalse(ran.is_set())
        self.assertEqual(self.prompts, [])

    def test_dispatch_rejects_invalid_arguments_or_handler(self) -> None:
        self.assertIn(
            "status=failed",
            action_policy.dispatch("file_controller", None, lambda: "done"),
        )
        self.assertIn(
            "status=failed",
            action_policy.dispatch("file_controller", {}, None),
        )

    def test_dispatch_rejects_oversized_arguments_without_calling_handler(self):
        ran = []

        result = action_policy.dispatch(
            "browser_control",
            {"action": "get_url", "text": "x" * 100_001},
            lambda: ran.append(True) or "status=done",
        )

        self.assertIn("status=failed", result)
        self.assertFalse(ran)

    def test_dispatch_rejects_non_json_arguments_without_calling_handler(self):
        ran = []

        result = action_policy.dispatch(
            "browser_control",
            {"action": "get_url", "value": object()},
            lambda: ran.append(True) or "status=done",
        )

        self.assertIn("status=failed", result)
        self.assertFalse(ran)

    def test_shared_argument_validation_covers_core_tool_inputs(self) -> None:
        self.assertIsNone(action_policy.validate_arguments({"query": "safe"}))
        self.assertIn(
            "size limit",
            action_policy.validate_arguments({"query": "x" * 100_001}) or "",
        )
        self.assertEqual(
            action_policy.validate_arguments({"query": "\ud800"}),
            "tool arguments contain invalid text",
        )

    def test_sensitive_action_waits_for_confirmation(self) -> None:
        ran = threading.Event()

        result = action_policy.request_confirmation(
            "file_controller",
            "write",
            "Write file?",
            "Synthetic file contents",
            lambda: ran.set() or "written",
            arguments={"action": "write"},
        )

        self.assertIn("[CONFIRMATION_PENDING]", result)
        self.assertEqual(self.prompts, [("Write file?", "Synthetic file contents")])
        self.assertFalse(ran.is_set())

        confirm.resolve(False)

        self.assertFalse(ran.wait(0.1))

    def test_voice_confirmation_is_not_bypassed_by_authorized_task_plan(self) -> None:
        ran = threading.Event()
        voice_handler = lambda _text: confirm.VoiceDecision("accept")
        arguments = {"platform": "WhatsApp", "receiver": "Synthetic"}

        with action_policy.authorize_plan([("send_message", arguments)]):
            result = action_policy.request_confirmation(
                "send_message",
                "send",
                "Send message?",
                "Synthetic message",
                lambda: ran.set() or "sent",
                arguments=arguments,
                voice_handler=voice_handler,
            )

        self.assertIn("[CONFIRMATION_PENDING]", result)
        self.assertFalse(ran.is_set())
        self.assertEqual(self.prompts, [("Send message?", "Synthetic message")])
        confirm.resolve(False)
        self.assertFalse(ran.wait(0.1))

    def test_confirmation_masks_secret_form_values(self) -> None:
        result = action_policy.dispatch(
            "task_planner",
            {
                "mode": "execute",
                "task": "fill login form",
                "steps": [
                    {
                        "tool": "browser_control",
                        "arguments": {
                            "action": "fill_form",
                            "fields": {
                                'input[name="email"]': "person@example.test",
                                'input[name="password"]': "private-password",
                                'input[name="credential"]': "private-credential",
                            },
                        },
                        "purpose": "fill fields",
                        "verification": {
                            "tool": "browser_control",
                            "arguments": {"action": "get_url"},
                            "expect": "https://example.test",
                        },
                    }
                ],
            },
            lambda: "not executed",
        )

        self.assertIn("[CONFIRMATION_PENDING]", result)
        detail = self.prompts[-1][1]
        self.assertIn("person@example.test", detail)
        self.assertIn("[REDACTED]", detail)
        self.assertNotIn("private-password", detail)
        self.assertNotIn("private-credential", detail)

    def test_confirmation_masks_text_for_sensitive_desktop_targets(self) -> None:
        result = action_policy.dispatch(
            "computer_control",
            {
                "action": "type",
                "selector": {
                    "window_name": "Synthetic login",
                    "name": "Password",
                    "control_type": "Edit",
                },
                "text": "private-password-value",
            },
            lambda: "not executed",
        )

        self.assertIn("[CONFIRMATION_PENDING]", result)
        detail = self.prompts[-1][1]
        self.assertIn("[REDACTED]", detail)
        self.assertNotIn("private-password-value", detail)

    def test_sensitive_action_refuses_if_hud_is_unavailable(self) -> None:
        ran = threading.Event()
        confirm.bind(None, None)

        result = action_policy.request_confirmation(
            "file_controller",
            "write",
            "Write file?",
            "Synthetic file contents",
            lambda: ran.set() or "written",
            arguments={"action": "write"},
        )

        self.assertIn("interface is not available", result)
        self.assertFalse(ran.is_set())

    def test_approval_runs_sensitive_action_once(self) -> None:
        completed = threading.Event()
        calls: list[int] = []

        def action() -> str:
            calls.append(1)
            completed.set()
            return "written"

        result = action_policy.request_confirmation(
            "file_controller",
            "write",
            "Write file?",
            "Synthetic file contents",
            action,
            arguments={"action": "write"},
        )
        self.assertIn("[CONFIRMATION_PENDING]", result)

        confirm.resolve(True)

        self.assertTrue(completed.wait(2))
        self.assertEqual(calls, [1])

    def test_task_history_clear_requires_hud_confirmation(self) -> None:
        ran = threading.Event()
        args = {"mode": "clear_history"}

        result = action_policy.dispatch(
            "task_planner",
            args,
            lambda: ran.set() or "status=done action=clear_history",
        )

        self.assertIn("[CONFIRMATION_PENDING]", result)
        self.assertFalse(ran.is_set())
        self.assertEqual(len(self.prompts), 1)
        self.assertIn("clear_history", self.prompts[0][1])

        confirm.resolve(True)

        self.assertTrue(ran.wait(2))

    def test_plan_authorizes_exact_nested_handler_signature(self) -> None:
        arguments = {"action": "activate_window", "title": "Synthetic"}
        called = []
        with action_policy.authorize_plan(
            [("computer_control", arguments)]
        ):
            result = action_policy.run_or_confirm(
                "computer_control",
                "activate_window",
                "title",
                "detail",
                lambda: called.append(True) or "ran",
                arguments=arguments,
            )

        self.assertEqual(result, "ran")
        self.assertEqual(called, [True])

    def test_routine_screen_action_runs_without_hud_confirmation(self) -> None:
        called = []

        result = action_policy.run_or_confirm(
            "computer_control",
            "activate_window",
            "Activate window?",
            "Synthetic window",
            lambda: called.append(True) or "status=done",
            arguments={"action": "activate_window", "title": "Settings"},
        )

        self.assertEqual(result, "status=done")
        self.assertEqual(called, [True])
        self.assertEqual(self.prompts, [])

    def test_critical_screen_target_still_requires_hud_confirmation(self) -> None:
        called = []

        result = action_policy.run_or_confirm(
            "computer_control",
            "screen_click",
            "Click button?",
            "Buy now",
            lambda: called.append(True) or "status=done",
            arguments={
                "action": "screen_click",
                "description": "Buy now",
            },
        )

        self.assertIn("[CONFIRMATION_PENDING]", result)
        self.assertEqual(called, [])
        self.assertEqual(len(self.prompts), 1)


if __name__ == "__main__":
    unittest.main()
