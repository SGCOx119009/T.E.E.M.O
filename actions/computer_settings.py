#computer_settings.py
import json
import os
import re
import sys
import time
import subprocess
from pathlib import Path

from core.lazyimport import LazyModule, installed

# Imported on first USE, not at launch. This file has eighty-six `pyautogui.`
# call sites; not one of them changes — see core/lazyimport.py.
_PYAUTOGUI = installed("pyautogui")
_PYPERCLIP = installed("pyperclip")


def _configure_pyautogui(module) -> None:
    module.FAILSAFE = True
    module.PAUSE    = 0.05


pyautogui = LazyModule("pyautogui", _configure_pyautogui)
pyperclip = LazyModule("pyperclip")

from core import action_policy, confirm, outcome
from core.undo import push_undo

# Windows only. There is no OS switch here any more: T.E.E.M.O. is a
# Windows desktop assistant, so every branch that asked "which platform is
# this?" was a dead code path that only ever cost a branch and a claim.
_OS = "Windows"

# Every subprocess in this file must not flash a console window over the HUD.
_WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

def _get_api_key() -> str:
    # The Credential Locker, not the settings file: the key is not written in
    # the file any more, so reading the file found nothing where there was
    # everything.
    from memory.config_manager import require_gemini_key
    return require_gemini_key()

# ── Volume ───────────────────────────────────────────────────────────────────
# "Turn it up by 30", "set it to 100", "a bit quieter": the level is read and
# written as a NUMBER wherever the OS allows it, so the change is the one asked
# for. The media keys are only the fallback. The old code pressed the volume key
# five times whatever was asked — two points a press on Windows — which is how
# "volume up by 50" and, via volume_up, "volume to 100" both became +10.

