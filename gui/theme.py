"""gui/theme.py — Palette, typography and the configuration values every part of the HUD reads.

Split out of ui.py in the previous chassis; behaviour unchanged."""
from __future__ import annotations

import json
import math
import os
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

# Windows-only: a console window must never open behind the HUD.
_WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}

# Qt's video backend prints the ffmpeg stream banner — codec, bitrate, the
# whole signed googlevideo URL — to the console for every stream it opens. That
# is two screenfuls per video, it looks like an error to anyone reading the log,
# and it puts a URL carrying the viewer's IP address into a terminal people
# paste into bug reports. Silenced here, before Qt initialises its logging.
# An explicit setting from the user is left alone.
os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.*=false")

from PyQt6.QtCore import (
    QEasingCurve, QLineF, QMimeData, QObject, QParallelAnimationGroup, QPointF,
    QPoint, QPropertyAnimation, QRect, QRectF, QSize, QSizeF, Qt, QThread,
    QTimer, QUrl, pyqtProperty, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QConicalGradient, QDragEnterEvent, QDropEvent, QFont,
    QFontDatabase, QKeySequence, QLinearGradient, QPainter, QPainterPath,
    QPen, QPixmap, QRadialGradient, QShortcut,
)
# Video playback for the HUD. Part of PyQt6, so it costs no new dependency —
# but the multimedia plugins are a separate piece of the Qt install and can be
# absent on a stripped-down system, so a failure here disables one feature
# rather than stopping the assistant from starting.
try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PyQt6.QtMultimediaWidgets import QGraphicsVideoItem
    HAVE_VIDEO = True
except Exception as _e:            # noqa: BLE001 - reported, never fatal
    QAudioOutput = QMediaPlayer = QGraphicsVideoItem = None
    HAVE_VIDEO = False
    print(f"[Video] playback unavailable ({_e}) — the HUD will not show video.")

from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QGraphicsScene, QGraphicsView, QGraphicsPixmapItem,
    QStackedWidget, QTextEdit, QVBoxLayout, QWidget, QProgressBar, QSlider,
)

try:
    from core.avatar import HoloAvatar
except Exception:      # pragma: no cover — HUD must never die over cosmetics
    HoloAvatar = None


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR   = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"


