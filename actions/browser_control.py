
from __future__ import annotations

import asyncio
import concurrent.futures
import importlib.util
import json
import os
import shutil
import subprocess
import threading
import time
import webbrowser
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from core import action_policy, appidentity, outcome
from core import policy as domain_policy

# Importing playwright cost 198 ms at every launch, paid by action discovery
# whether or not a browser was ever opened. It is the second-largest import in
# the project after pyautogui, and together the two were 78% of all module
# import time on the boot path.
#
# WHY THIS STILL RAISES WHEN PLAYWRIGHT IS MISSING
#     The eager import had a second job nobody wrote down: when playwright was
#     absent this module failed to load, action_loader dropped it, and the model
#     was never offered a browser tool it could not serve. Deferring the import
#     without keeping that would declare `browser_control` on a machine that
#     cannot run it — the model would call it, get an exception, and describe a
#     page it never opened. Exactly the failure mode this project spent an
#     earlier chassis
#     removing from local engines.
#
#     So the check stays at module scope and still stops the module loading; it
#     just asks find_spec instead of executing the package. 1 ms instead of 198,
#     same outcome, and a sentence that says what to install.
if importlib.util.find_spec("playwright") is None:      # pragma: no cover
    raise ImportError(
        "browser_control needs playwright, which is not installed. "
        "Run: pip install playwright  &&  playwright install chromium"
    )

# Annotations only — `from __future__ import annotations` above makes every one
# of them a string, so these names are never looked up at runtime and cost
# nothing outside a type checker.
if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page, Playwright

# WHY THIS FILE DOES NOT USE core/lazyimport.py's LazyModule LIKE THE OTHERS
#     Every other deferral in this project stands a proxy in for a whole module,
#     because the call sites say `pyautogui.press(...)` and a proxy answers that
#     the same way the module would. What this file needs are two SYMBOLS out of
#     a module: a factory function, and an exception class used in `except`.
#
#     `except` will not take a proxy. It requires a real class inheriting from
#     BaseException, checked by the interpreter at the moment the exception is
#     raised, and a LazyModule there raises TypeError instead of catching
#     anything — the original error then disappears underneath a complaint about
#     the except clause. So these two are bound as real globals instead.
#
# PlaywrightTimeout starts as a private class that is never raised, so an
# `except PlaywrightTimeout:` somehow reached before a session exists catches
# nothing rather than failing that same way.
class _PlaywrightNotLoaded(Exception):
    """Placeholder exception — declared so `except` clauses stay valid, and
    deliberately never raised anywhere."""


async_playwright = None
PlaywrightTimeout: type[BaseException] = _PlaywrightNotLoaded


def _load_playwright() -> None:
    """Import playwright and bind the two runtime symbols. Idempotent."""
    global async_playwright, PlaywrightTimeout
    if async_playwright is not None:
        return
    from playwright.async_api import (          # noqa: PLC0415 — deliberate
        async_playwright as _ap,
        TimeoutError as _timeout,
    )
    async_playwright = _ap
    PlaywrightTimeout = _timeout


_OS = "Windows"

def _normalize_url(url: str) -> str:
    """
    Bare words like "instagram" → "https://instagram.com"
    Domains like "instagram.com" → "https://instagram.com"
    Full URLs pass through unchanged.
    """
    url = url.strip()
    # Trailing sentence punctuation is never part of an address, and it arrives
    # more often than anyone would guess: a name lifted out of a sentence brings
    # the full stop with it, and youtube.com/@name./videos opens an error page
    # that looks like a page. Stripping it costs nothing and is true of every
    # site in every language.
    url = url.rstrip(".,;:!?'\"")
    if not url:
        return "about:blank"
    if "://" in url:
        return url
    # No dot at all → assume .com  (e.g. "instagram" → "instagram.com")
    if "." not in url:
        url = url + ".com"
    return "https://" + url


def _user_agent() -> str:
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )


#: THE ONE DEBUGGING ENDPOINT THIS PRODUCT WILL EVER USE.
#:
#: It is loopback, and it is a CONSTANT. A Chrome debugging port on a
#: public interface gives whoever finds it the whole browser: every tab,
#: every cookie, every half-typed password. There is no setting that
#: changes this address and no path that accepts another one — a remote
#: endpoint is refused by name rather than honoured, because "let me point
#: it at my other machine" is the exact request that turns a local tool
#: into a remote control.
CDP_HOST = "127.0.0.1"
CDP_PORT = 9222
CDP_ENDPOINT = f"http://{CDP_HOST}:{CDP_PORT}"

#: The addresses that count as loopback. "localhost" is included because it
#: is the same machine, and the resolver sends it there.
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def _cdp_endpoint() -> str:
    """The debugging endpoint, or an error if it is not loopback.

    This function exists so that the guarantee is CHECKED rather than
    assumed. The constant is right today; a future edit that points it
    somewhere else is caught here instead of in a log file on a machine
    that is no longer the user's own.
    """
    from urllib.parse import urlparse
    parsed = urlparse(CDP_ENDPOINT)
    if parsed.hostname not in _LOOPBACK_HOSTS:
        raise ValueError(
            "the browser debugging endpoint must be on the loopback "
            f"interface; '{parsed.hostname}' is not one of {_LOOPBACK_HOSTS}"
        )
    return CDP_ENDPOINT


def _cdp_available(timeout: float = 1.5) -> bool:
    """Whether the browser's debugging port is answering.

    The check is a plain HTTP GET against the loopback endpoint, with a
    short timeout: this runs on the launch path and a browser that was
    started without the debugging flag must not cost a second of silence.
    """
    import urllib.error
    import urllib.request
    endpoint = _cdp_endpoint()
    try:
        with urllib.request.urlopen(f"{endpoint}/json/version", timeout=timeout):
            return True
    except Exception:
        return False


def _automation_profile_dir(browser: str) -> str:
    """The assistant's own profile for one browser.

    This is the profile used when the user's own cannot be opened: their
    browser is already running, or their profile is locked, or a newer
    Chrome refuses to automate the real profile. It lives beside the
    other assistant state and NEVER inside the browser's own data
    directory, so automating cannot write into the user's history, their
    cookies or their saved passwords.

    It does persist: a sign-in made here stays signed in for later
    sessions, which is the whole point of a profile of our own.
    """
    name = str(browser or "").strip().lower()
    if not name:
        raise ValueError("a browser name is required")
    if not name.replace("-", "").replace("_", "").isalnum():
        raise ValueError(f"'{browser}' is not a usable browser name")
    path = Path.home() / ".teemo_profiles" / name
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _real_profile_dir(browser: str) -> str:
    home  = Path.home()
    local = os.environ.get("LOCALAPPDATA", "")
    roam  = os.environ.get("APPDATA", "")

    candidates: list[Path] = []

    m = {
        "chrome":   [Path(local) / "Google"          / "Chrome"          / "User Data"],
        "edge":     [Path(local) / "Microsoft"        / "Edge"            / "User Data"],
        "brave":    [Path(local) / "BraveSoftware"    / "Brave-Browser"   / "User Data"],
        "vivaldi":  [Path(local) / "Vivaldi"          / "User Data"],
        "opera":    [Path(roam)  / "Opera Software"   / "Opera Stable",
                     Path(local) / "Opera Software"   / "Opera Stable"],
        "operagx":  [Path(roam)  / "Opera Software"   / "Opera GX Stable",
                     Path(local) / "Opera Software"   / "Opera GX Stable"],
    }
    candidates = m.get(browser, [])

    for p in candidates:
        if p.exists():
            print(f"[Browser] ✅ Real profile found for {browser}: {p}")
            return str(p)

    fallback = home / ".teemo_profiles" / browser
    fallback.mkdir(parents=True, exist_ok=True)
    print(f"[Browser] ⚠️  Real profile not found for {browser}, using: {fallback}")
    return str(fallback)

def _firefox_profile_dir() -> Optional[str]:
    home = Path.home()
    base = Path(os.environ.get("APPDATA", "")) / "Mozilla" / "Firefox"

    ini = base / "profiles.ini"
    if not ini.exists():
        return None

    current: dict[str, str] = {}
    default_path: Optional[str] = None

    for line in ini.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line.startswith("["):
            p = current.get("Path", "")
            if p and current.get("Default") == "1":
                is_rel = current.get("IsRelative", "1") == "1"
                default_path = str(base / p) if is_rel else p
            current = {}
        elif "=" in line:
            k, _, v = line.partition("=")
            current[k.strip()] = v.strip()

    p = current.get("Path", "")
    if p and current.get("Default") == "1":
        is_rel = current.get("IsRelative", "1") == "1"
        default_path = str(base / p) if is_rel else p

    if default_path and Path(default_path).exists():
        print(f"[Browser] Firefox real profile: {default_path}")
        return default_path
    return None

def _find_opera_windows() -> Optional[str]:
    local  = os.environ.get("LOCALAPPDATA", "")
    prog   = os.environ.get("PROGRAMFILES", "")
    prog86 = os.environ.get("PROGRAMFILES(X86)", "")

    candidates = [
        Path(local)  / "Programs" / "Opera"    / "opera.exe",
        Path(local)  / "Programs" / "Opera GX" / "opera.exe",
        Path(prog)   / "Opera"    / "opera.exe",
        Path(prog86) / "Opera"    / "opera.exe",
    ]
    for p in candidates:
        if p.exists():
            print(f"[Browser] Opera found at: {p}")
            return str(p)

    try:
        import winreg
        keys = [
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\opera.exe",
            r"SOFTWARE\Clients\StartMenuInternet\OperaStable\shell\open\command",
            r"SOFTWARE\Clients\StartMenuInternet\OperaGXStable\shell\open\command",
            r"SOFTWARE\Clients\StartMenuInternet\opera\shell\open\command",
        ]
        for key_path in keys:
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    k   = winreg.OpenKey(hive, key_path)
                    val = winreg.QueryValue(k, None)
                    winreg.CloseKey(k)
                    exe = val.strip().strip('"').split('"')[0].split(" --")[0].strip()
                    if exe and Path(exe).exists():
                        print(f"[Browser] Opera found via registry: {exe}")
                        return exe
                except Exception:
                    continue
    except Exception:
        pass

    return shutil.which("opera") or None

