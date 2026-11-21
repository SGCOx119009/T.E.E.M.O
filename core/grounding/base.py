"""
core/grounding/base.py — the contract of the grounding layer.

WHAT THIS LAYER IS FOR
    The assistant used to act on the screen by clicking pixels. It could not
    know what was on the screen, so it guessed, and a guess that lands on the
    wrong control looks exactly like a guess that landed on the right one.
    Grounding replaces the guess with a NAME: the model is shown a numbered
    list of what is actually there, and it answers with a number.

THE ONE RULE THAT MATTERS
    The model never receives absolute screen coordinates. It receives INDICES.

    An index means nothing outside the observation that produced it. A
    coordinate means something on this screen, at this resolution, in this
    window position — and every one of those is allowed to change between the
    moment the model looks and the moment the assistant clicks. So the
    coordinates are kept on this side of the wall, and the model refers to
    elements the way a person does: "the seventeen one".

    The `bbox` in the observation is in CAPTURE space, not screen space, and
    the capture's own geometry is recorded next to it so the two can be
    mapped. That mapping happens at EXECUTION time, from live measurements,
    not from what the model was shown.

TWO LAYERS, AND WHICH IS WHICH
    A REASONING layer says what it wants: "I want to press the button called
    Send". A GROUNDING layer says where that is: index 17, and here is the
    rectangle. Neither decides for the other. That separation is the whole
    point of the phase: when a task fails, "the plan was wrong" and "the
    localisation was wrong" are two different bugs, and they can only be told
    apart if they are two different layers.

THE THREE LEVELS
    N0 — UI Automation. The accessibility tree: names, roles, rectangles. Exact,
    free, and the preferred level on Windows. See `core/grounding/uia.py`.
    N1 — Set-of-Mark. Numbered boxes drawn over a picture, for the surfaces
    that expose no tree at all. See `core/grounding/som.py`.
    N2 — a grounding model. The last resort, the only one that costs tokens,
    and the only one that can be talked into by what it reads. See
    `core/grounding/detect.py`.

THE FORMAT IS STABLE, AND THAT IS THE POINT
    The model is shown a list like this:

        [17] button    "Send"              bbox=(790,495)-(895,527)  enabled

    One element per line, the index first, the role, the quoted name, the
    rectangle in capture pixels, and the flags. It is stable because a model
    that has to re-learn the format every turn is a model that misparses it
    every turn. `SOM_FORMAT_VERSION` is the handle on it: when the format
    changes on purpose, this changes with it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

# Bumped when the observation format changes in a way a model could notice.
SOM_FORMAT_VERSION = "1"

# The grounding levels, in the order they are preferred.
LEVEL_UIA = "N0"
LEVEL_SOM = "N1"
LEVEL_VLM = "N2"
LEVELS: tuple[str, ...] = (LEVEL_UIA, LEVEL_SOM, LEVEL_VLM)


class GroundingError(RuntimeError):
    """Grounding could not produce an observation.

    Raised only for programming errors — a malformed argument, an unknown
    level. Everything that can happen on a real machine is reported IN the
    result, because a grounding layer that raises takes the turn with it.
    """


# ─────────────────────────────────────────────────────────────────────────────
#  The element
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ElementMark:
    """One thing on the screen, numbered.

    `index` is what the model refers to. `bbox` is in CAPTURE pixels — the
    pixels of the picture that was observed — and is `None` for a level that
    cannot say where the element is.

    `selector` is the structured way to act on this element WITHOUT
    coordinates at all: window title, control name, control type and
    automation id. It is opaque to the model and never rendered into the
    observation, because it is plumbing, not content.

    `own` marks an element that belongs to T.E.E.M.O. itself. Those are
    never actionable: the assistant's own buttons are not something it should
    be able to press, and a model that asks to press them gets a refusal.

    `pid` and `hwnd` are the element's OWNING process and its native window
    handle. They are carried because the question "is this ours?" cannot be
    answered without them: a control hosted by another process INSIDE one of
    our windows is still ours, and the only way to know that is to look at
    the window the control lives in, not at the process that drew it.
    """

    index: int
    role: str
    name: str
    bbox: tuple[int, int, int, int] | None = None      # (x, y, w, h) in capture px
    source: str = "uia"                                 # "uia" | "som" | "vlm"
    enabled: bool | None = None
    focused: bool = False
    protected: bool = False
    own: bool = False
    window: str = ""
    pid: int = 0
    hwnd: int = 0                                       # the element's own handle
    automation_id: str = ""
    control_type: str = ""                              # the RAW UIA type, e.g. "Button"
    selector: dict = field(default_factory=dict)
    score: float = 0.0

    @property
    def actionable(self) -> bool:
        """May the assistant act on this element at all?

        Three things say no: it belongs to the assistant itself, it is a
        secret field, and it is not enabled.
        """
        if self.own or self.protected:
            return False
        if self.enabled is False:
            return False
        return True

    @property
    def label(self) -> str:
        """What this element would be called out loud."""
        return self.name or self.automation_id or self.role

    def center_capture(self) -> tuple[int, int] | None:
        """The middle of the element, in capture pixels."""
        if not self.bbox:
            return None
        x, y, w, h = self.bbox
        return x + w // 2, y + h // 2

    def to_dict(self) -> dict[str, Any]:
        """A compact record — for logs and for the executor, never for a model."""
        d: dict[str, Any] = {"index": self.index, "role": self.role}
        if self.name:
            d["name"] = self.name
        if self.bbox:
            d["bbox"] = list(self.bbox)
        if self.source:
            d["source"] = self.source
        if self.enabled is False:
            d["disabled"] = True
        if self.focused:
            d["focused"] = True
        if self.protected:
            d["protected"] = True
        if self.own:
            d["own"] = True
        if self.window:
            d["window"] = self.window
        if self.automation_id:
            d["automation_id"] = self.automation_id
        return d


# ─────────────────────────────────────────────────────────────────────────────
#  Geometry — the DPI story, measured
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Geometry:
    """Where a capture came from, and what a pixel in it is worth.

    Three spaces, and confusing them is how a click lands on the wrong button
    on a 150% display:

      * CAPTURE pixels — the picture. What the observation shows.
      * SCREEN pixels  — the physical pixels of the virtual desktop.
      * LOGICAL units  — the mouse units a click is expressed in.

    `scale` is the multiplier that turns physical pixels into mouse units. It
    is MEASURED from the machine, never read out of a settings dialog: the
    second monitor is often set to a different scale than the first, and the
    only value that is always right is the one that came from a capture.
    """

    origin_screen: tuple[int, int] = (0, 0)     # top-left of the capture, screen px
    size_capture: tuple[int, int] = (0, 0)       # size of the picture, px
    size_logical: tuple[int, int] = (0, 0)       # size of the captured area, mouse units
    scale: float = 1.0                           # mouse units per physical pixel

    # -- capture -> screen -------------------------------------------------
    def capture_to_screen(self, rect: Sequence[int]) -> tuple[int, int, int, int]:
        """(x, y, w, h) in capture pixels -> (x, y, w, h) in screen pixels."""
        x, y, w, h = (int(v) for v in rect)
        ox, oy = self.origin_screen
        return x + ox, y + oy, w, h

    # -- capture -> logical ------------------------------------------------
    def capture_to_logical(self, rect: Sequence[int]) -> tuple[int, int, int, int]:
        """(x, y, w, h) in capture pixels -> (x, y, w, h) in mouse units."""
        sx, sy, sw, sh = self.capture_to_screen(rect)
        s = self.scale if self.scale else 1.0
        return (int(round(sx * s)), int(round(sy * s)),
                int(round(sw * s)), int(round(sh * s)))

    # -- logical -> capture ------------------------------------------------
    def logical_to_capture(self, rect: Sequence[int]) -> tuple[int, int, int, int]:
        """(x, y, w, h) in mouse units -> (x, y, w, h) in capture pixels."""
        x, y, w, h = (int(v) for v in rect)
        s = self.scale if self.scale else 1.0
        ox, oy = self.origin_screen
        return (int(round(x / s)) - ox, int(round(y / s)) - oy,
                int(round(w / s)), int(round(h / s)))

    @property
    def capture_size(self) -> tuple[int, int]:
        return self.size_capture

    def describe(self) -> str:
        """One line about where this observation came from.

        The capture's ORIGIN is deliberately not in it. The origin is a screen
        coordinate, and this string goes to a model; an element's rectangle
        in capture pixels plus the scale is enough to place it inside the
        picture, and that is all the model is allowed to know.
        """
        w, h = self.size_capture
        return f"capture={w}x{h}px scale={self.scale:.4f}"

    def describe_full(self) -> str:
        """The same line with the origin — for the log, never for a model."""
        w, h = self.size_capture
        ox, oy = self.origin_screen
        return (f"capture={w}x{h}px origin=({ox},{oy}) "
                f"scale={self.scale:.4f}")


# ─────────────────────────────────────────────────────────────────────────────
#  The result
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class GroundingResult:
    """One observation of the screen.

    `observation` is the text the model reads. It contains indices and
    capture-space rectangles. It never contains a screen coordinate, and it
    never contains anything that could be executed.

    `marks` is the same information, structured, for the executor. The two
    are built from the same marks on purpose: the model and the actuator must
    be looking at the same list, or "the seventeen one" means two things.
    """

    level: str = "none"
    status: str = "unavailable"       # "ok" | "degraded" | "unavailable"
    marks: tuple[ElementMark, ...] = ()
    observation: str = ""
    geometry: Geometry = field(default_factory=Geometry)
    duration_ms: float = 0.0
    reason: str = ""
    source: str = ""
    degraded: bool = False
    digest: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def count(self) -> int:
        return len(self.marks)

    def mark(self, index: int) -> ElementMark | None:
        """The mark with this index, or None. The only way in."""
        for m in self.marks:
            if m.index == index:
                return m
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "status": self.status,
            "source": self.source,
            "elements": self.count,
            "duration_ms": round(self.duration_ms, 1),
            "degraded": self.degraded,
            "reason": self.reason,
            "geometry": self.geometry.describe(),
            "observation": self.observation,
        }


# ─────────────────────────────────────────────────────────────────────────────
#  The observation format
# ─────────────────────────────────────────────────────────────────────────────
def _quote(text: str) -> str:
    """A name, quoted, with the characters that would break the line escaped."""
    out = str(text or "")
    out = out.replace("\\", "\\\\").replace('"', '\\"')
    out = out.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return f'"{out}"'


def format_mark(mark: ElementMark) -> str:
    """One element, one line, in the stable format.

        [17] button    "Send"              bbox=(790,495)-(895,527)  enabled

    The columns are padded so that a list of them reads as a table. The
    padding is cosmetic and the parser is not a parser — the model reads
    this, not a program — but a ragged list is harder to scan, and the whole
    point of the format is that it is easy to scan.
    """
    role = (mark.role or "other")[:16].ljust(10)
    name = _quote(mark.name or mark.automation_id or "")[:60].ljust(24)

    parts: list[str] = [f"[{mark.index}]", role, name]

    if mark.bbox:
        x, y, w, h = mark.bbox
        parts.append(f"bbox=({x},{y})-({x + w},{y + h})")

    flags: list[str] = []
    if mark.enabled is False:
        flags.append("disabled")
    elif mark.enabled is True:
        flags.append("enabled")
    if mark.focused:
        flags.append("focused")
    if mark.protected:
        flags.append("protected")
    if mark.own:
        flags.append("own")
    if flags:
        parts.append(" ".join(flags))

    return " ".join(parts)


def observation_header(level: str, source: str, geometry: Geometry,
                       count: int) -> str:
    """The line above the list: what this is and where it was taken."""
    return (f"# ground v{SOM_FORMAT_VERSION} level={level} source={source} "
            f"{geometry.describe()} elements={count}")


def format_observation(marks: Iterable[ElementMark], *, level: str = "N0",
                        source: str = "", geometry: Geometry | None = None,
                        header: bool = True) -> str:
    """The whole thing the model reads.

    Built ONLY from capture-space values. There is no code path from here to
    a screen coordinate, which is what makes "the model never receives
    absolute coordinates" a property of the code rather than a promise.
    """
    geometry = geometry or Geometry()
    items = list(marks)
    lines: list[str] = []
    if header:
        lines.append(observation_header(level, source or level, geometry, len(items)))
    lines.extend(format_mark(m) for m in items)
    return "\n".join(lines)


def observation_is_model_safe(result: GroundingResult) -> bool:
    """Does this observation leak a screen coordinate?

    A screen coordinate that is ALSO a capture coordinate is not a leak.
    On a single monitor, the capture of that monitor IS the screen, and
    the number in the text is the capture's own. What WOULD be a leak is a
    number that could only have come from the desktop's coordinate space —
    the absolute position of an element — because that is the number a
    model would use to click.

    So the check is this: for every mark, take its rectangle in screen
    space. If that rectangle differs from the capture rectangle AND the
    screen coordinates appear in the text, the observation is not safe.

    The check is not decorative. The capture space and the screen space are
    different by construction — the capture has an origin and a scale, and
    the screen does not — and this is what proves that nothing from the
    second ever reached the first.
    """
    text = result.observation or ""
    if not text:
        return True
    for mark in result.marks:
        if not mark.bbox:
            continue
        screen = result.geometry.capture_to_screen(mark.bbox)
        if tuple(screen) == tuple(mark.bbox):
            # The two spaces coincide on this machine. Whatever number is
            # in the text is the capture's, and no screen-only value was
            # added to it.
            continue
        sx, sy, sw, sh = screen
        for value in (sx, sy, sx + sw, sy + sh):
            if value and f"({value}," in text:
                return False
    return True
