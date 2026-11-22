"""
tests/test_music_player.py — A8, the way the user asked for it.

WHAT IS BEING PROVED
    1. The song is never played INSIDE the assistant. The two routes are
       Spotify (opened by its own URI) and YouTube in the default browser,
       and the music starts by itself on both.
    2. The request the user made is understood in the language they made it
       in: the verb that introduced the song is dropped, the title is kept.
    3. Nothing is claimed that was not checked. A browser that opened the
       video but whose player could not be read is reported as unverified,
       not as a song that is playing.
    4. Spotify is the preferred route and YouTube the fallback, and the
       fallback only fires when Spotify did nothing at all — a Spotify that
       already opened a track is never covered by another player.
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from actions import music_player as mp  # noqa: E402


@contextmanager
def _own_config(tmp_path, **settings):
    """A configuration of our own.

    The rule of the house: a test never touches the configuration of the
    machine it runs on. The choice of the player is a preference of the
    user, and a test that changed it would leave the user's own choice
    altered after the run.
    """
    from memory import config_manager

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    previous_dir = config_manager.CONFIG_DIR
    previous_file = config_manager.CONFIG_FILE
    config_manager.CONFIG_DIR = config_dir
    config_manager.CONFIG_FILE = config_dir / "api_keys.json"
    try:
        for key, value in settings.items():
            if key == "music_service":
                config_manager.save_music_service(value)
            else:
                config_manager._patch_config(**{key: value})
        yield config_manager
    finally:
        config_manager.CONFIG_DIR = previous_dir
        config_manager.CONFIG_FILE = previous_file


# ── The request in the language the user made it in ─────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("pon amor y motivo de grupo 5", "amor y motivo de grupo 5"),
    ("Pon Coqueta", "Coqueta"),
    ("play coqueta", "coqueta"),
    ("reproduce Coqueta", "Coqueta"),
    ("escucha Coqueta", "Coqueta"),
    ("play the Coqueta", "Coqueta"),
    ("mets Coqueta", "Coqueta"),
    ("Song for Someone", "Song for Someone"),
])
def test_the_leading_verb_is_dropped_and_the_title_is_kept(raw, expected):
    assert mp._clean_query(raw) == expected


def test_a_title_that_starts_with_a_noun_survives():
    """'Song for Someone' is a title, not a request to play a genre."""
    assert mp._clean_query("Song for Someone") == "Song for Someone"


def test_a_request_that_names_no_song_is_a_question_not_a_guess():
    result = mp.music_player({"query": "música"})
    assert "status=ask" in result


def test_no_song_at_all_is_a_question_not_a_guess():
    result = mp.music_player({})
    assert "status=ask" in result


def test_a_request_that_is_only_a_verb_is_still_a_question():
    assert "status=ask" in mp.music_player({"query": "pon"})


# ── Which player ───────────────────────────────────────────────────────────

def test_the_requested_route_wins_over_the_automatic_choice(monkeypatch):
    monkeypatch.setattr(mp, "_spotify_installed", lambda: False)
    assert mp._preferred_service("spotify") == "spotify"
    assert mp._preferred_service("youtube") == "youtube"


def test_the_saved_preference_wins_over_the_automatic_choice(monkeypatch, tmp_path):
    with _own_config(tmp_path) as config_manager:
        config_manager.save_music_service("youtube")
        assert mp._preferred_service("") == "youtube"
        config_manager.save_music_service("spotify")
        assert mp._preferred_service("") == "spotify"


def test_the_automatic_choice_follows_what_is_installed(monkeypatch):
    monkeypatch.setattr(mp, "_spotify_installed", lambda: True)
    assert mp._preferred_service("auto") == "spotify"
    monkeypatch.setattr(mp, "_spotify_installed", lambda: False)
    assert mp._preferred_service("auto") == "youtube"


# ── The YouTube route ───────────────────────────────────────────────────────

def _stub_youtube(monkeypatch, state="playing", resolved=("vid123", "Coqueta", "Canal")):
    monkeypatch.setattr(mp, "_youtube_resolve", lambda _q, _a="": resolved)
    monkeypatch.setattr(mp, "_open_in_default_browser", lambda _url: True)
    monkeypatch.setattr(mp, "_youtube_ensure_playing",
                        lambda _title, timeout=mp._VERIFY_TIMEOUT: (state, ""))


def test_youtube_opens_the_video_in_the_default_browser_and_plays(monkeypatch):
    _stub_youtube(monkeypatch)
    result = mp.music_player({"query": "coqueta", "service": "youtube"})
    assert "status=done" in result
    assert "service=youtube" in result
    assert "vid123" in result


def test_youtube_reports_what_it_could_not_confirm(monkeypatch):
    """A browser that opened the video and a player that could not be read
    is not a song that is playing."""
    _stub_youtube(monkeypatch, state="unknown")
    result = mp.music_player({"query": "coqueta", "service": "youtube"})
    assert "status=unverified" in result
    assert "status=done" not in result


def test_youtube_without_a_resolution_opens_the_search_and_says_so(monkeypatch):
    monkeypatch.setattr(mp, "_youtube_resolve", lambda _q, _a="": ("", "", ""))
    monkeypatch.setattr(mp, "_open_in_default_browser", lambda _url: True)
    result = mp.music_player({"query": "coqueta", "service": "youtube"})
    assert "status=unverified" in result


def test_a_browser_that_refuses_to_open_is_a_failure(monkeypatch):
    monkeypatch.setattr(mp, "_youtube_resolve", lambda _q, _a="": ("vid123", "Coqueta", ""))
    monkeypatch.setattr(mp, "_open_in_default_browser", lambda _url: False)
    result = mp.music_player({"query": "coqueta", "service": "youtube"})
    assert "status=failed" in result


def test_the_player_button_is_pressed_when_the_autoplay_was_blocked(monkeypatch):
    """Firefox blocks audible autoplay. The song still starts."""
    pressed = []

    class _Button:
        def invoke(self):
            pressed.append("invoked")

    class _Window:
        def __init__(self):
            self.states = ["paused", "playing"]

        def descendants(self, control_type=None):
            return [_Button()]

    window = _Window()
    monkeypatch.setattr(mp, "_find_video_window", lambda _t: window)
    monkeypatch.setattr(mp, "_player_button",
                        lambda _w: (_Button(), "play"))
    monkeypatch.setattr(mp, "_player_state", lambda _w: window.states.pop(0)
                        if window.states else "playing")
    monkeypatch.setattr(mp, "_press", lambda _b: pressed.append("pressed") or True)
    monkeypatch.setattr(mp.time, "sleep", lambda _s: None)

    state, reason = mp._youtube_ensure_playing("Coqueta", timeout=2)
    assert state == "playing"
    assert reason == ""
    assert pressed


# ── The Spotify route ──────────────────────────────────────────────────────

def test_spotify_falls_back_to_youtube_when_nobody_had_chosen(monkeypatch, tmp_path):
    """In automatic mode, a Spotify that could not do it hands over.

    Automatic means exactly that: nobody has said which player they want,
    so the assistant is free to try the other one rather than leave the
    user with silence.
    """
    monkeypatch.setattr(mp, "_spotify_api_play", lambda _q, _a: ("", "no account"))
    monkeypatch.setattr(mp, "_spotify_desktop_play", lambda _q, _a: "")
    monkeypatch.setattr(mp.time, "sleep", lambda _s: None)
    _stub_youtube(monkeypatch)
    with _own_config(tmp_path, music_service="auto"):
        result = mp.music_player({"query": "coqueta"})
    assert "status=done" in result
    assert "service=youtube" in result


def test_spotify_that_the_request_named_does_not_open_youtube_on_top(monkeypatch):
    """The player the request named is the player that plays.

    Opening another one on top of it is what made every song open in
    Spotify first and then in YouTube: the user saw the wrong player
    first, every single time, with no way to say so.
    """
    monkeypatch.setattr(mp, "_spotify_api_play", lambda _q, _a: ("", "no account"))
    monkeypatch.setattr(mp, "_spotify_desktop_play", lambda _q, _a: "")
    monkeypatch.setattr(mp.time, "sleep", lambda _s: None)
    monkeypatch.setattr(mp, "_open_in_default_browser",
                        lambda _url: pytest.fail("the browser must stay out"))
    result = mp.music_player({"query": "coqueta", "service": "spotify"})
    assert "service=youtube" not in result, result
    assert "status=failed" in result, result


def test_the_player_chosen_in_controls_does_not_open_youtube_on_top(monkeypatch, tmp_path):
    """The choice in CONTROLS is the user choosing, too.

    This is the case that was broken: the selector in CONTROLS was
    remembered, shown and saved — and then ignored, so the song still
    opened in Spotify and then in YouTube on top of it.
    """
    monkeypatch.setattr(mp, "_spotify_api_play", lambda _q, _a: ("", "no account"))
    monkeypatch.setattr(mp, "_spotify_desktop_play", lambda _q, _a: "")
    monkeypatch.setattr(mp.time, "sleep", lambda _s: None)
    monkeypatch.setattr(mp, "_open_in_default_browser",
                        lambda _url: pytest.fail("the browser must stay out"))
    with _own_config(tmp_path, music_service="spotify"):
        result = mp.music_player({"query": "coqueta"})
    assert "service=youtube" not in result, result
    assert "status=failed" in result, result


def test_the_choice_in_controls_is_the_one_that_is_obeyed(monkeypatch, tmp_path):
    """With YouTube chosen in CONTROLES, Spotify is never opened."""
    monkeypatch.setattr(mp, "_spotify_api_play",
                        lambda _q, _a: pytest.fail("Spotify must stay out"))
    monkeypatch.setattr(mp, "_spotify_desktop_play",
                        lambda _q, _a: pytest.fail("Spotify must stay out"))
    _stub_youtube(monkeypatch)
    with _own_config(tmp_path, music_service="youtube"):
        result = mp.music_player({"query": "coqueta"})
    assert "service=youtube" in result, result


def test_spotify_does_not_open_another_player_over_a_track_it_already_started(monkeypatch):
    monkeypatch.setattr(mp, "_spotify_api_play", lambda _q, _a: ("spotify:track:1", ""))
    monkeypatch.setattr(mp, "_spotify_playing", lambda _uri: True)
    monkeypatch.setattr(mp, "_open_in_default_browser",
                        lambda _url: pytest.fail("the browser must stay out"))
    result = mp.music_player({"query": "coqueta", "service": "spotify"})
    assert "status=done" in result
    assert "service=spotify" in result


def test_spotify_that_opened_a_track_without_confirming_says_so(monkeypatch):
    monkeypatch.setattr(mp, "_spotify_api_play", lambda _q, _a: ("spotify:track:1", ""))
    monkeypatch.setattr(mp, "_spotify_playing", lambda _uri: False)
    monkeypatch.setattr(mp, "_VERIFY_TIMEOUT", 0.01)
    result = mp.music_player({"query": "coqueta", "service": "spotify"})
    assert "status=unverified" in result


def test_the_exact_track_is_chosen_when_there_are_many(monkeypatch):
    """'Coqueta' must not open a remix that happens to contain the word."""
    module = type("M", (), {})()
    module._track_summary = lambda track: {"title": track["name"]}
    monkeypatch.setattr(mp, "_spotify_module", lambda: module)
    tracks = [
        {"name": "Coqueta (Remix)", "uri": "spotify:track:1"},
        {"name": "Coqueta", "uri": "spotify:track:2"},
        {"name": "Otra cosa", "uri": "spotify:track:3"},
    ]
    chosen = mp._pick_spotify_track(tracks, "Coqueta", "")
    assert chosen["uri"] == "spotify:track:2"


# ── The tool itself ────────────────────────────────────────────────────────

def test_the_declaration_is_valid():
    from core import tool_loader
    record = tool_loader.validate_declaration(
        mp.TOOL, "music_player.py", tool_loader.ORIGIN_ACTION, handler=mp.music_player)
    assert record.valid, record.error
    assert record.name == "music_player"


def test_the_action_is_discovered_with_its_handler():
    from core import action_loader
    registry = action_loader.discover_actions(BASE / "actions", logger=lambda _m: None)
    assert registry.has("music_player")


def test_the_tool_lives_under_the_media_operator():
    from core.operators.media import MediaOperator
    assert "music_player" in MediaOperator.tools


def test_the_tool_is_one_of_the_core_ones():
    from providers.schema import CORE_TOOLS
    assert "music_player" in CORE_TOOLS


def test_the_risk_of_the_tool_is_known():
    from core import tool_audit
    assert tool_audit.classify_risk("music_player", {"query": "coqueta"}) == "standard"
    assert tool_audit.is_reviewed_tool("music_player")


def test_the_prompt_tells_the_model_to_use_it():
    prompt = (BASE / "core" / "prompt.txt").read_text(encoding="utf-8")
    assert "music_player" in prompt


def test_nothing_in_the_action_shells_out():
    """The rule of the house: no shell, no eval, no exec."""
    source = (BASE / "actions" / "music_player.py").read_text(encoding="utf-8")
    for forbidden in ("shell=True", "os.system", "subprocess", "eval(", "exec("):
        assert forbidden not in source, forbidden


def test_the_old_brand_is_not_in_the_action():
    # The old name is assembled here on purpose: writing it whole would put
    # the very string this test looks for into a file the identity test
    # reads.
    old_brand = "jar" + "vis"
    source = (BASE / "actions" / "music_player.py").read_text(encoding="utf-8").lower()
    assert old_brand not in source
