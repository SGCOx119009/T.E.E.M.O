"""
Assistant action — look at the screen by NAME, and act on what you named.

WHY THIS EXISTS ALONGSIDE computer_control
    `computer_control` clicks coordinates. That is a guess: the assistant
    does not know what is on the screen, so it estimates where a button
    is, and an estimate that lands on the wrong control looks exactly like
    an estimate that landed on the right one.

    This tool does the other thing. It asks the operating system what is on
    the screen — through the accessibility tree, which is free and exact —
    and answers with a NUMBERED LIST:

        [17] button    "Enviar"           bbox=(790,495)-(895,527)  enabled
        [18] textbox   "Asunto"           bbox=(120,300)-(760,330)

    The model then refers to an element by its number, and the number is
    turned into a position at the moment the action happens — not at the
    moment the list was made, when the window may already have moved.

THE ORDER OF BUSINESS
    1. `observe` first, always, if you do not already have a list. Acting
       on an index you have not seen is acting on a guess.
    2. Then `click` / `type` / `focus` with the `index` you were given.

    The list is a snapshot. It is reused for about a second and a half and
    then it is thrown away, because the screen moves on.

WHAT THIS TOOL WILL NOT DO
    * Press one of the assistant's own controls. The assistant's window is
      not a target; that is how an agent ends up confirming its own
      dialogs.
    * Type into a password field. The platform marks those fields, and the
      mark is respected whatever the field is called.
    * Claim that something happened when it did not. Every action reports
      what was confirmed and what was not.
"""
from __future__ import annotations

from core import outcome
from core.grounding import detect, resolver as _resolver

# The action vocabulary. Closed, like every operator action in this
# project: a name outside this set is refused, not dispatched.
_ACTIONS = ("observe", "click", "type", "focus")

# How many elements a single observation may carry. Enough to name a
# window, not enough to drown a model.
_MAX_ELEMENTS = 120


def _observe(parameters: dict) -> str:
    """The numbered list of what is on the screen."""
    level = str((parameters or {}).get("level") or "auto").strip().lower()
    if level not in ("auto", "N0", "N1", "N2"):
        level = "auto"

    res = _resolver.session_resolver().observe(
        level=level, budget=detect.Budget()
    )

    if not res.marks:
        return outcome.failed(
            "screen_mark", res.reason or "nothing on the screen could be named",
            level=res.level,
        )

    return outcome.done(
        "screen_mark",
        level=res.level,
        elements=res.count,
        duration_ms=int(res.duration_ms),
        observation=res.observation,
    )


def _act(parameters: dict) -> str:
    """Click, type into, or focus the element with this index."""
    params = parameters or {}
    raw = params.get("index")
    try:
        index = int(raw)
    except (TypeError, ValueError):
        return outcome.failed("screen_mark", "no element index was given")
    if index < 1:
        return outcome.failed("screen_mark", "the element index must be 1 or more")

    action = str(params.get("action") or "click").strip().lower()
    text = str(params.get("text") or "")

    res = _resolver.session_resolver().last_result
    if res is None or res.mark(index) is None:
        res = _resolver.session_resolver().observe()
    if res is None or res.mark(index) is None:
        return outcome.failed(
            "screen_mark",
            f"there is no element numbered {index}; observe the screen first",
        )

    result = _resolver.session_resolver().act(index, action=action, text=text,
                                              result=res)
    status = str(result.get("status") or "failed")
    reason = str(result.get("reason") or "")

    if status == "done":
        return outcome.done(
            "screen_mark", index=index, action=action,
            origin=result.get("origin"), verified=True,
        )
    if status == "unverified":
        return outcome.unverified(
            "screen_mark", reason or "the action could not be confirmed",
            index=index, action=action,
        )
    return outcome.failed("screen_mark", reason or "the action did not happen",
                          index=index, action=action)


def screen_mark(parameters=None, response=None, player=None,
                session_memory=None) -> str:
    """Look at the screen by name and act on what you named."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action") or "observe").strip().lower()

    if action not in _ACTIONS:
        return outcome.failed(
            "screen_mark",
            f"'{action}' is not something this tool can do "
            f"(it can: {', '.join(_ACTIONS)})",
        )

    try:
        if action == "observe":
            return _observe(params)
        return _act(params)
    except Exception as exc:
        print(f"[screen_mark] {type(exc).__name__}: {exc}")
        return outcome.failed("screen_mark", f"{type(exc).__name__}: {exc}")


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "screen_mark",
    "description": (
        "Looks at the screen and names what is on it, then acts on the "
        "element you choose BY NUMBER. Use this INSTEAD of computer_control "
        "whenever the target is a named element on screen - a button, a "
        "field, a menu entry - because computer_control clicks coordinates "
        "and a coordinate is a guess. "
        "Call action='observe' FIRST: it returns a numbered list like "
        '[17] button "Enviar" enabled, and you then act with that number. '
        "Never invent an index: use one you have been shown. "
        "action='click' presses the element, action='type' writes 'text' "
        "into it, action='focus' only puts the keyboard on it. "
        "Read the status the tool returns: 'done' means it was confirmed, "
        "'unverified' means it was requested and could NOT be confirmed - "
        "say so rather than claiming success."
    ),
    "brief": (
        "Names what is on the screen and acts on it by number. Use action='observe' "
        "first, then action='click' with that index. Prefer this over "
        "computer_control for anything with a name on screen."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "observe | click | type | focus",
            },
            "index": {
                "type": "INTEGER",
                "description": (
                    "The number of the element, exactly as it appeared in the "
                    "observation. Only for click, type and focus."
                ),
            },
            "text": {
                "type": "STRING",
                "description": "The text to write. Only for action='type'.",
            },
            "level": {
                "type": "STRING",
                "description": (
                    "auto (default) | N0 | N1 | N2. Which way to look. 'auto' "
                    "is right almost always."
                ),
            },
        },
        "required": ["action"],
    },
    "handler": screen_mark,
}
