"""core/failures.py — a failure in the interface does not end the program.

WHAT PYTHON DOES BY ITSELF
    An exception raised inside a slot — and a slot is where almost
    everything of the interface happens — is not a message. Qt calls
    `qFatal()`, and `qFatal()` ends the process: no window, no icon in
    the tray, no explanation. The user is left looking at a desktop that
    no longer has an assistant in it.

    It was found the hard way: a mirrored camera called a transformation
    that does not exist in PyQt6, the exception arrived in a slot, and
    the whole program died in the middle of the test suite with
    «Windows fatal exception: access violation» and no clue.

    In a worker thread the same thing happens quietly: the thread dies
    and nobody is told.

WHAT THIS DOES
    The trace goes to the terminal, where the rest of the diagnostics
    already are, and the program carries on. A bug in a panel is a bug in
    a panel; it is not the end of the product.

    It is installed when the window is built, so it is in place for the
    tests as well as for the program.
"""
from __future__ import annotations

import sys
import threading
import traceback

_installed = False


def install_excepthooks() -> None:
    """Put the handlers in place. Calling it twice changes nothing."""
    global _installed
    if _installed:
        return
    _installed = True

    def _hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        try:
            text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        except Exception:
            text = f"{exc_type.__name__}: {exc_value}"
        print("[interface] a failure that did not stop the program:\n" + text,
              flush=True)

    def _thread_hook(args):
        try:
            text = "".join(traceback.format_exception(
                args.exc_type, args.exc_value, args.exc_traceback))
        except Exception:
            text = f"{args.exc_type.__name__}: {args.exc_value}"
        print(f"[thread {args.thread.name}] a failure in a worker:\n{text}",
              flush=True)

    sys.excepthook = _hook
    threading.excepthook = _thread_hook
