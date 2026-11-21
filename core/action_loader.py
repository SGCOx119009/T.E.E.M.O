"""
Action discovery, validation, and dispatch — the built-in twin of plugin_loader.

Every actions/*.py that exposes a module-level ``TOOL`` dict is auto-discovered
here, exactly like a drop-in plugin, so main.py never has to hardcode a tool
declaration or a dispatch branch for it. Adding a new bundled action is then the
same one-file operation as writing a plugin: define ``TOOL`` and a handler.

``TOOL`` shape (see actions/open_app.py for a live example):

    TOOL = {
        "name":        "open_app",              # unique, ^[a-zA-Z_][a-zA-Z0-9_]{0,63}$
        "description":  "...",                   # what Gemini reads to route the call
        "parameters":  {"type": "OBJECT", ...}, # Gemini function-declaration schema
        "handler":      open_app,                # the callable to run
    }

The handler is invoked through signature introspection: it receives ``parameters``
plus whichever of ``player`` / ``speak`` / ``response`` / ``session_memory`` it
actually declares — so existing action signatures work unchanged.

THIS FILE IS A FACADE
    The discovery engine now lives in ``core/tool_loader.py``, shared with the
    plugin loader: an action and a plugin are the same kind of thing with a
    different address, and the two copies of that machinery had drifted apart.
    Everything public here is unchanged — the same names, the same signatures,
    the same behaviour — so the rest of the app does not know that the engine
    moved.

TWO WAYS TO DISCOVER
    ``eager=True`` (the default) imports every module now and binds its
    handler: everything is known, and a file that explodes is reported as
    rejected at discovery time.

    ``eager=False`` reads the declaration out of the source WITHOUT running
    the module, and imports it later, when the tool is first called. The
    bundled actions cost 961 ms to import and 127 ms to read this way, which
    is the difference between a slow start and a fast one.
"""
from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from core import outcome
from core import tool_loader

_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")
_DEFAULT_PARAMS = {"type": "OBJECT", "properties": {}}
_CTX_KEYS = ("player", "speak", "response", "session_memory")


# Keys this project puts on a declaration for its own bookkeeping. They live
# here because this file documents what a TOOL dict may contain, and both
# loaders produce them.
#
# THEY MUST NEVER REACH A VENDOR. Gemini validates function declarations
# against a fixed schema and an unknown field is an error, not something it
# ignores — so main.py calls strip_meta() before building the Live config.
#
# The definition is here rather than in providers/schema.py, which is the only
# other place that cares, because main.py must be able to strip them WITHOUT
# importing providers: a default install never loads that package, and the
# measured cost of deciding not to is the release's headline number.
META_KEYS = ("tier", "brief")


def strip_meta(decls):
    """Declarations without META_KEYS. Non-dict entries pass through untouched;
    a declaration carrying none is returned as the same object, so what an API
    receives is byte-for-byte what it received before these keys existed."""
    out = []
    for decl in decls:
        if isinstance(decl, dict) and any(k in decl for k in META_KEYS):
            out.append({k: v for k, v in decl.items() if k not in META_KEYS})
        else:
            out.append(decl)
    return out


# Kept importable from here: both loaders used to define them, and other code
# may still ask for one of them by name.
_opt_upper = tool_loader.opt_upper
_opt_tier = tool_loader.opt_tier
_opt_text = tool_loader.opt_text


