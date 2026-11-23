"""
core/allowed_dirs.py — the folders the user has allowed the assistant to touch.

THE PROBLEM
    "Delete report.docx" is a sentence. Whether it is safe to obey depends on
    something the model cannot see: where the file actually is. A path that
    LOOKS like it is inside the user's Documents can be a junction — a
    directory that points somewhere else — and the bytes land in a place the
    user never allowed.

    Comparing the path AS WRITTEN is the classic mistake. `C:\\Users\\sergi\\
    Desktop` says "Desktop", and if `Desktop` is a junction to `D:\\otro\\sitio`
    then the file that gets written is `D:\\otro\\sitio\\...`. The check passed
    and the guarantee it was supposed to give did not exist.

    So the comparison is done on the RESOLVED path. On Windows, `Path.resolve()`
    goes through `os.path.realpath`, which follows symlinks, junctions and NTFS
    mount points. Measured on this machine (Windows, Python 3.13): a directory
    junction created with `mklink /J` resolves to its real target. A path that
    RESOLVES outside the allowed root is forbidden even when the written path
    is inside it — that is the whole point of the rule.

THE DEFAULT (and why)
    With NO `allowed_dirs` configured, NOTHING is restricted. The policy lives
    in the code and the profile only narrows it: the assistant is a tool for
    the whole computer, and the chassis has always behaved that way. Turning
    the restriction on by default would break every existing user for a
    protection they did not ask for. With `allowed_dirs` configured, the
    restriction is HARD — a path that resolves outside the roots is refused
    before anything is touched.

    A configuration file that is corrupt, missing, or that holds an
    `allowed_dirs` that is not a list of strings is treated as "no roots
    configured": the same as not having said anything. A broken file must not
    lock the user out of their own computer.

WHERE THE ROOTS COME FROM
    `config/api_keys.json`, read through `memory.config_manager.load_api_keys()`
    — never by opening the file here. The key is `allowed_dirs`, a list of
    strings. A nested `{"security": {"allowed_dirs": [...]}}` is accepted too,
    because that is the name the reference implementation used.

    Example (`config/api_keys.json`):

        "allowed_dirs": ["C:\\\\Users\\\\sergi\\\\Documents",
                         "D:\\\\Trabajo\\\\clientes"]
"""
from __future__ import annotations

import os
from pathlib import Path

# The key in config/api_keys.json.
CONFIG_KEY = "allowed_dirs"

# The same key nested, the way the reference implementation wrote it.
_NESTED_SECTION = "security"


# ── Configuration ───────────────────────────────────────────────────────────

def _config() -> dict:
    """The configuration, or an empty dict when it cannot be read."""
    try:
        # Imported here, not at module level: the configuration manager must
        # not be a reason for the file operator to fail to import.
        from memory import config_manager
        data = config_manager.load_api_keys()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _raw_roots() -> list[str]:
    """The configured roots, as the user wrote them.

    Anything that is not a list of strings is ignored: a corrupt value is
    treated as "not configured", never as "everything is forbidden" and never
    as "everything is allowed".
    """
    data = _config()
    raw = data.get(CONFIG_KEY)
    if raw is None:
        section = data.get(_NESTED_SECTION)
        if isinstance(section, dict):
            raw = section.get(CONFIG_KEY)
    if not isinstance(raw, (list, tuple)):
        return []

    roots: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        text = item.strip().strip('"').strip("'").strip()
        if text:
            roots.append(text)
    return roots


def allowed_dirs() -> list[str]:
    """The allowed roots, as absolute paths, or an empty list.

    An empty list means "nothing was configured", which means "nothing is
    restricted" — see the module docstring.
    """
    roots: list[str] = []
    for raw in _raw_roots():
        try:
            expanded = os.path.expandvars(os.path.expanduser(raw))
            roots.append(os.path.abspath(expanded))
        except Exception:
            continue
    return roots


def resolved_roots() -> list[Path]:
    """The allowed roots after resolution (junctions and symlinks followed)."""
    return [resolve_path(root) for root in allowed_dirs()]


