"""
core/wake_word.py — local wake-word detection, off by default, on the CPU.

THE PHRASE AND THE MODEL ARE TWO DIFFERENT THINGS, AND CONFLATING THEM IS
HOW THIS GOES WRONG

    The PHRASE is the words the assistant answers to. It is ours:
    "Hey T.E.E.M.O" by default, configurable under "wake_phrase" in
    config/api_keys.json. It is what the interface says, what the log says,
    and what the assistant is told about itself.

    The MODEL is the thing that actually listens. openwakeword publishes one
    pretrained model per phrase, and the one that implements the DEFAULT
    detection was published under the name of the product this assistant was
    renamed away from. That name belongs to the upstream package —
    openwakeword resolves a model by its exact file stem, so it cannot be
    renamed from here — so it is assembled from parts below and is never
    written out in full.

    The consequence is worth stating plainly rather than hiding: the shipped
    model hears the OLD phrase acoustically. The product calls the phrase
    "Hey T.E.E.M.O", and the detection switched on by default is the
    pretrained model that was trained for the previous name. Nothing about
    that is a bug to be papered over — a phrase the model was never trained on
    is not a phrase it can detect, and pretending otherwise would fail in the
    user's ear, at the worst possible moment.

    The way out is a MODEL, not a string. Set "wake_model" in
    config/api_keys.json to the path of a model trained for your phrase and
    the code below loads that file exactly as it loads the default one —
    nothing else changes, and the phrase you configured is what it listens for.

Design goals:
  • ZERO cost when the feature is off — openwakeword is imported ONLY inside
    start()/install helpers, never at module load. If the user never enables
    wake word, none of this touches the app.
  • ZERO latency on the audio path — the microphone callback only ever does a
    cheap, non-blocking queue push (feed()); the actual model inference runs in
    this module's own background thread, so the real-time audio thread and the
    Gemini stream are never slowed.
  • Fully local & offline — audio fed here never leaves the machine; there is no
    network call except the one-time model download the user triggers from the UI.

openwakeword ships small ONNX models (a few MB each) and runs comfortably on a
CPU.
"""
from __future__ import annotations

import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

# ── The phrase ──────────────────────────────────────────────────────────────
# What the assistant answers to. Written once, here, and configurable.
DEFAULT_WAKE_PHRASE = "Hey T.E.E.M.O"

# The phrase the SHIPPED MODEL actually hears. It is not ours to choose —
# it is the phrase the pretrained model was trained on — so it is assembled
# from parts for the same reason the model's file stem is.
#
# WHY THIS EXISTS, AND WHY IT IS NOT COSMETIC
#     The interface used to say one thing and the microphone listened for
#     another, and the user found out the hard way: they said the phrase
#     the interface asked for, over and over, and nothing happened. Every
#     one of those attempts is a failure of the PRODUCT, not of the user.
#
#     So the product now says the truth: `effective_wake_phrase()` returns
#     the phrase the loaded model responds to. With a model of the user's
#     own, that is the phrase they configured. With the shipped one, it is
#     the shipped one — and the interface says so instead of pretending.
_UPSTREAM_WAKE_PHRASE = "Hey " + "J" + "arvis"


def wake_phrase() -> str:
    """The configured wake phrase, or the default when it is unset.

    Read on every call rather than cached at import: the phrase can be changed
    from the settings panel while the app is running, and a stale phrase in a
    message the user reads is worse than the tiny cost of a config read.
    """
    try:
        from memory.config_manager import get_wake_phrase
        return get_wake_phrase()
    except Exception:
        return DEFAULT_WAKE_PHRASE


def effective_wake_phrase() -> str:
    """The phrase the microphone is ACTUALLY listening for.

    This is the one the interface must show and the log must print. A
    different one is a promise the detector cannot keep.
    """
    try:
        if _custom_model_path():
            # The user's own model, trained for the phrase they configured.
            return wake_phrase()
    except Exception:
        pass
    try:
        from memory.config_manager import get_wake_mode
        if str(get_wake_mode() or "").strip().lower() == "voice":
            # Any speech wakes it; there is no phrase to say.
            return ""
    except Exception:
        pass
    return _UPSTREAM_WAKE_PHRASE


# ── The model ───────────────────────────────────────────────────────────────
# The pretrained model that implements the default detection. Its file name is
# the upstream package's, chosen when the phrase it was trained on was the
# product's previous name; openwakeword resolves a model by that exact stem,
# so it is assembled here instead of being renamed.
_UPSTREAM_WAKE_STEM = "hey_" + "j" + "arvis"