def _read_full_config() -> dict:
    """The whole configuration, read the one way there is to read it.

    THROUGH `memory/config_manager.py`, and not by opening the file here.
    The file is written in one place —the configuration manager— and it
    must be READ in the same place, for one reason: a reader that opens
    the file by its own path ignores the manager, and the manager is what
    the tests move to a temporary folder. A reader that ignores it reads
    the owner's real settings while the test thinks it is reading its
    own — and the test then passes or fails depending on whether the
    owner happens to have a key saved.

    Returns {} on any error.
    """
    try:
        from memory.config_manager import load_api_keys
        data = load_api_keys()
        return data if isinstance(data, dict) else {}
    except Exception:
        pass
    try:
        return json.loads(API_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


# Single source of truth for the release name — the window title, the header
# badge and the readme must never disagree again.
APP_VERSION  = "T.E.E.M.O."
# The protocol badge shows the name WITHOUT its final period: the product is
# written "T.E.E.M.O." everywhere else, but a badge that ends in a period
# reads as a sentence that stopped rather than as a name. It must also never
# be empty — an empty protocol would put a bare label on the HUD.
APP_PROTOCOL = APP_VERSION.rstrip(".") or APP_VERSION

# THE SIZE OF THE WINDOW.
#
# The dashboard is three columns — the gauges, the head, the conversation —
# and it needs about twelve hundred and fifty pixels to lay them out side
# by side. It used to open at nine hundred and eighty, which is narrower
# than the interface it was supposed to show: the middle column arrived
# squeezed and the controls of the drawer had nowhere to stand.
#
# So the window opens wide enough for what it carries, and it does not go
# below the width the three columns need.
_DEFAULT_W, _DEFAULT_H = 1280, 800
_MIN_W,     _MIN_H     = 1100, 640
_LEFT_W  = 148
_RIGHT_W = 340

# T.E.E.M.O. is a Windows-only desktop assistant. The OS is a fact about the
# product, not a runtime question: nothing here asks what it is running on.
_OS = "Windows"


class C:
    BG        = "#00060a"
    PANEL     = "#010d14"
    PANEL2    = "#010f18"
    BORDER    = "#0d3347"
    BORDER_B  = "#1a5c7a"
    BORDER_A  = "#0f4060"
    PRI       = "#00d4ff"
    PRI_DIM   = "#007a99"
    PRI_GHO   = "#001f2e"
    ACC       = "#ff6b00"
    ACC2      = "#ffcc00"
    GREEN     = "#00ff88"
    GREEN_D   = "#00aa55"
    RED       = "#ff3355"
    MUTED_C   = "#ff3366"
    TEXT      = "#8ffcff"
    TEXT_DIM  = "#3a8a9a"
    TEXT_MED  = "#5ab8cc"
    WHITE     = "#d8f8ff"
    DARK      = "#000d14"
    BAR_BG    = "#011520"

    # ── Lifted out of the overlays, the widgets and the window ──────────────
    # Every colour in the product is named here; nothing else writes a hex.
    INPUT_BG       = "#000d12"   # the field behind every input, combo box, meter and slider
    GREEN_BG       = "#001a08"   # the plate of a switch that is ON
    GREEN_BG_HOVER = "#002010"   # that same plate while the pointer is on it
    GREEN_BADGE    = "#001a0d"   # the plate behind the "phone connected" tick
    ACT_BG         = "#00091a"   # the plate behind the accent-coloured action button
    ERR            = "#ff6b6b"   # a test that came back failed
    WARN           = "#f4b942"   # a warning the person has to read (firewall, bind error)
    WHITE_PURE     = "#ffffff"   # pure white — a handle, a plain chip
    GREY           = "#888888"   # the dim text of a quiet chip and of an unknown file
    GREY_LT        = "#aaaaaa"   # the dim text of a plain text file
    CAM_BG         = "#000308"   # the container behind the live camera
    VIDEO_BG       = "#000000"   # the container behind the HUD video
    FILE_AUDIO     = "#cc44ff"   # the audio file-type icon
    FILE_PDF       = "#ff4444"   # the pdf file-type icon
    FILE_WORD      = "#4488ff"   # the word file-type icon
    FILE_EXCEL     = "#44bb44"   # the excel file-type icon
    FILE_ARCHIVE   = "#ff8844"   # the archive file-type icon
    FILE_PPTX      = "#ff6622"   # the powerpoint file-type icon
    FILE_DATA      = "#88ddff"   # the data file-type icon

    # ── The deep field ──────────────────────────────────────────────────────
    # The background the interface is projected on. Almost black, but not
    # black: flat black reads as a page, and a face on flat black reads as
    # a sticker. These two are NOT re-derived when the accent changes — the
    # field is the room, and the room does not change colour.
    DEEP_TOP       = "#020a14"
    DEEP_BOT       = "#000306"


# Keys tied to the accent colour — status colours (ACC, GREEN, RED…) stay fixed
_HUE_LINKED = (
    "BG", "PANEL", "PANEL2", "BORDER", "BORDER_B", "BORDER_A",
    "PRI", "PRI_DIM", "PRI_GHO", "TEXT", "TEXT_DIM", "TEXT_MED",
    "WHITE", "DARK", "BAR_BG",
)
_PALETTE_DEFAULTS: dict[str, str] = {k: getattr(C, k) for k in _HUE_LINKED}

DEFAULT_UI_COLOR = _PALETTE_DEFAULTS["PRI"]


def _valid_hex(value: str) -> bool:
    """True for a six-digit `#rrggbb` colour, and for nothing else."""
    text = str(value or "").strip().lower()
    if not (text.startswith("#") and len(text) == 7):
        return False
    try:
        int(text[1:], 16)
    except ValueError:
        return False
    return True


def _hsv(h: str) -> tuple[float, float, float]:
    """`"#00d4ff"` -> `(hue, saturation, value)`, all in 0..1."""
    import colorsys

    r = int(h[1:3], 16) / 255
    g = int(h[3:5], 16) / 255
    b = int(h[5:7], 16) / 255
    return colorsys.rgb_to_hsv(r, g, b)


def _palette_for(accent_hex: str, exact: bool = False) -> dict[str, str]:
    """The whole accent-linked palette, derived from one accent colour.

    `exact=False` is the original rule: the hue moves and the brightness and
    the saturation of every colour stay where they were, so the DESIGN of the
    interface does not change — only the colour of the family does.

    `exact=True` also takes the saturation and the value of the target, and
    keeps the family's own ratios: every colour is scaled by the SAME factor
    the accent is scaled by, so the dark plate stays dark relative to the
    accent and the dim text stays dim. What changes is the colour of the
    family, never the design.
    """
    import colorsys

    base_h, base_s, base_v = _hsv(_PALETTE_DEFAULTS["PRI"])
    acc_h, acc_s, acc_v = _hsv(accent_hex)
    dh = acc_h - base_h
    grey = acc_s < 0.08   # near-grey accent → the whole theme is desaturated

    out: dict[str, str] = {}
    for key, hex0 in _PALETTE_DEFAULTS.items():
        h, s, v = _hsv(hex0)
        if grey:
            s *= 0.15
        ns, nv = s, v
        if exact:
            # The target's saturation and value, applied to the family with
            # the ratios of the family kept: the scale factor is the same for
            # every colour, so nothing changes its place in the design.
            ns = min(1.0, s * (acc_s / base_s)) if base_s > 1e-6 else 0.0
            nv = min(1.0, v * (acc_v / base_v)) if base_v > 1e-6 else 0.0
        r, g, b = colorsys.hsv_to_rgb((h + dh) % 1.0, ns, nv)
        out[key] = "#{:02x}{:02x}{:02x}".format(
            int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5))
    return out