def _number(value) -> int | None:
    """The whole number in a value: 40, 40.0, "40", "40%", "%40" -> 40.
    None when there is no number in it."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(round(value))
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(value))
    return int(round(float(m.group(0).replace(",", ".")))) if m else None


def _volume_step(delta: int) -> None:
    now = volume_get()
    if now is not None:
        volume_set(now + delta)
        return
    # The number could not be read, so the exact level is unknown: walk the
    # slider to the bottom and back up, which lands on the asked value.
    # Each volume key moves the Windows slider two points.
    key = "volumeup" if delta > 0 else "volumedown"
    for _ in range(max(1, round(abs(delta) / 2))):
        pyautogui.press(key)


def volume_up(step: int = 10):
    _volume_step(abs(int(step)))


def volume_down(step: int = 10):
    _volume_step(-abs(int(step)))


def mute_get() -> bool | None:
    """Whether the sound is muted, or None where this system will not say."""
    try:
        return bool(_win_endpoint_volume().GetMute())
    except Exception:
        return None


def mute_set(muted: bool) -> None:
    """Muted, or not — as asked, never a toggle.

    'unmute' used to press the mute KEY, which is a toggle: on a machine that
    was not muted, asking to unmute muted it."""
    muted = bool(muted)
    try:
        _win_endpoint_volume().SetMute(1 if muted else 0, None)
        return
    except Exception as e:
        print(f"[Settings] pycaw mute failed, using the mute key: {e}")
        pyautogui.press("volumemute")


def volume_mute():
    mute_set(True)


def volume_unmute():
    mute_set(False)


def volume_toggle_mute():
    state = mute_get()
    if state is not None:
        mute_set(not state)
    else:
        pyautogui.press("volumemute")


def _win_endpoint_volume():
    """The Windows master-volume interface, across pycaw versions.

    TWO BUGS LIVED HERE, AND NEITHER ANNOUNCED ITSELF.

    1. `AudioUtilities.GetSpeakers()` used to return the raw COM device, and
       the code called `.Activate(...)` on it. Newer pycaw returns an
       `AudioDevice` WRAPPER instead, which has no `.Activate` — so every call
       raised AttributeError, was swallowed by the `except Exception: return
       None` around it, and `volume_get()` answered "this system does not
       report its volume" on a system that reports it fine. Undo for volume
       silently stopped registering, and so did every verification built on it.

    2. The percentage was computed as `10 ** (db / 20) * 100`, which converts
       decibels to an amplitude RATIO. That is not what the Windows slider
       shows: the slider is the scalar, and the two disagree by a lot —
       measured here, scalar 43% against a formula answering 0%.

    So: prefer the wrapper's own `EndpointVolume`, fall back to activating the
    inner device for older pycaw, and read the scalar rather than deriving a
    number from decibels.
    """
    from ctypes import cast, POINTER
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    speakers = AudioUtilities.GetSpeakers()
    endpoint = getattr(speakers, "EndpointVolume", None)
    if endpoint is not None:
        return endpoint
    dev = getattr(speakers, "_dev", speakers)
    return cast(dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None),
                POINTER(IAudioEndpointVolume))


def volume_get() -> int | None:
    """Current master volume 0-100, or None if this machine will not say.

    Undo needs a "before" value, and reading one is cheap. Where it is not
    readable the action simply is not registered as undoable — a wrong undo is
    worse than no undo."""
    try:
        return max(0, min(100, round(
            _win_endpoint_volume().GetMasterVolumeLevelScalar() * 100)))
    except Exception:
        return None


def brightness_get() -> int | None:
    """Current brightness 0-100, or None where it cannot be read.

    Two sources, in order of truthfulness: the WMI monitor class, which is what
    the laptop's own panel reports, and `monitorcontrol.exe`, which some
    vendors ship for the same job. Neither exists on a desktop with an
    external monitor, and then the honest answer is None — the caller turns
    that into "unverified", never into a "done"."""
    value = _brightness_get_wmi()
    if value is not None:
        return value
    return _brightness_get_monitorcontrol()


def _brightness_get_wmi() -> int | None:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness"
             " -ErrorAction SilentlyContinue).CurrentBrightness"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=8, **_WIN_HIDE
        )
        raw = r.stdout.strip().splitlines()
        if r.returncode != 0 or not raw:
            return None
        return max(0, min(100, int(raw[0].strip())))
    except Exception:
        return None


def _brightness_get_monitorcontrol() -> int | None:
    """`monitorcontrol.exe` speaks the same numeric level, 0-100."""
    exe = _find_monitorcontrol()
    if exe is None:
        return None
    try:
        r = subprocess.run([exe, "/GetBrightness"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=8, **_WIN_HIDE)
        m = re.search(r"(\d{1,3})", r.stdout)
        return max(0, min(100, int(m.group(1)))) if m else None
    except Exception:
        return None


def _find_monitorcontrol() -> str | None:
    """Locate monitorcontrol.exe in the usual vendor install locations."""
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Monitor Control" / "monitorcontrol.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Monitor Control" / "monitorcontrol.exe",
        Path(os.environ.get("ProgramW6432", r"C:\Program Files")) / "Monitor Control" / "monitorcontrol.exe",
        Path.home() / "AppData" / "Local" / "Monitor Control" / "monitorcontrol.exe",
    ]
    for path in candidates:
        try:
            if path.is_file():
                return str(path)
        except OSError:
            continue
    return None


def brightness_set(value: int) -> None:
    """Set brightness to an absolute percentage — "brightness to 70" — and the
    way undo puts back a value captured before a change.

    Two writers, tried in order: the WMI monitor class (the laptop's own
    panel) and `monitorcontrol.exe`. When neither accepts the change this
    RAISES rather than returning quietly: a silent success here becomes a
    "done" the user can see with their own eyes is a lie."""
    value = max(0, min(100, int(value)))
    if _brightness_set_wmi(value):
        return
    if _brightness_set_monitorcontrol(value):
        return
    raise RuntimeError(
        "this display does not accept a brightness level from the computer "
        "(an external monitor usually cannot be set from the computer at all)")


def _brightness_set_wmi(value: int) -> bool:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "$m = Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods"
             " -ErrorAction SilentlyContinue | Select-Object -First 1;"
             f"if ($m) {{ $m.WmiSetBrightness(1, {value}); exit 0 }} else {{ exit 3 }}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=8, **_WIN_HIDE
        )
        return r.returncode == 0
    except Exception as e:
        print(f"[Settings] WMI brightness failed: {e}")
        return False


def _brightness_set_monitorcontrol(value: int) -> bool:
    exe = _find_monitorcontrol()
    if exe is None:
        return False
    try:
        r = subprocess.run([exe, "/SetBrightness", str(value)], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=8, **_WIN_HIDE)
        return r.returncode == 0
    except Exception as e:
        print(f"[Settings] monitorcontrol brightness failed: {e}")
        return False


def volume_set(value: int):
    value = max(0, min(100, int(value)))
    try:
        # Scalar, not decibels: this is the number the Windows slider shows,
        # so "set the volume to 40" lands where the user expects rather than
        # wherever a dB conversion happens to put it.
        endpoint = _win_endpoint_volume()
        endpoint.SetMasterVolumeLevelScalar(value / 100.0, None)
        # A level asked for is a level to be heard — the volume keys unmute
        # too. Setting 60 on a muted machine used to leave it silent.
        if value > 0 and endpoint.GetMute():
            endpoint.SetMute(0, None)
        return
    except Exception as e:
        # The old fallback pressed mute twice, which changes nothing. Each
        # volume key moves the slider two points: to the bottom, then up.
        print(f"[Settings] pycaw failed, using the volume keys: {e}")
        for _ in range(50):
            pyautogui.press("volumedown")
        for _ in range(round(value / 2)):
            pyautogui.press("volumeup")


def _brightness_step(delta: int) -> None:
    now = brightness_get()
    if now is not None:
        brightness_set(now + delta)
        return
    # Windows with no readable brightness is an external monitor, which WMI
    # cannot drive; the caller reports that it could not be read back.


def brightness_up(step: int = 10):
    _brightness_step(abs(int(step)))


def brightness_down(step: int = 10):
    _brightness_step(-abs(int(step)))

def close_app():
    """Removed. Closing an application now lives in computer_control.

    WHAT THIS USED TO BE, AND WHY IT HAD TO GO

        pyautogui.hotkey("alt", "f4")

    Two lines with no target. It sent the quit chord to WHATEVER WINDOW HAD
    FOCUS — and while somebody is talking to the assistant, the window with
    focus is very often the assistant. That is the whole of the bug people
    reported as "I asked it to close an app and it closed itself".

    It could not be fixed by aiming better. "The focused window" is the wrong
    idea: the user names an application, so the application is what has to be
    found. `computer_control` action='close_app' does that — matched against
    what is actually running, never the assistant's own process tree, asked
    politely so unsaved work survives, and checked afterwards.

    The function stays as a named refusal rather than disappearing, because a
    model that learnt the old name from an older prompt will still reach for
    it, and a KeyError here would surface as "computer_settings failed" with
    nothing to act on.
    """
    return outcome.failed(
        "close_app",
        "this action needs to know WHICH application to close - use the "
        "computer_control tool with action='close_app' and the application's "
        "name in 'app_name'",
    )

def close_window():
    pyautogui.hotkey("ctrl", "w")

def full_screen():
    pyautogui.press("f11")

def minimize_window():
    pyautogui.hotkey("win", "down")

def maximize_window():
    pyautogui.hotkey("win", "up")

def snap_left():
    pyautogui.hotkey("win", "left")

def snap_right():
    pyautogui.hotkey("win", "right")

def switch_window():
    pyautogui.hotkey("alt", "tab")

def show_desktop():
    pyautogui.hotkey("win", "d")

def open_task_manager():
    pyautogui.hotkey("ctrl", "shift", "esc")


def focus_search():
    pyautogui.hotkey("ctrl", "l")

def pause_video():      pyautogui.press("space")


# ── Media ────────────────────────────────────────────────────────────────────
# The system media keys reach whatever is playing — music, a browser tab, a
# video app — without its window having focus.

def _media(key: str) -> None:
    pyautogui.press(key)


def media_play_pause(): _media("playpause")
def media_next():       _media("nexttrack")
def media_previous():   _media("prevtrack")
def media_stop():       _media("stop")


# Seconds one arrow key moves YouTube and most web players. Players differ and
# none reports where it is, so a seek in another application says it is an
# estimate. A video on the assistant's own display is exact: video_player.
SEEK_PER_PRESS = 5


def seek_presses(seconds: int) -> int:
    """Arrow keys to the window in front, about `seconds` worth. Returns presses."""
    presses = max(1, min(120, round(abs(seconds) / SEEK_PER_PRESS)))
    key = "right" if seconds > 0 else "left"
    for _ in range(presses):
        pyautogui.press(key)
    return presses


def refresh_page():
    pyautogui.press("f5")

def close_tab():
    pyautogui.hotkey("ctrl", "w")

def new_tab():
    pyautogui.hotkey("ctrl", "t")

def next_tab():
    pyautogui.hotkey("ctrl", "tab")

def prev_tab():
    pyautogui.hotkey("ctrl", "shift", "tab")

def go_back():
    pyautogui.hotkey("alt", "left")

def go_forward():
    pyautogui.hotkey("alt", "right")

def zoom_in():
    pyautogui.hotkey("ctrl", "equal")

def zoom_out():
    pyautogui.hotkey("ctrl", "minus")

def zoom_reset():
    pyautogui.hotkey("ctrl", "0")

def find_on_page():
    pyautogui.hotkey("ctrl", "f")

def reload_page_n(n: int):
    for _ in range(max(1, n)):
        refresh_page()
        time.sleep(0.8)


def _wheel(notches: int) -> None:
    """Scroll by whole wheel notches — the same distance everywhere.

    pyautogui's unit is not a notch on Windows: it is 1/120 of a notch. The
    old scroll(500) therefore moved about four notches on Windows and five
    hundred elsewhere."""
    pyautogui.scroll(notches * 120)


def scroll_up(notches: int = 5):    _wheel(abs(int(notches)))
def scroll_down(notches: int = 5):  _wheel(-abs(int(notches)))

def scroll_top():
    pyautogui.hotkey("ctrl", "home")

def scroll_bottom():
    pyautogui.hotkey("ctrl", "end")

def page_up():   pyautogui.press("pageup")
def page_down(): pyautogui.press("pagedown")


def copy():
    pyautogui.hotkey("ctrl", "c")

def paste():
    pyautogui.hotkey("ctrl", "v")

def cut():
    pyautogui.hotkey("ctrl", "x")

def undo():
    pyautogui.hotkey("ctrl", "z")

def redo():
    pyautogui.hotkey("ctrl", "y")

def select_all():
    pyautogui.hotkey("ctrl", "a")

def save_file():
    pyautogui.hotkey("ctrl", "s")

def press_enter():   pyautogui.press("enter")
def press_escape():  pyautogui.press("escape")
def press_key(key: str): pyautogui.press(key)

def type_text(text: str, press_enter_after: bool = False):
    if not text:
        return
    if _PYPERCLIP:
        pyperclip.copy(str(text))
        time.sleep(0.15)
        paste()
    else:
        pyautogui.write(str(text), interval=0.03)
    if press_enter_after:
        time.sleep(0.1)
        pyautogui.press("enter")

def take_screenshot():
    pyautogui.hotkey("win", "shift", "s")

def lock_screen():
    pyautogui.hotkey("win", "l")

def open_system_settings():
    pyautogui.hotkey("win", "i")

def open_file_explorer():
    pyautogui.hotkey("win", "e")

def _send_monitor_power_off() -> None:
    """Ask Windows to switch the display off. Raises when the call fails."""
    import ctypes
    ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)


def _display_power_state() -> bool | None:
    """Whether a display reports itself as powered down.

    True when a monitor says it is off, False when one says it is on, and
    None when this machine will not say — which is the honest answer, and the
    one most drivers give.

    The read-back is `GetDevicePowerState` on the monitors Windows
    enumerates. Every failure along the way (no monitor, an old driver, a
    handle that will not open) ends in None rather than in a guess.
    """
    try:
        import ctypes
        from ctypes import wintypes

        class _DisplayDevice(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("DeviceName", wintypes.WCHAR * 32),
                ("DeviceString", wintypes.WCHAR * 128),
                ("StateFlags", wintypes.DWORD),
                ("DeviceID", wintypes.WCHAR * 128),
                ("DeviceKey", wintypes.WCHAR * 128),
            ]

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.EnumDisplayDevicesW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.POINTER(_DisplayDevice), wintypes.DWORD,
        ]
        user32.EnumDisplayDevicesW.restype = wintypes.BOOL
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.GetDevicePowerState.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetDevicePowerState.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        def _enumerate(parent, index):
            device = _DisplayDevice()
            device.cb = ctypes.sizeof(device)
            if not user32.EnumDisplayDevicesW(parent, index,
                                              ctypes.byref(device), 0):
                return None
            return device

        adapter_index = 0
        while True:
            adapter = _enumerate(None, adapter_index)
            if adapter is None:
                break
            adapter_index += 1
            monitor_index = 0
            while True:
                monitor = _enumerate(adapter.DeviceName, monitor_index)
                if monitor is None:
                    break
                monitor_index += 1
                state = wintypes.DWORD()
                handle = kernel32.CreateFileW(
                    "\\\\.\\" + str(monitor.DeviceID), 0, 0, None, 3, 0, None)
                if not handle or handle == 0xFFFFFFFF:
                    continue
                try:
                    readable = kernel32.GetDevicePowerState(
                        handle, ctypes.byref(state))
                finally:
                    kernel32.CloseHandle(handle)
                if not readable:
                    continue
                if state.value & 0x02 or state.value & 0x04:
                    return True          # powered off, or hardware off
                if state.value & 0x01:
                    return False         # powered on
    except Exception as e:
        print(f"[Settings] display power state unreadable: {e}")
    return None


def sleep_display():
    """Put the display to sleep, and say honestly whether it went.

    WINDOWS DOES NOT ALWAYS SAY.  The message that switches a monitor off is
    broadcast to every window, and most drivers never report back whether the
    panel actually went dark. So the answer is "done" only when a display says
    it is off, and "unverified" when the machine will not say — which is the
    honest answer, and the one that keeps the assistant from announcing a
    dark screen over a lit one.

    The old code caught the exception, printed it and said nothing: the
    caller then reported "done" for a display that never went to sleep.
    """
    try:
        _send_monitor_power_off()
    except Exception as e:
        print(f"[Settings] sleep_display failed: {e}")
        return outcome.failed(
            "computer_settings",
            f"the display could not be put to sleep: {e}",
            wanted="sleep_display")
    state = _display_power_state()
    if state is True:
        return outcome.done("computer_settings", wanted="sleep_display",
                            display="off", verified=True)
    return outcome.unverified(
        "computer_settings",
        "the request to switch the display off was sent to Windows, but this "
        "system does not report the display power state, so it could not be "
        "confirmed",
        wanted="sleep_display")


def open_run():
    pyautogui.hotkey("win", "r")


def _theme_is_dark() -> bool | None:
    """Whether Windows is set to the dark theme. None when it will not say."""
    try:
        import winreg
        key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return not bool(value)
    except Exception:
        return None


def dark_mode():
    """Flip between the light and the dark theme, and say whether it did.

    The registry value is the switch Windows itself reads, so this is the same
    toggle as Settings > Personalisation > Colours, and it is reversible by
    calling it again.

    The old code caught every exception, printed it and carried on, and the
    caller said "done" anyway: the theme had not changed and the user was
    told it had. Now the failure is reported as a failure, and the new value
    is read back before the change is claimed.
    """
    try:
        import winreg
        key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                             winreg.KEY_ALL_ACCESS)
        try:
            current, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD,
                              1 - current)
            winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD,
                              1 - current)
        finally:
            winreg.CloseKey(key)
    except Exception as e:
        print(f"[Settings] dark_mode registry failed: {e}")
        return outcome.failed(
            "computer_settings",
            f"the theme could not be changed: {e}",
            wanted="dark_mode")
    now = _theme_is_dark()
    if now is None:
        return outcome.unverified(
            "computer_settings",
            "the theme was written to the registry, but this system does not "
            "report which theme it is using, so the change could not be read "
            "back",
            wanted="dark_mode")
    return outcome.done("computer_settings", wanted="dark_mode",
                        theme="dark" if now else "light", verified=True)


# ── WiFi ─────────────────────────────────────────────────────────────────────
# `netsh` is the Windows tool for this. It needs the interface NAME, so the
# interface is read first — a wrong name would toggle the wrong adapter.

# The name of the wireless interface, and whether it is administratively on.
_WIFI_HINT = re.compile(r"802\.11|wireless|wi-?fi", re.IGNORECASE)


def _netsh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["netsh", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=15,
                          **_WIN_HIDE)


def _wifi_interface() -> str | None:
    """The name of the wireless interface, or None when there is not one."""
    try:
        r = _netsh("wlan", "show", "interfaces")
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                stripped = line.strip()
                if stripped.lower().startswith("name"):
                    name = stripped.split(":", 1)[-1].strip()
                    if name:
                        return name
        # No wireless interface listed: fall back to the general interface
        # list, where the wireless one is recognisable by its description.
        r = _netsh("interface", "show", "interface")
        if r.returncode == 0:
            current = None
            for line in r.stdout.splitlines():
                m = re.match(r"\s*Interface\s+\d+\s*:\s*(.+?)\s*$", line)
                if m:
                    current = m.group(1)
                    continue
                if current and _WIFI_HINT.search(line):
                    return current
                if current and re.search(r"Description\s*:", line, re.IGNORECASE):
                    desc = line.split(":", 1)[-1]
                    if _WIFI_HINT.search(desc):
                        return current
    except Exception as e:
        print(f"[Settings] wifi interface lookup failed: {e}")
    return None


def wifi_state() -> str | None:
    """'on' or 'off' — the ADMINISTRATIVE state, which is what `netsh
    interface set interface admin=` changes. None when it cannot be read."""
    name = _wifi_interface()
    if not name:
        return None
    try:
        r = _netsh("interface", "show", "interface")
        if r.returncode != 0:
            return None
        current = None
        for line in r.stdout.splitlines():
            m = re.match(r"\s*Interface\s+\d+\s*:\s*(.+?)\s*$", line)
            if m:
                current = m.group(1)
                continue
            if current and current.lower() == name.lower():
                m = re.search(r"Administrative state\s*:\s*(\w+)", line, re.IGNORECASE)
                if m:
                    return "on" if m.group(1).lower() == "enabled" else "off"
    except Exception:
        return None
    return None


def wifi_set(enabled: bool) -> bool:
    """Turn the wireless radio on or off. True when the change was accepted."""
    name = _wifi_interface()
    if not name:
        return False
    try:
        r = _netsh("interface", "set", "interface", f"name={name}",
                   "admin=enabled" if enabled else "admin=disabled")
        return r.returncode == 0
    except Exception as e:
        print(f"[Settings] wifi toggle failed: {e}")
        return False


def toggle_wifi():
    """Flip the wireless radio, and say whether it really flipped.

    Switching it OFF is the reason this action is behind a human confirmation
    and not behind a model vote: it cuts the assistant's own connection to the
    Live API, and a machine that cannot talk cannot be told to turn it back
    on.

    The old code threw away what `wifi_set` said. A radio that did not move
    — no adapter, no administrator rights, a `netsh` that refused — was
    announced as moved, and the assistant was left without a connection while
    claiming it still had one. Now a refusal is a failure, and the state is
    read back before the change is claimed.
    """
    current = wifi_state()
    if current is None:
        # Cannot read the state: turn it ON rather than risk switching a
        # working connection off by guessing.
        wanted = True
    else:
        wanted = current == "off"
    if not wifi_set(wanted):
        return outcome.failed(
            "computer_settings",
            "Windows did not accept the change to the wireless radio - the "
            "interface may need administrator rights, or this computer may "
            "have no wireless adapter",
            wanted="toggle_wifi")
    now = wifi_state()
    if now is None:
        return outcome.unverified(
            "computer_settings",
            "the wireless radio was switched, but this system does not report "
            "its state, so the change could not be read back",
            wanted="toggle_wifi")
    if now != ("on" if wanted else "off"):
        return outcome.unverified(
            "computer_settings",
            f"the wireless radio still reads {now}",
            wanted="toggle_wifi", wifi=now)
    return outcome.done("computer_settings", wanted="toggle_wifi", wifi=now,
                        verified=True)


def _schedule_shutdown(flag: str, action: str):
    """Ask Windows to restart or power off the machine, in ten seconds.

    `shutdown` exits with 0 when Windows accepted the request, and with
    something else when it did not: a pending update, a policy, a machine
    that is already going down. The old code threw the code away, so a
    refused request was announced as a restart that was about to happen.
    """
    try:
        r = subprocess.run(["shutdown", flag, "/t", "10"], capture_output=True,
                           timeout=30, **_WIN_HIDE)
    except Exception as e:
        print(f"[Settings] {action} could not be scheduled: {e}")
        return outcome.failed(
            "computer_settings",
            f"the {action} could not be scheduled: {e}",
            wanted=action)
    if r.returncode != 0:
        return outcome.failed(
            "computer_settings",
            f"Windows refused the {action} (the shutdown command exited with "
            f"code {r.returncode})",
            wanted=action, returncode=r.returncode)
    return outcome.done("computer_settings", wanted=action,
                        scheduled_in_seconds=10, verified=True)


def restart_computer():
    """Restart this computer in ten seconds, if Windows accepts the request."""
    return _schedule_shutdown("/r", "restart")


def shutdown_computer():
    """Power this computer down in ten seconds, if Windows accepts it."""
    return _schedule_shutdown("/s", "shutdown")


# ── Energy ─────────────────────────────────────────────────────────────────
# The battery is a NUMBER the machine will read back, and a desktop without
# one has to say so plainly. "85%" on a computer with no battery is a lie a
# person acts on: they decide not to plug in, and they were never going to.

# The Energy Saver plan, by GUID. Its NAME is localised — "Economía de energía"
# in Spanish — but the GUID is the same on every Windows, so this is the one
# way to ask whether energy saver is on without reading a translated string.
_ENERGY_SAVER_GUID = "961cc777-2547-4f9d-8174-7d86181b8a8a"


def _battery_reading() -> dict | None:
    """What Windows says about the battery, or None when it will not say.

    The keys are `present` (there is a battery), `percent` (0-100, or None
    when Windows reports 255 = unknown), `charging` (True, False or None) and
    `on_ac` (True when the cable is plugged in, False when it is not, None
    when Windows does not know).

    `GetSystemPowerStatus` is the Windows-native way to ask. It never
    invents a number: a percentage of 255 means "unknown", and on a desktop
    the battery flag says there is no battery at all.
    """
    try:
        import ctypes
        from ctypes import wintypes

        class _SystemPowerStatus(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus", wintypes.BYTE),
                ("BatteryFlag", wintypes.BYTE),
                ("BatteryLifePercent", wintypes.BYTE),
                ("Reserved1", wintypes.BYTE),
                ("BatteryLifeTime", wintypes.DWORD),
                ("BatteryFullLifeTime", wintypes.DWORD),
            ]

        status = _SystemPowerStatus()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return None
        flag = int(status.BatteryFlag)
        raw = int(status.BatteryLifePercent)
        percent = None if raw >= 100 else raw
        if flag & 0x10:                      # 16 = no system battery
            present = False
        elif percent is not None or flag & 0x80:
            # A level Windows is willing to give means there IS a battery;
            # 0x80 says the same on the machines that report no level.
            present = True
        else:
            present = None
        if flag & 0x08:                      # 8 = charging
            charging = True
        elif present is True:
            charging = False
        else:
            charging = None
        line = int(status.ACLineStatus)
        on_ac = None if line == 255 else bool(line)
        return {"present": present, "percent": percent,
                "charging": charging, "on_ac": on_ac}
    except Exception as e:
        print(f"[Settings] battery reading failed: {e}")
        return None


def _power_scheme() -> tuple[str, str] | None:
    """The active power plan as (GUID, name), or None when it cannot be read."""
    try:
        r = subprocess.run(["powercfg", "/getactivescheme"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=8, **_WIN_HIDE)
        if r.returncode != 0:
            return None
        m = re.search(
            r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{12})\s*\(([^)]*)\)", r.stdout)
        if not m:
            return None
        return m.group(1).lower(), m.group(2).strip()
    except Exception as e:
        print(f"[Settings] power scheme lookup failed: {e}")
        return None


def _yes_no(value) -> str:
    """True/False/None as something a model can read: yes, no or unknown."""
    if value is None:
        return "unknown"
    return "yes" if value else "no"


def energy_status():
    """The battery, the charging state and the power plan, as Windows says them.

    A laptop answers with a percentage and whether it is charging. A desktop
    without a battery answers with that fact, plainly, and never with an
    invented percentage. A machine that will not say is reported as not said.

    Nothing here is a guess: every number in the answer was read back from
    the system that is being asked about.
    """
    battery = _battery_reading()
    scheme = _power_scheme()
    if battery is None and scheme is None:
        return outcome.failed(
            "computer_settings",
            "Windows reported neither a battery nor a power plan, so there is "
            "nothing to say about the energy of this computer",
            wanted="energy_status")

    facts: dict = {}
    if scheme is not None:
        guid, name = scheme
        facts["power_scheme"] = name
        facts["energy_saver"] = (
            "on" if guid == _ENERGY_SAVER_GUID
            or "energy saver" in name.lower() else "off")

    if battery is None:
        return outcome.unverified(
            "computer_settings",
            "the battery of this computer could not be read, so no level is "
            "reported" + (
                f" (the power plan is {facts['power_scheme']})"
                if "power_scheme" in facts else ""),
            wanted="energy_status", **facts)

    if battery["present"] is not True:
        if battery["present"] is False:
            # A desktop with no battery: say so, and say no percentage.
            return outcome.done(
                "computer_settings", wanted="energy_status",
                battery="this computer has no battery", **facts, verified=True)
        return outcome.unverified(
            "computer_settings",
            "this computer does not say whether it has a battery, so no level "
            "is reported",
            wanted="energy_status", **facts)

    percent = battery["percent"]
    if percent is None:
        return outcome.unverified(
            "computer_settings",
            "Windows does not report the battery level of this computer, so "
            "no percentage is reported",
            wanted="energy_status", charging=_yes_no(battery["charging"]),
            **facts)

    return outcome.done(
        "computer_settings", wanted="energy_status",
        battery_percent=percent, charging=_yes_no(battery["charging"]),
        on_ac=_yes_no(battery["on_ac"]), **facts, verified=True)

ACTION_MAP: dict[str, callable] = {
    "volume_up":           volume_up,
    "volume_down":         volume_down,
    "mute":                volume_mute,
    "unmute":              volume_unmute,
    "toggle_mute":         volume_toggle_mute,
    "media_play_pause":    media_play_pause,
    "media_next":          media_next,
    "next_track":          media_next,
    "media_previous":      media_previous,
    "previous_track":      media_previous,
    "media_stop":          media_stop,
    "brightness_up":       brightness_up,
    "brightness_down":     brightness_down,
    "sleep_display":       sleep_display,
    "screen_off":          sleep_display,
    "pause_video":         pause_video,
    "play_pause":          pause_video,
    "close_app":           close_app,
    "close_window":        close_window,
    "full_screen":         full_screen,
    "fullscreen":          full_screen,
    "minimize":            minimize_window,
    "maximize":            maximize_window,
    "snap_left":           snap_left,
    "snap_right":          snap_right,
    "switch_window":       switch_window,
    "show_desktop":        show_desktop,
    "task_manager":        open_task_manager,
    "focus_search":        focus_search,
    "refresh_page":        refresh_page,
    "reload":              refresh_page,
    "close_tab":           close_tab,
    "new_tab":             new_tab,
    "next_tab":            next_tab,
    "prev_tab":            prev_tab,
    "go_back":             go_back,
    "go_forward":          go_forward,
    "zoom_in":             zoom_in,
    "zoom_out":            zoom_out,
    "zoom_reset":          zoom_reset,
    "find_on_page":        find_on_page,
    "scroll_up":           scroll_up,
    "scroll_down":         scroll_down,
    "scroll_top":          scroll_top,
    "scroll_bottom":       scroll_bottom,
    "page_up":             page_up,
    "page_down":           page_down,
    "copy":                copy,
    "paste":               paste,
    "cut":                 cut,
    "undo":                undo,
    "redo":                redo,
    "select_all":          select_all,
    "save":                save_file,
    "enter":               press_enter,
    "escape":              press_escape,
    "screenshot":          take_screenshot,
    "lock_screen":         lock_screen,
    "open_settings":       open_system_settings,
    "file_explorer":       open_file_explorer,
    "open_run":            open_run,
    "dark_mode":           dark_mode,
    "toggle_wifi":         toggle_wifi,
    "restart":             restart_computer,
    "shutdown":            shutdown_computer,
}

# ── What needs a human, and what just needs an undo ──────────────────────────
#
# The old gate was `_DANGEROUS_ACTIONS = {"restart", "shutdown"}` checked against
# a `confirmed` parameter the MODEL filled in — so the model confirmed its own
# shutdowns, and everything else (switching off the WiFi this assistant is
# talking over) had no gate at all.
#
# Two lists now, and the split is about reversibility, not about how alarming
# the word sounds:
#
#   _IRREVERSIBLE — a human presses a button on the HUD. Nothing else.
#   everything else — done immediately, with an undo pushed if it can be undone.
#
# Asking before every action is what makes an assistant unusable, and every
# question costs a round trip. Undo is both faster and safer than a prompt.
_IRREVERSIBLE = {
    "restart":     ("Restart this computer",
                    "Anything unsaved will be lost. The computer restarts in 10 seconds."),
    "shutdown":    ("Shut this computer down",
                    "Anything unsaved will be lost. The computer powers off in 10 seconds."),
    # Not obviously destructive, and that is exactly why it was missed: turning
    # the WiFi off cuts the assistant's own connection to the Live API, so it
    # cannot be asked to turn it back on.
    "toggle_wifi": ("Switch WiFi off or on",
                    "If this switches WiFi off, T.E.E.M.O. loses its connection "
                    "and cannot switch it back on by voice."),
}

# Kept so anything still importing the old name keeps working.
_DANGEROUS_ACTIONS = set(_IRREVERSIBLE)


# ── Local intent resolution ──────────────────────────────────────────────────
#
# This used to be an entire extra Gemini call, made INSIDE the tool: the Live
# model called computer_settings, and computer_settings then asked a second
# model which action was meant. Every "turn the volume down" paid for two round
# trips — and when that second call failed, the fallback was
# `description.lower().replace(" ", "_")`, which turns the Turkish for "turn it
# down" into `sesi_kis`, i.e. straight to "Unknown action".
#
# Nothing here needs a language model. The Live model already understands the
# sentence; it only needed the vocabulary, which the tool declaration now spells
# out in full. What is left is spelling tolerance, and difflib does that in
# microseconds instead of ~600 ms and a quota unit.
_ALIASES = {
    "volume_up":       ("louder", "raise volume", "turn it up", "increase volume"),
    "volume_down":     ("quieter", "lower volume", "turn it down", "decrease volume"),
    "mute":            ("silence", "sound off", "no sound"),
    "brightness_up":   ("brighter", "raise brightness", "increase brightness"),
    "brightness_down": ("dimmer", "dim", "lower brightness", "decrease brightness"),
    "close_window":    ("close this", "close it"),
    "full_screen":     ("fullscreen", "maximise screen"),
    "show_desktop":    ("minimise everything", "go to desktop"),
    "lock_screen":     ("lock", "lock the pc", "lock computer"),
    "sleep_display":   ("screen off", "turn off the screen", "display off"),
    "dark_mode":       ("night mode", "light mode", "toggle theme"),
    "toggle_wifi":     ("wifi", "wi-fi", "internet off", "internet on"),
    "task_manager":    ("processes", "task list"),
    "screenshot":      ("capture screen", "take a screenshot", "snip"),
    "refresh_page":    ("refresh", "reload page"),
    "new_tab":         ("open a tab", "open new tab"),
    "shutdown":        ("power off", "turn off the computer", "switch off the pc"),
    "restart":         ("reboot", "restart the pc"),
    "energy_status":   ("energy_status", "battery_status", "battery",
                        "battery level", "how much battery", "charge level",
                        "energy saver", "power plan", "power status"),
    "unmute":          ("sound on", "unmute"),
    "media_next":      ("next song", "next track", "skip song"),
    "media_previous":  ("previous song", "previous track"),
}

_VALUE_ACTIONS = {"volume_set", "type_text", "press_key", "reload_n",
                  "scroll_up", "scroll_down", "brightness_set",
                  "seek_forward", "seek_backward"}

# `value` is how far these move, not how many times to call them.
_STEP_ACTIONS = {"volume_up", "volume_down", "brightness_up", "brightness_down"}

# A key press worth repeating: "zoom in three times", "back two pages".
_REPEATABLE = {"zoom_in", "zoom_out", "next_tab", "prev_tab", "go_back", "go_forward",
               "page_up", "page_down", "undo", "redo", "enter", "escape", "close_tab",
               "new_tab", "media_next", "media_previous"}


def _normalise(text: str) -> str:
    return (text or "").strip().lower().replace("-", "_").replace(" ", "_")


def _detect_action(description: str) -> dict:
    """Resolve a free-text description to an action name, locally.

    Returns {"action": name, "value": ...}; `action` is "" when nothing matched,
    which the caller turns into a message naming real candidates — one round
    trip, and only in the case that used to cost one anyway."""
    raw  = (description or "").strip()
    norm = _normalise(raw)
    if not norm:
        return {"action": "", "value": None}

    known = set(ACTION_MAP) | _VALUE_ACTIONS

    # 1. Already an action name.
    if norm in known:
        return {"action": norm, "value": None}

    low = raw.lower()

    # 2. "set volume to 30", "sesi 30 yap" — a number next to a volume word.
    num = re.search(r"(\d{1,3})\s*%?", low)
    if num and any(w in low for w in ("volume", "ses", "sound", "lautstark", "громкость")):
        return {"action": "volume_set", "value": max(0, min(100, int(num.group(1))))}

    # 3. Alias phrases.
    for action, phrases in _ALIASES.items():
        if any(_normalise(p) == norm or p in low for p in phrases):
            return {"action": action, "value": None}

    # 4. Fuzzy match on the action names — catches "fullscren", "volumeup".
    import difflib
    close = difflib.get_close_matches(norm, sorted(known), n=1, cutoff=0.72)
    if close:
        return {"action": close[0], "value": None}

    # 5. Substring: "increase_the_brightness" contains "brightness".
    for action in sorted(known, key=len, reverse=True):
        if len(action) > 4 and (action in norm or norm in action):
            return {"action": action, "value": None}

    return {"action": "", "value": None}


def _suggest(description: str) -> str:
    """What to tell the model when nothing matched. Names real actions so its
    retry lands, instead of the old 'Unknown action' dead end."""
    import difflib
    near = difflib.get_close_matches(_normalise(description),
                                     sorted(ACTION_MAP), n=5, cutoff=0.3)
    hint = ", ".join(near) if near else ", ".join(sorted(ACTION_MAP)[:12])
    return outcome.ask(
        "computer_settings",
        f"'{description}' does not match any computer action - the closest "
        f"are: {hint}. Which was meant?",
        said=description)


def _run_confirmed(func, action: str) -> str:
    """Do a confirmed action and report what it actually did.

    The old callback was `(f(), f"{a} done.")[1]`: it ran the action, threw
    away whatever the action had to say, and wrote "<action> done." in its
    place. So a wireless radio that stayed exactly as it was was logged as
    switched, and the restart Windows had refused was logged as a restart.

    Now the action speaks for itself, and only an action that says nothing
    at all is reported as done.
    """
    try:
        result = func()
    except Exception as e:
        print(f"[Settings] Confirmed action failed ({action}): {e}")
        return outcome.failed("computer_settings", f"it failed: {e}",
                              wanted=action)
    if isinstance(result, str) and result.startswith("status="):
        return result
    return outcome.unverified(
        "computer_settings",
        "the action was performed, but it reported nothing about what it did, "
        "so the effect could not be confirmed",
        wanted=action)


def _reported(result) -> str | None:
    """The outcome an action reported for itself, or None if it reported none."""
    if isinstance(result, str) and result.startswith("status="):
        return result
    return None


def _succeeded(result) -> bool:
    """Whether an action that reported its own outcome reported success."""
    reported = _reported(result)
    if reported is None:
        return True      # an action that reports nothing raised no exception
    return reported.startswith(f"status={outcome.DONE} ")


def computer_settings(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    if not _PYAUTOGUI:
        return outcome.failed(
            "computer_settings",
            "pyautogui is not installed, so the keyboard cannot be driven - "
            "run: pip install pyautogui")

    params      = parameters or {}
    raw_action  = params.get("action", "").strip()
    description = params.get("description", "").strip()
    value       = params.get("value", None)

    if not raw_action and description:
        detected   = _detect_action(description)
        raw_action = detected.get("action", "")
        if value is None:
            value = detected.get("value")

    action = raw_action.lower().strip().replace(" ", "_").replace("-", "_")

    if not action:
        return _suggest(description or raw_action)

    print(f"[Settings] Action: {action}  Value: {value}  OS: {_OS}")
    if player:
        print(f"[Settings] {action}")            # terminal, not the HUD

    # ── The gate ─────────────────────────────────────────────────────────────
    # A human presses a button, or this does not happen. The model can no longer
    # write its own permission slip, and the action itself is handed to the UI
    # rather than performed here — so returning early is not "declining", it is
    # "parked until someone says yes".
    if action in _IRREVERSIBLE:
        title, detail = _IRREVERSIBLE[action]
        func = ACTION_MAP.get(action)
        if func is None:
            return outcome.failed("computer_settings",
                                  "that is not an action this tool has",
                                  wanted=raw_action)
        if confirm.pending_title():
            return outcome.ask(
                "computer_settings",
                "there is already a confirmation waiting on screen - the user "
                "has to answer that one first",
                wanted=action)
        return action_policy.ask_once(
            "computer_settings", action, title, detail,
            lambda f=func, a=action: _run_confirmed(f, a),
        )

    if action in ("volume_set", "set_volume"):
        # No level means no guess: the old default of 50 turned "set the volume"
        # with a lost value into a volume change nobody asked for.
        target = _number(value)
        if target is None:
            return outcome.ask("computer_settings",
                               "what level should the volume be, from 0 to 100?",
                               wanted="volume_set")
        target = max(0, min(100, target))
        try:
            before = volume_get()
            volume_set(target)
            if before is not None:
                push_undo(f"volume {before}% → {target}%",
                          lambda b=before: (volume_set(b), f"Back to {b}%.")[1])
            now = volume_get()
            if now is None:
                return outcome.unverified(
                    "computer_settings",
                    "this system does not report its volume, so the new "
                    "level could not be read back", wanted=action, asked=target)
            return outcome.done("computer_settings", wanted=action,
                                volume=now, verified=True)
        except Exception as e:
            return outcome.failed("computer_settings",
                                  f"the volume could not be set: {e}", wanted=action)

    if action in ("type_text", "write_on_screen", "type", "write"):
        text = str(value or params.get("text", "")).strip()
        if not text:
            return outcome.failed("computer_settings",
                                  "no text was given to type", wanted=action)
        enter_after = str(params.get("press_enter", "false")).lower() in ("true", "1", "yes")
        try:
            type_text(text, press_enter_after=enter_after)
        except Exception as e:
            return outcome.failed("computer_settings",
                                  f"the text could not be typed: {e}",
                                  wanted=action)
        # The characters are COUNTED, never quoted back: what gets typed is
        # often a password, and the result of a tool call is a thing people
        # paste into bug reports.
        #
        # And it is not a "done": the keys went to whatever had focus, and
        # nothing here reads that window back.
        return outcome.unverified(
            "computer_settings",
            "the text was typed into whatever had focus, but nothing here "
            "reads the screen back, so it could not be confirmed",
            wanted=action, typed_characters=len(text))

    if action == "press_key":
        key = str(value or params.get("key", "")).strip()
        if not key:
            return outcome.failed("computer_settings",
                                  "no key was given to press", wanted=action)
        times = max(1, min(20, _number(params.get("times")) or 1))
        for _ in range(times):
            press_key(key)
        return outcome.unverified(
            "computer_settings",
            "the key was sent to the window in front, but nothing here reads "
            "that window back, so the effect could not be confirmed",
            wanted=action, key=key,
            times=times if times > 1 else None)

    if action in ("reload_n", "refresh_n", "reload_page_n"):
        try:
            reload_page_n(int(value or 1))
        except Exception as e:
            return outcome.failed("computer_settings",
                                  f"the reload failed: {e}", wanted=action)
        return outcome.unverified(
            "computer_settings",
            "the reload was sent to the window in front, but nothing here "
            "reads that window back, so the reload could not be confirmed",
            wanted=action, times=value or 1)

    if action in ("scroll_up", "scroll_down"):
        notches = _number(value)
        notches = 5 if notches is None else abs(notches)
        if notches > 60:
            # An amount in pyautogui's Windows units, as the old default (500) was.
            notches = max(1, round(notches / 120))
        notches = max(1, min(60, notches))
        (scroll_up if action == "scroll_up" else scroll_down)(notches)
        return outcome.unverified(
            "computer_settings",
            "the scroll was sent to the window in front, but nothing here "
            "reads that window back, so it could not be confirmed",
            wanted=action, notches=notches)

    if action in ("brightness_set", "set_brightness"):
        target = _number(value)
        if target is None:
            return outcome.ask("computer_settings",
                               "what brightness, from 0 to 100?", wanted="brightness_set")
        target = max(0, min(100, target))
        before = brightness_get()
        try:
            brightness_set(target)
        except Exception as e:
            return outcome.failed("computer_settings",
                                  f"the brightness could not be set: {e}", wanted=action)
        if before is not None:
            push_undo(f"brightness {before}% → {target}%",
                      lambda b=before: (brightness_set(b), f"Brightness back to {b}%.")[1])
        now = brightness_get()
        if now is None:
            return outcome.unverified(
                "computer_settings",
                "this display does not report its brightness, so the new level could "
                "not be read back - an external monitor usually cannot be set from the "
                "computer at all", wanted=action, asked=target)
        return outcome.done("computer_settings", wanted=action, brightness=now, verified=True)

    if action in ("energy_status", "battery_status", "power_status",
                  "battery", "energy"):
        # A reading, not a change: the battery level, the charging state and
        # the power plan, as Windows reports them. It takes no value.
        return energy_status()

    if action in ("seek_forward", "seek_backward", "fast_forward", "rewind"):
        seconds = _number(value)
        seconds = 10 if seconds is None else max(1, abs(seconds))
        forward = action in ("seek_forward", "fast_forward")
        try:
            presses = seek_presses(seconds if forward else -seconds)
        except Exception as e:
            return outcome.failed("computer_settings", f"it failed: {e}", wanted=action)
        return outcome.unverified(
            "computer_settings",
            f"sent {presses} arrow-key presses to the window in front - most web players "
            f"move {SEEK_PER_PRESS} seconds a press, but players differ and none reports "
            f"its position. If the video is on the assistant's own display, use "
            f"video_player action='seek' instead, which is exact",
            wanted=action, seconds=seconds)

    func = ACTION_MAP.get(action)
    if not func:
        return _suggest(raw_action or description)

    # ── Capture "before" so the change can be taken back ─────────────────────
    # Read-then-write is the whole mechanism for settings: there is no clever
    # inverse to compute, just a value to remember. Where the platform will not
    # tell us the current value, nothing is registered — an undo that restores
    # a guess is worse than no undo at all.
    _before = None
    if action in ("volume_up", "volume_down"):
        _before = ("volume", volume_get())
    elif action in ("mute", "unmute", "toggle_mute"):
        _before = ("mute", mute_get())
    elif action in ("brightness_up", "brightness_down"):
        _before = ("brightness", brightness_get())

    # How far, and how many times. "Louder by 30" is a step of 30; "zoom in three
    # times" is three presses. Neither number used to reach the action at all.
    step = _number(value)
    step = 10 if step is None else max(1, min(100, abs(step)))
    times = 1
    if action in _REPEATABLE:
        times = _number(params.get("times"))
        if times is None:
            times = _number(value)
        times = max(1, min(20, times or 1))

    result = None
    try:
        if action in _STEP_ACTIONS:
            result = func(step)
        else:
            for i in range(times):
                result = func()
                if i + 1 < times:
                    time.sleep(0.08)
    except Exception as e:
        print(f"[Settings] Action failed ({action}): {e}")
        return outcome.failed("computer_settings", f"it failed: {e}",
                              wanted=action)

    if _before:
        kind, old = _before
        if old is not None:
            if kind == "mute":
                push_undo(f"sound ({action})",
                          lambda b=old: (mute_set(b), "Sound back as it was.")[1])
            elif kind == "volume":
                push_undo(f"volume ({action})",
                          lambda b=old: (volume_set(b), f"Volume back to {b}%.")[1])
            elif kind == "brightness":
                push_undo(f"brightness ({action})",
                          lambda b=old: (brightness_set(b), f"Brightness back to {b}%.")[1])
    elif action == "dark_mode" and _succeeded(result):
        # A pure toggle: calling it again is the undo. Only when the toggle
        # worked — an undo for a change that did not happen is a second lie.
        push_undo("dark mode toggled",
                  lambda: (dark_mode(), "Theme switched back.")[1])

    # ── Say what is known, and only that ─────────────────────────────────────
    #
    # This used to be `return f"Done: {action}."`, sent whether or not anything
    # had changed, because `func()` returns nothing and nobody looked. Then it
    # became `outcome.done(..., verified=True)` for EVERY action in this file,
    # including the ones that had swallowed an exception on the way — a
    # swallowed exception and a "done, verified" are the same claim.
    #
    # WHERE THE LINE IS NOW
    #     An action that checked its own effect says what it found, and that
    #     answer is the answer: sleep_display, dark_mode, toggle_wifi,
    #     restart, shutdown and energy_status all read the system back before
    #     they claim anything, and they are the ones that may say "done".
    #
    #     Volume and brightness are a state the machine will read back, and the
    #     "before" value has ALREADY been read two blocks above for undo — so
    #     checking costs one more call and turns a claim into a fact.
    #
    #     The other fifty-odd actions are a keystroke sent to whatever has
    #     focus, and there is NOTHING to read back. They used to be reported
    #     as "done, verified" because the user is looking straight at them —
    #     but "verified" is a word about the SYSTEM's state, not about what a
    #     person is watching, and a claim nobody checked is the thing this
    #     file exists to stop. So they are reported as what they are: sent,
    #     and not confirmed.
    reported = _reported(result)
    if reported is not None:
        return reported

    if _before and _before[0] == "mute":
        old_state = _before[1]
        now_muted = mute_get()
        if now_muted is None:
            return outcome.unverified(
                "computer_settings",
                "this system does not report whether its sound is muted, so the "
                "change could not be read back", wanted=action)
        wanted_state = {"mute": True, "unmute": False}.get(
            action, (not old_state) if old_state is not None else now_muted)
        if now_muted != wanted_state:
            return outcome.unverified(
                "computer_settings",
                f"the sound still reads {'muted' if now_muted else 'not muted'}",
                wanted=action, muted=now_muted)
        return outcome.done("computer_settings", wanted=action, muted=now_muted, verified=True)

    if _before:
        kind, old_value = _before
        now = volume_get() if kind == "volume" else brightness_get()
        if old_value is None or now is None:
            return outcome.unverified(
                "computer_settings",
                f"this system does not report its {kind}, so the change could "
                f"not be read back",
                wanted=action)
        if now == old_value:
            return outcome.unverified(
                "computer_settings",
                f"the {kind} still reads {now}% - it may already have been at "
                f"the limit, or this system may not have accepted the change",
                wanted=action, value=now)
        return outcome.done("computer_settings", wanted=action,
                            **{kind: now}, changed_by=step, verified=True)

    return outcome.unverified(
        "computer_settings",
        "the keystroke was sent to whatever had focus, but nothing here reads "
        "that window back, so the effect could not be confirmed",
        wanted=action, times=times if times > 1 else None)


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "computer_settings",
    "description": "Controls the computer: volume, mute, brightness, media playback (play/pause, next, previous, seek), window management, keyboard shortcuts, typing text on screen, fullscreen, dark mode, WiFi, restart, shutdown, scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page, and the battery and power plan (energy_status). Use for ANY single computer control command. NUMBERS MATTER: a level the user names is volume_set / brightness_set with value = that level ('volume to 100', 'max' = 100, 'half' = 50); an amount to change by is volume_up / volume_down / brightness_up / brightness_down with value = the amount ('30 louder' = 30; no amount = 10). Never approximate a level with repeated up/down calls. For a key worth repeating (zoom_in, next_tab, go_back, page_down, undo...) pass `times`. Media keys reach music and video apps without focusing them; seek_forward / seek_backward take value = seconds and act on the app in front (for a video on the assistant's own display use video_player instead). energy_status READS the battery, whether it is charging and the power plan; it changes nothing, and on a computer with no battery it says so instead of giving a percentage. restart, shutdown and toggle_wifi put a confirmation on the user's screen and do NOT happen until they press it — never claim they are done. Volume, mute, brightness and dark mode can be reversed with the `undo` tool.",
    # Used in place of `description` only when a small model is on a tool
    # budget - Gemini still gets the full text. See providers/schema.py.
    "brief": (
        "Changes a computer setting: volume, brightness, WiFi, window layout, power. "
        "Name the action in 'action'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            # The exact vocabulary, spelled out.
            #
            # This used to say only "The action to perform", so the model
            # usually filled `description` instead — and computer_settings then
            # made a SECOND Gemini call, inside the tool, purely to translate
            # that sentence into one of these names. Every "turn the volume
            # down" cost two model round trips.
            #
            # Naming the actions here costs ~600 characters once per session and
            # removes a whole round trip from every computer command.
            "action": {
                "type": "STRING",
                "description": (
                    "The exact action. Prefer this over `description` — pick one of: "
                    "volume_set | volume_up | volume_down | mute | unmute | toggle_mute | "
                    "brightness_set | brightness_up | brightness_down | sleep_display | "
                    "media_play_pause | media_next | media_previous | media_stop | "
                    "seek_forward | seek_backward | "
                    "pause_video | close_window | full_screen | "
                    "minimize | maximize | snap_left | snap_right | "
                    "switch_window | show_desktop | task_manager | focus_search | "
                    "refresh_page | close_tab | new_tab | next_tab | prev_tab | "
                    "go_back | go_forward | zoom_in | zoom_out | zoom_reset | "
                    "find_on_page | scroll_up | scroll_down | scroll_top | "
                    "scroll_bottom | page_up | page_down | copy | paste | cut | "
                    "undo | redo | select_all | save | enter | escape | press_key | "
                    "type_text | screenshot | lock_screen | open_settings | "
                    "file_explorer | open_run | dark_mode | toggle_wifi | "
                    "restart | shutdown | energy_status"
                )
            },
            "description": {
                "type": "STRING",
                "description": (
                    "Fallback only, when no action name above fits. "
                    "Resolved locally — no extra model call."
                )
            },
            "value": {
                "type": "STRING",
                "description": (
                    "volume_set / brightness_set: the level, 0-100. volume_up/down, "
                    "brightness_up/down: how much to change by (default 10). "
                    "seek_forward/backward: seconds. scroll_up/down: wheel notches. "
                    "type_text: the text. press_key: the key name. "
                    "energy_status takes no value."
                )
            },
            "times": {
                "type": "INTEGER",
                "description": "How many times to repeat a key action (zoom_in, next_tab, go_back, press_key...). Default 1."
            }
        },
        "required": []
    },
    "handler": computer_settings,
}
