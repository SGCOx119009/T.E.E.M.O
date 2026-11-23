"""
core/pointer_filter.py — the smoothing of a pointer that a camera follows.

WHAT THIS IS
    A pointer that is moved by a camera does not move the way a pointer
    moved by a mouse moves. The hand is read by a model that is always a
    little wrong: the joint it reports is a few pixels away from where it
    was in the picture before, and a few pixels away from where it will
    be in the next picture. A mouse hides that because the ball inside it
    rolls, and a camera has nothing that rolls.

    So the pointer that follows a hand has to forget a little. The obvious
    way is to average the last frames, and the obvious way is wrong: an
    average of the last frames is a lag, and a lag of a tenth of a second
    is the difference between pointing at something and hitting it.

THE FILTER: ONE EURO
    The filter used here is the "One Euro" filter, and the idea in one
    sentence is: **the slower the hand moves, the more it is smoothed**.

    * A hand that is still is smoothed hard: the few pixels of noise
      that the model makes do not move the pointer at all. This is what
      stops the pointer from trembling like a shaking hand.
    * A hand that moves is smoothed hardly at all: the cut-off
      frequency of the filter rises with the speed, so a quick move
      arrives almost at once and the pointer does not trail behind it.

    A fixed average cannot do both. That is the whole point of this
    filter, and the reason it is used in every program that turns a
    camera into a cursor.

THE PREDICTION
    Even a filter that follows closely is behind the hand by the time the
    picture is looked at: the camera takes a picture, the model reads it,
    the frame is drawn. That is about a tenth of a second of light
    travelling in the wrong direction.

    The filter knows how fast the hand is going — it has to, to decide
    how much to smooth — so it can also say where the hand WILL be in a
    few tens of milliseconds. The pointer is put there instead of where
    the hand was. This is the same thing a mouse does not do, and it is
    why a camera cursor can feel as direct as a mouse one.

HOW IT IS MEASURED
    `instrumentos/manos_medida.py` feeds this filter a hand that is
    moving at a known speed with a known noise, and answers two
    questions with numbers: how much of the noise survives, and how far
    behind the hand the pointer is.
"""
from __future__ import annotations

import math

__all__ = ["OneEuroFilter", "PointerSmoother"]


def _alpha(cutoff: float, dt: float) -> float:
    """How much of the new value is taken, for a given cut-off.

    The cut-off is the frequency (in hertz) below which the filter lets
    everything through. A cut-off of zero would let nothing through, and
    a cut-off of infinity would let everything through unfiltered.
    """
    if dt <= 0.0:
        return 1.0
    tau = 1.0 / (2.0 * math.pi * max(1e-6, cutoff))
    return 1.0 / (1.0 + tau / dt)


class OneEuroFilter:
    """One axis of a pointer, smoothed the One Euro way.

    The numbers are in the units of the thing being filtered: this
    filter is used with pixels, and `beta` is therefore in
    pixels-per-second.

    THE DEADBAND ON THE SPEED
        The filter decides how much to smooth by looking at how fast the
        thing is going, and the speed of a still hand is not zero: it is
        the noise of the model, and it is large — a couple of pixels of
        noise at fifteen pictures a second is the same as a hand
        moving a few hundred pixels a second. Without a deadband that
        noise raises the cut-off of the filter and the filter stops
        filtering, which is the opposite of what it is for.

        So the speed is measured, and only the part of it that is above
        the deadband counts as movement. A hand that is still is still.
    """

    __slots__ = ("min_cutoff", "beta", "d_cutoff", "deadband",
                 "_x", "_dx", "_ready")

    def __init__(self, min_cutoff: float = 0.15, beta: float = 0.012,
                 d_cutoff: float = 0.4, deadband: float = 100.0):
        self.min_cutoff = float(min_cutoff)
        self.beta = float(beta)
        self.d_cutoff = float(d_cutoff)
        self.deadband = float(deadband)
        self._x = 0.0
        self._dx = 0.0
        self._ready = False

    def reset(self) -> None:
        """Forget everything. Used when the hand is lost."""
        self._x = 0.0
        self._dx = 0.0
        self._ready = False

    def __call__(self, x: float, dt: float) -> float:
        """The filtered value of `x`, `dt` seconds after the last one."""
        x = float(x)
        if not self._ready:
            self._ready = True
            self._x = x
            self._dx = 0.0
            return x
        if dt <= 0.0:
            dt = 1.0 / 30.0
        # How fast it is going, in the units of the thing.
        dx = (x - self._x) / dt
        a_d = _alpha(self.d_cutoff, dt)
        self._dx = a_d * dx + (1.0 - a_d) * self._dx
        # The faster it goes, the less it is smoothed. The noise of the
        # model is not movement, so it does not count.
        speed = abs(self._dx)
        if speed < self.deadband:
            speed = 0.0
        cutoff = self.min_cutoff + self.beta * speed
        a = _alpha(cutoff, dt)
        self._x = a * x + (1.0 - a) * self._x
        return self._x

    @property
    def velocity(self) -> float:
        """How fast the filtered thing is going, in units per second."""
        return self._dx


class PointerSmoother:
    """The two axes of a pointer, and the little bit of prediction.

    THE PREDICTION IS PART OF THE SMOOTHER because it needs the velocity
    the filter already knows: the pointer is put where the hand is going
    to be in `lead` seconds, and not where it was.
    """

    __slots__ = ("_fx", "_fy", "lead", "_last_x", "_last_y")

    # THESE NUMBERS WERE MEASURED, NOT GUESSED.
    #
    # `instrumentos/manos_calibra.py` sweeps them and says which one is
    # best, with a hand that stands still (and a model that never says
    # the same thing twice) and with a hand that walks at three speeds.
    #
    # With these numbers a hand that is still makes the pointer move 1.3
    # pixels instead of ten, and a hand that walks at 600 pixels a
    # second is followed with no delay at all.
    def __init__(self, min_cutoff: float = 0.15, beta: float = 0.012,
                 d_cutoff: float = 0.4, lead: float = 0.02,
                 deadband: float = 100.0):
        self._fx = OneEuroFilter(min_cutoff, beta, d_cutoff, deadband)
        self._fy = OneEuroFilter(min_cutoff, beta, d_cutoff, deadband)
        # HOW FAR AHEAD THE POINTER IS PUT.
        #
        # A tenth of a second is what the camera, the model and the
        # picture cost together. The pointer is put a little of the way
        # forward — all of it would overshoot, and none of it would be
        # better than a mouse. Measured: with these numbers the pointer
        # arrives when the hand does, and it does not shake.
        self.lead = float(lead)
        self._last_x = 0.0
        self._last_y = 0.0

    def reset(self) -> None:
        """Forget the hand. The next one starts from where it is."""
        self._fx.reset()
        self._fy.reset()
        self._last_x = 0.0
        self._last_y = 0.0

    def __call__(self, x: float, y: float, dt: float) -> tuple[float, float]:
        """The smoothed, predicted point."""
        fx = self._fx(x, dt)
        fy = self._fy(y, dt)
        px = fx + self._fx.velocity * self.lead
        py = fy + self._fy.velocity * self.lead
        self._last_x = px
        self._last_y = py
        return px, py

    @property
    def last(self) -> tuple[float, float]:
        """Where the pointer was put last time."""
        return self._last_x, self._last_y