def _write_palette(accent_hex: str, exact: bool = False) -> None:
    """Write one accent into the palette of class C.

    This is the part `apply_ui_accent` does, and nothing more: it does NOT
    touch the state of the accent, because the travel needs to write the
    palette on every frame of the movement while the TARGET of that movement
    stays where it was put."""
    palette = _palette_for(accent_hex, exact=exact)
    for key, hex0 in palette.items():
        setattr(C, key, hex0)


def apply_ui_accent(accent_hex: str, *, exact: bool = False) -> bool:
    """
    Re-derives the whole teal-family palette from the chosen accent colour
    (hue shift — brightness/saturation ratios are preserved, design stays intact).
    Painted elements (HUD, waveform, metrics) pick up the new colour on the next
    frame; stylesheet-based panels pick it up when they are rebuilt.

    `exact=True` also takes the saturation and the value of the target, with
    the ratios of the family kept — the accent becomes the colour the family
    was designed around, and not only its hue.
    """
    accent_hex = (accent_hex or "").strip().lower()
    if not _valid_hex(accent_hex):
        return False

    _write_palette(accent_hex, exact=exact)

    global _accent_current, _accent_target
    with _accent_lock:
        _accent_current = accent_hex
        _accent_target = accent_hex
    return True


def current_palette() -> dict[str, str]:
    """A snapshot of the accent-linked colours currently on class C."""
    return {k: getattr(C, k) for k in _HUE_LINKED}


def retheme_all_widgets(old: dict[str, str], new: dict[str, str]) -> None:
    """
    LIVE full theme change. Replaces the old palette colours with the new ones
    in EVERY widget's stylesheet across the app and repaints them. This way the
    colour change applies INSTANTLY across the whole interface — panels, buttons,
    borders included — not just the painted elements. No restart needed.
    """
    mapping = {old[k].lower(): new[k].lower()
               for k in old if old[k].lower() != new.get(k, old[k]).lower()}
    if not mapping:
        return
    app = QApplication.instance()
    if app is None:
        return
    for w in app.allWidgets():
        try:
            ss = w.styleSheet()
            if ss:
                s2 = ss
                for o, n in mapping.items():
                    if o in s2:
                        s2 = s2.replace(o, n)
                if s2 != ss:
                    w.setStyleSheet(s2)
            w.update()
        except Exception:
            pass


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h); c.setAlpha(a); return c


