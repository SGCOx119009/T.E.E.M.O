"""
providers/tts_system.py — speech synthesis through the Windows voices already
installed on the machine.

WHY THIS ONE EXISTS AT ALL
    Every other way of speaking locally starts with a download — a neural voice
    is tens of megabytes and one per language. That is fine as a choice and
    wrong as the only option, because it means local mode does not work until
    something has been fetched, and it works in one language until something
    else has been fetched.

    Windows already ships a speech synthesiser with the languages its owner
    installed. Nothing to download, nothing to build, no model files in the
    repository. It is not as good as a neural voice. It is available
    immediately, everywhere, which for a fallback matters more.

    TWO ENGINES, TWO CATALOGUES, ONE LIST
        SAPI 5 is the classic engine. It is driven here through `comtypes`
        (`SAPI.SpVoice`), which needs no Python package beyond the binding
        itself, and it is the route that works on every Windows install.

        SAPI OneCore is the modern engine, and it is the one that carries the
        "Modern" voices Windows 10 and 11 download on demand. It is a
        DIFFERENT CATALOGUE: a voice installed for OneCore is often not in the
        SAPI 5 list at all, and vice versa. That is the whole reason a user
        can install a voice for their language, see it in the list, and still
        be told the engine does not cover the language.

        So this module reads BOTH catalogues and merges them into one list,
        each entry carrying the engine that can speak it. A voice the classic
        engine cannot see is not missing; it is spoken by the other one.

    THE HONEST LIMIT: it speaks the languages the machine has VOICES for, which
    is not the same as the languages the machine displays. Measured on an
    ordinary Windows 10 install: two voices, both English. A Turkish sentence
    came out as English phonetics — poor, but audible; a Russian one came out as
    nothing at all, with a success code. `speak()` turns that silence into an
    error that names the cause, because a fallback that goes quiet in some
    languages and not others is worse than one that says what is wrong.

    For an assistant that genuinely switches language mid-conversation, a
    multilingual hosted voice is the better choice and this is the floor beneath
    it.

THE ENCODING TRAP, WHICH IS THE WHOLE REASON THIS FILE IS CAREFUL
    The text to speak is whatever language the conversation is in. Passing it as
    a command-line argument means it goes through the shell's code page, and on
    a Turkish Windows that is cp1254, on a Russian one cp1251, on a Japanese one
    cp932 — none of which can carry the others. This project has already been
    bitten by exactly this: a status line with an emoji in it raised
    UnicodeEncodeError and took the session down.

    So the text is NEVER an argument on the route that shells out. It is
    written to a UTF-8 file and the engine is told to read that file. The
    in-process route (`comtypes`) has no code page at all, which is one more
    reason it is the primary one.

NO LANGUAGE DETECTION HERE EITHER
    Which voice speaks is a setting, listed from what the system actually has
    installed. Sentences are split on punctuation — the marks that end a
    sentence in Latin, CJK, Arabic and Devanagari scripts — not by recognising
    words. Splitting is only there so speech can start before the whole answer
    has been synthesised.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import unicodedata
from typing import AsyncIterator, Optional

from . import audio
from .base import DEFAULT_CAPS, Field

# Sentence-ending punctuation across the scripts this assistant is actually used
# in. Not grammar and not language detection — just the marks after which it is
# safe to start speaking while the rest is still being made.
#
# TWO RULES, BECAUSE THE SCRIPTS DIFFER
#     After a full stop, a space is required: without it "3.14" and every
#     version number in a technical answer become two sentences.
#     After 。！？ and the Arabic and Devanagari stops, no space is required —
#     Japanese and Chinese do not put one there, and demanding it meant a whole
#     CJK paragraph was synthesised as a single unbroken block. Measured: the
#     splitter left "今日はいい天気です。مرحبا بك." whole.
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+|(?<=[。！？؟۔।])\s*")

# Windows only: keep a console window from flashing open on every sentence.
_NO_WINDOW = 0x08000000

# The two engines, by name. SAPI5 first: it is on every Windows install, and
# OneCore is an addition, never a requirement.
_ENGINE_SAPI5 = "SAPI 5"
_ENGINE_ONECORE = "SAPI OneCore"


def _run(args: list[str], timeout: float = 60.0,
         stdin_data: Optional[bytes] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        input=stdin_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        creationflags=_NO_WINDOW,
    )


def _powershell() -> Optional[str]:
    """PowerShell 7 if it is there, Windows PowerShell otherwise."""
    return shutil.which("pwsh") or shutil.which("powershell")


def _has_comtypes() -> bool:
    from importlib.util import find_spec
    try:
        return find_spec("comtypes") is not None
    except Exception:
        return False


def _has_winrt() -> bool:
    from importlib.util import find_spec
    try:
        return find_spec("winrt") is not None
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  The catalogue: every voice this machine has, from every engine
# ─────────────────────────────────────────────────────────────────────────────
# Enumerating voices costs something — a COM call for SAPI 5, an async
# enumeration for OneCore — and the panel rebuilds its form on every dropdown
# change, so the merged list is answered once and cached. Measured on the
# classic engine: 1.70 seconds per enumeration, every single call.
#
# The installed voices do not change while the app is running, so this is
# answered once. `refresh=True` exists for somebody who has just installed a
# voice and does not want to restart.
_voice_cache: Optional[list] = None
_voice_lock = threading.Lock()


class Voice:
    """One installed voice, and the engine that can speak it."""

    __slots__ = ("name", "engine", "language", "token")

    def __init__(self, name: str, engine: str, language: str = "", token=None):
        self.name = name
        self.engine = engine
        self.language = language
        self.token = token          # the engine's own handle, when it has one

    def __repr__(self) -> str:
        return f"Voice({self.name!r}, {self.engine})"


def prefetch(on_ready=None) -> None:
    """Warm the voice list off the interface thread.

    `on_ready` is called from the worker when the list lands, so an interface
    that drew an empty dropdown can fill it in. It runs on a NON-UI thread —
    whatever is passed must marshal itself back, and the panel does that with a
    Qt signal rather than touching a widget from here.

    Called when the panel opens, never at startup: nothing in this folder may
    run before somebody asks for it."""
    if _voice_cache is not None:
        if on_ready:
            on_ready()
        return

    def work():
        list_voices()
        if on_ready:
            try:
                on_ready()
            except Exception:
                pass

    threading.Thread(target=work, daemon=True).start()


def list_voices(refresh: bool = False) -> list[str]:
    """Installed voice names. Empty when no engine is present, which the panel
    shows as 'no system voices found' rather than an empty dropdown with no
    explanation."""
    global _voice_cache
    with _voice_lock:
        if _voice_cache is not None and not refresh:
            return [v.name for v in _voice_cache]
    found = _read_voices()
    with _voice_lock:
        _voice_cache = found
    return [v.name for v in found]


def voice_records(refresh: bool = False) -> list[Voice]:
    """The full catalogue: names, the engine that speaks each, and its
    language. `list_voices()` is the names alone; this is what the rest of the
    module reasons about."""
    global _voice_cache
    with _voice_lock:
        if _voice_cache is not None and not refresh:
            return list(_voice_cache)
    found = _read_voices()
    with _voice_lock:
        _voice_cache = found
    return list(found)


def _read_voices() -> list[Voice]:
    """Every voice on this machine, from every engine, without duplicates."""
    found: list[Voice] = []
    seen: set[str] = set()

    for voice in _read_voices_sapi5():
        key = voice.name.strip().casefold()
        if key and key not in seen:
            seen.add(key)
            found.append(voice)

    for voice in _read_voices_onecore():
        key = voice.name.strip().casefold()
        if key and key not in seen:
            seen.add(key)
            found.append(voice)

    return found


_com_state = threading.local()


def _com_ready() -> None:
    """Initialise the COM apartment for THIS thread.

    `comtypes` initialises the apartment once, on the thread that imported it.
    Every other thread that touches SAPI has to ask for its own apartment, or
    COM answers CO_E_NOTINITIALIZED — which is exactly what happened when the
    catalogue was read from the event loop while the synthesis ran on a
    worker thread.

    The apartment is left initialised for the life of the thread: unbalancing
    it would only risk tearing down an apartment the thread is still using.
    """
    if getattr(_com_state, "ready", False):
        return
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:
        pass
    _com_state.ready = True


def _read_voices_sapi5() -> list[Voice]:
    """The classic engine's catalogue, through comtypes."""
    if not _has_comtypes():
        return []
    try:
        _com_ready()
        import comtypes.client

        sapi = comtypes.client.CreateObject("SAPI.SpVoice")
        out: list[Voice] = []
        for token in sapi.GetVoices():
            try:
                name = str(token.GetDescription() or "")
                language = ""
                try:
                    attr = token.GetAttribute("Language")
                    language = f"{int(attr) & 0x3FF:03d}"
                except Exception:
                    language = ""
                if name.strip():
                    out.append(Voice(name.strip(), _ENGINE_SAPI5, language, token))
            except Exception:
                continue
        return out
    except Exception as e:
        print(f"[tts] SAPI 5 catalogue unavailable: {e}")
        return []