@dataclass
class ActionRecord:
    name: str
    description: str = ""
    parameters: dict = field(default_factory=lambda: dict(_DEFAULT_PARAMS))
    handler: Optional[Callable] = None
    file: str = ""
    valid: bool = False
    error: str = ""
    behavior: Optional[str] = None     # None = the API's default (blocking)
    scheduling: Optional[str] = None   # None = the API's default (WHEN_IDLE)
    # Read by providers/schema.py when a small model gets a tool budget; never
    # sent to any API — main.py strips both before the Gemini config is built.
    #   tier   "core" | "extra" — this file's own opinion of whether it should
    #          survive a tight budget, instead of that being decided by the
    #          order files happen to be discovered in.
    #   brief  a short description used in place of the long one when a budget
    #          is in force. Nothing is deleted: Gemini still gets the full text.
    tier: Optional[str] = None
    brief: Optional[str] = None
    # Where the module lives when it has not been imported yet.
    module_name: str = ""
    module_path: str = ""

    def resolve(self) -> Optional[Callable]:
        """The handler, importing the module first if it has not been imported.

        Returns None when the module cannot be loaded; the reason is left in
        `error`. Never raises.
        """
        if self.handler is not None:
            return self.handler
        if not self.module_path:
            return None
        try:
            module = tool_loader.load_module(Path(self.module_path), self.module_name)
        except Exception as exc:
            self.error = tool_loader.safe_error(exc)
            return None
        decl = getattr(module, "TOOL", None)
        if isinstance(decl, dict):
            self.handler = decl.get("handler")
        if self.handler is None:
            self.error = "the tool has no callable handler"
        return self.handler


class ActionRegistry:
    def __init__(self, actions: dict[str, ActionRecord], logger: Callable[[str], None]):
        self._actions = actions          # name -> ActionRecord, VALID entries only
        self._all_records: list[ActionRecord] = []
        self._logger = logger

    # -- called by main.py at LiveConnectConfig build time --
    def get_tool_declarations(self) -> list[dict]:
        out = []
        for rec in self._actions.values():
            decl = {"name": rec.name, "description": rec.description,
                    "parameters": rec.parameters}
            if rec.behavior:
                decl["behavior"] = rec.behavior
            # Budget metadata. NOT part of any API's schema — main.py calls
            # providers.schema.strip_meta() before the Gemini config is built,
            # and budget() consumes them on the local-engine path.
            if rec.tier:
                decl["tier"] = rec.tier
            if rec.brief:
                decl["brief"] = rec.brief
            out.append(decl)
        return out

    def has(self, name: str) -> bool:
        return name in self._actions

    def scheduling(self, name: str) -> Optional[str]:
        """How this action's result should re-enter the conversation, if it said."""
        rec = self._actions.get(name)
        return rec.scheduling if rec else None

    def names(self) -> set[str]:
        return set(self._actions.keys())

    def entries(self) -> dict[str, ActionRecord]:
        """The valid records, by name — for the operator layer above."""
        return dict(self._actions)

    def records(self) -> list[ActionRecord]:
        """Every record, valid or not — for the UI and for the tests."""
        return list(self._all_records)

    def validate_pending(self) -> list[str]:
        """Import every module that has not been imported yet.

        Used by the AST-first path, where the declarations were read out of
        the source and no module has been run: a file that explodes on import
        is noticed HERE rather than when its tool is first called. The files
        that failed are marked invalid, so they stop being offered to the
        model, and their names are returned for the log.

        The exception's message is not repeated: it can carry a path or a
        token, and this ends up in a log people paste into bug reports.
        """
        failed: list[str] = []
        for name, rec in list(self._actions.items()):
            if rec.handler is not None or not rec.module_path:
                continue
            handler = rec.resolve()
            if handler is None:
                rec.valid = False
                failed.append(f"{rec.file} — {rec.error or 'could not be loaded'}")
                self._actions.pop(name, None)
        return failed

    # -- called by main.py from _execute_tool --
    def run(self, name: str, parameters: dict, ctx: dict | None = None) -> str:
        rec = self._actions.get(name)
        if rec is None or not rec.valid:
            return outcome.failed(
                "action_loader",
                "the requested action is not available",
                tool=name,
            )
        if not isinstance(parameters, dict):
            return outcome.failed(
                "action_loader",
                "action parameters must be an object",
                tool=name,
            )
        try:
            handler = rec.handler if rec.handler is not None else rec.resolve()
            if handler is None:
                # The module could not be loaded. Its own message is not
                # repeated here: it may carry a path or a token.
                self._logger(f"Action '{name}' could not be loaded.")
                return outcome.failed(
                    "action_loader",
                    "the action could not be loaded",
                    tool=name,
                )
            result = _call_handler(handler, parameters, ctx or {})
            return outcome.normalize_dispatch_result(name, result)
        except Exception as e:
            # The exception's own message may carry a secret (a path, a token,
            # a query string). Only its TYPE is logged and returned.
            error_type = type(e).__name__
            self._logger(f"Action '{name}' failed ({error_type}).")
            return outcome.failed(
                "action_loader",
                "the action raised an exception",
                tool=name,
                error=error_type,
            )


