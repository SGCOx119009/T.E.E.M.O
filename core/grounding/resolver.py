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
import threading
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

# How long a capture that is already on its way may be waited for. It was
# started beside the tree walk, so it is nearly always finished by the
# time anybody asks for it.
CAPTURE_WAIT_S = 5.0

# How long a tree walk that is already on its way may be waited for. It
# was started beside the picture, so it is nearly always finished too.
WALK_WAIT_S = 5.0

# The levels the resolver will try, in order.
LADDER: tuple[str, ...] = (LEVEL_UIA, LEVEL_SOM, LEVEL_VLM)


class _Pending:
    """A capture that is being taken while the tree is being walked.

    The picture and the walk do not need each other, so they do not wait
    for each other either.
    """

    __slots__ = ("done", "image", "geometry", "reason", "stamp")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.image: Any = None
        self.geometry = Geometry()
        self.reason = ""
        self.stamp = 0.0


class _Walk:
    """A tree walk that is being made while the picture is being taken.

    The same idea from the other side: the two halves of a grounding
    step do not depend on each other, so the one that is not needed yet
    is started anyway, in its own thread.
    """

    __slots__ = ("done", "result", "key", "stamp")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.result: GroundingResult | None = None
        self.key = ""
        self.stamp = 0.0


class GroundingResolver:
    """Observes the screen and acts on what was observed.

    One instance per session. It holds the last picture and the last
    observation, because both are expensive and both go stale together.
    """

    def __init__(self) -> None:
        self._capture: tuple[float, Any, Geometry, str] | None = None
        self._result: tuple[str, GroundingResult] | None = None
        self._result_stamp: float = 0.0
        self._last_reasons: list[str] = []
        self._pending: _Pending | None = None
        self._walk: _Walk | None = None

    # -- the geometry, without a picture ------------------------------------
    def geometry(self) -> Geometry:
        """Where the screen is, in every space that matters.

        Measured, not assumed: the monitor's rectangle and the ratio
        between physical pixels and mouse units are read off the machine.

        The measurement needs the capture libraries, and those are loaded
        in the background when the package is imported. Waiting for that
        here keeps the import out of the request path.
        """
        uia.warm(wait_s=uia.WARM_WAIT_S)
        try:
            from core import screenview
            scale = screenview.physical_to_logical()
        except Exception:
            scale = 1.0
        return uia._monitor_geometry(scale)

    # -- the picture --------------------------------------------------------
    def _capture_now(self) -> tuple[Any | None, Geometry, str]:
        """Take the picture, and the geometry that explains it."""
        try:
            from core import screenview
            image, _monitor, _px = screenview.capture()
        except Exception as exc:
            return None, self.geometry(), (
                f"the screen could not be captured: {type(exc).__name__}: {exc}"
            )
        return image, self.geometry(), ""

    def _start_prefetch(self, *, force: bool) -> _Pending | None:
        """Begin the picture, in its own thread, beside the tree walk.

        The tree does not need pixels. But when the tree says nothing,
        the pixels are needed, and taking them AFTER the walk means
        paying for the walk AND the picture, one after the other —
        measured together at 285 ms on this machine, against a budget of
        150.

        So the picture starts now, in its own thread, and the walk runs
        beside it. The tree never depends on the picture: when the tree
        answers, the picture is dropped and the observation is exactly
        what it would have been.

        Returns None when there is nothing to fetch — a picture taken
        moments ago is still good, and taking another one would be
        paying twice for the same thing.
        """
        if not force and self._capture is not None:
            stamp, _image, _geometry, _digest = self._capture
            if time.monotonic() - stamp <= CAPTURE_TTL_S:
                return None

        pending = _Pending()
        self._pending = pending

        def _run() -> None:
            try:
                image, geometry, reason = self._capture_now()
            except Exception as exc:                # pragma: no cover
                image, geometry, reason = None, Geometry(), (
                    f"the screen could not be captured: {type(exc).__name__}"
                )
            pending.image = image
            pending.geometry = geometry
            pending.reason = reason
            pending.stamp = time.monotonic()
            pending.done.set()

        try:
            threading.Thread(target=_run, daemon=True,
                             name="GroundingCapture").start()
        except Exception:                           # pragma: no cover
            self._pending = None
            return None
        return pending

    def _release_prefetch(self, pending: _Pending | None) -> None:
        """Give up on a picture nobody asked for."""
        if pending is not None and self._pending is pending:
            self._pending = None

    def _start_walk(self) -> None:
        """Begin the tree walk in the background, beside the picture.

        The other half of the same parallel step. The tree does not need
        the picture and the picture does not need the tree, so when
        somebody asks for the picture the walk is started too, and the
        two overlap instead of queueing up behind each other.

        The walk is only USED by whoever asks for it next, and only if it
        still describes the same screen — a walk that was started a while
        ago, in front of a different window, is not reused.
        """
        if self._walk is not None:
            return
        walk = _Walk()
        self._walk = walk

        def _run() -> None:
            try:
                geometry = self.geometry()
                walk.key = self._cache_key(geometry)
                walk.result = uia.observe(geometry=geometry)
            except Exception:
                walk.result = None
            walk.stamp = time.monotonic()
            walk.done.set()

        try:
            threading.Thread(target=_run, daemon=True,
                             name="GroundingWalk").start()
        except Exception:                           # pragma: no cover
            self._walk = None

    def _walk_now(self, geometry: Geometry, key: str) -> GroundingResult:
        """The tree, walked now — or the walk that is already on its way.

        A walk that was started a moment ago is waited for, not repeated
        — but only when it still describes THIS screen. A walk of
        another window, or of a screen that has moved on, is no answer
        at all, and the tree is walked again.
        """
        walk = self._walk
        if walk is not None:
            self._walk = None
            walk.done.wait(WALK_WAIT_S)
            if (walk.result is not None and walk.key == key
                    and time.monotonic() - walk.stamp <= OBSERVATION_TTL_S):
                return walk.result
        return uia.observe(geometry=geometry)

    def capture(self, *, force: bool = False,
                walk: bool = True) -> tuple[Any | None, Geometry, str]:
        """The screen as a picture, plus the geometry that explains it.

        Returns (image, geometry, reason). The image is None when the
        capture failed, and the reason says why.

        A capture can fail for reasons that are NOT the user's fault and
        NOT a bug: a UAC prompt puts the desktop on a different surface
        where screen capture is refused, and a locked session does the
        same. When that happens the assistant says so and carries on with
        what it knows, instead of taking the whole turn down with a
        traceback.

        Two things happen here that used to happen one after the other:
        the picture is taken, and the tree is walked BESIDE it. The walk
        is the other half of the same grounding step, it does not need
        the picture, and nobody has to wait for it twice.

        `walk=False` says "the tree has already been walked" — which is
        what the ladder says, because by then it has.
        """
        pending = self._pending
        if pending is not None:
            self._pending = None
            pending.done.wait(CAPTURE_WAIT_S)
            if pending.image is not None:
                self._capture = (pending.stamp, pending.image,
                                 pending.geometry, "")
                return pending.image, pending.geometry, ""

        if walk:
            self._start_walk()

        now = time.monotonic()
        if not force and self._capture is not None:
            stamp, image, geometry, digest = self._capture
            if now - stamp <= CAPTURE_TTL_S:
                return image, geometry, ""

        image, geometry, reason = self._capture_now()
        if image is not None:
            self._capture = (now, image, geometry, "")
        return image, geometry, reason

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

        # The one-off is not this request's to pay. The accessibility
        # stack and the capture libraries are loaded in the background
        # when the package is imported; this waits for that work rather
        # than repeating it in front of the user.
        uia.warm(wait_s=uia.WARM_WAIT_S)

        pending: _Pending | None = None
        try:
            # ── N0: the tree. No picture, no tokens, milliseconds ─────────
            if level in ("auto", LEVEL_UIA):
                key = self._cache_key(geometry)
                if not force:
                    cached = self._cached_result(key)
                    if cached is not None:
                        return cached

                # The picture is fetched BESIDE the walk, not after it.
                # Its freshness is the CAPTURE TTL, not this call's
                # `force`: `force` is about the OBSERVATION, and a
                # picture taken a moment ago is still a picture of this
                # screen.
                pending = self._start_prefetch(force=False)

                result = self._walk_now(geometry, key)
                if result.marks and self._n0_says_something(result):
                    self._result = (key, result)
                    self._result_stamp = time.monotonic()
                    self._last_reasons = reasons
                    return result
                if result.marks:
                    reasons.append(
                        f"{LEVEL_UIA}: only {result.count} elements, none of them "
                        f"something to press"
                    )
                else:
                    reasons.append(f"{LEVEL_UIA}: {result.reason or 'nothing found'}")
            else:
                pending = self._start_prefetch(force=force)

            # ── the picture, only now that something needs pixels ─────────
            image, geometry, capture_reason = self.capture(force=False,
                                                           walk=False)
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
                    self._result_stamp = time.monotonic()
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
        finally:
            # A picture nobody asked for is dropped rather than kept.
            self._release_prefetch(pending)

    def _cache_key(self, geometry: Geometry) -> str:
        """What the last observation described.

        The front window is the signal: a click lands there, so a change
        of front window invalidates everything that was read.

        How OLD the observation is is a different question, and it is
        answered by the observation's own timestamp rather than by a
        time BUCKET. A bucket throws a perfectly good observation away
        the moment the clock crosses a line — which is the same as
        throwing it away for no reason at all, and it made a second
        question about the same screen walk the tree again.
        """
        try:
            from core import screenview
            _app, title, pid, _handle = screenview._foreground()
            front = f"{pid}|{title}"
        except Exception:
            front = ""
        return f"{front}|{geometry.describe_full()}"

    def _cached_result(self, key: str) -> GroundingResult | None:
        """The last observation, if it still describes this screen.

        None when there is none, when it described something else, or
        when it is too old to be trusted.
        """
        if self._result is None:
            return None
        cached_key, cached = self._result
        if cached_key != key:
            return None
        if time.monotonic() - self._result_stamp > OBSERVATION_TTL_S:
            return None
        return cached

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
    def resolve_detail(self, index: int,
                       result: GroundingResult | None = None) -> dict:
        """Where the element is NOW, and how that answer was obtained.

        The point is in MOUSE UNITS — the units a click is expressed in.

        `source` says where the answer came from, and it is the honest
        answer, not a decoration:

          "live"   the element was measured again, just now, against the
                   tree that is there now. This is the answer the
                   docstring has always promised.
          "cached" the rectangle from the observation was used, because
                   the element could not be measured again. The reason
                   says why not.
          "none"   the index is unknown, or the element has no position.

        The observation may be up to a second and a half old. A window
        that moved in that time is clicked in the wrong place, so the
        rectangle is read again at the moment of the action whenever the
        element can be found again.
        """
        detail: dict[str, Any] = {"point": None, "source": "none",
                                   "reason": "", "rect": None}
        result = result or (self._result[1] if self._result else None)
        if result is None:
            detail["reason"] = "there is no observation to act on"
            return detail
        mark = result.mark(int(index))
        if mark is None:
            detail["reason"] = f"there is no element numbered {index}"
            return detail
        if not mark.bbox:
            detail["reason"] = "this element has no position to act on"
            return detail

        # The live measurement.
        rect, reason = uia.live_rect(mark)
        if rect is not None and self._same_shape(rect, mark.bbox):
            point = self._live_point(rect)
            if point is not None:
                detail["point"] = point
                detail["source"] = "live"
                detail["reason"] = reason
                detail["rect"] = [int(v) for v in rect]
                return detail
            reason = "the live rectangle could not be turned into a point"
        elif rect is not None:
            reason = ("the element measured now is not the shape it was "
                      "when it was observed")

        # The rectangle from the observation, used because the element
        # could not be measured again. This is said out loud: a point
        # from a remembered rectangle and a point measured now are not
        # the same evidence, and the caller is told which one it got.
        x, y, w, h = result.geometry.capture_to_logical(mark.bbox)
        detail["point"] = (x + w // 2, y + h // 2)
        detail["source"] = "cached"
        detail["reason"] = reason
        return detail

    def _same_shape(self, rect: tuple[int, int, int, int],
                    bbox: tuple[int, int, int, int]) -> bool:
        """Is the live rectangle still the same thing?

        The capture is the monitor's own pixel grid, so a size in capture
        pixels IS a size in physical pixels. A rectangle that is suddenly
        three times bigger is not this element any more — it is some
        ancestor of it — and clicking its middle would be a guess.
        """
        try:
            live_w = int(rect[2]) - int(rect[0])
            live_h = int(rect[3]) - int(rect[1])
            seen_w = int(bbox[2])
            seen_h = int(bbox[3])
        except Exception:
            return False
        if seen_w <= 0 or seen_h <= 0:
            return True
        return not (live_w > seen_w * 3 or live_h > seen_h * 3)

    def _live_point(self, rect: tuple[int, int, int, int]
                    ) -> tuple[int, int] | None:
        """The middle of a live rectangle, in mouse units.

        The scale is measured now, not remembered: the display may have
        changed scale between the observation and the action.
        """
        try:
            left, top, right, bottom = (int(v) for v in rect)
        except Exception:
            return None
        if right <= left or bottom <= top:
            return None
        scale = 1.0
        try:
            from core import screenview
            scale = float(screenview.physical_to_logical() or 1.0)
        except Exception:
            scale = 1.0
        cx = (left + right) / 2.0
        cy = (top + bottom) / 2.0
        return int(round(cx * scale)), int(round(cy * scale))

    def resolve(self, index: int, result: GroundingResult | None = None
                ) -> tuple[int, int] | None:
        """The point to click, in mouse units — computed NOW.

        None when the index is unknown or the element has no position.

        `resolve_detail` says HOW the point was found; this returns only
        the point, for the callers that only need the point.
        """
        return self.resolve_detail(index, result).get("point")

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

        detail = self.resolve_detail(index, result)
        point = detail.get("point")
        if point is None:
            return {"status": "failed", "verified": False,
                    "reason": detail.get("reason")
                    or "this element has no position to act on"}
        return self._act_at(point, mark, action, text, detail)

    def _act_at(self, point: tuple[int, int], mark: ElementMark,
                action: str, text: str, detail: dict | None = None
                ) -> dict:
        """The coordinate route, for the elements that have no selector.

        The point in `detail` was measured live when it could be, and
        taken from the observation when it could not. Which one it was
        travels back with the outcome, because the caller has to be able
        to say that the click was placed on a rectangle that is a second
        and a half old.
        """
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

        detail = detail or {}
        return {
            "status": "done" if act.ok else "failed",
            "verified": bool(act.ok),
            "reason": act.detail or act.error or str(act),
            "origin": "point",
            "point": list(point),
            "point_source": detail.get("source", "cached"),
            "point_reason": detail.get("reason", ""),
        }

    # -- state --------------------------------------------------------------
    @property
    def last_reasons(self) -> list[str]:
        """Why each level was passed over, for the log."""
        return list(self._last_reasons)

    @property
    def last_result(self) -> GroundingResult | None:
        return self._result[1] if self._result else None

    @property
    def capture_digest(self) -> str:
        """A fingerprint of the last picture, for the log.

        It is computed HERE, when somebody asks for it, and not when the
        picture is taken: the request path takes the picture and uses it,
        and nothing compares two pictures.
        """
        if self._capture is None:
            return ""
        stamp, image, geometry, digest = self._capture
        if not digest:
            digest = _digest(image)
            self._capture = (stamp, image, geometry, digest)
        return digest

    def invalidate(self) -> None:
        """Forget the cached observation. The picture goes with it."""
        self._capture = None
        self._result = None
        self._result_stamp = 0.0
        self._pending = None


def _digest(image: Any) -> str:
    """A cheap fingerprint of the picture.

    The picture is reduced to a small greyscale copy and hashed. It does not
    have to detect every change — it has to detect the changes that matter,
    and it has to be fast enough to run on every single step. A change
    smaller than the reduction is a change nobody is going to act on.

    The reduction happens on the small copy rather than on the whole
    picture: turning a full-size picture grey costs about twenty
    milliseconds, and a fingerprint nobody asked for is twenty
    milliseconds of somebody's time for nothing.
    """
    try:
        reduced = image.resize((64, 36)).convert("L")
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
