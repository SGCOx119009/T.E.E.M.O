"""
The risk catalogue, the read-only mode and the grants — the rules the FASE 9
calls for, and the ones that decide what the assistant may do to the
machine.

WHY THESE RULES ARE TESTED SO HARD
    Every other test in this suite asks "does the assistant work?". These
    ask "may the assistant do that?", and the second question is the one
    that costs something when it is answered wrongly. A policy that can be
    talked out of the arguments is not a policy; a grant for one tool that
    opens every tool is not a grant; a read-only mode that a corrupt
    configuration file turns off is not a mode.
"""
from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from core import policy


class _NoConfig:
    """The configuration, held still: nothing is read and nothing is written."""

    def __init__(self, data: dict | None = None) -> None:
        self.data = data if isinstance(data, dict) else {}

    def read(self) -> dict:
        return dict(self.data)

    def write(self, **fields) -> bool:
        self.data.update(fields)
        return True


class RiskCatalogTests(unittest.TestCase):
    """The seven classes, and the catalogue that describes them."""

    def test_there_are_seven_classes_and_no_more(self) -> None:
        self.assertEqual(len(policy.RISK_CLASSES), 7)
        self.assertEqual(
            policy.RISK_CLASSES,
            ("READ", "WRITE_LOCAL", "REVERSIBLE_SYS", "EXTERNAL",
             "IRREVERSIBLE", "CREDENTIAL", "PRIVACY"),
        )

    def test_every_class_says_what_it_is_and_what_happens_to_it(self) -> None:
        for name in policy.RISK_CLASSES:
            entry = policy.RISK_CATALOG[name]
            self.assertTrue(entry.meaning.strip(), name)
            self.assertTrue(entry.treatment.strip(), name)
            self.assertEqual(entry.name, name)

    def test_only_reading_is_silent(self) -> None:
        """READ is silent; the four dangerous classes ask."""
        self.assertFalse(policy.RISK_CATALOG["READ"].needs_confirmation)
        self.assertFalse(policy.RISK_CATALOG["WRITE_LOCAL"].needs_confirmation)
        self.assertFalse(policy.RISK_CATALOG["REVERSIBLE_SYS"].needs_confirmation)
        for name in ("EXTERNAL", "IRREVERSIBLE", "CREDENTIAL", "PRIVACY"):
            self.assertTrue(policy.RISK_CATALOG[name].needs_confirmation, name)


class RiskClassDerivationTests(unittest.TestCase):
    """The class is re-derived from the resolved tool, never from a label."""

    def test_the_real_cases_of_the_catalogue(self) -> None:
        cases = {
            ("file_controller", "write"): "WRITE_LOCAL",
            ("file_controller", "delete"): "IRREVERSIBLE",
            ("file_controller", "read"): "READ",
            ("computer_settings", "volume_up"): "REVERSIBLE_SYS",
            ("computer_settings", "shutdown"): "IRREVERSIBLE",
            ("computer_settings", "toggle_wifi"): "IRREVERSIBLE",
            ("send_message", "send"): "EXTERNAL",
            ("gmail_control", "send"): "EXTERNAL",
            ("calendar_control", "create"): "EXTERNAL",
            ("web_search", "search"): "READ",
            ("weather_report", ""): "READ",
            ("save_memory", "save"): "WRITE_LOCAL",
            ("emotional_spectrum", ""): "REVERSIBLE_SYS",
            ("computer_control", "screenshot"): "PRIVACY",
            ("code_helper", "run"): "EXTERNAL",
        }
        for (tool, action), expected in cases.items():
            self.assertEqual(policy.risk_class_of(tool, action), expected,
                             f"{tool}/{action}")

    def test_a_credential_tool_is_a_credential(self) -> None:
        self.assertEqual(policy.risk_class_of("vault_credentials"), "CREDENTIAL")
        self.assertEqual(policy.risk_class_of("api_key_store"), "CREDENTIAL")

    def test_the_model_cannot_label_its_own_action(self) -> None:
        """A class written by the caller changes nothing."""
        for label in ("READ", "read", "safe", "low"):
            self.assertEqual(
                policy.risk_class_of("file_controller", "write",
                                     {"risk": label, "class": label,
                                      "risk_class": label}),
                "WRITE_LOCAL",
            )

    def test_a_tool_with_no_policy_is_not_a_low_risk_tool(self) -> None:
        """Unreviewed means refused, not allowed."""
        self.assertFalse(policy.is_reviewed("a_tool_nobody_ever_classified"))
        verdict = policy.evaluate("a_tool_nobody_ever_classified", "do")
        self.assertFalse(verdict.allowed)
        self.assertIn("no reviewed risk classification", verdict.reason)


