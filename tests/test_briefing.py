"""tests/test_briefing.py — the morning briefing, measured.

WHAT IS TESTED
    The greeting follows the hour, the name is used when there is one,
    the weather comes from the facts and not from the model, the agenda
    says when to leave, and the whole thing is written in the language
    of the user.

    Nothing here touches the network: the weather and the calendar are
    replaced by fixed readings, because a test that depends on the
    weather in Tacna is a test that fails on a cloudy Tuesday.
"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import briefing  # noqa: E402


class _FakeWeather:
    """The weather, without the weather."""

    def __init__(self, facts):
        self._facts = facts

    def facts_for(self, city, lang=""):
        return dict(self._facts)


class _FakeCalendar:
    """A calendar with two events on it."""

    def __init__(self, events):
        self._events = events

    def run(self, parameters, player=None, session_memory=None):
        return json.dumps({"events": self._events, "count": len(self._events)})


class GreetingTests(unittest.TestCase):
    """The greeting follows the hour of the day."""

    def test_the_greeting_follows_the_hour(self):
        self.assertEqual(briefing._greeting_index(7), 0)     # morning
        self.assertEqual(briefing._greeting_index(15), 1)    # afternoon
        self.assertEqual(briefing._greeting_index(21), 2)    # evening
        self.assertEqual(briefing._greeting_index(2), 3)     # night

    def test_the_clock_is_written_as_a_person_writes_it(self):
        self.assertEqual(briefing._clock(datetime(2026, 11, 22, 7, 20)), "7:20")
        self.assertEqual(briefing._clock(datetime(2026, 11, 22, 15, 5)), "15:05")
        self.assertEqual(briefing._clock(datetime(2026, 11, 22, 0, 30)), "0:30")


class BriefingTextTests(unittest.TestCase):
    """The text of the morning, with the facts it is made of."""

    def _build(self, hour=8, weather=None, events=None, name="Sergi",
               lang="es"):
        weather = weather if weather is not None else {}
        events = events or []
        original_weather = briefing._weather
        original_agenda = briefing._agenda
        original_name = briefing._user_name
        original_language = briefing._language
        try:
            briefing._weather = lambda city, lang_code: dict(weather)
            briefing._agenda = lambda now, lang_code: list(events)
            briefing._user_name = lambda: name
            briefing._language = lambda: lang
            return briefing.build(now=datetime(2026, 11, 22, hour, 0), lang=lang)
        finally:
            briefing._weather = original_weather
            briefing._agenda = original_agenda
            briefing._user_name = original_name
            briefing._language = original_language

    def test_the_morning_says_good_morning_with_the_name_and_the_hour(self):
        result = self._build(hour=8)
        first = result["text"].splitlines()[0]
        self.assertIn("Buenos días", first)
        self.assertIn("Sergi", first)
        self.assertIn("8:00", first)

    def test_the_evening_does_not_say_good_morning(self):
        result = self._build(hour=21)
        self.assertIn("Buenas noches", result["text"].splitlines()[0])

    def test_the_weather_is_in_the_text_when_there_is_one(self):
        result = self._build(weather={
            "city": "Tacna", "temperature_c": 14.2, "condition": "overcast",
            "high_c": 18.0, "low_c": 9.0, "rain_probability_pct": 20,
        })
        self.assertIn("Tacna", result["text"])
        self.assertIn("14 °C", result["text"])
        self.assertIn("nublado", result["text"])

    def test_the_rain_is_warned_about_only_when_it_is_likely(self):
        dry = self._build(weather={
            "city": "Tacna", "temperature_c": 14.0, "condition": "clear",
            "high_c": 18.0, "low_c": 9.0, "rain_probability_pct": 10,
        })
        wet = self._build(weather={
            "city": "Tacna", "temperature_c": 14.0, "condition": "rain",
            "high_c": 18.0, "low_c": 9.0, "rain_probability_pct": 80,
        })
        self.assertNotIn("lluvia", dry["text"])
        self.assertIn("lluvia", wet["text"])

    def test_the_agenda_says_when_to_leave(self):
        result = self._build(events=[{
            "summary": "Clase dealgebra",
            "start": "2026-11-22T10:00:00",
            "end": "2026-11-22T11:00:00",
            "location": "Universidad",
            "leave_by": "2026-11-22T09:30:00",
            "all_day": False,
        }])
        self.assertIn("Clase", result["text"])
        self.assertIn("10:00", result["text"])
        self.assertIn("9:30", result["text"])
        self.assertIn("salir", result["text"])

    def test_an_empty_agenda_says_so(self):
        result = self._build(events=[])
        self.assertIn("agenda", result["text"].lower())

    def test_the_text_is_in_the_language_of_the_user(self):
        for lang, greeting in (("en", "Good morning"), ("es", "Buenos días"),
                                ("pt", "Bom dia"), ("fr", "Bonjour"),
                                ("zh", "早上好")):
            result = self._build(lang=lang)
            self.assertIn(greeting, result["text"],
                          f"the briefing does not greet in {lang}")

    def test_the_facts_for_the_model_carry_the_numbers(self):
        result = self._build(weather={
            "city": "Tacna", "temperature_c": 14.2, "condition": "overcast",
            "high_c": 18.0, "low_c": 9.0,
        })
        block = briefing.facts_for_prompt(result)
        self.assertIn("14.2", block)
        self.assertIn("Tacna", block)


class CalendarPluginTests(unittest.TestCase):
    """The calendar plugin, without a calendar."""

    def test_it_asks_for_the_read_only_authorization(self):
        from plugins import calendar_control

        self.assertIn("calendar.readonly", calendar_control._CALENDAR_SCOPE)

    def test_it_refuses_an_unknown_action(self):
        from plugins import calendar_control

        result = calendar_control.run({"action": "delete"})
        self.assertIn("status=failed", str(result))

    def test_it_asks_for_authorization_before_reading(self):
        from plugins import calendar_control

        original = calendar_control._load_authorized_credentials
        try:
            calendar_control._load_authorized_credentials = lambda: (
                None, "not authorized")
            result = calendar_control.run({"action": "today"})
            self.assertIn("status=failed", str(result))
        finally:
            calendar_control._load_authorized_credentials = original

    def test_the_events_carry_the_moment_to_leave(self):
        from plugins import calendar_control

        described = calendar_control._describe(
            {
                "summary": "Clase",
                "start": {"dateTime": "2026-11-22T10:00:00"},
                "end": {"dateTime": "2026-11-22T11:00:00"},
                "location": "Universidad",
            },
            30,
        )
        self.assertIsNotNone(described)
        self.assertTrue(described["leave_by"].endswith("09:30:00"))
        self.assertEqual(described["location"], "Universidad")

    def test_a_day_that_is_not_a_date_time_is_a_whole_day(self):
        from plugins import calendar_control

        described = calendar_control._describe(
            {
                "summary": "Holiday",
                "start": {"date": "2026-11-22"},
                "end": {"date": "2026-11-23"},
            },
            30,
        )
        self.assertIsNotNone(described)
        self.assertTrue(described["all_day"])


class WeatherFactsTests(unittest.TestCase):
    """The weather as facts, which is what the briefing needs."""

    def test_it_never_raises_when_the_service_is_not_there(self):
        from actions import weather_report

        original = weather_report._http_json
        try:
            weather_report._http_json = lambda url, params: (
                None, "the service is not there")
            self.assertEqual(weather_report.facts_for("Tacna", "es"), {})
        finally:
            weather_report._http_json = original

    def test_it_reads_the_numbers_of_the_service(self):
        from actions import weather_report

        def fake(url, params):
            if "geocoding" in url:
                return {"results": [{"latitude": -18.0, "longitude": -70.2,
                                     "country": "Peru"}]}, None
            return {
                "current": {"temperature_2m": 14.2, "weather_code": 3},
                "daily": {
                    "temperature_2m_max": [18.0],
                    "temperature_2m_min": [9.0],
                    "weather_code": [61],
                    "precipitation_probability_max": [75],
                },
            }, None

        original = weather_report._http_json
        try:
            weather_report._http_json = fake
            facts = weather_report.facts_for("Tacna", "es")
        finally:
            weather_report._http_json = original
        self.assertEqual(facts["temperature_c"], 14.2)
        self.assertEqual(facts["condition"], "overcast")
        self.assertEqual(facts["high_c"], 18.0)
        self.assertEqual(facts["rain_probability_pct"], 75)


if __name__ == "__main__":
    unittest.main()
