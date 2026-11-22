"""
FASE DE LOCALIZACION - el idioma del producto y el lugar donde vive.

LO QUE COMPRUEBAN ESTAS PRUEBAS
    El producto habla cinco idiomas, tiene un idioma guardado y tiene una
    region guardada (ciudad y pais). Las cinco traducciones estan escritas
    a mano, asi que lo que puede romperse es que una clave se quede sin
    traducir, que una traduccion sea la inglesa con otro nombre, o que un
    marcador ("{city}") exista en ingles y desaparezca en frances.

    Y lo que NO debe romperse nunca: la config real del usuario. Todas
    las pruebas que escriben usan una config temporal; la de
    config/api_keys.json no se toca ni una vez.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from core import localization  # noqa: E402
from memory import config_manager  # noqa: E402


_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

# Las unicas claves que pueden mantener el texto ingles en otro idioma:
#   * "app.name" es el nombre propio del producto: se escribe igual en
#     cualquier idioma.
#   * "action.ok" es un prestamo corto que el espanol, el portugues y el
#     frances escriben exactamente igual.
_ANYAS_IGUALES = {"app.name", "action.ok"}

# Las claves que el resto del producto va a pedir. Si una falta, quien la
# pida recibe la propia clave en pantalla.
_CLAVES_OBLIGATORIAS = (
    "app.name", "app.tagline", "app.ready", "app.listening", "app.stopped",
    "app.error", "app.ready_short",
    "settings.title", "settings.language", "settings.language_help",
    "settings.region", "settings.region_city", "settings.region_country",
    "settings.region_help", "settings.save", "settings.saved",
    "settings.cancel", "settings.wake_phrase", "settings.wake_mode",
    "settings.voice", "settings.scale", "settings.providers",
    "action.ok", "action.cancel", "action.close", "action.save", "action.retry",
    "action.copy", "action.paste", "action.open", "action.refresh",
    "action.send", "action.delete", "action.confirm", "action.back",
    "action.next",
    "hud.thinking", "hud.listening", "hud.speaking", "hud.idle",
    "hud.processing", "hud.error",
    "chat.you", "chat.assistant", "chat.empty", "chat.placeholder",
    "chat.copy", "chat.copied",
    "tool.done", "tool.unverified", "tool.failed", "tool.ask", "tool.started",
    "tool.done_help", "tool.unverified_help", "tool.failed_help",
    "tool.ask_help", "tool.started_help",
    "weather.report", "weather.for_city", "weather.today", "weather.tomorrow",
    "weather.now", "weather.temperature", "weather.feels_like",
    "weather.humidity", "weather.wind", "weather.precipitation",
    "news.headlines", "news.national", "news.regional", "news.international",
    "news.source", "news.summary",
    "region.set", "region.not_set", "region.current",
)


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Una config propia: la del usuario no se toca.

    Asi es como lo hacen las otras pruebas del repositorio (ver
    tests/test_plugin_credentials.py): se cambian CONFIG_DIR y CONFIG_FILE
    durante la prueba y pytest los devuelve solo al terminar.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config_manager, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_manager, "CONFIG_FILE",
                        config_dir / "api_keys.json")
    return config_dir / "api_keys.json"


# ── Los cinco idiomas ───────────────────────────────────────────────────────

def test_the_five_languages_are_there():
    assert [lang.code for lang in localization.LANGUAGES] == [
        "en", "es", "pt", "fr", "zh",
    ]
    assert localization.language_codes() == ["en", "es", "pt", "fr", "zh"]
    assert len(set(localization.language_codes())) == 5


def test_every_language_has_a_name_in_english_and_in_its_own():
    for lang in localization.LANGUAGES:
        assert lang.name_en.strip(), lang.code
        assert lang.name_native.strip(), lang.code
        # El ingles es el unico idioma cuyo nombre nativo coincide con el
        # ingles: "English" se escribe English en ingles.
        if lang.code == "en":
            assert lang.name_native == lang.name_en
        else:
            assert lang.name_native != lang.name_en, lang.code


# ── El catalogo ──────────────────────────────────────────────────────────────

def test_every_obligatory_key_is_in_the_catalog():
    missing = [key for key in _CLAVES_OBLIGATORIAS
               if key not in localization.CATALOG]
    assert not missing, f"faltan claves: {missing}"


def test_every_key_has_the_five_languages():
    codes = set(localization.language_codes())
    for key, entry in localization.CATALOG.items():
        assert set(entry) == codes, key
        for code, text in entry.items():
            assert str(text).strip(), f"{key}/{code} esta vacio"


def test_the_other_languages_are_not_the_english_ones():
    """Una traduccion que es el ingles con otro nombre no es traduccion.

    Solo se libran las dos claves de _ANYAS_IGUALES, y estan ahi porque son
    un nombre propio y un prestamo corto, no por descuido.
    """
    for key, entry in localization.CATALOG.items():
        english = entry["en"]
        for code, text in entry.items():
            if code == "en" or key in _ANYAS_IGUALES:
                continue
            assert text != english, f"{key}/{code} sigue en ingles"


def test_the_placeholders_are_the_same_in_every_language():
    """Un marcador que existe en ingles y no en frances deja una frase
    vacia, y una frase vacia no se nota hasta que esta en pantalla."""
    with_placeholders = 0
    for key, entry in localization.CATALOG.items():
        reference = set(_PLACEHOLDER.findall(entry["en"]))
        if reference:
            with_placeholders += 1
        for code, text in entry.items():
            assert set(_PLACEHOLDER.findall(text)) == reference, f"{key}/{code}"
    assert with_placeholders > 0, "ninguna clave lleva marcadores"


def test_no_translation_is_missing():
    for code in localization.language_codes():
        assert localization.missing_translations(code) == [], code
    # Un idioma que no existe no puede tener traducciones.
    assert len(localization.missing_translations("klingon")) == len(
        localization.CATALOG)


# ── Traducir ─────────────────────────────────────────────────────────────────

def test_t_falls_back_to_english_and_to_the_key():
    english = localization.CATALOG["app.ready"]["en"]
    assert localization.t("app.ready", "zz") == english
    assert localization.t("no.such.key") == "no.such.key"
    assert localization.t("no.such.key", "es") == "no.such.key"
    assert localization.t("") == ""


def test_t_substitutes_the_values_and_never_raises():
    assert localization.t("weather.for_city", "en", city="Tacna") == (
        "Weather for Tacna")
    # Un valor de mas no molesta: no hay marcador para el.
    assert localization.t("weather.for_city", "en", city="Tacna", extra="x") == (
        "Weather for Tacna")
    # Un marcador que nadie relleno se queda visible en vez de romperlo todo.
    text = localization.t("weather.for_city", "en")
    assert "{city}" in text


def test_translate_returns_the_translation_or_the_original():
    assert localization.translate("app.ready", "es") == (
        localization.CATALOG["app.ready"]["es"])
    assert localization.translate("una frase cualquiera", "es") == (
        "una frase cualquiera")


# ── El idioma guardado ───────────────────────────────────────────────────────

def test_the_language_is_stored_and_read_back(isolated_config):
    assert localization.set_language("es") is True
    assert localization.get_language() == "es"
    assert config_manager.get_language() == "es"


def test_an_invalid_language_changes_nothing(isolated_config):
    assert localization.set_language("es") is True
    assert localization.set_language("klingon") is False
    assert localization.get_language() == "es"
    assert localization.set_language("") is False
    assert localization.get_language() == "es"


def test_an_unknown_stored_language_falls_back_to_the_default(isolated_config,
                                                                monkeypatch):
    """A language code nobody has never heard of must never be used.

    When nothing has been chosen AND the memory says nothing either, the
    answer is the default. When the memory DOES know, the remembered
    language is the answer — the person is the one who decides.
    """
    monkeypatch.setattr(localization, "_remembered_language", lambda: "")
    config_manager.save_language("klingon")
    assert localization.get_language() == localization.DEFAULT_LANGUAGE
    assert localization.DEFAULT_LANGUAGE == "en"


def test_the_remembered_language_is_used_when_nothing_was_chosen(isolated_config,
                                                                 monkeypatch):
    monkeypatch.setattr(localization, "_remembered_language", lambda: "es")
    assert localization.get_language() == "es"
    # A choice beats a memory.
    localization.set_language("fr")
    assert localization.get_language() == "fr"


def test_choosing_the_language_the_person_already_speaks_is_still_a_choice(
        isolated_config, monkeypatch):
    """Choosing the remembered language must be stored, and must report
    that something changed: it is the difference between "the assistant
    noticed" and "the user asked for this"."""
    monkeypatch.setattr(localization, "_remembered_language", lambda: "es")
    assert localization.set_language("es") is True
    assert localization._stored_language() == "es"
    assert localization.set_language("es") is False


