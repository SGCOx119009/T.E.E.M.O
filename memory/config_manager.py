import functools
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR    = get_base_dir()
CONFIG_DIR  = BASE_DIR / "config"
CONFIG_FILE = CONFIG_DIR / "api_keys.json"

# The secure store is the Windows Credential Locker (via `keyring`). The service
# name is the product's own identity — never a predecessor's — so the entries in
# the vault belong to T.E.E.M.O. and to nothing else.
_KEYRING_SERVICE = "T.E.E.M.O."
_KEYRING_ACCOUNT = "gemini_api_key"

def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

def config_exists() -> bool:
    return CONFIG_FILE.exists()


# ── Reading and writing the file ─────────────────────────────────────────────
# Many threads use this file at once: the interface, the conversation loop,
# plugins polling a server, background tasks. Every setter read the file,
# changed one key and rewrote it IN PLACE, which failed two ways:
#
#   * a reader that arrived mid-write saw half a file, parsed nothing, and
#     treated every setting as unset — wake word off, no key, Gemini mode;
#   * a SETTER that arrived mid-write did the same, then wrote its one key into
#     an otherwise empty dict — erasing every other setting for good.
#
# Writes now go to a temporary file that replaces the real one in a single step,
# so a reader sees the old file or the new one and never half of either, and
# setters in this process take turns.
_lock = threading.RLock()


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _lock:
            return fn(*args, **kwargs)
    return wrapper


def _write_config(data: dict) -> None:
    ensure_config_dir()
    tmp = CONFIG_FILE.with_name(CONFIG_FILE.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=4), encoding="utf-8")
    for attempt in range(20):
        try:
            os.replace(tmp, CONFIG_FILE)
            return
        except PermissionError:
            # Windows: an antivirus scan or a reader can hold the file for a
            # moment. Worth a short wait, never a lost setting.
            if attempt == 19:
                raise
            time.sleep(0.05)


def _read_for_write() -> dict:
    """The current settings, for a setter to change one of them.

    A file that exists but cannot be parsed is set aside as
    api_keys.json.broken-<time> rather than silently replaced by one key — it
    may be a hand edit with a missing comma, and it still holds the user's key."""
    if not CONFIG_FILE.exists():
        return {}
    last = None
    for _ in range(3):
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError) as e:
            last = e
            time.sleep(0.05)
    backup = CONFIG_FILE.with_name(f"{CONFIG_FILE.name}.broken-{int(time.time())}")
    try:
        os.replace(CONFIG_FILE, backup)
        print(f"[Config] api_keys.json could not be read ({last}) - kept as {backup.name}")
    except Exception:
        pass
    return {}


# ── Secure credential storage ───────────────────────────────────────────────
# A key typed into the settings panel is a long-lived secret. In a JSON file
# next to the application it is one backup, one sync folder or one stray
# screenshot away from being exposed. The Credential Locker is what Windows
# offers for this, and `keyring` is how Python reaches it.
#
# There is NO plaintext fallback. If the vault is unavailable, the honest answer
# is that the credential cannot be stored — not that it can be stored somewhere
# less safe and mentioned in a log.
_CREDENTIAL_LOCK = threading.RLock()


def _secure_keyring():
    """Return a credential backend that meets this platform's storage policy."""
    try:
        import keyring

        backend = keyring.get_keyring()
    except Exception as exc:
        raise RuntimeError(
            f"Secure credential storage is unavailable ({type(exc).__name__})."
        ) from exc

    backend_type = type(backend)
    if backend_type.__name__.casefold().startswith("plaintext"):
        raise RuntimeError("Plaintext credential backends are not supported.")
    if backend_type.__name__ != "WinVaultKeyring":
        raise RuntimeError(
            "Windows credential storage requires the Windows Credential Locker."
        )
    priority = getattr(backend, "priority", None)
    if not isinstance(priority, (int, float)) or priority < 0:
        raise RuntimeError("The configured secure credential backend is unavailable.")
    return keyring


def _read_secure_secret(account: str) -> str | None:
    keyring = _secure_keyring()
    try:
        return keyring.get_password(_KEYRING_SERVICE, account)
    except Exception as exc:
        raise RuntimeError(
            f"Secure credential lookup failed ({type(exc).__name__})."
        ) from exc


def _store_secure_secret(account: str, secret: str) -> None:
    keyring = _secure_keyring()
    try:
        keyring.set_password(_KEYRING_SERVICE, account, secret)
    except Exception as exc:
        raise RuntimeError(
            f"Secure credential storage failed ({type(exc).__name__})."
        ) from exc


def _remove_secure_secret(account: str) -> None:
    keyring = _secure_keyring()
    try:
        keyring.delete_password(_KEYRING_SERVICE, account)
    except Exception as exc:
        raise RuntimeError(
            f"Secure credential removal failed ({type(exc).__name__})."
        ) from exc


def _read_secure_key() -> str | None:
    return _read_secure_secret(_KEYRING_ACCOUNT)


def _store_secure_key(key: str) -> None:
    _store_secure_secret(_KEYRING_ACCOUNT, key)


def _remove_secure_key() -> None:
    _remove_secure_secret(_KEYRING_ACCOUNT)


def _restore_secure_secret(account: str, previous: str | None) -> None:
    """Put the secure store back the way it was, after a failed write."""
    if previous:
        _store_secure_secret(account, previous)
        return
    current = _read_secure_secret(account)
    if current:
        _remove_secure_secret(account)


def _invalidate_gemini_key_cache() -> None:
    """A newly stored key must reach the live client, not the cached one."""
    module = sys.modules.get("core.gemini")
    invalidate = getattr(module, "invalidate_api_key_cache", None)
    if callable(invalidate):
        invalidate()


