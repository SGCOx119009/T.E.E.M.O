"""
gui/boot.py — the ignition.

WHAT IT IS
    The screen that comes up before the assistant is ready: the wordmark
    resolving out of the noise, a list of subsystems ticking one after
    another, a scale filling along the bottom, and then the interface
    opening underneath it.

WHY IT EXISTS
    Because an assistant that is simply THERE is a program, and a program
    that is there tells you nothing. A machine that shows its own
    checklist tells you three things at once: that it is alive, what it
    found, and what it could not check.

THE MOST IMPORTANT RULE IN HERE
    **The checklist is true.** Every row is a real check, run on this
    machine, in this moment. A row that cannot be checked in the time
    available says so — "EN ESPERA" — and never says "LISTO". A boot
    screen that lies is worse than no boot screen, because the first thing
    it teaches the user is that the rest of the interface lies too.

    That is why the model engine is never ticked at ignition: it is loaded
    lazily, on the first question, and ticking it here would be a claim
    about a request that has not been made yet.

THE COST
    One widget, one timer at 60 milliseconds, and a handful of checks that
    touch the disk and nothing else. It can be skipped with any key, and
    it skips itself if it takes longer than its own patience.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush, QColor, QLinearGradient, QPainter, QPen, QRadialGradient,
)
from PyQt6.QtWidgets import QWidget

from gui import fx
from gui.decode import DecodeText
from gui.style import mono_font, px_font
from gui.theme import APP_PROTOCOL, C

from core.localization import t as _t

# The three states a row can be in. There is no fourth: a check that did
# not happen is "EN ESPERA", never "LISTO".
OK = "ok"
FAIL = "fail"
PENDING = "pending"

_MARK = {
    OK: ("✓", "LISTO", C.GREEN),
    FAIL: ("✕", "FALLO", C.RED),
    PENDING: ("—", "EN ESPERA", C.TEXT_DIM),
}

ROW_MS = 190            # one row every this many milliseconds
FADE_MS = 420           # how long the overlay takes to go away
# THE IGNITION WAITS FOR THE MACHINE TO SAY HOW IT IS.
#
# The GPU counters are counted one process and one engine at a time and
# take three to twelve seconds, depending on how busy the machine is.
# The ignition stays until they land, so the four numbers on the screen
# are the four real ones — and not a row of "EN ESPERA" that nobody
# would believe. Nine seconds is the ceiling: past that the show is
# costing more than it is worth, and any key skips it anyway.
MAX_MS = 9000           # it gives up on itself after this long
VITALS_BEAT_MS = 400    # the pause between the last row and the door opening
VITALS_WAIT_MS = 8000   # how long it waits for the signs of life to land


def read_vitals() -> dict:
    """The machine's signs of life, as they are right now.

    The same numbers the panel on the right shows, read from the same
    place, so the ignition cannot say one thing and the interface another.

    A number that has not been measured yet is NOT zero: it is -1, and the
    ignition writes "EN ESPERA" for it. A meter that starts at zero is a
    meter that is lying for the first second of the life of the program.
    """
    out = {"cpu": -1.0, "mem": -1.0, "gpu": -1.0, "tmp": -1.0}
    try:
        from gui.metrics import _metrics
        snap = _metrics.snapshot()
    except Exception:
        return out
    for key in ("cpu", "mem", "gpu", "tmp"):
        try:
            out[key] = float(snap.get(key, -1.0))
        except Exception:
            continue
    return out


def collect_checks(probe_audio: bool = True) -> list[tuple[str, str, str]]:
    """Run the real checks and return them as (label, state, detail).

    Nothing here touches the network and nothing here takes long: the
    ignition has to be over before the user gets bored of it.

    `probe_audio` asks the sound card what it has, and that loads
    PortAudio — a native library. A program that only wanted to look at
    the microphone does not need it loaded for that, and a test process
    that loads a native audio library it is not going to use is a test
    process with one more way to fall over.
    """
    checks: list[tuple[str, str, str]] = []

    # 1. The configuration.
    try:
        from memory import config_manager as cm
        cfg = cm.load_api_keys() or {}
        providers = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
        # The key lives in the Credential Locker, not in the settings file.
        has_key = cm.has_gemini_key()
        local = bool(cfg.get("local_only")) or str(providers.get("mode") or "").lower() == "pipeline"
        if has_key or local:
            checks.append(("NÚCLEO DE IA", OK, "clave en su sitio" if has_key else "motor local"))
        else:
            checks.append(("NÚCLEO DE IA", PENDING, "falta configurar la clave"))
    except Exception as exc:                                   # noqa: BLE001
        checks.append(("NÚCLEO DE IA", FAIL, type(exc).__name__))

    # 2. The memory on disk.
    try:
        from pathlib import Path
        base = Path(__file__).resolve().parent.parent
        mem = base / "memory" / "long_term.json"
        if mem.exists():
            checks.append(("MEMORIA", OK, f"{mem.stat().st_size} bytes"))
        else:
            checks.append(("MEMORIA", PENDING, "todavía no hay nada escrito"))
    except Exception as exc:                                   # noqa: BLE001
        checks.append(("MEMORIA", FAIL, type(exc).__name__))

    # 3. The screen.
    try:
        from PyQt6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if screen is not None:
            size = screen.size()
            checks.append(("PANTALLA", OK, f"{size.width()}×{size.height()}"))
        else:
            checks.append(("PANTALLA", FAIL, "sin pantalla"))
    except Exception as exc:                                   # noqa: BLE001
        checks.append(("PANTALLA", FAIL, type(exc).__name__))

    # 4. The microphone.
    if not probe_audio:
        checks.append(("AUDIO", PENDING, "no se ha comprobado"))
    else:
        try:
            import sounddevice as sd
            inputs = [d for d in sd.query_devices() if d.get("max_input_channels", 0) > 0]
            if inputs:
                checks.append(("AUDIO", OK, f"{len(inputs)} entradas"))
            else:
                checks.append(("AUDIO", FAIL, "sin micrófono"))
        except Exception as exc:                                   # noqa: BLE001
            checks.append(("AUDIO", PENDING, type(exc).__name__))

    # 5. The camera — the eye of the gesture control.
    try:
        import cv2
        checks.append(("VISIÓN", OK, f"OpenCV {cv2.__version__.split()[0]}"))
    except Exception as exc:                                   # noqa: BLE001
        checks.append(("VISIÓN", PENDING, type(exc).__name__))

    # 6. The network is deliberately not checked.
    #
    # A ping at ignition is a second of waiting to learn something the
    # assistant will find out the first time it is asked something. The row
    # says "EN ESPERA", which is the truth.
    checks.append(("RED", PENDING, "se comprueba al usarla"))
    return checks


class BootOverlay(QWidget):
    """The ignition screen.

    It covers the whole interface, plays its checklist, and goes away. It
    can be skipped with any key or a click, because a user who has seen it
    once does not need it twice.
    """

    finished = pyqtSignal()

    def __init__(self, parent=None, checks: list[tuple[str, str, str]] | None = None,
                 brand: str = "T.E.E.M.O."):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self._checks = list(checks if checks is not None else collect_checks())
        self._brand = str(brand or "T.E.E.M.O.")
        self._shown = 0
        self._elapsed = 0
        self._alpha = 1.0
        self._done = False
        self._started_at = 0.0
        # The signs of life of the machine, and when they were asked for.
        self._vitals = read_vitals()
        self._vitals_asked = 0
        self._ending = False

        self._wordmark = DecodeText(self._brand, self, size=38, colour=C.PRI)
        self._wordmark.setMinimumHeight(68)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._clock)
        self._tick_timer.start(40)

    # ── the sequence ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Begin the ignition."""
        self._wordmark.set_text(self._brand)
        self._timer.start(ROW_MS)
        self._done = False
        self._ending = False
        self._alpha = 1.0
        self._started_at = time.monotonic()
        self._vitals_asked = 0
        self.show()
        self.raise_()
        self.update()

    def elapsed_ms(self) -> int:
        """How long the ignition has been up, in real milliseconds.

        THE WALL CLOCK, NOT THE TICKER. A timer that fires late would
        stretch the ignition: a machine that is busy would keep the user
        waiting longer than the patience says, and a machine that is idle
        would cut the show short. The ignition is timed against the
        clock, not against itself.
        """
        if not self._started_at:
            return 0
        return int((time.monotonic() - self._started_at) * 1000)

    def skip(self) -> None:
        """Go away now."""
        self._timer.stop()
        self._done = True
        self._ending = True
        self._alpha = 0.0
        self.update()
        self.hide()
        self.finished.emit()

    def _tick(self) -> None:
        """One more row of the checklist."""
        if self._shown < len(self._checks):
            self._shown += 1
        self._read_vitals()
        self.update()
        if self._shown >= len(self._checks):
            self._timer.stop()
            self._maybe_end()

    def _read_vitals(self) -> None:
        """Ask the machine how it is, right now."""
        if self._vitals_asked == 0:
            self._vitals_asked = self.elapsed_ms()
        self._vitals = read_vitals()

    def _vitals_complete(self) -> bool:
        """Are the signs of life all in?

        The processor and the memory are known at once. The temperature
        takes six hundredths of a second. The GPU counters take four
        seconds — they are counted one process and one engine at a time —
        and until they land the ignition says so instead of inventing a
        number.
        """
        v = self._vitals
        return (v.get("gpu", -1.0) >= 0.0) and (v.get("tmp", -1.0) > 0.0)

    def _maybe_end(self) -> None:
        """The checklist is done. The door opens when the machine has said
        how it is — or when the ignition runs out of patience."""
        if self._ending:
            return
        waited = self.elapsed_ms() - self._vitals_asked
        if not self._vitals_complete() and waited < VITALS_WAIT_MS:
            # The signs are still coming: keep the timer going and look
            # again in a moment.
            self._timer.start(ROW_MS)
            return
        self._ending = True
        # The last row is up: a beat, and then the interface opens.
        QTimer.singleShot(VITALS_BEAT_MS, self.skip)

    def _clock(self) -> None:
        self._elapsed += 40
        if self.elapsed_ms() > MAX_MS and not self._done:
            self.skip()

    # ── the painting ────────────────────────────────────────────────────────

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        rect = QRectF(0, 0, W, H)

        grad = QLinearGradient(0, 0, 0, H)
        grad.setColorAt(0.0, QColor(C.DEEP_TOP))
        grad.setColorAt(0.5, QColor(C.BG))
        grad.setColorAt(1.0, QColor(C.DEEP_BOT))
        p.fillRect(rect, QBrush(grad))
        fx.grid(p, rect, C.PRI, spacing=46, alpha=10)
        fx.falloff(p, rect, strength=0.45)
        fx.scanlines(p, rect, C.PRI, pitch=3, alpha=7)
        # The brackets are inset: drawn on the very edge they are half
        # outside the window, and a bracket that is half outside is a
        # bracket that is half missing.
        fx.brackets(p, QRectF(7, 7, W - 14, H - 14), C.PRI, size=60, alpha=170)

        # The wordmark, in the middle, resolving.
        self._layout()

        # A pool of light under the wordmark, so it sits in something.
        glow = QRadialGradient(QPointF(W / 2, H * 0.30 + self._mark_h / 2),
                               max(W, H) * 0.30)
        glow.setColorAt(0.0, fx.qcol(C.PRI, 46))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(rect, QBrush(glow))

        # The line under the wordmark: what is happening, in the language
        # the user reads.
        p.setFont(px_font(10, spacing=3.0))
        p.setPen(QPen(QColor(C.TEXT_DIM)))
        p.drawText(QRectF(0, int(H * 0.30) + self._mark_h + 6, W, 18),
                   Qt.AlignmentFlag.AlignHCenter, _t("window.boot_starting"))

        # The checklist, under the wordmark.
        top = int(H * 0.30) + self._mark_h + 44
        row_h = 34
        left = W / 2 - 250
        p.setFont(px_font(11, spacing=1.6))
        for index, (label, state, detail) in enumerate(self._checks):
            if index >= self._shown:
                break
            y = top + index * row_h
            if y > H - 90:
                break
            glyph, word, colour = _MARK.get(state, _MARK[PENDING])
            p.setPen(QPen(QColor(colour)))
            p.drawText(int(left), int(y), glyph)
            p.setPen(QPen(QColor(C.TEXT)))
            p.drawText(int(left + 26), int(y), label)
            p.setPen(QPen(QColor(C.TEXT_DIM)))
            p.setFont(px_font(10, mono=True))
            p.drawText(int(left + 26), int(y) + 15, detail)
            p.setFont(px_font(11, spacing=1.6))
            # The rule that fills in as the row is accepted.
            p.setPen(QPen(fx.qcol(colour, 110), 1))
            p.drawLine(int(left), int(y) + 22, int(left + 500), int(y) + 22)

        # The signs of life of the machine, read while it starts.
        self._paint_vitals(p, left, H)

        # The scale along the bottom: the progress of the ignition.
        total = max(1, len(self._checks))
        done = min(self._shown, total)
        bar = QRectF(left, H - 56, 500, 3)
        p.setPen(QPen(fx.qcol(C.PRI, 60), 1))
        p.drawLine(int(bar.left()), int(bar.top()), int(bar.right()), int(bar.top()))
        filled = bar.left() + bar.width() * (done / total)
        p.setPen(QPen(QColor(C.PRI), 2))
        p.drawLine(int(bar.left()), int(bar.top()), int(filled), int(bar.top()))
        p.setFont(mono_font(9))
        p.setPen(QPen(QColor(C.TEXT_DIM)))
        p.drawText(QRectF(left, H - 46, 500, 16),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   f"{done}/{total}")
        p.drawText(QRectF(left, H - 46, 500, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   APP_PROTOCOL)
        p.end()

    # ── the layout ──────────────────────────────────────────────────────────

    _mark_h = 68

    def _paint_vitals(self, p, left: int, H: int) -> None:
        """The vitals, along the bottom, as they are read.

        CPU, memory, GPU and temperature — the four numbers the panel on
        the right shows, read from the same place, so the ignition cannot
        say one thing and the interface another.

        A number that has not arrived yet says "EN ESPERA". It does not
        say zero: zero is a measurement, and a machine that has not been
        measured is not a machine with nothing to report.
        """
        v = self._vitals
        items = (
            ("CPU",  v.get("cpu", -1.0), "{:.0f}%"),
            ("MEM",  v.get("mem", -1.0), "{:.0f}%"),
            ("GPU",  v.get("gpu", -1.0), "{:.0f}%"),
            ("TEMP", v.get("tmp", -1.0),  "{:.0f}°C"),
        )
        y = H - 84
        p.setFont(px_font(9, spacing=1.2))
        x = int(left)
        for label, value, fmt in items:
            p.setPen(QPen(QColor(C.TEXT_DIM)))
            p.drawText(x, y, label)
            p.setFont(mono_font(12))
            if value < 0:
                p.setPen(QPen(QColor(C.TEXT_DIM)))
                p.drawText(x + 40, y, "EN ESPERA")
            else:
                p.setPen(QPen(QColor(C.PRI)))
                p.drawText(x + 40, y, fmt.format(value))
            p.setFont(px_font(9, spacing=1.2))
            x += 152

    def _layout(self) -> None:
        """Put the wordmark where it belongs.

        Done outside the paint event: moving a widget while it is being
        painted is how you get a paint event that paints itself.
        """
        W, H = self.width(), self.height()
        mark_w = max(220, min(W - 80, 460))
        self._wordmark.setGeometry(int((W - mark_w) / 2), int(H * 0.30), mark_w, self._mark_h)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout()

    # ── the input ───────────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        self.skip()
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        self.skip()
        super().mousePressEvent(event)
