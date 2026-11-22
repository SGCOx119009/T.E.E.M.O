"""
Play music the way a person does: open Spotify or YouTube, and let it sing.

WHY THIS IS NOT THE HUD's OWN PLAYER
    The HUD has a player, and it could not play a YouTube stream: the media
    foundation on this machine offered no decoder for the formats YouTube
    serves, and the player answered "av1_mf / h264_mf / hevc_mf could not find
    any MFT". So a song asked for out loud died in the one place the
    assistant could have played it.

    The fix is not a better decoder. It is to stop insisting on playing it
    INSIDE the program. Somebody asking for a song wants to hear it, in a
    player they already have, and they want it to start by itself.

TWO ROUTES, AND THE MUSIC ACTUALLY STARTS ON BOTH
    Spotify: the song is found in Spotify and opened by its own URI, so the
    Spotify client starts playing that exact track. When the Spotify account
    is connected, the track is chosen from the official search API and the
    playback is confirmed through it; without a connection the desktop
    client is driven through the Windows accessibility tree instead.

    YouTube: the first result for the request is resolved and opened in the
    DEFAULT browser with autoplay, and the player is inspected afterwards.
    If the browser refused to autoplay - Firefox does, by default - the play
    button is pressed for the user, so the song still starts.

WHICH ROUTE
    A setting decides, and the default is 'auto': Spotify when it is
    installed on this machine, YouTube otherwise. YouTube is the fallback on
    purpose - it needs no account, no client id and no token, so it always
    works.

WHAT IS REPORTED
    Only what was checked. When the browser opened the video and the player
    shows it playing, that is reported as done. When the page opened but the
    player could not be read, the answer says exactly that, instead of
    claiming a song is playing because a URL was requested.
"""
from __future__ import annotations

import os
import re
import time
import unicodedata
import webbrowser
from urllib.parse import quote

from core import outcome

_MAX_QUERY = 200
_YT_WATCH = "https://www.youtube.com/watch?v={video_id}&autoplay=1"
_YT_SEARCH = "https://www.youtube.com/results?search_query={query}"
_SPOTIFY_SEARCH = "spotify:search:{query}"
_VERIFY_TIMEOUT = 9.0
_OPEN_TIMEOUT = 6.0

# Words that lead a request and mean nothing to a search engine. Only verbs
# and articles: a noun like "song" is part of a title often enough
# ("Song for Someone") that removing it breaks the request.
_LEADING_VERBS = {
    "pon", "poner", "ponme", "ponlo", "ponga", "play", "plays", "playing",
    "played", "reproduce", "reproducir", "reproduceme", "escucha", "escuchar",
    "toca", "tocar", "suena", "suene", "mete", "sound", "sounds", "listen",
    "joue", "jouer", "mets", "metti", "stuck", "stcke", "ascolta", "spiele",
}
_LEADING_ARTICLES = {"la", "el", "los", "las", "the", "a", "an", "le", "la"}

# A request that names no song at all. Playing "music" is not a mistake to
# correct, it is a question to ask.
_GENERIC_REQUESTS = {
    "music", "musica", "musique", "musik", "musica", "cancion", "canción",
    "song", "musique", "kanta", "musica", "musica", "track", "pista",
}

# The YouTube player's own button. The names were read off a real browser:
# the video playing shows "Pausa (k)" in Spanish and "Pause (k)" in English,
# and paused it shows "Reproducir (k)" / "Play (k)".
#
# They are written FOLDED, because that is how they are compared: the
# folding drops the parentheses, so "Pausa (k)" arrives as "pausa k".
#
# An EXACT list, not a prefix: the same page carries "Reproducir en el
# televisor", "Reproduccion automatica activada" and "Reproduccion del chat
# en vivo", and a prefix rule would read any of those as the player.
_PLAY_BUTTON_NAMES = {
    "play", "play k",
    "reproducir", "reproducir k", "reproduce", "reproduce k",
    "pause", "pause k",
    "pausa", "pausa k", "pausar", "pausar k",
}
_PAUSE_NAMES = {"pause", "pause k", "pausa", "pausa k", "pausar", "pausar k"}
# The group that holds the player. Looking inside it first keeps the other
# buttons of the page out of the answer.
_PLAYER_GROUP_HINTS = ("video player", "reproductor de video",
                       "reproductor de v", "player de video")


