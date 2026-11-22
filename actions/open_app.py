"""
Assistant action — open an application, and know whether it actually opened.

WHAT WAS WRONG WITH THIS FILE
    It answered "Opened Spotify." whenever its launcher returned True, and on
    Windows the launcher returned True immediately after

        subprocess.Popen(app_name, shell=True)

    which succeeds because a SHELL started — not because an application did. A
    misspelling, an app that was never installed, an installer that moved:
    every one of them produced "Opened Spotify.", said out loud, while nothing
    appeared on screen.

    The same line was also a command injection. `shutil.which(name.split(".")[0])`
    passed "notepad.exe & whoami" because `which("notepad")` succeeds, and the
    whole string then went to a shell unquoted. Verified before it was changed.

WHAT IT DOES NOW
    * No shell, anywhere. Executables are resolved to a path and started as an
      argument vector; URI schemes go through the OS handler.
    * It watches the process table for a NEW process answering to the name, and
      only says `done` when one appears. When none does, it says `unverified`
      and the assistant tells the user it could not confirm — which is true,
      and is something they can act on.
    * It answers in milliseconds and verifies behind that, so asking to open
      something no longer costs the conversation one to four seconds.

WHY IT IS NOT A LIST OF APPLICATIONS
    Nothing here knows in advance what exists. Verification asks the running
    machine what appeared; matching compares against whatever is running. The
    alias map in core/appidentity.py covers the handful of names that differ
    BETWEEN OPERATING SYSTEMS and restricts nothing — every other name falls
    through untouched, which is what makes software nobody here has heard of
    work exactly as well as the software we have.
"""
import os
import shutil
import subprocess
import threading
import time

from core import appidentity, fileopen, outcome
from core.lazyimport import LazyModule, installed

_PYAUTOGUI = installed("pyautogui")
pyautogui = LazyModule("pyautogui")

_SYSTEM = "Windows"

# How long to keep watching for the application to appear. Chosen from the two
# ends of the range rather than the middle: a text editor is up in a fraction
# of a second, a game launcher can take ten, and a limit that suits the first
# reports the second as broken.
_LAUNCH_TIMEOUT = 12.0

# Nothing here is a keyword list. The Start-Menu / Spotlight fallback types the
# name and presses Enter, which is what a person does, and works in every
# language the desktop is running in.
_SEARCH_SETTLE = 0.8


# ─────────────────────────────────────────────────────────────────────────────
#  Per-OS launching — no shell on any path
# ─────────────────────────────────────────────────────────────────────────────
def _search_edit() -> object | None:
    """The one search box the Start menu is showing, or None.

    THE RULE THIS ENFORCES, AND WHY IT IS NOT OPTIONAL
        The old implementation typed the application's name into whatever
        had the keyboard focus and pressed Enter. That is how "open
        notepad" could end up typing into the user's chat window and
        pressing Enter in it: the name went wherever the focus happened to
        be, and a wrong keypress in somebody's document is not a mistake the
        product is allowed to make.

        So the search box is identified, not assumed:
          * it must be an Edit control whose automation id is
            "SearchTextBox" - the Start menu's own box;
          * it must live in the window that HAS the keyboard focus;
          * there must be exactly ONE such control. Two means the picture
            is not what we think it is, and typing into one of them is a
            coin toss.

        None of these failing is an error: it just means this route is
        closed, and the caller falls through to the next one.
    """
    try:
        import pywinauto
    except Exception as exc:
        print(f"[open_app] UI Automation unavailable: {exc}")
        return None

    try:
        desktop = pywinauto.Desktop(backend="uia")
        windows = list(desktop.windows())
    except Exception as exc:
        print(f"[open_app] could not read the desktop: {exc}")
        return None

    found: list = []
    for window in windows:
        try:
            focused = bool(
                window.element_info.element.CurrentHasKeyboardFocus
            )
        except Exception:
            focused = False
        if not focused:
            continue
        try:
            controls = list(window.descendants())
        except Exception:
            continue
        for control in controls:
            try:
                info = control.element_info
                if getattr(info, "control_type", "") != "Edit":
                    continue
                if getattr(info, "automation_id", "") != "SearchTextBox":
                    continue
                found.append(control)
            except Exception:
                continue

    if len(found) != 1:
        if len(found) > 1:
            print(f"[open_app] {len(found)} search boxes on screen — "
                  f"not typing into an ambiguous one.")
        return None
    return found[0]