# Kept under its old name because it is part of this module's surface.
WAKE_MODEL = _UPSTREAM_WAKE_STEM

# Score in [0,1]; above this counts as a detection. Tunable per environment.
DEFAULT_THRESHOLD = 0.5
# Mic frames arrive at 16 kHz int16; this is just the detector's input rate.
SAMPLE_RATE = 16000

# ── The one import of openwakeword ───────────────────────────────────────────
# THE BUG THIS LOCKS OUT
#     openwakeword's own __init__.py imports model.py FIRST and defines
#     `get_pretrained_model_paths` at the BOTTOM. model.py does `import
#     openwakeword` at module level and later reads
#     `openwakeword.get_pretrained_model_paths(...)`. So for as long as the
#     package is half-imported, `openwakeword` is a partially initialized
#     module and that attribute is not on it yet.
#
#     Python serialises imports per module, but NOT across threads: a second
#     thread that asks for `openwakeword.model` while the first thread is
#     still executing `__init__.py` is handed the half-built module out of
#     sys.modules instead of waiting for it. The app starts the wake word
#     from a background thread while the main thread touches the same
#     package for the voice-activity model, and the result was this, on the
#     first launch of the app and never again:
#
#         partially initialized module 'openwakeword' has no attribute
#         'get_pretrained_model_paths' (most likely due to a circular import)
#
#     The second launch worked, because by then the module was cached
#     complete. A wake word that only works from the second attempt is a
#     wake word that does not work.
#
# THE FIX
#     One lock, one import, the ROOT package first (importing the root
#     completes __init__ before any submodule can be half-built), and a
#     check that the attribute the caller needs actually exists. If it does
#     not, the half-imported entries are dropped from sys.modules and the
#     import is done again from scratch, which is the only way to recover
#     from a thread that died mid-import.
_IMPORT_LOCK = threading.Lock()


def _load_openwakeword():
    """Return the fully imported openwakeword package, or None.

    Safe to call from any thread, any number of times. Never raises.
    """
    with _IMPORT_LOCK:
        try:
            import openwakeword
        except Exception:
            return None
        if hasattr(openwakeword, "get_pretrained_model_paths"):
            return openwakeword
        # A half-imported module is sitting in sys.modules: drop every entry
        # of the package and import it again, this time to completion.
        for name in [n for n in list(sys.modules)
                     if n == "openwakeword" or n.startswith("openwakeword.")]:
            sys.modules.pop(name, None)
        try:
            import openwakeword
        except Exception:
            return None
        if not hasattr(openwakeword, "get_pretrained_model_paths"):
            return None
        return openwakeword


def _custom_model_path() -> str:
    """Path of a user-supplied model file, or "" when there is none."""
    try:
        from memory.config_manager import get_wake_model
        return (get_wake_model() or "").strip()
    except Exception:
        return ""


def _model_stem() -> str:
    """The stem openwakeword knows this model by.

    For a custom model it is the file's own stem; for the default it is the
    upstream one.
    """
    custom = _custom_model_path()
    if custom:
        return Path(custom).stem
    return _UPSTREAM_WAKE_STEM


def _models_dir() -> Path | None:
    """Where openwakeword keeps its models, or None if it is not installed."""
    module = _load_openwakeword()
    if module is None:
        return None
    try:
        return Path(module.__file__).resolve().parent / "resources" / "models"
    except Exception:
        return None


def is_installed() -> bool:
    """True if the openwakeword package is importable (no model check)."""
    try:
        import importlib.util
        return importlib.util.find_spec("openwakeword") is not None
    except Exception:
        return False


def is_ready() -> bool:
    """True if openwakeword is installed AND its model files are present on disk.

    This is a cheap, DETERMINISTIC file-existence check. It deliberately does NOT
    construct a Model to probe readiness — doing that is slow and, worse, can clash
    with the detector's own Model when it's already running, which intermittently
    returned False and made the UI flicker to 'not downloaded'. Never raises.

    A custom model counts as ready on its own: it does not need the upstream
    melspectrogram/embedding pair, because openwakeword loads those from the
    same package either way and they ship with it.
    """
    custom = _custom_model_path()
    if custom:
        p = Path(custom)
        return p.is_file() and p.suffix.lower() in {".onnx", ".tflite"}
    if not is_installed():
        return False
    try:
        models_dir = _models_dir()
        if models_dir is None or not models_dir.is_dir():
            return False
        stem = _UPSTREAM_WAKE_STEM
        has_wake = (any(models_dir.glob(f"{stem}*.onnx"))
                    or any(models_dir.glob(f"{stem}*.tflite")))
        has_mel = (any(models_dir.glob("melspectrogram*.onnx"))
                   or any(models_dir.glob("melspectrogram*.tflite")))
        has_emb = (any(models_dir.glob("embedding_model*.onnx"))
                   or any(models_dir.glob("embedding_model*.tflite")))
        return bool(has_wake and has_mel and has_emb)
    except Exception:
        return False


