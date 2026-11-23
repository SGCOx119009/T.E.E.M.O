"""
core/gesture_spec.py — the four categories of the hand vocabulary.

WHAT THIS IS
    The whole gesture vocabulary of T.E.E.M.O., written down as ONE
    recogniser that reads the twenty-one points of a hand and says what
    the hand is doing. It is plain Python: no Qt, no camera, no Windows.
    That is on purpose — the vocabulary has to be testable without a
    camera and without a screen, and the only way to test a threshold is
    to be able to write the threshold down.

    It follows the standard of the hand interfaces (Apple Vision Pro,
    Meta Quest): the pinch is the button of the mouse, the palm and the
    fist are the states of the flow, the swipes are the windows, and the
    gestures that need precision are done with two hands.

THE FOUR CATEGORIES
    1. Navigation and mouse, one hand
         point                 move the cursor
         pinch                 click
         pinch (twice)         double click
         pinch held still      right click — the context menu
         pinch and move        drag and drop
         two fingers           the wheel
    2. States of control, one hand
         open palm             clutch: the cursor freezes, and T.E.E.M.O.
                              goes quiet — the safe mode
         fist                  confirm / close
         thumb up              approve
         thumb down            reject
    3. Windows and system, one hand
         three fingers sideways  switch window
         palm pushed up          maximise
         palm pushed down        minimise
         the L                   wake T.E.E.M.O. up / dictate
    4. Precision, two hands
         two opposite L's       the screenshot frame
         two pinches, one still  select an area
         two pinches, both moving  zoom
         the clap               sleep / lock the HUD

THE TWO RULES THAT MAKE IT WORK
    NORMALISATION BY SIZE. The gap of the pinch is never measured in
    pixels: it is divided by the size of the palm, so the same pinch is
    the same pinch whether the hand is near the camera or far from it.

    HYSTERESIS AND HOLD TIME. A pose has to be held still for a fifth of
    a second before it fires, and it cannot fire again for half a second.
    Without that, every gesture is fired twice — once on the way in and
    once on the way out — and the user presses Alt+Tab when they meant
    to rest their hand.

WHAT THIS IS NOT
    It does not move the mouse and it does not press any key: it says
    WHAT HAPPENED, and `gui/gesture.py` is what does it. A recogniser
    that pressed keys itself could not be tested without a computer.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

__all__ = [
    "GestureEvent",
    "HandMetrics",
    "GestureSpec",
    "GESTURES",
    "CATEGORIES",
    "PINCH_RATIO",
    "PINCH_REACH",
    "HOLD",
    "COOLDOWN",
    "LONG_PRESS",
]


# ── the numbers, and why they are these ────────────────────────────────────

# THE GAP OF THE PINCH, DIVIDED BY THE SIZE OF THE PALM.
#
# The standard of the hand interfaces: a pinch is a gap smaller than a
# quarter of the palm. Measured in pixels it would break the moment the
# user moved their arm towards the camera; measured as a ratio it does
# not move at all.
PINCH_RATIO = 0.25

# AND THE INDEX HAS TO BE OUT IN FRONT.
#
# The ratio alone is NOT enough, and this is the number that was
# measured against a real hand: a FIST has its thumb against the index
# too, so the ratio of a fist is also small. What tells them apart is
# where the index is: in a pinch the index is out in the air, far from
# the knuckle of the middle finger; in a fist it is folded onto the palm.
PINCH_REACH = 0.40

# HOW LONG A POSE HAS TO BE HELD STILL BEFORE IT FIRES.
#
# Five frames at fifteen frames a second. It is the difference between
# "the user meant it" and "the user was passing through".
HOLD = 0.20

# AND HOW LONG BEFORE IT CAN FIRE AGAIN.
COOLDOWN = 0.45

# THE PELLIZCO SOSTENIDO: the pinch that stays still for six tenths of
# a second is the other button — the context menu.
LONG_PRESS = 0.60

# TWO PINCHES THIS CLOSE TOGETHER ARE A DOUBLE CLICK.
DOUBLE_CLICK = 0.35

# EVERY THRESHOLD FROM HERE ON IS A RATIO OF THE SIZE OF THE HAND, AND
# NEVER A NUMBER OF PIXELS.
#
# A hand that is twice as far from the camera is half as big in the
# picture, and a number of pixels that means "a long way" for a hand
# close to the lens means nothing at all for a hand far from it. So the
# distances are measured in SIZES OF THE HAND: the palm is the unit, and
# the hand is always one unit big.
#
# HOW FAR THE HAND HAS TO MOVE, WHILE IT IS PINCHING, TO BE A DRAG:
# seven hundredths of the hand.
DRAG_REL = 0.07

# HOW FAST THE PALM HAS TO MOVE TO BE A PUSH, IN HAND SIZES PER SECOND.
# A deliberate push covers a hand size in a fifth of a second.
PUSH_REL = 2.0

# THE HANDS TOGETHER, FOR THE CLAP, IN HAND SIZES.
CLAP_REL = 0.6

# THE TWO HANDS OF THE ZOOM AND OF THE AREA: how far the pinch point
# has to travel, in hand sizes, for the gesture to be real.
TWO_STILL_REL = 0.07
TWO_MOVED_REL = 0.15


# THE NAMES OF THE GESTURES, AND THE CATEGORY EACH ONE BELONGS TO.
CATEGORIES: dict[str, str] = {
    # 1. navigation and mouse
    "point_move": "navigation",
    "pinch_click": "navigation",
    "pinch_double_click": "navigation",
    "pinch_right": "navigation",
    "pinch_drag": "navigation",
    "two_scroll": "navigation",
    # 2. states of control
    "open_clutch": "control",
    "fist_confirm": "control",
    "thumb_up_approve": "control",
    "thumb_down_reject": "control",
    # 3. windows and system
    "three_switch": "windows",
    "palm_maximize": "windows",
    "palm_minimize": "windows",
    "l_dictation": "windows",
    # 4. two hands
    "frame_screenshot": "precision",
    "two_pinch_area": "precision",
    "two_pinch_zoom": "precision",
    "palm_clap_sleep": "precision",
}

# EVERY GESTURE, IN THE ORDER THEY ARE WRITTEN DOWN IN THE SPECIFICATION.
GESTURES = tuple(CATEGORIES)

# THE GESTURES THAT CONTINUE WHILE THEY LAST. They are not "fired",
# they are "reported", and the cooldown does not apply to them.
CONTINUOUS = ("pinch_drag", "two_scroll")


# ── what a hand is, in numbers ──────────────────────────────────────────────

@dataclass
class HandMetrics:
    """One hand, measured."""

    points: tuple = ()
    handedness: str = ""
    palm: float = 0.0                 # the size of the hand: wrist -> middle knuckle
    centre: tuple = (0.0, 0.0)        # the palm centre, the ninth point
    pinch_ratio: float = 1.0          # the gap of the pinch, over the size of the palm
    index_reach: float = 1.0          # the index tip, over the size of the palm
    fingers: tuple = (False, False, False, False)   # index, middle, ring, pinky
    out_count: int = 0
    thumb_up: bool = False
    thumb_down: bool = False
    l_shape: bool = False
    index_up: bool = False
    pinching: bool = False
    pose: str = "blob"

    @classmethod
    def of(cls, points, handedness: str = "") -> "HandMetrics":
        """Measure a hand: twenty-one points in, the truth out."""
        pts = tuple((float(p[0]), float(p[1])) for p in points[:21])
        if len(pts) < 21:
            return cls(points=pts, handedness=handedness, pose="blob")
        wrist = pts[0]
        middle = pts[9]
        palm = math.hypot(middle[0] - wrist[0], middle[1] - wrist[1])
        if palm < 1e-6:
            # A hand with no size is not a hand: it is the tracker saying
            # something without any information in it.
            return cls(points=pts, handedness=handedness, pose="blob")
        thumb = pts[4]
        index = pts[8]
        gap = math.hypot(thumb[0] - index[0], thumb[1] - index[1])
        metrics = cls(
            points=pts,
            handedness=handedness,
            palm=palm,
            centre=(middle[0], middle[1]),
            pinch_ratio=gap / palm,
            index_reach=math.hypot(index[0] - middle[0],
                                   index[1] - middle[1]) / palm,
        )
        metrics.fingers = (
            _out(pts, 6, 8, wrist),
            _out(pts, 10, 12, wrist),
            _out(pts, 14, 16, wrist),
            _out(pts, 18, 20, wrist),
        )
        metrics.out_count = sum(1 for f in metrics.fingers if f)
        metrics.index_up = (index[1] - pts[5][1]) < -0.25 * palm
        # THE THUMB IS A POSE ON ITS OWN, AND IT IS THE ONLY THING THAT
        # SAYS WHICH WAY IT POINTS.
        thumb_vector = (thumb[0] - pts[2][0], thumb[1] - pts[2][1])
        thumb_length = math.hypot(*thumb_vector)
        pointing = thumb_length > 0.55 * palm
        if pointing:
            metrics.thumb_up = thumb_vector[1] < -0.15 * palm
            metrics.thumb_down = thumb_vector[1] > 0.15 * palm
        # THE L: THE INDEX UP AND THE THUMB SIDEWAYS.
        if (metrics.fingers[0] and not metrics.fingers[1]
                and not metrics.fingers[2] and not metrics.fingers[3]
                and pointing):
            index_vector = (index[0] - pts[5][0], index[1] - pts[5][1])
            length = math.hypot(*index_vector) or 1.0
            cosine = abs((index_vector[0] * thumb_vector[0]
                          + index_vector[1] * thumb_vector[1]) / (length * thumb_length))
            metrics.l_shape = cosine < 0.5
        metrics.pinching = (metrics.pinch_ratio < PINCH_RATIO
                            and metrics.index_reach > PINCH_REACH)
        metrics.pose = _pose_of(metrics)
        return metrics

    # -- the useful little sums ------------------------------------------

    def moved_from(self, other: "HandMetrics") -> float:
        """How far the palm went, in units of the picture."""
        return math.hypot(self.centre[0] - other.centre[0],
                          self.centre[1] - other.centre[1])

    def displacement(self, origin: tuple) -> tuple:
        """Where the palm went, as a vector."""
        return (self.centre[0] - origin[0], self.centre[1] - origin[1])


def _out(pts, pip: int, tip: int, wrist) -> bool:
    """Is the finger standing up?

    The tip has to be further from the wrist than the joint in front of
    it. A finger that curls towards the palm brings its tip CLOSER to the
    wrist than its own knuckle, so the test says which way the finger
    points without needing an angle.
    """
    a = math.hypot(pts[tip][0] - wrist[0], pts[tip][1] - wrist[1])
    b = math.hypot(pts[pip][0] - wrist[0], pts[pip][1] - wrist[1])
    return a > b * 1.12


def _pose_of(m: HandMetrics) -> str:
    """The name of the shape."""
    if m.pinching:
        # THE PINCH WINS OVER EVERYTHING.
        #
        # A hand that is pinching also has its thumb pointing upwards,
        # and the palm of a pinch has the index folded onto the thumb:
        # read the thumb first and a pinch comes out as a "thumbs up".
        # The pinch is the button of the mouse, and the mouse is what
        # the hand is doing.
        return "pinch"
    if m.l_shape:
        return "l"
    if m.out_count == 0 and m.thumb_up:
        return "thumb_up"
    if m.out_count == 0 and m.thumb_down:
        # THE THUMB DOWN IS A POSE OF ITS OWN, and not a closed hand:
        # the closed hand has the thumb tucked in against the index, and
        # this one has the thumb pointing at the floor.
        return "thumb_down"
    if m.out_count == 0:
        return "fist"
    if m.out_count == 4:
        return "open"
    if m.out_count == 1:
        return "point"
    if m.out_count == 2:
        return "two"
    if m.out_count == 3:
        return "three"
    return "hand"


# ── what happened ───────────────────────────────────────────────────────────

@dataclass
class GestureEvent:
    """One gesture that just happened."""

    name: str
    hand: int = 0
    data: dict = field(default_factory=dict)

    def __str__(self) -> str:                       # pragma: no cover
        return self.name


# ── the state of one hand between two pictures ──────────────────────────────

@dataclass
class _HandState:
    """What the recogniser remembers about one hand."""

    centre: tuple = (0.0, 0.0)
    at: float = 0.0
    velocity: tuple = (0.0, 0.0)
    pose: str = ""
    pose_since: float = 0.0
    pose_fired: bool = False
    # THE TWO-HANDED GESTURES HAVE THEIR OWN CLOCKS.
    #
    # They cannot live in the same fields as the pose: the pose of a hand
    # changes fifteen times a second, and the clap and the frame have to
    # remember that they have been there for a fifth of a second.
    clap_since: float = 0.0
    clap_fired: bool = False
    frame_since: float = 0.0
    frame_fired: bool = False
    # WHETHER THE TWO HANDS ARE PINCHING TOGETHER RIGHT NOW.
    pair_active: bool = False
    pinch_since: float | None = None
    pinch_anchor: tuple = (0.0, 0.0)
    pinch_moved: bool = False
    pinch_right_fired: bool = False
    click_at: float | None = None
    scroll_anchor: tuple | None = None


# ── the recogniser ──────────────────────────────────────────────────────────

class GestureSpec:
    """Reads hands and says what the user is doing.

    It is fed the hands of a picture and the time of that picture, and
    it returns the gestures that happened in it. It keeps the state of
    the hands between pictures: a gesture is a thing that happens over
    time, and a recogniser that forgets everything between two frames
    can only ever see the poses, never the gestures.
    """

    def __init__(self, hold: float = HOLD, cooldown: float = COOLDOWN):
        self.hold = float(hold)
        self.cooldown = float(cooldown)
        self._states: list[_HandState] = []
        self._fired_at: dict[str, float] = {}

    # -- the public way in --------------------------------------------------

    def reset(self) -> None:
        """Forget everything. The hand left the picture."""
        self._states = []
        self._fired_at = {}

    def feed(self, hands, now: float) -> list[GestureEvent]:
        """The gestures that happened in this picture."""
        now = float(now)
        metrics = [HandMetrics.of(getattr(h, "points", h), getattr(h, "handedness", ""))
                   for h in hands]
        metrics = [m for m in metrics if m.pose != "blob"]
        states = self._match(metrics, now)
        events: list[GestureEvent] = []
        if len(metrics) >= 2:
            events.extend(self._two_hands(metrics, states, now))
        else:
            events.extend(self._one_hand_only(states))
        primary = self._primary(metrics)
        if primary is not None:
            events.extend(self._one_hand(metrics[primary], states[primary], now))
        return events

    def _one_hand_only(self, states: list[_HandState]) -> list[GestureEvent]:
        """THE OTHER HAND WENT AWAY.

        The gestures of two hands have to be FORGOTTEN when there is only
        one hand left: the clap that is not repeated, the frame that is
        not repeated, and the area whose button has to come up.
        """
        events: list[GestureEvent] = []
        for state in states:
            state.clap_since = 0.0
            state.clap_fired = False
            state.frame_since = 0.0
            state.frame_fired = False
            if state.pair_active:
                state.pair_active = False
                events.append(GestureEvent("two_pinch_end"))
        return events

    # -- keeping the hands from one picture to the next ---------------------

    def _match(self, metrics: list[HandMetrics], now: float) -> list[_HandState]:
        """The state of each hand, carried over from the last picture.

        The tracker does not always report the hands in the same order,
        and the state of the hand that is pinching must follow the hand
        and not the slot: so each hand takes the state of the hand that
        was closest to it.
        """
        states: list[_HandState] = []
        used: set[int] = set()
        for metric in metrics:
            best = -1
            best_distance = None
            for index, state in enumerate(self._states):
                if index in used:
                    continue
                distance = math.hypot(state.centre[0] - metric.centre[0],
                                      state.centre[1] - metric.centre[1])
                if best_distance is None or distance < best_distance:
                    best, best_distance = index, distance
            if best >= 0 and best_distance is not None and best_distance < 0.35:
                state = self._states[best]
                used.add(best)
            else:
                state = _HandState()
            # THE SPEED OF THE PALM.
            if state.at:
                seconds = max(1e-3, now - state.at)
                vx = (metric.centre[0] - state.centre[0]) / seconds
                vy = (metric.centre[1] - state.centre[1]) / seconds
                # A LITTLE SMOOTHING, so that a trembling hand is not a
                # hand that is pushing something.
                state.velocity = (0.5 * state.velocity[0] + 0.5 * vx,
                                   0.5 * state.velocity[1] + 0.5 * vy)
            state.centre = metric.centre
            state.at = now
            states.append(state)
        self._states = states
        return states

    def _primary(self, metrics: list[HandMetrics]) -> int | None:
        """Which hand the one-handed gestures belong to.

        THE HAND THAT IS PINCHING, because that is the hand that is
        working. Failing that, the hand that is not resting.
        """
        pinching = [i for i, m in enumerate(metrics) if m.pinching]
        if len(pinching) == 1:
            return pinching[0]
        busy = [i for i, m in enumerate(metrics) if m.pose not in ("open", "point")]
        if len(busy) == 1:
            return busy[0]
        return 0 if metrics else None

    # -- the one-handed gestures --------------------------------------------

    def _one_hand(self, m: HandMetrics, st: _HandState, now: float) -> list[GestureEvent]:
        events: list[GestureEvent] = []
        pose = m.pose

        # ── ONE FINGER: MOVE, AND NOTHING ELSE ───────────────────────────
        #
        # The cursor follows the hand. This is the gesture that says so,
        # and it is a CONTINUOUS one: it is reported while it lasts, and
        # it says where the cursor is going.
        if pose == "point":
            events.append(GestureEvent("point_move",
                                       data={"x": m.centre[0], "y": m.centre[1]}))

        # ── THE PINCH IS THE BUTTON OF THE MOUSE ────────────────────────
        if pose == "pinch":
            if st.pinch_since is None:
                st.pinch_since = now
                st.pinch_anchor = m.centre
                st.pinch_moved = False
                st.pinch_right_fired = False
            moved = math.hypot(m.centre[0] - st.pinch_anchor[0],
                               m.centre[1] - st.pinch_anchor[1])
            if moved > DRAG_REL * m.palm:
                st.pinch_moved = True
            held = now - st.pinch_since
            if st.pinch_moved:
                # A PELLIZCO THAT MOVES IS A DRAG, and never a click.
                st.click_at = None
                events.append(GestureEvent("pinch_drag", data={"moved": moved}))
            elif held >= LONG_PRESS and not st.pinch_right_fired:
                # THE PELLIZCO SOSTENIDO: the context menu.
                st.pinch_right_fired = True
                events.append(GestureEvent("pinch_right", data={"held": held}))
        else:
            if st.pinch_since is not None:
                held = now - st.pinch_since
                if not st.pinch_moved and held < LONG_PRESS:
                    # THE LEFT CLICK HAPPENS WHEN THE FINGERS OPEN.
                    if (st.click_at is not None
                            and now - st.click_at <= DOUBLE_CLICK):
                        st.click_at = None
                        events.append(GestureEvent("pinch_double_click"))
                    else:
                        st.click_at = now
                        events.append(GestureEvent("pinch_click",
                                                   data={"held": held}))
                st.pinch_since = None
                st.pinch_moved = False
                st.pinch_right_fired = False

        # ── THE POSES THAT ARE HELD ──────────────────────────────────────
        if pose != st.pose:
            st.pose = pose
            st.pose_since = now
            st.pose_fired = False
        held = now - st.pose_since
        if not st.pose_fired and held >= self.hold:
            name = _HOLD_GESTURES.get(pose)
            if name:
                event = self._fire(name, now)
                if event is not None:
                    st.pose_fired = True
                    events.append(event)

        # ── THE INDEX AND THE MIDDLE ARE THE WHEEL ───────────────────────
        #
        # THE RING FINGER DOES NOT DECIDE, AND THAT IS A MEASUREMENT.
        #
        # In the live session of the real hand, a two-finger scroll was
        # read as three fingers: the ring rests half out on a relaxed
        # hand, and the machine was counting fingers instead of looking
        # at what they were doing. The wheel is the index and the middle
        # travelling with the hand; the open palm (all four out) is not
        # the wheel, it is the sweep.
        if m.fingers[0] and m.fingers[1] and m.out_count < 4:
            if st.scroll_anchor is None:
                st.scroll_anchor = m.centre
            else:
                dx = m.centre[0] - st.scroll_anchor[0]
                dy = m.centre[1] - st.scroll_anchor[1]
                if abs(dx) > 1e-4 or abs(dy) > 1e-4:
                    events.append(GestureEvent("two_scroll", data={"dx": dx, "dy": dy}))
                    st.scroll_anchor = m.centre
        else:
            st.scroll_anchor = None

        # ── THE PALM: STILL, OR PUSHED ───────────────────────────────────
        if pose == "open":
            vx, vy = st.velocity
            # THE SPEED IN HAND SIZES, so that a hand twice as far away
            # needs the same push to count as one.
            vx = vx / m.palm
            vy = vy / m.palm
            speed = math.hypot(vx, vy)
            if vy <= -PUSH_REL and abs(vx) < PUSH_REL:
                event = self._fire("palm_maximize", now, {"speed": speed})
                if event:
                    events.append(event)
            elif vy >= PUSH_REL and abs(vx) < PUSH_REL:
                event = self._fire("palm_minimize", now, {"speed": speed})
                if event:
                    events.append(event)

        # ── THE THREE FINGERS SWITCH THE WINDOW ─────────────────────────
        if pose == "three":
            vx, vy = st.velocity
            vx = vx / m.palm
            vy = vy / m.palm
            if abs(vx) >= PUSH_REL and abs(vx) > abs(vy):
                event = self._fire("three_switch", now, {"speed": abs(vx)})
                if event:
                    events.append(event)
        return events

    # -- the two-handed gestures --------------------------------------------

    def _two_hands(self, metrics: list[HandMetrics], states: list[_HandState],
                   now: float) -> list[GestureEvent]:
        events: list[GestureEvent] = []
        a, b = metrics[0], metrics[1]
        sa, sb = states[0], states[1]

        # ── THE CLAP: THE HANDS COME TOGETHER ───────────────────────────
        distance = math.hypot(a.centre[0] - b.centre[0], a.centre[1] - b.centre[1])
        if (distance < CLAP_REL * max(a.palm, b.palm)
                and a.out_count >= 3 and b.out_count >= 3
                and not a.pinching and not b.pinching):
            if not sa.clap_since:
                sa.clap_since, sa.clap_fired = now, False
            if now - sa.clap_since >= self.hold and not sa.clap_fired:
                sa.clap_fired = True
                events.append(GestureEvent("palm_clap_sleep"))
        else:
            # THE HANDS WENT APART: THE NEXT CLAP IS A NEW CLAP.
            sa.clap_since = 0.0
            sa.clap_fired = False

        # ── THE FRAME: TWO L's, HOLDING STILL ───────────────────────────
        #
        # THE ORDER OF THE HANDS DOES NOT MATTER. The tracker does not
        # always report the same hand first, and a frame that only works
        # when the hands come in a certain order is a frame that works
        # half the time.
        #
        # AND THE TWO L's DO NOT HAVE TO POINT IN OPPOSITE DIRECTIONS.
        # The specification draws them that way —one up and one down—
        # but the live session showed that the pose is not makeable: the
        # machine saw the L a hundred and forty-two times and the frame
        # none. The frame is TWO L's, held still, at a distance from each
        # other: that is the rectangle of the screenshot.
        if a.l_shape and b.l_shape:
            separacion = abs(a.centre[0] - b.centre[0])
            if separacion > 0.30 * max(a.palm, b.palm):
                if not sa.frame_since:
                    sa.frame_since, sa.frame_fired = now, False
                if now - sa.frame_since >= self.hold and not sa.frame_fired:
                    sa.frame_fired = True
                    events.append(GestureEvent("frame_screenshot"))
        else:
            # THE HANDS ARE NOT IN THE FRAME ANY MORE: THE NEXT FRAME IS
            # A NEW FRAME.
            sa.frame_since = 0.0
            sa.frame_fired = False

        # ── TWO PINCHES: THE AREA OR THE ZOOM ───────────────────────────
        if a.pinching and b.pinching:
            if not sa.pair_active:
                sa.pair_active = True
                sb.pair_active = True
            if sa.pinch_since is None:
                sa.pinch_since, sa.pinch_anchor = now, a.centre
            if sb.pinch_since is None:
                sb.pinch_since, sb.pinch_anchor = now, b.centre
            moved_a = math.hypot(a.centre[0] - sa.pinch_anchor[0],
                                 a.centre[1] - sa.pinch_anchor[1])
            moved_b = math.hypot(b.centre[0] - sb.pinch_anchor[0],
                                 b.centre[1] - sb.pinch_anchor[1])
            # THE MOVEMENT IN HAND SIZES, so that the gesture is the same
            # gesture whether the hands are near the camera or far.
            unit = max(a.palm, b.palm)
            still_a = moved_a < TWO_STILL_REL * unit
            still_b = moved_b < TWO_STILL_REL * unit
            if (still_a != still_b) and max(moved_a, moved_b) > TWO_MOVED_REL * unit:
                # ONE HAND HOLDS STILL AND THE OTHER MOVES: THE AREA.
                events.append(GestureEvent(
                    "two_pinch_area",
                    data={"anchor": a.centre if still_a else b.centre,
                          "moved": max(moved_a, moved_b)}))
            elif not still_a and not still_b:
                # BOTH HANDS MOVE: THE ZOOM, by how far apart they are.
                spread = math.hypot(a.centre[0] - b.centre[0],
                                    a.centre[1] - b.centre[1])
                events.append(GestureEvent("two_pinch_zoom", data={"spread": spread}))
        else:
            sa.pinch_since = sb.pinch_since = None
            if sa.pair_active:
                # THE HANDS LET GO: whatever they were doing together is
                # over, and the mouse button that was down for the
                # selection of an area comes up.
                sa.pair_active = False
                sb.pair_active = False
                events.append(GestureEvent("two_pinch_end"))
        return events

    # -- the cooldown --------------------------------------------------------

    def _may_fire(self, name: str, now: float) -> bool:
        """Has this gesture had its turn yet?"""
        last = self._fired_at.get(name)
        if last is not None and now - last < self.cooldown:
            return False
        self._fired_at[name] = now
        return True

    def _fire(self, name: str, now: float, data: dict | None = None) -> GestureEvent | None:
        """The gesture, if it has had its turn yet.

        THE COOLDOWN IS WHAT STOPS A GESTURE FROM FIRING TWICE.

        A palm that is pushed up and stays there is read fifteen times a
        second, and without a cooldown that is fifteen maximisations. The
        gestures that CONTINUE — the drag and the wheel — are the only
        ones that are allowed to repeat, because repeating them is what
        they are for.
        """
        if name not in CONTINUOUS and not self._may_fire(name, now):
            return None
        self._fired_at[name] = now
        return GestureEvent(name, data=dict(data or {}))


# THE POSES THAT ARE HELD, AND WHAT THEY SAY WHEN THEY ARE HELD STILL.
_HOLD_GESTURES = {
    "open": "open_clutch",
    "fist": "fist_confirm",
    "thumb_up": "thumb_up_approve",
    "thumb_down": "thumb_down_reject",
    "l": "l_dictation",
}