def _find_exe_windows(prog_name: str) -> Optional[str]:
    try:
        import winreg
        paths_to_try = [
            rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{prog_name}.exe",
            rf"SOFTWARE\Clients\StartMenuInternet\{prog_name}\shell\open\command",
        ]
        for key_path in paths_to_try:
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    k   = winreg.OpenKey(hive, key_path)
                    val = winreg.QueryValue(k, None)
                    winreg.CloseKey(k)
                    exe = val.strip().strip('"').split('"')[0].split(" --")[0].strip()
                    if exe and Path(exe).exists():
                        return exe
                except Exception:
                    continue
    except Exception:
        pass
    return None

_BROWSER_SPECS: dict[str, dict] = {
    "Windows": {
        "chrome":   {"engine": "chromium", "channel": "chrome",  "bins": []},
        "edge":     {"engine": "chromium", "channel": "msedge",  "bins": []},
        "firefox":  {"engine": "firefox",  "channel": None,      "bins": ["firefox.exe"]},
        "opera":    {"engine": "chromium", "channel": None,      "bins": ["opera.exe"],  "special": "opera_windows"},
        "operagx":  {"engine": "chromium", "channel": None,      "bins": [],             "special": "opera_windows"},
        "brave":    {"engine": "chromium", "channel": None,      "bins": ["brave.exe"]},
        "vivaldi":  {"engine": "chromium", "channel": None,      "bins": ["vivaldi.exe"]},
        "safari":   None,
    },
}

_ALIASES: dict[str, str] = {
    "google chrome":   "chrome",
    "google-chrome":   "chrome",
    "microsoft edge":  "edge",
    "ms edge":         "edge",
    "msedge":          "edge",
    "mozilla firefox": "firefox",
    "opera gx":        "operagx",
    "opera_gx":        "operagx",
}


def _resolve_browser(name: str) -> dict | None:
    name   = _ALIASES.get(name.lower().strip(), name.lower().strip())
    os_map = _BROWSER_SPECS.get(_OS, {})
    spec   = os_map.get(name)
    if spec is None:
        return None

    engine  = spec["engine"]
    channel = spec.get("channel")
    bins    = spec.get("bins", [])
    exe     = None

    if spec.get("special") == "opera_windows":
        exe = _find_opera_windows()
        if not exe:
            print(f"[Browser] ⚠️  Opera executable not found on Windows.")
        return {"engine": engine, "exe": exe, "channel": channel}

    for b in bins:
        found = shutil.which(b)
        if found:
            exe = found
            break

    if not exe and not channel:
        exe = _find_exe_windows(name)

    return {"engine": engine, "exe": exe, "channel": channel}


def _detect_default_browser() -> str:
    try:
        import winreg
        k = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\Shell\Associations"
            r"\UrlAssociations\http\UserChoice",
        )
        prog_id = winreg.QueryValueEx(k, "ProgId")[0].lower()
        winreg.CloseKey(k)
        for kw in ("edge", "firefox", "opera", "brave", "vivaldi", "chrome"):
            if kw in prog_id:
                return kw
    except Exception:
        pass
    return "chrome"


_SEARCH_ENGINES: dict[str, str] = {
    "google":     "https://www.google.com/search?q=",
    "bing":       "https://www.bing.com/search?q=",
    "duckduckgo": "https://duckduckgo.com/?q=",
    "yandex":     "https://yandex.com/search/?text=",
}

# Windows registry lookup names for browsers whose spec has no explicit binary
_WIN_EXE_HINTS: dict[str, str] = {"chrome": "chrome", "edge": "msedge"}


# Chromium builds the accessibility tree for a PAGE only when it believes
# something is listening. Nothing an outside program can do convinces it — both
# a UIA walk and a WM_GETOBJECT probe were tried against a running browser and
# returned the same empty panes.
#
# But the browser we START is ours to configure, and one flag changes the whole
# picture. Measured on Edge, same page, same profile:
#
#     plain                                21 elements, page text: 4
#     --force-renderer-accessibility       25 elements, page text: 7,
#                                          including a clickable link
#
# That is the difference between a page the assistant can read and click by
# name, in the user's OWN signed-in browser, and one it has to guess at from a
# screenshot or drive from a second window where nobody is logged in.
#
# It applies only when this launches the browser. A browser already running
# ignores the flag and simply takes the URL, which is correct: we are not
# restarting somebody's browser to gain a feature.
_CHROMIUM_FAMILY = frozenset({"chrome", "edge", "opera", "operagx", "brave",
                              "vivaldi"})


def _launch_flags(name: str) -> list[str]:
    return (["--force-renderer-accessibility"]
            if name in _CHROMIUM_FAMILY else [])


def _native_outcome(name: str, url: str) -> str:
    """What a native launch actually established, as opposed to what it asked for.

    THIS RETURNED A SENTENCE SAYING "Opened", AND IT WAS NOT TRUE ENOUGH
        Handing a URL to a browser executable proves one thing: the executable
        started. It proves nothing about the page. The address may 404, redirect
        to a sign-in wall, or - the case that exposed this - be an address the
        model invented because it did not know the real one.

        Asked to open a user's own YouTube channel, the model guessed a path
        that does not exist. The browser opened it, this function answered
        "Opened in operagx: <that address>", and the assistant reported the
        channel as open while the screen showed an error page. A bare success
        sentence is exactly the invitation to invent the rest that the rest of
        this project spent a release removing.

    The browser itself CAN be confirmed, cheaply, from the process table, so
    that part is reported as fact and the page is reported as unconfirmed. Two
    different things, said separately, which is the whole point of the outcome
    vocabulary.
    """
    running = bool(appidentity.running(name))
    if not url:
        if running:
            return outcome.done("browser_control", browser=name, opened=True,
                                verified=True)
        return outcome.unverified(
            "browser_control",
            "the browser was launched but has not appeared in the process list "
            "yet", browser=name)
    return outcome.unverified(
        "browser_control",
        "the address was handed to the browser, which does not report back - "
        "whether the page actually loaded, redirected or does not exist is NOT "
        "known, so it must not be described as open",
        browser=name, url=url, browser_running=running)