def _read_voices_onecore() -> list[Voice]:
    """The modern engine's catalogue. Only present when `winrt` is installed."""
    if not _has_winrt():
        return []
    try:
        import winrt.windows.media.speechsynthesis as syn

        out: list[Voice] = []
        for voice in syn.SpeechSynthesizer.all_voices:
            try:
                name = str(voice.display_name or "")
                language = str(voice.language or "")
                if name.strip():
                    out.append(Voice(name.strip(), _ENGINE_ONECORE, language, voice))
            except Exception:
                continue
        return out
    except Exception as e:
        print(f"[tts] SAPI OneCore catalogue unavailable: {e}")
        return []


def engine_name() -> str:
    """What is actually installed, as one name. Empty when nothing is."""
    if _has_comtypes():
        try:
            import comtypes.client
            comtypes.client.CreateObject("SAPI.SpVoice")
            return f"{_ENGINE_SAPI5} (comtypes)"
        except Exception:
            pass
    if _powershell():
        return f"{_ENGINE_SAPI5} (System.Speech)"
    if _has_winrt():
        return _ENGINE_ONECORE
    return ""


# ─────────────────────────────────────────────────────────────────────────────
#  Synthesis
# ─────────────────────────────────────────────────────────────────────────────
def _pcm_to_wav(pcm: bytes, rate: int = audio.SPEAKER_RATE,
                channels: int = 1) -> bytes:
    """Wrap raw PCM in a WAV container, so every engine's output can be read
    by the same reader."""
    bits = 16
    block = channels * bits // 8
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
    header += struct.pack("<IHHIIHH", 16, 1, channels, rate,
                          rate * block, block, bits)
    header += b"data" + struct.pack("<I", len(pcm))
    return header + pcm