def _write_api_config(data: dict) -> None:
    """Write the configuration file atomically.

    The credential path uses this rather than `_write_config` because a failed
    write here has to leave the previous file AND the previous credential
    intact — the two are restored together, never one without the other.
    """
    ensure_config_dir()
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=CONFIG_DIR,
            prefix=".api_keys.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(json.dumps(data, indent=2))
            temp_file.flush()
            os.fsync(temp_file.fileno())
        for attempt in range(20):
            try:
                os.replace(temp_path, CONFIG_FILE)
                return
            except PermissionError:
                # Windows: an antivirus scan or a reader can hold the file for
                # a moment. Worth a short wait, never a lost setting.
                if attempt == 19:
                    raise
                time.sleep(0.05)
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def _read_api_config_for_update() -> dict:
    """The configuration file, read strictly for a credential operation.

    Unlike `_read_for_write` this does NOT repair a damaged file by replacing
    it: a file that cannot be parsed may still hold the user's key, and the
    honest response is to say so.
    """
    if not CONFIG_FILE.exists():
        return {}
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("The API configuration file is unreadable.") from exc
    if not isinstance(data, dict):
        raise ValueError("The API configuration must contain a JSON object.")
    return data


def _provider_key_account(provider_id: str) -> str:
    provider = str(provider_id or "").strip()
    if not provider or len(provider) > 128 or not all(
        char.isalnum() or char in "._-@" for char in provider
    ):
        raise ValueError("Provider identifier is invalid.")
    return f"provider:{provider}:api_key"


def _migrate_provider_keys(data: dict) -> dict:
    """Move provider API keys out of the JSON file and into the secure store."""
    providers = data.get("providers")
    if not isinstance(providers, dict):
        return {}
    creds = providers.get("creds")
    if not isinstance(creds, dict):
        return dict(providers)

    updated_providers = dict(providers)
    updated_creds = dict(creds)
    staged: list[tuple[str, str]] = []
    changed = False
    try:
        for provider_id, values in creds.items():
            if not isinstance(values, dict) or "api_key" not in values:
                continue
            account = _provider_key_account(provider_id)
            legacy_key = values.get("api_key")
            if legacy_key is not None and not isinstance(legacy_key, str):
                raise ValueError("A provider API key must be text.")
            stored_key = _read_secure_secret(account)
            if not stored_key and legacy_key:
                staged.append((account, legacy_key))
                _store_secure_secret(account, legacy_key)
            clean_values = dict(values)
            clean_values.pop("api_key", None)
            updated_creds[provider_id] = clean_values
            changed = True

        if changed:
            updated_providers["creds"] = updated_creds
            updated_data = dict(data)
            updated_data["providers"] = updated_providers
            _write_api_config(updated_data)
    except Exception as exc:
        rollback_errors = []
        for account, secret in reversed(staged):
            try:
                if _read_secure_secret(account) == secret:
                    _remove_secure_secret(account)
            except Exception as rollback_exc:
                rollback_errors.append(type(rollback_exc).__name__)
        if rollback_errors:
            raise RuntimeError(
                "Provider credential migration failed and secure-store "
                f"rollback was incomplete ({', '.join(rollback_errors)})."
            ) from exc
        if isinstance(exc, ValueError):
            raise
        raise RuntimeError(
            "Provider credential migration failed; newly stored credentials "
            "were rolled back and legacy values remain."
        ) from exc
    return updated_providers


def save_api_keys(gemini_api_key: str) -> None:
    """Store the Gemini key in the Credential Locker, never in the file."""
    if not isinstance(gemini_api_key, str):
        raise ValueError("Gemini API key must be text.")
    key = gemini_api_key.strip()
    if not key:
        raise ValueError("Gemini API key cannot be empty")

    with _CREDENTIAL_LOCK:
        data = _read_api_config_for_update()
        previous = _read_secure_key()
        _store_secure_key(key)
        data.pop("gemini_api_key", None)
        if key:
            data.pop("local_only", None)  # a key given is a choice to use Gemini again
        try:
            _write_api_config(data)
        except Exception as exc:
            try:
                _restore_secure_secret(_KEYRING_ACCOUNT, previous)
            except Exception as rollback_exc:
                raise RuntimeError(
                    "Configuration could not be updated and the previous "
                    "credential could not be restored "
                    f"({type(rollback_exc).__name__})."
                ) from exc
            raise RuntimeError(
                "Configuration could not be updated; the previous credential "
                "was restored."
            ) from exc
        _invalidate_gemini_key_cache()


def load_api_keys() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    for attempt in range(3):
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as e:
            if attempt == 2:
                # Plain text: an emoji here raised UnicodeEncodeError on a
                # Windows console, turning a failed read into a crash.
                print(f"[Config] Failed to load api_keys.json: {e}")
                return {}
            time.sleep(0.05)
    return {}


def get_gemini_key() -> str | None:
    """The Gemini key, from the Credential Locker.

    A key left in the file by an older version is moved into the vault the
    first time it is read, and removed from the file — a migration that can
    fail loudly rather than quietly leaving a secret in plain text.
    """
    with _CREDENTIAL_LOCK:
        secure_key = _read_secure_key()
        data = _read_api_config_for_update()
        legacy_key = data.get("gemini_api_key")
        if legacy_key is not None and not isinstance(legacy_key, str):
            raise ValueError("The legacy Gemini API key must be text.")

        if secure_key:
            if legacy_key is not None:
                data.pop("gemini_api_key", None)
                try:
                    _write_api_config(data)
                except Exception as exc:
                    raise RuntimeError(
                        "A stale plaintext Gemini credential could not be "
                        "removed from the configuration file."
                    ) from exc
            return secure_key
        if not legacy_key:
            return None

        _store_secure_key(legacy_key)
        data.pop("gemini_api_key", None)
        try:
            _write_api_config(data)
        except Exception as exc:
            try:
                if _read_secure_key() == legacy_key:
                    _remove_secure_key()
            except Exception as rollback_exc:
                raise RuntimeError(
                    "Legacy credential cleanup failed and secure-store "
                    f"rollback was incomplete ({type(rollback_exc).__name__})."
                ) from exc
            raise RuntimeError(
                "Legacy credential cleanup failed; the newly stored key was "
                "rolled back and the legacy copy remains."
            ) from exc
        return legacy_key


def delete_gemini_key() -> bool:
    """Remove the Gemini key from the vault and from the legacy file copy."""
    with _CREDENTIAL_LOCK:
        data = _read_api_config_for_update()
        removed = "gemini_api_key" in data
        secure_key = _read_secure_key()
        if removed:
            data.pop("gemini_api_key", None)
            _write_api_config(data)
        if secure_key:
            _remove_secure_key()
            removed = True
        if removed:
            _invalidate_gemini_key_cache()
        return removed


