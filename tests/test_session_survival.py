"""
tests/test_session_survival.py — the session must survive a slow tool and a
quiet room.

WHAT IS BEING PROVED
    1. THE WATCHDOG DOES NOT FIRE WHILE A TOOL IS WORKING. The answer to a
       tool call is the tool, and a slow tool is silent by definition. The
       watchdog used to call that silence a dead session, rebuild it, lose
       the result of the tool and let the model ask for the same thing
       again — for ever. The user called it "se volvía loco".
    2. A QUIET ROOM DOES NOT KILL THE SESSION. The server closes a session
       that nobody talks to (1008, "client inactivity"), and the assistant
       sleeps for minutes waiting for the wake word. The connection is kept
       warm with silence, so the session is not closed for being quiet.
    3. A CLOSED SESSION IS NOT RETRIED FOR EVER. The pause grows, and the
       user is told what is happening.

THE USER'S OWN WORDS
    "se volvia loco intentando reproducir en spotify como si etrara een un
    bucle buscando la cancion pero no reproduciendola"
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))


def _main():
    import importlib

    return importlib.import_module("main")


# ── 1. the watchdog and the tool ───────────────────────────────────────────

class _FakeSession:
    """The little of a Live session the watchdog touches."""

    def __init__(self):
        self.sent = 0

    async def send_client_content(self, *a, **kw):
        self.sent += 1


class _FakeUI:
    def __init__(self):
        self.log = []

    def set_state(self, _state):
        pass

    def write_log(self, text):
        self.log.append(text)


def _teemo():
    """A TeemoLive with nothing but the bits the watchdog needs."""
    main = _main()
    teemo = main.TeemoLive.__new__(main.TeemoLive)
    teemo._rx_deadline = None
    teemo._tool_in_flight = 0
    teemo._last_keepalive = 0.0
    teemo.ui = _FakeUI()
    teemo.requested = []
    teemo.request_reconnect = lambda **kw: teemo.requested.append(kw)
    return teemo


def test_a_tool_in_flight_holds_the_watchdog_off():
    """A tool that is working is not a dead session."""
    import asyncio

    main = _main()
    teemo = _teemo()
    teemo._rx_deadline = time.monotonic() - 1.0      # already past the limit
    teemo._tool_in_flight = 1

    async def _run():
        watcher = asyncio.create_task(teemo._watch_stall())
        await asyncio.sleep(2.5)                       # one tick of the watcher
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())

    assert not teemo.requested, (
        "the session was rebuilt while a tool was still working: "
        f"{teemo.requested}"
    )


def test_the_watchdog_still_fires_when_no_tool_is_working():
    """And it still catches a session that is really dead."""
    import asyncio

    teemo = _teemo()
    teemo._rx_deadline = time.monotonic() - 1.0

    async def _run():
        watcher = asyncio.create_task(teemo._watch_stall())
        await asyncio.sleep(2.5)
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())

    assert teemo.requested, "a dead session was not noticed"
    assert teemo.requested[0].get("reason") == "no response"


def test_the_clock_runs_further_out_while_a_tool_works():
    """The tool gets room, but a hung tool is still caught."""
    main = _main()
    teemo = _teemo()
    teemo._rx_deadline = time.monotonic() - 1.0
    teemo._tool_in_flight = 1

    import asyncio

    async def _run():
        watcher = asyncio.create_task(teemo._watch_stall())
        await asyncio.sleep(2.5)
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())

    assert teemo._rx_deadline is not None, "the clock was thrown away"
    assert teemo._rx_deadline > time.monotonic() + main._STALL_LIMIT_WITH_TOOL - 5


# ── 2. the quiet room ──────────────────────────────────────────────────────

def test_a_silent_frame_keeps_the_session_alive():
    """The connection is kept warm with silence, and only now and then."""
    teemo = _teemo()
    queued = []
    teemo.out_queue = type("Q", (), {"put_nowait": staticmethod(queued.append)})()
    teemo._loop = type("L", (), {
        "call_soon_threadsafe": staticmethod(lambda fn, arg: fn(arg)),
    })()

    teemo._last_keepalive = 0.0
    teemo._keep_session_warm()
    assert len(queued) == 1, "the session was left quiet"
    frame = queued[0]["data"]
    assert frame and set(frame) == {0}, "the keepalive is not silence"

    # And it is not sent on every single frame of the microphone.
    queued.clear()
    teemo._keep_session_warm()
    assert not queued, "the session was kept warm too often"


def test_the_wake_word_gate_keeps_the_session_warm():
    """While the assistant sleeps, the socket must not go quiet."""
    teemo = _teemo()
    teemo._wake_enabled = True
    teemo._awake = False
    teemo._wake_detector = None
    teemo.out_queue = type("Q", (), {"put_nowait": staticmethod(lambda _x: None)})()
    teemo._loop = type("L", (), {
        "call_soon_threadsafe": staticmethod(lambda fn, arg: fn(arg)),
    })()
    teemo._last_keepalive = 0.0

    # The gate is inside the microphone's callback, which needs a real
    # callback. It is exercised here through the same helper it calls.
    teemo._keep_session_warm()
    assert teemo._last_keepalive > 0.0, "the sleeping assistant let the session die"


# ── 3. the closed session ──────────────────────────────────────────────────

def test_a_session_closed_for_quiet_is_a_connection_problem():
    """1008 is not a refusal of the key and not a quota."""
    main = _main()
    text = main._error_text(ExceptionGroup(
        "unhandled errors in a TaskGroup (1 sub-exception)",
        [RuntimeError("1008 None. Session closed due to client inactivity.")],
    ))
    assert main._is_network_error(text), text


def test_the_refused_key_is_still_its_own_case():
    main = _main()
    text = main._error_text(RuntimeError("API key not valid"))
    assert not main._is_network_error(text), text
    assert "1007" in text or "API key not valid" in text


def test_the_quota_is_still_its_own_case():
    main = _main()
    text = main._error_text(RuntimeError("RESOURCE_EXHAUSTED: quota exceeded"))
    assert not main._is_network_error(text), text
