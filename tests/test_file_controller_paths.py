from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from actions import file_controller


class FileControllerPathTests(unittest.TestCase):
    def test_rename_rejects_parent_traversal_and_preserves_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.txt"
            source.write_text("synthetic", encoding="utf-8")

            with (
                patch.object(file_controller, "_resolve_path", return_value=root),
                patch.object(file_controller, "_SAFE_ROOTS", [root]),
            ):
                result = file_controller.rename_file(
                    ".", name=source.name, new_name="../outside.txt"
                )

            self.assertIn("single safe filename", result)
            self.assertTrue(source.exists())
            self.assertFalse((root.parent / "outside.txt").exists())

    def test_rename_refuses_destination_outside_safe_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.txt"
            source.write_text("synthetic", encoding="utf-8")

            with (
                patch.object(file_controller, "_resolve_path", return_value=root),
                patch.object(file_controller, "_SAFE_ROOTS", [root]),
            ):
                result = file_controller.rename_file(
                    ".", name=source.name, new_name=str(root.parent / "outside.txt")
                )

            self.assertIn("single safe filename", result)
            self.assertTrue(source.exists())


if __name__ == "__main__":
    unittest.main()
