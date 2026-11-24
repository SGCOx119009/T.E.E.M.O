"""El calendario tiene que leer TODOS los calendarios, en la hora del usuario.

QUE PRUEBA ESTE FICHERO
    Tres fallos reales que se encontraron al conectar el calendario de verdad:

    1. LA HORA. `core.cloud.user_tz()` devolvia UTC siempre, porque el offset
       que lee de la configuracion (`user_tz_offset_min`) solo lo escribe el
       telefono y en el escritorio no hay nadie que lo pongа. El resultado:
       cada evento volvia cinco horas desplazado y el "sal antes de las"
       salia a una hora que no era la del usuario.

    2. UN SOLO CALENDARIO. La herramienta leia `calendarId="primary"` y nada
       mas. El usuario tiene seis calendarios y las citas estan en los que no
       son el principal: la herramienta respondia "no hay nada" un dia con
       diez citas.

    3. SILENCIO SOBRE LO QUE NO SE PUDO LEER. Un calendario que falla al
       leerse se salta en silencio. Ahora se dice cual fue.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from plugins import calendar_control as cal


class FakeEvents:
    """The `service.events()` part of the Google client."""

    def __init__(self, by_calendar, fail_for=()):
        self.by_calendar = by_calendar
        self.fail_for = set(fail_for)
        self.asked: list[str] = []

    def list(self, **kwargs):
        cid = kwargs.get("calendarId")
        self.asked.append(cid)

        class _Call:
            def __init__(self, items=None, boom=False):
                self._items = items or []
                self._boom = boom

            def execute(self):
                if self._boom:
                    raise RuntimeError("403 forbidden")
                return {"items": self._items}

        if cid in self.fail_for:
            return _Call(boom=True)
        return _Call(self.by_calendar.get(cid, []))


class FakeCalendarList:
    def __init__(self, items):
        self._items = items

    def list(self, **kwargs):
        class _Call:
            def __init__(self, items):
                self._items = items

            def execute(self):
                return {"items": self._items}

        return _Call(self._items)


class FakeService:
    def __init__(self, calendars, events, fail_for=()):
        self._calendars = calendars
        self.events_obj = FakeEvents(events, fail_for)

    def events(self):
        return self.events_obj

    def calendarList(self):
        return FakeCalendarList(self._calendars)


def _event(summary, when, calendar=None):
    item = {
        "summary": summary,
        "start": {"dateTime": when.isoformat()},
        "end": {"dateTime": (when + timedelta(hours=1)).isoformat()},
    }
    if calendar:
        item["_calendar"] = calendar
    return item


class CalendarTimezoneTests(unittest.TestCase):
    def test_the_local_timezone_is_the_machines_own_when_nothing_is_configured(self) -> None:
        from core import cloud

        with patch("memory.config_manager.load_api_keys", return_value={}):
            tz = cloud.user_tz()
        self.assertIsNotNone(tz)
        offset = datetime.now().astimezone().utcoffset()
        self.assertEqual(
            tz.utcoffset(datetime.now()),
            offset,
            "sin offset configurado, la zona del usuario es la de la maquina",
        )

    def test_a_configured_offset_still_wins(self) -> None:
        from core import cloud

        with patch("memory.config_manager.load_api_keys",
                   return_value={"user_tz_offset_min": -300}):
            tz = cloud.user_tz()
        self.assertEqual(tz.utcoffset(datetime.now()), timedelta(minutes=-300))


class CalendarReadsEveryCalendarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 11, 24, 9, 0, tzinfo=timezone(timedelta(hours=-5)))
        self.calendars = [
            {"id": "primario@x.com", "summary": "Primario", "primary": True},
            {"id": "citas@x.com", "summary": "Citas Dental"},
            {"id": "familia@x.com", "summary": "Familia"},
        ]
        self.events = {
            "primario@x.com": [_event("algo mio", self.now)],
            "citas@x.com": [_event("Consulta ortodoncia", self.now + timedelta(hours=1))],
            "familia@x.com": [],
        }
        self.service = FakeService(self.calendars, self.events)

    def _run(self):
        with patch.object(cal, "_build_calendar_service", return_value=self.service), \
             patch.object(cal, "_load_authorized_credentials", return_value=(object(), "")), \
             patch.object(cal, "datetime") as fake_dt:
            fake_dt.now.return_value = self.now
            fake_dt.fromisoformat = datetime.fromisoformat
            fake_dt.timedelta = timedelta
            fake_dt.timezone = timezone
            return cal.run({"action": "days", "days": 7}, None, None)

    def test_it_asks_every_calendar_and_not_only_the_primary_one(self) -> None:
        self._run()
        asked = set(self.service.events_obj.asked)
        self.assertIn("citas@x.com", asked,
                      "las citas estan en el calendario que no es el principal")
        self.assertIn("familia@x.com", asked)
        self.assertIn("primario@x.com", asked)

    def test_the_events_of_every_calendar_are_in_the_answer(self) -> None:
        out = self._run()
        self.assertIn("Consulta ortodoncia", out)
        self.assertIn("Citas Dental", out,
                      "cada evento dice de que calendario viene")
        self.assertIn("algo mio", out)

    def test_it_says_how_many_calendars_it_read(self) -> None:
        out = self._run()
        self.assertIn("calendars_read=3", out)

    def test_a_calendar_that_cannot_be_read_is_named(self) -> None:
        self.service = FakeService(self.calendars, self.events, fail_for={"familia@x.com"})
        out = self._run()
        self.assertIn("unreadable_calendars=Familia", out,
                      "un calendario que no se pudo leer se dice, no se calla")
        self.assertIn("Consulta ortodoncia", out)


class CalendarTimezoneNameTests(unittest.TestCase):
    def test_a_name_the_api_does_not_understand_is_not_sent(self) -> None:
        """`str()` of a fixed offset is not an IANA name; sending it makes
        Google fall back to UTC and shift every event."""
        with patch("core.cloud.user_tz", return_value=timezone(timedelta(hours=-5))):
            self.assertEqual(cal._local_tz_name(), "")

    def test_a_real_iana_name_is_sent(self) -> None:
        with patch("core.cloud.user_tz", return_value="America/Lima"):
            self.assertEqual(cal._local_tz_name(), "America/Lima")


class BriefingReadsTheAgendaTests(unittest.TestCase):
    """The morning briefing used to read the tool's TEXT answer as JSON.

    It never was JSON, so the parse always failed and the briefing said
    "your agenda is clear" every single morning — with appointments or
    without. The two readers of the calendar have to read the same thing.
    """

    def test_the_briefing_gets_the_events_from_the_calendar(self) -> None:
        from core import briefing

        when = datetime(2026, 11, 24, 10, 0, tzinfo=timezone(timedelta(hours=-5)))
        read = {
            "events": [{
                "summary": "Consulta ortodoncia",
                "calendar": "Citas Dental",
                "start": when.isoformat(),
                "end": (when + timedelta(hours=1)).isoformat(),
                "location": "",
                "all_day": False,
                "leave_by": when.isoformat(),
                "minutes_of_travel": 30,
            }],
            "now": when.isoformat(),
            "days": 1,
            "travel_minutes": 30,
            "calendars_read": 3,
            "calendars_with_events": 1,
            "unreadable_calendars": [],
            "error": "",
        }
        with patch("plugins.calendar_control.read_events", return_value=read):
            events = briefing._agenda(when, "es")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["summary"], "Consulta ortodoncia")

    def test_the_briefing_says_nothing_when_the_calendar_is_not_connected(self) -> None:
        from core import briefing

        read = {"events": [], "error": "Authorize the calendar from the plugin settings first."}
        with patch("plugins.calendar_control.read_events", return_value=read):
            events = briefing._agenda(datetime.now(), "es")
        self.assertEqual(events, [])


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