# ── Text helpers ────────────────────────────────────────────────────────────

def _fold(value: str) -> str:
    """Lowercase, without accents, without punctuation noise."""
    decomposed = unicodedata.normalize("NFKD", str(value or "").casefold())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join("".join(ch if ch.isalnum() or ch.isspace() else " "
                           for ch in stripped).split())


def _clean_query(raw: str) -> str:
    """The request without the verb that introduced it.

    "pon amor y motivo de grupo 5" -> "amor y motivo de grupo 5".
    """
    text = " ".join(str(raw or "").split())
    text = text.replace("“", " ").replace("”", " ").replace("\"", " ")
    text = text.strip(" \t.,;:!?¿¡'")
    for _ in range(3):
        parts = text.split(" ", 1)
        if len(parts) < 2:
            break
        head = parts[0].strip(",.;:!?").casefold()
        if head in _LEADING_VERBS or head in _LEADING_ARTICLES:
            text = parts[1].strip()
            continue
        break
    return text.strip(" ,.;:!?")


def _generic_request(query: str) -> bool:
    return _fold(query) in {_fold(word) for word in _GENERIC_REQUESTS}


# ── Spotify ─────────────────────────────────────────────────────────────────

def _spotify_module():
    """The Spotify plugin's module, or None when it cannot be imported."""
    try:
        from plugins import spotify_control
        return spotify_control
    except Exception as exc:                       # noqa: BLE001
        print(f"[music] Spotify module unavailable: {type(exc).__name__}")
        return None


def _spotify_installed() -> bool:
    """Is there a Spotify on this machine to open?

    Three cheap checks, in order of cost: a running process, the URI
    handler in the registry, and the PROGRAM ITSELF.

    THE PROGRAM, NOT ITS FOLDER. The folder is not the application: a machine
    where Spotify was installed and then removed keeps the Spotify folder in
    APPDATA forever, and asking for the folder said "yes, Spotify is here" on
    a computer with no Spotify in it. The tool then spent half a minute trying
    to drive a client that did not exist before falling back to YouTube, and
    the user saw the wrong player open first every single time.
    """
    try:
        from core import appidentity
        if appidentity.running("Spotify"):
            return True
    except Exception:
        pass
    # The registry handler and the leftover folder BOTH survive an uninstall:
    # a machine without Spotify in it still answers "yes" to either of them.
    # Only the program itself proves that Spotify is here.
    candidates = []
    appdata = os.environ.get("APPDATA", "")
    localapp = os.environ.get("LOCALAPPDATA", "")
    if appdata:
        candidates.append(os.path.join(appdata, "Spotify", "Spotify.exe"))
    if localapp:
        candidates.append(os.path.join(localapp, "Spotify", "Spotify.exe"))
    candidates.append(os.path.join("C:", os.sep, "Program Files", "Spotify", "Spotify.exe"))
    candidates.append(os.path.join("C:", os.sep, "Program Files (x86)", "Spotify",
                                   "Spotify.exe"))
    return any(os.path.isfile(path) for path in candidates)


def _spotify_track_uri(track: dict) -> str:
    uri = str(track.get("uri") or "")
    return uri if uri.startswith("spotify:track:") else ""


def _pick_spotify_track(tracks: list[dict], query: str, artist: str) -> dict | None:
    """The track the user meant: exact title first, then a title that holds
    the request, then the top result."""
    module = _spotify_module()
    summarize = getattr(module, "_track_summary", None)
    wanted = _fold(query)
    exact: list[dict] = []
    loose: list[dict] = []
    for track in tracks:
        try:
            summary = summarize(track) if summarize else {}
        except Exception:
            summary = {}
        title = _fold(str(summary.get("title") or track.get("name") or ""))
        names = {_fold(str(item.get("name") or ""))
                 for item in (track.get("artists") or []) if isinstance(item, dict)}
        if artist and artist.strip() and not names & {_fold(artist.strip())}:
            continue
        if title == wanted:
            exact.append(track)
        elif wanted and wanted in title:
            loose.append(track)
    for pool in (exact, loose, tracks):
        for track in pool:
            if _spotify_track_uri(track):
                return track
    return None