# ── La region ───────────────────────────────────────────────────────────────

def test_the_region_table_has_the_cities_and_the_coordinates():
    expected = {
        "Tacna": ("Peru", "PE", -18.0146, -70.2536),
        "Lima": ("Peru", "PE", -12.0464, -77.0428),
        "La Libertad": ("Peru", "PE", -8.1089, -78.9594),
        "Arequipa": ("Peru", "PE", -16.4090, -71.5375),
        "La Paz": ("Bolivia", "BO", -16.4897, -68.1193),
        "Bogota": ("Colombia", "CO", 4.7110, -74.0721),
        "Medellin": ("Colombia", "CO", 6.2442, -75.5812),
        "Arica": ("Chile", "CL", -18.4783, -70.3126),
        "Santiago": ("Chile", "CL", -33.4489, -70.6693),
        "Paris": ("France", "FR", 48.8566, 2.3522),
        "Madrid": ("Spain", "ES", 40.4168, -3.7038),
        "London": ("United Kingdom", "GB", 51.5074, -0.1278),
        "Mexico City": ("Mexico", "MX", 19.4326, -99.1332),
        "Buenos Aires": ("Argentina", "AR", -34.6037, -58.3816),
        "Sao Paulo": ("Brazil", "BR", -23.5505, -46.6333),
        "Lisbon": ("Portugal", "PT", 38.7223, -9.1393),
        "Berlin": ("Germany", "DE", 52.5200, 13.4050),
        "New York": ("United States", "US", 40.7128, -74.0060),
        "Tokyo": ("Japan", "JP", 35.6762, 139.6503),
    }
    by_city = {region.city: region for region in localization.regions()}
    for city, (country, code, lat, lon) in expected.items():
        assert city in by_city, city
        region = by_city[city]
        assert region.country == country, city
        assert region.country_code == code, city
        assert region.latitude == pytest.approx(lat), city
        assert region.longitude == pytest.approx(lon), city


