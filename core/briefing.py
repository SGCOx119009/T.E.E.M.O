"""core/briefing.py — the morning briefing, built here and not by the model.

WHY THIS FILE EXISTS
    The briefing used to be one thing the model said: a prompt that said
    "greet the user warmly and mention the time". Two things were
    wrong with that.

    THE FIRST IS THAT IT SAID NOTHING WHEN THE MODEL COULD NOT ANSWER.
    A quota that is spent, a network that is down, a session that has
    not opened yet — and the user is left with a switch that is on and
    a morning with no greeting. The facts of a morning are not the
    model's to find: the time is the clock, the weather is a service
    that answers without asking anybody, and the agenda is a calendar
    the user already authorized. So they are gathered HERE, and the
    text is written HERE.

    THE SECOND IS THAT THE FACTS WERE NOT IN THE PROMPT. Even when the
    model did answer, it was asked to invent the temperature. It cannot:
    it does not know the city, and it will not call the weather service
    in the middle of a greeting. The facts are handed to it instead, as
    facts.

WHAT IT SAYS
    "Buenos días, don Sergio. Son las 7:20. En Tacna hacen 14 °C y está
    nublado; la máxima es de 18 °C. Hoy tienes clase a las 10:00 — para
    llegar a tiempo tienes que salir a las 9:30."
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

# ── the words ───────────────────────────────────────────────────────────────
#
# Five languages, in the order en, es, pt, fr, zh. The order is the
# same one the rest of the project uses.

_GREETING = {
    "en": ("Good morning", "Good afternoon", "Good evening", "Good night"),
    "es": ("Buenos días", "Buenas tardes", "Buenas noches", "Buenas noches"),
    "pt": ("Bom dia", "Boa tarde", "Boa noite", "Boa noite"),
    "fr": ("Bonjour", "Bon après-midi", "Bonsoir", "Bonsoir"),
    "zh": ("早上好", "下午好", "晚上好", "晚安"),
}

_TITLE = {
    "en": ("Mr.", "Ms.", "Mx.", "Dr.", ""),
    "es": ("don", "doña", "", "dr.", ""),
    "pt": ("Sr.", "Sra.", "", "Dr.", ""),
    "fr": ("M.", "Mme", "Mx.", "Dr.", ""),
    "zh": ("先生", "女士", "", "博士", ""),
}

# WHEN THE DAY STARTS AND WHEN IT ENDS, IN HOURS.
_MORNING = (5, 12)
_AFTERNOON = (12, 19)
_EVENING = (19, 23)

# ── the sentences ───────────────────────────────────────────────────────────

_SENTENCE = {
    "en": (
        "{greeting}, {name}. It is {time}.",
        "In {city} it is {temp} °C and {condition}; the high is {high} °C.",
        "Today you have {count} thing(s): {agenda}.",
        "You have to leave at {leave} to arrive on time.",
        "Your agenda today is clear.",
    ),
    "es": (
        "{greeting}, {name}. Son las {time}.",
        "En {city} hacen {temp} °C y {condition}; la máxima es de {high} °C.",
        "Hoy tienes {count} cosa(s): {agenda}.",
        "Tienes que salir a las {leave} para llegar a tiempo.",
        "Tu agenda de hoy está libre.",
    ),
    "pt": (
        "{greeting}, {name}. São {time}.",
        "Em {city} está {temp} °C e {condition}; a máxima é de {high} °C.",
        "Hoje você tem {count} coisa(s): {agenda}.",
        "Você precisa sair às {leave} para chegar a tempo.",
        "Sua agenda de hoje está livre.",
    ),
    "fr": (
        "{greeting}, {name}. Il est {time}.",
        "À {city}, il fait {temp} °C et {condition} ; le maximum est de {high} °C.",
        "Aujourd'hui, tu as {count} chose(s) : {agenda}.",
        "Tu dois partir à {leave} pour arriver à l'heure.",
        "Ton agenda du jour est libre.",
    ),
    "zh": (
        "{greeting}，{name}。现在是 {time}。",
        "{city} 现在 {temp} °C，{condition}；最高 {high} °C。",
        "你今天有 {count} 件事：{agenda}。",
        "你需要在 {leave} 出发才能准时到达。",
        "你今天的日程是空的。",
    ),
}

_RAIN = {
    "en": ("rain is likely this afternoon", "rain is likely today"),
    "es": ("se espera lluvia esta tarde", "se espera lluvia hoy"),
    "pt": ("chuva é provável esta tarde", "chuva é provável hoje"),
    "fr": ("de la pluie est probable cet après-midi", "de la pluie est probable aujourd'hui"),
    "zh": ("今天下午可能有雨", "今天可能有雨"),
}

_CONDITION = {
    "clear": ("clear", "despejado", "céu limpo", "dégagé", "晴朗"),
    "mainly clear": ("mainly clear", "mayormente despejado", "predominantemente limpo",
                     "plutôt dégagé", "大致晴朗"),
    "partly cloudy": ("partly cloudy", "parcialmente nublado", "parcialmente nublado",
                       "partiellement nuageux", "局部多云"),
    "overcast": ("overcast", "nublado", "nublado", "couvert", "阴"),
    "fog": ("fog", "niebla", "neblina", "brouillard", "雾"),
    "drizzle": ("drizzle", "llovizna", "garoa", "bruine", "毛毛雨"),
    "rain": ("rain", "lluvia", "chuva", "pluie", "雨"),
    "heavy rain": ("heavy rain", "lluvia intensa", "chuva forte", "pluie forte", "大雨"),
    "showers": ("showers", "chubascos", "pancadas", "averses", "阵雨"),
    "thunderstorm": ("thunderstorm", "tormenta", "trovoada", "orage", "雷阵雨"),
    "snow": ("snow", "nieve", "neve", "neige", "雪"),
}


def _language() -> str:
    """The language the interface is set to, or English."""
    try:
        from core.localization import get_language

        code = str(get_language() or "").strip().lower()
    except Exception:
        code = ""
    return code if code in _SENTENCE else "en"


def _index(lang: str) -> int:
    return {"en": 0, "es": 1, "pt": 2, "fr": 3, "zh": 4}.get(lang, 0)


def _greeting_index(hour: int) -> int:
    if _MORNING[0] <= hour < _MORNING[1]:
        return 0
    if _AFTERNOON[0] <= hour < _AFTERNOON[1]:
        return 1
    if _EVENING[0] <= hour < _EVENING[1]:
        return 2
    return 3


def _clock(now: datetime) -> str:
    """The time of the day, as a person writes it.

    "07:20" is a machine; "7:20" is a clock on a wall. Only the leading
    zero of the hour goes — the minutes keep theirs, because ":20" on
    its own is not a time.
    """
    hour, minute = now.strftime("%H:%M").split(":")
    if hour.startswith("0"):
        hour = hour[1:] or "0"
    return f"{hour}:{minute}"


def _user_name() -> str:
    """What the assistant calls the user.

    THE NAME IS ASKED FOR, AND IF IT IS NOT KNOWN, THE MACHINE KNOWS
    WHO IS SITTING IN FRONT OF IT. The Windows account is the person
    at the keyboard; a morning greeting with no name in it is worse
    than one that says "sergi" when the user has not been asked yet.
    """
    try:
        from memory import config_manager

        name = str(config_manager.get_user_name() or "").strip()
        if name:
            return name
    except Exception:
        pass
    try:
        from memory.memory_manager import load_memory

        identity = (load_memory() or {}).get("identity", {})
        entry = identity.get("name", "")
        value = entry.get("value", "") if isinstance(entry, dict) else str(entry)
        if str(value).strip():
            return str(value).strip()
    except Exception:
        pass
    try:
        import getpass

        account = str(getpass.getuser() or "").strip()
        account = account.split("\\")[-1].split("/")[-1].strip()
        if account and not account.upper().startswith("USER"):
            return account
    except Exception:
        pass
    return ""


def _title_for(name: str, lang: str) -> str:
    """The honorific, when the name carries one."""
    if not name:
        return ""
    first = name.split()[0].strip().lower()
    table = {
        "es": (("sr", "sra", "srta", "don", "doña", "dr", "dra"),),
        "en": (("mr", "mrs", "ms", "dr", "prof"),),
        "pt": (("sr", "sra", "dr", "dra"),),
        "fr": (("m", "mr", "mme", "dr", "prof"),),
    }.get(lang, ())
    if not table:
        return ""
    for word in table[0]:
        if first == word or first.startswith(word + "."):
            titles = _TITLE[lang]
            return titles[0] if word in ("sr", "m", "dr", "prof") else titles[1]
    return ""


def _region() -> tuple[str, str]:
    """The city and the country the user is in."""
    try:
        from core.localization import get_region

        region = get_region()
        city = str(getattr(region, "city", "") or "").strip()
        country = str(getattr(region, "country", "") or "").strip()
        if city:
            return city, country
    except Exception:
        pass
    try:
        from memory.config_manager import get_region as _stored

        raw = _stored() or {}
        return (str(raw.get("city") or "").strip(),
                str(raw.get("country") or "").strip())
    except Exception:
        return "", ""


def _weather(city: str, lang: str) -> dict:
    """The weather of the city, as facts. Empty when it cannot be had."""
    if not city:
        return {}
    try:
        from actions.weather_report import facts_for
    except Exception:
        return {}
    try:
        return facts_for(city, lang) or {}
    except Exception:
        return {}


def _agenda(today: datetime, lang: str) -> list[dict]:
    """What the user has today, from the calendar they authorized.

    It reads the calendar through the plugin's own door (`read_events`), which
    returns a dict. It used to read the plugin's TEXT answer as if it were
    JSON — and it never was — so the briefing said "your agenda is clear"
    every morning, with or without appointments.
    """
    try:
        from plugins import calendar_control
    except Exception:
        return []
    try:
        read = calendar_control.read_events(1)
    except Exception:
        return []
    if not isinstance(read, dict):
        return []
    events = read.get("events")
    if not isinstance(events, list):
        return []
    return [item for item in events if isinstance(item, dict)]


def _agenda_text(events: list[dict], lang: str) -> str:
    """The agenda as one line of words."""
    if not events:
        return ""
    parts = []
    for item in events:
        summary = str(item.get("summary") or "").strip()
        start = str(item.get("start") or "")
        when = ""
        if start:
            try:
                when = datetime.fromisoformat(start).strftime("%H:%M")
            except ValueError:
                when = ""
        if when:
            parts.append(f"{when} {summary}")
        else:
            parts.append(summary)
    return ", ".join(parts)


def _leave_text(event: dict, lang: str) -> str:
    """The moment to leave, as words."""
    leave = str(event.get("leave_by") or "")
    if not leave:
        return ""
    try:
        return datetime.fromisoformat(leave).strftime("%H:%M")
    except ValueError:
        return ""


def build(now: datetime | None = None, lang: str | None = None) -> dict:
    """The briefing: the text, and the facts behind it.

    Returns a dict with `text` (what the user reads and hears), `facts`
    (what the model is told, as plain data) and the pieces, so that a
    caller can use what it needs.
    """
    now = now or datetime.now()
    lang = lang or _language()
    idx = _index(lang)
    name = _user_name()
    title = _title_for(name, lang)
    city, country = _region()

    facts: dict = {
        "time": _clock(now),
        "date": now.strftime("%Y-%m-%d"),
        "name": name,
        "city": city,
        "country": country,
    }

    weather = _weather(city, lang)
    if weather:
        facts["weather"] = weather

    events = _agenda(now, lang)
    if events:
        facts["agenda"] = [
            {
                "summary": str(item.get("summary") or ""),
                "start": str(item.get("start") or ""),
                "location": str(item.get("location") or ""),
                "leave_by": str(item.get("leave_by") or ""),
            }
            for item in events
        ]

    # ── the text ──────────────────────────────────────────────────────────
    greeting = _GREETING[lang][_greeting_index(now.hour)]
    lines: list[str] = []
    called = f"{title} {name}".strip() if title else name
    lines.append(_SENTENCE[lang][0].format(
        greeting=greeting, name=called, time=facts["time"]))
    if weather:
        condition_key = str(weather.get("condition") or "")
        condition = _CONDITION.get(condition_key, ("",))[idx] if condition_key else ""
        if not condition:
            condition = condition_key
        try:
            temp = f"{float(weather.get('temperature_c')):.0f}"
            high = f"{float(weather.get('high_c')):.0f}"
        except (TypeError, ValueError):
            temp = str(weather.get("temperature_c", "-"))
            high = str(weather.get("high_c", "-"))
        lines.append(_SENTENCE[lang][1].format(
            city=city,
            temp=temp,
            condition=condition,
            high=high,
        ))
        rain = weather.get("rain_probability_pct")
        if rain is not None and float(rain) >= 40:
            lines.append(_RAIN[lang][0 if now.hour < 17 else 1].capitalize() + ".")
    if events:
        lines.append(_SENTENCE[lang][2].format(
            count=len(events), agenda=_agenda_text(events, lang)))
        leave = _leave_text(events[0], lang)
        if leave:
            lines.append(_SENTENCE[lang][3].format(leave=leave))
    else:
        lines.append(_SENTENCE[lang][4])

    text = "\n".join(line for line in lines if line.strip())
    return {"text": text, "facts": facts, "events": events}


def facts_for_prompt(briefing: dict) -> str:
    """The facts as one block of text for the model's prompt."""
    facts = briefing.get("facts") or {}
    lines = []
    if facts.get("time"):
        lines.append(f"time: {facts['time']}")
    if facts.get("name"):
        lines.append(f"the user's name: {facts['name']}")
    weather = facts.get("weather") or {}
    if weather:
        city = str(weather.get("city") or facts.get("city") or "")
        where = f"weather now in {city}: " if city else "weather now: "
        lines.append(
            where
            + f"{weather.get('temperature_c')} °C, {weather.get('condition')}, "
            f"high {weather.get('high_c')} °C, low {weather.get('low_c')} °C"
        )
    agenda = facts.get("agenda") or []
    if agenda:
        for item in agenda:
            lines.append(
                f"agenda: {item.get('start', '')[11:16]} {item.get('summary')}"
                + (f" at {item['location']}" if item.get("location") else "")
                + (f" (leave by {item.get('leave_by', '')[11:16]})"
                   if item.get("leave_by") else "")
            )
    return "\n".join(lines)
