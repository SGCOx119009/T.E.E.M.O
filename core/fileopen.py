"""
core/fileopen.py — find a file the user named and open it with its own app.

THE GAP THIS FILLS
    "Abre el Excel que dice WA." was answered "I cannot open it directly",
    and the reason was not a missing permission. It was a missing CAPABILITY:
    the only way this product could start a program was `open_app`, and
    `open_app` took the NAME OF AN APPLICATION. A document was therefore
    unreachable — the assistant could start Excel, but not the file inside
    Excel, which is what "open the file" means to a person.

    So the two halves live apart: this module resolves a spoken file name to a
    path on disk and hands that path to the application Windows has already
    associated with it; `actions/open_app.py` decides which of the two the
    model meant.

WHY THE ASSOCIATION IS ASKED OF WINDOWS AND NOT GUESSED
    A spreadsheet is not "an Excel file" in general — on a machine with
    LibreOffice it is a `soffice` file, and on a machine with nothing
    installed for it, it is a file with no application at all. The mapping
    lives in the registry (`HKCR\\.xlsx` → ProgID → the `open\\command`
    line), which is the same place Windows itself looks when the user
    double-clicks. Reading it means the answer is right on every machine
    instead of right on the one it was written on.

    `UserChoice` is read first when present: that is the association the USER
    picked, and it overrides the system default. Asking the registry the
    wrong question here produces a file opening in the wrong program, which
    is worse than not opening it.

WHAT IS VERIFIED, AND WHAT IS NOT
    Opening a file is not verifiable by the process table alone: if the
    application is already running, Windows hands the document to the running
    copy and NO new process appears. The proof in that case is the window
    TITLE — a document that opened puts its name in the title bar. So the
    check is two-sided: a new process for the application, or a window whose
    title carries the document's name. Either one is evidence. Neither, after
    the timeout, is reported as `unverified` rather than as success.

    Nothing here raises into the conversation. Every failure comes back as a
    sentence the model can relay, because a traceback in the HUD is not an
    answer to "open the file".
"""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
#  What counts as a file
# ─────────────────────────────────────────────────────────────────────────────

# Extensions worth treating as "a document to open" when the user says the
# name alone. Not a whitelist of what this product can open — it is a list of
# the names people actually say out loud, used only to decide WHICH of the two
# routes to take. Anything with a path separator or a drive letter goes down
# the file route whatever its extension is.
_FILE_EXTENSIONS = frozenset({
    ".xlsx", ".xls", ".xlsm", ".csv", ".ods",
    ".docx", ".doc", ".docm", ".odt", ".rtf", ".txt", ".md", ".log",
    ".pptx", ".ppt", ".odp", ".pdf", ".epub",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".heic", ".tif",
    ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".mp4", ".mkv", ".avi", ".mov",
    ".zip", ".7z", ".rar", ".tar", ".gz", ".json", ".xml", ".yaml", ".yml",
    ".py", ".js", ".ts", ".html", ".css", ".sql", ".db", ".sqlite",
})

# How long the search for a named file may take before giving up. A person
# says "open WA" and waits; they do not wait for a hard drive scan.
_SEARCH_BUDGET_S = 2.5

# How deep into a folder tree the search may go. Three levels is what a
# OneDrive desktop actually needs: the file the user named was in
# `OneDrive\Escritorio\nueva carpeta\actas verificadas 28 de julio\`, which is
# three levels down from the OneDrive root and one level down from the
# OneDrive desktop. The budget below is what keeps that from being slow.
_SEARCH_DEPTH = 3

# How many directory entries are read in one folder before moving on.
_SCAN_LIMIT = 4000


def looks_like_file(text: str) -> bool:
    """Is this string naming a file rather than an application?

    Three signals, any of which is enough:

      * it carries a path separator or a drive letter — `C:\\Users\\x\\a.xlsx`,
        `Desktop\\informe.pdf`
      * it ends in an extension people say out loud — `wa.xlsx`
      * it ends in ANY extension, and the stem before it is long enough to be
        a name — `presupuesto.2024`

    The third rule is deliberately late and deliberately narrow: "app.exe" is
    an application, not a document, and treating it as a file would send a
    program launch down the file route.
    """
    raw = (text or "").strip().strip('"').strip("'")
    if not raw:
        return False
    if "\\" in raw or "/" in raw:
        return True
    if len(raw) >= 2 and raw[1] == ":" and raw[0].isalpha():
        return True
    stem, ext = os.path.splitext(raw)
    if not ext or len(ext) > 6:
        return False
    ext = ext.lower()
    if ext in _FILE_EXTENSIONS:
        return True
    # An unfamiliar extension still counts as a file when the stem looks like
    # a name rather than an executable. `.exe`, `.bat`, `.cmd` and `.lnk` are
    # the other way round: they ARE programs.
    if ext in (".exe", ".bat", ".cmd", ".com", ".lnk", ".msi"):
        return False
    if " " in stem:
        return len(stem) >= 2
    return len(stem) >= 4


