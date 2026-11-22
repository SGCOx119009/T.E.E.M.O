"""
tests/test_live_errors.py — the death of a live session, read honestly.

WHAT IS BEING PROVED
    When the connection to Gemini dies, the program has to know WHY, because
    the answer decides what the user is told and what happens next:

        a dropped connection  → back off, try again, say so
        an exhausted quota    → change model, try again
        a refused key         → ask for the key again

    The failure arrives wrapped: a TaskGroup puts the failure of its tasks
    into an ExceptionGroup, and the group says only "unhandled errors in a
    TaskGroup (1 sub-exception)". The real error is inside. If the program
    reads only the group, it cannot tell a dead connection from a refused
    key — and then it says the wrong thing and waits the wrong amount of
    time.

THE USER'S OWN WORDS
    The log showed "TimeoutError: timed out while closing connection" and
    "websockets ConnectionClosedError: keepalive ping timeout", and the
    assistant sat there with a "TaskGroup" message and no idea.
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))


def _main_module():
    import importlib

    return importlib.import_module("main")


def test_a_plain_error_says_what_it_is():
    main = _main_module()
    text = main._error_text(ValueError("the key is not valid"))
    assert "ValueError" in text
    assert "the key is not valid" in text


def test_the_error_inside_a_group_is_read():
    """The group is opened up, and the leaves are read."""
    main = _main_module()
    group = ExceptionGroup(
        "unhandled errors in a TaskGroup (1 sub-exception)",
        [TimeoutError("timed out while closing connection")],
    )
    text = main._error_text(group)
    assert "TimeoutError" in text, text
    assert "timed out while closing connection" in text, text


def test_the_users_own_failure_is_recognised_as_a_dead_connection():
    """The exact pair of errors in the log.

    A group with a timeout and a closed connection in it: the assistant
    must know that the connection died, and not that the server said no.
    """
    main = _main_module()
    group = ExceptionGroup(
        "unhandled errors in a TaskGroup (2 sub-exceptions)",
        [
            TimeoutError("timed out while closing connection"),
            ConnectionResetError("keepalive ping timeout"),
        ],
    )
    text = main._error_text(group)
    assert main._is_network_error(text), text
    assert not main._is_network_error("RESOURCE_EXHAUSTED: quota exceeded")


def test_a_group_of_groups_is_read_to_the_bottom():
    """Nested groups happen; the deepest error is still the one that counts."""
    main = _main_module()
    inner = ExceptionGroup(
        "unhandled errors in a TaskGroup (1 sub-exception)",
        [TimeoutError("timed out")],
    )
    outer = ExceptionGroup("unhandled errors in a TaskGroup (1 sub-exception)", [inner])
    text = main._error_text(outer)
    assert "TimeoutError" in text, text
    assert "timed out" in text, text


def test_a_quota_failure_is_not_a_dead_connection():
    """The two must not be confused: the quota needs a different model."""
    main = _main_module()
    group = ExceptionGroup(
        "unhandled errors in a TaskGroup (1 sub-exception)",
        [RuntimeError("RESOURCE_EXHAUSTED: quota exceeded for model gemini-3.5-flash")],
    )
    text = main._error_text(group)
    assert not main._is_network_error(text), text
    assert "quota" in text.lower()


def test_an_error_without_a_message_still_says_something():
    """The name alone is better than nothing."""
    main = _main_module()
    text = main._error_text(ConnectionResetError())
    assert "ConnectionResetError" in text, text


def test_the_reconnect_signal_is_still_found_inside_a_group():
    """The voluntary reconnect must keep working."""
    main = _main_module()
    signal = main._ReconnectSignal(keep_context=False)
    group = ExceptionGroup("unhandled errors in a TaskGroup (1 sub-exception)", [signal])
    assert main._is_reconnect_signal(group)
    assert main._keep_context_of(group) is False
