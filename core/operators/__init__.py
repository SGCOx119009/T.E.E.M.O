"""
core/operators/__init__.py — the registry that ties the operators together.

WHAT THE REGISTRY IS FOR
    The model used to see one flat list of tools: every action and every
    plugin, side by side, with nothing to say which belong together. The
    operator registry is the layer that gives the list a shape. It knows
    which operator each tool belongs to, which context each operator serves,
    and how risky each tool is — and it can offer the model ONE operator's
    tools instead of all of them.

THE ACTIVE CONTEXT IS WHAT THE MODEL SEES
    `declarations()` with no context returns exactly what the app has always
    returned, in the same order, byte for byte. That is deliberate: a change
    of shape that also changed the contract would be a different product, not
    a refactor.

    With a context, only the tools of the operators in that context are
    offered. The measurement behind this is in the plan: the model was being
    handed forty-five tools worth fifty-six thousand characters, and the
    budget experiments show the cost of that. Narrowing by context is the
    same idea, applied to the Live path.

THE RISK IS REDERIVED, NEVER TAKEN ON TRUST
    A model can invent a label. It can put `"risk": "read"` next to a tool
    that deletes files, and a system that believed the label would delete
    files. So the risk of a tool call is derived HERE, from the tool that was
    actually resolved and the arguments that were actually sent, by
    `core.tool_audit.classify_risk` — the same function the audit already
    uses. Nothing the model says about risk is ever read.
"""
from __future__ import annotations

from typing import Callable, Iterable, Optional

from core.operators.base import (
    ALLOWED_ACTIONS,
    ActionRejected,
    ActionSpec,
    Operator,
    OperatorAction,
    ToolInfo,
    parse_action,
    spec_from_declaration,
    worst_risk,
)
from core.operators.browser import BrowserOperator
from core.operators.desktop import DesktopOperator
from core.operators.files import FilesOperator
from core.operators.media import MediaOperator
from core.operators.system import SystemOperator
from core.operators.vision import VisionOperator

__all__ = [
    "ALLOWED_ACTIONS",
    "ActionRejected",
    "ActionSpec",
    "BrowserOperator",
    "CONTEXT_ALL",
    "DEFAULT_OPERATORS",
    "DesktopOperator",
    "FilesOperator",
    "MediaOperator",
    "Operator",
    "OperatorAction",
    "OperatorRegistry",
    "SystemOperator",
    "ToolInfo",
    "VisionOperator",
    "parse_action",
]

# The context that means "everything". It is a name rather than a None so it
# can be stored, logged and shown to the user.
CONTEXT_ALL = "all"

DEFAULT_OPERATORS: tuple[type[Operator], ...] = (
    DesktopOperator,
    BrowserOperator,
    FilesOperator,
    SystemOperator,
    VisionOperator,
    MediaOperator,
)