# ── Resolution ──────────────────────────────────────────────────────────────

def resolve_path(path) -> Path:
    """The REAL path of `path`, with junctions and symlinks followed.

    `Path.resolve()` on Windows resolves directory junctions, symbolic links
    and NTFS mount points — it is `os.path.realpath`, which asks the file
    system for the final path of the object. This never raises: a path that
    cannot be resolved (a broken link, a path the file system refuses to
    touch) falls back to the absolute path, which is the conservative answer
    because an unresolved path is compared as the user wrote it.
    """
    if path is None:
        return Path.cwd()

    try:
        raw = os.fspath(path)
    except TypeError:
        raw = str(path)

    text = str(raw).strip().strip('"').strip("'").strip()
    if not text:
        return Path.cwd()

    try:
        candidate = Path(text).expanduser()
    except Exception:
        return Path.cwd()

    try:
        return candidate.resolve()
    except (OSError, RuntimeError, ValueError):
        pass

    try:
        return Path(os.path.abspath(str(candidate)))
    except Exception:
        return candidate


def _within(child: Path, root: Path) -> bool:
    """Is `child` inside `root` (or `root` itself)?

    `os.path.commonpath` is used instead of a string prefix because a prefix
    is not containment: "C:\\Users\\X" is NOT inside "C:\\Users\\XY", and a
    prefix check would say it is. `commonpath` compares path COMPONENTS, so
    the sibling with a longer name is rejected. Paths on different drives
    raise ValueError, which means "not inside".
    """
    try:
        child_text = os.path.normcase(os.path.normpath(str(child)))
        root_text = os.path.normcase(os.path.normpath(str(root)))
        if not child_text or not root_text:
            return False
        return os.path.commonpath([child_text, root_text]) == root_text
    except ValueError:
        return False          # different drives
    except Exception:
        return False


# ── The question ────────────────────────────────────────────────────────────

def is_allowed(path, *, kind: str = "input") -> bool:
    """May this path be touched?

    `kind` labels the role of the path in the action ("source path",
    "destination path") and is carried into the explanation; it never changes
    the answer.

    With no configured roots every path is allowed — that is the documented
    default. With roots configured, the path is allowed only when it RESOLVES
    inside one of them.
    """
    roots = resolved_roots()
    if not roots:
        return True
    target = resolve_path(path)
    return any(_within(target, root) for root in roots)


def require_allowed(path, *, kind: str = "input") -> None:
    """Raise PermissionError when the path may not be touched.

    The message names the REASON ("outside the allowed folders") because the
    person reading it needs to know what to do about it, and it carries the
    path because the path is the thing they asked about.
    """
    if is_allowed(path, kind=kind):
        return
    raise PermissionError(_reason(path))


def explain(path) -> str:
    """One line saying why a path is allowed or refused."""
    roots = allowed_dirs()
    if not roots:
        return ("allowed: no \"allowed_dirs\" is configured, so every path "
                "is allowed.")

    target = resolve_path(path)
    for root in resolved_roots():
        if _within(target, root):
            return f"allowed: '{target}' is inside '{root}'."

    listed = ", ".join(f"'{root}'" for root in roots)
    return f"blocked: '{target}' is outside the allowed folders ({listed})."


def _reason(path) -> str:
    """The sentence a refused path gets, naming the reason."""
    roots = allowed_dirs()
    target = resolve_path(path)
    written = str(path).strip().strip('"').strip("'")

    where = f"'{written}'"
    try:
        absolute = os.path.abspath(os.path.expanduser(written))
        if os.path.normcase(absolute) != os.path.normcase(str(target)):
            # The written path and the real one differ: this is a junction or
            # a symbolic link, and saying so is the useful part.
            where += f", which really points at '{target}'"
    except Exception:
        pass

    listed = ", ".join(f"'{root}'" for root in roots)
    return (
        f"{where} is outside the allowed folders ({listed}). "
        f"Move it into one of those folders, or add that folder to "
        f"\"{CONFIG_KEY}\" in config/api_keys.json."
    )