# ─────────────────────────────────────────────────────────────────────────────
#  Where files are looked for
# ─────────────────────────────────────────────────────────────────────────────
def search_roots() -> list[str]:
    """The folders a named file is looked for, in order.

    The user's own places first — Desktop, Downloads, Documents — because
    that is where a file somebody is talking about lives. OneDrive's
    Desktop is included next to the local one, because on a machine with
    OneDrive the desktop the user sees is the one inside it and the local
    folder is empty.
    """
    home = os.path.expanduser("~")
    roots: list[str] = []
    for candidate in (
        os.path.join(home, "Desktop"),
        os.path.join(home, "OneDrive", "Desktop"),
        os.path.join(home, "OneDrive", "Escritorio"),
        os.path.join(home, "Downloads"),
        os.path.join(home, "OneDrive", "Downloads"),
        os.path.join(home, "Documents"),
        os.path.join(home, "OneDrive", "Documentos"),
        os.path.join(home, "OneDrive", "Documents"),
        home,
    ):
        if os.path.isdir(candidate) and candidate not in roots:
            roots.append(candidate)
    return roots


def _walk(root: str, depth: int, budget: float):
    """Every file under `root`, up to `depth` levels, within `budget` seconds."""
    deadline = time.monotonic() + budget
    stack = [(root, 0)]
    while stack:
        if time.monotonic() > deadline:
            return
        folder, level = stack.pop()
        try:
            with os.scandir(folder) as entries:
                for n, entry in enumerate(entries):
                    if n >= _SCAN_LIMIT or time.monotonic() > deadline:
                        break
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if level + 1 < _SEARCH_DEPTH:
                                stack.append((entry.path, level + 1))
                        elif entry.is_file(follow_symlinks=False):
                            yield entry.path
                    except OSError:
                        continue
        except (OSError, PermissionError):
            continue


def _same(a: str, b: str) -> bool:
    return a.casefold() == b.casefold()


def _fold(text: str) -> str:
    """A name written any way at all, compared with any other.

    Accents and case are noise: "Escritorio" is "escritorio", and a folder
    called "Actas Verificadas" is the folder somebody called "actas verificadas
    28 de julio" once the date is part of the name they said out loud.
    """
    import unicodedata
    decomposed = unicodedata.normalize("NFKD", str(text or "").casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c)).strip()


def _segments(text: str) -> list[str]:
    """The parts of a spoken path: "OneDrive, escritorio, nueva carpeta"."""
    import re
    parts = re.split(r"[\\/,:;|]+", str(text or ""))
    return [p.strip() for p in parts if p.strip()]


def _search_child(folder: str, name: str, rest: list[str],
                  deadline: float, depth: int) -> Optional[str]:
    """A subfolder called `name` somewhere under `folder`, then the rest.

    The direct child is tried first — that is the common case and it is
    cheap. Only if there is no direct child does the search go one level
    deeper, which is what makes "actas verificadas" findable when the user
    names a folder that is two levels down from the desktop.
    """
    if time.monotonic() > deadline or depth <= 0:
        return None
    wanted = _fold(name)
    try:
        with os.scandir(folder) as entries:
            children = list(entries)
    except (OSError, PermissionError):
        return None

    for entry in children:
        try:
            if not entry.is_dir(follow_symlinks=False):
                continue
            if _fold(entry.name) != wanted:
                continue
            if not rest:
                return entry.path
            found = _search_child(entry.path, rest[0], rest[1:], deadline, depth)
            if found:
                return found
        except OSError:
            continue

    for entry in children:
        try:
            if not entry.is_dir(follow_symlinks=False):
                continue
            found = _search_child(entry.path, name, rest, deadline, depth - 1)
            if found:
                return found
        except OSError:
            continue
    return None


