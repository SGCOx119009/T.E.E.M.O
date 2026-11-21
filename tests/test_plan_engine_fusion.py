"""The plan and the engine are one execution path, not two.

These tests hold the shape of that fusion in place: the steps of a verified
plan are run by the engine's runner, the runner is the only place a planned
tool call is made, the engine's store keeps the plan's checkpoint, and the
plan's history is still inspectable through `core.task_history`.
"""
from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import action_policy, task_history, task_plan
from core.tasks.engine import TaskEngine
from core.tasks.plan_runner import PlanRunner

_STEPS = [{
    "tool": "computer_control",
    "arguments": {"action": "wait", "seconds": 1},
    "purpose": "Wait briefly",
    "verification": {
        "tool": "computer_control",
        "arguments": {"action": "inspect_windows", "limit": 5},
        "expect": "Demo",
    },
}]


class PlanEngineFusionTests(unittest.TestCase):
    def _run_plan(self, task: str = "One execution path"):
        """Preview, approve and run a one-step plan; return what happened."""
        preview = task_plan.preview(task, _STEPS)
        task_id = re.search(r"task_id=(\w+)", preview).group(1)
        calls: list[str] = []

        def executor(_tool, args):
            calls.append(args["action"])
            return (
                "status=done action=wait verified=true"
                if args["action"] == "wait"
                else "status=done action=inspect_windows verified=true evidence=Demo"
            )

        token = action_policy._CONFIRMED_TOOL.set("task_planner")
        try:
            result = task_plan.execute(task, _STEPS, executor, task_id)
        finally:
            action_policy._CONFIRMED_TOOL.reset(token)
        return task_id, result, calls

    def test_plan_steps_run_through_the_engine_runner(self) -> None:
        runners: list[PlanRunner] = []
        original = task_plan._runner_for

        def spy(record, executor):
            runner = original(record, executor)
            runners.append(runner)
            return runner

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"LOCALAPPDATA": tmp}, clear=False):
                with patch.object(task_plan, "_runner_for", spy):
                    _task_id, result, calls = self._run_plan()

        self.assertIn("status=done", result)
        self.assertEqual(len(runners), 1)
        runner = runners[0]
        self.assertIsInstance(runner, PlanRunner)
        self.assertEqual(
            [outcome.kind for outcome in runner.trail],
            ["action", "verification"],
        )
        self.assertEqual(calls, ["wait", "inspect_windows"])
        self.assertEqual(runner.budget.used_rounds, 1)

    def test_the_runner_is_the_only_place_a_planned_call_is_made(self) -> None:
        """One execution path: the trusted executor is called from one file."""
        root = Path(__file__).resolve().parent.parent
        pattern = re.compile(r"\.executor\(")
        call_sites: list[str] = []
        for folder in ("core", "actions"):
            for path in sorted((root / folder).rglob("*.py")):
                for number, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), 1
                ):
                    if pattern.search(line):
                        call_sites.append(
                            f"{path.relative_to(root).as_posix()}:{number}"
                        )
        files = {site.split(":")[0] for site in call_sites}

        self.assertEqual(files, {"core/tasks/plan_runner.py"}, call_sites)
        self.assertTrue(call_sites)

    def test_engine_store_keeps_the_plan_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = TaskEngine(
                root / "tasks", root / "reports", [root / "agents"],
                autostart=False,
            )
            with patch.dict(os.environ, {"LOCALAPPDATA": tmp}, clear=False):
                with patch.object(task_plan, "_engine", return_value=engine):
                    task_id, result, _calls = self._run_plan("Checkpointed plan")

            self.assertIn("status=done", result)
            record = engine.store.get(task_id)
            self.assertIsNotNone(record)
            self.assertEqual(record["kind"], "plan")
            self.assertEqual(record["status"], "done")
            self.assertEqual(record["plan"]["state"], "completed")
            self.assertEqual(record["plan"]["completed"], 1)
            self.assertEqual(record["used"]["rounds"], 1)
            self.assertEqual(engine.list(), [])

    def test_plan_history_is_inspectable_through_task_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"LOCALAPPDATA": tmp}, clear=False):
                task_id, result, _calls = self._run_plan("Readable plan")
                records = task_history.read_history()

                self.assertIn("status=done", result)
                self.assertEqual(records[0]["task_id"], task_id)
                self.assertEqual(records[0]["state"], "completed")
                self.assertEqual(records[0]["completed"], 1)
                self.assertNotIn("Readable plan", records[0].values())

    def test_the_engine_never_schedules_a_plan_as_an_agent_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = TaskEngine(
                root / "tasks", root / "reports", [root / "agents"],
                autostart=False,
            )
            runner = engine.plan_runner(
                "plan0000000000000000000000000", lambda _tool, _args: "status=done",
                title="A plan",
            )
            runner.checkpoint({
                "task_id": "plan0000000000000000000000000",
                "state": "running", "current_step": 1, "total_steps": 2,
                "completed": 0, "recovery_attempts": 0, "recovery_kind": "",
            })

            record = engine.store.get("plan0000000000000000000000000")
            self.assertEqual(record["kind"], "plan")
            self.assertEqual(engine.list(), [])
            self.assertEqual(engine.get(record["id"])["status"], "running")


if __name__ == "__main__":
    unittest.main()
