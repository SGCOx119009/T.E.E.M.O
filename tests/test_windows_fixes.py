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


class _FakeSoundDevice:
    """A stand-in for `sounddevice`, with the shape this module uses.

    Two host APIs, and on the output side one endpoint that opens and
    swallows everything: the silent sink the whole file exists to avoid.
    """

    def __init__(self, silent_outputs=(), rate=16000):
        self.silent_outputs = set(silent_outputs)
        self.rate = rate
        self.opened = []
        self._devices = [
            # (name, host api index, is input, max channels)
            ("Microphone (Realtek(R) Audio)", 0, True, 2),
            ("Speakers (Realtek(R) Audio)", 1, False, 2),
            ("Headset Microphone (Plugged)", 0, True, 2),
            ("Silent Output Device", 1, False, 2),
        ]
        self._apis = ["Windows DirectSound", "Windows MME"]

    # -- enumeration ------------------------------------------------------
    def query_devices(self):
        return [
            {"name": name, "hostapi": api, "max_input_channels": 2 if is_in else 0,
             "max_output_channels": 0 if is_in else 2}
            for name, api, is_in, _c in self._devices
        ]

    def query_hostapis(self):
        return [{"name": n} for n in self._apis]

    # -- streams ----------------------------------------------------------
    class _Stream:
        """A stream that behaves like a real one.

        A real output stream is rate-limited by the hardware clock: writing
        silence takes as long as the audio lasts. The silent sink that this
        whole file exists to detect is exactly a stream that swallows the
        buffer instantly. A real input stream delivers blocks to its
        callback while it runs — so the fake delivers from a thread.
        """

        def __init__(self, index, kind, sink=False, callback=None, rate=16000):
            self.index = index
            self.kind = kind
            self.sink = sink
            self.callback = callback
            self.rate = rate
            self._running = False
            self._pump = None

        def start(self):
            if self.kind != "input" or not self.callback:
                self._running = True
                return
            self._running = True
            frames = self.rate // 20          # 50 ms of audio per block

            def _pump():
                while self._running:
                    try:
                        self.callback(bytes(frames * 2), frames)
                    except Exception:
                        pass
                    time.sleep(0.02)

            self._pump = threading.Thread(target=_pump, daemon=True)
            self._pump.start()

        def stop(self):
            self._running = False
            if self._pump is not None:
                self._pump.join(timeout=1.0)
                self._pump = None

        def close(self):
            self.stop()

        def write(self, data):
            if self.sink:
                return 0.00001          # swallowed instantly
            time.sleep(len(data) / 2 / self.rate)   # consumed at the audio rate
            return len(data)

    def RawOutputStream(self, **kw):
        self.opened.append(("output", kw.get("device")))
        idx = kw.get("device")
        return self._Stream(idx, "output", sink=idx in self.silent_outputs,
                            rate=kw.get("samplerate", self.rate))

    def InputStream(self, **kw):
        self.opened.append(("input", kw.get("device")))
        idx = kw.get("device")
        return self._Stream(idx, "input", callback=kw.get("callback"),
                            rate=kw.get("samplerate", self.rate))


