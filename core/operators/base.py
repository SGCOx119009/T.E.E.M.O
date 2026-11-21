"""
core/operators/base.py — the contract every operator speaks.

THE ONE THING THIS FILE EXISTS TO PREVENT
    A model that can say "run this" can, one day, say "run THIS". The family of
    bugs that comes with that is not exotic: an assistant that evaluates the
    text a model produced, or that lets a model invent a tool name and has a
    lookup table for it, ends up one refactor away from running whatever the
    model felt like writing.

    The alternative is a CLOSED vocabulary. An action is a name in a fixed set,
    its arguments are checked against a schema, and anything outside is
    rejected with an error instead of being executed. There is no path from
    "the model said it" to "Python ran it".

    This project has no `eval` and no `exec` of model output, and there is a
    test that keeps it that way. This file is where the next layer of the same
    guarantee lives.

TWO LAYERS, AND WHICH IS WHICH
    A TOOL is what the model calls today: a named function with a JSON schema
    (`browser_control`, `open_app`). Its typed description is an `ActionSpec`.

    An OPERATOR ACTION is the primitive vocabulary underneath: click, type,
    key, scroll, wait, launch, read. This is the layer the grounded operators
    of the browser and vision work speak, where the model says "click this
    element" instead of naming a function and hoping. It is closed: the
    vocabulary is `ALLOWED_ACTIONS` and nothing else is ever executed.

    `OperatorAction` is the second layer, and it is a frozen dataclass on
    purpose: once parsed, it cannot be edited by whoever holds it.

COORDINATES ARE NORMALISED, NEVER ABSOLUTE
    A pixel coordinate is meaningless on another screen, at another DPI, after
    a window moves. So a coordinate in this contract is a fraction of the
    surface — 0.5 is the middle of the screen, whatever the screen is — and a
    value outside 0.0–1.0 is not "rescaled", it is REFUSED. Rescaling an
    absolute pixel position silently is how a click lands on the wrong button:
    it looks like it worked.

THE ARGS ARE ALSO KEPT, MASKED
    `raw_args` is what the model actually sent, with anything that looks like a
    credential replaced. It exists so the confirmation screen can show the user
    WHAT is about to be sent — and a screen that cannot show the payload is a
    screen that cannot ask for consent.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

# ── The closed vocabulary ───────────────────────────────────────────────────
# Every primitive an operator can execute. A name outside this set is
# rejected, not dispatched.
ALLOWED_ACTIONS: frozenset[str] = frozenset({
    # pointing
    "click", "double_click", "right_click", "move", "drag",
    # scrolling
    "scroll",
    # text
    "type", "key",
    # timing
    "wait",
    # windows and applications
    "launch", "focus", "close",
    # reading
    "read", "list", "search", "screenshot",
    # navigation
    "back", "forward", "stop", "reload",
    # files
    "upload", "download",
})

# Risk classes, from least to most severe. The order matters: a tool whose
# actions carry different risks is classified by the WORST of them, because a
# caller asking "how dangerous is this tool" needs the answer that covers
# every way of calling it.
#
# The labels are exactly the ones core/tool_audit.py emits — this list
# reorders them, it does not invent any. The order is by what the action
# DOES, not by how it is worded:
#   standard       — ordinary work
#   read_or_navigate — looks at something, changes nothing here
#   state_change   — changes something on this machine
#   external       — leaves this machine: mail sent, a message posted
#   high           — destructive, or irreversible
RISK_ORDER: tuple[str, ...] = (
    "unclassified", "standard", "read_or_navigate", "state_change", "external", "high",
)

# Argument names whose value must never be shown to the user or written to a
# log. The check is on the NAME, because a value can be anything: a password
# is a string, a token is a string, and the only thing that tells them apart
# is what the caller called them.
_SECRET_KEY_HINTS: tuple[str, ...] = (
    "password", "passwd", "secret", "token", "credential", "api_key", "apikey",
    "auth", "authorization", "otp", "pin", "cookie", "session_key",
)

# Argument names that hold a position. They are fractions of the surface.
_COORDINATE_KEYS: tuple[str, ...] = ("x", "y")


class ActionRejected(ValueError):
    """An action that is not in the vocabulary, or whose arguments do not fit.

    Raised, never logged and swallowed: the caller decides what the model is
    told, and "rejected" is information the caller needs.
    """


def mask_args(args: Any) -> dict:
    """A copy of `args` with anything that looks like a credential masked.

    The mask is a fixed marker rather than a truncation, so a reader can tell
    "there was something here" from "the value was empty".
    """
    if not isinstance(args, dict):
        return {}
    out: dict = {}
    for key, value in args.items():
        name = str(key).lower()
        if any(hint in name for hint in _SECRET_KEY_HINTS):
            out[key] = "***"
        elif isinstance(value, dict):
            out[key] = mask_args(value)
        else:
            out[key] = value
    return out


def normalise_coordinates(args: dict) -> dict:
    """Check that every coordinate is a fraction of the surface.

    A value outside 0.0–1.0 is refused rather than clamped: a clamped
    coordinate silently turns "2000 pixels to the right" into "the far right
    edge", which is a click on the wrong control that looks like it worked.
    """
    out = dict(args)
    for key in _COORDINATE_KEYS:
        if key not in out:
            continue
        value = out[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ActionRejected(f"the coordinate '{key}' must be a number")
        number = float(value)
        if number < 0.0 or number > 1.0:
            raise ActionRejected(
                f"the coordinate '{key}' must be normalised between 0.0 and 1.0 "
                f"(got {number}); absolute pixel coordinates are not accepted"
            )
        out[key] = number
    return out


@dataclass(frozen=True)
class OperatorAction:
    """Una acción cerrada y validable. Nunca código.

    Frozen because it is a decision, not a work-in-progress: once parsed, it
    is what gets executed, and nothing between the parse and the execution
    should be able to change what it says.
    """
    action: str          # "click" | "type" | "key" | "scroll" | "wait" | ...
    target: str = ""     # element_id del grounding, o "" si la acción no lo usa
    args: dict = field(default_factory=dict)
    raw_args: dict = field(default_factory=dict)   # para el log, enmascarado


@dataclass(frozen=True)
class ToolInfo:
    """What the registry knows about one tool, from wherever it came.

    The three sources — a bundled action, a plugin, and the handful of tools
    declared inline in main.py — are flattened into this one shape, so the
    operator layer never has to ask where a tool came from before it can
    describe it.
    """
    name: str
    description: str = ""
    parameters: dict = field(default_factory=dict)
    origin: str = "action"          # "action" | "plugin" | "inline"
    declaration: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ActionSpec:
    """The typed description of one tool.

    `parameters` is the schema the tool already declares, so the model keeps
    seeing exactly the contract it saw before. `required` and `defaults` are
    derived from it rather than written again, which means they cannot drift
    away from the declaration they describe.
    """
    name: str
    description: str = ""
    parameters: dict = field(default_factory=dict)
    required: tuple[str, ...] = ()
    defaults: dict = field(default_factory=dict)
    risk_class: str = "unclassified"
    context: str = "desktop"
    operator: str = ""
    origin: str = "action"

    @property
    def actions(self) -> list[str]:
        """The action values this tool accepts, when it has any."""
        props = self.parameters.get("properties") if isinstance(self.parameters, dict) else None
        if not isinstance(props, dict):
            return []
        for key in ("action", "mode"):
            prop = props.get(key)
            if isinstance(prop, dict):
                values = prop.get("enum")
                if isinstance(values, list):
                    return [str(v) for v in values]
        return []


def risk_rank(risk: str) -> int:
    """How severe a risk class is. Unknown classes sort as unclassified."""
    try:
        return RISK_ORDER.index(str(risk))
    except ValueError:
        return 0


def worst_risk(risks: Iterable[str]) -> str:
    """The most severe of several risk classes."""
    worst = "unclassified"
    for risk in risks:
        if risk_rank(risk) > risk_rank(worst):
            worst = risk
    return worst


def spec_from_declaration(name: str, description: str, parameters: dict, *,
                          risk_class: str = "unclassified",
                          context: str = "desktop",
                          operator: str = "",
                          origin: str = "action") -> ActionSpec:
    """Build an `ActionSpec` from a tool declaration.

    The required list and the defaults are read out of the schema itself, so
    a declaration and its spec cannot disagree: there is only one place where
    "what does this tool need" is written down.
    """
    props = parameters.get("properties") if isinstance(parameters, dict) else None
    props = props if isinstance(props, dict) else {}
    required = parameters.get("required") if isinstance(parameters, dict) else None
    required = tuple(str(r) for r in required) if isinstance(required, list) else ()
    defaults = {}
    for key, prop in props.items():
        if isinstance(prop, dict) and "default" in prop:
            defaults[key] = prop["default"]
    return ActionSpec(
        name=name,
        description=description,
        parameters=parameters if isinstance(parameters, dict) else {},
        required=required,
        defaults=defaults,
        risk_class=risk_class,
        context=context,
        operator=operator,
        origin=origin,
    )


def parse_action(payload: Any, *, spec: Optional[ActionSpec] = None,
                 allowed: frozenset[str] = ALLOWED_ACTIONS) -> OperatorAction:
    """Turn what the model said into a validated `OperatorAction`.

    Nothing here executes anything, and nothing here evaluates anything: the
    payload is a dict of values that are checked, copied and masked.

    REJECTS, rather than repairs:
        - a payload that is not an object
        - an action outside the closed vocabulary
        - an argument the tool's schema does not declare
        - a missing required argument
        - a coordinate that is not normalised

    The rejection message says what was wrong, because the model is the one
    that has to read it and try again.
    """
    if not isinstance(payload, dict):
        raise ActionRejected("an action must be an object")

    action = payload.get("action")
    if not isinstance(action, str) or not action.strip():
        raise ActionRejected("an action must name what to do")
    action = action.strip().lower()
    if action not in allowed:
        raise ActionRejected(
            f"'{action}' is not an action this operator can perform "
            f"(allowed: {', '.join(sorted(allowed))})"
        )

    target = payload.get("target", "")
    if target is None:
        target = ""
    if not isinstance(target, str):
        raise ActionRejected("the target of an action must be a string")

    raw = payload.get("args", {})
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ActionRejected("the arguments of an action must be an object")

    args = normalise_coordinates(raw)

    if spec is not None:
        props = spec.parameters.get("properties") if isinstance(spec.parameters, dict) else None
        props = props if isinstance(props, dict) else {}
        unknown = [k for k in args if k not in props]
        if unknown:
            raise ActionRejected(
                f"'{spec.name}' does not accept {', '.join(sorted(unknown))}"
            )
        missing = [k for k in spec.required if k not in args]
        if missing:
            raise ActionRejected(
                f"'{spec.name}' needs {', '.join(missing)}"
            )
        for key, value in spec.defaults.items():
            args.setdefault(key, value)

    return OperatorAction(
        action=action,
        target=target,
        args=args,
        raw_args=mask_args(raw),
    )


class Operator(ABC):
    """A domain the assistant can act on.

    An operator groups the tools of one domain and says what it needs: the
    context it belongs to, and how risky it is as a whole. It does NOT
    implement the tools — they stay where they are, with their own tests —
    because an operator is a way of SEEING the tools, not a rewrite of them.

    A subclass declares its metadata — `name`, `description`, `context`,
    `risk_class`, `tools` — and inherits the spec builder. That is the whole
    contract: the tools keep their own declarations, and the operator says
    which ones belong together.
    """

    name: str = ""
    description: str = ""
    #: The context this operator belongs to. The active context decides which
    #: operators' tools the model is offered.
    context: str = "desktop"
    #: How risky this operator is as a whole — the worst of its tools.
    risk_class: str = "standard"
    #: The tool names this operator owns.
    tools: tuple[str, ...] = ()

    def actions(self, tools: dict[str, ToolInfo]) -> list[ActionSpec]:
        """The typed specs of this operator's tools.

        `tools` maps a tool name to what is known about it, so the
        description and the schema come from the tool itself and cannot
        drift away from it. A tool that was not discovered — a plugin the
        user has not installed, an action that failed to load — still gets a
        spec, so the operator's shape does not change with the installation.
        """
        out: list[ActionSpec] = []
        for tool in self.tools:
            info = tools.get(tool)
            out.append(spec_from_declaration(
                tool,
                info.description if info else "",
                info.parameters if info else {},
                risk_class=self.risk_class,
                context=self.context,
                operator=self.name,
                origin=info.origin if info else "action",
            ))
        return out

    def owns(self, tool_name: str) -> bool:
        return tool_name in self.tools

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Operator {self.name} ({len(self.tools)} tools)>"