def _spotify_api_play(query: str, artist: str) -> tuple[str, str]:
    """(result, reason). Empty result means the API could not do it."""
    module = _spotify_module()
    if module is None:
        return "", "the Spotify connection is not available"
    try:
        token, error = module._authorized_token()
        if error:
            return "", str(error)
        tracks, search_error = module._search_tracks(query, artist)
        if search_error:
            return "", str(search_error)
        if not tracks:
            return "", "Spotify found no track for that request"
        track = _pick_spotify_track(tracks, query, artist)
        if track is None:
            return "", "Spotify found no track for that request"
        uri = _spotify_track_uri(track)
        if not uri:
            return "", "Spotify did not return a playable track"
        try:
            os.startfile(uri)                      # noqa: S606 — no shell involved
        except OSError as exc:
            return "", f"Spotify could not be opened ({type(exc).__name__})"
        return uri, ""
    except Exception as exc:                       # noqa: BLE001
        return "", f"the Spotify connection failed ({type(exc).__name__})"


def _spotify_playing(uri: str) -> bool:
    """Is the track we asked for the one playing right now?"""
    module = _spotify_module()
    if module is None:
        return False
    try:
        status, payload, error = module._api_request(
            "GET", "/me/player/currently-playing")
        if error or status >= 400 or not isinstance(payload, dict):
            return False
        item = payload.get("item")
        if not isinstance(item, dict):
            return False
        return _spotify_track_uri(item) == uri
    except Exception:
        return False


def _spotify_launch() -> bool:
    """Ask Spotify to open itself.

    The client was installed on this machine and was never started: the tool
    looked for its window, found nothing and gave up, and the song never
    played. Spotify knows its own URI, so it is asked to open itself — and
    then the attempt is made again with its window on screen.
    """
    try:
        os.startfile("spotify:")
        return True
    except OSError as exc:
        print(f"[music] Spotify could not be opened ({type(exc).__name__})")
        return False


def _spotify_desktop_play(query: str, artist: str) -> str:
    """Drive the Spotify desktop client through the accessibility tree.

    Returns the plugin's own result string; it knows how to press the play
    control of an exact match and how to confirm it in the now-playing bar.
    """
    module = _spotify_module()
    if module is None:
        return ""
    try:
        result = str(module._desktop_play(query, artist) or "")
    except Exception as exc:                       # noqa: BLE001
        print(f"[music] Spotify desktop control failed: {type(exc).__name__}")
        result = ""
    if result and "status=failed" not in result:
        return result
    # THE CLIENT WAS NOT ON SCREEN. It is opened, and the attempt is made
    # again with its window there.
    if not _spotify_launch():
        return result
    for _ in range(12):
        time.sleep(1.0)
        try:
            again = str(module._desktop_play(query, artist) or "")
        except Exception:
            continue
        if again and "status=failed" not in again:
            return again
        result = again or result
    return result


def _spotify_result_fields(result: str) -> dict:
    """The fields of a result string, with or without quotes.

    The values come in two shapes: a word without spaces is written bare
    (track=Monster) and one with spaces is written between quotes
    (track="Skillet Monster"). Both are read, because the two shapes come
    from two different places and the field that matters is the title of
    the song that is really playing.
    """
    fields: dict[str, str] = {}
    for key, quoted, bare in re.findall(
        r'(\w+)=(?:"([^"]*)"|([^\s]+))', result or ""
    ):
        fields[key] = quoted if quoted else bare
    return fields


# ── YouTube ────────────────────────────────────────────────────────────────