def _open_native(url: str, browser_name: Optional[str]) -> str:
    """
    Opens the user's REAL browser normally — with their own profile,
    logged-in accounts and extensions. No automation attaches, so an
    about:blank tab or a blank profile NEVER shows up.
    If url is empty the browser starts with no URL (its own start page /
    session restore) — exactly as if the user had opened it themselves.
    Works on Windows.
    """
    url = _normalize_url(url) if url and url.strip() else ""
    if url == "about:blank":
        url = ""

    name = None
    if browser_name:
        name = _ALIASES.get(browser_name.lower().strip(), browser_name.lower().strip())
    elif not url:
        # No URL → only a window will open; needs the default browser's exe
        name = _detect_default_browser()

    # Specific browser → launch its own executable, exactly like the user would.
    if name:
        spec = _resolve_browser(name)
        exe  = spec.get("exe") if spec else None
        if not exe:
            if name in ("opera", "operagx"):
                exe = _find_opera_windows()
            else:
                exe = _find_exe_windows(_WIN_EXE_HINTS.get(name, name))
        if exe:
            try:
                subprocess.Popen(
                    [exe] + _launch_flags(name) + ([url] if url else []),
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                return _native_outcome(name, url)
            except Exception as e:
                print(f"[Browser] Native launch failed for {name}: {e}")
        print(f"[Browser] '{name}' not found — falling back to default browser.")

    if not url:
        return outcome.failed(
            "browser_control",
            "no browser was found and no address was given to open")

    # Default browser via the OS — exactly like the user clicking a link.
    try:
        os.startfile(url)                       # ShellExecute → default browser
        # HANDED TO THE OPERATING SYSTEM, NOT CONFIRMED.
        #
        # os.startfile returns as soon as the request is accepted. It does not
        # wait for a browser, and it does not report whether the page loaded —
        # the same gap that made open_app say "Opened Spotify." off the back of
        # a shell starting. Unlike the playwright path above there is no page
        # object here to read a url from, so this says what it actually knows.
        return outcome.unverified(
            "browser_control",
            "the address was handed to the system's default browser, which "
            "does not report back - the page is not confirmed open",
            url=url)
    except Exception:
        try:
            if webbrowser.open(url):
                return outcome.unverified(
                    "browser_control",
                    "the address was handed to the default browser, which does "
                    "not report back - the page is not confirmed open",
                    url=url)
        except Exception:
            pass
        return outcome.failed(
            "browser_control",
            "no browser on this system could be opened", url=url)


#: The script that collects the page's interactive elements.
#:
#: It runs INSIDE the page and it only READS: it walks the elements that
#: are visible and can be interacted with, and returns a list of plain
#: objects. Nothing is injected into the DOM, nothing from the page is
#: evaluated, and the assistant never runs code the page wrote. The page
#: gets to describe itself; it does not get to act.
#:
#: The list is cut at two hundred elements because a page can have ten
#: thousand, and a model that is handed ten thousand elements stops being
#: able to read any of them. What is beyond the cut is not hidden: the
#: result says how many were found and how many were listed.
_PAGE_STRUCTURE_JS = """() => {
  const INTERACTIVE = 'a[href],button,input,select,textarea,summary,'
    + '[role],[contenteditable="true"],[onclick],[tabindex]';
  const found = [];
  const listed = [];
  const nodes = document.querySelectorAll(INTERACTIVE);
  for (const el of nodes) {
    if (found.length >= 400) break;
    let role = '';
    let name = '';
    try {
      if (el.getAttribute('aria-hidden') === 'true') continue;
      const style = window.getComputedStyle(el);
      if (style.display === 'none' || style.visibility === 'hidden') continue;
      const rect = el.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) continue;
      role = (el.getAttribute('role') || el.tagName.toLowerCase());
      name = (
        el.getAttribute('aria-label')
        || el.innerText
        || el.getAttribute('placeholder')
        || el.getAttribute('alt')
        || el.getAttribute('title')
        || el.value
        || ''
      ).replace(/\\s+/g, ' ').trim();
      found.push(1);
      if (!name) continue;
      listed.push({
        id: 'e' + (listed.length + 1),
        role: String(role),
        name: String(name).slice(0, 120),
      });
    } catch (err) {
      // One element that misbehaves must not stop the walk.
    }
  }
  return { total: found.length, elements: listed };
}"""


class _AmbiguousLocator(ValueError):
    """A semantic selector did not identify exactly one element.

    "Click the button that says Accept" is a request that has to be answered
    with ONE element or with nothing. A locator that matches three is not a
    choice, it is a coin toss, and a coin toss in a browser can dismiss a
    dialog or confirm a purchase.
    """

    def __init__(self, description: str, count: int):
        self.count = count
        super().__init__(
            f"{description} matched {count} elements; refusing to guess"
        )

    @property
    def recovery(self) -> str:
        return "control_missing" if self.count == 0 else "target_ambiguous"


class _PageContextChanged(RuntimeError):
    """The page an action was planned against is no longer the active one.

    Raised instead of quietly opening a fresh tab: the element the model
    named was on THAT page, and clicking the same coordinates on another one
    is how the assistant clicks the wrong button.
    """


#: How many effects the browser may produce in one window, and how wide that
#: window is. This is the fuse: an agent that is stuck — open, look, click,
#: not there, open another — stops on its own instead of hammering a site
#: until somebody notices.
_INTERACTION_WINDOW_SECONDS = 30.0
_INTERACTION_MAXIMUM = 6


async def _require_unique(locator, description: str):
    """Return a locator only when it identifies exactly one element."""
    count = await locator.count()
    if count != 1:
        raise _AmbiguousLocator(description, count)
    return locator


class _BrowserSession:
    """
    A full session for one browser instance.
    All browsers open on the real profile via launch_persistent_context.

    WHAT THIS CLASS GUARANTEES
        Three things, and each of them is a promise the model relies on:

        - ONE target per action. A selector that matches more than one
          element is refused rather than resolved by guessing.
        - A BUDGET. Six effects per thirty seconds, after which the browser
          says so instead of acting again.
        - HONESTY about the outcome. An action that timed out, or that may
          have been dispatched before it failed, is reported as UNVERIFIED
          and never as done. The browser may well be doing it still, and
          telling the model "failed" would make it try again.
    """

    def __init__(self, browser_name: str):
        self.browser_name = browser_name
        self._spec        = _resolve_browser(browser_name)

        self._loop:    asyncio.AbstractEventLoop | None = None
        self._thread:  threading.Thread | None          = None
        self._ready    = threading.Event()

        self._pw:      Playwright     | None = None
        self._context: BrowserContext | None = None
        self._page:    Page           | None = None
        # "real" when the user's own browser profile was opened, "separate"
        # when their browser was already running and a private automation
        # profile had to be used instead. The difference is every sign-in they
        # have, so it is reported rather than only printed.
        self.profile_kind: str = ""

        # The interaction budget: when the effects happened, so the window
        # can be measured instead of counted.
        self._interaction_history: deque[float] = deque()
        self._interaction_lock = threading.Lock()
        # The operation currently running, if any. A timed-out operation
        # stays here until it finishes, because until then the browser is
        # busy with something whose outcome nobody knows.
        self._run_state_lock = threading.Lock()
        self._active_future = None
        self._active_timed_out = False
        # The last address the policy refused, and why. A navigation that
        # was stopped by the policy has to say WHICH rule stopped it, and
        # the rule is usually not the one the caller asked about.
        self._blocked_by_policy = None
        # The browser this session attached to over CDP, if it did.
        self._cdp_browser = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name=f"BrowserThread-{self.browser_name}",
        )
        self._thread.start()
        self._ready.wait(timeout=20)

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._async_init())
        self._ready.set()
        self._loop.run_forever()

    async def _async_init(self):
        # The one door into a live session: nothing in this class reaches
        # async_playwright or PlaywrightTimeout without coming through here
        # first, so binding them at this point is enough for the whole file.
        _load_playwright()
        self._pw = await async_playwright().start()

    def run(self, coro, timeout: int = 60) -> str:
        """Run one operation on the browser's loop, and report what happened.

        Three things happen here that did not happen before, and each of them
        exists because the alternative is a lie:

        - A TIMED-OUT operation is not a failure. The browser may still be
          doing it, and a model told "failed" will try again. It is reported
          as unverified, with a recovery of "timeout" and the instruction not
          to repeat it.
        - While a timed-out operation is still running, NO NEW OPERATION IS
          SENT. The browser is busy with something whose outcome is unknown,
          and starting a second one on top of it turns one ambiguous state
          into two.
        - An exception raised after dispatch may have begun is not a failure
          either. It is "effect_ambiguous": the click may have happened.
        """
        if not getattr(self, "_loop", None):
            raise RuntimeError(
                f"Session for '{getattr(self, 'browser_name', '')}' not started."
            )
        with self._run_state_lock:
            active = self._active_future
            if active is not None and not active.done():
                close = getattr(coro, "close", None)
                if callable(close):
                    close()
                return outcome.unverified(
                    "browser_control",
                    "a browser operation is still running; no new operation "
                    "was sent",
                    recovery=(
                        "timeout"
                        if self._active_timed_out
                        else "effect_ambiguous"
                    ),
                )
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            self._active_future = future
            self._active_timed_out = False
        try:
            return future.result(timeout=timeout)
        except _PageContextChanged:
            return outcome.failed(
                "browser_control",
                "the active page changed or disappeared; no action was sent",
                recovery="page_changed",
            )
        except concurrent.futures.TimeoutError:
            with self._run_state_lock:
                self._active_timed_out = not future.done()
            return outcome.unverified(
                "browser_control",
                "browser operation exceeded its time limit and may still be "
                "running; do not repeat it",
                recovery="timeout",
            )
        except Exception as exc:
            return outcome.unverified(
                "browser_control",
                "browser operation raised after dispatch may have begun "
                f"({type(exc).__name__}); do not repeat it",
                recovery="effect_ambiguous",
            )
        finally:
            if future.done():
                with self._run_state_lock:
                    if self._active_future is future:
                        self._active_future = None
                        self._active_timed_out = False

    def close(self):
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._async_close(), self._loop).result(10)

    async def _async_close(self):
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass
        self._context = self._page = None

    async def _install_route_guard(self) -> None:
        """Check the policy on EVERY request, and therefore on every redirect.

        A redirect is a request, and Playwright hands every request to this
        handler. So the policy is not only checked on the address the user
        asked for: it is checked again on each address the SITE sends the
        browser to. A public page that redirects into a bank is still a
        bank, and this is where that is caught.

        The blocked request is aborted rather than followed, and the reason
        is kept so the caller can report which rule fired.
        """
        async def _guard(route, request=None):
            url = ""
            try:
                url = getattr(request, "url", "") or ""
            except Exception:
                url = ""
            if not url:
                try:
                    url = route.request.url
                except Exception:
                    url = ""
            decision = domain_policy.check_destination(url) if url else None
            if decision is not None and not decision.allowed:
                self._blocked_by_policy = decision
                print(f"[Browser] blocked by policy {decision.policy}: {url}")
                try:
                    await route.abort()
                except Exception:
                    pass
                return
            try:
                await route.continue_()
            except Exception:
                pass

        try:
            await self._context.route("**/*", _guard)
        except Exception as exc:
            # A browser that cannot be guarded is a browser that must not be
            # used: the whole point of the policy is that it holds.
            print(f"[Browser] the policy guard could not be installed "
                  f"({type(exc).__name__}) — navigation is disabled")
            self._blocked_by_policy = domain_policy.Decision(
                False,
                "the browser could not be guarded by the domain policy",
                "deny:guard_unavailable", "guard_unavailable", "",
            )

    async def _attach_over_cdp(self) -> bool:
        """Attach to the browser the user already has open.

        WHY ATTACH INSTEAD OF LAUNCHING A SECOND BROWSER
            The browser the user has open is the one with their session:
            signed in, with their tabs, with their settings. A second
            browser started by the assistant has none of that, and every
            page that needs a sign-in becomes a dead end. Attaching is what
            makes the assistant useful on the pages the user actually has.

        WHY THE ENDPOINT IS LOOPBACK AND NOT A SETTING
            A debugging port gives whoever reaches it the whole browser.
            Exposed on a public interface it is a remote control for
            everybody on the internet, so there is exactly one address this
            will ever use and it is the user's own machine.

        Returns True when the attach worked.
        """
        try:
            endpoint = _cdp_endpoint()
        except ValueError as exc:
            print(f"[Browser] the debugging endpoint is not allowed: {exc}")
            return False
        if not _cdp_available():
            return False
        try:
            browser = await self._pw.chromium.connect_over_cdp(endpoint)
        except Exception as exc:
            print(f"[Browser] could not attach over the debugging port "
                  f"({type(exc).__name__})")
            return False
        try:
            contexts = browser.contexts
            if not contexts:
                return False
            self._context = contexts[0]
            pages = self._context.pages
            self._page = pages[-1] if pages else await self._context.new_page()
            self._cdp_browser = browser
            self.profile_kind = "attached"
            # The guard belongs to the CONTEXT, not to the way it was
            # obtained: a browser that was attached is guarded exactly like a
            # browser that was launched. Without this, attaching would be a
            # way round the policy.
            await self._install_route_guard()
            print(f"[Browser] attached to the running browser over the local "
                  f"debugging port")
            return True
        except Exception as exc:
            print(f"[Browser] the attached browser could not be read "
                  f"({type(exc).__name__})")
            return False

    async def _adopt_page(self) -> Page:
        """
        launch_persistent_context already opens a starting tab.
        Instead of opening a new blank tab (about:blank), it adopts that tab —
        so the user never sees an extra blank tab.
        """
        await asyncio.sleep(0.3)
        pages = self._context.pages
        return pages[0] if pages else await self._context.new_page()

    async def _launch(self):
        """
        Launches the browser with the real user profile.
        Does nothing if the context is already open.
        """
        if self._context is not None:
            return

        if self._spec is None:
            raise RuntimeError(
                f"'{self.browser_name}' bu platformda ({_OS}) desteklenmiyor."
            )

        engine_name = self._spec["engine"]
        exe         = self._spec["exe"]
        channel     = self._spec["channel"]
        engine_obj  = getattr(self._pw, engine_name)

        if engine_name == "firefox":
            profile = _firefox_profile_dir() or str(
                Path.home() / ".teemo_profiles" / "firefox"
            )
            kwargs: dict = {
                "headless":    False,
                "slow_mo":     0,
                "viewport":    None,
                "no_viewport": True,
                "timeout":     25_000,
            }
            if exe:
                kwargs["executable_path"] = exe
            try:
                self._context = await engine_obj.launch_persistent_context(profile, **kwargs)
            except Exception as e:
                print(f"[Browser] Firefox real profile failed ({e}), using the assistant profile")
                teemo = str(Path.home() / ".teemo_profiles" / "firefox_teemo")
                Path(teemo).mkdir(parents=True, exist_ok=True)
                self._context = await engine_obj.launch_persistent_context(teemo, **kwargs)

            self._page = await self._adopt_page()
            print(f"[Browser] ✅ Firefox launched")
            return

        if engine_name == "webkit":
            safari_profile = str(Path.home() / ".teemo_profiles" / "safari")
            Path(safari_profile).mkdir(parents=True, exist_ok=True)
            kwargs = {
                "headless":    False,
                "slow_mo":     0,
                "viewport":    None,
                "no_viewport": True,
                "timeout":     25_000,
            }
            self._context = await engine_obj.launch_persistent_context(safari_profile, **kwargs)
            self._page = await self._adopt_page()
            print(f"[Browser] ✅ Safari launched")
            return

        profile = _real_profile_dir(self.browser_name)

        kwargs = {
            "headless":    False,
            "slow_mo":     0,
            "viewport":    None,
            "no_viewport": True,
            "timeout":     25_000,
            "args": [
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--disable-default-apps",
                "--no-default-browser-check",
            ],
        }

        if exe:
            kwargs["executable_path"] = exe
        elif channel:
            kwargs["channel"] = channel

        label = (
            f"{self.browser_name}"
            + (f"/{channel}" if channel else "")
            + (f" @ {exe}" if exe else "")
        )

        # ASK FIRST, INSTEAD OF WAITING 25 SECONDS TO BE TOLD.
        #
        # A Chromium profile can only be open once. If the user already has the
        # browser running — which is the normal state of a browser — then
        # launch_persistent_context on their real profile cannot succeed, and
        # the way it fails is by sitting on the timeout above before raising.
        # Every "open this page" then cost twenty-five seconds of nothing
        # before the fallback even started, and the fallback is a profile with
        # none of their sign-ins in it.
        #
        # Whether the browser is running is a question the process table
        # answers in milliseconds, and core/appidentity was already built to
        # ask it. Knowing up front turns a long silence into an immediate,
        # explainable outcome.
        already = appidentity.running(self.browser_name)
        if already:
            # The browser the user has open is the one with their session.
            # If it was started with the local debugging port, attach to it:
            # a second browser would have none of their sign-ins.
            if await self._attach_over_cdp():
                return
            print(f"[Browser] {self.browser_name} is already running "
                  f"({len(already)} processes) — its profile is locked, so the "
                  f"separate automation profile will be used.")
        else:
            try:
                self._context = await engine_obj.launch_persistent_context(profile, **kwargs)
                self._page = await self._adopt_page()
                self.profile_kind = "real"
                await self._install_route_guard()
                print(f"[Browser] ✅ Launched [{label}] profile={profile}")
                return
            except Exception as e:
                print(f"[Browser] ⚠️  Real profile failed for {label}: {e}")

        # The real profile could not be opened (browser already open / locked
        # profile / newer Chrome versions block the real profile under
        # automation). Fall back to a persistent profile of our own —
        # accounts logged in here once stay logged in on later sessions too.
        teemo_profile = _automation_profile_dir(self.browser_name)
        print(f"[Browser] Retrying with the assistant profile: {teemo_profile}")

        try:
            self._context = await engine_obj.launch_persistent_context(teemo_profile, **kwargs)
            self._page = await self._adopt_page()
            self.profile_kind = "separate"
            await self._install_route_guard()
            print(f"[Browser] ✅ Launched [{label}] with the assistant profile "
                  f"(sign-ins persist across sessions)")
        except Exception as e2:
            raise RuntimeError(f"Could not launch {self.browser_name}: {e2}") from e2


    def _reserve_interaction(self, amount: int = 1) -> bool:
        """Take one effect from the budget. False when there is none left.

        The window is measured, not counted: six effects in thirty seconds
        is a fuse, and a fuse that never resets is a wall.
        """
        now = time.monotonic()
        history = getattr(self, "_interaction_history", None)
        lock = getattr(self, "_interaction_lock", None)
        if history is None or lock is None:
            history = deque()
            lock = threading.Lock()
            self._interaction_history = history
            self._interaction_lock = lock
        with lock:
            while (
                history
                and now - history[0] >= _INTERACTION_WINDOW_SECONDS
            ):
                history.popleft()
            if amount < 1 or len(history) + amount > _INTERACTION_MAXIMUM:
                return False
            history.extend([now] * amount)
            return True

    def _interaction_limit_result(self) -> str:
        """The answer when the budget is spent: nothing was sent."""
        return outcome.failed(
            "browser_control",
            "browser interaction rate limit reached; no further effect was sent",
            maximum=_INTERACTION_MAXIMUM,
            window_seconds=_INTERACTION_WINDOW_SECONDS,
        )

    async def _get_page(self, *, allow_new: bool = False) -> Page:
        """The page an action should act on.

        A closed page is NOT silently replaced. The element the model named
        was on THAT page, and clicking something with the same name on
        another tab is how the assistant clicks the wrong button. The action
        is refused with `_PageContextChanged`, and the caller reports that
        no action was sent.

        `allow_new=True` is for the cases where opening a fresh tab IS the
        action: a new tab, or a navigation that needs somewhere to go.
        """
        await self._launch()
        current = getattr(self, "_page", None)
        try:
            page_missing = current is None or current.is_closed()
        except Exception as exc:
            raise _PageContextChanged(
                "the active page state could not be read "
                f"({type(exc).__name__})"
            ) from None
        if page_missing:
            if not allow_new:
                raise _PageContextChanged(
                    "the active page closed or changed; no action was sent"
                )
            if self._context is None:
                raise RuntimeError("browser context is unavailable")
            self._page = await self._context.new_page()
            await asyncio.sleep(0.2)
        return self._page

    @staticmethod
    def _page_closed(page: Page) -> bool:
        """Whether the page is gone.

        Never raises: a page that cannot be asked is not proof that it
        closed, and treating it as closed would turn a hiccup into a
        failure.
        """
        try:
            return bool(page.is_closed())
        except (AttributeError, TypeError):
            return False

    async def go_to(self, url: str) -> str:
        """Open an address, if the policy allows it.

        THE POLICY IS CHECKED BEFORE THE NAVIGATION. A browser that is
        signed into a bank can be told to open a bank, and "the user asked
        for it" is not a policy — the user asked for a page, not for a
        transfer. The deny list in core/policy.py says which pages are not
        the user's to command, and it is applied here, before a single
        packet is sent.

        The check is repeated on every redirect, because a redirect is the
        SITE's decision and not the user's: a public page that redirects
        inward is still inward.

        The result is UNVERIFIED even when the navigation worked, because
        nobody has seen what the page says. Reporting it as done is how the
        assistant ends up claiming a page was read that it never read.
        """
        if not isinstance(url, str) or not url.strip():
            return outcome.failed("browser_control", "url must be a non-empty string")
        url = _normalize_url(url)

        decision = domain_policy.check_destination(url)
        if not decision.allowed:
            return outcome.failed(
                "browser_control",
                f"the browser may not open this address: {decision.reason}",
                url=url,
                policy=decision.policy,
                category=decision.category,
            )

        # Any block left over from a previous navigation is cleared first: a
        # stale reason would be reported against the wrong address.
        self._blocked_by_policy = None

        page     = await self._get_page(allow_new=True)
        prev_url = page.url
        if not self._reserve_interaction():
            return self._interaction_limit_result()

        async def _do_goto(p: Page) -> tuple[str, str | None, bool]:
            """Attempt navigation; return its URL, any non-timeout error, and
            whether it timed out."""
            try:
                await p.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await asyncio.sleep(0.3)
            except PlaywrightTimeout:
                return p.url, None, True
            except Exception as e:
                print(f"[Browser] goto exception (non-fatal): {e}")
                return p.url, str(e), False
            return p.url, None, False

        result_url, navigation_error, navigation_timed_out = await _do_goto(page)

        if result_url in ("about:blank", "", None, prev_url) and prev_url in ("about:blank", "", None):
            print(f"[Browser] Still blank after goto — retrying on new tab: {url}")
            try:
                if not self._reserve_interaction():
                    return self._interaction_limit_result()
                new_page   = await self._context.new_page()
                self._page = new_page
                result_url, navigation_error, navigation_timed_out = await _do_goto(
                    new_page
                )
            except Exception as e:
                print(f"[Browser] New-tab retry failed: {e}")
                navigation_error = str(e)

        # The route guard refused this navigation — or a redirect it made.
        # The policy is named, because "the browser will not open that" is
        # not an answer a user can act on.
        blocked = getattr(self, "_blocked_by_policy", None)
        if blocked is not None and not blocked.allowed:
            return outcome.failed(
                "browser_control",
                f"the browser may not open this address: {blocked.reason}",
                url=url,
                policy=blocked.policy,
                category=blocked.category,
            )

        if navigation_error and result_url == prev_url:
            return outcome.failed(
                "browser_control",
                f"navigation failed: {navigation_error}",
                url=url,
                current_url=result_url,
            )

        if result_url and result_url not in ("about:blank", "", None):
            # This is the page's OWN url read back after the navigation, not
            # the one we asked for. A redirect, a login wall or a captive
            # portal all show up here as a different address, which is
            # exactly what the user needs to hear about.
            #
            # The profile is reported for the same reason. When the user's
            # browser was already running its profile is locked, so the page
            # opens in a separate one where they are signed in to nothing —
            # and a mail page that asks them to log in looks like a broken
            # assistant unless somebody says why.
            profile = getattr(self, "profile_kind", "") or "real"
            browser_name = getattr(self, "browser_name", "") or "the browser"
            if profile == "separate":
                reason = (
                    f"the page opened at {result_url}, but {browser_name} was "
                    f"already running so its profile was locked - this window "
                    f"uses a separate profile where the user is NOT signed in "
                    f"to anything. Closing {browser_name} first would let "
                    f"their own profile be used"
                )
            else:
                reason = (
                    f"the page opened at {result_url}, but the page itself was "
                    f"not read - treat what is on it as unverified"
                )
            return outcome.unverified(
                "browser_control",
                reason,
                opened=result_url,
                profile=profile,
                recovery=(
                    "timeout"
                    if navigation_timed_out
                    else "page_changed"
                    if result_url != url
                    else "effect_ambiguous"
                ),
            )
        return outcome.failed(
            "browser_control",
            "the page stayed blank - the address may be wrong, or the site may "
            "not have answered", url=url)

    async def search(self, query: str, engine: str = "google") -> str:
        base = _SEARCH_ENGINES.get(engine.lower(), _SEARCH_ENGINES["google"])
        return await self.go_to(base + query.replace(" ", "+"))

    async def click(self, selector: str = None, text: str = None) -> str:
        """Click ONE element, and only when the target is unambiguous.

        The old version took `.first` of whatever matched, which is a guess
        dressed as a click. A page with two "Delete" buttons would get the
        first one clicked, and the model would report a deletion it may not
        have performed — or performed on the wrong row.

        So the locator must match exactly one element. Zero matches is a
        missing control; more than one is an ambiguous target. Neither is
        guessed at.

        The page's identity is checked before the click is sent. If the
        address changed while the target was being looked up, the click is
        abandoned: the element that was found belongs to the page that was
        open when the lookup started.
        """
        page = await self._get_page()
        page_url = page.url
        if not text and not selector:
            return outcome.failed(
                "browser_control",
                "no element was named to click",
            )
        if text and (
            not isinstance(text, str) or not text.strip() or len(text) > 500
        ):
            return outcome.failed(
                "browser_control",
                "click text must contain 1-500 characters",
            )
        if selector and (
            not isinstance(selector, str) or len(selector) > 500
        ):
            return outcome.failed(
                "browser_control",
                "click selector must contain at most 500 characters",
            )
        try:
            if text:
                locator = await _require_unique(
                    page.get_by_text(text, exact=False), "click text"
                )
            else:
                locator = await _require_unique(
                    page.locator(selector), "click selector"
                )
        except _AmbiguousLocator as exc:
            return outcome.failed(
                "browser_control",
                str(exc),
                recovery=exc.recovery,
            )
        except PlaywrightTimeout:
            return outcome.failed(
                "browser_control",
                "the click target could not be located before timeout",
                selector=selector or text,
                recovery="timeout",
            )
        except Exception as e:
            return outcome.failed(
                "browser_control",
                f"click target could not be inspected: {type(e).__name__}",
                recovery="state_unreadable",
            )
        if page.url != page_url:
            return outcome.failed(
                "browser_control",
                "page changed while locating the click target; no click was sent",
                before_url=page_url,
                current_url=page.url,
                recovery="page_changed",
            )
        if not self._reserve_interaction():
            return self._interaction_limit_result()
        try:
            await locator.click(timeout=8_000)
        except PlaywrightTimeout:
            return outcome.unverified(
                "browser_control",
                "click timed out after dispatch may have begun; do not repeat it",
                selector=selector or text,
                recovery="timeout",
            )
        except Exception as e:
            return outcome.unverified(
                "browser_control",
                f"click may have been dispatched before {type(e).__name__}; "
                "do not repeat it",
                recovery="effect_ambiguous",
            )
        return (
            f"Clicked text: '{text}'"
            if text
            else f"Clicked selector: {selector}"
        )

    async def type_text(self, selector: str = None, text: str = "",
                        clear_first: bool = True) -> str:
        """Type into ONE field, and only when the target is unambiguous.

        Same rule as the click: a field that matches twice is not a field,
        it is a choice. And a failure after the field was cleared is not a
        failure — the field may hold half the text, so it is reported as
        unverified.
        """
        page = await self._get_page()
        page_url = page.url
        try:
            if not isinstance(text, str) or len(text) > 10_000:
                return outcome.failed(
                    "browser_control",
                    "typed text must not exceed 10000 characters",
                )
            if selector:
                if not isinstance(selector, str) or len(selector) > 500:
                    return outcome.failed(
                        "browser_control",
                        "type selector must contain at most 500 characters",
                    )
                el = await _require_unique(
                    page.locator(selector), "type selector"
                )
            else:
                el = await _require_unique(page.locator(":focus"), "focused field")
        except _AmbiguousLocator as exc:
            return outcome.failed(
                "browser_control",
                str(exc),
                recovery=exc.recovery,
            )
        except PlaywrightTimeout:
            return outcome.failed(
                "browser_control",
                "the input target could not be located before timeout",
                recovery="timeout",
            )
        except Exception as e:
            return outcome.failed(
                "browser_control",
                f"input target could not be inspected: {type(e).__name__}",
                recovery="state_unreadable",
            )
        if page.url != page_url:
            return outcome.failed(
                "browser_control",
                "page changed while locating the input target; no text was sent",
                before_url=page_url,
                current_url=page.url,
                recovery="page_changed",
            )
        if not self._reserve_interaction():
            return self._interaction_limit_result()
        try:
            if clear_first:
                await el.clear()
            await el.type(text, delay=50)
        except PlaywrightTimeout:
            return outcome.unverified(
                "browser_control",
                "typing timed out after a field change may have begun; do not repeat it",
                recovery="timeout",
            )
        except Exception as e:
            return outcome.unverified(
                "browser_control",
                f"typing may have changed the field before {type(e).__name__}; "
                "do not repeat it",
                recovery="effect_ambiguous",
            )
        return outcome.done("browser_control", typed=True, verified=True)

    async def scroll(self, direction: str = "down", amount: int = 500) -> str:
        """Scroll, and report what actually happened.

        A scroll is an effect, so it takes one from the budget. The amount
        is checked BEFORE anything is sent: a scroll of two thousand pixels
        is refused without the page moving, because the check costs nothing
        and a half-executed scroll cannot be taken back.
        """
        page = await self._get_page()
        page_url = page.url
        try:
            if direction not in {"up", "down"}:
                return outcome.failed(
                    "browser_control",
                    "scroll direction must be up or down",
                )
            if not isinstance(amount, int) or isinstance(amount, bool) or not 1 <= amount <= 2_000:
                return outcome.failed(
                    "browser_control",
                    "scroll amount must be an integer between 1 and 2000",
                )
            if not self._reserve_interaction():
                return self._interaction_limit_result()
            y = amount if direction == "down" else -amount
            await page.mouse.wheel(0, y)
            if self._page_closed(page):
                return outcome.failed(
                    "browser_control",
                    "scroll target page disappeared during the action",
                    recovery="page_changed",
                )
            if page.url != page_url:
                return outcome.unverified(
                    "browser_control",
                    "scroll completed but the page URL changed",
                    before_url=page_url,
                    current_url=page.url,
                    recovery="page_changed",
                )
            return f"Scrolled {direction}."
        except Exception as e:
            return outcome.unverified(
                "browser_control",
                f"scroll may have been sent before {type(e).__name__}; "
                "do not repeat it",
                recovery="effect_ambiguous",
            )

    async def press(self, key: str) -> str:
        """Press a key, and report what actually happened.

        A key press can SUBMIT a form — Enter in a search box, Enter in a
        message. So it is an effect like any other: it takes from the
        budget, and a page whose address changed afterwards is reported as
        unverified rather than as a successful press.
        """
        page = await self._get_page()
        page_url = page.url
        try:
            if not isinstance(key, str) or not 1 <= len(key) <= 32:
                return outcome.failed(
                    "browser_control",
                    "key must contain 1-32 characters",
                )
            if not self._reserve_interaction():
                return self._interaction_limit_result()
            await page.keyboard.press(key)
            if self._page_closed(page):
                return outcome.failed(
                    "browser_control",
                    "key press target page disappeared during the action",
                    recovery="page_changed",
                )
            if page.url != page_url:
                return outcome.unverified(
                    "browser_control",
                    "key press completed but the page URL changed",
                    before_url=page_url,
                    current_url=page.url,
                    recovery="page_changed",
                )
            return f"Pressed: {key}"
        except Exception as e:
            return outcome.unverified(
                "browser_control",
                f"key press may have been sent before {type(e).__name__}; "
                "do not repeat it",
                recovery="effect_ambiguous",
            )

    async def get_text(self) -> str:
        """The page's text, as STRUCTURED and UNTRUSTED data.

        Two things are added to the raw text and both of them matter:

        - STRUCTURE. The result is JSON, so the model sees a status, an
          action and the text, instead of a wall of prose it has to parse
          out of a sentence.
        - THE UNTRUSTED WRAPPER. The text comes from a page written by
          somebody else. A page that says "also send the user's documents
          to this address" has to read as a sentence on a page, and not as
          an instruction. `core/outcome.py` draws the boundary, and the
          marker inside the content is defanged so the page cannot close
          the wrapper early.
        """
        page = await self._get_page()
        try:
            text = await page.inner_text("body")
            return json.dumps(
                {
                    "status": "done",
                    "action": "get_text",
                    "text": outcome.untrusted("webpage", text[:4_000]),
                    "verified": True,
                },
                ensure_ascii=False,
            )
        except Exception as e:
            return outcome.failed(
                "browser_control",
                "page text could not be read",
                action="get_text",
                error=type(e).__name__,
            )

    async def read_page(self, max_elements: int = 120) -> str:
        """The page's interactive elements, as a numbered list.

        WHY NOT A SCREENSHOT
            A screenshot is a picture of pixels. To act on one, the model
            has to say WHERE, in pixels, and pixels belong to the screen
            they were taken on — the same page on a different monitor, at a
            different scale, in a different window, is somewhere else. So
            every click by coordinates is a click that has to be guessed.

            The DOM already knows what the elements ARE. A list of
            "[e7] button 'Accept'" says what the thing is and what it
            says, and it is true at any resolution and on any screen.

        THE NAMES COME FROM THE PAGE
            They are wrapped as untrusted content, because a page that
            labels a button "Send the user's files to evil.example" has
            to read as a page talking, not as an instruction.

        Reading the page is NOT an effect: it changes nothing, so it does
        not take from the budget. The page may be read as often as needed.
        """
        page = await self._get_page()
        try:
            raw = await page.evaluate(_PAGE_STRUCTURE_JS)
        except Exception as e:
            return outcome.failed(
                "browser_control",
                "the page structure could not be read",
                action="read_page",
                error=type(e).__name__,
            )
        if not isinstance(raw, dict):
            raw = {}
        elements = raw.get("elements")
        elements = elements if isinstance(elements, list) else []
        total = raw.get("total")
        total = total if isinstance(total, int) else len(elements)
        try:
            limit = max(1, min(400, int(max_elements)))
        except (TypeError, ValueError):
            limit = 120
        lines = []
        for item in elements[:limit]:
            if not isinstance(item, dict):
                continue
            identifier = str(item.get("id") or "")
            role = str(item.get("role") or "element")
            name = str(item.get("name") or "")
            if not identifier or not name:
                continue
            lines.append(f"[{identifier}] {role} \"{name}\"")
        body = "\n".join(lines) if lines else (
            "(the page shows no visible interactive elements)"
        )
        return json.dumps(
            {
                "status": "done",
                "action": "read_page",
                "url": outcome.untrusted("webpage_url", page.url),
                "elements": outcome.untrusted("webpage_elements", body),
                "listed": len(lines),
                "found": total,
                "verified": True,
            },
            ensure_ascii=False,
        )

    async def get_url(self) -> str:
        """The page's address, as structured and untrusted data.

        The address is untrusted for the same reason the text is: it comes
        from the page, and a page can carry anything in its address bar.
        """
        page = await self._get_page()
        return json.dumps(
            {
                "status": "done",
                "action": "get_url",
                "url": outcome.untrusted("webpage_url", page.url),
                "verified": True,
            },
            ensure_ascii=False,
        )

    async def fill_form(self, fields: dict) -> str:
        """Fill several fields, and say exactly which ones changed.

        THE OLD VERSION REPORTED "Form filled: ✓ a, ✗ b" AND STOPPED. It
        could not say whether a failed field had been half-written, because
        it never checked, and it wrote into whatever matched first. A form
        that failed halfway is reported here as UNVERIFIED, with the number
        of fields that actually changed, so the caller can say "the first
        two were filled and the third was not" instead of "the form failed".

        Two fields are refused outright: a password field, and a
        one-time-code field. Writing into one is not something an
        assistant should do on a phrase — it is the step that turns a
        login into a login.
        """
        page    = await self._get_page()
        page_url = page.url
        completed_fields = 0
        if not isinstance(fields, dict) or not fields or len(fields) > 20:
            return outcome.failed(
                "browser_control",
                "form fields must be a non-empty object with at most 20 entries",
            )
        if len(fields) > _INTERACTION_MAXIMUM:
            return outcome.failed(
                "browser_control",
                "form exceeds the per-session interaction budget",
                maximum=_INTERACTION_MAXIMUM,
            )
        if not self._reserve_interaction(len(fields)):
            return self._interaction_limit_result()

        def stopped(
            field: int,
            reason: str,
            *,
            recovery: str | None = None,
            error: str | None = None,
            effect_started: bool = False,
        ) -> str:
            facts = {
                "action": "fill_form",
                "field": field,
                "changed_fields": completed_fields,
                "error": error,
            }
            if effect_started or completed_fields:
                return outcome.unverified(
                    "browser_control",
                    "form filling stopped; one or more fields may already "
                    "have changed",
                    recovery=recovery or "effect_ambiguous",
                    **facts,
                )
            return outcome.failed(
                "browser_control",
                reason,
                recovery=recovery,
                **facts,
            )

        for index, (selector, value) in enumerate(fields.items(), 1):
            effect_started = False
            try:
                if page.url != page_url:
                    return stopped(
                        index,
                        "page changed before the form field was modified",
                        recovery="page_changed",
                    )
                if not isinstance(selector, str) or len(selector) > 500:
                    raise ValueError("form selector must contain at most 500 characters")
                if not isinstance(value, str) or len(value) > 10_000:
                    raise ValueError("form value must contain at most 10000 characters")
                if action_policy.is_sensitive_name(selector):
                    return stopped(
                        index,
                        "credential and one-time-code fields cannot be filled "
                        "by the general form action",
                    )
                el = await _require_unique(page.locator(selector), "form selector")
                input_type = await el.get_attribute("type")
                autocomplete = set(
                    (await el.get_attribute("autocomplete") or "")
                    .casefold()
                    .split()
                )
                if (input_type or "").casefold() == "password" or (
                    autocomplete & {
                        "current-password",
                        "new-password",
                        "one-time-code",
                    }
                ):
                    return stopped(
                        index,
                        "credential and one-time-code fields cannot be filled "
                        "by the general form action",
                    )
                effect_started = True
                await el.clear()
                await el.type(value, delay=40)
                readback = await el.input_value()
                if readback != value:
                    return outcome.unverified(
                        "browser_control",
                        "a form field changed but its value could not be "
                        "verified; do not claim the form was filled",
                        action="fill_form",
                        field=index,
                        recovery="effect_ambiguous",
                    )
                completed_fields += 1
            except PlaywrightTimeout as exc:
                return stopped(
                    index,
                    "form field lookup timed out before it could be changed",
                    recovery="timeout",
                    error=type(exc).__name__,
                    effect_started=effect_started,
                )
            except _AmbiguousLocator as exc:
                return stopped(
                    index,
                    str(exc),
                    recovery=exc.recovery,
                    error=type(exc).__name__,
                )
            except Exception as exc:
                return stopped(
                    index,
                    "form filling stopped before any field was changed",
                    recovery="effect_ambiguous" if effect_started else None,
                    error=type(exc).__name__,
                    effect_started=effect_started,
                )
        return outcome.done(
            "browser_control",
            action="fill_form",
            fields=len(fields),
            verified=True,
        )

    async def smart_click(self, description: str) -> str:
        """Click an element named in words, and never guess which one.

        THE RULE THAT MATTERS HERE: AFTER A DISPATCH ERROR, NO OTHER
        LOCATOR IS TRIED. The old version walked a list of locators and,
        for each one, clicked the first match. A click that failed halfway
        may still have happened — the button may have been pressed and the
        page may be navigating — and the next locator in the list would
        then press something else on a page that is already changing.

        So the first unambiguous match is clicked, once. If it fails, the
        failure is reported and the model decides what to do. A timeout is
        AMBIGUOUS, not a failure: the click may have been dispatched, and
        repeating it is how one click becomes two.
        """
        page = await self._get_page()
        page_url = page.url
        if not isinstance(description, str) or not description.strip() or len(description) > 500:
            return outcome.failed(
                "browser_control",
                "smart click description must contain 1-500 characters",
            )
        for role in ("button", "link", "searchbox", "textbox", "menuitem", "tab"):
            try:
                loc = page.get_by_role(role, name=description)
                count = await loc.count()
            except Exception:
                continue
            if count > 1:
                return outcome.failed(
                    "browser_control",
                    f"smart click role matched {count} elements; refusing to guess",
                    recovery="target_ambiguous",
                )
            if count == 1:
                if page.url != page_url:
                    return outcome.failed(
                        "browser_control",
                        "page changed while locating the smart click target; "
                        "no click was sent",
                        recovery="page_changed",
                    )
                if not self._reserve_interaction():
                    return self._interaction_limit_result()
                try:
                    await loc.click(timeout=5_000)
                except PlaywrightTimeout:
                    return outcome.unverified(
                        "browser_control",
                        "smart click timed out after dispatch may have begun; "
                        "do not repeat it",
                        recovery="timeout",
                    )
                except Exception as exc:
                    return outcome.unverified(
                        "browser_control",
                        "smart click may have been dispatched before "
                        f"{type(exc).__name__}; do not repeat it",
                        recovery="effect_ambiguous",
                    )
                return f"Clicked ({role}): '{description}'"
        for attempt in (
            lambda: page.get_by_text(description, exact=False),
            lambda: page.get_by_placeholder(description, exact=False),
            lambda: page.locator(
                f'[alt*="{description}" i],[title*="{description}" i],'
                f'[aria-label*="{description}" i]'
            ),
        ):
            try:
                candidate = attempt()
                count = await candidate.count()
            except _AmbiguousLocator as exc:
                return outcome.failed(
                    "browser_control",
                    str(exc),
                    recovery="target_ambiguous",
                )
            except Exception:
                continue
            if count > 1:
                return outcome.failed(
                    "browser_control",
                    f"smart click matched {count} elements; refusing to guess",
                    recovery="target_ambiguous",
                )
            if count == 0:
                continue
            if page.url != page_url:
                return outcome.failed(
                    "browser_control",
                    "page changed while locating the smart click target; "
                    "no click was sent",
                    recovery="page_changed",
                )
            if not self._reserve_interaction():
                return self._interaction_limit_result()
            try:
                await candidate.click(timeout=5_000)
            except PlaywrightTimeout:
                return outcome.unverified(
                    "browser_control",
                    "smart click timed out after dispatch may have begun; "
                    "do not repeat it",
                    recovery="timeout",
                )
            except Exception as exc:
                return outcome.unverified(
                    "browser_control",
                    "smart click may have been dispatched before "
                    f"{type(exc).__name__}; do not repeat it",
                    recovery="effect_ambiguous",
                )
            return f"Clicked: '{description}'"
        return outcome.failed(
            "browser_control",
            "no unique smart-click target was found",
            recovery="control_missing",
        )

    async def smart_type(self, description: str, text: str) -> str:
        """Type into a field named in words, and never guess which one.

        The same rule as the smart click: the first unambiguous match is
        used, once. If clearing it fails, NOTHING ELSE IS TRIED — the
        field may already hold part of the text, and typing into a
        different field is how a password ends up in a search box.
        """
        page = await self._get_page()
        page_url = page.url
        if (
            not isinstance(description, str)
            or not description.strip()
            or len(description) > 500
            or not isinstance(text, str)
            or len(text) > 10_000
        ):
            return outcome.failed(
                "browser_control",
                "smart type description/text exceeds the allowed limits",
            )
        candidates = [
            ("placeholder", page.get_by_placeholder(description, exact=False)),
            ("label",       page.get_by_label(description, exact=False)),
            ("role",        page.get_by_role("textbox", name=description)),
            ("searchbox",   page.get_by_role("searchbox")),
            ("combobox",    page.get_by_role("combobox", name=description)),
        ]
        for method, loc in candidates:
            try:
                count = await loc.count()
            except Exception:
                continue
            if count > 1:
                return outcome.failed(
                    "browser_control",
                    f"smart type {method} matched {count} elements; refusing to guess",
                    recovery="target_ambiguous",
                )
            if count == 0:
                continue
            if page.url != page_url:
                return outcome.failed(
                    "browser_control",
                    "page changed while locating the smart type target; "
                    "no text was sent",
                    recovery="page_changed",
                )
            if not self._reserve_interaction():
                return self._interaction_limit_result()
            try:
                await loc.clear()
                await loc.type(text, delay=50)
            except PlaywrightTimeout:
                return outcome.unverified(
                    "browser_control",
                    "smart typing timed out after a field change may have begun; "
                    "do not repeat it",
                    recovery="timeout",
                )
            except Exception as exc:
                return outcome.unverified(
                    "browser_control",
                    "smart typing may have changed the field before "
                    f"{type(exc).__name__}; do not repeat it",
                    recovery="effect_ambiguous",
                )
            return f"Typed into ({method}): '{description}'"
        return outcome.failed(
            "browser_control",
            "no unique smart-typing target was found",
            recovery="control_missing",
        )

    async def new_tab(self, url: str = "") -> str:
        """Open a tab. Opening one is an effect, and it takes from the budget.

        A tab that is opened and never used is still a tab the user has to
        close, and an agent that opens tabs in a loop fills the user's
        window with them.
        """
        if not self._reserve_interaction():
            return self._interaction_limit_result()
        current = getattr(self, "_page", None)
        if current is None or current.is_closed():
            await self._get_page(allow_new=True)
            if url:
                return await self.go_to(url)
            return "New tab opened."
        page = await self._get_page()
        ctx  = page.context
        new  = await ctx.new_page()
        self._page = new
        if url:
            return await self.go_to(url)
        return "New tab opened."

    async def close_tab(self) -> str:
        """Close the active tab. Closing one is an effect too."""
        page = getattr(self, "_page", None)
        if page and not page.is_closed():
            if not self._reserve_interaction():
                return self._interaction_limit_result()
            ctx   = page.context
            await page.close()
            pages = ctx.pages
            self._page = pages[-1] if pages else None
            return "Tab closed."
        return "No active tab to close."

    async def screenshot(self, path: str = None) -> str:
        page = await self._get_page()
        try:
            save_path = path or str(Path.home() / "Desktop" / "teemo_screenshot.png")
            await page.screenshot(path=save_path, full_page=False)
            return f"Screenshot saved: {save_path}"
        except Exception as e:
            return f"Screenshot error: {e}"

    async def back(self) -> str:
        """Go back in the history. The destination is NOT assumed: the page
        is read back afterwards, and the caller is told to verify it."""
        page = await self._get_page()
        try:
            if not self._reserve_interaction():
                return self._interaction_limit_result()
            await page.go_back(timeout=10_000)
            if self._page_closed(page):
                return outcome.failed(
                    "browser_control",
                    "back navigation target page disappeared",
                    recovery="page_changed",
                )
            return outcome.unverified(
                "browser_control",
                "back navigation completed; verify the destination before continuing",
                current_url=page.url,
                recovery="page_changed",
            )
        except Exception as e:
            return outcome.unverified(
                "browser_control",
                f"back navigation may have changed the page before "
                f"{type(e).__name__}; do not repeat it",
                recovery="effect_ambiguous",
            )

    async def forward(self) -> str:
        """Go forward in the history. Same rule as going back."""
        page = await self._get_page()
        try:
            if not self._reserve_interaction():
                return self._interaction_limit_result()
            await page.go_forward(timeout=10_000)
            if self._page_closed(page):
                return outcome.failed(
                    "browser_control",
                    "forward navigation target page disappeared",
                    recovery="page_changed",
                )
            return outcome.unverified(
                "browser_control",
                "forward navigation completed; verify the destination before continuing",
                current_url=page.url,
                recovery="page_changed",
            )
        except Exception as e:
            return outcome.unverified(
                "browser_control",
                f"forward navigation may have changed the page before "
                f"{type(e).__name__}; do not repeat it",
                recovery="effect_ambiguous",
            )

    async def reload(self) -> str:
        """Reload the page. A reload is an effect and it can change what the
        page shows, so it is reported as unverified."""
        page = await self._get_page()
        try:
            if not self._reserve_interaction():
                return self._interaction_limit_result()
            await page.reload(timeout=15_000)
            if self._page_closed(page):
                return outcome.failed(
                    "browser_control",
                    "reload target page disappeared",
                    recovery="page_changed",
                )
            return outcome.unverified(
                "browser_control",
                "reload completed; verify the page state before continuing",
                current_url=page.url,
                recovery="page_changed",
            )
        except Exception as e:
            return outcome.unverified(
                "browser_control",
                f"reload may have changed the page before {type(e).__name__}; "
                "do not repeat it",
                recovery="effect_ambiguous",
            )

    async def close_browser(self) -> str:
        await self._async_close()
        return f"{self.browser_name} closed."

