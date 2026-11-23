"""
emotional_spectrum.py — the assistant's affective state, as a tool the user can ask for.

WHAT THIS IS
    A named state of mind with a colour, a voice and a body language. Twelve of
    them, and the user picks one out loud: "be sceptical about this plan", "be
    more empathetic", "be tactical". The state is not a word in the prompt and
    nothing else — it drives three things at once:

      1. THE PROMPT. The state carries the instruction that shapes the tone of
         the answer ("state → {prompt, palette, micro-animations}", the plan).
      2. THE PALETTE. The HUD's accent colour moves, by continuous
         interpolation, toward the state's colour.
      3. THE MICRO-ANIMATIONS. The face and the ring react to the state.

WHY IT IS A TOOL AND NOT A SETTING
    Because the user asks for it in the middle of a conversation, in the middle
    of an argument, and expects the answer that follows to sound different. A
    setting in a panel is read once and forgotten; this is said once and obeyed
    from the next sentence on.

WHAT IT DOES NOT DO
    It does not touch anything outside the assistant's own presentation. There
    is no path from here to the file system, to the network, or to the
    permissions of the application — the state is a mood, and a mood is not a
    capability.

THE INTENSITY PARAMETER
    Optional, 0 to 1. At 1 (the default) the state is taken whole. Below 1 the
    colour moves only part of the way from where it was, so "a bit more
    sceptical" is a real thing and not the same as "sceptical". The tone
    follows the same fraction, so the two never disagree.

THE THREAD
    Tool handlers run on worker threads, never on the Qt thread. This file
    therefore never touches a widget: it hands the state to the interface
    through `player`, which marshals it onto the Qt thread by signal.
"""
from __future__ import annotations

from core import outcome


def _clamp(value, low: float = 0.0, high: float = 1.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(high, number))


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Set the affective state. Returns a short sentence for the model to say."""
    params = parameters if isinstance(parameters, dict) else {}

    try:
        from core import emotional_spectrum as spectrum
    except Exception as exc:                       # pragma: no cover - defensive
        return outcome.failed(
            "emotional_spectrum",
            "the affective spectrum is not available in this build",
            error=type(exc).__name__,
        )

    raw_state = params.get("state")
    if not isinstance(raw_state, str) or not raw_state.strip():
        return outcome.failed(
            "emotional_spectrum",
            "no state was given; name one of the twelve affective states",
            states=list(spectrum.STATE_NAMES),
        )

    try:
        target = spectrum.state_by_name(raw_state)
    except (KeyError, ValueError) as exc:
        return outcome.failed(
            "emotional_spectrum",
            f"'{raw_state.strip()}' is not one of the twelve affective states",
            states=list(spectrum.STATE_NAMES),
            error=type(exc).__name__,
        )

    intensity = _clamp(params.get("intensity", 1.0))

    try:
        previous = spectrum.current_state()
        spectrum.set_state(target.name)
    except Exception as exc:
        return outcome.failed(
            "emotional_spectrum",
            "the affective state could not be changed",
            error=type(exc).__name__,
        )

    # The colour is blended, so "a little more sceptical" lands between the two
    # colours instead of snapping to the new one.
    colour = target.color
    if intensity < 1.0 and previous.name != target.name:
        try:
            colour = spectrum.blend_path(previous.name, target.name, intensity)
        except Exception:
            colour = target.color

    # The interface is told, so the palette and the face follow. The model is
    # told too, through the prompt fragment that travels with the notification.
    prompt = spectrum.prompt_fragment(target)
    try:
        if player is not None:
            setter = getattr(player, "set_emotional_state", None)
            if callable(setter):
                setter(target.name, prompt, colour)
    except Exception:
        # A cosmetic failure must never cost the user the state they asked for.
        pass

    return outcome.done(
        "emotional_spectrum",
        state=target.name,
        tone=target.tone,
        colour=colour,
        intensity=intensity,
        verified=True,
    )


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "emotional_spectrum",
    "description": (
        "Sets the AFFECTIVE STATE of the assistant — how it speaks, how its "
        "interface looks and how its face moves — for the rest of the "
        "conversation, until another state is set. Use it when the user asks "
        "how you should sound or how you should take something: 'be sceptical "
        "about this plan', 'be more empathetic', 'be tactical', 'be playful', "
        "'be serious about this', 'be motivating me'. The twelve states are: "
        "EMPATHETIC (warm, understanding, generous), TACTICAL (precise, "
        "economical, no decoration), WITTY (light, dry, quick), MOTIVATIONAL "
        "(driving, pushing forward), CHALLENGING (provocative, testing the "
        "plan), VIGILANT (alert, watchful, expecting trouble), FRUSTRATED "
        "(irritated, blunt), SKEPTICAL (doubting, asking for the weak point), "
        "SOLEMN (grave, weighty), CONCERNED (urgent, worried), INDIGNANT (out "
        "of patience, hot) and COLD (flat, impersonal). "
        "It changes only HOW the assistant speaks and looks — never what it is "
        "allowed to do. Do not call it to change permissions or to act: it "
        "cannot. When the user asks for a mood, set it and then answer in that "
        "mood; do not merely announce that you have done so."
    ),
    "brief": (
        "Sets how the assistant speaks and looks. States: EMPATHETIC, "
        "TACTICAL, WITTY, MOTIVATIONAL, CHALLENGING, VIGILANT, FRUSTRATED, "
        "SKEPTICAL, SOLEMN, CONCERNED, INDIGNANT, COLD. Optional 'intensity' "
        "0-1 blends the colour part of the way. Changes presentation only."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "state": {
                "type": "STRING",
                "description": (
                    "One of the twelve states: EMPATHETIC, TACTICAL, WITTY, "
                    "MOTIVATIONAL, CHALLENGING, VIGILANT, FRUSTRATED, "
                    "SKEPTICAL, SOLEMN, CONCERNED, INDIGNANT, COLD."
                ),
            },
            "intensity": {
                "type": "NUMBER",
                "description": (
                    "Optional, 0 to 1. How far to move toward the state. "
                    "1 (the default) takes it whole; below 1 blends the "
                    "colour part of the way from the current state."
                ),
            },
        },
        "required": ["state"],
    },
    # The callable the loader invokes. Without this key the declaration is
    # read out of the source but never bound, and the tool disappears from
    # the catalogue the moment the modules are warmed up.
    "handler": run,
}
