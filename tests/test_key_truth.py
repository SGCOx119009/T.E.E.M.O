"""
tests/test_key_truth.py — where the Gemini key really is.

WHAT IS BEING PROVED
    1. The Credential Locker is where the key lives, and every reader looks
       there: the engine, the window, the ignition.
    2. The settings file is NOT where the key lives. A key that is only in the
       vault still counts, so the user is not asked for a key they gave.
    3. A key is never lost to a silent failure: the store has to confirm it
       kept the key before the only other copy is deleted.
    4. The setup form saves into the locker, and if it cannot, the form stays
       open with the key still in it.

THE BUG THIS FILE IS ABOUT
    The engine read the key from `config/api_keys.json`, the configuration
    manager had moved it to the Credential Locker and deleted it from that
    file. So the credential was safe in the vault and invisible to everything
    that used it: the model was told the key was invalid while the key sat
    right there. The window asked for the key on every single start.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from memory import config_manager  # noqa: E402


# ── the locker, as the tests need it ───────────────────────────────────────

class _FakeLocker:
    """The Windows Credential Locker, standing in for the real one.

    `keeps` decides whether the store keeps what it is given. A store that
    accepts a write and loses it is the worst case there is, and it is the one
    that must never take the user's only copy of the key with it.
    """

    def __init__(self, value: str | None = None, keeps: bool = True):
        self.value = value
        self.keeps = keeps
        self.writes: list[str] = []
        self.backend = type("WinVaultKeyring", (), {"priority": 5})()

    def get_keyring(self):
        return self.backend

    def get_password(self, service: str, account: str) -> str | None:
        if account == "gemini_api_key":
            return self.value
        return None

    def set_password(self, service: str, account: str, value: str) -> None:
        if account == "gemini_api_key":
            self.writes.append(value)
            if self.keeps:
                self.value = value

    def delete_password(self, service: str, account: str) -> None:
        if account == "gemini_api_key":
            self.value = None


@pytest.fixture(scope="module")
def qt_app():
    """A Qt application, for the parts of the window that need one."""
    import os

    previous = os.environ.get("QT_QPA_PLATFORM")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    yield app
    if previous is None:
        os.environ.pop("QT_QPA_PLATFORM", None)
    else:
        os.environ["QT_QPA_PLATFORM"] = previous


@pytest.fixture()
def locker(monkeypatch):
    """A locker of our own, and a configuration file of our own."""
    import tempfile

    temp_dir = Path(tempfile.mkdtemp())
    config_dir = temp_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config_manager, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_manager, "CONFIG_FILE", config_dir / "api_keys.json")

    fake = _FakeLocker()
    monkeypatch.setitem(sys.modules, "keyring", fake)
    yield fake
    from core import gemini

    gemini.invalidate_api_key_cache()


def _write_config(locker, **fields):
    config_manager.CONFIG_FILE.write_text(
        json.dumps(fields), encoding="utf-8"
    )


# ── the locker is where the key is ─────────────────────────────────────────

def test_the_key_is_read_from_the_locker(locker):
    locker.value = "AIza-lockered"
    _write_config(locker, language="es")
    assert config_manager.read_gemini_key() == "AIza-lockered"


def test_a_key_in_the_locker_counts_as_configured(locker):
    locker.value = "AIza-lockered"
    _write_config(locker, language="es")
    assert config_manager.has_gemini_key() is True


def test_the_engine_finds_the_key_in_the_locker(locker):
    """The bug, in one test: the file has no key and the vault has one."""
    from core import gemini

    locker.value = "AIza-lockered"
    _write_config(locker, language="es")
    gemini.invalidate_api_key_cache()
    assert gemini.api_key(refresh=True) == "AIza-lockered"


def test_the_window_does_not_ask_for_a_key_that_is_already_saved(locker):
    """A key in the vault must stop the setup form from appearing again."""
    from gui.window import MainWindow

    locker.value = "AIza-lockered"
    _write_config(locker, language="es")
    assert MainWindow._check_config(None) is True


def test_the_ignition_reports_a_key_that_is_already_saved(locker):
    from gui import boot

    locker.value = "AIza-lockered"
    _write_config(locker, language="es")
    checks = boot.collect_checks(probe_audio=False)
    ai = [c for c in checks if c[0] == "NÚCLEO DE IA"]
    assert ai and "clave en su sitio" in ai[0][2]


def test_nothing_is_asked_when_there_is_no_key_at_all(locker):
    from gui.window import MainWindow

    _write_config(locker, language="es")
    assert MainWindow._check_config(None) is False


# ── the file is only a fallback ────────────────────────────────────────────

def test_a_legacy_copy_in_the_file_is_still_read(locker):
    _write_config(locker, gemini_api_key="AIza-legacy")
    assert config_manager.read_gemini_key() == "AIza-legacy"


def test_reading_the_key_never_rewrites_the_file(locker):
    """Looking for a key must not change anything: a reader that rewrites the
    file is a reader that can lose what is in it."""
    _write_config(locker, gemini_api_key="AIza-legacy", language="es")
    before = config_manager.CONFIG_FILE.read_text(encoding="utf-8")
    config_manager.read_gemini_key()
    config_manager.has_gemini_key()
    assert config_manager.CONFIG_FILE.read_text(encoding="utf-8") == before


def test_an_unavailable_locker_does_not_kill_the_reader(locker):
    """A locker that cannot be reached is not a reason to crash a screen."""
    class _Broken:
        def get_keyring(self):
            raise RuntimeError("no locker here")

        def get_password(self, service, account):
            return None

        def set_password(self, service, account, value):
            raise RuntimeError("no locker here")

        def delete_password(self, service, account):
            raise RuntimeError("no locker here")

    sys.modules["keyring"] = _Broken()
    _write_config(locker, gemini_api_key="AIza-legacy")
    assert config_manager.read_gemini_key() == "AIza-legacy"
    assert config_manager.has_gemini_key() is True


def test_a_missing_key_is_an_error_where_it_always_was_one(locker):
    _write_config(locker, language="es")
    with pytest.raises(KeyError):
        config_manager.require_gemini_key()


# ── a key is never lost to a silent failure ────────────────────────────────

def test_the_plaintext_copy_survives_a_locker_that_loses_the_key(locker):
    """The store accepted the write and lost it. The file keeps the key."""
    locker.keeps = False
    _write_config(locker, gemini_api_key="AIza-legacy")
    with pytest.raises(RuntimeError):
        config_manager.get_gemini_key()
    saved = config_manager.CONFIG_FILE.read_text(encoding="utf-8")
    assert "AIza-legacy" in saved


def test_saving_reports_a_locker_that_loses_the_key(locker):
    """Saying 'saved' when it was not is the worst thing this code could do."""
    locker.keeps = False
    with pytest.raises(RuntimeError):
        config_manager.save_api_keys("AIza-new")


def test_a_failed_save_keeps_the_credential_that_was_there(locker):
    locker.value = "AIza-previous"
    locker.keeps = False
    with pytest.raises(RuntimeError):
        config_manager.save_api_keys("AIza-new")
    assert locker.value == "AIza-previous"


def test_saving_keeps_the_key_out_of_the_file(locker):
    config_manager.save_api_keys("AIza-new")
    assert locker.value == "AIza-new"
    assert "AIza-new" not in config_manager.CONFIG_FILE.read_text(encoding="utf-8")


def test_a_new_key_reaches_the_live_client(locker):
    """The engine caches its key; a saved key has to replace the cached one."""
    from core import gemini

    _write_config(locker, language="es")
    gemini.invalidate_api_key_cache()
    assert gemini.api_key(refresh=True) == ""
    config_manager.save_api_keys("AIza-new")
    assert gemini.api_key() == "AIza-new"


# ── the setup form ─────────────────────────────────────────────────────────

class _FakeLog:
    def __init__(self):
        self.lines: list[str] = []

    def append_log(self, text):
        self.lines.append(text)


class _FakeWindow:
    """Just enough of the window for the setup path."""

    def __init__(self):
        self._log = _FakeLog()
        self._ready = False
        self._overlay = None
        self._assistant_name = ""
        self._states: list[str] = []
        self._boots: list[bool] = []

    def _apply_state(self, state):
        self._states.append(state)

    def _start_boot(self):
        self._boots.append(True)

    def _open_providers(self):
        pass


def test_the_form_saves_the_key_in_the_locker(locker, qt_app):
    from gui.window import MainWindow

    _write_config(locker, language="es")
    fake = _FakeWindow()
    MainWindow._on_setup_done(fake, "AIza-typed")
    assert locker.value == "AIza-typed"
    saved = config_manager.CONFIG_FILE.read_text(encoding="utf-8")
    assert "AIza-typed" not in saved
    assert fake._ready is True


def test_a_locker_that_fails_leaves_the_form_open(locker, qt_app):
    """Nothing is lost: the key stays in the field and the user can retry."""
    from gui.window import MainWindow

    locker.keeps = False
    _write_config(locker, language="es")
    fake = _FakeWindow()
    MainWindow._on_setup_done(fake, "AIza-typed")
    assert fake._ready is False
    assert locker.value is None
    saved = config_manager.CONFIG_FILE.read_text(encoding="utf-8")
    assert "AIza-typed" not in saved


def test_running_without_gemini_does_not_delete_a_saved_key(locker, qt_app):
    """Choosing the local engine is a choice, not a deletion."""
    from gui.window import MainWindow

    locker.value = "AIza-already-there"
    _write_config(locker, language="es")
    fake = _FakeWindow()
    MainWindow._on_setup_done(fake, "")
    assert locker.value == "AIza-already-there"
    assert config_manager.load_api_keys().get("local_only") is True
