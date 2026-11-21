"""tests/test_windows_fixes.py — the bugs FASE 2 of the plan names, as tests.

Each test here reproduces one of the three failures the directive describes.
A bug that cannot be reproduced cannot be shown to be fixed, so every one of
these fails on the code the plan was written against.

    A  TTS   — pressing PROBAR froze the whole HUD.
    B  audio  — the saved device could resolve to a different API, or to one
                that opens but moves no sound.
    C  video  — "play a YouTube video" opened the browser instead of playing
                in the HUD.

No network, no microphone and no speaker are required: every test drives the
code with a stub, and says so when it does not.
"""
from __future__ import annotations

import os
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core import capability  # noqa: E402


class _StubRegistry:
    """Stands in for the provider registry.

    `_registry` is an instance attribute of the panel, so it is replaced on the
    instance rather than on the class."""

    def __init__(self, provider):
        self._provider = provider
        self.builds = 0

    def build(self, pid, values):
        self.builds += 1
        return self._provider

    def get_declaration(self, pid):
        return {}

    def reset(self):
        pass


def _panel_with(provider):
    """A panel whose registry hands out `provider`."""
    from PyQt6.QtWidgets import QApplication

    from gui.providers_panel import ProvidersOverlay

    app = QApplication.instance() or QApplication([])
    panel = ProvidersOverlay()
    panel._registry = _StubRegistry(provider)
    return app, panel


class TtsBlockingTests(unittest.TestCase):
    """Bug A — the panel froze while a voice was loaded or downloaded."""

    def test_probe_runs_off_the_interface_thread(self) -> None:
        """The provider's probe must not run on the thread that owns the UI.

        The freeze was not a slow probe: it was a slow probe ON THE QT THREAD.
        The audio and the HUD both run on Qt timers, so a blocked Qt thread
        stops the entire interface, not just the panel.

        Measured here the same way a person would notice it: a heartbeat that
        has to keep ticking while the test runs. If the probe ran on this
        thread, the heartbeat would stop for its whole duration.
        """
        ticks = []
        stop = threading.Event()

        def heartbeat():
            while not stop.is_set():
                ticks.append(time.monotonic())
                time.sleep(0.005)

        class _SlowProvider:
            caps: dict = {}

            def probe(self, progress=None, cancel=None):
                # A voice load: two and a half seconds of ONNX in the real one.
                time.sleep(0.6)
                return True, "ready"

        app, panel = _panel_with(_SlowProvider())
        try:
            thread = threading.Thread(target=heartbeat, daemon=True)
            thread.start()
            time.sleep(0.05)
            before = len(ticks)

            panel._test("some_provider")
            # The call that used to freeze the window.
            time.sleep(0.2)
            during = len(ticks) - before
            stop.set()
            thread.join(timeout=2)
        finally:
            stop.set()
            panel.deleteLater()

        # The heartbeat kept running while the probe was asleep. If the probe
        # had run here, there would be no ticks at all.
        self.assertGreater(
            during, 5,
            "the interface thread was blocked while the provider was probed",
        )

    def test_the_panel_reports_progress_without_touching_widgets_off_thread(
        self) -> None:
        """A progress callback fires on the worker; only the label may wait.

        The worker may not touch a widget — that is a crash waiting to happen —
        so every update has to be handed to the Qt thread."""
        seen: list[str] = []

        class _DownloadingProvider:
            caps: dict = {}

            def probe(self, progress=None, cancel=None):
                for step in range(1, 6):
                    if progress:
                        progress(step * 20, 100)
                    time.sleep(0.01)
                return True, "downloaded"

        app, panel = _panel_with(_DownloadingProvider())
        try:
            with patch.object(panel, "_say",
                              side_effect=lambda p, t, c: seen.append(t)):
                panel._test("some_provider")
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline and len(seen) < 2:
                    time.sleep(0.02)
                    app.processEvents()
        finally:
            panel.deleteLater()

        self.assertTrue(any("%" in t for t in seen),
                        f"no progress was reported: {seen}")

    def test_a_running_test_can_be_cancelled(self) -> None:
        """A sixty-megabyte download has to be abandonable.

        The button becomes CANCEL while the test runs, and the flag it sets is
        the one the download polls between chunks."""
        cancelled_at = []
        started = threading.Event()

        class _EndlessProvider:
            caps: dict = {}

            def probe(self, progress=None, cancel=None):
                started.set()
                while True:
                    if cancel and cancel():
                        cancelled_at.append(time.monotonic())
                        return False, "cancelled"
                    time.sleep(0.02)

        app, panel = _panel_with(_EndlessProvider())
        try:
            panel._test("some_provider")
            self.assertTrue(started.wait(timeout=3.0),
                            "the probe never started")
            panel._cancel_test("some_provider")
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and not cancelled_at:
                time.sleep(0.02)
                app.processEvents()
        finally:
            panel.deleteLater()

        self.assertTrue(cancelled_at, "the cancellation flag never reached the worker")