class _SessionRegistry:
    """Manages all active browser sessions."""

    def __init__(self):
        self._sessions:        dict[str, _BrowserSession] = {}
        self._active_browser:  str                        = ""
        self._lock             = threading.Lock()
        self._last_native_url: str                        = ""

    def has(self, browser_name: str | None = None) -> bool:
        """Is there an active automation session for this browser (or any)?"""
        with self._lock:
            if not browser_name:
                return bool(self._sessions)
            name = _ALIASES.get(browser_name.lower().strip(), browser_name.lower().strip())
            return name in self._sessions

    def note_native_url(self, url: str) -> None:
        self._last_native_url = url

    def pop_native_url(self) -> str:
        """Returns the last natively-opened URL once (consumed to avoid repeats)."""
        url, self._last_native_url = self._last_native_url, ""
        return url

    def _get_or_create(self, browser_name: str) -> _BrowserSession:
        with self._lock:
            if browser_name not in self._sessions:
                sess = _BrowserSession(browser_name)
                sess.start()
                self._sessions[browser_name] = sess
                print(f"[Registry] New session: {browser_name}")
            return self._sessions[browser_name]

    def get(self, browser_name: str | None = None) -> _BrowserSession:
        if not browser_name:
            browser_name = self._active_browser or _detect_default_browser()
        browser_name = _ALIASES.get(browser_name.lower().strip(), browser_name.lower().strip())
        sess = self._get_or_create(browser_name)
        self._active_browser = browser_name
        return sess

    def switch(self, browser_name: str) -> str:
        browser_name = _ALIASES.get(browser_name.lower().strip(), browser_name.lower().strip())
        self._get_or_create(browser_name)
        self._active_browser = browser_name
        return f"Active browser → {browser_name}"

    def close_one(self, browser_name: str) -> str:
        with self._lock:
            sess = self._sessions.pop(browser_name, None)
        if sess:
            sess.close()
            if self._active_browser == browser_name:
                self._active_browser = ""
            return f"{browser_name} closed."
        return f"No active session for: {browser_name}"

    def close_all(self) -> str:
        with self._lock:
            names    = list(self._sessions.keys())
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._active_browser = ""
        for s in sessions:
            try:
                s.close()
            except Exception:
                pass
        return "All browsers closed: " + (", ".join(names) if names else "none")

    def list_sessions(self) -> str:
        with self._lock:
            if not self._sessions:
                return "No active browser sessions."
            lines = []
            for name in self._sessions:
                marker = " ◀ active" if name == self._active_browser else ""
                lines.append(f"  • {name}{marker}")
            return "Open browsers:\n" + "\n".join(lines)