def _match_score(title: str, query: str, artist: str) -> int:
    """How much a video's title looks like the song that was asked for.

    The title is folded the same way the request is, so accents and capital
    letters do not count against a match.
    """
    t = _fold(title)
    if not t:
        return 0
    score = 0
    q = _fold(query)
    if q:
        if t == q:
            score += 6
        elif q in t:
            score += 4
        elif any(word in t for word in q.split() if len(word) > 2):
            score += 1
    a = _fold(artist)
    if a:
        if a in t:
            score += 3
        elif any(word in t for word in a.split() if len(word) > 2):
            score += 1
        else:
            # The artist was named and the title does not carry them: this is
            # a DIFFERENT song that happens to share a word with the request.
            # "Mandarina" by Gian Marco came back as a video about Roblox
            # that happened to have the word in its title.
            score = 0
    return score


def _youtube_resolve(query: str, artist: str = "") -> tuple[str, str, str]:
    """(video id, title, channel) for the result that IS the request.

    THE FIRST RESULT IS NOT THE SONG. Asked for "Mandarina" by Gian Marco,
    the search answered with a different song entirely, and the tool opened
    it: the user asked for one song and heard another. So several results are
    asked for and the one whose title carries the request — the title and,
    when there is one, the artist — is the one that is opened. A result that
    carries neither is not opened at all: the search page is, and the answer
    says the song was not identified.
    """
    try:
        import yt_dlp
    except Exception:
        return "", "", ""
    # THE SEARCH IS FLAT. Asking for the full details of every result cost
    # thirteen seconds per song — measured: 32 s for the first, 13 s and 20 s
    # for the next ones — and the only thing needed from a result is its
    # title and its id. The flat search answers in a fraction of that.
    options = {"quiet": True, "no_warnings": True, "skip_download": True,
               "noplaylist": True, "extract_flat": True}
    # THE ARTIST GOES INTO THE SEARCH. With the title alone, "Mandarina"
    # brought a video about Roblox that happened to have the word in it; with
    # the artist as well, the song comes first.
    search = f"{query} {artist}".strip() if artist else query
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(f"ytsearch6:{search}", download=False)
    except Exception as exc:                       # noqa: BLE001
        print(f"[music] YouTube lookup failed: {type(exc).__name__}")
        return "", "", ""
    entries = []
    if info:
        entries = info.get("entries") or []
    best = None
    best_score = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        score = _match_score(str(entry.get("title") or ""), query, artist)
        if score > best_score:
            best, best_score = entry, score
    if best is None or best_score < 2:
        # Nothing that looks like the request: the search page is opened
        # instead, and the user is told the song was not identified.
        return "", "", ""
    video_id = str(best.get("id") or "")
    title = str(best.get("title") or "")
    channel = str(best.get("uploader") or best.get("channel") or "")
    return video_id, title, channel


def _open_in_default_browser(url: str) -> bool:
    """Open the URL with the browser the system uses by default."""
    try:
        return bool(webbrowser.open(url))
    except Exception as exc:                       # noqa: BLE001
        print(f"[music] the browser did not open: {type(exc).__name__}")
        return False


def _top_level_windows() -> list:
    try:
        from pywinauto import Desktop
        return list(Desktop(backend="uia").windows(
            visible_only=True, top_level_only=True))
    except Exception as exc:                       # noqa: BLE001
        print(f"[music] the window list is not available: {type(exc).__name__}")
        return []


def _find_video_window(needle: str):
    """The browser window that opened the video."""
    windows = _top_level_windows()
    folded = _fold(needle)
    for window in windows:
        try:
            title = str(window.element_info.name or "")
        except Exception:
            continue
        if folded and folded in _fold(title):
            return window
    for window in windows:
        try:
            title = _fold(str(window.element_info.name or ""))
        except Exception:
            continue
        if "youtube" in title:
            return window
    return None


def _is_player_group(name: str) -> bool:
    folded = _fold(name)
    return any(hint in folded for hint in _PLAYER_GROUP_HINTS)


def _button_inside(container):
    """The play/pause button inside a container, or (None, "")."""
    try:
        controls = container.descendants(control_type="Button")
    except TypeError:
        try:
            controls = [c for c in container.descendants()
                        if str(c.element_info.control_type or "") == "Button"]
        except Exception:
            return None, ""
    except Exception:
        return None, ""
    for control in controls:
        try:
            name = _fold(str(control.element_info.name or ""))
        except Exception:
            continue
        if name in _PLAY_BUTTON_NAMES:
            return control, name
    return None, ""


