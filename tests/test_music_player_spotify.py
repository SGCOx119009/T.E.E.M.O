"""
tests/test_music_player_spotify.py — the song has to SOUND.

THE OWNER'S OWN WORDS
    "Pon la canción Coqueta de Grupo Frontera en Spotify." The tool found
    the song, opened Spotify, and the music never came out. The session
    died in the middle of the attempt ("A tool is still working (1)"),
    the model came back and called spotify_control with play_pause by
    hand, and the song still did not play.

WHAT IS BEING PROVED
    1. Finding a track is not playing it. The route has to ORDER the
       playback - PUT /me/player/play - and the order has to be there
       before anything is reported.
    2. The report has to carry the title and the artist of the track
       that was found, not the words of the request.
    3. "done" means it sounds. A track that is loaded and paused, or a
       client that never confirmed anything, is never "done".
    4. A client that was closed is opened, and the attempt is made ONCE
       more with its window there.
    5. The route has a CEILING. The old one tried the desktop search
       twelve times, and each try could take twelve seconds: five
       minutes of a live session that the session itself gives up on
       after 120 seconds.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from actions import music_player as mp  # noqa: E402

TRACK = {
    "name": "Coqueta",
    "uri": "spotify:track:4uLU6hMCjMI75M1A2tKUQC",
    "artists": [{"name": "Grupo Frontera"}],
}
URI = TRACK["uri"]


class FakeSpotify:
    """The Spotify plugin, with a driver that records what was asked.

    Every call the route makes is written down, in order, so a test can
    look at the DECISION and not at the screen.
    """

    def __init__(self, *, ordered=True, playing=True, device_error=None):
        self.calls: list[tuple] = []
        self.ordered = ordered
        self.playing = playing
        self.device_error = device_error
        self.desktop_results: list[str] = []
        self.silent_polls = 0

    # ── the account ──
    def _authorized_token(self):
        self.calls.append(("token",))
        return ({"access_token": "fake"}, None)

    def _search_tracks(self, query, artist="", deadline=None):
        self.calls.append(("search", query, artist))
        return ([dict(TRACK)], None)

    def _track_summary(self, track):
        return {"title": "COQUETA", "artist": "Fuerza Regida, Grupo Frontera"}

    def _play_uri(self, uri, title="", artist="", deadline=None):
        self.calls.append(("order", uri, title, artist))
        if self.device_error and len(
            [c for c in self.calls if c[0] == "order"]) == 1:
            return (False, self.device_error)
        return ((True, "") if self.ordered else (False, "Spotify said no"))

    def _api_request(self, method, path, **kwargs):
        self.calls.append(("api", method, path))
        if path.endswith("/currently-playing"):
            if self.silent_polls > 0:
                # The account has not caught up with the order yet.
                self.silent_polls -= 1
                return (204, None, None)
            return (200, {
                "item": {"uri": URI},
                "is_playing": self.playing,
            }, None)
        return (200, {}, None)

    # ── the desktop client ──
    def _desktop_play(self, query, artist="", deadline=None):
        self.calls.append(("desktop", query, artist, deadline))
        if self.desktop_results:
            return self.desktop_results.pop(0)
        return "status=failed action=play reason='Spotify is not on screen'"

    def calls_of(self, kind):
        return [c for c in self.calls if c[0] == kind]


@pytest.fixture
def fake(monkeypatch):
    driver = FakeSpotify()
    monkeypatch.setattr(mp, "_spotify_module", lambda: driver)
    monkeypatch.setattr(mp, "_spotify_installed", lambda: True)
    return driver


def _no_sleep(monkeypatch):
    """A sleep that records the time it was asked for and does not wait."""
    asked: list[float] = []

    def _sleep(seconds):
        asked.append(float(seconds))

    monkeypatch.setattr(mp.time, "sleep", _sleep)
    return asked


# ── 1. The order ───────────────────────────────────────────────────────────

def test_a_found_track_is_ordered_to_play(fake, monkeypatch):
    """The route does not stop at 'the track was found'.

    The order - the call that makes the music sound - has to be made,
    and it has to be made with the track that was chosen.
    """
    _no_sleep(monkeypatch)
    result = mp.music_player({
        "query": "Coqueta", "artist": "Grupo Frontera", "service": "spotify",
    })

    orders = fake.calls_of("order")
    assert len(orders) == 1, fake.calls
    assert orders[0][1] == URI
    assert "status=done" in result, result
    assert "verified=true" in result, result


def test_the_order_is_made_before_anything_is_reported(fake, monkeypatch):
    """The order comes first; the check comes after it."""
    _no_sleep(monkeypatch)
    mp.music_player({
        "query": "Coqueta", "artist": "Grupo Frontera", "service": "spotify",
    })
    kinds = [c[0] for c in fake.calls]
    assert kinds.index("order") < kinds.index("api"), kinds


def test_the_report_carries_the_title_and_the_artist_of_the_track(fake,
                                                                 monkeypatch):
    """The song that sounds is named, not the words of the request."""
    _no_sleep(monkeypatch)
    result = mp.music_player({
        "query": "Coqueta", "artist": "Grupo Frontera", "service": "spotify",
    })
    assert "status=done" in result, result
    assert "track=COQUETA" in result, result
    assert 'artist="Fuerza Regida, Grupo Frontera"' in result, result


# ── 2. "done" means it sounds ──────────────────────────────────────────────

def test_a_paused_track_is_never_reported_as_done(fake, monkeypatch):
    """The account has to say it is PLAYING.

    The old check only compared the URI, so a Coqueta that was loaded
    and paused was answered with "status=done" and the user heard
    nothing at all.
    """
    fake.playing = False
    monkeypatch.setattr(mp, "_VERIFY_TIMEOUT", 0.05)
    _no_sleep(monkeypatch)
    result = mp.music_player({
        "query": "Coqueta", "artist": "Grupo Frontera", "service": "spotify",
    })
    assert "status=done" not in result, result
    assert "status=unverified" in result, result


def test_the_account_that_has_not_caught_up_yet_is_waited_for(fake, monkeypatch):
    """Right after the order the account can answer with nothing.

    That is not a failure - it is the account that has not caught up
    with the order yet, and the song IS starting.
    """
    fake.silent_polls = 3
    _no_sleep(monkeypatch)
    result = mp.music_player({
        "query": "Coqueta", "artist": "Grupo Frontera", "service": "spotify",
    })
    assert "status=done" in result, result
    assert len(fake.calls_of("api")) == 4, fake.calls


def test_a_silent_client_is_never_reported_as_done(fake, monkeypatch):
    fake.desktop_results = [        "status=unverified action=play reason='the client did not confirm it'"
    ]
    monkeypatch.setattr(mp, "_spotify_api_play",
                        lambda _q, _a: ("", "no account"))
    _no_sleep(monkeypatch)
    result = mp.music_player({
        "query": "Coqueta", "artist": "Grupo Frontera", "service": "spotify",
    })
    assert "status=done" not in result, result
    assert "status=unverified" in result, result


# ── 3. The client that was closed ──────────────────────────────────────────

def test_a_closed_client_is_opened_and_the_order_is_made_once_more(
        fake, monkeypatch):
    """No active device means the client was shut: it is opened and the
    order is made again, ONCE."""
    fake.device_error = (
        "No active Spotify playback device. Open Spotify on this laptop."
    )
    opened: list[str] = []
    monkeypatch.setattr(mp.os, "startfile", lambda uri: opened.append(uri))
    monkeypatch.setattr(mp, "_spotify_client_running", lambda: True)
    monkeypatch.setattr(mp, "_wait_for_spotify_client", lambda _d: True)
    _no_sleep(monkeypatch)

    result = mp.music_player({
        "query": "Coqueta", "artist": "Grupo Frontera", "service": "spotify",
    })

    assert opened == ["spotify:"], opened
    assert len(fake.calls_of("order")) == 2, fake.calls
    assert "status=done" in result, result


def test_the_desktop_client_is_opened_and_tried_once_more(fake, monkeypatch):
    """The client was closed: it is opened, and the attempt is made again
    with its window there."""
    monkeypatch.setattr(mp, "_spotify_api_play",
                        lambda _q, _a: ("", "no account"))
    fake.desktop_results = [
        "status=failed action=play reason='Spotify is not on screen'",
        'status=done action=play track="Coqueta" artist="Grupo Frontera" '
        "verified=true",
    ]
    opened: list[str] = []
    monkeypatch.setattr(mp.os, "startfile", lambda uri: opened.append(uri))
    monkeypatch.setattr(mp, "_spotify_client_running", lambda: False)
    monkeypatch.setattr(mp, "_wait_for_spotify_client", lambda _d: True)
    _no_sleep(monkeypatch)

    result = mp.music_player({
        "query": "Coqueta", "artist": "Grupo Frontera", "service": "spotify",
    })

    assert opened == ["spotify:"], opened
    assert len(fake.calls_of("desktop")) == 2, fake.calls
    assert "status=done" in result, result
    assert "track=Coqueta" in result, result


# ── 4. The ceiling ─────────────────────────────────────────────────────────

def test_the_desktop_route_is_tried_twice_and_not_twelve_times(fake,
                                                               monkeypatch):
    """Twelve tries is what killed the session.

    Each try could take twelve seconds of its own, so the tool could
    hold the conversation for five minutes - and the live session gives
    a working tool 120 seconds before it rebuilds itself.
    """
    monkeypatch.setattr(mp, "_spotify_api_play",
                        lambda _q, _a: ("", "no account"))
    monkeypatch.setattr(mp, "_spotify_client_running", lambda: True)
    asked = _no_sleep(monkeypatch)

    mp.music_player({
        "query": "Coqueta", "artist": "Grupo Frontera", "service": "spotify",
    })

    assert len(fake.calls_of("desktop")) == 2, fake.calls
    assert sum(asked) <= mp._DESKTOP_ROUTE_BUDGET, asked


def test_the_whole_spotify_route_stays_inside_its_budget(fake, monkeypatch):
    """The two budgets are the whole route, and they add up to less than
    the 120 seconds the live session allows."""
    monkeypatch.setattr(mp, "_spotify_api_play",
                        lambda _q, _a: ("", "no account"))
    monkeypatch.setattr(mp, "_spotify_client_running", lambda: True)
    asked = _no_sleep(monkeypatch)

    mp.music_player({
        "query": "Coqueta", "artist": "Grupo Frontera", "service": "spotify",
    })

    assert sum(asked) <= (mp._API_ROUTE_BUDGET
                          + mp._DESKTOP_ROUTE_BUDGET), asked
    assert (mp._API_ROUTE_BUDGET + mp._DESKTOP_ROUTE_BUDGET) < 120.0


def test_the_route_really_finishes_quickly_when_it_cannot(fake, monkeypatch):
    """Measured, not asserted on paper: the whole call takes less than
    the ceiling even when the client never answers."""
    monkeypatch.setattr(mp, "_spotify_api_play",
                        lambda _q, _a: ("", "no account"))
    monkeypatch.setattr(mp, "_spotify_client_running", lambda: True)
    _no_sleep(monkeypatch)

    started = time.monotonic()
    result = mp.music_player({
        "query": "Coqueta", "artist": "Grupo Frontera", "service": "spotify",
    })
    elapsed = time.monotonic() - started

    assert elapsed < 5.0, elapsed
    assert "status=failed" in result, result


def test_the_desktop_attempts_are_given_the_budget_of_the_route(fake,
                                                                monkeypatch):
    """The client is told how much time is left, so the second attempt
    is a real one and not a formality."""
    monkeypatch.setattr(mp, "_spotify_api_play",
                        lambda _q, _a: ("", "no account"))
    monkeypatch.setattr(mp, "_spotify_client_running", lambda: True)
    _no_sleep(monkeypatch)

    mp.music_player({
        "query": "Coqueta", "artist": "Grupo Frontera", "service": "spotify",
    })

    deadlines = [c[3] for c in fake.calls_of("desktop")]
    assert deadlines, fake.calls
    for deadline in deadlines:
        assert deadline - time.monotonic() <= mp._DESKTOP_ROUTE_BUDGET


# ── 5. The account route orders it through the Web API ─────────────────────

def test_the_web_api_is_asked_to_play_the_track_it_found(fake, monkeypatch):
    """PUT /me/player/play is the call that makes the music sound."""
    _no_sleep(monkeypatch)
    mp.music_player({
        "query": "Coqueta", "artist": "Grupo Frontera", "service": "spotify",
    })
    searches = fake.calls_of("search")
    orders = fake.calls_of("order")
    assert searches and orders
    assert searches[0][1] == "Coqueta"
    assert orders[0][1] == URI


def test_the_title_the_user_asked_for_is_not_what_is_reported(fake,
                                                               monkeypatch):
    """'Coqueta' was asked for; 'COQUETA' by Fuerza Regida y Grupo
    Frontera is what sounds."""
    _no_sleep(monkeypatch)
    result = mp.music_player({
        "query": "Coqueta", "artist": "Grupo Frontera", "service": "spotify",
    })
    assert "track=COQUETA" in result, result
    assert "track=Coqueta" not in result, result
