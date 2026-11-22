"""
tests/test_session_truth_fixes.py — the defects found in a real session.

WHAT THESE FIX
    A session on this machine produced a transcript in which the assistant:
      * failed to load the wake model on the FIRST launch and only worked
        from the second one (a thread race inside openwakeword's own import),
      * summarised a document three times, told the user each time that it
        could not confirm the summary had worked, and ran out of tool budget
        with the summary never read out loud,
      * reported the weather in English inside a Spanish conversation, in a
        browser it opened without checking which browser that was,
      * said it had no sleep timeout while the application log said it had
        been asleep for two minutes,
      * and denied that a file had opened two seconds after the application
        had confirmed, on screen, that it had.

    Each test below pins one of those down.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from core import localization, outcome, wake_word  # noqa: E402
from actions import file_processor as file_processor_module  # noqa: E402
from actions import weather_report  # noqa: E402
from actions import web_search as web_search_module  # noqa: E402
from memory import config_manager  # noqa: E402


@pytest.fixture()
def isolated_config(tmp_path, monkeypatch):
    """A config of our own: the user's config/api_keys.json is never touched."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config_manager, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_manager, "CONFIG_FILE", config_dir / "api_keys.json")
    return config_dir


# ── A1: the wake model has to load on the FIRST launch ─────────────────────

def test_the_wake_package_loads_completely():
    module = wake_word._load_openwakeword()
    assert module is not None
    assert hasattr(module, "get_pretrained_model_paths"), (
        "the package was imported half-way and the attribute the model needs "
        "is not on it")


