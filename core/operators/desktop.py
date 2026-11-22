"""
core/operators/desktop.py — the operator for what is on the screen.

THE DESKTOP IS THE DEFAULT PLACE
    Most of what the assistant does that touches the world happens here: the
    mouse, the keyboard, a window, an application. This operator is the one
    the app falls back to when nothing else is more specific.

WHAT IT OWNS
    The tools that act on the machine's SCREEN and on the applications on it:
    pointing, typing, windows, the desktop itself, and the handful of
    controls that are about the assistant rather than about a document —
    starting a background task, checking the machine's status, stopping the
    assistant, undoing what it did.

WHAT IT DOES NOT OWN
    Files are the files operator. The browser is the browser operator. A
    volume slider is a system setting, not a desktop one — `computer_settings`
    belongs to the system operator, and the split matters because the two are
    filtered by context separately.
"""
from __future__ import annotations

from core.operators.base import Operator


class DesktopOperator(Operator):
    name = "desktop"
    description = (
        "What is on the screen: the mouse, the keyboard, windows, "
        "applications, and the desktop itself."
    )
    context = "desktop"
    risk_class = "high"
    tools = (
        "computer_control",
        "desktop_control",
        "open_app",
        "screen_mark",
        "task_planner",
        "background_tasks",
        "system_status",
        # The assistant's own body: whether it is asleep, whether the
        # microphone is muted, the language it speaks and the region the user
        # lives in. It sits beside system_status because it is the same kind
        # of question — "what is this machine doing right now" — asked of the
        # part of the machine that answers.
        "assistant_state",
        "shutdown_teemo",
        "undo",
    )
