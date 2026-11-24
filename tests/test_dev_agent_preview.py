"""The coding agent shows the plan before it writes, and asks only once.

The plan of this file is the contract that the inherited tests already asked
for, plus the parts they could not see:

  * the EXECUTOR must not ask on top of the handler's own preview — one
    action, one question (the rule of the FASE 9);
  * the read-only mode must stop it before anything is planned;
  * a concession granted in the permissions panel must let it through
    without a banner;
  * the mode that changes an existing folder must show its plan too, and
    must not touch a single file before the human approves it.

Nothing here runs code, installs anything or opens an editor: the agent
writes source files and leaves the next step to the human.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from actions import dev_agent
from core import action_policy, confirm, policy


PLAN = {
    "project_name": "synthetic-demo",
    "entry_point": "main.py",
    "files": [
        {
            "path": "main.py",
            "description": "Synthetic entry point",
            "imports": [],
        },
    ],
    "run_command": "python main.py",
    "dependencies": ["synthetic-package>=1"],
}


class DevAgentPreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        with confirm._lock:
            confirm._pending = None
        self.prompts: list[tuple[str, str]] = []
        confirm.bind(
            lambda title, detail: self.prompts.append((title, detail)),
            lambda: None,
        )
        self.projects = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.projects)
        self.project_root = patch.object(dev_agent, "PROJECTS_DIR", self.root)
        self.project_root.start()
        self.addCleanup(self.project_root.stop)
        self.planner = patch.object(dev_agent, "_plan_project", return_value=PLAN)
        self.planner.start()
        self.addCleanup(self.planner.stop)

    def tearDown(self) -> None:
        with confirm._lock:
            confirm._pending = None
        confirm.bind(None, None)

    def _wait_for(self, predicate, seconds: float = 3.0) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return predicate()

    def _wait_for_the_job_to_end(self, seconds: float = 8.0) -> bool:
        """The job holds the queue while it waits for the human, so a test
        that starts another job has to let the first one finish."""
        return self._wait_for(lambda: not dev_agent._job_lock.locked(), seconds)

    # ── one question, not two ───────────────────────────────────────────────

    def test_the_executor_does_not_ask_on_top_of_the_preview(self) -> None:
        with patch.object(policy, "read_only_enabled", return_value=False):
            result = action_policy.dispatch(
                "dev_agent",
                {"description": "Create a synthetic demo", "mode": "build"},
                lambda: dev_agent.dev_agent({
                    "description": "Create a synthetic demo",
                    "mode": "build",
                }),
            )
        self.assertIn("[CONFIRMATION_PENDING]", result)
        self.assertEqual(
            len(self.prompts), 1,
            "the user must be asked ONCE, not twice",
        )
        self.assertIn("main.py", self.prompts[0][1])

    def test_read_only_stops_it_before_anything_is_planned(self) -> None:
        with patch.object(policy, "read_only_enabled", return_value=True), \
             patch.object(dev_agent, "_plan_project") as planner:
            result = action_policy.dispatch(
                "dev_agent",
                {"description": "Create a synthetic demo", "mode": "build"},
                lambda: dev_agent.dev_agent({
                    "description": "Create a synthetic demo",
                    "mode": "build",
                }),
            )
        self.assertIn("status=failed", result)
        self.assertIn("read-only", result)
        self.assertEqual(self.prompts, [])
        planner.assert_not_called()

    def test_a_concession_lets_it_through_without_a_banner(self) -> None:
        with patch.object(policy, "read_only_enabled", return_value=False), \
             patch.object(policy, "is_granted", return_value=True), \
             patch.object(dev_agent, "_get_model", return_value=SimpleNamespace(
                 generate_content=lambda _prompt: SimpleNamespace(
                     text="print('synthetic')"))):
            action_policy.dispatch(
                "dev_agent",
                {"description": "Create a synthetic demo", "mode": "build"},
                lambda: dev_agent.dev_agent({
                    "description": "Create a synthetic demo",
                    "mode": "build",
                }),
            )
        self.assertEqual(self.prompts, [])
        self.assertTrue((self.root / "synthetic-demo" / "main.py").exists())

    # ── what the agent never does ──────────────────────────────────────────

    def test_the_agent_has_no_way_to_run_install_or_open(self) -> None:
        for gone in ("_run_project", "_install_dependencies", "_open_vscode"):
            self.assertFalse(
                hasattr(dev_agent, gone),
                f"{gone} is back: the agent can act on the machine",
            )

    def test_the_preview_says_what_will_not_happen(self) -> None:
        dev_agent.dev_agent({
            "description": "Create a synthetic demo",
            "mode": "build",
        })
        detail = self.prompts[-1][1]
        self.assertIn("will NOT be run", detail)
        self.assertIn("will NOT be installed", detail)
        self.assertNotIn("python main.py", detail)

    # ── the folder mode shows its plan as well ─────────────────────────────

    def test_the_folder_mode_shows_the_plan_and_writes_nothing_first(self) -> None:
        folder = self.root / "existing-project"
        folder.mkdir()
        target = folder / "main.py"
        target.write_text("print('before')\n", encoding="utf-8")

        with patch.object(dev_agent, "_scan", return_value=(
                ["main.py"], {"main.py": "print('before')\n"}, {})), \
             patch.object(dev_agent, "_project_brief", return_value=""), \
             patch.object(dev_agent, "_get_model", return_value=SimpleNamespace(
                 generate_content=lambda _prompt: SimpleNamespace(
                     text="print('after')\n"))), \
             patch.object(dev_agent, "_plan_changes", return_value={
                 "summary": "Add a greeting.",
                 "changes": [{"path": "main.py", "action": "edit",
                              "what": "greet the user"}],
             }):
            result = dev_agent.dev_agent({
                "mode": "improve",
                "folder": str(folder),
                "description": "Add a greeting",
            })

            got = self._wait_for(lambda: bool(self.prompts))
            self.assertTrue(
                got,
                "the plan never reached the screen "
                f"(result={result!r} lock={dev_agent._job_lock.locked()} "
                f"pending={confirm._pending is not None} "
                f"job={dev_agent._job_snapshot().get('status')!r})",
            )
            detail = self.prompts[-1][1]
            self.assertIn("main.py", detail)
            self.assertIn("Add a greeting.", detail)
            self.assertEqual(
                target.read_text(encoding="utf-8"), "print('before')\n",
                "the file was written BEFORE the human approved the plan",
            )

            confirm.resolve(True)

            self.assertTrue(
                self._wait_for(
                    lambda: target.read_text(encoding="utf-8")
                    != "print('before')\n"),
                "the approved change was never written",
            )
            self.assertTrue(self._wait_for_the_job_to_end())
        self.assertIn("[STARTED]", result)

    def test_a_refused_plan_leaves_the_folder_alone(self) -> None:
        folder = self.root / "existing-project"
        folder.mkdir()
        target = folder / "main.py"
        target.write_text("print('before')\n", encoding="utf-8")

        with patch.object(dev_agent, "_scan", return_value=(
                ["main.py"], {"main.py": "print('before')\n"}, {})), \
             patch.object(dev_agent, "_project_brief", return_value=""), \
             patch.object(dev_agent, "_plan_changes", return_value={
                 "summary": "Add a greeting.",
                 "changes": [{"path": "main.py", "action": "edit",
                              "what": "greet the user"}],
             }):
            dev_agent.dev_agent({
                "mode": "improve",
                "folder": str(folder),
                "description": "Add a greeting",
            })
            self.assertTrue(self._wait_for(lambda: bool(self.prompts)))
            confirm.resolve(False)

            self.assertTrue(
                self._wait_for_the_job_to_end(),
                "the job never let go of the queue after the refusal",
            )
            self.assertEqual(
                target.read_text(encoding="utf-8"), "print('before')\n",
                "a refused plan changed the folder anyway",
            )
            self.assertEqual(list(folder.glob("main.py")), [target])


if __name__ == "__main__":
    unittest.main()
