"""
core/grounding/som.py — grounding level N1: Set-of-Mark.

WHAT THIS LEVEL IS FOR
    Some windows expose nothing. A game, a PDF viewer, a canvas application
    and anything that paints its own widgets have no accessibility tree, so
    the level above has nothing to say about them. For those surfaces the
    assistant has to LOOK.

    The Set-of-Mark idea is that a picture alone is hard for a model to
    reason about — it has to estimate where a button is and then guess the
    coordinates, which is where small models fail. So the picture is
    ANNOTATED: every element gets a numbered box drawn on it, and the model
    is shown both the numbered picture and the list. It answers with a
    number, the same as everywhere else.

THE DETECTOR IS DELIBERATELY WEAK
    The full detector this technique comes from needs a graphics card with
    more memory than this machine has. What is here instead is a CPU-only
    edge-and-contour pass: it finds rectangles, which is what interface
    chrome mostly is. It finds fewer things and costs nothing.

    It is a fallback, not a competitor. When the accessibility tree has an
    answer, that answer is better and this level never runs.

NAMES COME FROM WHERE THEY CAN BE READ
    A box with no name is still a target — "the third one" is enough to
    press something — but it is a poor one. So the elements read by the OCR
    backend, when there is one, are merged in and the picture's words end
    up next to the picture's boxes.
"""
from __future__ import annotations

import io
import time
from typing import Any, Iterable, Sequence

from core.grounding.base import (
    LEVEL_SOM,
    ElementMark,
    Geometry,
    GroundingResult,
    format_observation,
)

# The colours of the annotation. Bright, because they have to survive a JPEG
# and a screen full of dark UI: a box that cannot be seen is a box that does
# not exist.
_MARK_COLOUR = (255, 64, 0)
_MARK_COLOUR_ALT = (0, 170, 255)
_MARK_WIDTH = 2


def available() -> tuple[bool, str]:
    """Can this level run? ((True, "") when it can.)"""
    try:
        import PIL.Image  # noqa: F401
    except Exception:
        return False, "Pillow is not installed, so nothing can be drawn"
    return True, ""


def detector_available() -> tuple[bool, str]:
    """Is there a detector that can find boxes without a GPU?"""
    try:
        import cv2  # noqa: F401
    except Exception:
        return False, "OpenCV is not installed, so no boxes can be detected"
    return True, ""


# ─────────────────────────────────────────────────────────────────────────────
#  Detection
# ─────────────────────────────────────────────────────────────────────────────
def detect_boxes(image: Any, *, max_boxes: int = 60,
                 min_area_ratio: float = 0.0008,
                 max_area_ratio: float = 0.30) -> list[tuple[int, int, int, int]]:
    """Rectangular regions of a picture, as (x, y, w, h) in capture pixels.

    A contour pass: edges, close the gaps, take the bounding rectangles.
    It is a weak detector and it is honest about being one — it finds
    rectangles, not buttons, and it cannot tell one button from another.

    Never raises. No detector is a normal state, not an error.
    """
    try:
        import cv2
        import numpy as np
    except Exception:
        return []

    try:
        array = np.array(image.convert("RGB"))
        height, width = array.shape[:2]
        if width < 8 or height < 8:
            return []

        gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, 60, 180)
        kernel = np.ones((3, 3), np.uint8)
        closed = cv2.dilate(edges, kernel, iterations=1)
        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)

        total = float(width * height)
        found: list[tuple[int, int, int, int]] = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if w < 8 or h < 8:
                continue
            ratio = (w * h) / total if total else 0.0
            if ratio < min_area_ratio or ratio > max_area_ratio:
                continue
            if w / float(h) > 24 or h / float(w) > 24:
                continue
            found.append((int(x), int(y), int(w), int(h)))

        return _suppress(found, max_boxes)
    except Exception:
        return []


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1 = max(ax, bx)
    y1 = max(ay, by)
    x2 = min(ax + aw, bx + bw)
    y2 = min(ay + ah, by + bh)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    union = aw * ah + bw * bh - inter
    return inter / float(union) if union else 0.0


