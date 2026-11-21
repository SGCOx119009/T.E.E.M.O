# config/__init__.py
"""The operating system is not a question here — T.E.E.M.O. runs on Windows.

The configuration file used to carry an "os_system" key that told the code
which platform it was running on, and every platform-sensitive decision branched
on it. On a Windows-only product that key is a fact, not a setting: it was
writable, it could be wrong, and it turned hundreds of decisions into
configuration that whoever edited a JSON file could change.

What remains here is the part of this module that is genuinely about
configuration: reading the file.
"""
import json
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent / "api_keys.json"


def get_config() -> dict:
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def get_os() -> str:
    """Always "windows". Kept because a few readers still ask."""
    return "windows"


def is_windows() -> bool:
    return True