def _script_of(text: str) -> str:
    """The name of the script a text is written in, for the error message.

    Only the scripts that change the answer. Latin is the default because it
    is the script of the overwhelming majority of the text that passes
    through here, and "this is Latin" never explains a silence.
    """
    samples = [c for c in text if not c.isspace() and not c.isascii()]
    if not samples:
        return "Latin"
    counts: dict[str, int] = {}
    for ch in samples:
        name = _script_of_char(ch)
        if name:
            counts[name] = counts.get(name, 0) + 1
    if not counts:
        return "Latin"
    return max(counts, key=lambda k: counts[k])


_SCRIPT_RANGES = (
    ("Cyrillic", 0x0400, 0x04FF),
    ("Greek", 0x0370, 0x03FF),
    ("Armenian", 0x0530, 0x058F),
    ("Georgian", 0x10A0, 0x10FF),
    ("Hebrew", 0x0590, 0x05FF),
    ("Arabic", 0x0600, 0x06FF),
    ("Devanagari", 0x0900, 0x097F),
    ("Thai", 0x0E00, 0x0E7F),
    ("Hangul", 0xAC00, 0xD7AF),
    ("Han", 0x4E00, 0x9FFF),
    ("Hiragana", 0x3040, 0x309F),
    ("Katakana", 0x30A0, 0x30FF),
    ("CJK", 0x3000, 0x303F),
)


def _script_of_char(ch: str) -> str:
    code = ord(ch)
    for name, low, high in _SCRIPT_RANGES:
        if low <= code <= high:
            return name
    return ""


