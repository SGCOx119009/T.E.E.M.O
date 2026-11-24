"""Central confirmation policy for high-impact tool actions."""
from __future__ import annotations

from collections.abc import Callable
import contextvars
import json
import re
from contextlib import contextmanager

from core import confirm, outcome, tool_audit

_CONFIRMATION_RISKS = {"high", "external"}
_CONFIRMED_TOOL: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "confirmed_tool_dispatch", default=None
)
_AUTHORIZED_PLAN: contextvars.ContextVar[frozenset[str] | None] = contextvars.ContextVar(
    "authorized_task_plan", default=None
)
_HANDLER_OWN_GATE: dict[str, set[str]] = {
    # These handlers ask in their own words, with a better preview than the
    # generic one, so the executor lets them do it.
    #
    # The list is SHORT on purpose, and every entry in it is checked by
    # `tests/test_policy_coverage.py`: a delegation to a handler that does
    # NOT ask is a hole in the gate, and a hole is how "delete the file"
    # ended up running with nobody seeing it. Only four handlers in this
    # product have a gate of their own:
    #   * `computer_settings` — for the three it cannot take back (restart,
    #     shutdown, toggle_wifi), with a warning that says why;
    #   * `meet_control` — because the preview of a call is the recipient
    #     and the message, and only the handler knows them;
    #   * `game_updater` — because the preview is the game, the platform and
    #     whether the machine will be shut down afterwards;
    #   * `dev_agent` — because the preview is the PLAN: which files will be
    #     written, what each one is for, and that nothing will be run or
    #     installed.
    "computer_settings": {"restart", "shutdown", "toggle_wifi"},
    "meet_control": {"create", "new"},
    "game_updater": {"install", "update", "schedule"},
    "dev_agent": {"build", "improve"},
}
_SENSITIVE_NAME_RE = re.compile(
    r"(?:password|passwd|secret|token|api[_-]?key|authorization|credential|private[_-]?key|otp)",
    re.IGNORECASE,
)
_SENSITIVE_TARGET_KEYS = {
    "automation_id", "control_name", "control_type", "label", "name",
    "placeholder", "selector", "target", "window_title",
}
_SENSITIVE_VALUE_KEYS = {"content", "text", "value"}
_MAX_ARGUMENT_BYTES = 256_000
_MAX_ARGUMENT_DEPTH = 32
_MAX_ARGUMENT_ITEMS = 1_000
_MAX_ARGUMENT_STRING = 100_000


def _has_sensitive_target(value) -> bool:
    if not isinstance(value, dict):
        return False
    for key, item in value.items():
        if str(key).casefold() not in _SENSITIVE_TARGET_KEYS:
            continue
        if isinstance(item, dict):
            if _has_sensitive_target(item):
                return True
        elif is_sensitive_name(item):
            return True
    return False


def is_sensitive_name(value: object) -> bool:
    """Whether a field name or selector identifies sensitive data."""
    return isinstance(value, str) and bool(_SENSITIVE_NAME_RE.search(value))


def _confirmation_value(value, *, selector_sensitive: bool = False):
    if selector_sensitive:
        return "[REDACTED]"
    if isinstance(value, dict):
        is_form = value.get("action") == "fill_form"
        sensitive_target = _has_sensitive_target(value)
        result = {}
        for key, item in value.items():
            if is_sensitive_name(str(key)):
                result[key] = "[REDACTED]"
            elif sensitive_target and str(key).casefold() in _SENSITIVE_VALUE_KEYS:
                result[key] = "[REDACTED]"
            elif is_form and key == "fields" and isinstance(item, dict):
                result[key] = {
                    selector: _confirmation_value(
                        field_value,
                        selector_sensitive=bool(
                            is_sensitive_name(str(selector))
                        ),
                    )
                    for selector, field_value in item.items()
                }
            else:
                result[key] = _confirmation_value(item)
        return result
    if isinstance(value, list):
        return [_confirmation_value(item) for item in value]
    return value


def _validate_argument_tree(value, depth: int = 0) -> None:
    if depth > _MAX_ARGUMENT_DEPTH:
        raise ValueError("tool arguments are nested too deeply")
    if isinstance(value, str):
        if len(value) > _MAX_ARGUMENT_STRING:
            raise ValueError("a tool argument exceeds the text size limit")
    elif isinstance(value, dict):
        if len(value) > _MAX_ARGUMENT_ITEMS:
            raise ValueError("a tool argument object has too many entries")
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 1_000:
                raise ValueError("tool argument keys must be short text")
            _validate_argument_tree(item, depth + 1)
    elif isinstance(value, list):
        if len(value) > _MAX_ARGUMENT_ITEMS:
            raise ValueError("a tool argument list has too many entries")
        for item in value:
            _validate_argument_tree(item, depth + 1)
    elif value is not None and not isinstance(value, (bool, int, float)):
        raise ValueError("tool arguments must contain JSON-compatible values")