def test_the_region_is_the_default_one_when_nothing_is_configured(isolated_config):
    assert localization.get_region() == localization.DEFAULT_REGION


def test_the_region_round_trips(isolated_config):
    region = localization.set_region("Tacna", "Peru")
    assert region.city == "Tacna"
    assert region.country == "Peru"
    assert region.country_code == "PE"
    assert localization.get_region() == region
    assert config_manager.get_region() == {"city": "Tacna", "country": "Peru"}
    assert localization.region_label() == "Tacna, Peru"


def test_a_city_outside_the_table_is_accepted(isolated_config):
    region = localization.set_region("Cotonou")
    assert region.city == "Cotonou"
    assert region.country_code == ""
    assert region.latitude == 0.0
    assert region.longitude == 0.0
    assert localization.get_region().city == "Cotonou"


@pytest.mark.parametrize("country", ["peru", "PERÚ", "PE", "pe", "Peru"])
def test_the_country_matching_is_tolerant(isolated_config, country):
    region = localization.set_region("Tacna", country)
    assert region.country == "Peru"
    assert region.country_code == "PE"


# ── Lo que se le dice al modelo ──────────────────────────────────────────────

def test_the_prompt_lines_name_the_language_and_the_region(isolated_config):
    localization.set_language("es")
    localization.set_region("Tacna", "Peru")

    region_line = localization.region_prompt_line()
    assert "Español" in region_line
    assert "Tacna, Peru" in region_line

    language_line = localization.language_prompt_line()
    assert "Español" in language_line
    assert "Tacna, Peru" in language_line