def _suppress(boxes: list[tuple[int, int, int, int]],
              limit: int) -> list[tuple[int, int, int, int]]:
    """Drop the boxes that are inside other boxes, and cap the count.

    Without this, a single button with a border produces three overlapping
    rectangles and the model is shown the same thing three times with
    three different numbers.
    """
    ordered = sorted(boxes, key=lambda b: b[2] * b[3], reverse=True)
    kept: list[tuple[int, int, int, int]] = []
    for box in ordered:
        if len(kept) >= limit:
            break
        if any(_iou(box, other) > 0.6 for other in kept):
            continue
        kept.append(box)
    kept.sort(key=lambda b: (b[1], b[0]))
    return kept


# ─────────────────────────────────────────────────────────────────────────────
#  Drawing
# ─────────────────────────────────────────────────────────────────────────────
def annotate(image: Any, marks: Sequence[ElementMark]) -> Any:
    """Draw the numbered boxes on a copy of the picture.

    The copy matters: the caller keeps the original for anything that needs
    to look at pixels, and only the model ever sees the marked one.
    """
    ok, _ = available()
    if not ok:
        return image
    try:
        from PIL import ImageDraw
    except Exception:
        return image

    try:
        marked = image.copy()
        draw = ImageDraw.Draw(marked)
        for mark in marks:
            if not mark.bbox:
                continue
            x, y, w, h = mark.bbox
            colour = _MARK_COLOUR_ALT if mark.own else _MARK_COLOUR
            draw.rectangle([x, y, x + w, y + h], outline=colour,
                           width=_MARK_WIDTH)
            label = str(mark.index)
            try:
                draw.text((x + 2, max(0, y - 12)), label, fill=colour)
            except Exception:
                pass
        return marked
    except Exception:
        return image


def to_png(image: Any) -> bytes:
    """The picture as PNG bytes, or b"" when it cannot be encoded."""
    try:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    except Exception:
        return b""


# ─────────────────────────────────────────────────────────────────────────────
#  Observation
# ─────────────────────────────────────────────────────────────────────────────
def observe(image: Any, *, geometry: Geometry | None = None,
            elements: Iterable[Any] = (),
            boxes: Sequence[tuple[int, int, int, int]] | None = None,
            max_elements: int = 60) -> GroundingResult:
    """Build a Set-of-Mark observation from a picture.

    `elements` are the elements another backend read — the OCR backend
    hands them over, and their text becomes the name of the box they are
    in. `boxes` are the rectangles a detector found; when they are not
    given, they are detected here.

    The picture that comes back is ANNOTATED. The caller sends that one to
    the model and keeps the original.
    """
    started = time.monotonic()
    geometry = geometry or Geometry()
    ok, why = available()
    if not ok:
        return GroundingResult(
            level=LEVEL_SOM, status="unavailable", reason=why,
            geometry=geometry, duration_ms=(time.monotonic() - started) * 1000.0,
        )

    marks: list[ElementMark] = []
    index = 0

    # Elements read elsewhere come first: they have names, and a named
    # element is worth more than an unnamed box at the same place.
    for element in elements:
        if index >= max_elements:
            break
        try:
            rect = (int(element.left), int(element.top),
                    int(element.width), int(element.height))
            name = str(element.name or element.value or "")
            role = str(element.role or "text")
        except Exception:
            continue
        index += 1
        marks.append(ElementMark(
            index=index,
            role=role,
            name=name,
            bbox=geometry.logical_to_capture(rect),
            source="som",
            enabled=bool(getattr(element, "enabled", True)),
            focused=bool(getattr(element, "focused", False)),
            protected=bool(getattr(element, "protected", False)),
        ))

    found = list(boxes) if boxes is not None else detect_boxes(image)
    for x, y, w, h in found:
        if index >= max_elements:
            break
        if any(_iou((x, y, w, h), m.bbox) > 0.6 for m in marks if m.bbox):
            continue
        index += 1
        marks.append(ElementMark(
            index=index,
            role="region",
            name="",
            bbox=(int(x), int(y), int(w), int(h)),
            source="som",
            enabled=True,
        ))

    marked = annotate(image, marks)
    duration = (time.monotonic() - started) * 1000.0

    if not marks:
        return GroundingResult(
            level=LEVEL_SOM, status="unavailable",
            reason="nothing could be found in the picture",
            geometry=geometry, duration_ms=duration, source="som",
        )

    return GroundingResult(
        level=LEVEL_SOM,
        status="degraded",
        marks=tuple(marks),
        observation=format_observation(
            marks, level=LEVEL_SOM, source="som", geometry=geometry
        ),
        geometry=geometry,
        duration_ms=duration,
        source="som",
        degraded=True,
    )