# ── the accent, and the trip towards it ──────────────────────────────────────
#
# WHY THE ACCENT IS A THING OF ITS OWN
#     The palette used to be a snapshot of `C` and nothing more: you asked for
#     a colour and you got it, at once, as a jump. The plan for phase 8 asks
#     for something else — a colour that is TRAVELLED to, at a bounded speed,
#     because a colour that jumps reads as a mistake and a colour that
#     settles reads as a mood changing.
#
#     So the accent is now a small state of its own:
#
#         _accent_current — the colour on screen right now.
#         _accent_target  — the colour the trip is heading for. When no trip
#                           is running it is the same as the current one.
#
#     The trip itself is a `QPropertyAnimation` over a `QColor` property, and
#     the property lives on `_AccentCarrier` because `QPropertyAnimation`
#     animates the properties of a QObject and a QColor is not one.
#
# WHY A TRIP NEVER JUMPS
#     A new trip starts from `_accent_current`, which is where the last trip
#     got to — never from the beginning. Two states in a row therefore read as
#     one continuous movement, which is the whole point of the mechanism.

_accent_lock = threading.RLock()
_accent_current: str = DEFAULT_UI_COLOR      # "#00d4ff"
_accent_target: str = DEFAULT_UI_COLOR
_accent_anim: QPropertyAnimation | None = None
_accent_carrier: "_AccentCarrier | None" = None
# The palette as it was BEFORE the trip: the stylesheets still hold those
# colours, and they are what the end of the trip has to replace.
_accent_palette_before: dict[str, str] | None = None


class _AccentCarrier(QObject):
    """The object that owns the colour that travels.

    `QPropertyAnimation` animates a property OF A QOBJECT, and a `QColor` is
    not a QObject, so the colour that moves lives in a property of this object
    and the animation drives that property. Nothing else uses it."""

    def __init__(self, color: QColor):
        super().__init__()
        self._color = QColor(color)

    def _get_color(self) -> QColor:
        return self._color

    def _set_color(self, value) -> None:
        try:
            self._color = QColor(value)
        except Exception:
            pass

    color = pyqtProperty(QColor, fget=_get_color, fset=_set_color)


def current_accent() -> str:
    """The accent colour that is on screen right now, as a hex string.

    During a trip this is the colour in between: the accent is not swapped at
    the end of the movement, it is walked there."""
    with _accent_lock:
        return _accent_current


def accent_target() -> str:
    """The hex the accent is travelling towards.

    When no trip is running it is the current accent — the colour the
    interface is at rest on."""
    with _accent_lock:
        return _accent_target


def _apply_accent(accent_hex: str, *, exact: bool = True, repaint: bool = True,
                  old: dict[str, str] | None = None) -> None:
    """Write one accent into the palette, and (optionally) repaint the app.

    `repaint=False` is what the animation uses for its intermediate frames:
    the painted parts of the interface read the palette at paint time and
    glide by themselves, while the STYLESHEET panels are rewritten once, at
    the end of the trip, when the colour has settled. Rewriting every
    stylesheet on every frame of a 700 ms movement would cost more than the
    movement itself.

    `old` is the palette to compare against when repainting. The end of a
    trip has to compare against the palette from BEFORE the trip: the
    stylesheets still hold that one, and comparing against the palette of
    the last frame would find nothing to replace."""
    if old is None:
        old = current_palette()
    _write_palette(accent_hex, exact=exact)
    if repaint:
        retheme_all_widgets(old, current_palette())


