"""Read-only Google Calendar integration using the official Calendar API.

WHY A PLUGIN AND NOT A BUILT-IN TOOL
    The calendar belongs to the user's Google account, and the account is
    theirs: it is authorized with a client of their own, exactly like
    the Gmail plugin, and the token lives in the plugin's own settings
    and nowhere else.

WHAT IT ANSWERS
    "What do I have today?" and "when do I have to leave to arrive on
    time?" — the second one is the reason this plugin exists. Every
    timed event carries the moment it starts and the place it happens
    at, and the time to be on the way is a setting of the user (how
    long it takes them to get there). So the answer is not only WHAT
    and WHEN, but WHEN TO LEAVE.

    The calendar is only ever read. There is no way, from here, to
    create, move or delete an event.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from core import outcome
from memory import config_manager

PLUGIN = {
    "name": "calendar_control",
    "description": (
        "Read the connected Google Calendar — EVERY calendar the account can "
        "read, not only the primary one. Use for 'what do I have today', "
        "'what is my agenda', 'when do I have to leave to arrive on time'. "
        "Each event comes with the calendar it belongs to. "
        "This integration never creates, moves or deletes an event. "
        "Configure a Google OAuth Desktop client in plugin settings and "
        "authorize it with the read-only button before asking."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["today", "days"],
                "description": (
                    "'today' for the rest of today, 'days' for the days that "
                    "come."
                ),
            },
            "days": {
                "type": "INTEGER",
                "description": "How many days to look at, from today (1-14).",
            },
            "place": {
                "type": "STRING",
                "description": (
                    "Where the user is now, to judge whether there is time "
                    "to get there. Optional."
                ),
            },
        },
        "required": ["action"],
    },
}

_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
_TOKEN_KEY = "oauth_credentials"
_MAX_DAYS = 14
_DEFAULT_TRAVEL_MIN = 30
_MAX_EVENTS = 40


def _plugin_config() -> dict:
    return config_manager.get_plugin_config(
        "calendar_control",
        secret_keys=["client_secret", _TOKEN_KEY],
    )


def _save_credentials(credentials_json: str) -> None:
    config_manager.save_plugin_config(
        "calendar_control",
        {_TOKEN_KEY: credentials_json},
        secret_keys=["client_secret", _TOKEN_KEY],
    )


def _travel_minutes() -> int:
    """How long the user says it takes to get where they are going."""
    raw = _plugin_config().get("travel_minutes", _DEFAULT_TRAVEL_MIN)
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return _DEFAULT_TRAVEL_MIN
    return max(0, min(value, 240))


def _new_authorization_flow(client_id: str, client_secret: str):
    from google_auth_oauthlib.flow import InstalledAppFlow

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": (
                "https://www.googleapis.com/oauth2/v1/certs"
            ),
            "redirect_uris": ["http://localhost"],
        }
    }
    return InstalledAppFlow.from_client_config(
        client_config,
        scopes=[_CALENDAR_SCOPE],
    )


def _authorize_from_settings(values: dict) -> tuple[bool, str]:
    client_id = values.get("client_id", "")
    client_secret = values.get("client_secret", "")
    if not isinstance(client_id, str) or not client_id.strip():
        return False, "Enter a Google OAuth Desktop Client ID first."
    if not isinstance(client_secret, str) or not client_secret.strip():
        return False, "Enter a Google OAuth Desktop Client Secret first."
    try:
        flow = _new_authorization_flow(client_id.strip(), client_secret.strip())
        credentials = flow.run_local_server(
            port=0,
            open_browser=True,
            access_type="offline",
            prompt="consent",
        )
        granted = set(getattr(credentials, "scopes", None) or [])
        if _CALENDAR_SCOPE not in granted:
            return False, "Google did not grant the calendar read-only scope."
        _save_credentials(credentials.to_json())
    except Exception as exc:
        return False, f"Calendar authorization failed ({type(exc).__name__})."
    return True, "The calendar was authorized with read-only access."


PLUGIN_SETTINGS = {
    "namespace": "calendar_control",
    "title": "GOOGLE CALENDAR — READ ONLY",
    "fields": [
        {
            "key": "client_id",
            "type": "text",
            "label": "Google OAuth Desktop Client ID",
            "placeholder": "Client ID from your Google Cloud OAuth app",
        },
        {
            "key": "client_secret",
            "type": "password",
            "label": "Google OAuth Desktop Client Secret",
            "placeholder": "Stored in the Windows credential vault",
        },
        {
            "key": "travel_minutes",
            "type": "text",
            "label": "Minutes it takes to get there",
            "placeholder": str(_DEFAULT_TRAVEL_MIN),
        },
    ],
    "action": {
        "label": "AUTHORIZE CALENDAR READ ONLY",
        "run": _authorize_from_settings,
    },
}


def _load_authorized_credentials():
    values = _plugin_config()
    token_json = values.get(_TOKEN_KEY)
    if not isinstance(token_json, str) or not token_json.strip():
        return None, "Authorize the calendar from the plugin settings first."
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        credentials = Credentials.from_authorized_user_info(
            json.loads(token_json),
            scopes=[_CALENDAR_SCOPE],
        )
        granted = set(getattr(credentials, "scopes", None) or [])
        if _CALENDAR_SCOPE not in granted:
            return None, "The saved Google authorization lacks calendar access."
        if not credentials.valid:
            if not credentials.expired or not credentials.refresh_token:
                return None, "The calendar authorization expired; reconnect it."
            credentials.refresh(Request())
            _save_credentials(credentials.to_json())
        return credentials, None
    except Exception as exc:
        return None, f"Could not load the calendar authorization ({type(exc).__name__})."


def _build_calendar_service(credentials):
    from googleapiclient.discovery import build

    return build(
        "calendar",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )


def _local_tz_name() -> str:
    """The IANA name of the local timezone, or "" when we cannot name it.

    Google wants an IANA name ("America/Lima"). `str()` of a fixed offset is
    not one ("UTC-05:00"), and sending a name the API does not know makes it
    fall back to UTC — which is how every event ended up five hours out. So
    this returns a name only when there really is one.
    """
    try:
        from core.cloud import user_tz

        name = str(user_tz())
        if "/" in name and " " not in name:
            return name
    except Exception:
        pass
    return ""


def _event_time(raw: Any) -> datetime | None:
    """The start or the end of an event, as a local datetime."""
    if not isinstance(raw, dict):
        return None
    stamp = raw.get("dateTime")
    if isinstance(stamp, str) and stamp.strip():
        try:
            return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            return None
    day = raw.get("date")
    if isinstance(day, str) and day.strip():
        try:
            return datetime.fromisoformat(day.strip())
        except ValueError:
            return None
    return None


def _readable_calendars(service) -> list[tuple[str, str, bool]]:
    """Every calendar this token can read: (id, name, is_the_primary_one).

    "primary" is a shorthand for the calendar the user writes in, so it comes
    first — but it is NOT the only one that matters.
    """
    out: list[tuple[str, str, bool]] = []
    try:
        page = service.calendarList().list(maxResults=250).execute()
    except Exception:
        return [("primary", "primary", True)]
    for item in (page.get("items") or []):
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id") or "").strip()
        if not cid:
            continue
        name = str(item.get("summaryOverride") or item.get("summary") or cid).strip()
        out.append((cid, name, bool(item.get("primary"))))
    if not out:
        return [("primary", "primary", True)]
    out.sort(key=lambda triple: 0 if triple[2] else 1)
    return out


def _sort_key(item: dict) -> tuple[bool, str]:
    stamp = _event_time(item.get("start"))
    return (stamp is None, stamp.isoformat() if stamp else "")


def _events_for(service, start: datetime, end: datetime) -> tuple[list[dict], list[str]]:
    """Every event in the window, from EVERY calendar the token can read.

    WHY EVERY CALENDAR AND NOT ONLY "primary"
        The user has six calendars, and the appointments live in the ones that
        are not the primary. Reading only "primary" answered "no events" on a
        window that held ten appointments: the tool was right about the
        calendar it looked at, and wrong about the day.

    It returns the events (each one tagged with the calendar it came from) and
    the list of calendars that could not be read, so the answer can say so
    instead of pretending it looked everywhere.
    """
    found: list[dict] = []
    unreadable: list[str] = []
    calendars = _readable_calendars(service)
    for cid, name, _is_primary in calendars:
        try:
            response = (
                service.events()
                .list(
                    calendarId=cid,
                    timeMin=start.isoformat(),
                    timeMax=end.isoformat(),
                    maxResults=_MAX_EVENTS,
                    singleEvents=True,
                    orderBy="startTime",
                    timeZone=_local_tz_name() or None,
                )
                .execute()
            )
        except Exception:
            unreadable.append(name)
            continue
        for item in (response.get("items") or []):
            if not isinstance(item, dict):
                continue
            tagged = dict(item)
            tagged["_calendar"] = name
            found.append(tagged)
    found.sort(key=_sort_key)
    return found[:_MAX_EVENTS], unreadable, len(calendars)


def _describe(item: dict, travel: int) -> dict | None:
    start = _event_time(item.get("start"))
    end = _event_time(item.get("end"))
    if start is None:
        return None
    summary = str(item.get("summary") or "").strip() or "(sin título)"
    place = str(item.get("location") or "").strip()
    leave_by = start - timedelta(minutes=travel) if travel else None
    return {
        "summary": summary[:160],
        "calendar": str(item.get("_calendar") or "")[:80],
        "start": start.isoformat(),
        "end": end.isoformat() if end else "",
        "location": place[:160],
        "all_day": not isinstance((item.get("start") or {}).get("dateTime"), str),
        "leave_by": leave_by.isoformat() if leave_by else "",
        "minutes_of_travel": travel,
    }


def read_events(days: int = 1) -> dict:
    """Everything the calendar knows about the next `days` days.

    One door for everybody who needs the agenda: the tool itself, the morning
    briefing, and whatever asks later. It returns a DICT and never raises, so
    a calendar that is not connected is an empty agenda and not a crash.

    Why a dict and not the tool's text answer: the briefing used to read the
    tool's answer as if it were JSON, and it never was — so the morning
    briefing said "your agenda is clear" every single morning, appointments
    or not. Two readers of the same calendar have to read the same thing.
    """
    empty = {
        "events": [],
        "now": "",
        "days": 0,
        "travel_minutes": 0,
        "calendars_read": 0,
        "calendars_with_events": 0,
        "unreadable_calendars": [],
        "error": "",
    }
    try:
        span = int(days)
    except (TypeError, ValueError):
        span = 1
    span = max(1, min(span, _MAX_DAYS))

    credentials, error = _load_authorized_credentials()
    if error:
        return {**empty, "days": span, "error": error}

    now = datetime.now().astimezone()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=span)
    travel = _travel_minutes()
    try:
        service = _build_calendar_service(credentials)
        raw, unreadable, calendars_read = _events_for(service, start, end)
    except Exception as exc:
        return {
            **empty,
            "days": span,
            "now": now.isoformat(),
            "travel_minutes": travel,
            "error": f"The calendar could not be read ({type(exc).__name__}).",
        }

    events = []
    for item in raw:
        described = _describe(item, travel)
        if described is not None:
            events.append(described)
    return {
        "events": events,
        "now": now.isoformat(),
        "days": span,
        "travel_minutes": travel,
        "calendars_read": calendars_read,
        "calendars_with_events": len({item.get("_calendar") for item in raw}),
        "unreadable_calendars": unreadable,
        "error": "",
    }


def _today_events(parameters: dict) -> str:
    action = str(parameters.get("action") or "today").strip().lower()
    days = parameters.get("days", 1)
    if isinstance(days, bool) or not isinstance(days, int):
        days = 1
    days = max(1, min(days, _MAX_DAYS))
    if action == "days":
        span = days
    elif action == "today":
        span = 1
    else:
        return outcome.failed(
            "calendar_control",
            "the action must be 'today' or 'days'",
            action=action,
        )

    read = read_events(span)
    if read.get("error"):
        return outcome.failed(
            "calendar_control",
            read["error"],
            action=action,
        )

    events = read["events"]
    return outcome.done(
        "calendar_control",
        action=action,
        days=read["days"],
        now=read["now"],
        count=len(events),
        travel_minutes=read["travel_minutes"],
        calendars_read=read["calendars_read"],
        calendars_with_events=read["calendars_with_events"],
        unreadable_calendars=", ".join(read["unreadable_calendars"]) or "none",
        events=(
            outcome.untrusted(
                "calendar",
                json.dumps(events, ensure_ascii=False, separators=(",", ":")),
            )
            if events else "none"
        ),
        verified=True,
    )


def run(parameters: dict, player=None, session_memory=None) -> str:
    del player, session_memory
    if not isinstance(parameters, dict):
        return outcome.failed("calendar_control", "parameters must be an object")
    return _today_events(parameters)