class SystemTTS:
    def __init__(self, cfg: dict):
        self.voice = (cfg.get("voice") or "").strip()
        self.rate = int(cfg.get("rate") or 0)        # -10..10 on Windows
        self.caps = dict(DEFAULT_CAPS)
        self.caps["audio_out"] = True
        self._engine = engine_name()

    # -- the engines ---------------------------------------------------------
    def _synth_sapi5(self, text_file: str, wav_file: str) -> None:
        """SAPI 5 through comtypes. The primary route: no code page, no shell,
        no PowerShell between the text and the voice."""
        import comtypes.client

        _com_ready()
        sapi = comtypes.client.CreateObject("SAPI.SpVoice")
        sapi.Rate = max(-10, min(10, self.rate))
        if self.voice:
            sapi.Voice = self._sapi5_token(sapi, self.voice)

        stream = comtypes.client.CreateObject("SAPI.SpFileStream")
        # SSFMCreateForWrite = 3
        stream.Open(wav_file, 3)
        try:
            sapi.AudioOutputStream = stream
            with open(text_file, "r", encoding="utf-8") as f:
                text = f.read()
            sapi.Speak(text)
        finally:
            try:
                sapi.AudioOutputStream = None
            except Exception:
                pass
            stream.Close()

    def _sapi5_token(self, sapi, wanted: str):
        """The SAPI 5 token whose description is `wanted`."""
        for token in sapi.GetVoices():
            try:
                if str(token.GetDescription() or "").strip() == wanted:
                    return token
            except Exception:
                continue
        raise RuntimeError(
            f"there is no installed voice called {wanted!r} for the classic "
            f"speech engine")

    def _synth_powershell(self, text_file: str, wav_file: str) -> None:
        """SAPI 5 through System.Speech, for a machine without `comtypes`.

        The text is READ FROM A UTF-8 FILE, never interpolated into this
        script — see the module docstring. The voice name is the only thing
        substituted, and it is quoted and doubled so a name with an
        apostrophe cannot end the string early.
        """
        ps = _powershell()
        if not ps:
            raise RuntimeError("PowerShell was not found")
        voice = self.voice.replace("'", "''")
        select = (f"$s.SelectVoice('{voice}');" if self.voice else "")
        script = (
            "Add-Type -AssemblyName System.Speech;"
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            f"{select}"
            f"$s.Rate = {max(-10, min(10, self.rate))};"
            f"$t = [System.IO.File]::ReadAllText('{text_file}',"
            " [System.Text.Encoding]::UTF8);"
            f"$s.SetOutputToWaveFile('{wav_file}');"
            "$s.Speak($t);"
            "$s.Dispose();"
        )
        r = _run([ps, "-NoProfile", "-NonInteractive", "-Command", script])
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode("utf-8", "replace")[:200]
                               or "the speech engine failed")

    def _synth_onecore(self, text_file: str, wav_file: str) -> None:
        """SAPI OneCore, for the voices the classic engine cannot see."""
        import asyncio as _asyncio

        import winrt.windows.media.speechsynthesis as syn
        import winrt.windows.storage.Streams as streams

        with open(text_file, "r", encoding="utf-8") as f:
            text = f.read()

        synth = syn.SpeechSynthesizer()
        if self.voice:
            for voice in syn.SpeechSynthesizer.all_voices:
                try:
                    if str(voice.display_name or "").strip() == self.voice:
                        synth.voice = voice
                        break
                except Exception:
                    continue

        async def _go() -> bytes:
            stream = await synth.synthesize_text_to_stream_async(text)
            reader = await streams.DataReader.load_async(
                stream.get_input_stream_at(0))
            data = reader.read_bytes(reader.size)
            return bytes(data)

        pcm = _asyncio.run(_go())
        with open(wav_file, "wb") as f:
            f.write(_pcm_to_wav(pcm))

    def _synth_blocking(self, text: str) -> bytes:
        # Both files are created and closed before the engine is pointed at
        # them: on Windows a file still open in this process cannot be written
        # by another one, which is the standard way this fails there.
        td = tempfile.mkdtemp(prefix="teemo_tts_")
        txt = os.path.join(td, "say.txt")
        wav = os.path.join(td, "say.wav")
        try:
            with open(txt, "w", encoding="utf-8") as f:
                f.write(text)
            self._synth(txt, wav)
            with open(wav, "rb") as f:
                return f.read()
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def _synth(self, txt: str, wav: str) -> None:
        """Speak `txt` into the WAV file at `wav`, with the best engine that
        can take this voice."""
        reasons: list[str] = []

        if self.voice:
            engine = self._engine_for(self.voice)
            if engine == _ENGINE_ONECORE:
                try:
                    self._synth_onecore(txt, wav)
                    return
                except Exception as e:
                    reasons.append(str(e))
            else:
                if _has_comtypes():
                    try:
                        self._synth_sapi5(txt, wav)
                        return
                    except Exception as e:
                        reasons.append(str(e))
                if _powershell():
                    try:
                        self._synth_powershell(txt, wav)
                        return
                    except Exception as e:
                        reasons.append(str(e))
            raise RuntimeError("; ".join(reasons) or "no speech engine accepted the request")

        # No voice chosen: the first engine that works takes it.
        if _has_comtypes():
            try:
                self._synth_sapi5(txt, wav)
                return
            except Exception as e:
                reasons.append(str(e))
        if _powershell():
            try:
                self._synth_powershell(txt, wav)
                return
            except Exception as e:
                reasons.append(str(e))
        if _has_winrt():
            try:
                self._synth_onecore(txt, wav)
                return
            except Exception as e:
                reasons.append(str(e))
        raise RuntimeError("; ".join(reasons) or "no speech engine was found")

    def _engine_for(self, name: str) -> str:
        """Which engine owns the voice called `name`."""
        for voice in voice_records():
            if voice.name.strip().casefold() == name.strip().casefold():
                return voice.engine
        return _ENGINE_SAPI5

    # -- the contract -------------------------------------------------------
    async def speak(self, text: str, voice: str = "") -> AsyncIterator[bytes]:
        """24 kHz mono PCM, a sentence at a time.

        Sentence by sentence rather than all at once because the whole answer
        can be several seconds of synthesis, and waiting for the last word
        before the first one is heard is the difference between an assistant
        that replies and one that pauses.
        """
        if voice:
            self.voice = voice
        produced = 0
        sentences = _split(text)
        for sentence in sentences:
            wav = await asyncio.to_thread(self._synth_blocking, sentence)
            pcm = audio.wav_to_pcm(wav, audio.SPEAKER_RATE)
            produced += len(pcm)
            for piece in audio.chunk(pcm):
                yield piece

        # A system voice given a script it does not cover returns SUCCESS and no
        # audio. Measured on an ordinary Windows install: the two voices present
        # were English, and a Cyrillic sentence produced exactly zero bytes with
        # no error anywhere. Silence that reports success is the worst failure
        # mode there is — the assistant looks like it ignored the user, and the
        # obvious suspicion falls on the speakers or the microphone.
        #
        # So it is turned into something that can be said out loud, and the
        # message names the script the text is written in, because "install a
        # voice" is useless advice without knowing WHICH language.
        if sentences and produced == 0:
            script = _script_of(text)
            raise RuntimeError(
                f"{self._engine or 'the speech engine'} produced no audio for "
                f"that text — the installed voice "
                f"({self.voice or 'system default'}) does not cover "
                f"{script} script. Install a voice for that language in your "
                f"system settings, or use a downloadable or hosted voice."
            )

    def probe(self) -> tuple[bool, str]:
        if not self._engine:
            return False, ("No speech engine was found on this system — "
                           "SAPI 5 needs either the comtypes package or "
                           "Windows PowerShell.")
        try:
            wav = self._synth_blocking("ok")
            pcm = audio.wav_to_pcm(wav, audio.SPEAKER_RATE)
        except Exception as e:
            return False, str(e)[:120]
        if not pcm:
            return False, f"{self._engine} produced no audio."
        n = len(list_voices())
        return True, (f"{self._engine}, {n} voice{'s' if n != 1 else ''} installed "
                      f"({audio.duration_s(pcm, audio.SPEAKER_RATE):.2f}s of test audio)")