def _on_accent_value(color) -> None:
    """One step of the trip: the palette follows the travelling colour."""
    global _accent_current
    try:
        hexv = QColor(color).name().lower()
        with _accent_lock:
            _accent_current = hexv
        _apply_accent(hexv, exact=True, repaint=False)
    except Exception:
        pass


def _on_accent_finished() -> None:
    """The trip is over: the accent is the target, and the app is repainted."""
    global _accent_current, _accent_anim, _accent_palette_before
    with _accent_lock:
        _accent_current = _accent_target
        target = _accent_target
        before = _accent_palette_before
        _accent_palette_before = None
    try:
        _apply_accent(target, exact=True, repaint=True, old=before)
    except Exception:
        pass
    with _accent_lock:
        _accent_anim = None


def animate_accent_to(target_hex: str, duration_ms: int = 700) -> None:
    """Travel the accent towards a colour, at a bounded speed.

    The movement is continuous: the accent is walked from where it is to
    where it is going, frame by frame, and it is never swapped in one jump.
    A second call while a trip is running starts from where the first one
    got to, so the colour never jumps.

    WITHOUT A QAPPLICATION (headless, tests) there is no clock to travel on:
    the final colour is applied at once and nothing raises.

    OFF THE GUI THREAD a `QPropertyAnimation` may not be built, so the final
    colour is applied directly instead. The palette is plain data and is safe
    to write from anywhere; the widgets are not, and are left alone."""
    global _accent_anim, _accent_carrier, _accent_palette_before, _accent_current
    global _accent_target

    target = str(target_hex or "").strip().lower()
    if not _valid_hex(target):
        return
    try:
        duration_ms = int(duration_ms)
    except (TypeError, ValueError):
        duration_ms = 700
    duration_ms = max(0, min(10000, duration_ms))

    with _accent_lock:
        _accent_target = target

    app = QApplication.instance()
    if app is None:
        # No Qt at all: no clock, no animation. The end colour, now.
        try:
            with _accent_lock:
                _accent_current = target
            _apply_accent(target, exact=True, repaint=True)
        except Exception:
            pass
        return

    try:
        if QThread.currentThread() is not app.thread():
            # Not the GUI thread. Writing the palette is safe (it is plain
            # data); touching the widgets is not, so they are left to their
            # own repaint.
            with _accent_lock:
                _accent_current = target
            _apply_accent(target, exact=True, repaint=False)
            return
    except Exception:
        pass

    try:
        with _accent_lock:
            start_hex = _accent_current
            if _accent_anim is not None:
                try:
                    _accent_anim.stop()
                except Exception:
                    pass
                _accent_anim = None
            # The stylesheets still hold the palette of before the trip: this
            # is the one the end of the trip has to replace.
            _accent_palette_before = current_palette()
            carrier = _AccentCarrier(QColor(start_hex))
            anim = QPropertyAnimation(carrier, b"color")
            anim.setDuration(duration_ms)
            anim.setStartValue(QColor(start_hex))
            anim.setEndValue(QColor(target))
            anim.setEasingCurve(QEasingCurve.Type.InOutSine)
            anim.valueChanged.connect(_on_accent_value)
            anim.finished.connect(_on_accent_finished)
            _accent_anim = anim
            _accent_carrier = carrier
            anim.start()
    except Exception:
        # A trip that cannot start must not take the app with it.
        try:
            with _accent_lock:
                _accent_current = target
            _apply_accent(target, exact=True, repaint=True)
        except Exception:
            pass


def set_accent_mix(states) -> str:
    """Apply the colour that SEVERAL affective states add up to.

    `states` is what `core.emotional_spectrum.blend` takes: a list of
    `(EmotionalState, weight)` pairs. This is the path a PARTIAL intensity
    takes — a state that is only half in force is a blend, and a blend is
    one colour. Returns the hex that was applied."""
    from core.emotional_spectrum import blend

    try:
        hexv = blend(list(states))
    except Exception:
        return current_accent()
    try:
        old = current_palette()
        apply_ui_accent(hexv, exact=True)
        retheme_all_widgets(old, current_palette())
    except Exception:
        pass
    return hexv

