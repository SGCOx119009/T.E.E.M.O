"""
core/capability.py — what this particular Windows machine can actually let the
assistant do, and what it cannot.

WHY THIS EXISTS
    An assistant that runs on somebody's desktop can end up somewhere it cannot
    see: launched as a service, installed for another user, or started while a
    privilege prompt is holding the secure desktop. In that state every
    keyboard and screen call either does nothing or fails in a way that looks
    like success, and the user is told the assistant clicked something it
    never touched.

    The old version of this module answered a different question — why a
    non-Windows session refused to cooperate. On Windows that question has no
    answer to give, because Windows permits all of it by default. What is
    missing is the question that DOES have an answer here: is there an
    interactive desktop for THIS process to act on?

WHAT THIS MODULE DOES ABOUT IT
    It does not fix anything — none of it is fixable from inside the app. It
    makes it VISIBLE: the limits are reported to the user in the activity log
    and, more importantly, to the MODEL through `_describe_limits`, so the
    assistant says "this session has no interactive desktop, so I cannot drive
    the screen" instead of cheerfully reporting a click that never happened.

    That is the same rule the rest of this release follows: never claim an
    outcome that was not observed. A machine that forbids the action is just
    another thing that was not observed.

THE THREE CHECKS, AND WHY EACH ONE IS A SEPARATE QUESTION
    SESSION
        A process in session 0 is a service. It has a window station of its
        own and no interactive desktop, so it can neither drive the input nor
        capture the screen. This is the check that catches "the assistant was
        installed as a service and launched at boot".

    INPUT DESKTOP
        The session can exist and still have no input desktop: a machine with
        no one logged on, or one sitting at the lock screen. Windows refuses
        to open the input desktop in both cases.

    SECURE DESKTOP
        While a privilege prompt is up, the screen belongs to "Winlogon" and
        input from another session is not delivered. The desktop still opens;
        the keystrokes simply go nowhere, which is the worst kind of failure
        because nothing reports it.

WHAT IS ASSUMED, AND WHY THAT IS HONEST
    The capabilities are DECLARED as facts of Windows, not measured by
    attempting the action and watching it silently do nothing. `pyautogui`
    and the screen-capture backends are checked by the modules that use them,
    where the answer to "is it installed" belongs. What this module adds is
    the part nobody else can answer: whether there is anything to act ON.
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
from typing import Optional

# UOI_NAME — the name of a window station or desktop.
_UOI_NAME = 2
# DESKTOP_READOBJECTS — the least privilege that is enough to ask for a name.
_DESKTOP_READOBJECTS = 0x0001
# DESKTOP_SWITCHDESKTOP — needed to find out which desktop is the input one.
_DESKTOP_SWITCHDESKTOP = 0x0100


@dataclass
class Support:
    """What is possible here. Every field is a fact about THIS machine."""

    os_name: str = "Windows"
    can_control_input: bool = True      # type, click, hotkeys
    can_capture_screen: bool = True     # screenshots, vision
    can_focus_windows: bool = True      # raise another app's window
    notes: list[str] = field(default_factory=list)

    @property
    def fully_capable(self) -> bool:
        return (self.can_control_input and self.can_capture_screen
                and self.can_focus_windows)


@dataclass
class Capabilities:
    """The model the rest of the release speaks in.

    `available` is whether the assistant may ACT at all. `read_only` is the
    state in between: the screen can be seen but not driven. `capabilities`
    names what is permitted, and `reason` says why not — never empty when
    something is not permitted, because "it did not work" is not an
    explanation the user can act on.
    """

    available: bool = True
    read_only: bool = False
    capabilities: list[str] = field(default_factory=list)
    reason: str = ""


def _process_session_id() -> Optional[int]:
    """The Windows session this process belongs to. None when it cannot be read.

    Session 0 is the service session: it exists on every machine, it is where
    services live, and it has no interactive desktop.
    """
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        pid = ctypes.windll.kernel32.GetCurrentProcessId()
        session_id = ctypes.c_ulong(0)
        if kernel32.ProcessIdToSessionId(pid, ctypes.byref(session_id)):
            return int(session_id.value)
    except Exception:
        return None
    return None


def _input_desktop_name() -> str:
    """The name of the current input desktop, '' when it cannot be read.

    "Winlogon" is the secure desktop: the one a privilege prompt lives on.
    """
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        handle = user32.OpenInputDesktop(0, False, _DESKTOP_READOBJECTS)
        if not handle:
            return ""
        try:
            needed = ctypes.c_ulong(0)
            user32.GetUserObjectInformationW(handle, _UOI_NAME, None, 0,
                                            ctypes.byref(needed))
            if needed.value == 0:
                return ""
            buffer = ctypes.create_unicode_buffer(needed.value // 2 + 1)
            if not user32.GetUserObjectInformationW(handle, _UOI_NAME, buffer,
                                                    needed, ctypes.byref(needed)):
                return ""
            return buffer.value or ""
        finally:
            user32.CloseDesktop(handle)
    except Exception:
        return ""


def _has_input_desktop() -> bool:
    """Is there an interactive desktop this process could act on?"""
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        handle = user32.OpenInputDesktop(0, False, _DESKTOP_READOBJECTS)
        if not handle:
            return False
        user32.CloseDesktop(handle)
        return True
    except Exception:
        # Cannot tell. Do not block: a failure to ask is not a refusal.
        return True


def _desktop_state() -> tuple[bool, str]:
    """(hay escritorio interactivo, causa cuando no lo hay). Never raises."""
    session_id = _process_session_id()
    if session_id == 0:
        return False, (
            "this process runs in session 0, the service session, which has no "
            "interactive desktop: the keyboard, the mouse and the screen "
            "cannot be reached from it")

    if not _has_input_desktop():
        return False, (
            "this session has no interactive desktop right now — nobody is "
            "logged on, or the machine is at the lock screen — so the "
            "keyboard, the mouse and the screen cannot be reached")

    name = _input_desktop_name().strip().lower()
    if name == "winlogon":
        return False, (
            "the secure desktop is in front — a privilege prompt is waiting on "
            "screen — so keystrokes sent now would go nowhere until it is "
            "answered")

    return True, ""


def detect() -> Support:
    """Work out what this machine permits. Never raises, never blocks.

    Deliberately cheap — one session id and one desktop handle — because it
    runs at startup and because the expensive way to find out is to try the
    action and watch it silently do nothing, which is the problem.
    """
    support = Support(os_name="Windows")
    ok, reason = _desktop_state()
    if ok:
        return support

    # No interactive desktop at all: nothing can be seen and nothing can be
    # driven. A partial desktop (secure desktop up) still lets the screen be
    # captured, so the two cases are told apart.
    support.can_control_input = False
    support.can_focus_windows = False
    if "secure desktop" in reason:
        support.can_capture_screen = True
    else:
        support.can_capture_screen = False
    support.notes.append(reason)
    return support


def capabilities() -> Capabilities:
    """What the assistant may do here, and why not when it may not.

    This is the shape the rest of the release speaks in: `available` for
    acting at all, `read_only` for the state in between, `capabilities` naming
    what is permitted, and `reason` carrying the cause whenever something is
    not.
    """
    support = detect()
    if support.fully_capable:
        return Capabilities(
            available=True,
            read_only=False,
            capabilities=["control_input", "capture_screen", "focus_windows"],
            reason="",
        )

    granted = []
    if support.can_capture_screen:
        granted.append("capture_screen")
    if support.can_control_input:
        granted.append("control_input")
    if support.can_focus_windows:
        granted.append("focus_windows")

    reason = "; ".join(support.notes) or "this session does not permit it"
    return Capabilities(
        available=support.can_control_input,
        read_only=not support.can_control_input and support.can_capture_screen,
        capabilities=granted,
        reason=reason,
    )


def describe_for_model(support: Support) -> list[str]:
    """The limits, phrased for `_describe_limits` in main.py.

    Written as facts about the machine rather than as refusals, because a model
    told "you cannot do X here, and this is why" explains it to the user; a
    model told "do not do X" invents a reason.
    """
    lines: list[str] = []
    if not support.can_control_input:
        lines.append(
            "- You CANNOT type, click, press keys or move the mouse in this "
            "session: this Windows session has no interactive desktop for you "
            "to act on. Do not attempt it and do not report having done "
            "it — say plainly that this machine does not allow it.")
    if not support.can_capture_screen:
        lines.append(
            "- You CANNOT see the screen in this session, for the same reason. "
            "Say so rather than describing anything.")
    if not support.can_focus_windows and support.can_control_input:
        lines.append(
            "- You cannot bring an already-running application to the front "
            "here; opening it starts another window instead.")
    return lines


def describe_for_user(support: Support) -> list[str]:
    """One line per note, for the activity log at startup."""
    return [f"SYS: {note}" for note in support.notes]
