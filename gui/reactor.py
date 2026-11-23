"""gui/reactor.py — the reactor core, drawn in three dimensions.

The flat ring stack it replaces was a picture of a machine; this is a small
model of one. A holographic sphere sits above a projector platform, two
gyroscope rings turn through it on different axes, and an orbit ring carries
three tracers round the equator. Everything is projected from real 3-D points
through a camera tilted down at the scene, so the rings foreshorten into
ellipses, pass behind the sphere and come out in front of it.

Depth is what sells it, and depth is cheap: every line segment knows how far
it is from the camera. Segments behind the sphere are drawn before it and
dimmer; segments in front are drawn after it and brighter. That ordering and
that falloff are the whole trick — no z-buffer, no GPU, no new dependency.

What does not move is not redrawn. The platform's rings and the globe's lines
of latitude turn about the vertical axis, and a circle turned about its own
axis is the same circle — so they are drawn once into two cached layers and
blitted every frame after that. Only the gyroscope rings, the meridians and
the things that follow the voice are drawn per frame. Measured on the first
version, which redrew everything, this halves the cost of a frame.

Like the face in core/avatar.py it is software QPainter, it takes its colours
as arguments so the accent picker retints it, and everything that moves means
something: the spin rate is the assistant's state, the sphere's inner light
and the platform's spectrum ticks are the real audio level.
"""
from __future__ import annotations

import math

import numpy as np
from PyQt6.QtCore import QLineF, QPointF, QRectF, Qt
from PyQt6.QtGui import (QBrush, QColor, QFont, QLinearGradient, QPainter, QPen, QPixmap,
                         QRadialGradient)

from gui.style import px_font

_PITCH = math.radians(17.0)     # the camera looks down at the platform
_CAM_D = 4.2                    # perspective distance, in core radii
_N_RING = 72                    # points on a ring
_N_GLOBE = 44                   # points on a globe line
_FLOOR_Y = -1.02                # the platform, below the sphere's centre
_SPHERE = 0.46                  # sphere radius, in core radii

# HOW FAST THE MACHINE TURNS, AND HOW MUCH DUST IT SHOWS, AT EACH STEP OF THE
# LADDER (core/hardware_equilibrium.py). A busy machine is given a smaller
# picture and a slower one: the presentation degrades BEFORE the capacity.
_LEVEL_RATE = {"OPTIMAL": 1.0, "BALANCED": 0.80, "THROTTLED": 0.55,
               "CRITICAL_RECOVERY": 0.30}
_LEVEL_MOTES = {"OPTIMAL": 1.0, "BALANCED": 0.70, "THROTTLED": 0.40,
                "CRITICAL_RECOVERY": 0.15}