def _find_folder(segments: list[str], deadline: float) -> Optional[str]:
    """Walk down from the user's own folders, matching one segment at a time.

    WHY A WALK AND NOT A STRING MATCH
        The user says "la ruta es OneDrive, escritorio, nueva carpeta, actas
        verificadas 28 de julio" and then "abre el archivo ELS2022". Joining
        those into one string and searching the disk for it fails, because the
        separators a person uses are commas and the folder names do not line
        up with the search roots. Walking down — root by root, segment by
        segment — is what the words actually describe.
    """
    if not segments:
        return None
    for root in search_roots():
        if time.monotonic() > deadline:
            return None
        found = _search_child(root, segments[0], segments[1:], deadline, 3)
        if found:
            return found
    return None


def _match_in_folder(folder: str, wanted: str, deadline: float) -> Optional[str]:
    """The file called `wanted` inside `folder`, by exact name or by stem."""
    stem, ext = os.path.splitext(wanted)
    exact: Optional[str] = None
    by_stem: Optional[str] = None
    for path in _walk(folder, _SEARCH_DEPTH, max(0.1, deadline - time.monotonic())):
        name = os.path.basename(path)
        if ext:
            if _same(name, wanted):
                exact = path
                break
            if by_stem is None and _fold(os.path.splitext(name)[0]) == _fold(stem):
                by_stem = path
        elif _fold(os.path.splitext(name)[0]) == _fold(stem):
            by_stem = path
            break
        if time.monotonic() > deadline:
            break
    return exact or by_stem


def resolve(text: str) -> Optional[str]:
    """The path a spoken file name refers to, or None.

    The order is deliberate: an exact path the user gave wins over anything
    found by searching, because a path is an answer and a search is a guess.
    """
    raw = (text or "").strip().strip('"').strip("'")
    if not raw:
        return None

    # 1. The user gave a path.
    if os.path.isabs(raw) or "\\" in raw or "/" in raw:
        candidate = os.path.expanduser(raw)
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
        home = os.path.expanduser("~")
        relative = os.path.join(home, raw)
        if os.path.isfile(relative):
            return os.path.abspath(relative)

    deadline = time.monotonic() + _SEARCH_BUDGET_S

    # 2. The user gave a FOLDER and a file: "OneDrive, escritorio, nueva
    #    carpeta" + "ELS2022". The folder is walked down and the file is
    #    looked for inside it, which is what the words mean.
    parts = _segments(raw)
    if len(parts) >= 2:
        last = parts[-1]
        folder = _find_folder(parts[:-1], deadline)
        if folder:
            found = _match_in_folder(folder, last, deadline)
            if found:
                return os.path.abspath(found)

    # 3. The name alone, found in the user's own folders.
    wanted = os.path.basename(raw)
    stem, ext = os.path.splitext(wanted)
    exact: list[str] = []
    by_stem: list[str] = []
    for root in search_roots():
        if time.monotonic() > deadline:
            break
        for path in _walk(root, _SEARCH_DEPTH,
                          max(0.1, deadline - time.monotonic())):
            name = os.path.basename(path)
            if not ext:
                # No extension was said: match the stem, so "open WA" finds
                # "WA.xlsx" and "WA.pdf" alike.
                if os.path.splitext(name)[0].casefold() == stem.casefold():
                    by_stem.append(path)
            elif _same(name, wanted):
                exact.append(path)
            if len(exact) >= 20:
                break
        if exact:
            break
    if exact:
        return os.path.abspath(exact[0])
    if by_stem:
        return os.path.abspath(by_stem[0])
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  Which application opens this file
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Association:
    """What Windows will do with a file of this type."""

    prog_id: str = ""      # e.g. "Excel.Sheet.12"
    command: str = ""      # the `open\\command` line
    exe: str = ""         # the executable inside it
    name: str = ""        # its name without extension, for matching processes

    @property
    def usable(self) -> bool:
        return bool(self.exe)


def _read_value(key_path: str, root) -> Optional[str]:
    import winreg
    try:
        with winreg.OpenKey(root, key_path) as key:
            value, _ = winreg.QueryValueEx(key, "")
            return str(value)
    except OSError:
        return None


def _exe_from_command(command: str) -> str:
    """The executable inside a registry `open\\command` line.

    The line looks like `"C:\\Program Files\\Microsoft Office\\root\\Office16\\
    EXCEL.EXE" "%1"` — quoted, with the document as a later argument. Only the
    first token is the program; the rest belongs to the program.
    """
    command = (command or "").strip()
    if not command:
        return ""
    if command.startswith('"'):
        end = command.find('"', 1)
        if end > 0:
            return command[1:end]
        return command[1:].strip()
    # Unquoted: the path ends at the first space. A path with spaces in it is
    # quoted in practice, so this is the rare branch.
    return command.split(" ", 1)[0].strip()