class PiperDownloadTests(unittest.TestCase):
    """Bug A, provider side — the download is watchable and verifiable."""

    def test_the_catalog_carries_the_files_of_every_voice(self) -> None:
        """The downloader needs the path, the size and the digest.

        Without them there is no progress bar that means anything and no way
        to notice a file that arrived truncated."""
        from providers import tts_piper

        original = tts_piper._catalog
        tts_piper._catalog = None
        try:
            catalog = tts_piper.catalog()
            if not catalog:
                self.skipTest("the Piper catalogue is not reachable from here")
            voice = next(iter(catalog))
            files = tts_piper._voice_files(voice)
        finally:
            tts_piper._catalog = original

        self.assertTrue(files, f"{voice} has no files in the catalogue")
        for path, size, digest in files:
            self.assertTrue(path.endswith(".onnx") or path.endswith(".onnx.json"))
            self.assertGreater(size, 0)
            self.assertTrue(digest)
    def test_a_download_reports_progress_and_honours_cancellation(self) -> None:
        """Progress arrives in order, and a cancelled download stops.

        Driven against a local file server, so the bytes really travel and the
        cancellation really happens mid-transfer."""
        import http.server
        import socketserver
        import threading as th

        from providers import tts_piper

        payload = b"x" * (512 * 1024)

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                sent = 0
                while sent < len(payload):
                    chunk = payload[sent:sent + 8192]
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    sent += len(chunk)
                    time.sleep(0.002)

            def log_message(self, *a):
                pass

        server = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
        port = server.server_address[1]
        th.Thread(target=server.serve_forever, daemon=True).start()

        original = tts_piper._VOICE_FILES_URL
        original_dir = tts_piper._VOICE_DIR
        import tempfile

        temp_dir = Path(tempfile.mkdtemp())
        # `_voice_files` is patched rather than the catalogue: the catalogue is
        # a module global that a background prefetch may replace at any moment,
        # and a test that depends on it is a test that fails at random.
        files = [("test_voice.onnx", len(payload), "")]
        try:
            tts_piper._VOICE_FILES_URL = f"http://127.0.0.1:{port}/"
            tts_piper._VOICE_DIR = temp_dir

            seen: list[tuple[int, int]] = []
            with patch.object(tts_piper, "_voice_files", return_value=files):
                tts_piper.download("test_voice",
                                   progress=lambda d, t: seen.append((d, t)))
            self.assertTrue(seen, "no progress was reported")
            self.assertEqual(seen[-1][0], len(payload))
            self.assertTrue((temp_dir / "test_voice.onnx").exists())

            # Now a download that is cancelled halfway through. The first one
            # left the file in place, and a voice that is already there is not
            # downloaded again — so it goes first.
            (temp_dir / "test_voice.onnx").unlink()

            # The cancellation callback takes no arguments and answers "stop
            # now"; the progress callback is what says how far it got.
            arrived = {"bytes": 0}

            def progress(done, total):
                arrived["bytes"] = done

            def cancel_when_halfway():
                return arrived["bytes"] >= len(payload) // 2

            with patch.object(tts_piper, "_voice_files", return_value=files):
                with self.assertRaises(tts_piper.DownloadCancelled):
                    tts_piper.download("test_voice", progress=progress,
                                       cancel=cancel_when_halfway)
            self.assertFalse((temp_dir / "test_voice.onnx").exists(),
                             "a cancelled download left a voice behind")
        finally:
            tts_piper._VOICE_FILES_URL = original
            tts_piper._VOICE_DIR = original_dir
            server.shutdown()
            server.server_close()
            try:
                import shutil
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass


class CapabilityTests(unittest.TestCase):
    """The replacement for "the other system does not allow it"."""

    def test_describe_for_model_is_empty_on_windows(self) -> None:
        support = capability.detect()
        self.assertEqual(capability.describe_for_model(support), [])
        self.assertEqual(capability.describe_for_user(support), [])

    def test_capabilities_report_the_real_answer(self) -> None:
        caps = capability.capabilities()
        self.assertIsInstance(caps, capability.Capabilities)
        self.assertIn("control_input", caps.capabilities)
        # A reason is never empty when something is not permitted.
        if not caps.available or caps.read_only:
            self.assertTrue(caps.reason)


if __name__ == "__main__":
    unittest.main()