def is_configured() -> bool:
    key = get_gemini_key()
    return bool(key and len(key) > 15)


def get_assistant_name() -> str:
    """Return the configured assistant name, or T.E.E.M.O. if not set."""
    return load_api_keys().get("assistant_name", "T.E.E.M.O.") or "T.E.E.M.O."


def get_user_name() -> str:
    """Return the configured user name for addressing."""
    return load_api_keys().get("user_name", "")


@_locked
def save_assistant_config(assistant_name: str, user_name: str) -> None:
    """Persist assistant name and user name to config."""
    ensure_config_dir()
    data = _read_for_write()
    data["assistant_name"] = assistant_name.strip() or "T.E.E.M.O."
    data["user_name"] = user_name.strip()
    _write_config(data)


# ── Assistant voice ──────────────────────────────────────────────────────────
# Gemini Live prebuilt voices. Names are proper nouns — identical in every
# language, so this list is safe to show verbatim in any locale.
AVAILABLE_VOICES = ["Charon", "Puck", "Kore", "Fenrir", "Aoede"]
DEFAULT_VOICE    = "Charon"


def get_voice() -> str:
    """Return the configured Live voice, falling back to the default if unset
    or if the stored value is not a voice we recognise."""
    v = load_api_keys().get("voice_name", DEFAULT_VOICE) or DEFAULT_VOICE
    return v if v in AVAILABLE_VOICES else DEFAULT_VOICE


@_locked
def save_voice(voice_name: str) -> None:
    """Persist the chosen Live voice. Unknown names collapse to the default so a
    bad value can never reach the API and break the session."""
    ensure_config_dir()
    data = _read_for_write()
    v = (voice_name or "").strip()
    data["voice_name"] = v if v in AVAILABLE_VOICES else DEFAULT_VOICE
    _write_config(data)


def get_wake_word_enabled() -> bool:
    """Whether local wake-word gating is on (the assistant sleeps until called)."""
    return load_api_keys().get("wake_word_enabled", False)


@_locked
def save_wake_word_enabled(enabled: bool) -> None:
    ensure_config_dir()
    data = _read_for_write()
    data["wake_word_enabled"] = bool(enabled)
    _write_config(data)


# ── Wake phrase ─────────────────────────────────────────────────────────────
# The words the assistant answers to. It is ours, and it is configurable: the
# default lives in core.wake_word.DEFAULT_WAKE_PHRASE and is imported rather
# than repeated, so there is exactly one place that says what the phrase is.
DEFAULT_WAKE_PHRASE = "Hey T.E.E.M.O"


def get_wake_phrase() -> str:
    """The configured wake phrase, or the default when it is unset.

    An empty or whitespace-only value falls back to the default: a wake
    phrase made of spaces would silently disable the feature while leaving
    the button looking enabled, which is the kind of silence nobody can
    debug.
    """
    try:
        from core.wake_word import DEFAULT_WAKE_PHRASE as _default
    except Exception:
        _default = DEFAULT_WAKE_PHRASE
    value = str(load_api_keys().get("wake_phrase") or "").strip()
    return value or _default


@_locked
def save_wake_phrase(phrase: str) -> None:
    """Persist the wake phrase. An empty value restores the default."""
    ensure_config_dir()
    data = _read_for_write()
    value = (phrase or "").strip()
    if value:
        data["wake_phrase"] = value
    else:
        data.pop("wake_phrase", None)
    _write_config(data)


def get_wake_model() -> str:
    """Path of a custom wake model file, or "" to use the pretrained default.

    The pretrained model was trained for a phrase that is no longer this
    product's name; a model trained for the configured phrase can be dropped
    in here and is loaded exactly as the default one is.
    """
    return str(load_api_keys().get("wake_model") or "").strip()


@_locked
def save_wake_model(path: str) -> None:
    """Persist (or clear) the path of a custom wake model."""
    ensure_config_dir()
    data = _read_for_write()
    value = (path or "").strip()
    if value:
        data["wake_model"] = value
    else:
        data.pop("wake_model", None)
    _write_config(data)


def get_wake_mode() -> str:
    """How the assistant listens for the person: a phrase, or any speech.

    "voice" is the default. The phrase model that ships inside openwakeword
    was trained for a phrase this product no longer uses, so a person who
    says the product's own name into the microphone hears nothing happen —
    and repeating it does not help, because the model cannot hear a phrase
    it was never trained on. Voice mode has no such blind spot: any speech
    wakes the assistant, which is what the owner of this machine asked for.

    "phrase" listens for the configured wake phrase and needs a model
    trained for it.
    """
    value = str(load_api_keys().get("wake_mode") or "").strip().lower()
    return value if value in ("phrase", "voice") else "voice"


@_locked
def save_wake_mode(mode: str) -> None:
    """Persist how the assistant listens. An unknown value restores the default."""
    ensure_config_dir()
    value = str(mode or "").strip().lower()
    _save_flag("wake_mode", value if value in ("phrase", "voice") else "voice")


def get_push_to_talk_enabled() -> bool:
    """Hold-a-key-to-speak. When on, the mic is closed unless the chord is held."""
    return load_api_keys().get("push_to_talk_enabled", False)


def save_push_to_talk_enabled(enabled: bool) -> None:
    _save_flag("push_to_talk_enabled", enabled)


HUD_STYLES = ("face", "core")


def get_hud_style() -> str:
    """Which centrepiece the HUD draws: the animated head, or the reactor core.

    Taste, not capability — both render in the same software painter and cost
    about the same. Defaults to the head because that is what the earlier chassis shipped
    with; anyone who preferred the older look can switch back in ⚙ and the
    choice survives a restart.
    """
    v = str(load_api_keys().get("hud_style", "face")).strip().lower()
    return v if v in HUD_STYLES else "face"


def save_hud_style(style: str) -> None:
    s = str(style or "").strip().lower()
    _save_flag("hud_style", s if s in HUD_STYLES else "face")