class OperatorRegistry:
    """The operators, the tools they own, and the context that is active."""

    def __init__(self, operators: Iterable[Operator], tools: dict[str, ToolInfo], *,
                 declarations: Optional[list[dict]] = None,
                 action_registry=None,
                 plugin_registry=None,
                 logger: Callable[[str], None] = print):
        self._operators: list[Operator] = list(operators)
        self._tools: dict[str, ToolInfo] = dict(tools)
        # The declarations in the order the app has always sent them.
        self._declarations: list[dict] = list(declarations or [])
        self._action_registry = action_registry
        self._plugin_registry = plugin_registry
        self._logger = logger
        self._context: str = CONTEXT_ALL
        self._specs: dict[str, ActionSpec] = {}
        self._owner: dict[str, Operator] = {}
        self._build_specs()

    # ── construction ────────────────────────────────────────────────────────

    @classmethod
    def from_registries(cls, action_registry=None, plugin_registry=None,
                        inline_declarations: Iterable[dict] = (),
                        logger: Callable[[str], None] = print,
                        operators: Optional[Iterable[type[Operator]]] = None) -> "OperatorRegistry":
        """Build the registry from what was discovered.

        The three sources — the inline tools, the bundled actions and the
        plugins — are flattened into one map of tools and one ordered list of
        declarations. The declarations keep the order they had before, so
        with no context active the model sees precisely what it saw before.
        """
        tools: dict[str, ToolInfo] = {}
        declarations: list[dict] = []

        for decl in inline_declarations:
            if not isinstance(decl, dict):
                continue
            name = decl.get("name")
            if not isinstance(name, str) or not name:
                continue
            tools[name] = ToolInfo(
                name=name,
                description=decl.get("description", "") or "",
                parameters=decl.get("parameters", {}) or {},
                origin="inline",
                declaration=decl,
            )
            declarations.append(decl)

        if action_registry is not None:
            for name, rec in action_registry.entries().items():
                decl = {"name": rec.name, "description": rec.description,
                        "parameters": rec.parameters}
                if rec.behavior:
                    decl["behavior"] = rec.behavior
                if rec.tier:
                    decl["tier"] = rec.tier
                if rec.brief:
                    decl["brief"] = rec.brief
                tools[name] = ToolInfo(
                    name=name,
                    description=rec.description,
                    parameters=rec.parameters,
                    origin="action",
                    declaration=decl,
                )
                declarations.append(decl)

        if plugin_registry is not None:
            for name, rec in plugin_registry.entries().items():
                decl = {"name": rec.name, "description": rec.description,
                        "parameters": rec.parameters}
                if rec.behavior:
                    decl["behavior"] = rec.behavior
                if rec.tier:
                    decl["tier"] = rec.tier
                if rec.brief:
                    decl["brief"] = rec.brief
                tools[name] = ToolInfo(
                    name=name,
                    description=rec.description,
                    parameters=rec.parameters,
                    origin="plugin",
                    declaration=decl,
                )
                declarations.append(decl)

        instances = [op() for op in (operators or DEFAULT_OPERATORS)]
        return cls(instances, tools, declarations=declarations,
                   action_registry=action_registry, plugin_registry=plugin_registry,
                   logger=logger)

    def _build_specs(self) -> None:
        """The typed spec of every tool, and who owns it."""
        self._specs.clear()
        self._owner.clear()
        for operator in self._operators:
            for spec in operator.actions(self._tools):
                self._specs[spec.name] = spec
                self._owner[spec.name] = operator

    # ── what the model is offered ───────────────────────────────────────────

    def declarations(self, context: Optional[str] = None) -> list[dict]:
        """The tool declarations for a context.

        With no context — or with "all" — this is exactly the list the app
        has always sent: same tools, same order, same schemas. With a context,
        only the tools of the operators that serve it.
        """
        wanted = self._resolve_context(context)
        if wanted is None:
            return list(self._declarations)
        out: list[dict] = []
        for operator in self._operators:
            if operator.context != wanted:
                continue
            for tool in operator.tools:
                info = self._tools.get(tool)
                if info is None:
                    continue
                out.append(info.declaration)
        return out

    def _resolve_context(self, context: Optional[str]) -> Optional[str]:
        if context is None:
            context = self._context
        if not context or context == CONTEXT_ALL:
            return None
        return str(context).strip().lower()

    @property
    def active_context(self) -> str:
        return self._context

    def set_context(self, context: Optional[str]) -> str:
        """Choose the context the model is offered. Returns the one in force.

        An unknown context is refused and the previous one kept: a typo in a
        setting must not leave the model with no tools at all.
        """
        if context is None or str(context).strip().lower() in ("", CONTEXT_ALL):
            self._context = CONTEXT_ALL
            return self._context
        wanted = str(context).strip().lower()
        if wanted not in self.contexts():
            self._logger(f"[Operators] unknown context '{wanted}' — keeping '{self._context}'")
            return self._context
        self._context = wanted
        return self._context

    def contexts(self) -> list[str]:
        """Every context an operator serves, plus "all"."""
        found = {op.context for op in self._operators}
        return [CONTEXT_ALL] + sorted(found)

    def operators(self) -> list[Operator]:
        return list(self._operators)

    def operator_for(self, tool_name: str) -> Optional[Operator]:
        """The operator that owns a tool, if any."""
        return self._owner.get(tool_name)

    def specs(self) -> list[ActionSpec]:
        """Every typed spec, in operator order."""
        return [spec for op in self._operators for spec in op.actions(self._tools)]

    def spec_for(self, tool_name: str) -> Optional[ActionSpec]:
        return self._specs.get(tool_name)

    # ── risk ────────────────────────────────────────────────────────────────

    def risk_for(self, tool_name: str, parameters: Optional[dict] = None) -> str:
        """The risk of a call, DERIVED from the tool that was resolved.

        Never from anything the model said. `core.tool_audit.classify_risk`
        is the function the audit already uses, so there is one policy and
        not two.
        """
        try:
            from core import tool_audit
            return tool_audit.classify_risk(tool_name, parameters or {})
        except Exception:
            return "unclassified"

    def declared_risk(self, tool_name: str) -> str:
        """The risk recorded in the tool's spec.

        It is the most severe risk among the tool's own actions, computed
        from the tool's schema — again, from the tool and never from the
        model. When the tool has no policy of its own, the operator's
        standing class is used, which is the coarser but honest answer.
        """
        spec = self._specs.get(tool_name)
        if spec is None:
            return "unclassified"
        actions = spec.actions
        if actions:
            risks = [self.risk_for(spec.name, {"action": action}) for action in actions]
            derived = worst_risk(risks)
            if derived != "unclassified":
                return derived
        operator = self._owner.get(tool_name)
        return operator.risk_class if operator else spec.risk_class

    # ── dispatch ────────────────────────────────────────────────────────────

    def has(self, name: str) -> bool:
        return name in self._tools

    def scheduling(self, name: str) -> Optional[str]:
        """How this tool's result should re-enter the conversation, if it said."""
        if self._action_registry is not None:
            value = self._action_registry.scheduling(name)
            if value:
                return value
        if self._plugin_registry is not None:
            return self._plugin_registry.scheduling(name)
        return None

    def run(self, name: str, parameters: dict, ctx: Optional[dict] = None) -> str:
        """Run a tool by name. One place, so the policy is one place.

        The bundled actions and the plugins are dispatched through their own
        registries — they keep their own guarantees, including that a failure
        never raises and never leaks what the exception said. What this adds
        is that there is now ONE place where a tool name turns into a call,
        which is what the policy needs.
        """
        ctx = ctx or {}
        if self._action_registry is not None and self._action_registry.has(name):
            return self._action_registry.run(name, parameters, ctx)
        if self._plugin_registry is not None and self._plugin_registry.has(name):
            return self._plugin_registry.run(
                name, parameters,
                player=ctx.get("player"),
                session_memory=ctx.get("session_memory"),
            )
        return f"Unknown tool: {name}"

    # ── the closed schema ───────────────────────────────────────────────────

    def parse(self, payload, tool: Optional[str] = None) -> OperatorAction:
        """Validate what the model said against the closed vocabulary.

        With a tool name, the arguments are checked against that tool's
        schema as well: an argument the tool does not declare, or a missing
        required one, is rejected.
        """
        spec = self._specs.get(tool) if tool else None
        return parse_action(payload, spec=spec)

    def tool_names(self) -> list[str]:
        return list(self._tools.keys())