def _player_button(window):
    """(control, name) of the player's play/pause button, or (None, "").

    The player's own group is looked at first, because the page carries
    several buttons that merely begin with the same word.
    """
    try:
        groups = [c for c in window.descendants(control_type="Group")
                  if _is_player_group(str(c.element_info.name or ""))]
    except TypeError:
        try:
            groups = [c for c in window.descendants()
                      if str(c.element_info.control_type or "") == "Group"
                      and _is_player_group(str(c.element_info.name or ""))]
        except Exception:
            groups = []
    except Exception:
        groups = []
    for group in groups:
        button, name = _button_inside(group)
        if button is not None:
            return button, name
    return _button_inside(window)


def _player_state(window) -> str:
    """"playing", "paused" or "unknown"."""
    _, name = _player_button(window)
    if not name:
        return "unknown"
    return "playing" if name in _PAUSE_NAMES else "paused"


def _press(button) -> bool:
    """Press the player's button, the way the desktop path does."""
    try:
        if hasattr(button, "invoke"):
            button.invoke()
            return True
    except Exception as exc:                       # noqa: BLE001
        print(f"[music] the player button did not answer: {type(exc).__name__}")
    try:
        button.click_input()
        return True
    except Exception:
        return False


def _youtube_ensure_playing(title: str, timeout: float = _VERIFY_TIMEOUT):
    """(state, reason). The player is inspected; a blocked autoplay is
    answered by pressing the play button once."""
    deadline = time.monotonic() + timeout
    window = None
    while time.monotonic() < deadline:
        if window is None:
            window = _find_video_window(title)
        if window is not None:
            state = _player_state(window)
            if state == "playing":
                return "playing", ""
            if state == "paused":
                button, _ = _player_button(window)
                if button is not None and _press(button):
                    time.sleep(1.2)
                    if _player_state(window) == "playing":
                        return "playing", ""
                    return "paused", ("the play button was pressed but the "
                                      "player did not start")
                return "unknown", ("the player shows a paused video and its "
                                   "play button could not be pressed")
        time.sleep(0.8)
    if window is None:
        return "unknown", ("no browser window carrying the video was found")
    return "unknown", "the player could not be read"


# ── The tool ────────────────────────────────────────────────────────────────

def _preferred_service(requested: str) -> str:
    """'spotify', 'youtube' - the route this request should take."""
    chosen = (requested or "auto").strip().lower()
    if chosen in ("spotify", "youtube"):
        return chosen
    try:
        from memory import config_manager
        stored = str(config_manager.get_music_service() or "auto").lower()
    except Exception:
        stored = "auto"
    if stored in ("spotify", "youtube"):
        return stored
    return "spotify" if _spotify_installed() else "youtube"


def _service_was_chosen(requested: str) -> bool:
    """Has the user picked a player? Then the other one stays closed.

    There are two ways to pick one, and the second one is the one that was
    being ignored: the request can name the player, and the CONTROLS panel
    can hold the choice for good. The choice in CONTROLS is the one the
    user makes ONCE and expects to be obeyed, and it was being dropped on
    the floor — so every song opened in Spotify and then in YouTube on top
    of it, and the user saw the wrong player first every single time with
    no way to say so.
    """
    if str(requested or "").strip().lower() in ("spotify", "youtube"):
        return True
    try:
        from memory import config_manager
        stored = str(config_manager.get_music_service() or "auto").lower()
    except Exception:
        stored = "auto"
    return stored in ("spotify", "youtube")