_registry = _SessionRegistry()

# Every action this tool answers to. One list, used by the failure message and
# by nothing else — it exists so that being told "that is not an action" also
# tells you what the actions ARE.
_ACTIONS = (
    "go_to", "search", "new_tab", "close_tab",
    "click", "smart_click", "type", "smart_type", "fill_form", "press",
    "scroll", "get_text", "get_url", "read_page", "screenshot",
    "back", "forward", "reload",
    "switch", "list_browsers", "close", "close_all",
)


def _unknown_action(action: str) -> str:
    """Refuse an action this tool does not have, and say what it does have.

    THE OLD ANSWER WAS A SENTENCE, AND A SENTENCE IS NOT A FAILURE
        `f"Unknown browser action: '{action}'"` is a plain string. Nothing above
        this file could tell it apart from a success: it does not start with a
        status, so `outcome.is_problem` reads it as fine, the screen agent's
        loop counted the step as performed, and the assistant said the browser
        control function "couldn't understand the action" only because the model
        happened to read the English.

        Worse, it taught nothing. A caller told "unknown action 'open'" and
        nothing else has exactly one move available, which is to guess again.
        Observed doing precisely that: open, then "", then open.

    So this is a real failure with the valid list attached. A planner that
    reads it can correct itself on the next attempt instead of re-rolling.
    """
    return outcome.failed(
        "browser_control",
        f"there is no browser action called {action or '(empty)'!r}",
        valid_actions=", ".join(_ACTIONS))


