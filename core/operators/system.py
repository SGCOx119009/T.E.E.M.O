"""
core/operators/system.py — the operator for the machine and for what leaves it.

TWO KINDS OF THING, ONE DOMAIN
    The volume, the brightness, the WiFi, the power and the battery are the
    machine's SETTINGS. The messages, the mail and the reminders are the
    machine's EFFECTS on the world outside it.

    They are one operator because they share the two things that make them
    different from the rest: they change the state of the machine or of
    somebody else's world rather than reading anything, and several of them
    need a confirmation the user has to press.

WHY SENDING IS HERE AND NOT IN THE DESKTOP
    "Send this to Ana" is not a mouse action. It reaches another person, it
    cannot be undone, and it is the class of action the confirmation gate
    exists for. Putting it beside the desktop tools would have hidden that.

WHAT IT OWNS
    The system settings, the things that wait (reminders, routines), the
    games updater, and the two channels that reach another human.
"""
from __future__ import annotations

from core.operators.base import Operator


class SystemOperator(Operator):
    name = "system"
    description = (
        "The machine's settings and its effects outside it: sound, display, "
        "network, and its power and energy (the battery, the charging state "
        "and the power plan); reminders and routines; and the channels that "
        "reach another person, read or written — the mail and the calendar."
    )
    context = "system"
    risk_class = "high"
    tools = (
        "computer_settings",
        "reminder",
        "routines",
        "manage_monitor",
        "game_updater",
        "send_message",
        "gmail_control",
        "calendar_control",
    )
