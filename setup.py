"""
T.E.E.M.O. - one-time setup for Windows.

Four modes, and only the first one changes the machine:

  python setup.py                                  full install (base + browsers)
  python setup.py --dry-run                        print what WOULD happen; installs nothing
  python setup.py --plan                           read-only description of the effects
  python setup.py --install-optional <group>[,...]  install one optional group (needs confirmation)

Optional groups are never installed by accident: they need the --confirm flag
AND the exact confirmation token typed at an interactive terminal. A group
name that does not exist is rejected, never silently ignored.

Two things it deliberately does NOT install:
  * the optional local wake word - one-click, opt-in, from the settings
    panel inside the app (WAKE WORD);
  * anything for the avatar - the holographic head renders in software on the
    PyQt6 and numpy already listed here. No GPU, no OpenGL, no extra packages.

Every line this script prints is plain ASCII on purpose: the Windows console
still runs on code pages that cannot encode an emoji, and a setup that dies
with a UnicodeEncodeError after it has already installed half the machine is
worse than one that prints a dull line.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

MIN_PY = (3, 12)        # hard floor: below this the syntax used here won't parse
MAX_PY = (3, 13)        # highest version this is actually tested on

APP_NAME = "T.E.E.M.O."

BASE_MANIFEST = "requirements-base.txt"
OPTIONAL_INDEX = "requirements-optional.txt"

# Optional extras, each in its own manifest so that picking one group can
# never drag the others in. The names are the ones the project documents:
# whisper (local hearing), piper (neural voice), openwakeword (local wake
# word) and documents (spreadsheet/CSV analysis and audio metadata).
OPTIONAL_GROUPS = {
    "whisper": "requirements-optional-whisper.txt",
    "piper": "requirements-optional-piper.txt",
    "openwakeword": "requirements-optional-openwakeword.txt",
    "documents": "requirements-optional-documents.txt",
}

# The exact word a human has to type to let an optional group be installed.
CONFIRM_TOKEN = "INSTALL"

BROWSERS = ("chromium", "firefox")


def _run(label: str, args: list[str]) -> None:
    print(f"\n> {label}")
    subprocess.run(args, check=True)


def _configure_output() -> None:
    """Keep setup usable on legacy Windows consoles with limited code pages."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):
                pass


def _check_python() -> None:
    """Fail immediately and clearly rather than deep inside a pip resolver.

    A wrong interpreter is the single most common way this install goes sideways,
    and the error it produces on its own names a wheel, not the real problem.
    """
    v = sys.version_info[:2]
    if v > MAX_PY:
        # Newer is a warning, not a wall. Turning away someone who installed
        # today's Python is a worse first impression than a version that
        # turns out to work fine, and if a wheel really is missing pip says
        # so plainly.
        print(f"\nWARNING: Python {v[0]}.{v[1]} is newer than the "
              f"{MAX_PY[0]}.{MAX_PY[1]} this is tested on. Continuing - if a "
              f"package has no wheel yet, install Python "
              f"{MAX_PY[0]}.{MAX_PY[1]} and run setup with that.")
        return
    if v < MIN_PY:
        print(f"\nERROR: Python {v[0]}.{v[1]} detected - {APP_NAME} needs at "
              f"least Python {MIN_PY[0]}.{MIN_PY[1]}.")
        print("   Install a supported version and run setup with it, e.g.:")
        print(f"     py -{MIN_PY[0]}.{MIN_PY[1]} setup.py        (Windows)")
        sys.exit(1)


def _check_assets() -> bool:
    """The avatar's face is a shipped file; a truncated clone should say so."""
    face = HERE / "core" / "face_model.obj"
    if not face.exists() or face.stat().st_size < 4096:
        print(
            "\nWARNING: core/face_model.obj is missing or truncated - the avatar "
            "will fall back to the plain glowing core.\n"
            "    Re-clone the repository, or fetch that one file again."
        )
        return False
    return True


def _check_manifests() -> None:
    """Verify that the clean-copy installation inputs are present."""
    required = (
        "requirements.txt",
        BASE_MANIFEST,
        OPTIONAL_INDEX,
        *OPTIONAL_GROUPS.values(),
    )
    missing = [name for name in required if not (HERE / name).is_file()]
    if missing:
        raise SystemExit(
            "ERROR: Installation manifests missing: " + ", ".join(missing)
        )