def get_ui_scale() -> float:
    """How much larger than designed the whole interface is drawn, or 0 for
    'work it out from the screen'.

    Read once, before the window exists — Qt only takes a scale factor at
    start-up, so a change here applies on the next launch."""
    try:
        v = float(load_api_keys().get("ui_scale", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    return min(v, 3.0) if v >= 0.5 else 0.0


def save_ui_scale(scale: float) -> None:
    _save_flag("ui_scale", float(scale or 0))


# ── Live-session tuning ──────────────────────────────────────────────────────
# Everything here is optional and has a working default, so an untouched
# config behaves exactly like a configured one. Each value is also a way out:
# if a future model dislikes one of these, set it back and nothing else changes.

def get_thinking_enabled() -> bool:
    """Whether the Live model may spend tokens thinking before it answers.

    Off by default. A voice assistant is judged on how fast it starts talking,
    and the reasoning that actually needs deliberation in this app is delegated
    to the planning tools, which run on a separate non-Live model.
    """
    return bool(load_api_keys().get("thinking_enabled", False))


def save_thinking_enabled(enabled: bool) -> None:
    _save_flag("thinking_enabled", enabled)


def get_turn_tuning() -> dict:
    """How eagerly the server decides you have stopped speaking.

    OFF by default, and that default was earned. Cutting turns shorter looks
    like a free speed win and is not: proactive audio has to judge whether an
    utterance was even addressed to the assistant, and a turn clipped early
    gives it less to judge, so it stays quiet — and the reply to your first
    sentence only arrives once your second one has given it enough context.
    That reads as the assistant being a turn behind, which is far worse than
    the fraction of a second the tuning saves.

    Turn it on with "turn_tuning": {"enabled": true} if your own microphone and
    speaking pace suit it. `silence_ms` is the one that is felt: the pause the
    server sits through before accepting your turn is over.
    """
    cfg = load_api_keys().get("turn_tuning")
    cfg = cfg if isinstance(cfg, dict) else {}

    def _int(key, default, lo, hi):
        try:
            return max(lo, min(hi, int(cfg.get(key, default))))
        except (TypeError, ValueError):
            return default

    return {
        "enabled":    bool(cfg.get("enabled", False)),
        "silence_ms": _int("silence_ms", 550, 200, 3000),
        "prefix_ms":  _int("prefix_ms", 150, 0, 1000),
        # "high" = quicker to decide speech has ended.
        "end_sensitivity":   str(cfg.get("end_sensitivity", "high")).lower(),
        "start_sensitivity": str(cfg.get("start_sensitivity", "default")).lower(),
    }


@_locked
def save_turn_tuning(values: dict) -> None:
    ensure_config_dir()
    data = _read_for_write()
    cur = data.get("turn_tuning")
    cur = dict(cur) if isinstance(cur, dict) else {}
    cur.update(values or {})
    data["turn_tuning"] = cur
    _write_config(data)


def get_proactive_audio_enabled() -> bool:
    """Whether the model gets to decide an utterance was not aimed at it and
    stay quiet.

    On by default — it is what stops the assistant answering the room. But it
    is also the first thing to switch off if replies ever seem to arrive a turn
    late: what looks like lag is usually the model having judged your previous
    sentence as not addressed to it, and only changing its mind once the next
    one arrives.
    """
    return bool(load_api_keys().get("proactive_audio", True))


def save_proactive_audio_enabled(enabled: bool) -> None:
    _save_flag("proactive_audio", enabled)


MEDIA_RESOLUTIONS = ("default", "low", "medium", "high")


def get_media_resolution() -> str:
    """How finely the model tokenises the screenshots and camera frames it is
    sent. 'medium' keeps on-screen text readable at a fraction of the tokens a
    full-resolution frame costs; 'low' is cheaper still but starts losing small
    text, which is most of what screen captures are for."""
    v = str(load_api_keys().get("media_resolution", "medium")).strip().lower()
    return v if v in MEDIA_RESOLUTIONS else "medium"


def save_media_resolution(value: str) -> None:
    v = str(value or "").strip().lower()
    _save_flag("media_resolution", v if v in MEDIA_RESOLUTIONS else "medium")


@_locked
def _save_flag(key: str, value) -> None:
    """Read-modify-write one key without disturbing the rest of the config."""
    ensure_config_dir()
    data = _read_for_write()
    data[key] = bool(value) if isinstance(value, bool) else value
    _write_config(data)


def get_brief_enabled() -> bool:
    return load_api_keys().get("morning_brief_enabled", True)


def get_grounding_vlm_enabled() -> bool:
    """May the grounding model (level N2) be used?

    OFF by default. It is the only grounding level that spends tokens and
    the only one that reads pixels as if they were instructions, so turning
    it on is the user's decision and not a side effect of installing
    something.
    """
    return bool(load_api_keys().get("grounding_vlm_enabled", False))


@_locked
def save_grounding_vlm_enabled(enabled: bool) -> None:
    ensure_config_dir()
    _save_flag("grounding_vlm_enabled", bool(enabled))


@_locked
def save_brief_enabled(enabled: bool) -> None:
    ensure_config_dir()
    data = _read_for_write()
    data["morning_brief_enabled"] = enabled
    _write_config(data)


# ── Audio devices ────────────────────────────────────────────────────────────
# Stored as device NAMES, not sounddevice indices. Indices shift every time a
# USB device is plugged in or removed, so a saved index silently starts pointing
# at a different microphone. The empty string means "system default", which is
# both the factory setting and what an unresolvable saved device falls back to —
# so unplugging a headset degrades to the built-in speakers instead of crashing.

@_locked
def _patch_config(**fields) -> None:
    """Read-modify-write one or more keys in api_keys.json.

    Every setter in this file open-coded this. Collapsing it here means a new
    setting is one line, and there is one place where a corrupt config file is
    handled instead of nine."""
    ensure_config_dir()
    data = _read_for_write()
    data.update(fields)
    _write_config(data)


def update_config(**fields) -> None:
    """Set one or more top-level settings, leaving every other one as it is.
    For code outside this file that used to rewrite api_keys.json itself."""
    _patch_config(**fields)


def get_input_device() -> str:
    """Microphone device name, or '' for the system default."""
    return (load_api_keys().get("input_device", "") or "").strip()


def save_input_device(name: str) -> None:
    _patch_config(input_device=(name or "").strip())


def get_output_device() -> str:
    """Speaker device name, or '' for the system default."""
    return (load_api_keys().get("output_device", "") or "").strip()


def save_output_device(name: str) -> None:
    _patch_config(output_device=(name or "").strip())


def get_plugin_enabled(plugin_name: str) -> bool:
    """Plugins are enabled by default the moment they're discovered (opt-out model)."""
    return load_api_keys().get("plugins_enabled", {}).get(plugin_name, True)


# ── Per-plugin settings ("tokens" / connection details) ───────────────────────
# Generic store so a plugin can declare its own config fields (PLUGIN_SETTINGS)
# and the settings UI renders + persists them WITHOUT any core edit — keeping the
# drop-in model intact. Values live under plugin_config[<namespace>][<key>].
# A namespace defaults to the plugin name, but a suite of plugins (e.g. the
# several printer plugins) can share ONE namespace.
#
# A field whose NAME or declared TYPE says it is a secret (password, token,
# api_key, private_key...) does not live in this file at all: it goes to the
# Credential Locker, and only the fact that it exists is remembered here.
def _plugin_secret_account(namespace: str, key: str) -> str:
    if not isinstance(namespace, str) or not namespace.strip() or len(namespace) > 256:
        raise ValueError("Plugin settings namespace is invalid.")
    if not isinstance(key, str) or not key.strip() or len(key) > 128:
        raise ValueError("Plugin secret field is invalid.")
    value = f"{namespace}\0{key}".encode("utf-8")
    return "plugin:" + hashlib.sha256(value).hexdigest()


def _normalize_plugin_secret_keys(secret_keys) -> set[str]:
    if secret_keys is None:
        return set()
    if isinstance(secret_keys, (str, bytes)):
        raise ValueError("Plugin secret fields must be a sequence of names.")
    keys = set()
    for key in secret_keys:
        if not isinstance(key, str) or not key.strip() or len(key) > 128:
            raise ValueError("Plugin secret field is invalid.")
        keys.add(key)
    return keys


_SECRET_FIELD_MARKERS = (
    "password", "passwd", "secret", "token", "credential", "api_key",
    "authorization", "private_key", "otp",
)


def is_plugin_secret_field(key: object, field_type: object = None) -> bool:
    """Whether a plugin setting's key or declared type identifies a secret."""
    for candidate in (key, field_type):
        if not isinstance(candidate, str):
            continue
        normalized = candidate.casefold().replace("-", "_")
        if any(marker in normalized for marker in _SECRET_FIELD_MARKERS):
            return True
    return False


def _looks_like_secret_field(key: object) -> bool:
    return is_plugin_secret_field(key)


def _load_plugin_config(namespace: str, secret_keys=()) -> dict:
    """Read a namespace's settings, moving any secret field into the vault."""
    with _CREDENTIAL_LOCK:
        data = _read_api_config_for_update()
        stored = data.get("plugin_config")
        stored = stored if isinstance(stored, dict) else {}
        values = stored.get(namespace)
        values = dict(values) if isinstance(values, dict) else {}

        secret_fields = data.get("plugin_secret_fields")
        secret_fields = secret_fields if isinstance(secret_fields, dict) else {}
        known = secret_fields.get(namespace, [])
        known = set(known) if isinstance(known, list) else set()
        requested = _normalize_plugin_secret_keys(secret_keys)
        inferred = {
            key for key, value in values.items()
            if _looks_like_secret_field(key)
            and (isinstance(value, str) or value is None)
        }
        all_secrets = known | requested | inferred
        staged: list[tuple[str, str]] = []
        changed = all_secrets != known and bool(requested)

        try:
            for key in all_secrets:
                account = _plugin_secret_account(namespace, key)
                legacy = values.pop(key, None)
                if legacy is not None and not isinstance(legacy, str):
                    raise ValueError("A plugin secret must be text.")
                secret = _read_secure_secret(account)
                if not secret and legacy:
                    staged.append((account, legacy))
                    _store_secure_secret(account, legacy)
                    secret = legacy
                if secret:
                    values[key] = secret
                if legacy is not None:
                    changed = True

            if changed:
                saved_values = dict(values)
                for key in all_secrets:
                    saved_values.pop(key, None)
                updated_stored = dict(stored)
                updated_stored[namespace] = saved_values
                updated_fields = dict(secret_fields)
                updated_fields[namespace] = sorted(all_secrets)
                data["plugin_config"] = updated_stored
                data["plugin_secret_fields"] = updated_fields
                _write_api_config(data)
        except Exception as exc:
            rollback_errors = []
            for account, secret in reversed(staged):
                try:
                    if _read_secure_secret(account) == secret:
                        _remove_secure_secret(account)
                except Exception as rollback_exc:
                    rollback_errors.append(type(rollback_exc).__name__)
            if rollback_errors:
                raise RuntimeError(
                    "Plugin credential migration failed and secure-store "
                    f"rollback was incomplete ({', '.join(rollback_errors)})."
                ) from exc
            if isinstance(exc, ValueError):
                raise
            raise RuntimeError(
                "Plugin credential migration failed; newly stored credentials "
                "were rolled back and legacy values remain."
            ) from exc
        return values


def get_plugin_config(namespace: str, secret_keys=()) -> dict:
    """All stored values for a namespace (empty dict if none set yet)."""
    return _load_plugin_config(namespace, secret_keys)


def get_plugin_setting(namespace: str, key: str, default=None):
    """A single value from a namespace, or `default` if unset."""
    return get_plugin_config(namespace).get(key, default)


# ── Browser allow list ───────────────────────────────────────────────────────
# The domains the browser operator is allowed to reach. Empty means the user
# has not restricted anything — which is NOT the same as "nothing is
# allowed": the deny list in core/policy.py is in force either way. The
# allow list is an opt-in TIGHTENING, never a way to reach a blocked domain.
def get_browser_allowlist() -> list[str]:
    """The domains the user has authorised the browser to reach."""
    raw = load_api_keys().get("browser_allowlist", [])
    if isinstance(raw, str):
        raw = [part for part in re.split(r"[,\s;]+", raw) if part]
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        domain = str(item or "").strip().lower().rstrip(".")
        if domain and domain not in out:
            out.append(domain)
    return out


def save_browser_allowlist(domains: list[str] | str) -> None:
    """Store the authorised domains. An empty list clears the restriction."""
    if isinstance(domains, str):
        domains = [part for part in re.split(r"[,\s;]+", domains) if part]
    cleaned: list[str] = []
    for item in (domains or []):
        domain = str(item or "").strip().lower().rstrip(".")
        if domain and domain not in cleaned:
            cleaned.append(domain)
    _patch_config(browser_allowlist=cleaned)


def save_plugin_config(namespace: str, values: dict, secret_keys=()) -> None:
    """Merge `values` into a namespace's stored config (read-modify-write, like
    every other helper here). Only the provided keys are touched."""
    with _CREDENTIAL_LOCK:
        secret_fields = _normalize_plugin_secret_keys(secret_keys)
        updates = dict(values or {})
        inferred = {
            key for key, value in updates.items()
            if _looks_like_secret_field(key)
            and (isinstance(value, str) or value is None)
        }
        secret_fields |= inferred
        _load_plugin_config(namespace, secret_fields)
        data = _read_api_config_for_update()
        pc = data.get("plugin_config")
        pc = dict(pc) if isinstance(pc, dict) else {}
        cur = pc.get(namespace)
        cur = dict(cur) if isinstance(cur, dict) else {}
        fields = data.get("plugin_secret_fields")
        fields = dict(fields) if isinstance(fields, dict) else {}
        known = fields.get(namespace, [])
        known = set(known) if isinstance(known, list) else set()
        all_secrets = known | secret_fields
        staged: list[tuple[str, str | None]] = []
        removals: list[str] = []

        try:
            for key in all_secrets:
                if key not in updates:
                    continue
                new_secret = updates.pop(key)
                if new_secret is None:
                    new_secret = ""
                if not isinstance(new_secret, str):
                    raise ValueError("A plugin secret must be text.")
                new_secret = new_secret.strip()
                account = _plugin_secret_account(namespace, key)
                previous = _read_secure_secret(account)
                if new_secret:
                    if new_secret != previous:
                        staged.append((account, previous))
                        _store_secure_secret(account, new_secret)
                elif previous:
                    removals.append(account)

            cur.update(updates)
            for key in all_secrets:
                cur.pop(key, None)
            pc[namespace] = cur
            fields[namespace] = sorted(all_secrets)
            data["plugin_config"] = pc
            data["plugin_secret_fields"] = fields
            _write_api_config(data)
        except Exception as exc:
            rollback_errors = []
            for account, previous in reversed(staged):
                try:
                    _restore_secure_secret(account, previous)
                except Exception as rollback_exc:
                    rollback_errors.append(type(rollback_exc).__name__)
            if rollback_errors:
                raise RuntimeError(
                    "Plugin configuration failed and credential rollback "
                    f"was incomplete ({', '.join(rollback_errors)})."
                ) from exc
            if isinstance(exc, ValueError):
                raise
            raise RuntimeError(
                "Plugin configuration failed; prior credentials were restored."
            ) from exc

        for account in removals:
            if _read_secure_secret(account):
                _remove_secure_secret(account)


@_locked
def save_plugin_enabled(plugin_name: str, enabled: bool) -> None:
    ensure_config_dir()
    data = _read_for_write()
    plugins_cfg = data.get("plugins_enabled")
    if not isinstance(plugins_cfg, dict):
        plugins_cfg = {}
    plugins_cfg[plugin_name] = enabled
    data["plugins_enabled"] = plugins_cfg
    _write_config(data)

# ── Providers: local STT/LLM/TTS and non-Gemini APIs ─────────────────────────
# Stored here rather than in a file of their own, for the same reason plugin
# settings are: one config to back up, one already in .gitignore, one place a
# user knows to look.
#
# The important property is what happens when nobody has configured anything.
# `get_provider_mode()` returns "gemini" on a config that has never heard of
# providers, and every caller checks that BEFORE importing the providers
# package — so an ordinary install never executes a line of it. This is the
# whole basis of the claim that the folder costs nothing when unused.
PROVIDER_MODE_GEMINI = "gemini"      # the conversation runs on the Live API
PROVIDER_MODE_PIPELINE = "pipeline"  # STT -> LLM -> TTS, assembled from providers


def _upgrade_ids(block: dict) -> dict:
    """Give every catalog service its own id and its own stored settings.

    One provider serves Ollama, OpenAI, MiniMax and a dozen more, and they used
    to share ONE block of settings under "openai_compat" — so the fallback and
    ② THINKING could not be two different services, picking one overwrote the
    other's address, and an API key typed for MiniMax was sent to OpenAI after
    a switch. Each service is now "<provider>@<entry>", e.g.
    "openai_compat@minimax", with settings of its own.

    An older config is converted as it is read, not by a migration step: the
    shared block moves to the service its "catalog" key names (its key belonged
    to that service and nothing else), and every reference to it follows.
    Writing the providers block back persists it; until then nothing on disk
    changes."""
    creds = block.get("creds")
    if not isinstance(creds, dict):
        return block
    creds = dict(creds)
    moved: dict = {}
    for pid, cfg in list(creds.items()):
        if "@" in pid or not isinstance(cfg, dict):
            continue
        eid = str(cfg.get("catalog") or "").strip()
        if not eid:
            continue
        new = f"{pid}@{eid}"
        creds.setdefault(new, dict(cfg))
        del creds[pid]
        moved[pid] = new
    if not moved:
        return block
    block = dict(block)
    block["creds"] = creds
    pipe = block.get("pipeline")
    if isinstance(pipe, dict):
        block["pipeline"] = {k: moved.get(v, v) for k, v in pipe.items()}
    for key in ("sidecar", "coding"):
        if block.get(key) in moved:
            block[key] = moved[block[key]]
    return block


def get_providers() -> dict:
    """The whole providers block, or an empty dict.

    Reading it also migrates any provider key still sitting in the JSON file
    into the Credential Locker, and removes it from the file.
    """
    with _CREDENTIAL_LOCK:
        data = _read_api_config_for_update()
        migrated = _migrate_provider_keys(data)
        return _upgrade_ids(dict(migrated)) if isinstance(migrated, dict) else {}


def get_provider_mode() -> str:
    """Which engine holds the conversation. Defaults to Gemini Live."""
    mode = str(get_providers().get("mode", "") or "").strip().lower()
    return mode if mode in (PROVIDER_MODE_GEMINI, PROVIDER_MODE_PIPELINE) \
        else PROVIDER_MODE_GEMINI


def get_provider_config(provider_id: str) -> dict:
    """Stored settings for one provider — address, model, key, limits."""
    account = _provider_key_account(provider_id)
    creds = get_providers().get("creds")
    val = creds.get(provider_id) if isinstance(creds, dict) else None
    config = dict(val) if isinstance(val, dict) else {}
    secret = _read_secure_secret(account)
    if secret:
        config["api_key"] = secret
    return config


def get_pipeline() -> dict:
    """Which provider fills each slot of the pipeline engine."""
    val = get_providers().get("pipeline")
    return dict(val) if isinstance(val, dict) else {}


def get_sidecar_provider() -> str:
    """The provider id used for one-shot calls when Gemini cannot answer them,
    or '' for none.

    Separate from the conversation on purpose: these are different jobs with
    different failure modes. Gemini's Live quota and its text quota are separate
    pools, and on the free tier it is the text pool that runs out first — which
    kills document reading, screen reasoning and code generation while the
    conversation carries on. Being able to point only those at another model,
    without changing the voice at all, is the cheapest useful thing this whole
    folder does.
    """
    return str(get_providers().get("sidecar", "") or "").strip()


@_locked
def _patch_providers(**fields) -> None:
    p = get_providers()
    p.update(fields)
    _patch_config(providers=p)


def save_provider_mode(mode: str) -> None:
    mode = (mode or "").strip().lower()
    _patch_providers(mode=mode if mode in (PROVIDER_MODE_GEMINI,
                                           PROVIDER_MODE_PIPELINE)
                     else PROVIDER_MODE_GEMINI)


def save_provider_config(provider_id: str, values: dict) -> None:
    """Merge settings for one provider. Only the given keys are touched, so the
    panel can save one field without reading and rewriting the rest.

    The API key goes to the Credential Locker; the rest stays in the file.
    """
    _save_provider_configs({provider_id: values})


def _save_provider_configs(provider_values: dict, block_updates: dict | None = None) -> None:
    with _CREDENTIAL_LOCK:
        get_providers()
        data = _read_api_config_for_update()
        block = data.get("providers")
        block = _upgrade_ids(dict(block)) if isinstance(block, dict) else {}
        creds = block.get("creds")
        creds = dict(creds) if isinstance(creds, dict) else {}
        staged: list[tuple[str, str | None]] = []
        removals: list[str] = []

        try:
            for provider_id, values in provider_values.items():
                account = _provider_key_account(provider_id)
                if not isinstance(values, dict):
                    raise ValueError("Provider settings must be an object.")
                updates = dict(values)
                if "api_key" in updates:
                    new_key = updates.pop("api_key")
                    if new_key is None:
                        new_key = ""
                    if not isinstance(new_key, str):
                        raise ValueError("A provider API key must be text.")
                    new_key = new_key.strip()
                    previous = _read_secure_secret(account)
                    if new_key:
                        if new_key != previous:
                            staged.append((account, previous))
                            _store_secure_secret(account, new_key)
                    elif previous:
                        removals.append(account)

                current = creds.get(provider_id)
                current = dict(current) if isinstance(current, dict) else {}
                current.pop("api_key", None)
                current.update(updates)
                creds[provider_id] = current

            block["creds"] = creds
            block.update(block_updates or {})
            data["providers"] = block
            _write_api_config(data)
        except Exception as exc:
            rollback_errors = []
            for account, previous in reversed(staged):
                try:
                    _restore_secure_secret(account, previous)
                except Exception as rollback_exc:
                    rollback_errors.append(type(rollback_exc).__name__)
            if rollback_errors:
                raise RuntimeError(
                    "Provider configuration failed and credential rollback "
                    f"was incomplete ({', '.join(rollback_errors)})."
                ) from exc
            if isinstance(exc, ValueError):
                raise
            raise RuntimeError(
                "Provider configuration failed; prior credentials were restored."
            ) from exc

        for account in removals:
            if _read_secure_secret(account):
                _remove_secure_secret(account)


def delete_provider_credentials(provider_id: str) -> bool:
    """Delete one provider's stored credentials and disable its selection."""
    provider = str(provider_id or "").strip()
    if not provider:
        return False
    account = _provider_key_account(provider)
    with _CREDENTIAL_LOCK:
        get_providers()
        data = _read_api_config_for_update()
        block = data.get("providers")
        block = _upgrade_ids(dict(block)) if isinstance(block, dict) else {}
        changed = False
        creds = block.get("creds")
        if isinstance(creds, dict) and provider in creds:
            creds = dict(creds)
            creds.pop(provider, None)
            block["creds"] = creds
            changed = True
        if block.get("sidecar") == provider:
            block["sidecar"] = ""
            changed = True
        pipeline = block.get("pipeline")
        if isinstance(pipeline, dict):
            pipeline = dict(pipeline)
            for slot, selected in tuple(pipeline.items()):
                if selected == provider:
                    pipeline[slot] = ""
                    changed = True
            block["pipeline"] = pipeline

        secure_key = _read_secure_secret(account)
        if changed:
            data["providers"] = block
            _write_api_config(data)
        if secure_key:
            _remove_secure_secret(account)
            changed = True
        return changed


@_locked
def save_pipeline(stt: str = None, llm: str = None, tts: str = None) -> None:
    """Set one or more pipeline slots. `None` leaves a slot alone; '' clears it."""
    p = get_providers()
    pipe = p.get("pipeline")
    pipe = dict(pipe) if isinstance(pipe, dict) else {}
    for slot, value in (("stt", stt), ("llm", llm), ("tts", tts)):
        if value is not None:
            pipe[slot] = str(value).strip()
    p["pipeline"] = pipe
    _patch_config(providers=p)


def save_sidecar_provider(provider_id: str) -> None:
    _patch_providers(sidecar=(provider_id or "").strip())


def get_coding_provider() -> str:
    """The service dev_agent writes code with, or '' to use whatever answers
    background requests.

    Its own setting because coding is the one background job where the model
    matters most and the request is longest — somebody may well want a strong
    coding model there and something cheap and fast for reading the screen."""
    return str(get_providers().get("coding", "") or "").strip()


def save_coding_provider(provider_id: str) -> None:
    _patch_providers(coding=(provider_id or "").strip())


def get_sidecar_first() -> bool:
    """Whether the side-call provider is tried BEFORE Gemini rather than after.

    Off by default: measured in core/gemini.py, the Gemini rungs answer a short
    request in well under a second, and a local model on ordinary hardware does
    not. On means something specific — 'I would rather this stayed on my
    machine, and I accept it being slower' — so it is a choice someone makes,
    never a default they inherit.
    """
    return bool(get_providers().get("sidecar_first", False))


def save_sidecar_first(enabled: bool) -> None:
    _patch_providers(sidecar_first=bool(enabled))


@_locked
def save_providers(mode: str = None, pipeline: dict = None, creds: dict = None,
                   sidecar: str = None, sidecar_first: bool = None,
                   coding: str = None) -> None:
    """Write the whole providers block in ONE file write.

    The settings panel used to call five setters in a row — one per provider's
    credentials, then the mode, then the pipeline, then the two side-call
    keys — and every one of them was a separate read-modify-write of
    api_keys.json. Five reads and five writes of the same file, in sequence,
    while the panel's own APPLY was already triggering a reconnect that makes
    main.py write to it too. Any interleaving loses whichever key was written
    first, and the one people noticed was the mode: set MY OWN SETUP, save,
    reopen, and it says Gemini again.

    `None` leaves a section alone; `creds` is merged per provider so a panel
    that only edited one does not have to send the others.
    """
    block_updates = {}
    if mode is not None:
        m = str(mode).strip().lower()
        block_updates["mode"] = (m if m in (PROVIDER_MODE_GEMINI,
                                            PROVIDER_MODE_PIPELINE)
                                 else PROVIDER_MODE_GEMINI)
    if pipeline is not None:
        current = get_providers().get("pipeline")
        current = dict(current) if isinstance(current, dict) else {}
        current.update({k: str(v or "").strip() for k, v in pipeline.items()})
        block_updates["pipeline"] = current
    if sidecar is not None:
        block_updates["sidecar"] = str(sidecar or "").strip()
    if sidecar_first is not None:
        block_updates["sidecar_first"] = bool(sidecar_first)
    if coding is not None:
        block_updates["coding"] = str(coding or "").strip()

    _save_provider_configs(creds or {}, block_updates)


# ── Language and region ─────────────────────────────────────────────────────
# The product speaks five languages and lives in one place. Both settings
# live at the top level of api_keys.json, next to everything else, and both
# go through the same locked read-modify-write as every other setter here.
#
# The language is a code ("en", "es", "pt", "fr", "zh"); which codes are
# real is decided by core/localization.py, which is the only place that
# knows the catalog. Here it is just a stored string.
DEFAULT_LANGUAGE_CODE = "en"


def get_language() -> str:
    """The stored language code, or the default when it is unset."""
    value = str(load_api_keys().get("language") or "").strip().lower()
    return value or DEFAULT_LANGUAGE_CODE


def save_language(code: str) -> None:
    """Persist the language code."""
    _patch_config(language=str(code or "").strip().lower())


def get_region() -> dict:
    """The stored region as {"city": ..., "country": ...}, or {} when unset."""
    region = load_api_keys().get("region")
    if not isinstance(region, dict):
        return {}
    city = str(region.get("city") or "").strip()
    country = str(region.get("country") or "").strip()
    if not city and not country:
        return {}
    return {"city": city, "country": country}


def save_region(city: str, country: str) -> None:
    """Persist the region."""
    _patch_config(region={
        "city": str(city or "").strip(),
        "country": str(country or "").strip(),
    })


# Which player a music request goes to. 'auto' means "Spotify when it is
# installed on this machine, YouTube otherwise", and it is the default
# because YouTube needs no account, no client id and no token.
DEFAULT_MUSIC_SERVICE = "auto"


def get_music_service() -> str:
    """The stored music route: 'auto', 'spotify' or 'youtube'."""
    value = str(load_api_keys().get("music_service") or "").strip().lower()
    return value if value in ("auto", "spotify", "youtube") else DEFAULT_MUSIC_SERVICE


def save_music_service(service: str) -> None:
    """Persist the music route."""
    value = str(service or "").strip().lower()
    _patch_config(music_service=value if value in ("auto", "spotify", "youtube")
                  else DEFAULT_MUSIC_SERVICE)


# How the assistant shows itself: the whole dashboard, only the core in a
# corner, or nothing but the icon in the tray. And the corner the core sits
# in. Both are remembered between runs, because a face that forgets where
# it was put is a face that has to be placed again every morning.
DEFAULT_APPEARANCE_MODE = "dashboard"
DEFAULT_CORE_CORNER = "bottom-right"
_APPEARANCE_MODES = ("dashboard", "core", "tray")
_CORE_CORNERS = ("bottom-right", "bottom-left", "top-right", "top-left")


def get_appearance_mode() -> str:
    """The saved appearance: 'dashboard', 'core' or 'tray'."""
    value = str(load_api_keys().get("appearance_mode") or "").strip().lower()
    return value if value in _APPEARANCE_MODES else DEFAULT_APPEARANCE_MODE


def save_appearance_mode(mode: str) -> None:
    """Persist the appearance."""
    value = str(mode or "").strip().lower()
    _patch_config(appearance_mode=value if value in _APPEARANCE_MODES
                  else DEFAULT_APPEARANCE_MODE)


def get_core_corner() -> str:
    """The saved corner for the core."""
    value = str(load_api_keys().get("core_corner") or "").strip().lower()
    return value if value in _CORE_CORNERS else DEFAULT_CORE_CORNER


def save_core_corner(corner: str) -> None:
    """Persist the corner for the core."""
    value = str(corner or "").strip().lower()
    _patch_config(core_corner=value if value in _CORE_CORNERS
                  else DEFAULT_CORE_CORNER)
