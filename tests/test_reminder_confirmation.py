from __future__ import annotations

import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from actions import reminder
from core import action_policy, confirm, policy, tool_audit


class ReminderConfirmationTests(unittest.TestCase):
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
    def future_args(message: str = "Synthetic reminder") -> dict[str, str]:
        target = datetime.now() + timedelta(days=1)
        return {
            "date": target.strftime("%Y-%m-%d"),
            "time": target.strftime("%H:%M"),
            "message": message,
        }

    def test_schedule_is_blocked_by_read_only_until_the_user_allows_it(self) -> None:
        """A reminder is a LOCAL write: silent, but not free.

        THE GATE IS IN THE POLICY LAYER, NOT IN THE ACTION.
            `core/action_policy.py` decides what has to be approved and the
            action does what it is told. A local write does not ask, but
            the read-only mode refuses it until the user allows the tool,
            and the refusal says what to do about it.
        """
        scheduled = threading.Event()
        args = self.future_args()
        with (
            patch.object(reminder, "_write_notify_script", return_value=object()) as write_script,
            patch.object(
                reminder,
                "_schedule_windows",
                side_effect=lambda *_args: scheduled.set() or "task-id",
            ) as schedule,
        ):
            with patch.object(policy, "read_only_enabled", return_value=True):
                result = action_policy.dispatch(
                    "reminder",
                    dict(args),
                    lambda: reminder.reminder(args),
                )

        self.assertIn("status=failed", result)
        self.assertIn("read-only", result)
        self.assertIn("Permissions", result)
        write_script.assert_not_called()
        schedule.assert_not_called()

    def test_approved_schedule_creates_the_job(self) -> None:
        scheduled = threading.Event()
        args = self.future_args()
        with (
            patch.object(reminder, "_write_notify_script", return_value=object()) as write_script,
            patch.object(
                reminder,
                "_schedule_windows",
                side_effect=lambda *_args: scheduled.set() or "task-id",
            ) as schedule,
        ):
            with patch.object(policy, "read_only_enabled", return_value=False):
                result = action_policy.dispatch(
                    "reminder",
                    dict(args),
                    lambda: reminder.reminder(args),
                )

        self.assertNotIn("status=failed", result)
        write_script.assert_called_once()
        schedule.assert_called_once()

    def test_invalid_or_overlong_reminder_is_rejected(self) -> None:
        result = reminder.reminder(self.future_args("x" * 201))

        self.assertIn("status=failed", result)
        self.assertEqual(self.prompts, [])

    def test_a_rejected_reminder_leaves_nothing_behind(self) -> None:
        """A text that is too long is refused BEFORE anything is written.

        A task that is registered and then abandoned is a task that fires
        later and shows a message that nobody asked for. It also happened
        here: the suite was creating real scheduled tasks on the machine of
        whoever ran it — one per run — and they went on firing long after
        the tests were over. That is where the console windows that
        appeared every now and then came from.
        """
        scripts = Path.home() / ".teemo" / "reminders"
        before = set(scripts.glob("TEEMOReminder_*.py")) if scripts.exists() else set()

        with patch.object(reminder, "_schedule_windows") as schedule:
            result = reminder.reminder(self.future_args("x" * 201))

        self.assertIn("status=failed", result)
        schedule.assert_not_called()
        after = set(scripts.glob("TEEMOReminder_*.py")) if scripts.exists() else set()
        self.assertEqual(after, before, "the reminder wrote a script anyway")

    def test_a_reminder_that_is_too_long_says_so_in_words(self) -> None:
        """The sentence says what is wrong, not just that something is."""
        result = reminder.reminder(self.future_args("y" * 250))
        self.assertIn("too long", result)
        self.assertIn("200", result)

    def test_reminder_schedule_is_high_risk(self) -> None:
        self.assertEqual(
            tool_audit.classify_risk("reminder", {"action": "schedule"}),
            "high",
        )


if __name__ == "__main__":
    unittest.main()
