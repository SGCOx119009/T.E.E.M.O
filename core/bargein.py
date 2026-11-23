"""
Deciding that the user has interrupted the assistant by speaking.

WHAT THIS IS
    A pure decision over three facts per microphone block — how loud it was,
    when it arrived, and whether the echo guard says it is a voice. It answers
    one question, "does this block cut the answer?", and it answers it the
    same way every time because the clock is injected.

WHY IT IS A SEPARATE MODULE
    The decision needs three guards, and each is easy to get wrong alone:

      * the DEAD ZONE, so a cough does not cut a sentence;
      * the ECHO GUARD, so the assistant does not interrupt itself;
      * the COOLDOWN, so the tail of its own reply does not restart the turn.

    Wiring that inside main.py's microphone callback would put it next to two
    thousand lines of unrelated code, where it could not be tested without a
    microphone. Here it is a small object with an injected clock, and the
    whole thing is decided in tests without touching hardware.

NO MODEL, NO DOWNLOAD
    The chassis already decided this: the end-of-turn VAD is energy plus
    silence (providers/engine_pipeline.py), and barge-in is that same
    measurement with two extra guards. Nothing here loads a neural detector,
    because that would mean a second download, a second warm-up and a second
    behaviour per platform. This module imports the standard library only.

THE ALGORITHM, IN ORDER
    1. Not speaking        → there is nothing to interrupt.
    2. In cooldown         → too soon after the last interruption.
    3. The guard says "us" → our own voice; never an interruption.
    4. The guard could not be asked → we cannot tell, so we do not interrupt.
    5. Below the dead zone → too quiet to be the user talking over us.
    6. Above the dead zone → count consecutive blocks; when there are enough
       of them, and enough time has passed, interrupt.

    Step 3 and step 4 come BEFORE the level is compared, and that is the whole
    point of the module: the echo is checked first, so a loud block that is
    really our own voice can never cut the answer, however loud it is.

THE DEAD ZONE
    The interruption bar sits ABOVE the bar that ends a turn:

        turn_end = max(noise x TURN_END_MARGIN, ABSOLUTE_MIN)
        threshold = max(noise x DEAD_ZONE_MARGIN, turn_end) x (1 + DEAD_ZONE_GAP)

    So a cough that would be too short to end a turn certainly cannot end an
    answer, and the extra gap makes the zone a band rather than a line. Both
    margins are multiples of the measured noise floor, so the bar follows the
    room instead of being a number that is wrong in most of them.

THE NOISE FLOOR
    Measured here, the same way providers/engine_pipeline.py measures it: the
    floor follows the quiet blocks down at once and creeps up slowly, so a
    room that goes quiet is recognised immediately while one loud moment does
    not raise the bar so far that the next sentence is inaudible to it. A
    caller that already knows the floor (the pipeline's VAD does) can pass it
    in and this module will use that instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# ── The margins ──────────────────────────────────────────────────────────────

# The interruption bar, as a multiple of the measured noise floor. Below this
# the block is the room, not the user.
DEAD_ZONE_MARGIN = 2.5

# After an interruption, no second one is accepted for this long. Without it
# the tail of the assistant's own reply (or the user's own "yes" while the
# assistant is still winding down) restarts the turn it just ended.
COOLDOWN_SECONDS = 0.30

# The end-of-turn bar, as a multiple of the noise floor. The same margin the
# VAD uses in providers/engine_pipeline.py, so the two agree on what "the
# user is talking" means.
TURN_END_MARGIN = 3.5

# How far ABOVE the higher of (the dead-zone margin, the end-of-turn bar) the
# interruption bar sits. A zone has to have width: without this the bar would
# land exactly on the end-of-turn bar and a cough sitting on it would count.
DEAD_ZONE_GAP = 0.15

# The floor of the floor. A silent room's own noise can be almost zero, and
# then "3.5 times nothing" is nothing. The same absolute minimum the VAD uses.
ABSOLUTE_MIN = 0.006

# The reasons a decision can carry. They are strings on purpose: they end up
# in the log, and a log that says "echo" is worth more than a log that says
# False.
REASON_VOICE = "voice"            # it is the user, and it counts
REASON_ECHO = "echo"              # it was us, coming back through the speakers
REASON_TOO_QUIET = "too_quiet"    # below the dead zone
REASON_COOLDOWN = "cooldown"      # too soon after the last interruption
REASON_WARMING_UP = "warming_up"  # the echo guard could not be asked
REASON_NOT_SPEAKING = "not_speaking"


@dataclass(frozen=True)
class BargeInDecision:
    """What the detector concluded about one microphone block.

    `since_voice_ms` is the milliseconds since the FIRST block of the current
    run that cleared the threshold — 64 ms for a two-block answer at 64 ms a
    block. It is what makes the "< 300 ms from the start of the voice"
    criterion measurable, and it is None when no block has cleared the bar yet.
    """

    interrupted: bool
    reason: str
    level: float
    threshold: float
    since_voice_ms: Optional[float] = None


class BargeInDetector:
    """Decides whether a microphone block interrupts the assistant.

    Pure: it holds no clock of its own, opens no file, and talks to nobody.
    The caller injects the time (`now`) and the echo guard's verdict, and the
    detector answers with a decision it can be trusted to repeat.
    """

    def __init__(self, *, dead_zone_margin: float = DEAD_ZONE_MARGIN,
                 cooldown_seconds: float = COOLDOWN_SECONDS,
                 turn_end_margin: float = TURN_END_MARGIN,
                 min_blocks: int = 2,
                 min_voice_ms: float = 0.0):
        self._dead_zone_margin = float(dead_zone_margin)
        self._cooldown_seconds = float(cooldown_seconds)
        self._turn_end_margin = float(turn_end_margin)
        # At least one block: a detector that can never fire is a detector that
        # was configured wrong.
        self._min_blocks = max(1, int(min_blocks))
        self._min_voice_ms = float(min_voice_ms)

        self._floor = ABSOLUTE_MIN
        self._blocks = 0
        self._voice_start: Optional[float] = None
        self._last_interrupt: Optional[float] = None

    # ── state ───────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Forget the current run. The learned noise floor stays: the room
        did not change just because the assistant stopped talking."""
        self._blocks = 0
        self._voice_start = None
        self._last_interrupt = None

    @property
    def noise_floor(self) -> float:
        """The floor the thresholds are computed from."""
        return self._floor

    @property
    def turn_end_threshold(self) -> float:
        """The bar that ends a turn — the same margin the VAD uses."""
        return max(self._floor * self._turn_end_margin, ABSOLUTE_MIN)

    @property
    def threshold(self) -> float:
        """The bar that interrupts. Always ABOVE `turn_end_threshold`."""
        base = max(self._floor * self._dead_zone_margin, self.turn_end_threshold)
        return base * (1.0 + DEAD_ZONE_GAP)

    @property
    def blocks(self) -> int:
        """Consecutive blocks above the bar in the current run."""
        return self._blocks

    @property
    def cooldown_until(self) -> Optional[float]:
        """The time the cooldown started by the last interruption ends.

        None when no interruption has happened yet.
        """
        if self._last_interrupt is None:
            return None
        return self._last_interrupt + self._cooldown_seconds

    # ── the decision ────────────────────────────────────────────────────────

    def feed(self, *, level: float, now: float, speaking: bool,
             is_user_speech: bool | None = None,
             noise_floor: float | None = None) -> BargeInDecision:
        """One microphone block.

        `level` is the block's loudness on whatever scale the caller uses
        (the app uses RMS). `now` is a monotonic timestamp in seconds — it is
        injected rather than read from the clock so the tests are
        deterministic and the latency criterion can be measured exactly.

        `is_user_speech` is the echo guard's verdict:
          * True  — this block is a voice that is not ours;
          * False — this block is our own echo, and it never interrupts;
          * None  — the guard could not be asked (not warmed up, or busy).
                    Then we do NOT interrupt: interrupting on a block we cannot
                    tell apart from our own voice is how an assistant cuts off
                    its own answer.

        `noise_floor`, when given, is the caller's own measurement of the
        room's noise and is used as-is for this block.
        """
        level = float(level)
        now = float(now)

        if noise_floor is not None:
            self._floor = max(float(noise_floor), 0.0)
        threshold = self.threshold

        # 1. Nothing is playing: nothing to interrupt.
        if not speaking:
            self._reset_run()
            return BargeInDecision(False, REASON_NOT_SPEAKING, level, threshold, None)

        # 2. Too soon after the last interruption. The cooldown survives the
        #    assistant stopping to speak, because the point of it is to ignore
        #    the tail of the reply that was just cut off.
        if self._last_interrupt is not None:
            if (now - self._last_interrupt) < self._cooldown_seconds:
                self._reset_run()
                return BargeInDecision(False, REASON_COOLDOWN, level, threshold, None)
            self._last_interrupt = None

        # 3. The echo guard knows this is us. The level is never consulted.
        if is_user_speech is False:
            self._reset_run()
            return BargeInDecision(False, REASON_ECHO, level, threshold, None)

        # 4. The guard could not be asked. Prudence: do not interrupt.
        if is_user_speech is None:
            self._reset_run()
            return BargeInDecision(False, REASON_WARMING_UP, level, threshold, None)

        # 5. Below the dead zone. A cough lives here.
        if level < threshold:
            self._reset_run()
            self._update_floor(level)
            return BargeInDecision(False, REASON_TOO_QUIET, level, threshold, None)

        # 6. Above the bar: this might be the user. Count it.
        if self._voice_start is None:
            self._voice_start = now
        self._blocks += 1
        since_ms = (now - self._voice_start) * 1000.0

        if self._blocks >= self._min_blocks and since_ms >= self._min_voice_ms:
            self._last_interrupt = now
            self._reset_run()
            return BargeInDecision(True, REASON_VOICE, level, threshold, since_ms)

        return BargeInDecision(False, REASON_VOICE, level, threshold, since_ms)

    # ── internals ───────────────────────────────────────────────────────────

    def _reset_run(self) -> None:
        """The current run of above-the-bar blocks is over."""
        self._blocks = 0
        self._voice_start = None

    def _update_floor(self, level: float) -> None:
        """Track the room's noise.

        The floor follows the quiet blocks down at once and creeps up slowly,
        exactly like the VAD's floor in providers/engine_pipeline.py: a room
        that goes quiet is recognised at once, while one loud moment does not
        raise the bar so far that the next sentence is inaudible to it.

        Only quiet blocks count. A block that cleared the bar is somebody
        talking, and a block the echo guard called ours is us — neither may
        lift the floor.
        """
        if level < self._floor:
            self._floor = level
        else:
            self._floor += (level - self._floor) * 0.002
        if self._floor < 0.0:
            self._floor = 0.0