def _rx(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def _ry(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def _rz(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def _with_alpha(col: QColor, a: float) -> QColor:
    c = QColor(col)
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


class Reactor3D:
    """State + renderer. `step()` advances it, `paint()` draws a frame."""

    # Vertical extent in core radii: the highest mote above the centre, and the
    # bottom of the name below it. Measured through the projection, not guessed —
    # the platform's front edge is nearer the camera, so perspective enlarges it.
    TOP = 1.05
    BOTTOM = 2.05

    def __init__(self):
        t = np.linspace(0.0, 2 * math.pi, _N_RING, endpoint=False)
        self._ring = np.vstack([np.cos(t), np.zeros_like(t), np.sin(t)])   # unit circle, XZ plane
        g = np.linspace(0.0, 2 * math.pi, _N_GLOBE, endpoint=False)
        self._glat = []
        for lat in (-58, -30, 0, 30, 58):
            la = math.radians(lat)
            self._glat.append(np.vstack([np.cos(g) * math.cos(la),
                                         np.full_like(g, math.sin(la)),
                                         np.sin(g) * math.cos(la)]))
        self._glon = []
        for k in range(6):
            lo = k * math.pi / 6
            self._glon.append(np.vstack([np.cos(g) * math.cos(lo),
                                         np.sin(g),
                                         np.cos(g) * math.sin(lo)]))
        rng = np.random.default_rng(57)
        self._motes = [(float(rng.uniform(1.18, 1.55)), float(rng.uniform(0, 2 * math.pi)),
                        float(rng.uniform(-0.55, 0.55)), float(rng.uniform(0.25, 0.9)))
                       for _ in range(22)]
        self.spin = 0.0
        self._view = _rx(_PITCH)
        self._cache_key = None
        self._under: QPixmap | None = None      # platform + back half of the latitudes
        self._lat_front: QPixmap | None = None  # front half of the latitudes
        self._box = QRectF()

        # THE STATE OF MIND AND THE STATE OF THE MACHINE.
        #
        # The core is not a picture that happens to move: how it moves says
        # what kind of mind the assistant is in, and how much of it there is
        # to draw says how busy the machine is. Both are numbers, both come
        # from outside, and neither one is allowed to break the frame.
        self._emo_animation = "still"
        self._emo_params: dict[str, float] = {}
        self._emo_tempo = 1.0
        self._level = "OPTIMAL"
        self._t = 0.0

    # ── what the state and the machine ask of the movement ───────────────────
    def set_emotional_state(self, state) -> None:
        """Take the affective state: it changes the movement, not the colour.

        The state may be given as an `EmotionalState` or as its name; a name
        that is not a state leaves the core exactly as it was."""
        if isinstance(state, str):
            try:
                from core.emotional_spectrum import state_by_name
                state = state_by_name(state)
            except Exception:
                return
        animation = getattr(state, "animation", None)
        if not animation:
            return
        self._emo_animation = str(animation)
        try:
            self._emo_params = dict(getattr(state, "animation_params", {}) or {})
        except Exception:
            self._emo_params = {}
        try:
            self._emo_tempo = float(getattr(state, "tempo", 1.0) or 1.0)
        except (TypeError, ValueError):
            self._emo_tempo = 1.0

    def apply_resource_state(self, state) -> None:
        """Take a reading of the machine: less dust and a slower turn when it
        is busy. The frame gets cheaper as the load goes up, never dearer."""
        level = str(getattr(state, "level", "") or "")
        if level:
            self._level = level

    # ── motion ────────────────────────────────────────────────────────────────
    def step(self, dt: float, speaking: bool, thinking: bool, muted: bool) -> None:
        dt = min(0.10, max(0.0, dt))
        self._t += dt
        rate = 0.12 if muted else 0.38 + (1.5 if thinking else 0.0) + (0.8 if speaking else 0.0)
        # The state of mind sets the tempo of the whole machine.
        rate *= max(0.2, min(1.8, self._emo_tempo))
        if self._emo_animation == "still":
            rate *= 0.22            # a still mind barely turns anything
        elif self._emo_animation == "stutter":
            # it moves in bursts: it holds, and then it jumps
            try:
                period = max(0.12, float(self._emo_params.get("period_ms", 900.0)) / 1000.0)
            except (TypeError, ValueError):
                period = 0.9
            phase = (self._t % period) / period
            rate *= (3.2 if phase < 0.35 else 0.0)
        # The ladder of the machine.
        rate *= _LEVEL_RATE.get(self._level, 1.0)
        self.spin += dt * rate

    # ── projection ────────────────────────────────────────────────────────────
    def _project(self, pts, cx, cy, r):
        v = self._view @ pts
        s = _CAM_D / (_CAM_D - v[2])
        return cx + v[0] * s * r, cy - v[1] * s * r, v[2], s

    @staticmethod
    def _cued(p, X, Y, Z, col, a_front, a_back, width, part):
        """Draw one side of a closed polyline — the half behind the depth plane
        or the half in front of it — in three brightness buckets."""
        zm = (Z + np.roll(Z, -1)) * 0.5
        if part == "back":
            mask = zm < 0
            if not mask.any():
                return
            alpha = a_back * (0.35 + 0.65 * np.maximum(1.0 + zm, 0.0))
        else:
            mask = zm >= 0
            if not mask.any():
                return
            alpha = a_front * (0.55 + 0.45 * np.minimum(zm, 1.0))
        idx = np.flatnonzero(mask)
        av = alpha[idx]
        top = float(av.max())
        levels = np.minimum((av * (2.999 / max(1e-6, top))).astype(np.int8), 2).tolist()
        xs, ys = X[idx].tolist(), Y[idx].tolist()
        xe, ye = np.roll(X, -1)[idx].tolist(), np.roll(Y, -1)[idx].tolist()
        buckets = ([], [], [])
        for i, lv in enumerate(levels):
            buckets[lv].append(QLineF(xs[i], ys[i], xe[i], ye[i]))
        for k, lines in enumerate(buckets):
            if lines:
                pen = QPen(_with_alpha(col, top * (0.45 + 0.275 * k)), width)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                p.drawLines(lines)

    # ── the parts that never change shape, drawn once ─────────────────────────
    def _layer(self, dpr: float) -> tuple[QPixmap, QPainter]:
        pm = QPixmap(max(1, int(self._box.width() * dpr)), max(1, int(self._box.height() * dpr)))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        lp = QPainter(pm)
        lp.setRenderHint(QPainter.RenderHint.Antialiasing)
        lp.translate(-self._box.left(), -self._box.top())
        return pm, lp

    def _build_layers(self, cx, cy, r, main, dpr) -> None:
        self._box = QRectF(cx - r * 2.1, cy - r * 1.3, r * 4.2, r * 3.4)

        pm, p = self._layer(dpr)
        fx, fy, _, _ = self._project(np.array([[0.0], [_FLOOR_Y], [0.0]]), cx, cy, r)
        fx, fy = float(fx[0]), float(fy[0])
        # the lit disc of the platform
        p.save()
        p.translate(fx, fy)
        p.scale(1.0, math.sin(_PITCH) * 1.25)
        glow_r = r * 1.3
        g = QRadialGradient(0, 0, glow_r)
        g.setColorAt(0.0, _with_alpha(main, 0.24))
        g.setColorAt(0.45, _with_alpha(main, 0.08))
        g.setColorAt(1.0, _with_alpha(main, 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(0, 0), glow_r, glow_r)
        p.restore()
        # its rings
        for rr, a, w in ((1.20, 0.55, 1.4), (1.02, 0.32, 1.0), (0.74, 0.40, 1.2), (0.46, 0.22, 1.0)):
            pts = self._ring * rr
            pts[1] += _FLOOR_Y
            X, Y, Z, _ = self._project(pts, cx, cy, r)
            self._cued(p, X, Y, Z, main, a, a * 0.75, w, "back")
            self._cued(p, X, Y, Z, main, a, a * 0.75, w, "front")
        # the far half of the globe's latitudes
        lats = [self._project(pts * _SPHERE, cx, cy, r) for pts in self._glat]
        for X, Y, Z, _ in lats:
            self._cued(p, X, Y, Z, main, 0.5, 0.16, 1.0, "back")
        p.end()
        self._under = pm

        pm, p = self._layer(dpr)
        for X, Y, Z, _ in lats:
            self._cued(p, X, Y, Z, main, 0.46, 0.16, 1.0, "front")
        p.end()
        self._lat_front = pm

    # ── frame ─────────────────────────────────────────────────────────────────
    def paint(self, p: QPainter, cx: float, cy: float, r: float, main: QColor, acc: QColor,
              white: QColor, amp: float, live: bool, name: str = "") -> None:
        spin = self.spin
        amp = max(0.0, min(1.0, amp))
        # THE STATE OF MIND, in numbers: the period it beats at, how hard it
        # beats, and where it is in the beat right now.
        emo = self._emo_animation
        try:
            params = self._emo_params or {}
            period = max(0.12, float(params.get("period_ms", 1600.0)) / 1000.0)
            eamp = max(0.0, min(1.0, float(params.get("amplitude", 0.5))))
        except (TypeError, ValueError):
            period, eamp = 1.6, 0.5
        ephase = (self._t % period) / period
        ewave = math.sin(ephase * math.tau)
        # A stuttering state breaks the light into bursts: the core is bright
        # for a moment and almost dark for the next.
        light = eamp if (emo != "stutter" or ephase < 0.30) else eamp * 0.20
        dev = p.device()
        dpr = float(dev.devicePixelRatioF()) if dev is not None else 1.0
        key = (round(cx), round(cy), round(r, 1), main.rgba(), dpr)
        if key != self._cache_key or self._under is None:
            self._build_layers(cx, cy, r, main, dpr)
            self._cache_key = key

        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.drawPixmap(self._box.topLeft(), self._under)

        # spectrum ticks on the platform rim: the voice, standing up off the floor
        n_t = 64
        ang = np.linspace(0, 2 * math.pi, n_t, endpoint=False) + spin * 0.25
        wob = 0.5 + 0.5 * np.sin(ang * 5.0 + spin * 6.0) * np.cos(ang * 3.0 - spin * 2.3)
        h = 0.03 + (0.20 * amp * wob if live else 0.0)
        base = np.vstack([np.cos(ang) * 1.20, np.full(n_t, _FLOOR_Y), np.sin(ang) * 1.20])
        tip = base.copy()
        tip[1] += h
        bX, bY, bZ, _ = self._project(base, cx, cy, r)
        tX, tY, _, _ = self._project(tip, cx, cy, r)
        bXl, bYl, tXl, tYl = bX.tolist(), bY.tolist(), tX.tolist(), tY.tolist()
        front = (bZ >= 0).tolist()
        col_t = acc if live else main
        for want, a in ((False, 0.25), (True, 0.65)):
            lines = [QLineF(bXl[i], bYl[i], tXl[i], tYl[i]) for i in range(n_t) if front[i] == want]
            if lines:
                p.setPen(QPen(_with_alpha(col_t, a * (0.5 + amp)), 1.4))
                p.drawLines(lines)

        # light beam from the platform up into the core: bright on its axis,
        # fading to nothing at its sides and towards the top
        fx, fy, _, _ = self._project(np.array([[0.0], [_FLOOR_Y], [0.0]]), cx, cy, r)
        fx, fy = float(fx[0]), float(fy[0])
        beam_w = r * 0.30
        top = cy - r * 0.05
        hg = QLinearGradient(fx - beam_w, 0, fx + beam_w, 0)
        hg.setColorAt(0.0, _with_alpha(main, 0.0))
        hg.setColorAt(0.5, _with_alpha(main, 0.16 + 0.22 * amp))
        hg.setColorAt(1.0, _with_alpha(main, 0.0))
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(hg))
        steps, hgt = 8, fy - top
        for k in range(steps):
            p.setOpacity(((k + 1) / steps) ** 1.6)
            p.drawRect(QRectF(fx - beam_w, top + hgt * k / steps, beam_w * 2, hgt / steps + 0.5))
        p.restore()

        # the rings: two gyroscope rings and the equatorial orbit
        rings = (
            (self._project(_ry(spin * 0.9) @ _rz(math.radians(64)) @ (self._ring * 0.90), cx, cy, r),
             acc, 0.80, 1.7),
            (self._project(_ry(-spin * 1.25) @ _rx(math.radians(58)) @ (self._ring * 0.78), cx, cy, r),
             main, 0.62, 1.3),
            (self._project(self._ring * 1.08, cx, cy, r), main, 0.45, 1.1),
        )
        # meridians turn with the globe; the latitudes are in the cached layers
        spin_g = _ry(spin * 0.6)
        meridians = [self._project(spin_g @ (pts * _SPHERE), cx, cy, r) for pts in self._glon]

        for (X, Y, Z, _s), col, a, w in rings:
            self._cued(p, X, Y, Z, col, a, a * 0.55, w, "back")
        for X, Y, Z, _s in meridians:
            self._cued(p, X, Y, Z, main, 0.5, 0.16, 1.0, "back")

        # the halo of the state: a wide, soft light around the core that
        # breathes at the state's own period. It is the difference between a
        # core that is lit and a core that is ALIVE.
        if emo == "halo":
            hrad = r * 0.62 * (1.0 + 0.22 * eamp * ewave)
            hgr = QRadialGradient(QPointF(cx, cy), hrad)
            hgr.setColorAt(0.0, _with_alpha(main, 0.0))
            hgr.setColorAt(0.72, _with_alpha(main, 0.10 * light))
            hgr.setColorAt(1.0, _with_alpha(main, 0.0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(hgr))
            p.drawEllipse(QPointF(cx, cy), hrad, hrad)

        # the sweep of the state: an arc running round the core.
        if emo == "ring_sweep":
            srad = r * 1.22
            sbox = QRectF(cx - srad, cy - srad, srad * 2, srad * 2)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(_with_alpha(acc if live else main, 0.30 + 0.45 * light), 1.8))
            p.drawArc(sbox, int(-ephase * 360.0 * 16), int((50.0 + 120.0 * eamp) * 16))

        # the sphere — lit from the upper left, translucent like a hologram
        sr = r * _SPHERE
        shade = QRadialGradient(QPointF(cx, cy), sr, QPointF(cx - sr * 0.38, cy - sr * 0.42))
        shade.setColorAt(0.0, _with_alpha(white, 0.55 + 0.3 * amp))
        shade.setColorAt(0.18, _with_alpha(main, 0.55 + 0.25 * amp))
        shade.setColorAt(0.70, _with_alpha(main, 0.16))
        shade.setColorAt(1.0, _with_alpha(main, 0.05))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(shade))
        p.drawEllipse(QPointF(cx, cy), sr, sr)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_with_alpha(main, 0.65), 1.3))
        p.drawEllipse(QPointF(cx, cy), sr, sr)
        # the heart: brightens and swells with the voice
        pulse = 1.0 + (0.30 * eamp * ewave if emo == "pulse" else 0.0)
        hr = sr * (0.20 + 0.16 * amp) * 2.4 * pulse
        heart = QRadialGradient(cx, cy, hr)
        heart.setColorAt(0.0, _with_alpha(white, 0.95))
        heart.setColorAt(0.25, _with_alpha(acc if live else main, 0.75 * (0.4 + 0.6 * light)))
        heart.setColorAt(1.0, _with_alpha(main, 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(heart))
        p.drawEllipse(QPointF(cx, cy), hr, hr)

        p.drawPixmap(self._box.topLeft(), self._lat_front)
        for X, Y, Z, _s in meridians:
            self._cued(p, X, Y, Z, main, 0.46, 0.16, 1.0, "front")
        for (X, Y, Z, _s), col, a, w in rings:
            self._cued(p, X, Y, Z, col, a, a * 0.55, w, "front")

        # tracers riding the orbit ring
        (X, Y, Z, _s), _c, _a, _w = rings[2]
        p.setPen(Qt.PenStyle.NoPen)
        for k in range(3):
            i = int((k * _N_RING / 3 + spin * 7.0) % _N_RING)
            depth = float(Z[i])
            rad = (2.2 + 1.6 * max(0.0, depth)) * 3
            x, y = float(X[i]), float(Y[i])
            tg = QRadialGradient(x, y, rad)
            tg.setColorAt(0.0, _with_alpha(white, 0.9 if depth >= 0 else 0.35))
            tg.setColorAt(0.35, _with_alpha(acc, 0.6 if depth >= 0 else 0.2))
            tg.setColorAt(1.0, _with_alpha(acc, 0.0))
            p.setBrush(QBrush(tg))
            p.drawEllipse(QPointF(x, y), rad, rad)

        # drifting motes around the core
        #
        # How many of them there are is the machine's business: a busy
        # machine is shown less dust, because the frame has to stay cheap.
        n_motes = max(0, int(len(self._motes) * _LEVEL_MOTES.get(self._level, 1.0)))
        motes = self._motes[:n_motes]
        a = np.array([ph + spin * sp for _r0, ph, _y, sp in motes])
        r0 = np.array([m[0] for m in motes])
        pts = np.vstack([np.cos(a) * r0, np.array([m[2] for m in motes]), np.sin(a) * r0])
        MX, MY, MZ, _ = self._project(pts, cx, cy, r)
        for x, y, d in zip(MX.tolist(), MY.tolist(), MZ.tolist()):
            d = max(0.0, d)
            p.setBrush(QBrush(_with_alpha(main, 0.25 + 0.35 * d)))
            p.drawEllipse(QPointF(x, y), 1.1 + 0.8 * d, 1.1 + 0.8 * d)

        # the name, standing on the front edge of the platform
        if name:
            nx, ny, _, _ = self._project(np.array([[0.0], [_FLOOR_Y], [1.2]]), cx, cy, r)
            px = max(10, int(r * 0.11))
            f = px_font(px, weight=QFont.Weight.Light,
                        spacing=max(2.0, round(r * 0.035)))
            p.setFont(f)
            p.setPen(QPen(_with_alpha(white, 0.78 + 0.2 * min(1.0, amp * 2))))
            p.drawText(QRectF(cx - r * 1.6, float(ny[0]) + r * 0.10, r * 3.2, px * 1.8),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, name)
        p.restore()
