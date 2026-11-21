"""
Plugin discovery, validation, collision detection, and dispatch.

Discovery runs once (TeemoLive.__init__ calls discover_plugins()); the resulting
PluginRegistry is cached for the process lifetime. Enable/disable state is re-read
from config on every call to get_tool_declarations() / run() / list_for_ui(), so
toggling a plugin does not require restarting the app or re-importing anything.

THIS FILE IS A FACADE
    The discovery engine now lives in ``core/tool_loader.py``, shared with the
    action loader: an action and a plugin are the same kind of thing with a
    different address, and the two copies of that machinery had drifted apart.
    Everything public here is unchanged — the same names, the same signatures,
    the same behaviour — so the rest of the app does not know that the engine
    moved.

    One thing DID change, and it was a leak: the message of an exception
    raised while importing somebody's file used to be written into the log.
    That message can contain a path, a token or a query string, and the log
    is a thing people paste into bug reports. Only the exception TYPE is kept
    now, which is the same rule the dispatch path already followed.
"""
from __future__ import annotations

import inspect
import re
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from core import outcome
from core import tool_loader

_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")
_DEFAULT_PARAMS = {"type": "OBJECT", "properties": {}}

# Optional, and the same contract actions use: a plugin that takes a moment can
# say so, and the model carries on talking instead of waiting on it. See
# core/action_loader.py for what each value means.
_BEHAVIORS = ("BLOCKING", "NON_BLOCKING")
_SCHEDULING = ("WHEN_IDLE", "SILENT", "INTERRUPT")