class ReadOnlyTests(unittest.TestCase):
    """Read-only is the default, and a broken configuration cannot lift it."""

    def setUp(self) -> None:
        self.config = _NoConfig({})
        patcher = patch.object(policy, "_config", self.config.read)
        patcher.start()
        self.addCleanup(patcher.stop)
        writer = patch.object(policy, "_write_config", self.config.write)
        writer.start()
        self.addCleanup(writer.stop)
        with policy._grants_lock:
            policy._grants.clear()
        self.addCleanup(lambda: policy._grants.clear())

    def test_read_only_is_on_when_nothing_is_configured(self) -> None:
        self.assertTrue(policy.read_only_enabled())

    def test_a_corrupt_configuration_does_not_open_the_gate(self) -> None:
        for value in ("yes", 17, [], {}, None, "not a boolean"):
            self.config.data["read_only"] = value
            self.assertTrue(policy.read_only_enabled(), repr(value))

    def test_read_only_can_be_turned_off_and_remembered(self) -> None:
        policy.set_read_only(False)
        self.assertFalse(policy.read_only_enabled())
        self.assertIs(self.config.data["read_only"], False)
        policy.set_read_only(True)
        self.assertTrue(policy.read_only_enabled())

    def test_a_write_in_read_only_is_refused_with_an_actionable_message(self) -> None:
        verdict = policy.evaluate("file_controller", "write")
        self.assertFalse(verdict.allowed)
        self.assertIn("file_controller", verdict.reason)
        self.assertIn("permission", verdict.reason.lower())
        self.assertIn("permissions panel", verdict.reason.lower())
        # Not silence, and not a generic error.
        self.assertTrue(verdict.reason.strip())
        self.assertNotIn("status=failed", verdict.reason)

    def test_reading_is_always_allowed(self) -> None:
        verdict = policy.evaluate("web_search", "search")
        self.assertTrue(verdict.allowed)
        self.assertEqual(verdict.risk_class, "READ")
        self.assertFalse(verdict.needs_confirmation)

    def test_a_grant_opens_exactly_one_tool(self) -> None:
        policy.grant("file_controller")
        self.assertTrue(policy.evaluate("file_controller", "write").allowed)
        self.assertFalse(policy.evaluate("send_message", "send").allowed)

    def test_a_grant_for_one_action_does_not_open_the_others(self) -> None:
        policy.grant("file_controller", "write")
        self.assertTrue(policy.evaluate("file_controller", "write").allowed)
        self.assertFalse(policy.evaluate("file_controller", "delete").allowed)


class GrantTests(unittest.TestCase):
    """A grant is per tool, and it can run out."""

    def setUp(self) -> None:
        self.config = _NoConfig({})
        patcher = patch.object(policy, "_config", self.config.read)
        patcher.start()
        self.addCleanup(patcher.stop)
        writer = patch.object(policy, "_write_config", self.config.write)
        writer.start()
        self.addCleanup(writer.stop)
        with policy._grants_lock:
            policy._grants.clear()
        self.addCleanup(lambda: policy._grants.clear())

    def test_a_grant_is_written_and_comes_back_after_a_restart(self) -> None:
        policy.grant("file_controller", "", 3600)
        self.assertIn("permissions", self.config.data)
        # The restart: the memory is empty and only the file is left.
        with policy._grants_lock:
            policy._grants.clear()
        self.assertTrue(policy.is_granted("file_controller"))
        self.assertTrue(policy.evaluate("file_controller", "write").allowed)

    def test_a_grant_without_an_expiry_never_runs_out(self) -> None:
        entry = policy.grant("send_message")
        self.assertIsNone(entry.expires_at)
        self.assertFalse(entry.expired())

    def test_a_grant_with_an_expiry_runs_out(self) -> None:
        entry = policy.grant("send_message", "", -1)
        self.assertTrue(entry.expired())
        self.assertFalse(policy.is_granted("send_message"))
        self.assertFalse(policy.evaluate("send_message", "send").allowed)

    def test_an_expired_grant_saved_on_disk_is_ignored(self) -> None:
        self.config.data["permissions"] = [
            {"tool": "file_controller", "action": "", "expires_at": time.time() - 10},
        ]
        self.assertFalse(policy.is_granted("file_controller"))

    def test_revoking_takes_the_permission_back(self) -> None:
        policy.grant("file_controller")
        self.assertTrue(policy.is_granted("file_controller"))
        policy.revoke("file_controller")
        self.assertFalse(policy.is_granted("file_controller"))

    def test_the_tools_that_need_a_permission_are_the_ones_above_read(self) -> None:
        tools = policy.tools_above_read()
        self.assertIn("file_controller", tools)
        self.assertIn("send_message", tools)
        self.assertNotIn("web_search", tools)
        for name in tools:
            # The class of a TOOL is the worst of its actions: `music_player`
            # reads nothing and plays music, and what the user decides about
            # is the playing.
            self.assertNotEqual(policy.tool_class(name), "READ", name)
        self.assertEqual(policy.tool_class("music_player"), "REVERSIBLE_SYS")


class MaskingTests(unittest.TestCase):
    """The banner shows that a secret is going out, without showing it."""

    def test_a_secret_is_shortened_and_never_shown_whole(self) -> None:
        masked = policy.mask_secret("sk-abc123def456f4a")
        self.assertIn("…", masked)
        self.assertTrue(masked.startswith("sk-"))
        self.assertNotIn("abc123def456", masked)
        self.assertLess(len(masked), len("sk-abc123def456f4a") // 2)

    def test_a_short_secret_is_not_revealed_either(self) -> None:
        masked = policy.mask_secret("short")
        self.assertNotIn("short", masked)

    def test_the_key_stays_and_the_value_is_masked(self) -> None:
        masked = policy.mask_arguments({"user": "sergio", "api_key": "sk-abc123def456f4a"})
        self.assertIn("api_key", masked)
        self.assertEqual(masked["user"], "sergio")
        self.assertIn("…", masked["api_key"])
        self.assertNotIn("abc123def456", masked["api_key"])

    def test_a_secret_inside_a_form_is_masked(self) -> None:
        masked = policy.mask_arguments({
            "fields": {
                'input[name="email"]': "person@example.test",
                'input[name="password"]': "private-password",
            },
        })
        self.assertIn("person@example.test", str(masked))
        self.assertNotIn("private-password", str(masked))

    def test_the_text_typed_into_a_password_field_is_masked(self) -> None:
        """The key is called "text"; the FIELD is called Password."""
        masked = policy.mask_arguments({
            "selector": {"name": "Password", "control_type": "Edit"},
            "text": "private-password-value",
        })
        self.assertNotIn("private-password-value", str(masked))


if __name__ == "__main__":
    unittest.main()
