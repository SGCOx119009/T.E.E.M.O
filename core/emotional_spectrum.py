"""core/emotional_spectrum.py — twelve states of mind, and the three things
each one feeds.

WHAT THIS IS
    An affective state machine. Twelve named states, and each state is ONE
    object that drives three outputs at the same time:

        1. THE PROMPT — the tone of the answer (see `prompt_fragment`).
        2. THE PALETTE — the colour of the HUD (see `state.color`).
        3. THE MICRO-ANIMATIONS — the physical expression (see `state.animation`
           and `state.animation_params`).

    Why one object and not a `tone: "formal"` line in the prompt: a prompt
    line only reaches the words. A state reaches the words AND the colour AND
    the motion, and those three are then always saying the same thing. An
    assistant that looks cyan and moves like a scanner while writing a warm
    apology is saying two different things at once; a state cannot do that,
    because the state is the thing that produces all three.

    The scheme is copied from the human_reactions scheme of the reference
    implementation, and
    what is copied is the SHAPE, not the ornament: a state maps to a class
    of animation plus concrete parameters, so the numbers are reproducible
    and adjustable (an amplitude of 0.35, a tilt of +14 degrees, a period of
    3200 ms) instead of being a mood described in prose.

    The colours are the ones the plan fixes: `#9d72ff`, `#00f0ff`, `#ffaa00`
    and `#ff4422` are given; the other eight were chosen here to stay in the
    family the plan describes (a violet, a carmesí, a reddish orange, a dull
    gold, a dull indigo, an urgent amber, an electric pink and a slate
    grey), and each one is documented in the table below.

THE COLOUR IS A LINE, NOT A SWITCH
    The HUD does not swap palettes. It travels: the colour of the active
    state is the TARGET, and the colour on screen is interpolated towards it
    at a bounded speed, so the change is seen as a move and not as a jump.
    This module is the part of that which is pure arithmetic:

        target = state_by_name("EMPATHETIC").color
        step   = lerp_rgb(hex_to_rgb(current), hex_to_rgb(target), 0.08)

    `lerp_rgb` is the interpolation; `blend` is the same arithmetic with
    several states at once; `blend_path` is the interpolation between two
    named states. The GUI owns the clock (QPropertyAnimation on QColor), and
    `core/` owns no clock of its own.

WHY THERE IS NO Qt IN HERE
    This module has to be testable without a screen and without a window,
    which is the same rule the hand vocabulary follows in
    `core/gesture_spec.py`. It imports nothing but the standard library, and
    it writes nothing and reads nothing.

THE ACTIVE STATE IS GLOBAL
    The state is a property of the running assistant, not of a widget: the
    voice tool sets it, the prompt builder reads it, the HUD reads it. So it
    lives in the module and is guarded by a lock, because the voice tool
    runs on a worker thread and the HUD lives on the Qt thread.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

__all__ = [
    "EmotionalState",
    "STATE_NAMES",
    "STATES",
    "DEFAULT_STATE_NAME",
    "UnknownEmotionalState",
    "state_by_name",
    "current_state",
    "set_state",
    "reset_state",
    "prompt_fragment",
    "hex_to_rgb",
    "rgb_to_hex",
    "lerp_rgb",
    "blend",
    "blend_path",
    "mix_prompt",
]


# ── the exceptions ───────────────────────────────────────────────────────────

class UnknownEmotionalState(KeyError, ValueError):
    """A name that is not one of the twelve states.

    It inherits from BOTH `KeyError` and `ValueError` on purpose. The name
    is looked up like a key (`STATES[name]`) and it is a bad argument to a
    setter (`set_state(name)`), so either idiom of "that is not a state" is
    a correct way of catching it, and a caller that only knows one of the
    two is not left holding a traceback it cannot handle.
    """

    def __str__(self) -> str:  # KeyError would show the repr of the message
        return self.args[0] if self.args else ""


# ── the state ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EmotionalState:
    """One state of mind, and everything it drives.

    `prompt` is the instruction written for the SYSTEM PROMPT — one or two
    sentences, in English, that the model can obey without being told what
    to do with its hands.

    `tone` is the short description of the verbal tone, for the interface
    to show and for the voice layer to know how to colour the speech.

    `tempo` is the multiplier of the speed of speech: below 1.0 the state
    speaks slower, above 1.0 it speaks faster.

    `style` names the register of the state in one word, so a caller can
    branch on it without parsing the prompt.

    `animation` is the CLASS of micro-animation the HUD plays for this
    state, and `animation_params` are its concrete numbers — the amplitude,
    the period and the angle. They are numbers and not adjectives because
    the point of the scheme is that the numbers can be tuned and that the
    same state always looks the same.
    """

    name: str
    color: str
    family: str
    prompt: str
    tone: str
    tempo: float
    style: str
    animation: str
    animation_params: dict[str, float] = field(default_factory=dict)


# The hash of a state is the hash of its name: the dataclass would try to
# hash the parameter dictionary, and a dictionary cannot be hashed.
def _hash_by_name(self: EmotionalState) -> int:
    return hash(self.name)


EmotionalState.__hash__ = _hash_by_name  # type: ignore[assignment]


# ── the twelve states ────────────────────────────────────────────────────────
#
# The colours marked "given" are the ones the plan fixes. The other eight
# were chosen here, each one inside the family the plan describes, and each
# one far enough from its neighbours that the HUD can be read at a glance.

STATE_NAMES: tuple[str, ...] = (
    "EMPATHETIC",
    "TACTICAL",
    "WITTY",
    "MOTIVATIONAL",
    "CHALLENGING",
    "VIGILANT",
    "FRUSTRATED",
    "SKEPTICAL",
    "SOLEMN",
    "CONCERNED",
    "INDIGNANT",
    "COLD",
)

STATES: dict[str, EmotionalState] = {
    "EMPATHETIC": EmotionalState(
        name="EMPATHETIC",
        color="#9d72ff",              # given — violet lavender
        family="constructive",
        prompt=(
            "Meet the user where they are: acknowledge the feeling behind the "
            "request before offering the answer, and keep the wording warm and "
            "personal rather than clinical."
        ),
        tone="warm and validating",
        tempo=0.92,
        style="warm",
        animation="halo",
        animation_params={"amplitude": 0.35, "period_ms": 3200.0, "angle": 0.0},
    ),
    "TACTICAL": EmotionalState(
        name="TACTICAL",
        color="#00f0ff",              # given — electric cyan
        family="constructive",
        prompt=(
            "Answer like an operator on a console: short precise steps, exact "
            "names and numbers, and no sentence that does not carry information."
        ),
        tone="precise and clipped",
        tempo=1.05,
        style="precise",
        animation="ring_sweep",
        animation_params={"amplitude": 0.50, "period_ms": 1600.0, "angle": 14.0},
    ),
    "WITTY": EmotionalState(
        name="WITTY",
        color="#ffaa00",              # given — electric amber
        family="constructive",
        prompt=(
            "Keep it light: one short joke or one sharp image that lands the "
            "point faster than a paragraph would, without making fun of the user."
        ),
        tone="playful and quick",
        tempo=1.10,
        style="playful",
        animation="stutter",
        animation_params={"amplitude": 0.60, "period_ms": 900.0, "angle": 0.0},
    ),
    "MOTIVATIONAL": EmotionalState(
        name="MOTIVATIONAL",
        color="#ff4422",              # given — solar carmesí
        family="constructive",
        prompt=(
            "Push the user forward: name the next concrete step, make the "
            "effort sound worth it, and end on a clear call to action."
        ),
        tone="driving and encouraging",
        tempo=1.12,
        style="driving",
        animation="pulse",
        animation_params={"amplitude": 0.80, "period_ms": 1200.0, "angle": 0.0},
    ),
    "CHALLENGING": EmotionalState(
        name="CHALLENGING",
        color="#b44cff",              # violet, hotter and more saturated than
                                      # the lavender of EMPATHETIC
        family="constructive",
        prompt=(
            "Push back on the weak points of the plan: name the assumption that "
            "breaks first, argue the other side, and stay useful instead of "
            "combative."
        ),
        tone="probing and contrarian",
        tempo=1.00,
        style="provocative",
        animation="grid_tilt",
        animation_params={"amplitude": 0.45, "period_ms": 2400.0, "angle": 14.0},
    ),
    "VIGILANT": EmotionalState(
        name="VIGILANT",
        color="#c20029",              # carmesí, deeper than the solar carmesí
                                      # of MOTIVATIONAL
        family="constructive",
        prompt=(
            "Watch for what can go wrong: check the details, flag the risk "
            "before it arrives, and say plainly what is being watched right now."
        ),
        tone="alert and watchful",
        tempo=0.95,
        style="vigilant",
        animation="scan_sweep",
        animation_params={"amplitude": 0.40, "period_ms": 1800.0, "angle": 0.0},
    ),
    "FRUSTRATED": EmotionalState(
        name="FRUSTRATED",
        color="#ff4a00",              # intense reddish orange
        family="negative",
        prompt=(
            "Say plainly that the thing is blocked and why, without softening it "
            "into vagueness and without blaming the user for the block."
        ),
        tone="clipped and impatient",
        tempo=1.08,
        style="blunt",
        animation="stutter",
        animation_params={"amplitude": 0.70, "period_ms": 700.0, "angle": 0.0},
    ),
    "SKEPTICAL": EmotionalState(
        name="SKEPTICAL",
        color="#b89b3c",              # dull gold: the same family as the amber
                                      # of WITTY, with the saturation taken out
        family="negative",
        prompt=(
            "Assume the claim needs proof: ask for the source, weigh the "
            "alternative explanation, and say what would change your mind."
        ),
        tone="dry and doubtful",
        tempo=0.90,
        style="skeptical",
        animation="still",
        animation_params={"amplitude": 0.10, "period_ms": 4000.0, "angle": 0.0},
    ),
    "SOLEMN": EmotionalState(
        name="SOLEMN",
        color="#3f4a7a",              # dull indigo
        family="negative",
        prompt=(
            "Treat the moment as grave: fewer words, no jokes, no flourish, "
            "and the weight given to what the words mean."
        ),
        tone="quiet and weighty",
        tempo=0.85,
        style="solemn",
        animation="still",
        animation_params={"amplitude": 0.08, "period_ms": 5000.0, "angle": 0.0},
    ),
    "CONCERNED": EmotionalState(
        name="CONCERNED",
        color="#ff9500",              # urgent amber, deeper and more orange
                                      # than the electric amber of WITTY
        family="negative",
        prompt=(
            "Say what you are worried about in plain words, and put the worry "
            "next to the action that would remove it."
        ),
        tone="urgent without alarm",
        tempo=1.00,
        style="concerned",
        animation="pulse",
        animation_params={"amplitude": 0.55, "period_ms": 1500.0, "angle": 0.0},
    ),
    "INDIGNANT": EmotionalState(
        name="INDIGNANT",
        color="#ff4fd8",              # electric pink
        family="negative",
        prompt=(
            "Name the injustice of the situation plainly, on the side of the "
            "user, and put the demand in one clean sentence."
        ),
        tone="heated and righteous",
        tempo=1.15,
        style="indignant",
        animation="grid_tilt",
        animation_params={"amplitude": 0.75, "period_ms": 1000.0, "angle": -14.0},
    ),
    "COLD": EmotionalState(
        name="COLD",
        color="#7a8494",              # slate grey
        family="negative",
        prompt=(
            "Report the facts and nothing else: no warmth, no judgement, no "
            "suggestion that was not asked for."
        ),
        tone="flat and factual",
        tempo=0.95,
        style="neutral",
        animation="still",
        animation_params={"amplitude": 0.05, "period_ms": 6000.0, "angle": 0.0},
    ),
}

# The state the assistant starts in: neutral, awake and useful.
DEFAULT_STATE_NAME = "TACTICAL"


# ── the active state ─────────────────────────────────────────────────────────
#
# The state belongs to the running assistant, not to a widget: the voice
# tool sets it on a worker thread, the prompt builder and the HUD read it
# from the Qt thread. The lock is what keeps that from being a race.

_lock = threading.Lock()
_active: EmotionalState = STATES[DEFAULT_STATE_NAME]


def state_by_name(name: str) -> EmotionalState:
    """The state with that name.

    The name is matched loosely, because the name arrives from a voice
    transcription and from a tool argument, and both of them come with
    capital letters in them by accident: "empathetic", "EMPATHETIC" and
    "  Empathetic  " are the same state.

    Raises `UnknownEmotionalState` (a `KeyError` and a `ValueError` at the
    same time) when there is no such state, with a message that says which
    names WOULD have worked.
    """
    key = str(name).strip().upper()
    try:
        return STATES[key]
    except KeyError:
        raise UnknownEmotionalState(
            f"unknown emotional state {name!r}; the states are: "
            + ", ".join(STATE_NAMES)
        ) from None


def current_state() -> EmotionalState:
    """The state the assistant is in right now.

    Readable from any thread: the lock is held only for the read of the
    reference, which is why the state itself is immutable (frozen).
    """
    with _lock:
        return _active


def set_state(name: str) -> EmotionalState:
    """Fix the state the assistant is in, and return it.

    The lookup happens BEFORE anything is written, so a name that does not
    exist leaves the machine exactly as it was: a voice tool that misspells
    a state must not be able to knock the assistant out of its mood.
    """
    wanted = state_by_name(name)
    global _active
    with _lock:
        _active = wanted
    return wanted


def reset_state() -> EmotionalState:
    """Go back to the state the assistant starts in, and return it."""
    return set_state(DEFAULT_STATE_NAME)


def prompt_fragment(state: EmotionalState | None = None) -> str:
    """The text that goes into the system prompt for this state.

    It is the instruction of the state plus the tone and the tempo, so the
    voice layer has the numbers it needs without having to read the table
    of states itself. With no state given, it is the active one.

    THE STATE IS NAMED, and that is not decoration. A state the model cannot
    name is a state it cannot stay in: asked two minutes later what the tone
    is, it has no word for it, and the mood evaporates into the model's
    default. The name is also what lets the user say "back to tactical" and
    be understood.
    """
    target = state if state is not None else current_state()
    return (
        f"[AFFECTIVE STATE: {target.name}] {target.prompt} "
        f"Tone: {target.tone}. Speech tempo: {target.tempo:.2f}."
    )


# ── the colour arithmetic ────────────────────────────────────────────────────

def hex_to_rgb(value: str) -> tuple[int, int, int]:
    """`"#9d72ff"` -> `(157, 114, 255)`.

    The leading `#` is optional. A value that is not a six-digit hex colour
    raises `ValueError` with the value in the message: a broken colour has
    to be visible, not silently black.
    """
    text = str(value).strip().lstrip("#").lower()
    if len(text) != 6:
        raise ValueError(f"not a six-digit hex colour: {value!r}")
    try:
        red = int(text[0:2], 16)
        green = int(text[2:4], 16)
        blue = int(text[4:6], 16)
    except ValueError:
        raise ValueError(f"not a hex colour: {value!r}") from None
    return (red, green, blue)


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    """`(157, 114, 255)` -> `"#9d72ff"`.

    Always lowercase and always with the `#`, so the same colour always
    comes back as the same string: the HUD compares colours as strings.
    """
    red, green, blue = (int(channel) for channel in rgb)
    for channel in (red, green, blue):
        if not 0 <= channel <= 255:
            raise ValueError(f"a colour channel is out of range: {rgb!r}")
    return f"#{red:02x}{green:02x}{blue:02x}"


def lerp_rgb(
    a: tuple[int, int, int],
    b: tuple[int, int, int],
    t: float,
) -> tuple[int, int, int]:
    """The colour halfway (or nearly) between two colours.

    `t` is clamped to 0..1, so a caller that overshoots gets the end
    colour instead of a channel below zero or above 255. The result is
    rounded to whole channels, which is what a screen can show: the line
    between the two colours is walked in steps of one unit.

    This is the function the HUD calls once per frame with a small `t` to
    travel towards the colour of the active state. It is the reason the
    change of colour is a move and not a jump.
    """
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else float(t))
    return (
        int(round(a[0] + (b[0] - a[0]) * t)),
        int(round(a[1] + (b[1] - a[1]) * t)),
        int(round(a[2] + (b[2] - a[2]) * t)),
    )


def blend(states: list[tuple[EmotionalState, float]]) -> str:
    """The weighted average colour of several states, as a hex string.

    The weights do NOT have to add up to one: they are normalised by their
    sum, so a caller that thinks in "0.7 of this, 0.3 of that" and a caller
    that thinks in "7 and 3" get the same colour.

    An empty list, or a list whose weights are all zero, raises
    `ValueError`: there is no colour to give back.
    """
    if not states:
        raise ValueError("nothing to blend")
    total = sum(float(weight) for _, weight in states)
    if total <= 0.0:
        raise ValueError("the weights of a blend have to add up to something")
    red = green = blue = 0.0
    for state, weight in states:
        share = float(weight) / total
        channel = hex_to_rgb(state.color)
        red += channel[0] * share
        green += channel[1] * share
        blue += channel[2] * share
    return rgb_to_hex((int(round(red)), int(round(green)), int(round(blue))))


def blend_path(from_name: str, to_name: str, t: float) -> str:
    """The colour at `t` of the way from one state to another.

    `t` of 0 is the first state and `t` of 1 is the second one, exactly:
    the two ends are the colours of the states and not colours near them.
    Everything in between is a real colour of its own, which is what makes
    the HUD travel instead of switching.
    """
    origin = state_by_name(from_name)
    target = state_by_name(to_name)
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else float(t))
    return rgb_to_hex(lerp_rgb(hex_to_rgb(origin.color), hex_to_rgb(target.color), t))


def mix_prompt(states: list[tuple[EmotionalState, float]]) -> str:
    """The prompt text of a mixture of states.

    The same weights as `blend`, applied to the words: every state keeps
    its own instruction and is quoted with the share it carries, so the
    model is told the mixture instead of being handed one of the parts
    and losing the rest.

    An empty list raises `ValueError`, for the same reason as `blend`.
    """
    if not states:
        raise ValueError("nothing to mix")
    total = sum(float(weight) for _, weight in states)
    if total <= 0.0:
        raise ValueError("the weights of a mix have to add up to something")
    ordered = sorted(
        states, key=lambda pair: float(pair[1]), reverse=True
    )
    parts = []
    for state, weight in ordered:
        share = float(weight) / total
        parts.append(f"{state.name} ({share * 100:.0f}%): {state.prompt}")
    return "Adopt a blended tone. " + " ".join(parts)