def test_the_wake_package_loads_when_many_threads_ask_at_once():
    """THE RACE THAT BROKE THE FIRST LAUNCH.

    Python serialises imports per module, but NOT across threads: a second
    thread that asks for a submodule while the first thread is still running
    the package's __init__ gets the half-built module out of sys.modules
    instead of waiting. The app starts the wake word on a background thread
    while the main thread touches the same package for the voice model, and
    the first launch of the day died with "partially initialized module
    'openwakeword' has no attribute 'get_pretrained_model_paths'".
    """
    errors = []
    loaded = []
    lock = threading.Lock()

    def _load():
        try:
            module = wake_word._load_openwakeword()
            if module is None or not hasattr(module, "get_pretrained_model_paths"):
                raise AssertionError("half-imported module")
            with lock:
                loaded.append(module)
        except Exception as exc:          # pragma: no cover - the failure itself
            with lock:
                errors.append(repr(exc))

    threads = [threading.Thread(target=_load) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert not errors, errors
    assert len(loaded) == 12


def test_a_half_imported_module_in_sys_modules_is_repaired(monkeypatch):
    """The other half of the same failure: a module already in sys.modules
    without the attribute the caller needs. The loader has to notice and
    import it again from scratch."""
    import types
    broken = types.ModuleType("openwakeword")
    monkeypatch.setitem(sys.modules, "openwakeword", broken)
    module = wake_word._load_openwakeword()
    assert module is not None
    assert hasattr(module, "get_pretrained_model_paths")


def test_the_voice_detector_still_finds_its_model():
    """The voice-activity model ships inside openwakeword, so the loader
    change must not have broken the path to it."""
    path = wake_word.VoiceActivityDetector._model_path()
    assert path is not None and path.is_file()


# ── A3: a tool that PRODUCED something says so ──────────────────────────────

def test_a_summarised_document_comes_back_verified(tmp_path):
    source = tmp_path / "notas.txt"
    source.write_text("una frase de prueba\n", encoding="utf-8")
    result = file_processor_module.file_processor({
        "file_path": str(source),
        "action": "word_count",
    })
    assert result.startswith("status=done ")
    assert "verified=true" in result
    assert outcome.normalize_dispatch_result(
        "file_processor", result).startswith("status=done")


def test_a_missing_file_is_a_failure_not_a_soft_answer(tmp_path):
    result = file_processor_module.file_processor({
        "file_path": str(tmp_path / "nope.txt"),
        "action": "info",
    })
    assert result.startswith("status=failed ")


# ── A7: the weather is real, and it is in the language of the installation ─

def _fake_weather(city="Tacna", country="Peru"):
    return (
        -18.0146,
        -70.2536,
        country,
        None,
    )


def _fake_forecast(code=3, temperature=19.9):
    return (
        {
            "current": {
                "temperature_2m": temperature,
                "relative_humidity_2m": 86,
                "apparent_temperature": 21.2,
                "weather_code": code,
                "wind_speed_10m": 10.0,
            },
            "daily": {
                "temperature_2m_max": [26.9, 23.3],
                "temperature_2m_min": [18.0, 18.5],
            },
        },
        None,
    )


def test_the_weather_is_numbers_and_not_a_browser(monkeypatch):
    """It used to open a Google search in the default browser and say
    "Showing the weather for Tacna, today, sir." — in English, inside a
    Spanish conversation, and with no weather in it."""
    monkeypatch.setattr(weather_report, "_geocode", lambda city, lang: _fake_weather())
    monkeypatch.setattr(weather_report, "_forecast", lambda lat, lon: _fake_forecast())
    result = weather_report.weather_action({"city": "Tacna"})
    assert result.startswith("status=done ")
    assert "verified=true" in result
    assert "temperature_c=19.9" in result
    assert "condition=overcast" in result
    assert "Tacna" in result


def test_the_weather_is_spoken_in_the_chosen_language(monkeypatch, isolated_config):
    monkeypatch.setattr(weather_report, "_geocode", lambda city, lang: _fake_weather())
    monkeypatch.setattr(weather_report, "_forecast", lambda lat, lon: _fake_forecast())
    localization.set_language("es")
    result = weather_report.weather_action({"city": "Tacna"})
    assert "°C" in result
    assert "sensación" in result, "the report has to be in Spanish"
    assert "nublado" in result
    localization.set_language("en")


def test_the_weather_uses_the_configured_region(monkeypatch, isolated_config):
    monkeypatch.setattr(weather_report, "_forecast", lambda lat, lon: _fake_forecast())
    localization.set_region("Tacna", "Peru")
    result = weather_report.weather_action({})
    assert result.startswith("status=done ")
    assert "city=Tacna" in result


def test_the_weather_asks_when_no_region_is_configured(monkeypatch, isolated_config):
    result = weather_report.weather_action({})
    assert result.startswith("status=ask ")


# ── The news is the news of the place the user lives in ────────────────────

def test_a_news_query_carries_the_region(isolated_config):
    localization.set_region("Tacna", "Peru")
    query = web_search_module._with_region("the latest headlines")
    assert "Tacna" in query
    assert "Peru" in query


def test_a_news_query_that_names_a_place_is_left_alone(isolated_config):
    localization.set_region("Tacna", "Peru")
    query = web_search_module._with_region("news in Madrid")
    assert query == "news in Madrid"


def test_the_morning_briefing_asks_for_the_country_first(isolated_config):
    import main
    localization.set_region("Tacna", "Peru")
    topic = main._news_topic_for_region()
    assert "Peru" in topic
    assert "Tacna" in topic


# ── A9: the assistant knows its own body ───────────────────────────────────

def test_the_prompt_carries_the_language_and_the_region():
    prompt = (BASE / "core" / "prompt.txt").read_text(encoding="utf-8")
    assert "{reply_language}" in prompt
    assert "{region}" in prompt
    assert "[YOUR OWN STATE]" in prompt


def test_the_assistant_has_a_tool_for_its_own_state():
    import main
    names = {d.get("name") for d in main.TOOL_DECLARATIONS}
    assert "assistant_state" in names


def test_the_assistant_state_tool_is_not_rejected_by_the_policy():
    from core import tool_audit
    assert tool_audit.classify_risk("assistant_state", {}) != "unclassified"


# ── A11: a folder plus a file name finds the file ─────────────────────────

def _fake_tree(tmp_path, monkeypatch):
    """A OneDrive-shaped tree: home/Escritorio/Nueva carpeta/actas verificadas."""
    from core import fileopen
    home = tmp_path / "home"
    deep = home / "OneDrive" / "Escritorio" / "Nueva carpeta" / "actas verificadas"
    deep.mkdir(parents=True)
    (deep / "ACTA MD.docx").write_text("acta", encoding="utf-8")
    (home / "OneDrive" / "Escritorio" / "wa.xlsx").write_text("wa", encoding="utf-8")
    monkeypatch.setattr(fileopen, "search_roots", lambda: [str(home)])
    return home


def test_a_file_is_found_by_its_name_alone(tmp_path, monkeypatch):
    from core import fileopen
    _fake_tree(tmp_path, monkeypatch)
    found = fileopen.resolve("wa.xlsx")
    assert found is not None and found.endswith("wa.xlsx")


def test_a_folder_plus_a_file_name_finds_the_file(tmp_path, monkeypatch):
    """'OneDrive, escritorio, nueva carpeta, actas verificadas' + the file.

    This is what the user actually said, and the separators a person uses
    are commas: the words describe a walk down a tree, not a string to
    match against the disk.
    """
    from core import fileopen
    _fake_tree(tmp_path, monkeypatch)
    found = fileopen.resolve("OneDrive, Escritorio, Nueva carpeta, "
                             "actas verificadas, ACTA MD.docx")
    assert found is not None and found.endswith("ACTA MD.docx")


def test_the_folder_names_are_matched_without_case_or_accents(tmp_path, monkeypatch):
    from core import fileopen
    _fake_tree(tmp_path, monkeypatch)
    found = fileopen.resolve("onedrive, escritorio, nueva carpeta, "
                             "actas verificadas, ACTA MD.docx")
    assert found is not None and found.endswith("ACTA MD.docx")


def test_a_folder_that_is_not_there_does_not_invent_a_file(tmp_path, monkeypatch):
    from core import fileopen
    _fake_tree(tmp_path, monkeypatch)
    assert fileopen.resolve("carpeta que no existe, ACTA MD.docx") is None