def _requirement_lines(path: Path) -> list[str]:
    """Read a manifest into the list of requirements it really installs.

    "-r other.txt" lines are followed, so the base manifest can point at the
    Windows dependency list without anybody having to keep two copies of it
    in sync.
    """
    lines: list[str] = []
    seen: set[str] = set()
    pending = [path]
    while pending:
        current = pending.pop(0)
        if current.name in seen or not current.is_file():
            continue
        seen.add(current.name)
        for raw in current.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("-r "):
                pending.append((current.parent / line[3:].strip()).resolve())
                continue
            if line.startswith("-"):
                # Any other pip option (--index-url, --extra-index-url...) is
                # not a package and is not worth showing in a plan.
                continue
            lines.append(line.split(" #")[0].strip())
    return lines


def _show_plan() -> None:
    """Print the installation and network plan without changing the system."""
    print("\nT.E.E.M.O. setup plan (read-only; no changes will be made)")
    print("  Base packages (requirements-base.txt):")
    for requirement in _requirement_lines(HERE / BASE_MANIFEST):
        print(f"    - {requirement}")
    print("  Optional groups (not selected; explicit install required):")
    for group, manifest in OPTIONAL_GROUPS.items():
        print(f"    {group} ({manifest}):")
        for requirement in _requirement_lines(HERE / manifest):
            print(f"      - {requirement}")
    print("  Python package source: pip configuration (PyPI unless a local index is set)")
    print("  Network: package downloads only if setup is run normally;")
    print("           optional groups are installed only when selected and confirmed")
    print(f"  Playwright browsers: not selected by this plan - a normal run downloads "
          f"{' and '.join(BROWSERS)}")
    print("  Credentials/OAuth: not requested or stored")
    print(f"  Written to: {sys.prefix} (site-packages) and the Playwright browser cache")
    print("  Native tools and permissions: reported by startup diagnostics")


def _dry_run() -> None:
    """Say exactly what a real run would do - and do none of it."""
    print(f"\n{APP_NAME} setup - DRY RUN (nothing will be installed, downloaded or written)")
    print(f"  Interpreter: {sys.executable}")
    print(f"  Python: {sys.version_info[0]}.{sys.version_info[1]} "
          f"(needs at least {MIN_PY[0]}.{MIN_PY[1]})")
    print("  Base packages that would be installed:")
    for requirement in _requirement_lines(HERE / BASE_MANIFEST):
        print(f"    - {requirement}")
    print("  Optional groups (none selected; each one needs an explicit confirmation):")
    for group, manifest in OPTIONAL_GROUPS.items():
        print(f"    {group} ({manifest}):")
        for requirement in _requirement_lines(HERE / manifest):
            print(f"      - {requirement}")
    print(f"  Playwright browsers that would be downloaded: {', '.join(BROWSERS)}")
    print("  Files that would be written: python packages into this interpreter's")
    print("                               site-packages, browser binaries into the")
    print("                               Playwright cache.")
    print("  Files that would NOT be written: no virtual environment, no config/api_keys.json.")
    print("\nOK: Dry run complete. Manifests and assets are present; no packages, browsers,")
    print("   network connections, or files were changed.")


def _selected_optional_groups(argv: list[str]) -> list[str]:
    """Read --install-optional and reject any group that does not exist."""
    values: list[str] = []
    for index, argument in enumerate(argv):
        if argument == "--install-optional":
            if index + 1 >= len(argv):
                raise SystemExit(
                    "ERROR: --install-optional requires a comma-separated group list. "
                    "Choose: " + ", ".join(OPTIONAL_GROUPS)
                )
            values.extend(
                item.strip().lower()
                for item in argv[index + 1].split(",")
                if item.strip()
            )
            break
    unknown = sorted(set(values) - set(OPTIONAL_GROUPS))
    if unknown:
        raise SystemExit(
            "ERROR: Unknown optional group(s): " + ", ".join(unknown)
            + ". Choose: " + ", ".join(OPTIONAL_GROUPS)
        )
    return list(dict.fromkeys(values))


