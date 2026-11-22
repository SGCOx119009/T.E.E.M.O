"""
core/grounding/uia.py — grounding level N0: the accessibility tree.

WHY THIS LEVEL IS FIRST
    Windows exposes what is on the screen to any process that asks, in
    structured form: every button, every field, every menu, with its name,
    its role and its rectangle. That is a list of things that ARE on the
    screen, which is exactly what a model needs and exactly what a picture
    of the screen cannot give it reliably.

    It costs a few milliseconds and no tokens. So it is tried first, and the
    expensive levels only run when this one has nothing to say.

WHAT IT DOES NOT DO
    It does not click. This module READS. The coordinates it produces are
    kept for the executor, and the structured selector it produces is the
    way to act on an element WITHOUT coordinates at all — which is the
    better answer whenever it is available, because a selector that still
    matches when the window moves is worth more than a pixel that was right
    a moment ago.

THE BUDGET IS A PROMISE, NOT A HOPE
    A UI Automation tree is a cross-process protocol: every property of
    every node is a message to the other application. Measured on this
    machine, a property costs about half a millisecond and one node about
    three, and the tree of an ordinary application has thousands of nodes
    at depth ten — where one `children()` call materialises four thousand
    wrappers.

    So the walk is bounded by the CLOCK, not by the tree: it is breadth
    first, so what survives an exhausted budget is the shallow, big,
    obvious controls — which are the ones a person would click — and it
    stops rather than running for a second. The result is a partial
    observation that is still an observation, instead of a perfect one that
    arrives after the user has given up.

THE SELF-GUARD
    The assistant's own windows are never grounds for anything. Two reasons,
    and the second one is the real one:

      * There is nothing to gain from reading them. The agent's job is on
        somebody else's window.
      * A UI Automation query into another process is answered by THAT
        process's message loop. When the target is us, every one of the
        hundreds of calls in a tree walk goes out to our own interface
        thread and comes back while that thread is also drawing a face at
        fifty frames a second. Measured on a Qt window with forty buttons:
        47 elements in 5.56 seconds. The whole application went stiff.

    "Ours" means OUR WINDOWS — the ones this process owns — and not
    "somewhere in our ancestry". A shell started from File Explorer makes
    explorer.exe an ancestor, and with the looser rule every Explorer
    window on the machine becomes the assistant's own: four of them, all
    of them the user's, all of them refused.

    And it means the window, not the drawing: a control hosted by ANOTHER
    process inside one of our windows is still ours, because clicking it
    clicks our own interface. That is why the element carries its window
    handle and its owning process: the question "is this ours?" is a
    question about where the thing LIVES, not about who drew it.
"""
from __future__ import annotations

import atexit
import os
import threading
import time
from typing import Any

from core.grounding.base import (
    LEVEL_UIA,
    ElementMark,
    Geometry,
    GroundingResult,
    format_observation,
)

# How long one walk may take. The criterion for this phase is a capture and
# a grounding step inside 150 ms, so the walk is given less than that and
# the rest is the picture.
DEFAULT_BUDGET_S = 0.09

# Hard ceilings, so a pathological tree cannot fill the observation.
MAX_ELEMENTS = 120
MAX_WINDOWS = 6
# A control worth clicking is never deep. Measured: the useful controls of
# an ordinary application live in the first three levels, and the tree
# explodes at level ten.
MAX_DEPTH = 6

# How long a request may wait for the background warm-up. The wait is a
# WAIT: the work is done by the thread that started when the package was
# imported, so the request never pays for it, it only waits for it.
WARM_WAIT_S = 20.0

# How long the live lookup may take. It happens at EXECUTION time, on the
# way to a click, so it is bounded by the same kind of clock the walk is:
# a control that cannot be found quickly is a control that is not there.
LIVE_BUDGET_S = 0.12

# The one-off. Importing pywinauto costs about half a second and importing
# pyautogui about one and a quarter, and neither is the cost of a question.
# They are the cost of the SESSION, and the session starts long before the
# user asks the assistant to look at anything.
_WARM_EVENT = threading.Event()
_WARM_LOCK = threading.Lock()
_WARM_STARTED = False

# The warm-up thread is told to leave when the program is leaving.
_WARM_STOP = threading.Event()


