"""
tests/test_gui_localization.py — the language and region selectors.

WHAT IS BEING PROVED
    1. The window still builds. The translation pass touched every panel,
       and a widget that was left pointing at a string that does not exist
       is a crash at startup, not a wrong word.
    2. The two selectors carry the right data: the language combo's VALUES
       are the language codes, and the wake-mode combo's VALUES survive the
       translation — a language change must never turn "voice" into "any
       voice" in the thing that gets stored.
    3. Changing the language really changes the interface, in place.
    4. The catalogue is complete: every key has all five languages.
    5. A person who speaks Spanish and has chosen nothing gets Spanish.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from core import localization  # noqa: E402
from memory import config_manager  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    os_env = {}
    os_env["QT_QPA_PLATFORM"] = "offscreen"
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
def window(qt_app, tmp_path, monkeypatch):
    """A real MainWindow, with a config of our own."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config_manager, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_manager, "CONFIG_FILE", config_dir / "api_keys.json")

    from gui.window import MainWindow
    win = MainWindow(str(BASE / "core" / "face_model.obj"))
    yield win
    win.close()
    win.deleteLater()


def test_the_window_still_builds(window):
    assert window is not None
    assert window._language_combo is not None
    assert window._region_combo is not None


def test_the_language_combo_carries_the_five_languages(window):
    codes = [window._language_combo.itemData(i)
             for i in range(window._language_combo.count())]
    assert codes == ["en", "es", "pt", "fr", "zh"]
    # The visible text is the NATIVE name: that is how a person asks for it.
    assert window._language_combo.itemText(1) == "Español"


def test_the_region_combo_carries_the_regions(window):
    assert window._region_combo.count() == len(localization.regions())
    assert window._region_combo.isEditable()


def test_changing_the_language_changes_the_interface(window):
    before = window._selector_note.text()
    index = window._language_combo.findData("fr")
    assert index >= 0
    window._language_combo.setCurrentIndex(index)
    after = window._selector_note.text()
    assert after != before
    assert localization.get_language() == "fr"


def test_the_wake_mode_values_survive_the_translation(window):
    """The translation changes the WORDS, never what gets stored."""
    window._language_combo.setCurrentIndex(window._language_combo.findData("es"))
    combo = window._wake_mode_combo
    values = [combo.itemData(i) for i in range(combo.count())]
    assert values == ["phrase", "voice"]
    texts = [combo.itemText(i) for i in range(combo.count())]
    assert texts[0] != texts[1]
    assert all(text.strip() for text in texts)


def test_applying_the_language_twice_does_not_duplicate_items(window):
    window._apply_language("es", "es")
    window._apply_language("es", "fr")
    window._apply_language("fr", "es")
    assert window._language_combo.count() == 5
    assert window._region_combo.count() == len(localization.regions())
    assert window._wake_mode_combo.count() == 2


def test_the_region_is_stored_and_shown(window):
    localization.set_region("Tacna", "Peru")
    window._refresh_region_combo()
    assert "Tacna" in window._region_combo.currentText()
    assert localization.get_region().city == "Tacna"


# ── The catalogue ───────────────────────────────────────────────────────────

def test_every_catalog_key_has_all_five_languages():
    codes = set(localization.language_codes())
    for key in localization.catalog_keys():
        entry = localization.CATALOG[key]
        assert set(entry) == codes, f"{key} is missing a language"


def test_the_non_english_languages_are_not_the_english_string():
    """A catalogue that says the same thing five times is not a translation."""
    # Two keys are genuinely the same word everywhere: the product's own
    # name, and "Ok", which is a loan word in Spanish, Portuguese and French
    # and the standard rendering in a Chinese interface.
    identical = {"app.name", "action.ok"}
    for key in localization.catalog_keys():
        if key in identical:
            continue
        english = localization.CATALOG[key]["en"]
        for code in ("es", "pt", "fr", "zh"):
            assert localization.CATALOG[key][code] != english, (
                f"{key} was not translated into {code}")


def test_a_person_who_speaks_spanish_gets_spanish(tmp_path, monkeypatch):
    """Nobody chose a language, and the memory says Spanish."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config_manager, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_manager, "CONFIG_FILE", config_dir / "api_keys.json")
    assert localization.get_language() == "es"


def test_an_explicit_choice_beats_the_remembered_language(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config_manager, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_manager, "CONFIG_FILE", config_dir / "api_keys.json")
    localization.set_language("fr")
    assert localization.get_language() == "fr"
