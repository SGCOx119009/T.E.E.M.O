"""
tests/test_allowed_dirs.py — the file operator respects "allowed_dirs",
including the junctions that would otherwise smuggle a path out of them.

The junction case is the point of the whole thing, so it is not simulated:
a real directory junction is created with `mklink /J` and the real file
system is asked where it points. On a machine where junctions cannot be
created the test SKIPS, loudly, rather than passing on a fiction.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from actions import file_controller
from core import allowed_dirs


def _configured(roots: list[str]):
    """Patch the configuration so the assistant sees these allowed roots."""
    return patch(
        "memory.config_manager.load_api_keys",
        return_value={"allowed_dirs": roots},
    )


def _make_junction(link: Path, target: Path) -> bool:
    """A real NTFS directory junction, or False if this machine refuses."""
    try:
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return False
    return result.returncode == 0 and link.exists()


class AllowedDirsTests(unittest.TestCase):
    # ── The plain case ──────────────────────────────────────────────────────

    def test_path_inside_the_allowed_root_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "allowed"
            root.mkdir()
            inside = root / "note.txt"

            with _configured([str(root)]):
                self.assertTrue(allowed_dirs.is_allowed(inside))
                self.assertEqual(allowed_dirs.allowed_dirs(),
                                 [str(root.resolve())])
                # No exception: the path may be touched.
                allowed_dirs.require_allowed(inside)
                self.assertIn("allowed:", allowed_dirs.explain(inside))

    def test_path_outside_the_allowed_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "allowed"
            root.mkdir()
            outside = base / "elsewhere" / "note.txt"

            with _configured([str(root)]):
                self.assertFalse(allowed_dirs.is_allowed(outside))
                with self.assertRaises(PermissionError) as caught:
                    allowed_dirs.require_allowed(outside)
                self.assertIn("outside the allowed folders", str(caught.exception))
                self.assertIn("blocked:", allowed_dirs.explain(outside))

    # ── The junction case: written inside, really outside ──────────────────

    def test_junction_that_leaves_the_allowed_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "allowed"
            root.mkdir()
            hidden = base / "hidden"
            hidden.mkdir()
            link = root / "link"

            try:
                if not _make_junction(link, hidden):
                    self.skipTest(
                        "this machine does not allow creating directory "
                        "junctions (mklink /J failed)"
                    )

                # The junction really points where it says it points.
                self.assertEqual(
                    allowed_dirs.resolve_path(link),
                    hidden.resolve(),
                )

                with _configured([str(root)]):
                    # Written inside the root...
                    self.assertTrue(link.is_relative_to(root))
                    # ...but it really is somewhere else, so it is refused.
                    self.assertFalse(allowed_dirs.is_allowed(link))
                    self.assertFalse(allowed_dirs.is_allowed(link / "secret.txt"))
                    with self.assertRaises(PermissionError):
                        allowed_dirs.require_allowed(link / "secret.txt")
            finally:
                # Remove the link itself, never what it points at.
                if link.exists():
                    try:
                        os.rmdir(link)
                    except OSError:
                        pass

    def test_sibling_with_a_common_prefix_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "X"
            root.mkdir()
            sibling = base / "XY"
            sibling.mkdir()
            target = sibling / "note.txt"

            with _configured([str(root)]):
                # "…\X" and "…\XY" share a string prefix and nothing else.
                self.assertFalse(allowed_dirs.is_allowed(target))
                with self.assertRaises(PermissionError):
                    allowed_dirs.require_allowed(target)

    # ── The default: nothing configured, nothing restricted ─────────────────

    def test_without_allowed_dirs_every_path_is_allowed(self) -> None:
        with patch("memory.config_manager.load_api_keys", return_value={}):
            self.assertEqual(allowed_dirs.allowed_dirs(), [])
            self.assertTrue(allowed_dirs.is_allowed(Path(r"C:\anywhere\at\all")))
            self.assertTrue(allowed_dirs.is_allowed(Path("relative/thing.txt")))
            # No exception.
            allowed_dirs.require_allowed(Path(r"C:\anywhere\at\all"))
            self.assertIn("allowed:", allowed_dirs.explain(Path(".")))

    def test_broken_configuration_is_treated_as_no_roots(self) -> None:
        broken = [
            {},                                     # unreadable file -> {}
            {"allowed_dirs": "C:\\Users"},           # not a list
            {"allowed_dirs": [42, None]},            # not strings
            {"allowed_dirs": []},                    # configured empty
        ]
        for payload in broken:
            with self.subTest(payload=payload):
                with patch("memory.config_manager.load_api_keys",
                           return_value=payload):
                    self.assertEqual(allowed_dirs.allowed_dirs(), [])
                    self.assertTrue(allowed_dirs.is_allowed(Path(r"C:\anywhere")))

        with patch("memory.config_manager.load_api_keys",
                   side_effect=RuntimeError("the file is unreadable")):
            self.assertEqual(allowed_dirs.allowed_dirs(), [])
            self.assertTrue(allowed_dirs.is_allowed(Path(r"C:\anywhere")))


class FileOperatorAllowedDirsTests(unittest.TestCase):
    """The operator of files obeys the configured roots."""

    def test_write_outside_the_allowed_root_is_refused_and_touches_nothing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "allowed"
            root.mkdir()
            outside = base / "outside"
            outside.mkdir()
            target = outside / "note.txt"

            with _configured([str(root)]):
                result = file_controller.write_file(
                    str(outside), name="note.txt", content="synthetic"
                )

            self.assertIn("status=failed", result)
            self.assertIn("outside the allowed folders", result)
            self.assertFalse(target.exists())

    def test_write_inside_the_allowed_root_still_works(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "allowed"
            root.mkdir()
            target = root / "note.txt"

            with _configured([str(root)]):
                result = file_controller.write_file(
                    str(root), name="note.txt", content="synthetic"
                )

            self.assertIn("Written to", result)
            self.assertTrue(target.exists())

    def test_write_through_a_junction_out_of_the_root_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "allowed"
            root.mkdir()
            hidden = base / "hidden"
            hidden.mkdir()
            link = root / "link"

            try:
                if not _make_junction(link, hidden):
                    self.skipTest(
                        "this machine does not allow creating directory "
                        "junctions (mklink /J failed)"
                    )

                with _configured([str(root)]):
                    result = file_controller.write_file(
                        str(link), name="secret.txt", content="synthetic"
                    )

                self.assertIn("status=failed", result)
                self.assertIn("outside the allowed folders", result)
                # Nothing was written — not inside the junction, not anywhere.
                self.assertFalse((hidden / "secret.txt").exists())
                self.assertFalse((link / "secret.txt").exists())
            finally:
                if link.exists():
                    try:
                        os.rmdir(link)
                    except OSError:
                        pass

    def test_copy_destination_outside_the_allowed_root_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "allowed"
            root.mkdir()
            outside = base / "outside"
            outside.mkdir()
            source = root / "note.txt"
            source.write_text("synthetic", encoding="utf-8")

            with _configured([str(root)]):
                result = file_controller.copy_file(
                    str(root), name="note.txt", destination=str(outside)
                )

            self.assertIn("status=failed", result)
            self.assertIn("outside the allowed folders", result)
            self.assertFalse((outside / "note.txt").exists())

    def test_reading_outside_the_allowed_root_is_not_restricted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "allowed"
            root.mkdir()
            outside = base / "outside"
            outside.mkdir()
            (outside / "note.txt").write_text("synthetic", encoding="utf-8")

            with _configured([str(root)]):
                result = file_controller.read_file(
                    str(outside), name="note.txt"
                )

            # Listing and reading are not acts on the disk.
            self.assertNotIn("status=failed", result)
            self.assertEqual(result, "synthetic")


if __name__ == "__main__":
    unittest.main()
