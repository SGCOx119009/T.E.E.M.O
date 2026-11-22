"""
weather_report.py — the weather, as numbers, for the place the user is in.

WHAT THIS USED TO BE, AND WHY IT WAS WRONG
    It opened a Google search for "weather in Tacna today" in the default
    browser and answered "Showing the weather for Tacna, today, sir." Two
    things were wrong with that, and the user hit both of them.

    First: it said nothing about the weather. The browser was left to show it.
    The assistant told the user it had shown the weather, in English, inside a
    conversation in Spanish, and then — because the reply carried no
    verification status — the dispatcher marked it "unverified, NOT confirmed",
    and the assistant told the user it could not confirm the weather report
    had worked. Three sentences to say nothing, in the wrong language.

    Second: it opened a BROWSER. Which browser is the system default is a
    question about this machine, not a thing to assert: the user had changed
    the default months earlier and was told, in plain words, that the
    assistant was using Opera GX "because it is the system's default browser".
    That was a claim nobody checked.

    So: no browser, no claim about the default, and the numbers themselves.

THE DATA
    Open-Meteo. It needs no key, no account and no signup, it answers in
    JSON, and it works for any coordinate on earth — which is what makes the
    region's city usable without a table of weather stations.

THE LANGUAGE
    The report is written in the language the interface is set to, not in the
    language the code is written in. The conditions come from a table that
    carries all five supported languages, so "Tacna" reads "despejado" in
    Spanish, "céu limpo" in Portuguese, "dégagé" in French and "晴朗" in Chinese.

THE REGION
    "What is the weather like?" with no city means the place the user lives.
    That place is the region configured in the settings, and when none has
    been configured the tool says so instead of guessing a city.
"""
from __future__ import annotations

from core import outcome

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_TIMEOUT = 8.0

# WMO weather interpretation codes, as Open-Meteo reports them.
# The order inside each tuple is: en, es, pt, fr, zh.
_CONDITIONS = {
    0:  ("clear", "despejado", "céu limpo", "dégagé", "晴朗"),
    1:  ("mainly clear", "mayormente despejado", "predominantemente limpo",
         "plutôt dégagé", "大致晴朗"),
    2:  ("partly cloudy", "parcialmente nublado", "parcialmente nublado",
         "partiellement nuageux", "局部多云"),
    3:  ("overcast", "nublado", "nublado", "couvert", "阴"),
    45: ("fog", "niebla", "neblina", "brouillard", "雾"),
    48: ("rime fog", "niebla helada", "neblina gelada",
         "brouillard givrant", "雾凇"),
    51: ("light drizzle", "llovizna ligera", "garoa fraca",
         "bruine légère", "小毛毛雨"),
    53: ("drizzle", "llovizna", "garoa", "bruine", "毛毛雨"),
    55: ("heavy drizzle", "llovizna intensa", "garoa intensa",
         "bruine forte", "浓毛毛雨"),
    56: ("freezing drizzle", "llovizna helada", "garoa congelante",
         "bruine verglaçante", "冻毛毛雨"),
    57: ("dense freezing drizzle", "llovizna helada intensa",
         "garoa congelante intensa", "brouillard verglaçant dense", "强冻毛毛雨"),
    61: ("light rain", "lluvia ligera", "chuva fraca", "pluie faible", "小雨"),
    63: ("rain", "lluvia", "chuva", "pluie", "雨"),
    65: ("heavy rain", "lluvia intensa", "chuva forte", "pluie forte", "大雨"),
    66: ("freezing rain", "lluvia helada", "chuva congelante",
         "pluie verglaçante", "冻雨"),
    67: ("heavy freezing rain", "lluvia helada intensa",
         "chuva congelante forte", "pluie verglaçante forte", "强冻雨"),
    71: ("light snow", "nieve ligera", "neve fraca", "neige légère", "小雪"),
    73: ("snow", "nieve", "neve", "neige", "雪"),
    75: ("heavy snow", "nieve intensa", "neve forte", "neige forte", "大雪"),
    77: ("snow grains", "cinarra", "grão de neve", "grains de neige", "米雪"),
    80: ("light showers", "chubascos ligeros", "pancadas leves",
         "averses légères", "阵雨"),
    81: ("showers", "chubascos", "pancadas", "averses", "强阵雨"),
    82: ("violent showers", "chubascos violentos", "pancadas fortes",
         "averses violentes", "暴雨"),
    85: ("snow showers", "chubascos de nieve", "pancadas de neve",
         "averses de neige", "阵雪"),
    86: ("heavy snow showers", "chubascos de nieve intensos",
         "pancadas de neve fortes", "averses de neige fortes", "强阵雪"),
    95: ("thunderstorm", "tormenta", "trovoada", "orage", "雷阵雨"),
    96: ("thunderstorm with hail", "tormenta con granizo",
         "trovoada com granizo", "orage avec grêle", "雷阵雨伴冰雹"),
    99: ("severe thunderstorm with hail", "tormenta fuerte con granizo",
         "trovoada forte com granizo", "orage violent avec grêle", "强雷暴伴冰雹"),
}
_UNKNOWN_CONDITION = ("unknown", "desconocido", "desconhecido", "inconnu", "未知")