def _confirm_optional_groups(groups: list[str], argv: list[str]) -> None:
    """Two keys, both required, before an optional group is installed.

    The --confirm flag says the person meant it; the typed token proves a
    human is sitting at the keyboard. Without either of them nothing is
    installed - an unattended run cannot install an extra by accident.
    """
    if "--confirm" not in argv:
        raise SystemExit(
            "ERROR: Optional groups are never installed without an explicit "
            f"confirmation. Rerun with `--install-optional {','.join(groups)} "
            f"--confirm` and type {CONFIRM_TOKEN} at the prompt. Nothing was "
            "installed."
        )
    if sys.stdin is None or not sys.stdin.isatty():
        raise SystemExit(
            "ERROR: Optional installation needs an interactive terminal (stdin is "
            f"not a TTY), so the {CONFIRM_TOKEN} confirmation cannot be given. "
            "Nothing was installed."
        )

    print(f"\nOptional groups to install: {', '.join(groups)}")
    for group in groups:
        print(f"  {group} ({OPTIONAL_GROUPS[group]}):")
        for requirement in _requirement_lines(HERE / OPTIONAL_GROUPS[group]):
            print(f"    - {requirement}")
    print(f"\nType {CONFIRM_TOKEN} to install them, anything else to cancel:")
    try:
        answer = input()
    except EOFError:
        answer = ""
    if answer.strip() != CONFIRM_TOKEN:
        raise SystemExit(
            f"ERROR: The confirmation did not match {CONFIRM_TOKEN}. Nothing was installed."
        )


def _install_optional_groups(groups: list[str], argv: list[str]) -> None:
    _confirm_optional_groups(groups, argv)
    for group in groups:
        _run(
            f"Installing optional group: {group}",
            [sys.executable, "-m", "pip", "install", "-r",
             str(HERE / OPTIONAL_GROUPS[group])],
        )
    print(f"\nOK: Optional group(s) installed: {', '.join(groups)}")
    print("   The base dependency set is installed by a plain run: python setup.py")


def _install_browsers() -> None:
    """Chromium covers Chrome/Edge/Opera/Brave/Vivaldi; Firefox for Firefox.

    Not fatal: these are a few hundred megabytes from a CDN that a corporate
    network or a flaky connection can refuse, and everything except browser
    automation works without them. Failing the whole install there would send
    a user away from a working app.
    """
    try:
        _run("Installing Playwright browsers (chromium + firefox)...",
             [sys.executable, "-m", "playwright", "install", *BROWSERS])
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"\nWARNING: Playwright browsers were not installed ({e}).")
        print("    Everything except browser automation works. Retry later with:")
        print(f'    {sys.executable} -m playwright install chromium firefox')


def main() -> None:
    _configure_output()
    argv = sys.argv[1:]
    print(f"{APP_NAME} setup - Windows, "
          f"Python {sys.version_info[0]}.{sys.version_info[1]}")
    _check_python()
    _check_manifests()

    if "--plan" in argv:
        _check_assets()
        _show_plan()
        return

    if "--dry-run" in argv:
        if not _check_assets():
            raise SystemExit(
                "ERROR: Dry run failed: required assets are missing or truncated."
            )
        _dry_run()
        return

    groups = _selected_optional_groups(argv)
    if groups:
        _install_optional_groups(groups, argv)
        return

    _run("Installing Python dependencies...",
         [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])

    _install_browsers()

    _check_assets()

    # -- Post-install note -----------------------------------------------------
    try:
        import win32com.client  # noqa: F401
    except ImportError:
        postinstall = Path(sys.executable).parent / "Scripts" / "pywin32_postinstall.py"
        print(
            "\nWARNING: pywin32 did not register correctly - desktop-shortcut "
            "creation will use a slower fallback. To fix it, run:\n"
            f'    "{sys.executable}" -m pip install --force-reinstall pywin32\n'
            f'    "{sys.executable}" "{postinstall}" -install'
        )

    print("\nOK: Setup complete!")
    print("   1) Launch it:  python main.py")
    print("   2) Paste your free Gemini API key when the setup screen appears.")
    print("   3) (Optional) Enable the local wake word from the WAKE WORD panel.")


if __name__ == "__main__":
    main()