def validate_arguments(arguments: object) -> str | None:
    """Validate shared JSON argument limits before any tool handler runs."""
    if not isinstance(arguments, dict):
        return "tool arguments must be an object"
    try:
        _validate_argument_tree(arguments)
    except (TypeError, ValueError, RecursionError) as error:
        if isinstance(error, ValueError):
            return str(error)
        return "tool arguments must contain bounded JSON-compatible values"
    try:
        serialized = json.dumps(
            arguments, ensure_ascii=False, allow_nan=False
        )
    except (TypeError, ValueError, RecursionError):
        return "tool arguments must contain bounded JSON-compatible values"
    try:
        serialized_size = len(serialized.encode("utf-8"))
    except UnicodeEncodeError:
        return "tool arguments contain invalid text"
    if serialized_size > _MAX_ARGUMENT_BYTES:
        return "tool arguments exceed the supported size limit"
    return None


def requires_confirmation(tool: str, action: str,
                         arguments: dict | None = None) -> bool:
    """Whether the central risk policy requires explicit HUD approval.

    The answer comes from the FINE class of the action — the one declared
    per action in `core/policy.py` — and not from the coarse vocabulary of
    the audit, which is too blunt: it calls "write a file" and "delete a
    file" the same thing, because it only knows four words.

    The four classes that ask are EXTERNAL, IRREVERSIBLE, CREDENTIAL and
    PRIVACY. READ does not ask (it changes nothing), and neither do
    WRITE_LOCAL and REVERSIBLE_SYS, which are silent, recorded and
    reversible: the permission for them comes from the read-only mode and
    from the concessions, not from a banner on every keystroke.
    """
    from core import policy as _policy

    if not _policy.class_is_known(tool, action):
        # Nothing is known about this action: it is not that it is
        # harmless, it is that nobody has looked at it. The gate fails
        # closed rather than waving it through.
        return False
    klass = _policy.risk_class_of(tool, action, arguments)
    entry = _policy.RISK_CATALOG.get(klass)
    if entry is not None:
        return entry.needs_confirmation
    params = dict(arguments) if isinstance(arguments, dict) else {}
    params["action"] = action
    return tool_audit.classify_risk(tool, params) in _CONFIRMATION_RISKS


def request_confirmation(
    tool: str,
    action: str,
    title: str,
    detail: str,
    run: Callable[[], str],
    arguments: dict | None = None,
    voice_handler: Callable[[str], confirm.VoiceDecision] | None = None,
) -> str:
    """Queue an action only when the central policy classifies it as sensitive.

    This function intentionally fails closed if a caller attempts to use the
    confirmation gate for an action not marked high-impact.
    """
    if not requires_confirmation(tool, action, arguments):
        return outcome.failed(
            "action_policy",
            "the confirmation gate was requested for an action not classified "
            "as high-impact; nothing was executed",
            tool=tool,
            action=action,
        )
    if voice_handler is None and (
        _CONFIRMED_TOOL.get() == tool
        or _plan_authorizes(tool, arguments or {})
        or _is_granted(tool, action)
    ):
        return run()
    if voice_handler is None:
        return confirm.request(
            key=f"{tool}-{action}",
            title=title,
            detail=detail,
            run=run,
        )
    return confirm.request(
        key=f"{tool}-{action}",
        title=title,
        detail=detail,
        run=run,
        voice_handler=voice_handler,
    )


def run_or_confirm(
    tool: str,
    action: str,
    title: str,
    detail: str,
    run: Callable[[], str],
    arguments: dict | None = None,
) -> str:
    """Run routine actions directly and gate only high-impact actions."""
    if not requires_confirmation(tool, action, arguments):
        return run()
    return request_confirmation(
        tool=tool,
        action=action,
        title=title,
        detail=detail,
        run=run,
        arguments=arguments,
    )


def is_confirmed(tool: str) -> bool:
    """Whether this dispatch is running inside its HUD-approved callback."""
    return _CONFIRMED_TOOL.get() == tool


def _is_granted(tool: str, action: str = "") -> bool:
    """Whether the user has already allowed this tool in the panel."""
    try:
        from core import policy as _policy
        return bool(_policy.is_granted(tool, action))
    except Exception:
        return False


def ask_once(
    tool: str,
    action: str,
    title: str,
    detail: str,
    run: Callable[[], str],
    arguments: dict | None = None,
) -> str:
    """Ask the user, unless the user has already said yes.

    The handlers that know the preview better than the executor does — the
    three that cannot be taken back, the call that shows the recipient and
    the message, the game update that shows the game and the platform —
    ask through here. So a concession granted in the permissions panel means
    the same thing whoever asks: the user is not asked twice for the same
    permission, in two different words.
    """
    if _CONFIRMED_TOOL.get() == tool or _is_granted(tool, action):
        return run()
    return confirm.request(
        key=f"{tool}-{action}", title=title, detail=detail, run=run,
    )