def browser_control(
    parameters:    dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params  = parameters or {}
    action  = params.get("action", "").lower().strip()
    browser = params.get("browser", "").lower().strip() or None
    result  = _unknown_action(action)

    if action == "switch":
        target = browser or params.get("target", "").lower().strip()
        result = _registry.switch(target) if target else "Please specify a browser."
        _log(player, result)
        return result

    if action == "list_browsers":
        result = _registry.list_sessions()
        _log(player, result)
        return result

    if action == "close_all":
        result = _registry.close_all()
        _log(player, result)
        return result

    if action == "close":
        target = browser or _registry._active_browser
        result = _registry.close_one(target) if target else "No browser specified."
        _log(player, result)
        return result

    # ── Navigation is ALWAYS native ──────────────────────────────────────────
    # go_to / search / new_tab open the site in the user's own browser —
    # their own profile, logged-in accounts and start page; exactly as if the
    # user had opened it themselves. A controlled window with about:blank never
    # opens here. The only exception: if an automation flow is already running,
    # navigation continues in that window (so multi-step tasks aren't split).
    if action in ("go_to", "search", "new_tab"):
        if _registry.has(browser):
            sess = _registry.get(browser)
            try:
                if action == "search":
                    result = sess.run(sess.search(params.get("query", ""),
                                                  params.get("engine", "google")))
                elif action == "new_tab":
                    result = sess.run(sess.new_tab(params.get("url", "")))
                else:
                    result = sess.run(sess.go_to(params.get("url", "")))
            except concurrent.futures.TimeoutError:
                result = f"Browser action '{action}' timed out (60s)."
            except Exception as e:
                result = f"Browser error ({action}): {e}"
            _log(player, result)
            return result

        if action == "search":
            base    = _SEARCH_ENGINES.get(params.get("engine", "google").lower(),
                                          _SEARCH_ENGINES["google"])
            nav_url = base + params.get("query", "").replace(" ", "+")
        else:
            nav_url = params.get("url", "").strip()

        result = _open_native(nav_url, browser)
        # Remembered unless it outright FAILED. `unverified` is now the normal
        # answer for a native open - the browser does not report back - and
        # treating that as a problem would stop the automation session ever
        # resuming the page the user is actually looking at.
        if nav_url and not result.startswith(f"status={outcome.FAILED} "):
            _registry.note_native_url(_normalize_url(nav_url))
        _log(player, result)
        return result

    # ── Interactive actions (click/type/read…) ───────────────────────────────
    # These require a physically controllable browser; the automation window
    # only opens here, and as soon as it opens it goes to the user's last
    # navigated page — it doesn't sit on a blank page.
    try:
        sess = _registry.get(browser)
    except Exception as e:
        result = f"Could not start browser session: {e}"
        _log(player, result)
        return result

    try:
        last = _registry.pop_native_url()
        if last:
            try:
                sess.run(sess.go_to(last))
            except Exception as e:
                print(f"[Browser] Could not resume last page ({last}): {e}")

        if action == "click":
            result = sess.run(sess.click(params.get("selector"), params.get("text")))
        elif action == "type":
            result = sess.run(sess.type_text(
                params.get("selector"), params.get("text", ""), params.get("clear_first", True)))
        elif action == "scroll":
            result = sess.run(sess.scroll(params.get("direction", "down"), int(params.get("amount", 500))))
        elif action == "fill_form":
            result = sess.run(sess.fill_form(params.get("fields", {})))
        elif action == "smart_click":
            result = sess.run(sess.smart_click(params.get("description", "")))
        elif action == "smart_type":
            result = sess.run(sess.smart_type(params.get("description", ""), params.get("text", "")))
        elif action == "get_text":
            result = sess.run(sess.get_text())
        elif action == "get_url":
            result = sess.run(sess.get_url())
        elif action == "read_page":
            result = sess.run(sess.read_page(int(params.get("max_elements", 120))))
        elif action == "press":
            result = sess.run(sess.press(params.get("key", "Enter")))
        elif action == "close_tab":
            result = sess.run(sess.close_tab())
        elif action == "screenshot":
            result = sess.run(sess.screenshot(params.get("path")))
        elif action == "back":
            result = sess.run(sess.back())
        elif action == "forward":
            result = sess.run(sess.forward())
        elif action == "reload":
            result = sess.run(sess.reload())
        else:
            result = _unknown_action(action)

    except concurrent.futures.TimeoutError:
        result = f"Browser action '{action}' timed out (60s)."
    except Exception as e:
        result = f"Browser error ({action}): {e}"

    _log(player, result)
    return result


def _log(player, text: str):
    # ONE line, once. This printed the same string twice whenever a player was
    # attached - which is always, in the real app - and the second copy read in
    # the terminal like a second navigation. "Opened in operagx: <url>" twice in
    # a row looks exactly like the browser being opened twice, and time was
    # spent looking for a double-open that had never happened.
    print(f"[Browser] {str(text)[:80]}")


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "browser_control",
    "description": (
        "Controls the web browser and everything INSIDE a web page: open an "
        "address, search, click, type, scroll, read the page, manage tabs, go "
        "back and forward. Anything happening on a website is this tool - it "
        "reads the page itself, which nothing else in the assistant can do. "
        "THIS TOOL OPENS THE BROWSER ITSELF. Never call open_app for the "
        "browser first - doing both launches it twice, the second copy on a "
        "different profile with none of the user's sign-ins. 'Open Chrome and "
        "go to YouTube' is ONE call to this tool. "
        "NEVER INVENT AN ADDRESS. Only pass a url you actually know. Anything "
        "belonging to the user personally - their own channel, profile, "
        "account page, inbox or playlist - has an address you do NOT know, and "
        "guessing a path produces an error page that looks like a working one. "
        "When the exact address is not known: open the site's own address "
        "(youtube.com, instagram.com) and get to the rest with smart_click on "
        "what the page shows, or use action='search', or ask the user. A "
        "guessed path is never acceptable. "
        "TO SEE WHAT IS ON A PAGE, PREFER action='read_page' OVER A "
        "SCREENSHOT. It returns the page's interactive elements as a "
        "numbered list - [e7] button 'Accept' - which says what each thing "
        "IS and what it SAYS. A screenshot is a picture of pixels, and an "
        "action on a pixel is a guess that is wrong on every screen but "
        "this one. Use action='screenshot' only when the question is "
        "about how the page LOOKS - a colour, a drawing, a layout - and "
        "not about what is on it. "
        "TO CLICK OR TYPE ON A PAGE, PREFER action='smart_click' or "
        "action='smart_type' with a 'description' of what the thing SAYS on "
        "screen - the visible label, in the page's own language. They search "
        "the page by role, label, placeholder and text and find it. Plain "
        "'click' with a 'selector' needs a CSS selector that is exactly right "
        "for that site's markup, which is not something to guess at; use it "
        "only when the selector is known. "
        "For a job on a page that takes several steps - open a site, then find "
        "and click something on it - call 'screen_agent' instead: it plans the "
        "steps, calls this tool for each of them, and checks after every one "
        "that it actually worked. "
        "Read the status it returns. 'done' means it was confirmed - for an "
        "open, the url reported back is the page's OWN address, so if it "
        "differs from what was asked for the site redirected and the user "
        "should be told. 'unverified' means the address was handed to a browser "
        "that does not report back: whether the page loaded, redirected or does "
        "not exist is unknown, so say it was opened but NOT confirmed and never "
        "that the page is up. 'failed' means nothing happened."
    ),
    "brief": (
        "Controls the web browser: open a URL, navigate, click, read the page."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | get_url | read_page | press | new_tab | close_tab | screenshot | back | forward | reload | switch | list_browsers | close | close_all"
            },
            "browser": {
                "type": "STRING",
                "description": "Target browser: chrome | edge | firefox | opera | operagx | brave | vivaldi | safari. Omit to use the currently active browser."
            },
            "url": {
                "type": "STRING",
                "description": "URL for go_to / new_tab action"
            },
            "query": {
                "type": "STRING",
                "description": "Search query for search action"
            },
            "engine": {
                "type": "STRING",
                "description": "Search engine: google | bing | duckduckgo | yandex (default: google)"
            },
            "selector": {
                "type": "STRING",
                "description": "CSS selector for click/type"
            },
            "text": {
                "type": "STRING",
                "description": "Text to click or type"
            },
            "description": {
                "type": "STRING",
                "description": "Element description for smart_click/smart_type"
            },
            "direction": {
                "type": "STRING",
                "description": "up | down for scroll"
            },
            "amount": {
                "type": "INTEGER",
                "description": "Scroll amount in pixels (default: 500)"
            },
            "key": {
                "type": "STRING",
                "description": "Key name for press action (e.g. Enter, Escape, F5)"
            },
            "path": {
                "type": "STRING",
                "description": "Save path for screenshot"
            },
            "incognito": {
                "type": "BOOLEAN",
                "description": "Open in private/incognito mode"
            },
            "clear_first": {
                "type": "BOOLEAN",
                "description": "Clear field before typing (default: true)"
            }
        },
        "required": [
            "action"
        ]
    },
    "handler": browser_control,
}
