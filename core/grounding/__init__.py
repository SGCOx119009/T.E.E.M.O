"""
core/grounding/ — the assistant stops clicking pixels.

WHAT THIS PACKAGE IS
    Grounding turns "there is something on the screen" into "it is number
    seventeen", so the model can choose a NAME instead of guessing a
    coordinate. It is the answer to the oldest failure of a screen agent:
    a click that lands on the wrong control and looks exactly like one that
    landed on the right one.

THE LAYERS
    `base`     the contract: what a mark is, what an observation is, and
               the stable text format the model reads.
    `uia`      level N0 — the accessibility tree. Free, exact, preferred.
    `som`      level N1 — numbered boxes drawn over a picture.
    `detect`   level N2 — a model that looks at the picture. Off by default.
    `resolver` the orchestrator: which level, what to cache, and when the
               coordinates are finally computed.

THE TWO RULES
    The model never receives absolute screen coordinates. It receives
    indices.
    The coordinates are computed at execution time, from live measurements,
    never from what the model was shown.
"""
from __future__ import annotations

from core.grounding.base import (
    LEVEL_SOM,
    LEVEL_UIA,
    LEVEL_VLM,
    LEVELS,
    SOM_FORMAT_VERSION,
    ElementMark,
    Geometry,
    GroundingError,
    GroundingResult,
    format_mark,
    format_observation,
    observation_is_model_safe,
)
from core.grounding.resolver import GroundingResolver, session_resolver
from core.grounding import uia as _uia


def _warm_in_background() -> None:
    """Load the accessibility machinery while nothing is waiting for it.

    The first UI Automation query of a session pays for importing
    pywinauto and for generating the COM type library — about 270
    milliseconds measured on this machine. That is the cost of the
    SESSION, and the session starts long before the user asks the
    assistant to look at anything, so it is paid here, in the background,
    instead of in front of the first question.

    Nothing is asked of the desktop here: only modules are imported.

    The warm-up itself lives in `core.grounding.uia`, which is where the
    request path waits for it — so there is ONE thread doing this work,
    not two, and no request repeats it.
    """
    try:
        _uia.warm()
    except Exception:
        pass


_warm_in_background()

__all__ = [
    "ElementMark",
    "Geometry",
    "GroundingError",
    "GroundingResolver",
    "GroundingResult",
    "LEVELS",
    "LEVEL_SOM",
    "LEVEL_UIA",
    "LEVEL_VLM",
    "SOM_FORMAT_VERSION",
    "format_mark",
    "format_observation",
    "observation_is_model_safe",
    "session_resolver",
]