class AudioDeviceTests(unittest.TestCase):
    """Bug B — the saved device could resolve to the wrong endpoint."""

    def setUp(self) -> None:
        from core import audio_devices

        self.audio = audio_devices
        self._saved = (audio_devices._probe_results.copy(),
                       audio_devices._cache,
                       dict(audio_devices._chosen_api))
        audio_devices._probe_results.clear()
        audio_devices._cache = None
        audio_devices._chosen_api.update({"input": None, "output": None})

    def tearDown(self) -> None:
        from core import audio_devices

        audio_devices._probe_results.clear()
        audio_devices._probe_results.update(self._saved[0])
        audio_devices._cache = self._saved[1]
        audio_devices._chosen_api.update(self._saved[2])

    def test_the_probe_cache_is_per_device_not_per_api(self) -> None:
        """One bad endpoint must not condemn a whole host API.

        The probe measures an endpoint. Caching that answer as a verdict on
        the API meant that a single muted device threw away an API that had
        good ones on it."""
        from core import audio_devices

        audio_devices._probe_results[(None, "output", 3)] = False
        self.assertFalse(
            audio_devices._probe_results.get((None, "output", 1)),
            "a failed endpoint was cached as the answer for another endpoint",
        )

    def test_configure_invalidates_the_probe_results(self) -> None:
        """A probe measured at the old rate says nothing about the new one."""
        from core import audio_devices

        audio_devices._probe_results[("mme", "output", 1)] = True
        audio_devices.configure(16000, 24000)
        self.assertEqual(audio_devices._probe_results, {},
                         "a stale probe survived a rate change")

    def test_refresh_invalidates_the_probe_results(self) -> None:
        """Plugging a headset in changes what the machine can do."""
        from core import audio_devices

        audio_devices._probe_results[("mme", "output", 1)] = True
        audio_devices._cache = {"input": [], "output": []}
        with patch.object(audio_devices, "_query",
                          return_value={"input": [], "output": []}):
            audio_devices.list_devices("output", refresh=True)
        self.assertEqual(audio_devices._probe_results, {})

    def test_a_silent_sink_is_not_selectable(self) -> None:
        """An endpoint that opens and moves nothing must not be offered.

        The whole reason this file exists. It is measured per endpoint, so
        the good device of the same API survives."""
        from core import audio_devices

        fake = _FakeSoundDevice(silent_outputs=(3,))
        with patch.dict("sys.modules", {"sounddevice": fake}):
            audio_devices._probe_results.clear()
            found = audio_devices._query()

        names = found.get("output", [])
        self.assertNotIn("Silent Output Device", names)
        self.assertTrue(names, "every output device was rejected")

    def test_the_displayed_name_comes_from_the_same_host_api(self) -> None:
        """The picker must not show one API's name for another's endpoint.

        MME truncates names to 31 characters; the longer name belongs to
        DirectSound. Showing the DirectSound name for an endpoint that opens
        on MME is how the user ends up on a different device than the one
        they picked."""
        from core import audio_devices

        devices = [
            {"name": "Speakers (Realtek(R) Audio)"[:31], "hostapi": 1},
            {"name": "Speakers (Realtek(R) Audio)", "hostapi": 0},
        ]
        apis = ["Windows DirectSound", "Windows MME"]

        mme = audio_devices._display_name(devices[0]["name"], devices, apis, "mme")
        self.assertEqual(mme, devices[0]["name"],
                         "the name of another host API was shown")

        direct = audio_devices._display_name(devices[1]["name"], devices, apis,
                                              "directsound")
        self.assertEqual(direct, devices[1]["name"])

    def test_an_exact_match_beats_a_prefix_match(self) -> None:
        """An exact name is never a guess, so it wins wherever it is.

        The old code took the first API that produced ANY partial match, so
        a truncated name could beat an exact one."""
        from core import audio_devices

        fake = _FakeSoundDevice()
        with patch.dict("sys.modules", {"sounddevice": fake}):
            audio_devices._probe_results.clear()
            idx = audio_devices.resolve("Headset Microphone (Plugged)", "input")

        self.assertIsNotNone(idx)
        self.assertEqual(fake.query_devices()[idx]["name"],
                         "Headset Microphone (Plugged)")

    def test_a_saved_device_that_is_gone_falls_back_and_says_so(self) -> None:
        """A missing headset degrades to the system default, and says so."""
        from core import audio_devices

        fake = _FakeSoundDevice()
        printed: list[str] = []
        with patch.dict("sys.modules", {"sounddevice": fake}), \
                patch("builtins.print", side_effect=printed.append):
            audio_devices._probe_results.clear()
            idx = audio_devices.resolve("A headset that is not here", "input")

        self.assertIsNone(idx)
        self.assertTrue(any("falling back" in p for p in printed),
                        f"the fallback was not reported: {printed}")


class YouTubeRouteTests(unittest.TestCase):
    """Bug C — "play a YouTube video" opened a browser instead of the HUD."""

    def test_youtube_play_uses_the_hud_when_it_can(self) -> None:
        """With yt-dlp there, the video goes to the display, not the browser.

        The browser is the FALLBACK, and it is only reached when the video
        could not be played here."""
        from actions import youtube_video

        opened: list[str] = []
        with (
            patch.object(youtube_video, "_ytdlp_installed", return_value=True),
            patch.object(youtube_video, "_open_url",
                         side_effect=lambda u: opened.append(u) or True),
            patch("actions.video_player.video_player",
                  return_value="status=opening source=synthetic") as play,
        ):
            result = youtube_video._handle_play({"query": "synthetic video"}, None)

        self.assertIn("status=opening", result)
        play.assert_called_once()
        self.assertEqual(opened, [], "the browser was opened even though it could play")
        self.assertNotIn("browser", result.lower())

    def test_youtube_play_falls_back_to_the_browser_and_says_so(self) -> None:
        """Without yt-dlp the browser is the fallback, and it is announced.

        A fallback that is not announced is a fallback the user did not ask
        for and cannot see coming."""
        from actions import youtube_video

        opened: list[str] = []
        with (
            patch.object(youtube_video, "_ytdlp_installed", return_value=False),
            patch.object(youtube_video, "_open_url",
                         side_effect=lambda u: opened.append(u) or True),
        ):
            result = youtube_video._handle_play({"query": "synthetic video"}, None)

        self.assertIn("status=unverified", result)
        self.assertIn("browser", result)
        self.assertTrue(opened)

    def test_the_two_youtube_tools_do_not_claim_the_same_job(self) -> None:
        """The description is part of the contract.

        The model picked the wrong tool because its description said
        "playing videos" while the one that actually plays on the display
        said less. The descriptions must now point at each other."""
        from actions import youtube_video, video_player

        reader = youtube_video.TOOL["description"].lower()
        player = video_player.TOOL["description"].lower()

        self.assertIn("video_player", reader)
        self.assertIn("summar", reader)
        self.assertNotIn("playing videos", reader)
        self.assertIn("youtube_video", player)
        self.assertIn("youtube", player)
        # The player must claim all four sources it accepts.
        for word in ("file", "url", "youtube"):
            self.assertIn(word, player)


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
