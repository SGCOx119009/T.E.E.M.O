"""
core/grounding/resolver.py — the orchestrator: which level, and what happens next.

THE LADDER
    Three levels, tried in order, and the first one with something to say
    wins:

        N0  the accessibility tree. Free, exact, and it knows the NAME of
            things. The preferred level by a wide margin.
        N1  a picture with numbered boxes drawn on it, for the surfaces
            that expose no tree at all.
        N2  a model that looks at the picture. Costs tokens, reads pixels as
            if they might be instructions, and is off unless the user turns
            it on.

    Each step down is a step towards a worse answer and a higher price, so
    the ladder only goes down when the level above had nothing.

THE PICTURE IS TAKEN LAST, NOT FIRST
    The accessibility tree does not need pixels. The coordinate space it
    reports in is the monitor's own pixel grid, which is known without
    photographing anything — and taking the photograph costs about seventy
    milliseconds on this machine, which is most of the budget the whole step
    is allowed.

    So the picture is taken only when a level that genuinely needs it runs.
    On a machine where the accessibility tree answers, nothing is captured
    at all.

WHAT IS CACHED, AND WHEN IT IS THROWN AWAY
    The tree walk is the expensive part, and it is repeated for every
    question. So the last observation is kept, and it is thrown away when
    the thing it described has changed: when the front window changes, or
    when it has simply been long enough that the screen may have moved on.

    The alternative — a picture, hashed, compared — is the plan's own
    suggestion, and it is used for the picture. For the tree the front
    window is the better signal, because it is the thing an action would
    land in.

WHEN THE COORDINATES ARE COMPUTED
    At EXECUTION time, never at observation time.

    The model is shown an index. The index is turned into a rectangle when
    the action is about to happen, from the geometry measured at that
    moment. Between the observation and the action the window may have
    moved, the display may have changed scale, the user may have scrolled —
    and a rectangle computed two seconds ago is a rectangle that is wrong
    now.

    Where it can be done without coordinates at all, it is: an element with
    a structured selector is acted upon THROUGH the selector, and the
    selector is re-resolved against the live tree.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any

from core.grounding import detect, som, uia
from core.grounding.base import (
    LEVEL_SOM,
    LEVEL_UIA,
    LEVEL_VLM,
    ElementMark,
    Geometry,
    GroundingResult,
)

# How long a captured picture is reused for. Long enough that a model that
# asks two questions in one turn does not pay for two captures; short
# enough that the picture is not stale by the time it is used.
CAPTURE_TTL_S = 0.5

# How long an observation may be reused before it is assumed stale.
OBSERVATION_TTL_S = 1.5

# The levels the resolver will try, in order.
LADDER: tuple[str, ...] = (LEVEL_UIA, LEVEL_SOM, LEVEL_VLM)


class GroundingResolver:
    """Observes the screen and acts on what was observed.

    One instance per session. It holds the last picture and the last
    observation, because both are expensive and both go stale together.
    """

    def __init__(self) -> None:
        self._capture: tuple[float, Any, Geometry, str] | None = None
        self._result: tuple[str, GroundingResult] | None = None
        self._last_reasons: list[str] = []

    # -- the geometry, without a picture ------------------------------------
    def geometry(self) -> Geometry:
        """Where the screen is, in every space that matters.

        Measured, not assumed: the monitor's rectangle and the ratio
        between physical pixels and mouse units are read off the machine.
        """
        try:
            from core import screenview
            scale = screenview.physical_to_logical()
        except Exception:
            scale = 1.0
        return uia._monitor_geometry(scale)

    # -- the picture --------------------------------------------------------
    def capture(self, *, force: bool = False) -> tuple[Any | None, Geometry, str]:
        """The screen as a picture, plus the geometry that explains it.

        Returns (image, geometry, reason). The image is None when the
        capture failed, and the reason says why.

        A capture can fail for reasons that are NOT the user's fault and
        NOT a bug: a UAC prompt puts the desktop on a different surface
        where screen capture is refused, and a locked session does the
        same. When that happens the assistant says so and carries on with
        what it knows, instead of taking the whole turn down with a
        traceback.
        """
        now = time.monotonic()
        if not force and self._capture is not None:
            stamp, image, geometry, digest = self._capture
            if now - stamp <= CAPTURE_TTL_S:
                return image, geometry, ""

        try:
            from core import screenview
            image, monitor, _px = screenview.capture()
        except Exception as exc:
            return None, self.geometry(), (
                f"the screen could not be captured: {type(exc).__name__}: {exc}"
            )

        geometry = self.geometry()
        digest = _digest(image)
        self._capture = (now, image, geometry, digest)
        return image, geometry, ""

    def _n0_says_something(self, result: GroundingResult) -> bool:
        """Does the tree name something an agent can ACT on?

        A window whose tree is only its own frame is not an answer. That is
        the normal shape of a Chromium application: the address bar, the
        tabs and every button are drawn, not exposed, and UI Automation
        reports the frame and nothing else. Measured on this machine, a
        Chromium window and a Visual Studio Code window both report three
        controls — the window and its own title bar.

        Three controls that cannot be clicked are not a reading of the
        screen, and continuing to the next level is cheaper than handing
        the model a list it cannot use.
        """
        if not result.marks:
            return False
        try:
            from core import screenview
            interactable = screenview.INTERACTABLE
        except Exception:
            interactable = frozenset()
        if not interactable:
            return True
        return any(m.role in interactable and m.enabled is not False
                   for m in result.marks)

    # -- the observation ----------------------------------------------------
    def observe(self, *, level: str = "auto", force: bool = False,
                budget: detect.Budget | None = None) -> GroundingResult:
        """What is on the screen, as numbered marks.

        Never raises. A machine with no accessibility tree, a refused
        capture and a grounding model that is switched off all produce a
        result that says so, in words a model can read and act on.
        """
        started = time.monotonic()
        reasons: list[str] = []
        geometry = self.geometry()

        # ── N0: the tree. No picture, no tokens, milliseconds ─────────────
        if level in ("auto", LEVEL_UIA):
            key = self._cache_key(geometry)
            if not force and self._result is not None:
                cached_key, cached = self._result
                if cached_key == key:
                    return cached
            result = uia.observe(geometry=geometry)
            if result.marks and self._n0_says_something(result):
                self._result = (key, result)
                self._last_reasons = reasons
                return result
            if result.marks:
                reasons.append(
                    f"{LEVEL_UIA}: only {result.count} elements, none of them "
                    f"something to press"
                )
            else:
                reasons.append(f"{LEVEL_UIA}: {result.reason or 'nothing found'}")

        # ── the picture, only now that something needs pixels ─────────────
        image, geometry, capture_reason = self.capture(force=force)
        if image is None:
            self._last_reasons = reasons + [capture_reason]
            return GroundingResult(
                level="none", status="unavailable",
                reason="; ".join(reasons + [capture_reason]),
                geometry=geometry,
                duration_ms=(time.monotonic() - started) * 1000.0,
            )

        order = LADDER if level == "auto" else (level,)
        for step in order:
            if step == LEVEL_UIA:
                continue
            if step == LEVEL_SOM:
                result = som.observe(image, geometry=geometry,
                                     elements=self._ocr_elements())
            elif step == LEVEL_VLM:
                result = detect.detect(image, budget=budget, geometry=geometry)
            else:
                continue
            if result.marks:
                self._result = (self._cache_key(geometry), result)
                self._last_reasons = reasons
                return result
            if result.reason:
                reasons.append(f"{step}: {result.reason}")
            else:
                reasons.append(f"{step}: nothing found")

        self._last_reasons = reasons
        return GroundingResult(
            level="none", status="unavailable",
            reason="; ".join(reasons) or "nothing on the screen could be named",
            geometry=geometry,
            duration_ms=(time.monotonic() - started) * 1000.0,
        )

    def _cache_key(self, geometry: Geometry) -> str:
        """What the last observation described, and when it was made.

        The front window is the signal: a click lands there, so a change of
        front window invalidates everything that was read. The time bucket
        is the other half — an observation that is more than a second and a
        half old is assumed to describe a screen that has moved on.
        """
        try:
            from core import screenview
            _app, title, pid, _handle = screenview._foreground()
            front = f"{pid}|{title}"
        except Exception:
            front = ""
        bucket = int(time.monotonic() / OBSERVATION_TTL_S)
        return f"{front}|{geometry.describe_full()}|{bucket}"

    def _ocr_elements(self) -> list[Any]:
        """Whatever the OCR backend could read, if there is one.

        Only asked for when the accessibility tree had nothing: the OCR
        backend walks the tree first and falls back to reading the picture,
        so calling it earlier would pay for a walk we already did.

        And only when there IS an OCR engine. The backend's own tree walk
        costs about a second on this machine, and asking a reader that does
        not exist for its opinion is a second wasted on every fallback.
        """
        try:
            from core import screenview
            if not screenview.has_ocr():
                return []
            view = screenview.look(allow_ocr=True)
        except Exception:
            return []
        if getattr(view, "source", "") != "ocr":
            return []
        try:
            return list(view.elements)
        except Exception:
            return []

    # -- acting -------------------------------------------------------------
    def resolve(self, index: int, result: GroundingResult | None = None
                ) -> tuple[int, int] | None:
        """The point to click, in mouse units — computed NOW.

        None when the index is unknown or the element has no position.
        """
        result = result or (self._result[1] if self._result else None)
        if result is None:
            return None
        mark = result.mark(int(index))
        if mark is None or not mark.bbox:
            return None
        x, y, w, h = result.geometry.capture_to_logical(mark.bbox)
        return x + w // 2, y + h // 2

    def act(self, index: int, *, action: str = "click",
            text: str = "",
            result: GroundingResult | None = None) -> dict:
        """Act on the element the model named by its index.

        Prefers a route with NO coordinates at all: an element that has a
        structured selector is invoked through it, which is re-resolved
        against the live tree at this moment. Only when there is no
        selector does this fall back to a point — and then it says so, in
        the `origin` field, because a coordinate from a picture and a
        coordinate from the accessibility tree are not the same evidence.
        """
        result = result or (self._result[1] if self._result else None)
        if result is None:
            return {"status": "failed", "verified": False,
                    "reason": "there is no observation to act on"}
        mark = result.mark(int(index))
        if mark is None:
            return {"status": "failed", "verified": False,
                    "reason": f"there is no element numbered {index}"}
        if not mark.actionable:
            return {"status": "failed", "verified": False,
                    "reason": "this element is not one the assistant may act on"}

        action = (action or "click").strip().lower()

        if action in ("click", "press", "invoke") and mark.source == "uia":
            outcome = uia.invoke(mark)
            if outcome.get("status") in ("done", "unverified"):
                outcome["origin"] = "selector"
                return outcome
            # The selector did not work — a window moved, a control was
            # replaced. Falling back to the point is allowed, and it says
            # which route it took.
        elif action == "focus" and mark.source == "uia":
            outcome = uia.focus(mark)
            outcome["origin"] = "selector"
            return outcome
        elif action == "type" and mark.source == "uia" and text:
            selector = uia.selector_for(mark)
            if selector:
                try:
                    from core import ui_adapter
                    outcome = ui_adapter.write_control(selector, text)
                    outcome["origin"] = "selector"
                    return outcome
                except Exception as exc:
                    return {"status": "failed", "verified": False,
                            "reason": f"UI Automation failed: {type(exc).__name__}"}

        point = self.resolve(index, result)
        if point is None:
            return {"status": "failed", "verified": False,
                    "reason": "this element has no position to act on"}
        return self._act_at(point, mark, action, text)

    def _act_at(self, point: tuple[int, int], mark: ElementMark,
                action: str, text: str) -> dict:
        """The coordinate route, for the elements that have no selector."""
        try:
            from core import screenact
        except Exception as exc:
            return {"status": "failed", "verified": False,
                    "reason": f"the actuator is unavailable: {type(exc).__name__}"}

        target = screenact.Point(point[0], point[1], origin="vision",
                                 label=mark.label)
        try:
            if action == "type" and text:
                act = screenact.type_text(text, target=target)
            elif action == "focus":
                act = screenact.hover(target)
            else:
                act = screenact.click(target)
        except Exception as exc:
            return {"status": "failed", "verified": False,
                    "reason": f"the action failed: {type(exc).__name__}: {exc}"}

        return {
            "status": "done" if act.ok else "failed",
            "verified": bool(act.ok),
            "reason": act.detail or act.error or str(act),
            "origin": "point",
            "point": list(point),
        }

    # -- state --------------------------------------------------------------
    @property
    def last_reasons(self) -> list[str]:
        """Why each level was passed over, for the log."""
        return list(self._last_reasons)

    @property
    def last_result(self) -> GroundingResult | None:
        return self._result[1] if self._result else None

    def invalidate(self) -> None:
        """Forget the cached observation. The picture goes with it."""
        self._capture = None
        self._result = None


def _digest(image: Any) -> str:
    """A cheap fingerprint of the picture.

    The picture is reduced to a small greyscale copy and hashed. It does not
    have to detect every change — it has to detect the changes that matter,
    and it has to be fast enough to run on every single step. A change
    smaller than the reduction is a change nobody is going to act on.
    """
    try:
        reduced = image.convert("L").resize((64, 36))
        return hashlib.sha256(reduced.tobytes()).hexdigest()[:32]
    except Exception:
        return ""


# The resolver the session uses. One is enough: the picture is the screen,
# and there is only one screen.
_RESOLVER: GroundingResolver | None = None


def session_resolver() -> GroundingResolver:
    """The session's resolver.

    Named `session_resolver` and not `resolver` on purpose: a function
    called `resolver` would shadow the `core.grounding.resolver` MODULE in
    the package namespace, and `from core.grounding import resolver` would
    then hand back the function instead of the module.
    """
    global _RESOLVER
    if _RESOLVER is None:
        _RESOLVER = GroundingResolver()
    return _RESOLVER