def _release_warm_thread() -> None:
    """Let the warm-up thread go, before the interpreter starts tearing down.

    A daemon thread is killed when the interpreter finalises, and its COM
    apartment dies with it: the thread that is asked to leave is the thread
    that still owns the door, so it is the one that has to close it.
    """
    _WARM_STOP.set()


atexit.register(_release_warm_thread)


def warm(*, wait_s: float = 0.0) -> bool:
    """Load the accessibility machinery, in the background.

    The first UI Automation query of a session pays for importing
    pywinauto, for generating the COM type library and for importing
    pyautogui. Measured on this machine that is about 1.9 seconds. It is
    the cost of the SESSION, not of a question, and it is charged to the
    request path when the request does it itself.

    So the request does not do it: the package starts this when it is
    imported, and a request waits for it if it is not finished yet. The
    work is done ONCE, by the thread that started it, and no request ever
    repeats it.

    Returns True when the machinery is loaded.
    """
    global _WARM_STARTED
    with _WARM_LOCK:
        if not _WARM_STARTED:
            _WARM_STARTED = True

            def _run() -> None:
                try:
                    import mss                     # noqa: F401
                    import pyautogui               # noqa: F401
                except Exception:
                    pass
                try:
                    from core import screenview
                    screenview.warm_up()
                except Exception:
                    pass
                finally:
                    _WARM_EVENT.set()
                    # THE THREAD STAYS ALIVE, AND THAT IS THE POINT.
                    #
                    #     pywinauto opens a COM apartment in the thread that
                    #     imports it, and it sets the apartment mode of the
                    #     WHOLE PROCESS from there. If that thread dies, its
                    #     apartment dies with it, and every COM object that
                    #     crossed it is left pointing at a door that no
                    #     longer exists: RPC_E_INVALID_HANDLE, which is
                    #     0x80010108, and Windows announces it as a fatal
                    #     exception even when the program carries on.
                    #
                    #     It was measured here: four of those announcements
                    #     in one run of the suite, always in the middle of
                    #     something else entirely — a repaint, a file read —
                    #     which is the signature of an accident that belongs
                    #     to somebody else.
                    #
                    #     So the thread that warms up stays in the house for
                    #     as long as the program does, and it is the one that
                    #     closes the door when the program leaves.
                    if threading.current_thread() is not threading.main_thread():
                        _WARM_STOP.wait()

            try:
                threading.Thread(target=_run, daemon=True,
                                 name="GroundingWarmUp").start()
            except Exception:
                _WARM_EVENT.set()

    if wait_s and wait_s > 0:
        return _WARM_EVENT.wait(wait_s)
    return _WARM_EVENT.is_set()


def available() -> tuple[bool, str]:
    """Can this level run at all? ((True, "") when it can.)"""
    try:
        from core import screenview
    except Exception as exc:                      # pragma: no cover - import guard
        return False, f"screenview is unavailable: {type(exc).__name__}"
    if not screenview.has_uia():
        return False, "UI Automation is not available on this machine"
    return True, ""


def own_handles() -> set[int]:
    """The handles of the assistant's own windows."""
    try:
        from core import screenview
        return set(screenview.own_window_handles())
    except Exception:
        return set()


def _root_window(hwnd: int) -> int:
    """The TOP-LEVEL window that contains this handle.

    Zero when the handle is unknown, invalid, or already top-level and
    the operating system could not say.

    This is the question that separates "our window" from "a window of
    somebody else that happens to be on top of ours": a control drawn by
    another process inside our window has a handle whose ROOT is our
    window, and that is what makes it ours.
    """
    try:
        hwnd = int(hwnd)
    except Exception:
        return 0
    if not hwnd:
        return 0
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        GA_ROOT = 2
        root = user32.GetAncestor(wintypes.HWND(hwnd), GA_ROOT)
        return int(root or 0)
    except Exception:
        return 0


def _is_own(pid: int, hwnd: int = 0, handles: set[int] | None = None) -> bool:
    """Is this ours?

    Two questions, asked in the right order: is the process this very
    one, and is the handle one of our own windows. Neither of them is
    "is this executable somewhere in our ancestry".

    A handle is ours when it is one of our windows, and also when the
    TOP-LEVEL window that contains it is one of ours: that is the case
    of a control hosted by another process inside our own window, which
    is ours by where it lives even though another process drew it.

    `handles` is passed in rather than looked up, because enumerating the
    desktop's windows costs tens of milliseconds and doing it once per
    ELEMENT ate the whole walk budget: the fourth control of a window was
    the one that never arrived.
    """
    try:
        if pid and int(pid) == os.getpid():
            return True
    except Exception:
        pass
    if not hwnd:
        return False
    try:
        known = own_handles() if handles is None else handles
    except Exception:
        return False
    if not known:
        return False
    try:
        hwnd = int(hwnd)
    except Exception:
        return False
    if hwnd in known:
        return True
    root = _root_window(hwnd)
    return bool(root and root in known)