def __getattr__(name: str):
    """Expose the config manager's helpers under the names they had here.

    They used to be imported at module level. Importing them eagerly pulls in
    the credential store — hashlib, OpenSSL, the Windows Vault — and a plugin
    registry has no business paying that before anybody has decided to use a
    plugin. A module-level __getattr__ keeps the names importable, and
    patchable, without the cost.
    """
    if name in ("get_plugin_enabled", "get_plugin_config"):
        from memory import config_manager
        return getattr(config_manager, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _plugin_enabled(name: str) -> bool:
    """Whether the plugin is switched on.

    The lookup goes through THIS module on purpose: it is what keeps
    `patch("core.plugin_loader.get_plugin_enabled")` working, which is how
    the plugin registry has always been tested.
    """
    return getattr(sys.modules[__name__], "get_plugin_enabled")(name)


def _plugin_config(namespace: str) -> dict:
    """The stored configuration of one settings namespace."""
    return getattr(sys.modules[__name__], "get_plugin_config")(namespace)


# Kept importable from here: both loaders used to define them, and other code
# may still ask for one of them by name.
_opt_upper = tool_loader.opt_upper
_opt_tier = tool_loader.opt_tier
_opt_text = tool_loader.opt_text


@dataclass
class PluginRecord:
    name: str
    description: str = ""
    parameters: dict = field(default_factory=lambda: dict(_DEFAULT_PARAMS))
    run: Optional[Callable] = None
    file: str = ""
    valid: bool = False
    error: str = ""
    settings: Optional[dict] = None   # optional PLUGIN_SETTINGS schema (config fields)
    behavior: Optional[str] = None    # None = the API's default (blocking)
    scheduling: Optional[str] = None  # None = the API's default (WHEN_IDLE)
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
        """The plugin's run(), importing the module first if it has not been.

        Returns None when the module cannot be loaded; the reason is left in
        `error`. Never raises.
        """
        if self.run is not None:
            return self.run
        if not self.module_path:
            return None
        try:
            module = tool_loader.load_module(Path(self.module_path), self.module_name)
        except Exception as exc:
            self.error = tool_loader.safe_error(exc)
            return None
        self.run = getattr(module, "run", None)
        if self.run is None:
            self.error = "the plugin has no callable run() function"
        else:
            # The settings schema lives in the module, so it can only be read
            # once the module has been imported. On the lazy path it arrives
            # here, the first time the plugin is used.
            schema = getattr(module, "PLUGIN_SETTINGS", None)
            if isinstance(schema, dict) and isinstance(schema.get("fields"), list):
                self.settings = schema
        return self.run


class PluginRegistry:
    def __init__(self, plugins: dict[str, PluginRecord], logger: Callable[[str], None],
                 notify: Callable[[str], None] | None = None):
        self._plugins = plugins          # name -> PluginRecord, VALID entries only
        self._all_records: list[PluginRecord] = []   # valid + invalid, for UI listing
        self._logger = logger
        # Where user-facing notices go. `logger` is the console transcript and
        # carries everything; `notify` reaches the activity log, so only things
        # the user has to know about are sent to it. Defaults to dropping them,
        # which keeps every existing single-sink caller working unchanged.
        self._notify = notify or (lambda _msg: None)

    # -- called by main.py at LiveConnectConfig build time --
    def get_tool_declarations(self) -> list[dict]:
        decls = []
        for name, rec in self._plugins.items():
            if _plugin_enabled(name):
                decl = {
                    "name": rec.name,
                    "description": rec.description,
                    "parameters": rec.parameters,
                }
                if rec.behavior:
                    decl["behavior"] = rec.behavior
                # Budget metadata. NOT part of any API's schema — main.py calls
                # providers.schema.strip_meta() before the Gemini config is
                # built, and budget() consumes them on the local-engine path.
                if rec.tier:
                    decl["tier"] = rec.tier
                if rec.brief:
                    decl["brief"] = rec.brief
                decls.append(decl)
        return decls

    def has(self, name: str) -> bool:
        return name in self._plugins

    def scheduling(self, name: str) -> Optional[str]:
        """How this plugin's result should re-enter the conversation, if it said."""
        rec = self._plugins.get(name)
        return rec.scheduling if rec else None

    def names(self) -> set[str]:
        return set(self._plugins.keys())

    def entries(self) -> dict[str, PluginRecord]:
        """The valid records, by name — for the operator layer above."""
        return dict(self._plugins)

    def records(self) -> list[PluginRecord]:
        """Every record, valid or not — for the UI and for the tests."""
        return list(self._all_records)

    def validate_pending(self) -> list[str]:
        """Import every plugin that has not been imported yet.

        The mirror of ActionRegistry.validate_pending, and the reason a lazy
        plugin is still safe: a file that explodes on import is noticed HERE
        rather than when the model happens to call it. The plugin is marked
        invalid, so it stops being offered, and its name is returned for the
        log.

        The exception's message is not repeated: it can carry a path or a
        token, and this ends up in a log people paste into bug reports.
        """
        failed: list[str] = []
        for name, rec in list(self._plugins.items()):
            if rec.run is not None or not rec.module_path:
                continue
            run = rec.resolve()
            if run is None:
                rec.valid = False
                failed.append(f"{rec.file} — {rec.error or 'could not be loaded'}")
                self._plugins.pop(name, None)
        return failed

    # -- called by main.py from _execute_tool's else branch --
    def run(self, name: str, parameters: dict, player=None, session_memory=None) -> str:
        rec = self._plugins.get(name)
        if rec is None or not rec.valid:
            return outcome.failed(
                "plugin_loader",
                "the requested plugin is not available",
                tool=name,
            )
        if not isinstance(parameters, dict):
            return outcome.failed(
                "plugin_loader",
                "plugin parameters must be an object",
                tool=name,
            )
        if not _plugin_enabled(name):
            return outcome.failed(
                "plugin_loader",
                "the plugin is disabled",
                tool=name,
            )
        try:
            run_fn = rec.run if rec.run is not None else rec.resolve()
            if run_fn is None:
                # The module could not be loaded. Its own message is not
                # repeated here: it may carry a path or a token.
                self._logger(f"Plugin '{name}' could not be loaded.")
                self._notify(f"Plugin '{name}' could not be loaded.")
                return outcome.failed(
                    "plugin_loader",
                    "the plugin could not be loaded",
                    tool=name,
                )
            _began = time.monotonic()
            result = _call_run(run_fn, parameters, player, session_memory)
            normalized = outcome.normalize_dispatch_result(name, result)
            _record_audit(name, parameters, normalized, _began)
            return normalized
        except Exception as e:
            # Only the exception TYPE is shown: its message can carry a path,
            # a token or a query string that must not reach the model.
            error_type = type(e).__name__
            self._logger(f"Plugin '{name}' failed ({error_type}).")
            self._notify(f"Plugin '{name}' failed ({error_type}).")
            return outcome.failed(
                "plugin_loader",
                "the plugin raised an exception",
                tool=name,
                error=error_type,
            )

    # -- called by ui.py's settings tab to render per-plugin config forms --
    def settings_schemas(self) -> list[dict]:
        """One entry per settings SECTION, for enabled plugins that declare a
        PLUGIN_SETTINGS schema. Sections are deduped by namespace so a suite of
        plugins sharing one namespace (e.g. the printer trio) shows a single
        form. Current stored values are merged in so the UI can pre-fill fields.
        """
        seen: set[str] = set()
        out: list[dict] = []
        for name, rec in self._plugins.items():
            if not rec.settings:
                # The schema lives in the module. On the lazy path it has not
                # been read yet, and the settings form is exactly the thing
                # that needs it — so the module is imported here, on demand.
                if rec.run is None and rec.module_path:
                    rec.resolve()
                if not rec.settings or not _plugin_enabled(name):
                    continue
            if not _plugin_enabled(name):
                continue
            ns = rec.settings.get("namespace") or rec.name
            if ns in seen:
                continue
            seen.add(ns)
            out.append({
                "plugin":    rec.name,
                "namespace": ns,
                "title":     rec.settings.get("title") or rec.name,
                "fields":    rec.settings.get("fields", []),
                "values":    _plugin_config(ns),
                "action":    rec.settings.get("action"),   # optional test/connect button
            })
        return out

    # -- called by ui.py's Plugin Manager overlay --
    def list_for_ui(self) -> list[dict]:
        out = []
        for rec in self._all_records:
            out.append({
                "name": rec.name,
                "description": rec.description,
                "file": rec.file,
                "valid": rec.valid,
                "error": rec.error,
                "enabled": _plugin_enabled(rec.name) if rec.valid else False,
            })
        return out


def _record_audit(name: str, parameters: dict, result: str,
                  began: float) -> None:
    """Put this call in the audit stream, with its latency.

    The same reason as in core/action_loader.py: a plugin is a tool, and the
    Event Stream is supposed to show what the assistant did and how long it
    took. Metadata only - never the arguments and never the result, which
    for a plugin can be a message to somebody.
    """
    try:
        from core import tool_audit
        tool_audit.record(
            name, result,
            (time.monotonic() - began) * 1000.0,
            parameters,
        )
    except Exception as exc:
        print(f"[PluginLoader] audit record failed: {type(exc).__name__}")


def _call_run(run_fn, parameters, player, session_memory):
    """Invoke run() passing only the kwargs it actually declares (or all of them
    if it has **kwargs), so a minimal `def run(parameters):` plugin still works."""
    sig = inspect.signature(run_fn)
    has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    kwargs = {}
    if has_var_kw or "player" in sig.parameters:
        kwargs["player"] = player
    if has_var_kw or "session_memory" in sig.parameters:
        kwargs["session_memory"] = session_memory
    return run_fn(parameters, **kwargs)


def _validate(module, filename: str) -> PluginRecord:
    """Returns a PluginRecord; .valid=False + .error set on any problem. Never raises."""
    plugin_meta = getattr(module, "PLUGIN", None)
    record = tool_loader.validate_declaration(
        plugin_meta if isinstance(plugin_meta, dict) else {},
        filename,
        tool_loader.ORIGIN_PLUGIN,
        run=getattr(module, "run", None),
        settings=getattr(module, "PLUGIN_SETTINGS", None),
    )
    if not isinstance(plugin_meta, dict):
        record.error = "Missing PLUGIN dict constant."
    return PluginRecord(
        name=record.name,
        description=record.description,
        parameters=record.parameters,
        run=record.run,
        file=record.file,
        valid=record.valid,
        error=record.error,
        settings=record.settings,
        behavior=record.behavior,
        scheduling=record.scheduling,
        tier=record.tier,
        brief=record.brief,
    )


# The app's own top-level packages. Named explicitly rather than only probed on
# disk, because the case that matters most is the folder being ABSENT: a plugin
# importing core.undo on a build with no core/ at all must still be told to
# update, not to install something called "core".
_FIRST_PARTY = {"core", "memory", "actions", "plugins", "dashboard", "config"}


def _is_first_party(top: str, plugins_dir: Path) -> bool:
    """True when `top` names part of this app rather than a third-party package.

    The disk probe is the second half so a package added to the project later is
    recognised without anyone remembering to edit the set above.
    """
    if top in _FIRST_PARTY:
        return True
    root = plugins_dir.parent
    try:
        return (root / top).is_dir() or (root / f"{top}.py").is_file()
    except OSError:
        return False


def _load_error(path: Path, plugins_dir: Path, exc: Exception) -> str:
    """Turn an import failure into something the person who downloaded the file
    can act on.

    Plugins are shared one file at a time, but some of them sit on a helper —
    anything named with a leading underscore, which this loader deliberately
    skips so it is never treated as a plugin of its own. Download the plugin
    without its helper and Python reports `No module named 'plugins._x'`, which
    is accurate and tells a non-programmer nothing. Naming the missing file, and
    saying it belongs next to this one, turns a support question into a
    thirty-second fix. Nothing here is specific to any plugin: the helper's name
    comes from the exception itself.

    The one thing this function must NOT do is repeat the exception's message.
    It can contain a path, a token or a query string, and this string ends up
    in the log. The exception TYPE is enough.
    """
    if isinstance(exc, ModuleNotFoundError):
        missing = (getattr(exc, "name", "") or "").split(".")
        if len(missing) == 2 and missing[0] == "plugins" and missing[1].startswith("_"):
            helper = missing[1] + ".py"
            return (f"Needs the shared file '{helper}', which is not in "
                    f"{plugins_dir.name}/. It comes with this plugin — download "
                    f"'{helper}' into the same folder as {path.name} and restart.")
        if missing and missing[0] not in ("plugins",):
            if _is_first_party(missing[0], plugins_dir):
                # 'pip install core' is not merely unhelpful — it is dangerous
                # advice. These names belong to this project, and telling
                # somebody to install a same-named stranger from an index is a
                # supply-chain hazard dressed up as a fix. A plugin written
                # against a newer release reaches this line, and the honest
                # answer is that the app is behind the plugin, not that a
                # package is absent.
                needed = "/".join(missing) + ".py"
                return (f"Needs '{needed}', which is part of this app rather than "
                        f"something to install — your copy doesn't have it, so it "
                        f"comes with a newer version. Update the app; there is "
                        f"nothing to pip install.")
            return (f"Needs a package that is not installed: "
                    f"pip install {missing[0]}")
    return tool_loader.safe_error(exc)


def discover_plugins(plugins_dir: Path, core_tool_names: set[str],
                      logger: Callable[[str], None] = print,
                      notify: Callable[[str], None] | None = None,
                      eager: bool = True) -> PluginRegistry:
    """
    Scans plugins_dir for *.py files (skips files starting with '_', e.g. __init__.py,
    _template.py, and any shared-helper modules an author prefixes with '_').
    Import errors, validation errors, and name collisions are logged and the offending
    file is skipped — they NEVER raise out of this function and never abort the scan of
    remaining files.

    With ``eager=False`` the modules are not imported at all: the declaration is
    read out of the source and the module is imported when the plugin is first
    used, or by the background warm-up. See core/tool_loader.py for why that is
    worth having.
    """
    def _error(path: Path, exc: BaseException) -> str:
        return _load_error(path, plugins_dir, exc)

    valid, all_records = tool_loader.scan(
        plugins_dir,
        tool_loader.ORIGIN_PLUGIN,
        reserved_names=core_tool_names,
        logger=logger,
        eager=eager,
        module_prefix="plugins",
        load_error=_error,
    )

    plugins: dict[str, PluginRecord] = {}
    records: list[PluginRecord] = []
    for rec in list(valid.values()) + list(all_records):
        item = PluginRecord(
            name=rec.name,
            description=rec.description,
            parameters=rec.parameters,
            run=rec.run,
            file=rec.file,
            valid=rec.valid,
            error=rec.error,
            settings=rec.settings,
            behavior=rec.behavior,
            scheduling=rec.scheduling,
            tier=rec.tier,
            brief=rec.brief,
            module_name=rec.module_name,
            module_path=rec.module_path,
        )
        records.append(item)
        if rec.valid and rec.name not in plugins:
            plugins[rec.name] = item

    notify = notify or (lambda _msg: None)
    registry = PluginRegistry(plugins, logger, notify)
    registry._all_records = records
    rejected = len(records) - len(plugins)
    logger(f"Plugin discovery complete: {len(plugins)} active, "
           f"{rejected} rejected, {len(records)} total.")
    # The activity log is the user's conversation, not a boot transcript: a
    # plugin that loaded correctly is not news, so only a failure surfaces there
    # — and then as one line, because the per-plugin detail is on the console.
    if rejected:
        notify(f"{rejected} plugin(s) could not be loaded — see the console.")
    return registry