def _play_spotify(query: str, artist: str) -> tuple[str, bool]:
    """The Spotify route. -> (result, another route may still be tried)

    The second value is False when Spotify has already been asked to play
    something - opening a track that is on its way is not something to undo
    by opening another player on top of it.
    """
    uri, reason = _spotify_api_play(query, artist)
    if uri:
        deadline = time.monotonic() + _VERIFY_TIMEOUT
        while time.monotonic() < deadline:
            if _spotify_playing(uri):
                return outcome.done(
                    "music_player", service="spotify", track=query,
                    artist=artist or None, verified=True), False
            time.sleep(0.7)
        return outcome.unverified(
            "music_player",
            "Spotify was asked to play the track but the account did not "
            "report it as playing yet",
            service="spotify", track=query, artist=artist or None), False

    desktop = ""
    # PATIENCE, ONCE.
    #
    # The first search after Spotify wakes up is slower than the next ones:
    # the results list appears late, and the client gave up before it did.
    # A second attempt, a couple of seconds later, is the difference between
    # "Spotify could not find it" and "Spotify was still loading".
    for attempt in range(2):
        desktop = _spotify_desktop_play(query, artist)
        if desktop and "status=failed" not in desktop:
            break
        if attempt == 0:
            time.sleep(2.5)
    if desktop:
        if "status=done" in desktop:
            fields = _spotify_result_fields(desktop)
            return outcome.done(
                "music_player", service="spotify",
                track=fields.get("track") or query,
                artist=fields.get("artist") or (artist or None),
                verified=True), False
        if "status=ask" in desktop:
            return outcome.ask(
                "music_player",
                "several Spotify songs match that name; ask the user which "
                "title and artist they meant, and do not start playback yet",
                service="spotify", track=query), False
        if "status=unverified" in desktop:
            return outcome.unverified(
                "music_player",
                "Spotify was opened and asked to play the track, but the "
                "client did not confirm it",
                service="spotify", track=query, artist=artist or None), False
        # status=failed: Spotify could not do it at all, so another player
        # may still be able to.
        return outcome.failed(
            "music_player",
            f"Spotify could not be used ({reason or 'the song was not found'})",
            service="spotify", track=query), True

    return outcome.failed(
        "music_player",
        f"Spotify could not be used ({reason or 'it is not connected'})",
        service="spotify", track=query), True


def _play_youtube(query: str, artist: str = "") -> str:
    """The YouTube route: resolve, open in the default browser, make it
    play, and check."""
    video_id, title, channel = _youtube_resolve(query, artist)
    if not video_id:
        if not _open_in_default_browser(
                _YT_SEARCH.format(query=quote(query, safe=""))):
            return outcome.failed(
                "music_player",
                "YouTube could not be opened in the browser",
                service="youtube", track=query)
        return outcome.unverified(
            "music_player",
            "the YouTube search page was opened, but the first result could "
            "not be resolved so the song was not started automatically",
            service="youtube", track=query)

    url = _YT_WATCH.format(video_id=video_id)
    if not _open_in_default_browser(url):
        return outcome.failed(
            "music_player",
            "the browser did not open the video",
            service="youtube", track=title or query, url=url)

    state, reason = _youtube_ensure_playing(title or video_id)
    if state == "playing":
        return outcome.done(
            "music_player", service="youtube", track=title or query,
            channel=channel or None, url=url, verified=True)
    return outcome.unverified(
        "music_player",
        f"the video was opened in the browser, but the playback could not be "
        f"confirmed ({reason})",
        service="youtube", track=title or query, channel=channel or None,
        url=url)


def _pause_youtube(title: str = "") -> str:
    window = _find_video_window(title)
    if window is None:
        return outcome.failed(
            "music_player", "no YouTube window was found to pause")
    state = _player_state(window)
    if state == "unknown":
        return outcome.failed(
            "music_player", "the YouTube player could not be read")
    button, _ = _player_button(window)
    if button is None or not _press(button):
        return outcome.failed(
            "music_player", "the YouTube play button could not be pressed")
    time.sleep(0.8)
    after = _player_state(window)
    if after == "paused":
        return outcome.done("music_player", service="youtube", verified=True)
    return outcome.unverified(
        "music_player",
        "the pause was sent but the player has not reported it",
        service="youtube")