def _node_attribute(node: Any, name: str, default: Any = "") -> Any:
    """One UIA attribute, without letting a COM error kill the walk.

    UIA is a cross-process protocol: any property can raise when the target
    application is busy, mid-repaint or shutting down. One control having a
    bad moment must not cost us the other four hundred.
    """
    try:
        value = getattr(node, name, default)
    except Exception:
        return default
    return default if value is None else value


def _node_pid(node: Any) -> int:
    try:
        return int(_node_attribute(node, "process_id", 0) or 0)
    except Exception:
        return 0


def _node_handle(node: Any) -> int:
    """The element's OWN native window handle, or 0 when it has none.

    Most controls in the accessibility tree are not windows of their own,
    and for those this is 0 — which is exactly why the ownership rule
    cannot be the process alone.
    """
    try:
        return int(_node_attribute(node, "handle", 0) or 0)
    except Exception:
        return 0


def _node_focused(node: Any) -> bool:
    try:
        raw = getattr(node, "element", None)
        return bool(getattr(raw, "CurrentHasKeyboardFocus", False))
    except Exception:
        return False


def _windows(exclude: set[int] | None = None) -> list[tuple[Any, str, int, int]]:
    """Visible top-level windows, as (node, title, pid, hwnd).

    Expensive: one enumeration of every window on the desktop costs about
    seventy-five milliseconds on this machine, which is most of the whole
    budget. So it is only done when the foreground window had nothing, and
    it is never done first.
    """
    from pywinauto import Desktop

    exclude = exclude or set()
    out: list[tuple[Any, str, int, int]] = []
    for window in Desktop(backend="uia").windows(
        visible_only=True, top_level_only=True
    ):
        try:
            info = window.element_info
            title = str(_node_attribute(info, "name", "") or "")
            pid = _node_pid(info)
            hwnd = int(_node_attribute(info, "handle", 0) or 0)
        except Exception:
            continue
        if hwnd in exclude:
            continue
        out.append((info, title, pid, hwnd))
    return out


def _foreground_window() -> tuple[Any, str, int, int] | None:
    """The window in front, as (node, title, pid, hwnd). None when there is none.

    Resolved from the handle the OS already knows, which costs two
    property reads instead of a desktop-wide enumeration.
    """
    try:
        from core import screenview
        from pywinauto.uia_element_info import UIAElementInfo
        _app, _title, _pid, handle = screenview._foreground()
        handle = int(handle or 0)
        if not handle:
            return None
        node = UIAElementInfo(handle)
        title = str(_node_attribute(node, "name", "") or "")
        pid = _node_pid(node)
        return node, title, pid, handle
    except Exception:
        return None


def _walk(root: Any, scale: float, deadline: float,
          max_elements: int) -> list[tuple[Any, Any]]:
    """Breadth-first walk of one window's tree.

    Breadth first, so that when the budget runs out what survives is the
    shallow, big, obvious controls rather than one deep branch of a
    sidebar. The clock is checked BEFORE every `children()` call, because
    that call is the expensive one: on a deep tree it materialises
    thousands of wrappers at once.

    The root is the window's own element information, so nothing has to be
    resolved twice.
    """
    from core import screenview

    out: list[tuple[Any, Any]] = []
    frontier: list[tuple[Any, int]] = [(root, 0)]
    visited = 0

    while frontier and len(out) < max_elements:
        if time.monotonic() > deadline:
            break
        # Let everything else breathe, every so often. A tree walk is
        # hundreds of cross-process calls in a tight Python loop, and this
        # runs inside an application that is also streaming audio; without a
        # yield the user hears it as a stutter.
        visited += 1
        if visited % 50 == 0:
            time.sleep(0.001)

        node, depth = frontier.pop(0)
        children: list[Any] = []
        if depth < MAX_DEPTH and time.monotonic() <= deadline:
            try:
                children = node.children()
            except Exception:
                children = []

        element = screenview.element_from_uia(node, len(out), depth, scale)
        if element is not None:
            out.append((element, node))

        for child in children:
            frontier.append((child, depth + 1))

    return out