def association(path: str) -> Association:
    """The application Windows has associated with this file.

    `UserChoice` first — the association the USER picked — then the system
    default for the extension, then the extension itself as a ProgID
    (`.txt` → `txtfile`).
    """
    import winreg

    ext = os.path.splitext(path)[1].lower()
    if not ext:
        return Association()

    prog_id = ""
    # A per-user override beats the system default: this is the association
    # the person on this machine actually made.
    user_choice = _read_value(
        os.path.join(
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts",
            ext, "UserChoice"),
        winreg.HKEY_CURRENT_USER,
    )
    if user_choice:
        prog_id = user_choice
    if not prog_id:
        prog_id = _read_value(
            os.path.join(r"Software\Classes", ext), winreg.HKEY_CURRENT_USER) or ""
    if not prog_id:
        prog_id = _read_value(ext, winreg.HKEY_CLASSES_ROOT) or ""
    if not prog_id:
        prog_id = ext.lstrip(".")

    command = _read_value(
        os.path.join(prog_id, r"shell\open\command"), winreg.HKEY_CLASSES_ROOT)
    if not command and prog_id != ext.lstrip("."):
        command = _read_value(
            os.path.join(ext.lstrip("."), r"shell\open\command"),
            winreg.HKEY_CLASSES_ROOT)

    exe = _exe_from_command(command)
    name = os.path.splitext(os.path.basename(exe))[0] if exe else ""
    return Association(prog_id=prog_id, command=command or "", exe=exe, name=name)


# ─────────────────────────────────────────────────────────────────────────────
#  Opening it, and proving it opened
# ─────────────────────────────────────────────────────────────────────────────
def open_with_default_app(path: str) -> None:
    """Hand the file to its associated application.

    `os.startfile` is the call Windows' own "Open with" makes. It raises
    `OSError` when nothing on this machine handles the extension, and that
    error is the answer — it is the difference between "there is no program
    for this file" and "the program did not start".
    """
    os.startfile(path)          # noqa: S606 — the OS handler, not a shell


def document_stem(path: str) -> str:
    """The name a window would carry for this document: 'wa' for 'WA.xlsx'."""
    return os.path.splitext(os.path.basename(path))[0]


def _titles() -> list[str]:
    """Every visible top-level window title on this machine."""
    try:
        from core import appidentity
        out: list[str] = []
        for titles in appidentity.window_titles().values():
            out.extend(titles)
        return out
    except Exception as exc:
        print(f"[fileopen] window titles unavailable: {exc}")
        return []


def _mentions(title: str, stem: str) -> bool:
    """Does this window title carry this document's name?

    ON WHOLE WORDS, NOT SUBSTRINGS
        The document "WA.xlsx" must not be found in a window titled
        "WhatsApp" — the letter pair is there, the file is not. A title
        matches only when the name appears with a non-alphanumeric character
        (or the edge of the title) on both sides, which is how a title bar
        actually delimits a file name: "WA - Excel", "cuentas.xlsx - Excel".
    """
    haystack = title.casefold()
    needle = stem.casefold()
    if not needle:
        return False
    start = haystack.find(needle)
    while start != -1:
        end = start + len(needle)
        before_ok = start == 0 or not haystack[start - 1].isalnum()
        after_ok = end == len(haystack) or not haystack[end].isalnum()
        if before_ok and after_ok:
            return True
        start = haystack.find(needle, start + 1)
    return False


def wait_for_document(path: str, timeout: float = 12.0,
                      poll: float = 0.3) -> Optional[str]:
    """Wait until the document is visibly open. Returns the evidence, or None.

    Two kinds of evidence, because either one alone would be wrong:

      * a window whose title carries the document's name — the case where the
        application was ALREADY running and simply took the file
      * a new process for the associated application — the case where the
        application had to start, and its window may not carry the name yet

    The process check is done by the caller, which knows the process table
    from before the launch; this function only looks at titles.
    """
    stem = document_stem(path)
    if not stem:
        return None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for title in _titles():
            if _mentions(title, stem):
                return title
        time.sleep(poll)
    return None


def describe(path: str) -> str:
    """A short description of a file for the log: its name and its folder."""
    folder = os.path.dirname(path)
    return os.path.basename(path) if not folder else f"{os.path.basename(path)} ({folder})"