def _request_signature(tool: str, arguments: dict) -> str:
    return json.dumps(
        [tool, arguments],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _plan_authorizes(tool: str, arguments: dict) -> bool:
    authorized = _AUTHORIZED_PLAN.get()
    if authorized is None:
        return False
    try:
        return _request_signature(tool, arguments) in authorized
    except (TypeError, ValueError):
        return False


@contextmanager
def authorize_plan(steps: list[tuple[str, dict]]):
    """Authorize only the exact tool calls shown in an approved plan."""
    signatures = frozenset(_request_signature(tool, args) for tool, args in steps)
    token = _AUTHORIZED_PLAN.set(signatures)
    try:
        yield
    finally:
        _AUTHORIZED_PLAN.reset(token)


def dispatch(
    tool: str,
    arguments: dict,
    run: Callable[[], str],
) -> str:
    """Enforce a dispatcher-level confirmation for unreviewed sensitive tools.

    Migrated handlers keep their exact, action-specific preview. All other
    high-impact handlers and discovered plugins are gated here with the full
    serialized arguments; a handler-level gate under this authorization runs
    only after the dispatcher confirmation has already been accepted.

    THIS IS THE SINGLE POINT OF APPLICATION. The policy of the FASE 9 is
    consulted here and nowhere else: not in the prompt, not in the planner,
    not in the description of the tool. A tool that describes itself as
    harmless is harmless; a tool that is resolved here is what it is.
    """
    if not isinstance(arguments, dict):
        return outcome.failed(
            "action_policy",
            "tool arguments must be an object; nothing was executed",
            tool=tool,
        )
    if not callable(run):
        return outcome.failed(
            "action_policy",
            "tool handler is not callable; nothing was executed",
            tool=tool,
        )
    validation_error = validate_arguments(arguments)
    if validation_error:
        return outcome.failed(
            "action_policy",
            f"{validation_error}; nothing was executed",
            tool=tool,
        )

    default_actions = {
        "game_updater": "update",
        "reminder": "schedule",
        "task_planner": "preview",
        "dev_agent": "build",
    }
    action_value = arguments.get(
        "mode" if tool in ("task_planner", "dev_agent") else "action",
        default_actions.get(tool, "execute"),
    )
    action = action_value.strip().lower() if isinstance(action_value, str) else "execute"
    risk = tool_audit.classify_risk(tool, arguments)

    from core import policy as _policy

    if risk == "unclassified" and not _policy.is_reviewed(tool):
        return outcome.failed(
            "action_policy",
            "the tool action has no reviewed risk classification; nothing was executed",
            tool=tool,
            action=action,
        )
    authorized_plan = _AUTHORIZED_PLAN.get()
    if authorized_plan is not None:
        try:
            exact = _request_signature(tool, arguments)
        except (TypeError, ValueError):
            exact = ""
        if exact in authorized_plan:
            # A plan the user APPROVED is an approval, not a request: it is
            # the user's own decision about these exact calls, and it is not
            # withdrawn by the read-only mode.
            return run()

    # THE POLICY, CONSULTED AT THE ONE POINT EVERY RESOLVED ACTION PASSES.
    #
    # The verdict says two things: may this happen, and does the user have to
    # see it first. A refusal is never silent and never generic — it says
    # which tool was stopped and what to allow, because "permission denied"
    # is a message that leaves the user with nothing to do.
    verdict = _policy.evaluate(tool, action, arguments)
    if not verdict.allowed:
        return outcome.failed(
            "action_policy",
            verdict.reason,
            tool=tool,
            action=action,
            risk_class=verdict.risk_class,
        )
    if not verdict.needs_confirmation:
        return run()
    if action in _HANDLER_OWN_GATE.get(tool, set()):
        # The handler asks in its own words, with a better preview. The class
        # of the action has already been checked above.
        return run()

    try:
        serialized = json.dumps(
            _policy.mask_arguments(arguments),
            ensure_ascii=False,
            sort_keys=True,
        )
    except (TypeError, ValueError):
        return outcome.failed(
            "action_policy",
            "arguments could not be safely shown for confirmation; nothing was executed",
            tool=tool,
        )
    detail = f"Tool: {tool}\nAction: {action}\nArguments:\n{serialized}"
    if len(detail) > confirm.MAX_DETAIL_LENGTH:
        return outcome.failed(
            "action_policy",
            "the complete arguments do not fit in the confirmation panel; "
            "nothing was executed",
            tool=tool,
            action=action,
        )

    def run_after_approval() -> str:
        token = _CONFIRMED_TOOL.set(tool)
        try:
            return run()
        finally:
            _CONFIRMED_TOOL.reset(token)

    return request_confirmation(
        tool,
        action,
        f"Allow {tool} to perform this action?",
        detail,
        run_after_approval,
        arguments=arguments,
    )