# Sentence templates, one per supported language.
_TEMPLATES = {
    "en": "{city}, {country}: {temp} °C (feels like {feels} °C), {condition}, "
          "humidity {humidity}%, wind {wind} km/h. Today: high {high} °C, low {low} °C.",
    "es": "{city}, {country}: {temp} °C (sensación {feels} °C), {condition}, "
          "humedad {humidity} %, viento {wind} km/h. Hoy: máxima {high} °C, mínima {low} °C.",
    "pt": "{city}, {country}: {temp} °C (sensação de {feels} °C), {condition}, "
          "umidade {humidity}%, vento {wind} km/h. Hoje: máxima {high} °C, mínima {low} °C.",
    "fr": "{city}, {country} : {temp} °C (ressenti {feels} °C), {condition}, "
          "humidité {humidity} %, vent {wind} km/h. Aujourd'hui : maximum {high} °C, minimum {low} °C.",
    "zh": "{city}，{country}：{temp} °C（体感 {feels} °C），{condition}，"
          "湿度 {humidity}%，风速 {wind} 公里/小时。今天：最高 {high} °C，最低 {low} °C。",
}
_CONDITION_INDEX = {"en": 0, "es": 1, "pt": 2, "fr": 3, "zh": 4}


def _language() -> str:
    """The language the interface is set to, or English."""
    try:
        from core.localization import get_language
        code = str(get_language() or "").strip().lower()
    except Exception:
        code = ""
    return code if code in _TEMPLATES else "en"


def _region():
    """The configured region, or None when there is none."""
    try:
        from core.localization import get_region, DEFAULT_REGION
        region = get_region()
    except Exception:
        return None
    try:
        if region is None or region.city == DEFAULT_REGION.city:
            return None
    except Exception:
        pass
    return region


def _condition(code, lang: str) -> str:
    entry = _CONDITIONS.get(code)
    if entry is None:
        return _UNKNOWN_CONDITION[_CONDITION_INDEX.get(lang, 0)]
    return entry[_CONDITION_INDEX.get(lang, 0)]


def _condition_en(code) -> str:
    entry = _CONDITIONS.get(code)
    return entry[0] if entry else "unknown"


def _http_json(url: str, params: dict):
    """Fetch JSON with a deadline. Returns (payload, error_text)."""
    try:
        import requests
    except Exception as e:                       # pragma: no cover - requests ships
        return None, f"the http client is not available: {e}"
    try:
        response = requests.get(url, params=params, timeout=_TIMEOUT,
                                headers={"User-Agent": "T.E.E.M.O/1.0"})
    except Exception as e:
        return None, f"the weather service could not be reached: {e}"
    if response.status_code != 200:
        return None, f"the weather service answered with {response.status_code}"
    try:
        return response.json(), None
    except Exception as e:
        return None, f"the weather service sent something unreadable: {e}"


def _geocode(city: str, lang: str):
    """(latitude, longitude, country, error) for a city name."""
    payload, error = _http_json(
        _GEOCODE_URL,
        {"name": city, "count": 1, "language": lang, "format": "json"},
    )
    if error:
        return None, None, "", error
    results = (payload or {}).get("results") or []
    if not results:
        return None, None, "", f"no place called '{city}' was found"
    first = results[0]
    try:
        latitude = float(first.get("latitude"))
        longitude = float(first.get("longitude"))
    except Exception:
        return None, None, "", f"'{city}' could not be located on the map"
    return latitude, longitude, str(first.get("country") or ""), None


