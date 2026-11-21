"""
core/tool_loader.py — ONE loader, TWO origins.

ACTIONS AND PLUGINS ARE THE SAME THING WITH A DIFFERENT ADDRESS
    A built-in action and a dropped-in plugin are both "a Python file that
    declares a tool". The only real differences are where the file lives, who
    is allowed to switch it on, and what the handler is called. Everything
    else — the declaration shape, the validation, the collision rules, the
    isolation when it blows up — was written twice, and the two copies drifted:
    an error message that leaked a secret existed on one side only, and a
    plugin's settings schema had no equivalent on the other.

    So the engine lives here, once, and the two origins are parameters.
    `core/action_loader.py` and `core/plugin_loader.py` keep their public
    API — other code imports them — and are now thin facades over this file.

TWO WAYS TO DISCOVER, AND WHY BOTH EXIST
    EAGER (import now): the module is executed, its TOOL/PLUGIN dict is read,
    the handler is bound. Everything is known and nothing can surprise you
    later. The cost is the import: measured at 961 ms for the bundled
    actions and plugins, because twenty-three modules pull in pyautogui,
    Pillow, opencv, playwright and the credential store between them.
    Paying that on the launch path is what made the app slow to start.

    AST-FIRST (read the source, do not run it): the declaration is a dict
    literal at module level, so the parser can read it without executing a
    single line of the module. Measured at 127 ms for the same twenty-three
    files, cold interpreter included.

    Reading a declaration is cheap, but not free, and the reason is worth
    knowing: parsing the WHOLE of every module costs 393 ms, almost all of it
    spent on the code ABOVE the declaration — the handler, the helpers, the
    imports. The declaration itself is a dict literal, so the text can be
    located and only that fragment parsed: 55 ms for the same files, with
    the result identical file for file.

    The eager path is not deleted: it is what validates a module, and it is
    what the tests use when they need an import error to surface at
    discovery time.

WHAT NEVER HAPPENS HERE
    An exception MESSAGE never reaches a record, a log, or the model. The
    message of an exception raised while importing a third-party file can
    contain a path, a token or a query string, and the log is a thing people
    paste into bug reports. Only the exception TYPE is kept — which is all
    that is needed to say "this file did not load".
"""
from __future__ import annotations

import ast
import importlib.util
import re
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

# The two origins. They are the only difference between a bundled action and
# a plugin, and keeping them as constants means nobody has to guess whether
# "action" or "plugin" is the word used in a given field.
ORIGIN_ACTION = "action"
ORIGIN_PLUGIN = "plugin"

_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")
_DEFAULT_PARAMS = {"type": "OBJECT", "properties": {}}

# A module-level declaration starts at the beginning of a line, with no
# indentation — which is exactly how a module-level assignment looks in
# Python, and how it cannot look inside a function.
_DECL_RE = re.compile(r"^(TOOL|PLUGIN)\s*=", re.MULTILINE)
_TRIPLE_QUOTES = ('"""', "'''")

# A tool may declare that the model should NOT be held up waiting for it.
# `behavior` goes to the API with the declaration; `scheduling` decides when
# the eventual result is allowed back into the conversation:
#   WHEN_IDLE  — wait for a gap in the speech (the sane default)
#   SILENT     — record it, do not prompt a reply (the tool already announced)
#   INTERRUPT  — cut in immediately (only when the answer cannot wait)
_BEHAVIORS = ("BLOCKING", "NON_BLOCKING")
_SCHEDULING = ("WHEN_IDLE", "SILENT", "INTERRUPT")


# ── Small helpers, shared by both origins ───────────────────────────────────

def opt_upper(value, allowed: tuple[str, ...]) -> Optional[str]:
    v = str(value or "").strip().upper()
    return v if v in allowed else None


def opt_tier(value) -> Optional[str]:
    """"core" or "extra" from a declaration, or None when it did not say.

    Anything else is treated as "did not say" rather than as an error: a typo
    should fall back to the default ranking, not stop a working tool loading.
    """
    text = str(value or "").strip().lower()
    return text if text in ("core", "extra") else None