def _desktop_search(app_name: str) -> bool:
    """Type the name into the Start menu's search box and press Enter.

    The last resort, and the only one that reaches Store apps, shortcuts and
    anything else with no executable on PATH. It cannot be verified from its
    own return value — which is fine, because nothing here is trusted on its
    own any more; the process table decides.

    WHAT IS VERIFIED
        The search box is located through UI Automation, the name is written
        into THAT box, and the text is read back before Enter is pressed. If
        the box cannot be identified, or if it holds something other than
        what was written, nothing is submitted — a query that was never
        typed must not be run.
    """
    if not _PYAUTOGUI:
        return False
    try:
        pyautogui.hotkey("win", "s")
        time.sleep(_SEARCH_SETTLE)

        field = _search_edit()
        if field is None:
            return False

        field.set_edit_text(app_name)
        time.sleep(0.2)

        written = field.get_value()
        if written != app_name:
            print(f"[open_app] the search box holds {written!r}, not "
                  f"{app_name!r} — nothing was submitted.")
            return False

        pyautogui.press("enter")
        return True
    except Exception as exc:
        print(f"[open_app] desktop search failed: {exc}")
        return False


def _launch_windows(app_name: str) -> bool:
    """-> was a launch REQUESTED successfully. Never 'did it open'.

    Three routes, none of them a shell:

      1. an executable on PATH, started as a one-element argument vector. The
         old code passed the raw string with shell=True, which is what let
         "notepad.exe & whoami" run two things.
      2. a URI scheme such as `ms-settings:` — handed to the OS handler with
         os.startfile, which is what the shell would have called anyway,
         without a command line for anything to be appended to.
      3. the Start Menu, typed like a person would.
    """
    resolved = shutil.which(app_name)
    if resolved:
        try:
            subprocess.Popen(
                [resolved],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception as exc:
            print(f"[open_app] Popen failed for {resolved!r}: {exc}")

    # A scheme, not a path: "ms-settings:", "spotify:", "steam://run/...".
    # Checked before ":" appears anywhere else — a Windows path has one too,
    # at index 1, and that is a file, not a scheme.
    scheme, _, rest = app_name.partition(":")
    if rest and scheme and scheme.isalnum() and len(scheme) > 1:
        try:
            os.startfile(app_name)               # noqa: S606 — no shell involved
            return True
        except Exception as exc:
            print(f"[open_app] startfile failed for {app_name!r}: {exc}")

    return _desktop_search(app_name)


_LAUNCHERS = {
    "Windows": _launch_windows,
}


# ─────────────────────────────────────────────────────────────────────────────
#  HUD helpers — no-ops without a player, exactly as the whatsapp plugins do
# ─────────────────────────────────────────────────────────────────────────────
def _log(player, message: str) -> None:
    if player:
        try:
            player.write_log(message)
        except Exception:
            pass


def _say(player, instruction: str) -> None:
    if player and hasattr(player, "request_say"):
        try:
            player.request_say(instruction)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
#  Launch + verify, off the conversation's thread
# ─────────────────────────────────────────────────────────────────────────────
def _launch_and_verify(player, spoken: str, launcher_name: str,
                       before: set[int]) -> None:
    """The part that takes seconds. Speaks ONLY when something needs saying.

    Silence means it worked: the window is on the user's screen and a second
    announcement over the top of it is noise — the same reasoning
    whatsapp_call.py applies to a phone that is already ringing.
    """
    try:
        launcher = _LAUNCHERS[_SYSTEM]
        requested = launcher(launcher_name)
        if not requested and launcher_name.lower() != spoken.lower():
            requested = launcher(spoken)

        appeared = appidentity.wait_for_launch(
            spoken, before, timeout=_LAUNCH_TIMEOUT
        )
        if appeared is not None:
            _log(player, f"T.E.E.M.O.: {appeared.label} is open.")
            # The model has to be told, not just the screen.
            #
            # WHY: the tool answered "started" and the turn ended on that.
            # The window appeared two seconds later and the fact went to the
            # activity log, so the person could see "it is open" while the
            # model — which never saw the confirmation — told them it could
            # not confirm anything had opened. Two halves of one fact, and
            # the model only had one of them.
            _say(player, f"[TOOL_CONFIRMED] {appeared.label} IS open. Say in "
                         f"one short sentence that it is open, in the user's "
                         f"language. Do not call any tool.")
            return

        if not requested:
            trouble = (f"{spoken} could not be started - nothing on this system "
                       f"answers to that name, so it may not be installed")
        else:
            trouble = (f"{spoken} was asked to start but no new application "
                       f"appeared within {int(_LAUNCH_TIMEOUT)} seconds, so it "
                       f"is NOT confirmed open - it may be slow to load, or not "
                       f"installed under that name")

        _log(player, f"T.E.E.M.O.: {trouble}")
        _say(player, f"[TOOL_FAILED] Tell the user, in one short sentence in "
                     f"their own language: {trouble}")
    except Exception as exc:
        print(f"[open_app] {type(exc).__name__}: {exc}")
        _say(player, "[TOOL_FAILED] Tell the user, in one short sentence in "
                     f"their own language: opening {spoken} failed - {exc}")


# ─────────────────────────────────────────────────────────────────────────────
#  Opening a DOCUMENT — the same tool, the other half of what "open" means
# ─────────────────────────────────────────────────────────────────────────────
def _verify_document(player, path: str, assoc, before: set[int]) -> None:
    """Prove the document is open, or say plainly that it is not.

    Two kinds of evidence, and the FIRST one is the usual one: a window whose
    title carries the document's name. Opening a file in an application that
    is already running starts no new process at all, so the process table —
    which is the only evidence for the application route — says nothing here.
    The title is what proves it.

    The process check is the fallback for the other case: the application had
    to start, and its window may not carry the document's name.
    """
    name = os.path.basename(path)
    try:
        title = fileopen.wait_for_document(path, timeout=_LAUNCH_TIMEOUT)
        if title:
            _log(player, f"T.E.E.M.O.: {name} is open — {title}")
            _say(player, f"[TOOL_CONFIRMED] {name} IS open — the window title "
                         f"is '{title}'. Say in one short sentence that it is "
                         f"open, in the user's language. Do not call any tool.")
            return

        if assoc and assoc.name:
            appeared = appidentity.wait_for_launch(
                assoc.name, before, timeout=2.0)
            if appeared is not None:
                _log(player, f"T.E.E.M.O.: {name} is open in {appeared.label}.")
                _say(player, f"[TOOL_CONFIRMED] {name} IS open in "
                             f"{appeared.label}. Say in one short sentence that "
                             f"it is open, in the user's language. Do not call "
                             f"any tool.")
                return

        trouble = (f"{name} was handed to Windows but no window showing it "
                   f"appeared within {int(_LAUNCH_TIMEOUT)} seconds, so it is "
                   f"NOT confirmed open")
        _log(player, f"T.E.E.M.O.: {trouble}")
        _say(player, f"[TOOL_FAILED] Tell the user, in one short sentence in "
                     f"their own language: {trouble}")
    except Exception as exc:
        print(f"[open_app] {type(exc).__name__}: {exc}")
        _say(player, "[TOOL_FAILED] Tell the user, in one short sentence in "
                     f"their own language: opening the file failed - {exc}")


def _open_document(spoken: str, player) -> str:
    """Open a file with the application Windows has associated with it.

    The route exists because "open the file" and "open the program" are two
    different requests that sound like one. The assistant could start Excel
    for hours and never put a single document in front of the user, which is
    what it answered when asked to open a spreadsheet.

    The association is read from the registry, so the file opens in whatever
    this machine actually uses for it — LibreOffice where that is what is
    installed, the user's own choice where they have made one.
    """
    path = fileopen.resolve(spoken)
    if not path:
        return outcome.failed(
            "open_app",
            f"no file called '{spoken}' was found on this computer. It was "
            f"looked for in Desktop, Downloads and Documents, and the whole "
            f"path as given was tried too. Ask the user where the file is, "
            f"or use its full path.",
            file=spoken,
        )

    assoc = fileopen.association(path)
    before = appidentity.snapshot()
    _log(player, f"T.E.E.M.O.: Opening {os.path.basename(path)}…")
    try:
        fileopen.open_with_default_app(path)
    except OSError as exc:
        ext = os.path.splitext(path)[1] or "that type of file"
        return outcome.failed(
            "open_app",
            f"{os.path.basename(path)} could not be opened: no application on "
            f"this machine is registered to open {ext} files ({exc}).",
            file=path,
        )

    threading.Thread(
        target=_verify_document,
        args=(player, path, assoc, before),
        daemon=True, name=f"open-file-{os.path.basename(path)[:20]}",
    ).start()

    return outcome.started(
        "open_app",
        file=os.path.basename(path),
        path=path,
        application=assoc.name or None,
        opened_with="the application Windows associates with this file type",
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────────────
def open_app(parameters=None, response=None, player=None,
             session_memory=None) -> str:
    spoken = ((parameters or {}).get("app_name") or "").strip()
    if not spoken:
        return outcome.failed("open_app", "no application name was given")

    if _SYSTEM not in _LAUNCHERS:
        return outcome.failed(
            "open_app", f"this operating system ({_SYSTEM}) is not supported",
            app=spoken,
        )

    # A DOCUMENT, not a program. "Open WA.xlsx" and "open Excel" are two
    # different requests, and answering the first with the second is how the
    # assistant ended up saying it could not open files at all.
    if fileopen.looks_like_file(spoken):
        return _open_document(spoken, player)

    # Already open? Then there is nothing to launch and nothing to wait for,
    # and saying so is more useful than a second copy. This is also the one
    # branch that can answer `done` immediately, because the evidence is
    # already in hand rather than somewhere in the next ten seconds.
    #
    # include_self, unlike the close path, and for the opposite reason. There
    # the assistant's own processes must never be a TARGET; here the question
    # is only "is this already running", and it is already running whether the
    # process belongs to us or not. Without this, asking to open the very
    # application the assistant lives inside reports `started` and goes off to
    # launch a second copy of it — seen exactly that way, against a packaged
    # build whose siblings the close guard had just learnt to protect.
    existing = appidentity.running(spoken, include_self=True)
    if existing:
        best = existing[0]
        # ALREADY RUNNING IS NOT ALREADY DONE.
        #
        # This branch used to report "it was already open" and stop. That is
        # true and useless: an application running behind three other windows
        # is not on the user's screen, nothing changed when they asked, and
        # from where they are sitting the assistant did not open anything.
        # Reported as a bug in exactly those words — "it does not even open
        # applications" — with WhatsApp and Edge both running in the tray.
        #
        # Clicking a taskbar icon for a running app raises its window. So does
        # this. When there is no window to raise — a background service, or an
        # app that has gone to the tray — focus() says so and the launcher runs
        # below, which is what brings a tray app back.
        # Try every candidate, not just the best-scoring one.
        #
        # A modern application is many processes and only one of them owns the
        # window. Ranking puts the exact name match first, and on Windows that
        # is routinely a background service: measured here, `WhatsApp` and
        # `msedge` both scored 100 on processes with no window at all, so
        # focusing "the best match" focused nothing and the branch gave up.
        #
        # Store apps are the harder half of the same thing — their window
        # belongs to ApplicationFrameHost, not to the app's own process — which
        # is why failing all of these falls through to the launcher rather than
        # reporting failure. Typing the name into the Start Menu raises a
        # running Store app, and that is the path that ends up serving them.
        for candidate in existing[:8]:
            if appidentity.focus(candidate):
                _log(player, f"T.E.E.M.O.: {candidate.label} brought to the front.")
                return outcome.done(
                    "open_app", app=spoken, matched=candidate.label,
                    already_running=True, focused=True, verified=True,
                )
        print(f"[open_app] {best.label} is running but no window could be "
              f"raised — launching it instead, which is what brings back an "
              f"app that is only in the tray.")

    launcher_name = appidentity.launcher_name(spoken)
    before = appidentity.snapshot()
    _log(player, f"T.E.E.M.O.: Opening {spoken}…")

    threading.Thread(
        target=_launch_and_verify,
        args=(player, spoken, launcher_name, before),
        daemon=True, name=f"open-app-{spoken[:20]}",
    ).start()

    # Facts, not a sentence. Handing the model a finished English sentence is
    # an invitation to repeat it verbatim — whatsapp_call.py has the receipt:
    # a request made in Turkish came back in English, padded with an offer
    # nobody had made. The model writes the sentence, in the user's language.
    return outcome.started("open_app", app=spoken)


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "open_app",
    "description": (
        "Opens something on this computer: an APPLICATION, or a FILE with the "
        "application Windows has associated with it. Use it whenever the user "
        "asks to open, launch, start, run or see the contents of an app, a "
        "document, a spreadsheet or a file. "
        "For an application, put its name in 'app_name' exactly as the user "
        "said it. "
        "For a FILE, put the FILE's name in 'app_name' WITH its extension "
        "('wa.xlsx', 'informe.pdf') or its full path - the file is then "
        "opened in the program this machine uses for that type, which is "
        "Excel for a .xlsx when Excel is installed and the user's own choice "
        "when they have made one. Saying only 'WA' or only 'the spreadsheet' "
        "is not enough: the extension is what tells a document from a "
        "program. "
        "This tool answers immediately and confirms the opening in the "
        "background: on status=started say only that it is opening, never that "
        "it is open. If it does not appear, you will be told separately - "
        "relay that plainly rather than claiming success. Never say you "
        "opened something without calling this tool, and never tell the user "
        "you cannot open a file or an application: you can, and this tool is "
        "how. "
        "To CLOSE an application use computer_control with action='close_app', "
        "not this tool. "
        "DO NOT use this tool to reach a WEB PAGE. 'open Chrome and go to "
        "YouTube', 'open YouTube', 'search the web' are all browser_control "
        "alone - it opens the browser itself. Calling open_app for the browser "
        "first and browser_control after it launches the browser TWICE, the "
        "second copy on a different profile, which is what puts a 'Who's using "
        "Chrome?' picker on the screen. Use this tool only when the user wants "
        "the application or the document and nothing in it."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "app_name": {
                "type": "STRING",
                "description": (
                    "The application's REAL name, not the user's shorthand. "
                    "Expand abbreviations and nicknames before passing them: "
                    "'wp' or 'whats' is 'WhatsApp', 'ig' is 'Instagram', 'vs "
                    "code' is 'Visual Studio Code'. A two-letter fragment "
                    "matches the wrong program - 'wp' finds WPS Office. Any "
                    "application on the machine works; there is no fixed list. "
                    "OR the name of a FILE to open, with its extension "
                    "('wa.xlsx') or its full path - in that case it is opened "
                    "in the program this machine associates with that file "
                    "type."
                ),
            }
        },
        "required": ["app_name"],
    },
    "handler": open_app,
}