def _call_handler(fn: Callable, parameters: dict, ctx: dict) -> str:
    """Invoke the handler passing only the context kwargs it actually declares
    (or all of them if it has **kwargs), so each action's existing signature
    works unchanged."""
    sig = inspect.signature(fn)
    has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    kwargs = {}
    for key in _CTX_KEYS:
        if has_var_kw or key in sig.parameters:
            kwargs[key] = ctx.get(key)
    return fn(parameters=parameters, **kwargs)


def _validate(module, filename: str) -> ActionRecord:
    """Returns an ActionRecord; .valid=False + .error set on any problem. Never raises."""
    tool = getattr(module, "TOOL", None)
    record = tool_loader.validate_declaration(
        tool if isinstance(tool, dict) else {},
        filename,
        tool_loader.ORIGIN_ACTION,
        handler=(tool.get("handler") if isinstance(tool, dict) else None),
    )
    if not isinstance(tool, dict):
        record.error = "No module-level TOOL dict (not a discoverable action)."
    return ActionRecord(
        name=record.name,
        description=record.description,
        parameters=record.parameters,
        handler=record.handler,
        file=record.file,
        valid=record.valid,
        error=record.error,
        behavior=record.behavior,
        scheduling=record.scheduling,
        tier=record.tier,
        brief=record.brief,
    )


def discover_actions(actions_dir: Path, reserved_names: set[str] | None = None,
                     logger: Callable[[str], None] = print,
                     eager: bool = True) -> ActionRegistry:
    """
    Scans actions_dir for *.py files (skips files starting with '_'). A file is
    only treated as an action if it exposes a module-level TOOL dict; files
    without one (shared helpers, capture-only modules) are silently ignored.
    Import/validation errors and name collisions are logged and the file is
    skipped — they NEVER raise out of this function.

    With ``eager=False`` the modules are not imported at all: the declaration
    is read out of the source and the module is imported when the tool is
    first called. See core/tool_loader.py for why that is worth having.
    """
    valid, all_records = tool_loader.scan(
        actions_dir,
        tool_loader.ORIGIN_ACTION,
        reserved_names=reserved_names,
        logger=logger,
        eager=eager,
        module_prefix="actions",
    )

    actions: dict[str, ActionRecord] = {}
    records: list[ActionRecord] = []
    for rec in valid.values():
        actions[rec.name] = ActionRecord(
            name=rec.name,
            description=rec.description,
            parameters=rec.parameters,
            handler=rec.handler,
            file=rec.file,
            valid=rec.valid,
            error=rec.error,
            behavior=rec.behavior,
            scheduling=rec.scheduling,
            tier=rec.tier,
            brief=rec.brief,
            module_name=rec.module_name,
            module_path=rec.module_path,
        )
    for rec in all_records:
        records.append(ActionRecord(
            name=rec.name,
            description=rec.description,
            parameters=rec.parameters,
            handler=rec.handler,
            file=rec.file,
            valid=rec.valid,
            error=rec.error,
            behavior=rec.behavior,
            scheduling=rec.scheduling,
            tier=rec.tier,
            brief=rec.brief,
            module_name=rec.module_name,
            module_path=rec.module_path,
        ))

    registry = ActionRegistry(actions, logger)
    registry._all_records = records
    logger(f"Action discovery complete: {len(actions)} active.")
    return registry