def _forecast(latitude: float, longitude: float):
    """The current conditions and today's high/low, or (None, error)."""
    payload, error = _http_json(
        _FORECAST_URL,
        {
            "latitude": latitude,
            "longitude": longitude,
            "current": ("temperature_2m,relative_humidity_2m,apparent_temperature,"
                        "weather_code,wind_speed_10m"),
            "daily": "temperature_2m_max,temperature_2m_min",
            "timezone": "auto",
            "forecast_days": 2,
        },
    )
    if error:
        return None, error
    current = (payload or {}).get("current") or {}
    daily = (payload or {}).get("daily") or {}
    if not current:
        return None, "the weather service sent no current conditions"
    return {"current": current, "daily": daily}, None


def weather_action(parameters: dict, player=None, session_memory=None) -> str:
    lang = _language()
    city = str(parameters.get("city") or "").strip()

    latitude = longitude = None
    country = ""
    if city:
        latitude, longitude, country, error = _geocode(city, lang)
        if error:
            msg = outcome.failed("weather_report", error, city=city)
            _log(msg, player)
            return msg
    else:
        region = _region()
        if region is None:
            msg = outcome.ask(
                "weather_report",
                "which city? no region is configured on this machine",
            )
            _log(msg, player)
            return msg
        city = region.city
        country = region.country
        latitude, longitude = region.latitude, region.longitude
        if not latitude:
            latitude, longitude, country, error = _geocode(city, lang)
            if error:
                msg = outcome.failed("weather_report", error, city=city)
                _log(msg, player)
                return msg

    data, error = _forecast(latitude, longitude)
    if error:
        msg = outcome.failed("weather_report", error, city=city)
        _log(msg, player)
        return msg

    current = data["current"]
    daily = data.get("daily") or {}

    def _pick(value, default=None):
        """A number, whether the service sent it as a scalar or as a list.

        The current conditions come back as plain values and the daily
        forecast as one value per day, so the same reader has to take both.
        """
        if isinstance(value, (list, tuple)):
            value = value[0] if value else None
        if value is None:
            return default
        try:
            return float(value)
        except Exception:
            return default

    temp = _pick(current.get("temperature_2m"))
    feels = _pick(current.get("apparent_temperature"), default=temp)
    humidity = _pick(current.get("relative_humidity_2m"))
    wind = _pick(current.get("wind_speed_10m"))
    code = current.get("weather_code")
    try:
        code = int(code)
    except Exception:
        code = -1
    high = _pick(daily.get("temperature_2m_max"))
    low = _pick(daily.get("temperature_2m_min"))

    if temp is None:
        msg = outcome.failed("weather_report",
                             "the weather service sent no temperature",
                             city=city)
        _log(msg, player)
        return msg

    sentence = _TEMPLATES[lang].format(
        city=city,
        country=country or "-",
        temp=f"{temp:.1f}",
        feels=f"{feels:.1f}" if feels is not None else f"{temp:.1f}",
        condition=_condition(code, lang),
        humidity=f"{humidity:.0f}" if humidity is not None else "-",
        wind=f"{wind:.0f}" if wind is not None else "-",
        high=f"{high:.1f}" if high is not None else "-",
        low=f"{low:.1f}" if low is not None else "-",
    )

    facts = {
        "city": city,
        "country": country,
        "temperature_c": round(temp, 1),
        "feels_like_c": round(feels, 1) if feels is not None else round(temp, 1),
        "condition": _condition_en(code),
        "verified": True,
    }
    if humidity is not None:
        facts["humidity_pct"] = round(humidity)
    if wind is not None:
        facts["wind_kmh"] = round(wind)
    if high is not None:
        facts["high_c"] = round(high, 1)
    if low is not None:
        facts["low_c"] = round(low, 1)

    result = outcome.done("weather_report", **facts) + "\n" + sentence
    _log(result, player)

    if session_memory:
        try:
            session_memory.set_last_search(query=f"weather {city}", response=sentence)
        except Exception:
            pass
    return result


def _log(message: str, player=None) -> None:
    print(f"[Weather] {message}")
    if player:
        try:
            player.write_log(f"T.E.E.M.O.: {message}")
        except Exception:
            pass


# ── Tool declaration (auto-discovered by core/action_loader.py) ───────────────
TOOL = {
    "name": "weather_report",
    "description": (
        "Gives the current weather and today's high and low for a city, as "
        "numbers: temperature, how it feels, humidity, wind and the sky "
        "condition. Leave the city out to use the region configured on this "
        "machine. The report comes back already written in the language the "
        "interface is set to — use it as it is, do not translate it again."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "city": {
                "type": "STRING",
                "description": "City name. Leave it out for the configured region.",
            },
            "time": {
                "type": "STRING",
                "description": "Which day: today or tomorrow. Defaults to today.",
            },
        },
        "required": [],
    },
    "handler": weather_action,
}