def _split(text: str, max_chars: int = 240) -> list[str]:
    """Break an answer into speakable pieces.

    Punctuation only. A very long sentence with no punctuation in it — which is
    a whole class of perfectly normal writing in several languages — is cut on
    whitespace so it does not become one enormous synthesis call.
    """
    text = (text or "").strip()
    if not text:
        return []
    pieces = []
    for part in _SENTENCE_END.split(text):
        part = part.strip()
        if not part:
            continue
        while len(part) > max_chars:
            cut = part.rfind(" ", 0, max_chars)
            if cut <= 0:
                cut = max_chars
            pieces.append(part[:cut].strip())
            part = part[cut:].strip()
        if part:
            pieces.append(part)
    return pieces


def _make(cfg: dict) -> SystemTTS:
    return SystemTTS(cfg)


PROVIDER = {
    "id": "system_tts",
    "kind": "tts",
    "label": "System voice",
    "hint": "The voices already installed on this computer — nothing to download",
    "requires": (),
    "catalog": (
        {"id": "system", "label": "This computer's own voices",
         "note": "free · instant · nothing to download", "local": True,
         "values": {}},
    ),
    "needs": (
        Field("voice", "Voice", kind="choice", choices=(), default="",
              required=False,
              hint="Empty uses the system default; the list is read from this "
                   "machine"),
        Field("rate", "Speed", kind="number", default=0, required=False,
              advanced=True,
              hint="-10 to 10, 0 is normal"),
    ),
    "caps": {"audio_out": True},
    "factory": _make,
}


def available() -> bool:
    return bool(engine_name())


def choices_for(field_key: str) -> tuple:
    """Live values for a `choices` field the panel cannot know in advance.

    NEVER blocks. Reading the voices costs a COM call, and this is called while
    a form is being drawn; waiting for it here is what made the panel take two
    seconds to open. Before the cache is warm this answers with nothing,
    `prefetch()` fills it in the background, and the panel redraws once it
    arrives. The voice already saved is still shown, because the form adds the
    stored value to the list whether or not it was offered."""
    if field_key != "voice":
        return ()
    with _voice_lock:
        return tuple(v.name for v in _voice_cache) if _voice_cache is not None else ()