def _mark_for(element: Any, node: Any, index: int, *, title: str, pid: int,
              hwnd: int, geometry: Geometry, source: str,
              own: bool = False, handles: set[int] | None = None) -> ElementMark:
    """One element plus its UIA metadata, as a mark."""
    control_type = str(_node_attribute(node, "control_type", "") or "")
    automation_id = str(_node_attribute(node, "automation_id", "") or "")

    # The element's rectangle is in MOUSE UNITS (core/screenview.py scales it
    # on the way out). The observation is in CAPTURE PIXELS. The conversion
    # is the DPI story, and it is the reason a 150% display does not send
    # every click to the wrong control.
    bbox = geometry.logical_to_capture(
        (element.left, element.top, element.width, element.height)
    )

    # An element hosted by another process inside our window is still ours.
    # The question is where the element LIVES, so it is answered with the
    # handles: the window that was walked, and the element's own handle
    # (whose top-level ancestor is the window it is really inside).
    element_hwnd = _node_handle(node)
    if not own:
        own = (_is_own(pid, hwnd, handles)
               or _is_own(pid, element_hwnd, handles))

    selector: dict[str, Any] = {}
    if title and control_type:
        selector = {
            "window_name": title,
            "name": element.name or "",
            "automation_id": automation_id,
            "control_type": control_type,
            "ordinal": index,
        }

    return ElementMark(
        index=index,
        role=element.role,
        name=element.name,
        bbox=bbox,
        source=source,
        enabled=element.enabled,
        focused=_node_focused(node),
        protected=element.protected,
        own=own,
        window=title,
        pid=pid,
        hwnd=element_hwnd,
        automation_id=automation_id,
        control_type=control_type,
        selector=selector,
    )