def music_player(parameters: dict = None, response=None, player=None,
                 session_memory=None) -> str:
    params = parameters or {}
    action = str(params.get("action") or "play").strip().lower()
    query = str(params.get("query") or params.get("title")
                 or params.get("song") or params.get("track") or "").strip()
    artist = str(params.get("artist") or "").strip()
    service = str(params.get("service") or "").strip().lower()

    if action in ("pause", "resume", "stop"):
        if action == "resume":
            action = "pause"          # the player's button is a toggle
        window = _find_video_window("")
        if window is not None and _player_state(window) != "unknown":
            return _pause_youtube()
        module = _spotify_module()
        if module is not None:
            try:
                token, error = module._authorized_token()
                if not error:
                    method = "PUT"
                    path = "/me/player/pause"
                    status, _payload, api_error = module._api_request(
                        method, path)
                    if status and status < 400:
                        return outcome.done(
                            "music_player", service="spotify", verified=True)
                    if api_error:
                        return outcome.failed(
                            "music_player", str(api_error), service="spotify")
            except Exception:
                pass
        return outcome.failed(
            "music_player",
            "nothing that could be paused was found playing")

    if action not in ("play", ""):
        return outcome.failed(
            "music_player", f"'{action}' is not something this tool can do")

    if not query:
        return outcome.ask("music_player",
                           "which song should I play?")

    if len(query) > _MAX_QUERY:
        return outcome.failed(
            "music_player",
            "that request is too long to search for")

    cleaned = _clean_query(query)
    if (not cleaned
            or _generic_request(cleaned)
            or _fold(cleaned) in {_fold(word) for word in _LEADING_VERBS}):
        return outcome.ask(
            "music_player",
            "which song or artist should I play? If they did not say, ask for "
            "the title before calling this again.")

    route = _preferred_service(service)
    if route == "spotify":
        result, may_fallback = _play_spotify(cleaned, artist)
        # Spotify is the preferred route, but it is not the only one: a
        # missing client or an unconnected account must not end the request.
        #
        # UNLESS THE USER ASKED FOR SPOTIFY. Opening another player over the
        # one they chose is not what they asked for, and it is what made every
        # song open in Spotify first and then in YouTube: the user saw the
        # wrong player first every single time and had no way to say so.
        #
        # AND THE CHOICE IN CONTROLS COUNTS AS THE USER HAVING ASKED. That is
        # the whole point of the selector: the user picks a player once, and
        # the assistant stops asking a different question every time.
        if may_fallback and not _service_was_chosen(service):
            fallback = _play_youtube(cleaned, artist)
            if "status=failed" not in fallback:
                return fallback
        return result
    return _play_youtube(cleaned, artist)


TOOL = {
    "name": "music_player",
    "description": (
        "Plays a SONG out loud: it opens Spotify or YouTube on this computer "
        "and starts the music by itself. Use it whenever the user asks for a "
        "song, a track, an album or 'something to listen to' - in any "
        "language, and whether they name the song or only the artist. "
        "It never plays inside the assistant's own window: the music starts "
        "in Spotify, or in the default browser on YouTube, and keeps playing "
        "after the assistant goes quiet. "
        "'query' is the song as the user asked for it, in English if you can: "
        "the title and, when they gave one, the artist. 'artist' is optional "
        "and helps when a title has many versions. "
        "'service' picks the route: 'spotify', 'youtube', or 'auto' (the "
        "default - Spotify when it is installed here, YouTube otherwise). "
        "'action' is 'play' (the default), 'pause' or 'stop'. "
        "When the user names no song at all, ask them which one instead of "
        "guessing - do not call this tool with 'music' as the query. "
        "If it answers 'status=done', say in one short sentence that the "
        "music is playing. If it answers 'status=unverified', say plainly "
        "that it was asked for but could not be confirmed."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "query": {
                "type": "STRING",
                "description": "The song to play: title, and artist if known.",
            },
            "artist": {
                "type": "STRING",
                "description": "The artist, when the user gave one.",
            },
            "service": {
                "type": "STRING",
                "enum": ["auto", "spotify", "youtube"],
                "description": "Which player to use. 'auto' decides.",
            },
            "action": {
                "type": "STRING",
                "enum": ["play", "pause", "stop"],
                "description": "What to do. 'play' is the default.",
            },
        },
        "required": ["query"],
    },
    "handler": music_player,
}
