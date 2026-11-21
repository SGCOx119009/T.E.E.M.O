from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


class SetupPlanTests(unittest.TestCase):
    def test_plan_is_read_only_and_describes_external_effects(self) -> None:
        root = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            [sys.executable, "setup.py", "--plan"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        # The heading carries this product's name, not the one the test was
        # written against. The contract being checked is "the plan is
        # printed, read-only, with these sections" - not which brand spells
        # it.
        self.assertIn("T.E.E.M.O. setup plan", result.stdout)
        self.assertIn("Network:", result.stdout)
        self.assertIn("Credentials/OAuth:", result.stdout)
        self.assertIn("not selected", result.stdout)
        self.assertNotIn("Installing Python dependencies", result.stdout)
        self.assertNotIn("Installing Playwright browsers", result.stdout)