def _rect_of(node: Any) -> tuple[int, int, int, int] | None:
    """The node's rectangle RIGHT NOW, in physical screen pixels.

    Read from the live tree, which is the whole point: the rectangle the
    model was shown was read up to a second and a half ago.
    """
    try:
        rect = getattr(node, "rectangle", None)
        if rect is None:
            return None
        left = int(getattr(rect, "left", 0))
        top = int(getattr(rect, "top", 0))
        right = int(getattr(rect, "right", 0))
        bottom = int(getattr(rect, "bottom", 0))
    except Exception:
        return None
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _rect_by_handle(hwnd: int) -> tuple[int, int, int, int] | None:
    """The rectangle of a window handle, in physical screen pixels."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        rect = wintypes.RECT()
        if not user32.GetWindowRect(wintypes.HWND(int(hwnd)),
                                    ctypes.byref(rect)):
            return None
        left = int(rect.left)
        top = int(rect.top)
        right = int(rect.right)
        bottom = int(rect.bottom)
    except Exception:
        return None
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _live_window(title: str, deadline: float) -> Any | None:
    """The top-level window with this title, as it is NOW.

    The window in front is tried first: it costs two property reads
    instead of a desktop-wide enumeration, and the element usually still
    is in the window that was in front when it was observed.
    """
    front = _foreground_window()
    if front is not None and str(front[1] or "") == str(title or ""):
        return front[0]
    if time.monotonic() > deadline:
        return None
    try:
        from pywinauto import Desktop

        windows = Desktop(backend="uia").windows(
            title=title, visible_only=True, top_level_only=True
        )
    except Exception:
        return None
    if len(windows) != 1:
        return None
    try:
        return windows[0].element_info
    except Exception:
        return None


def _find_live(root: Any, control_type: str, name: str, automation_id: str,
               deadline: float) -> tuple[Any | None, str]:
    """The control with this identity, in the tree that is there NOW.

    The same rule the executor uses: the control type, the name and the
    automation id must identify exactly ONE control. Two controls with
    the same name are not a control, they are a guess.
    """
    matches: list[Any] = []
    frontier: list[tuple[Any, int]] = [(root, 0)]
    while frontier:
        if time.monotonic() > deadline:
            return None, "the live lookup ran out of time"
        node, depth = frontier.pop(0)
        try:
            kind = str(_node_attribute(node, "control_type", "") or "")
            label = str(_node_attribute(node, "name", "") or "")
        except Exception:
            kind, label = "", ""
        if kind == control_type and label == name:
            if not automation_id or str(
                    _node_attribute(node, "automation_id", "") or ""
            ) == automation_id:
                matches.append(node)
                if len(matches) > 1:
                    return None, ("the control's name no longer identifies "
                                  "exactly one control")
        if depth < MAX_DEPTH:
            try:
                children = node.children()
            except Exception:
                children = []
            for child in children:
                frontier.append((child, depth + 1))
    if not matches:
        return None, "the control is no longer in the tree"
    return matches[0], "the control was found again in the live tree"


def live_rect(mark: ElementMark, *,
              budget_s: float = LIVE_BUDGET_S
              ) -> tuple[tuple[int, int, int, int] | None, str]:
    """Where the element is RIGHT NOW, in physical screen pixels.

    Returns ((left, top, right, bottom), reason). The rectangle is None
    when it could not be measured, and the reason says why — because a
    caller that falls back to the old rectangle has to be able to SAY
    that it fell back.

    Two ways to measure it, and the honest one is tried first:

      * by the selector — the control is looked up again in the live
        tree and its rectangle is read from it. This is the same
        measurement the observation was built from, taken again.
      * by the element's own window handle — one call to the operating
        system, for the controls that are windows of their own.
    """
    reason = "this element has no selector to look it up with"
    selector = selector_for(mark)
    if selector:
        deadline = time.monotonic() + max(0.02, float(budget_s))
        root = _live_window(str(selector.get("window_name") or ""), deadline)
        if root is None:
            reason = "the window is no longer on the screen"
        else:
            node, reason = _find_live(
                root,
                str(selector.get("control_type") or ""),
                str(selector.get("name") or ""),
                str(selector.get("automation_id") or ""),
                deadline,
            )
            if node is not None:
                rect = _rect_of(node)
                if rect is not None:
                    return rect, reason
                reason = "the control has no rectangle right now"
    if mark.hwnd:
        rect = _rect_by_handle(mark.hwnd)
        if rect is not None:
            return rect, "the element's own window handle, read just now"
        reason = "the element's window handle is no longer valid"
    return None, reason


def observe(*, budget_s: float = DEFAULT_BUDGET_S,
            max_elements: int = MAX_ELEMENTS,
            max_windows: int = MAX_WINDOWS,
            include_own: bool = False,
            scan_all: bool = False,
            geometry: Geometry | None = None) -> GroundingResult:
    """Read the accessibility tree and turn it into numbered marks.

    The window in FRONT is read first, and it is read alone unless the
    caller asks for the whole desktop: a click lands in the front window,
    and enumerating every window on the desktop costs more than the entire
    budget.

    Never raises: a machine with no UI Automation, a busy target and a
    truncated tree are all NORMAL outcomes, and each of them comes back as a
    result that says what happened.
    """
    started = time.monotonic()

    ok, why = available()
    if not ok:
        return GroundingResult(
            level=LEVEL_UIA, status="unavailable", reason=why,
            duration_ms=(time.monotonic() - started) * 1000.0,
        )

    from core import screenview

    # The accessibility machinery is loaded in the BACKGROUND, by the
    # thread that started when this package was imported. The first
    # query of a session pays for importing pywinauto and for generating
    # the COM type library — about 1.9 seconds on this machine — and that
    # is the cost of the session, not of the walk. Charged to the walk,
    # it ended it before the fourth element.

    # So the request does not do that work: it waits for the warm-up
    # that is already running, and walks the tree with a stack that is
    # already loaded.
    warm(wait_s=WARM_WAIT_S)

    scale = (geometry.scale if geometry is not None
             else screenview.physical_to_logical())
    geometry = geometry or _monitor_geometry(scale)

    marks: list[ElementMark] = []
    # The assistant's own windows, enumerated ONCE. Doing it per element
    # cost tens of milliseconds each time and ate the walk budget.
    handles = own_handles()

    front = _foreground_window()
    candidates: list[tuple[Any, str, int, int]] = []
    if front is not None:
        candidates.append(front)
    if scan_all:
        exclude = {front[3]} if front else set()
        try:
            candidates.extend(_windows(exclude))
        except Exception:
            pass

    for window_node, title, pid, hwnd in candidates[:max_windows]:
        if marks:
            break
        window_is_own = _is_own(pid, hwnd, handles)
        if not include_own and window_is_own:
            continue
        # The clock starts HERE, when the walk starts. The budget is for the
        # walk: it is not for finding the window, which costs a couple of
        # property reads, and it is certainly not for the setup above.
        # Budgeting the whole call meant that the first observation of a
        # session — the one where the accessibility libraries are still
        # loading — arrived with an empty list and no explanation.
        deadline = time.monotonic() + max(0.02, float(budget_s))
        for element, node in _walk(window_node, scale, deadline,
                                   max_elements - len(marks)):
            if len(marks) >= max_elements:
                break
            element_pid = _node_pid(node) or pid
            mark = _mark_for(element, node, len(marks) + 1, title=title,
                             pid=element_pid, hwnd=hwnd, geometry=geometry,
                             source="uia", own=window_is_own, handles=handles)
            if mark.own and not include_own:
                continue
            marks.append(mark)

    duration = (time.monotonic() - started) * 1000.0
    if not marks:
        return GroundingResult(
            level=LEVEL_UIA, status="unavailable",
            reason="the accessibility tree held nothing worth naming",
            geometry=geometry, duration_ms=duration, source="uia",
        )

    return GroundingResult(
        level=LEVEL_UIA,
        status="ok",
        marks=tuple(marks),
        observation=format_observation(
            marks, level=LEVEL_UIA, source="uia", geometry=geometry
        ),
        geometry=geometry,
        duration_ms=duration,
        source="uia",
    )


def _monitor_geometry(scale: float) -> Geometry:
    """The geometry of the monitor that a capture would cover.

    No picture is taken: the monitor's pixel grid IS what a capture of it
    would be, and the geometry is what the observation needs. That is what
    keeps this level inside its budget — the picture is only worth taking
    when a level that needs pixels is the one running.
    """
    try:
        from core import screenview
        monitor = screenview.monitor_rect()
    except Exception:
        return Geometry()
    left = int(monitor.get("left", 0) or 0)
    top = int(monitor.get("top", 0) or 0)
    width = int(monitor.get("width", 0) or 0)
    height = int(monitor.get("height", 0) or 0)
    return Geometry(
        origin_screen=(left, top),
        size_capture=(width, height),
        size_logical=(int(round(width * scale)), int(round(height * scale))),
        scale=scale,
    )


def selector_for(mark: ElementMark) -> dict:
    """The structured selector for this element, or {} when it has none.

    A selector is how an element is acted upon WITHOUT coordinates. It is
    only usable when the window has a title, the control has a type and the
    name is not empty — otherwise the match would be a guess.
    """
    selector = dict(mark.selector or {})
    if not selector.get("window_name") or not selector.get("control_type"):
        return {}
    if not selector.get("name"):
        return {}
    return selector


def invoke(mark: ElementMark) -> dict:
    """Press this element, by selector, with no coordinates anywhere.

    Delegates to `core/ui_adapter.py`, the adapter that was ported and
    tested for exactly this: it requires the selector to identify EXACTLY
    one control, it refuses password fields, and it verifies the effect
    instead of claiming it.
    """
    if not mark.actionable:
        return {"status": "failed", "verified": False,
                "reason": "this element is not one the assistant may act on"}
    selector = selector_for(mark)
    if not selector:
        return {"status": "failed", "verified": False,
                "reason": "this element has no unambiguous selector"}
    try:
        from core import ui_adapter
    except Exception as exc:
        return {"status": "failed", "verified": False,
                "reason": f"the UI Automation adapter is unavailable: "
                          f"{type(exc).__name__}"}
    try:
        return ui_adapter.invoke_control(selector)
    except Exception as exc:
        return {"status": "failed", "verified": False,
                "reason": f"UI Automation failed: {type(exc).__name__}"}


def focus(mark: ElementMark) -> dict:
    """Put the keyboard focus on this element, by selector."""
    if not mark.actionable:
        return {"status": "failed", "verified": False,
                "reason": "this element is not one the assistant may act on"}
    selector = selector_for(mark)
    if not selector:
        return {"status": "failed", "verified": False,
                "reason": "this element has no unambiguous selector"}
    try:
        from core import ui_adapter
        return ui_adapter.focus_control(selector)
    except Exception as exc:
        return {"status": "failed", "verified": False,
                "reason": f"UI Automation failed: {type(exc).__name__}"}


# The session's warm-up starts HERE, when this module is imported: the
# accessibility stack and the capture libraries are loaded while nothing
# is waiting for them. A request finds them already loaded.
warm()