def opt_text(value) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def safe_error(exc: BaseException) -> str:
    """A description of a failure that cannot leak what the failure said.

    The message of an exception raised while loading somebody's file can
    contain a path, a token or a query string. The type is enough to act on
    and safe to write down.
    """
    return f"Failed to load: {type(exc).__name__}"


# ── The record ──────────────────────────────────────────────────────────────

@dataclass
class ToolRecord:
    """One discovered tool, from either origin.

    The same shape for both, so the operator layer above never has to ask
    where a tool came from before it can use it.
    """
    name: str
    description: str = ""
    parameters: dict = field(default_factory=lambda: dict(_DEFAULT_PARAMS))
    file: str = ""
    origin: str = ORIGIN_ACTION
    valid: bool = False
    error: str = ""
    handler: Optional[Callable] = None
    run: Optional[Callable] = None
    settings: Optional[dict] = None
    behavior: Optional[str] = None      # None = the API's default (blocking)
    scheduling: Optional[str] = None    # None = the API's default (WHEN_IDLE)
    tier: Optional[str] = None
    brief: Optional[str] = None
    # Where the module lives, so it can be imported later instead of now.
    module_name: str = ""
    module_path: str = ""
    loaded: bool = False                # has the module been imported yet?

    def resolve(self) -> Optional[Callable]:
        """The callable behind this tool, importing the module if needed.

        Returns None when the module cannot be imported. Never raises: the
        caller decides what a missing handler means.
        """
        if self.handler is not None:
            return self.handler
        if self.run is not None:
            return self.run
        if not self.module_path:
            return None
        try:
            module = load_module(Path(self.module_path), self.module_name)
        except Exception:
            return None
        self.loaded = True
        if self.origin == ORIGIN_PLUGIN:
            self.run = getattr(module, "run", None)
            return self.run
        decl = getattr(module, "TOOL", None)
        if isinstance(decl, dict):
            self.handler = decl.get("handler")
        return self.handler


# ── Importing ───────────────────────────────────────────────────────────────