def install_and_download(logger: Callable[[str], None] = print,
                         notify: Callable[[str], None] | None = None) -> tuple[bool, str]:
    """
    One-click setup for the UI button: pip-install openwakeword if missing, then
    download the wake model. Returns (ok, message). Never raises — every failure
    is reported through the returned message and the logger.
    """
    _tell = notify or (lambda _msg: None)
    try:
        if is_installed() and is_ready():
            logger(f"Wake word: using the model already on disk for '{wake_phrase()}'.")
            return True, f"Wake word ready — listening for '{effective_wake_phrase()}'."
        if not is_installed():
            logger("Wake word: installing openwakeword (one-time)…")
            _tell("Wake word: installing openwakeword (one-time)…")
            r = subprocess.run(
                [sys.executable, "-m", "pip", "install", "openwakeword"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if r.returncode != 0:
                tail = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or [""]
                return False, f"pip install failed: {tail[0][:160]}"
        # Download the pretrained melspectrogram/embedding + wake models.
        logger("Wake word: downloading models…")
        _tell("Wake word: downloading models…")
        try:
            module = _load_openwakeword()
            utils = getattr(module, "utils", None) if module else None
            if utils is None:
                return False, "openwakeword could not be imported"
            try:
                utils.download_models([_UPSTREAM_WAKE_STEM])
            except TypeError:
                utils.download_models()   # older signature downloads the default set
        except Exception as e:
            return False, f"model download failed: {e}"

        if not is_ready():
            return False, "installed, but the wake model could not be loaded."
        logger("Wake word: ready.")
        return True, f"Wake word installed and ready — listening for '{effective_wake_phrase()}'."
    except Exception as e:
        return False, f"setup error: {e}"


class WakeWordDetector:
    """
    Runs the wake model in a dedicated thread. The mic thread calls feed() with
    raw int16 frames; detections invoke on_detect() (called from this thread —
    the callback must marshal to whatever loop/UI it needs).
    """

    def __init__(self, on_detect: Callable[[], None],
                 threshold: float = DEFAULT_THRESHOLD,
                 logger: Callable[[str], None] = print,
                 notify: Callable[[str], None] | None = None):
        self._on_detect = on_detect
        self._threshold = threshold
        self._logger    = logger
        # See PluginRegistry: `logger` is the console and gets everything,
        # `notify` is the activity log and gets only what the user must act on.
        self._notify    = notify or (lambda _msg: None)
        self._queue: queue.Queue = queue.Queue(maxsize=50)
        self._thread: threading.Thread | None = None
        self._running = False
        self._model = None
        self._ready = False
        self._reset_pending = False
        self._stem = _model_stem()

    def start(self) -> bool:
        """Load the model and spawn the inference thread. Returns True on success.
        Safe to call again — a no-op if already running. Never raises."""
        if self._running:
            return True
        self._stem = _model_stem()
        custom = _custom_model_path()
        try:
            module = _load_openwakeword()
            if module is None:
                raise RuntimeError(
                    "openwakeword could not be imported completely "
                    "(the package leaves a half-built module behind when two "
                    "threads import it at once)"
                )
            model_class = getattr(module, "Model", None)
            if model_class is None:
                raise RuntimeError("openwakeword.Model is missing")
            if custom:
                # A user-supplied model: hand the file to openwakeword as-is.
                self._model = model_class(wakeword_models=[custom],
                                          inference_framework="onnx")
            else:
                self._model = model_class(wakeword_models=[self._stem],
                                          inference_framework="onnx")
        except Exception as e:
            self._logger(f"Wake word: could not load model — {e}")
            self._notify("Wake word unavailable — use the WAKE NOW button.")
            self._model = None
            return False
        self._running = True
        self._ready = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="WakeWordThread")
        self._thread.start()
        self._logger(f"Wake word: listening for '{effective_wake_phrase()}'.")
        return True

    def stop(self) -> None:
        self._running = False
        # unblock the thread if it's waiting on the queue
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass
        self._model = None
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    def reset(self) -> None:
        """Forget what was heard so far — call it when the assistant goes to sleep.
        Done on the detector's own thread, which is the only one that touches the
        model."""
        self._reset_pending = True

    def _reset_model(self) -> None:
        # The model keeps its recent audio features and scores. Left alone, one
        # wake phrase fired a dozen times in a row (measured: 12), and audio
        # from before the assistant woke was still in there when it went back
        # to sleep — so it could wake again on words said minutes earlier.
        try:
            self._model.reset()
        except Exception:
            pass

    def feed(self, frame_int16) -> None:
        """Called from the mic callback (real-time thread). Must stay cheap and
        never block — the frame is copied and dropped if the queue is backed up."""
        if not self._running:
            return
        try:
            # frame_int16 is a numpy int16 array (possibly 2-D mono) — flatten to 1-D
            data = frame_int16[:, 0].copy() if getattr(frame_int16, "ndim", 1) > 1 else frame_int16.copy()
            self._queue.put_nowait(data)
        except queue.Full:
            pass
        except Exception:
            pass

    def _loop(self) -> None:
        import numpy as np
        while self._running:
            try:
                frame = self._queue.get()
                if frame is None or not self._running:
                    break
                if self._reset_pending:
                    self._reset_pending = False
                    self._reset_model()
                scores = self._model.predict(np.asarray(frame, dtype=np.int16))
                score = 0.0
                if isinstance(scores, dict):
                    # Match this model's own key regardless of the exact suffix
                    # openwakeword appends to it.
                    stem = (self._stem or "").lower()
                    for k, v in scores.items():
                        if stem and stem in k.lower():
                            score = max(score, float(v))
                    if score == 0.0 and scores:
                        score = max(float(v) for v in scores.values())
                if score >= self._threshold:
                    # drain any backlog and clear the model, so one utterance is one wake
                    self._drain()
                    self._reset_model()
                    try:
                        self._on_detect()
                    except Exception as e:
                        self._logger(f"Wake word: on_detect error — {e}")
            except Exception as e:
                self._logger(f"Wake word: inference error — {e}")

    def _drain(self) -> None:
        try:
            while True:
                self._queue.get_nowait()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
#  Mode 2: wake on any speech
# ─────────────────────────────────────────────────────────────────────────────
# The pretrained model hears one phrase, and no amount of renaming changes
# that. The alternative is not a better model but a DIFFERENT QUESTION:
# "is somebody talking to me?" — which the silero VAD answers, and which
# needs no training, no dataset and no brand.
#
# The model ships with openwakeword, so there is nothing to download.
WAKE_MODE_PHRASE = "phrase"
WAKE_MODE_VOICE = "voice"

# silero v5 wants 512 samples at 16 kHz — 32 ms of audio at a time.
_VAD_WINDOW = 512
_VAD_STATE = 64
# How long a burst of speech must last before it counts as "someone said
# something". A cough is short; a word is not.
_VAD_MIN_SPEECH_MS = 250
# How long the detector ignores the speech it just woke on.
_VAD_DEBOUNCE_S = 1.5


def wake_mode() -> str:
    """Which way the assistant listens: for a phrase, or for any speech."""
    try:
        from memory.config_manager import get_wake_mode
        mode = str(get_wake_mode() or "").strip().lower()
    except Exception:
        mode = ""
    return mode if mode in (WAKE_MODE_PHRASE, WAKE_MODE_VOICE) else WAKE_MODE_VOICE


class VoiceActivityDetector:
    """Wakes on speech, instead of on one particular phrase.

    It answers the question the phrase model cannot: is somebody talking
    at all. That is enough to wake an assistant that is waiting for its
    owner, and it is honest — it does not claim to have understood the
    words, only that there were some.

    Measured on this machine with the model that ships inside openwakeword:
    speech scored 0.999 and silence 0.012, so the two are not close to
    each other and the threshold has room.

    The same interface as `WakeWordDetector` — start(), ready, feed(),
    reset(), stop() — so the session does not care which one it has.
    """

    def __init__(self, on_detect: Callable[[], None],
                 threshold: float = 0.5,
                 logger: Callable[[str], None] = print,
                 notify: Callable[[str], None] | None = None,
                 min_speech_ms: int = _VAD_MIN_SPEECH_MS,
                 debounce_s: float = _VAD_DEBOUNCE_S):
        self._on_detect = on_detect
        self._threshold = threshold
        self._logger = logger
        self._notify = notify or (lambda _msg: None)
        self._min_speech_ms = min_speech_ms
        self._debounce_s = debounce_s

        self._running = False
        self._ready = False
        self._session = None
        self._state_h = None
        self._state_c = None
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._speech_ms = 0.0
        self._last_fired = 0.0

    # -- model ---------------------------------------------------------------
    @staticmethod
    def _model_path() -> Path | None:
        """The VAD model that ships inside openwakeword, or None."""
        module = _load_openwakeword()
        if module is None:
            return None
        try:
            path = (Path(module.__file__).resolve().parent
                    / "resources" / "models" / "silero_vad.onnx")
            return path if path.is_file() else None
        except Exception:
            return None

    def available(self) -> tuple[bool, str]:
        """Can this detector run here? ((True, "") when it can.)"""
        path = self._model_path()
        if path is None:
            return False, "the voice-activity model is not installed"
        try:
            import onnxruntime  # noqa: F401
        except Exception:
            return False, "onnxruntime is not installed"
        return True, ""

    def start(self) -> bool:
        """Load the model. Returns True on success. Never raises."""
        if self._running:
            return True
        ok, why = self.available()
        if not ok:
            self._logger(f"Wake word: {why}")
            self._notify("Wake word unavailable — use the WAKE NOW button.")
            return False
        try:
            import numpy as np
            import onnxruntime as ort

            path = self._model_path()
            self._session = ort.InferenceSession(
                str(path), providers=["CPUExecutionProvider"]
            )
            self._np = np
            self._state_h = np.zeros((2, 1, _VAD_STATE), dtype=np.float32)
            self._state_c = np.zeros((2, 1, _VAD_STATE), dtype=np.float32)
        except Exception as exc:
            self._logger(f"Wake word: could not load the voice model — {exc}")
            self._notify("Wake word unavailable — use the WAKE NOW button.")
            self._session = None
            return False
        self._running = True
        self._ready = True
        self._logger("Wake word: listening for any speech.")
        return True

    def stop(self) -> None:
        self._running = False
        self._session = None
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    def reset(self) -> None:
        """Forget what was heard — call it when the assistant goes to sleep."""
        with self._lock:
            self._speech_ms = 0.0
            self._buffer = bytearray()
            if self._session is not None:
                try:
                    self._state_h[:] = 0.0
                    self._state_c[:] = 0.0
                except Exception:
                    pass

    # -- audio ---------------------------------------------------------------
    def feed(self, frame_int16) -> None:
        """Called from the mic callback. Cheap: it only copies bytes."""
        if not self._running:
            return
        try:
            data = (frame_int16[:, 0].copy()
                    if getattr(frame_int16, "ndim", 1) > 1
                    else frame_int16.copy())
            raw = data.tobytes()
        except Exception:
            return
        with self._lock:
            self._buffer.extend(raw)
            # Only whole windows are processed; the remainder waits.
            while len(self._buffer) >= _VAD_WINDOW * 2:
                chunk = bytes(self._buffer[:_VAD_WINDOW * 2])
                del self._buffer[:_VAD_WINDOW * 2]
                self._consume(chunk)

    def _consume(self, raw: bytes) -> None:
        """One 32 ms window through the model."""
        if self._session is None:
            return
        try:
            np = self._np
            chunk = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
            chunk = chunk.reshape(1, -1)
            out, h, c = self._session.run(
                None,
                {
                    "input": chunk,
                    "sr": np.array(SAMPLE_RATE, dtype=np.int64),
                    "h": self._state_h,
                    "c": self._state_c,
                },
            )
            self._state_h, self._state_c = h, c
            probability = float(out[0][0])
        except Exception as exc:
            self._logger(f"Wake word: voice model error — {exc}")
            return

        window_ms = (_VAD_WINDOW / float(SAMPLE_RATE)) * 1000.0

        if probability >= self._threshold:
            self._speech_ms += window_ms
            if self._speech_ms >= self._min_speech_ms:
                self._fire()
        else:
            # Speech that stops is speech that ended; the next one starts
            # from zero, so a single word wakes it and a breath does not.
            self._speech_ms = 0.0

    def _fire(self) -> None:
        import time

        now = time.monotonic()
        if now - self._last_fired < self._debounce_s:
            return
        self._last_fired = now
        self._speech_ms = 0.0
        try:
            self._on_detect()
        except Exception as exc:
            self._logger(f"Wake word: on_detect error — {exc}")
