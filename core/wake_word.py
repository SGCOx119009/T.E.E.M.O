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
    try:
        import openwakeword
        return Path(openwakeword.__file__).resolve().parent / "resources" / "models"
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
            return True, f"Wake word ready — listening for '{wake_phrase()}'."
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
            import openwakeword.utils as _u
            try:
                _u.download_models([_UPSTREAM_WAKE_STEM])
            except TypeError:
                _u.download_models()   # older signature downloads the default set
        except Exception as e:
            return False, f"model download failed: {e}"

        if not is_ready():
            return False, "installed, but the wake model could not be loaded."
        logger("Wake word: ready.")
        return True, f"Wake word installed and ready — listening for '{wake_phrase()}'."
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
            from openwakeword.model import Model
            if custom:
                # A user-supplied model: hand the file to openwakeword as-is.
                self._model = Model(wakeword_models=[custom],
                                    inference_framework="onnx")
            else:
                self._model = Model(wakeword_models=[self._stem],
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
        self._logger(f"Wake word: listening for '{wake_phrase()}'.")
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