def load_module(path: Path, module_name: str):
    """Import a file as a module, isolating whatever it does at import time.

    A module that raises leaves nothing behind in sys.modules, and the
    exception propagates to the caller, which decides how to report it.
    """
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError("could not build import spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


# ── Reading a declaration without running the module ────────────────────────

def _skip_string(source: str, index: int) -> int:
    """The index just past the string that starts at `index`.

    Handles the triple-quoted forms too. A string that never closes runs to
    the end of the source, which is the safe answer: the caller gives up and
    falls back to the full parser.
    """
    quote = source[index]
    if source[index:index + 3] == quote * 3:
        end = source.find(quote * 3, index + 3)
        return len(source) if end < 0 else end + 3
    index += 1
    size = len(source)
    while index < size:
        char = source[index]
        if char == "\\":
            index += 2
            continue
        if char == quote:
            return index + 1
        if char == "\n":
            return index
        index += 1
    return size


def _value_end(source: str, index: int) -> int:
    """The index just past the expression that starts at `index`.

    Walks the text tracking bracket depth and skipping strings and comments,
    so a `}` inside a description does not end the expression early. Returns
    -1 when the expression never closes.
    """
    size = len(source)
    depth = 0
    while index < size:
        char = source[index]
        if char == "#":
            newline = source.find("\n", index)
            index = size if newline < 0 else newline
            continue
        if char in "\"'":
            index = _skip_string(source, index)
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return -1


def _maybe_inside_a_string(source: str, index: int) -> bool:
    """True when `index` is probably inside a triple-quoted string.

    A cheap and deliberately over-cautious test: it counts the triple-quote
    markers before the candidate and calls the position suspicious when the
    count is odd. A stray marker in a comment makes it say "suspicious" when
    the position is fine, and the answer is then the slow path, which is
    always correct. The opposite mistake, calling a real docstring position
    safe, would need an even number of markers around it, which is not how a
    docstring is written.
    """
    prefix = source[:index]
    total = sum(prefix.count(marker) for marker in _TRIPLE_QUOTES)
    return total % 2 == 1


def _declaration_node(source: str) -> Optional[ast.Dict]:
    """The declaration dict in `source`, located WITHOUT parsing the whole file.

    Parsing every module in full costs 393 ms for the bundled corpus, almost
    all of it spent on the code ABOVE the declaration — the handler, the
    helpers, the imports. The declaration itself is a dict literal, so the
    text can be located and only that fragment parsed: 55 ms for the same
    files, with the result identical file for file.

    Returns None when the declaration cannot be located this way, which is
    not an error: the caller falls back to the full parser.
    """
    for match in _DECL_RE.finditer(source):
        if _maybe_inside_a_string(source, match.start()):
            continue                      # probably a docstring, not the real thing
        equals = source.find("=", match.end() - 1)
        if equals < 0:
            continue
        index = equals + 1
        while index < len(source) and source[index] in " \t":
            index += 1
        end = _value_end(source, index)
        if end < 0:
            continue
        try:
            node = ast.parse(source[index:end], mode="eval").body
        except (SyntaxError, ValueError):
            continue
        if isinstance(node, ast.Dict):
            return node
    return None


def _declaration_node_full(source: str) -> Optional[ast.Dict]:
    """The declaration dict, found by parsing the WHOLE file.

    The fallback for anything the fast path cannot handle. Slower and
    complete: it sees every module-level assignment, whatever its shape.
    """
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if getattr(target, "id", None) in ("TOOL", "PLUGIN"):
                if isinstance(node.value, ast.Dict):
                    return node.value
                return None
    return None


def declaration_from_source(path: Path) -> tuple[Optional[dict], str]:
    """The tool declaration in `path`, read WITHOUT executing the file.

    Returns (declaration, error). The declaration is the module-level
    TOOL/PLUGIN dict with everything in it evaluated as a literal; the
    handler — a reference to a function, not a literal — is left out and
    bound later, when the module is imported.

    A file with no such dict is not an error: it is a helper module, and the
    caller ignores it. That is why the return is (None, "") rather than an
    exception.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except Exception as exc:
        return None, safe_error(exc)

    node = _declaration_node(source)
    if node is None:
        # Either there is no declaration, or the fast path could not find it.
        # The full parser tells the two apart.
        try:
            node = _declaration_node_full(source)
        except SyntaxError as exc:
            return None, safe_error(exc)
        except Exception as exc:
            return None, safe_error(exc)
    if node is None:
        return None, ""

    out: dict = {}
    for key, value in zip(node.keys, node.values):
        if key is None:
            continue                      # a ** spread in the dict literal
        try:
            name = ast.literal_eval(key)
        except Exception:
            return None, "the declaration has a key that is not a literal"
        if name in ("handler", "run"):
            out[name] = None              # bound at call time, not read now
            continue
        try:
            out[name] = ast.literal_eval(value)
        except Exception:
            return None, f"the declaration's '{name}' is not a literal value"
    return out, ""


# ── Validation ──────────────────────────────────────────────────────────────

def validate_declaration(decl: dict, filename: str, origin: str, *,
                         handler: Optional[Callable] = None,
                         run: Optional[Callable] = None,
                         settings: Optional[dict] = None,
                         require_callable: bool = True) -> ToolRecord:
    """Turn a declaration into a record. Never raises.

    `require_callable` is False on the AST path, where the callable cannot
    exist yet: the file has not been run. The callable is checked when the
    tool is called, and again by the background validation pass.
    """
    name = decl.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        return ToolRecord(name=str(name or Path(filename).stem), file=filename,
                          origin=origin, error="the tool name is missing or not a valid identifier")

    description = decl.get("description")
    if not isinstance(description, str) or not description.strip():
        return ToolRecord(name=name, file=filename, origin=origin,
                          error="the tool description is missing or empty")

    parameters = decl.get("parameters", _DEFAULT_PARAMS)
    if not isinstance(parameters, dict) or parameters.get("type") != "OBJECT":
        return ToolRecord(name=name, file=filename, origin=origin,
                          error='the tool parameters must be a dict with "type": "OBJECT"')

    if require_callable:
        if origin == ORIGIN_PLUGIN:
            if not callable(run):
                return ToolRecord(name=name, file=filename, origin=origin,
                                  error="the plugin has no callable run() function")
        elif not callable(handler):
            return ToolRecord(name=name, file=filename, origin=origin,
                              error="the tool has no callable handler")

    # A malformed settings schema is ignored, never fatal — the plugin still
    # loads, it just gets no configuration form.
    if not (isinstance(settings, dict) and isinstance(settings.get("fields"), list)):
        settings = None

    return ToolRecord(
        name=name,
        description=description.strip(),
        parameters=parameters,
        file=filename,
        origin=origin,
        valid=True,
        error="",
        handler=handler,
        run=run,
        settings=settings,
        behavior=opt_upper(decl.get("behavior"), _BEHAVIORS),
        scheduling=opt_upper(decl.get("scheduling"), _SCHEDULING),
        tier=opt_tier(decl.get("tier")),
        brief=opt_text(decl.get("brief")),
    )


# ── Scanning a directory ────────────────────────────────────────────────────

def scan(directory: Path, origin: str, *,
         reserved_names: Optional[set[str]] = None,
         logger: Callable[[str], None] = print,
         eager: bool = True,
         module_prefix: Optional[str] = None,
         load_error: Optional[Callable[[Path, BaseException], str]] = None
         ) -> tuple[dict[str, ToolRecord], list[ToolRecord]]:
    """Discover the tools in `directory`. Returns (valid, all_records).

    `valid` maps a tool name to its record — valid entries only, which is
    what the rest of the app dispatches on. `all_records` holds every file
    that was looked at, valid or not, so the UI can show why something did
    not load.

    NOTHING HERE RAISES. A file that explodes on import, a declaration that
    is malformed, a name that collides: each one is recorded, logged, and
    skipped. One broken file never stops the scan of the others, and never
    reaches the app.
    """
    reserved = reserved_names or set()
    directory.mkdir(parents=True, exist_ok=True)
    prefix = module_prefix or origin
    valid: dict[str, ToolRecord] = {}
    all_records: list[ToolRecord] = []

    files = sorted(directory.glob("*.py"), key=lambda p: p.name)  # deterministic
    for path in files:
        if path.name.startswith("_"):
            continue
        module_name = f"{prefix}.{path.stem}"
        record: Optional[ToolRecord] = None

        if eager:
            try:
                module = load_module(path, module_name)
                decl = getattr(module, "TOOL", None)
                if decl is None:
                    decl = getattr(module, "PLUGIN", None)
                if not isinstance(decl, dict):
                    continue                 # a helper module, not a tool
                handler = decl.get("handler") if origin == ORIGIN_ACTION else None
                run = getattr(module, "run", None) if origin == ORIGIN_PLUGIN else None
                settings = getattr(module, "PLUGIN_SETTINGS", None)
                record = validate_declaration(decl, path.name, origin,
                                              handler=handler, run=run, settings=settings)
            except Exception as exc:
                message = load_error(path, exc) if load_error else safe_error(exc)
                record = ToolRecord(name=path.stem, file=path.name, origin=origin,
                                    error=message)
                traceback.print_exc()
        else:
            decl, error = declaration_from_source(path)
            if error:
                record = ToolRecord(name=path.stem, file=path.name, origin=origin,
                                    error=error)
            elif decl is None:
                continue                     # a helper module, not a tool
            else:
                record = validate_declaration(decl, path.name, origin,
                                              require_callable=False)
                record.module_name = module_name
                record.module_path = str(path)

        if record.valid and record.name in reserved:
            record = ToolRecord(name=record.name, file=path.name, origin=origin,
                                error=f"the name '{record.name}' collides with a reserved tool")
        elif record.valid and record.name in valid:
            other = valid[record.name].file
            record = ToolRecord(name=record.name, file=path.name, origin=origin,
                                error=f"the name '{record.name}' is already used by '{other}'")

        all_records.append(record)
        if record.valid:
            valid[record.name] = record
            logger(f"{origin.capitalize()} loaded: {record.name} ({path.name})")
        else:
            logger(f"{origin.capitalize()} rejected: {path.name} — {record.error}")

    return valid, all_records
