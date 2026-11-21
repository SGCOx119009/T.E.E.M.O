"""core/tasks/plan_runner.py — the engine's runner for a verified plan.

WHY THIS FILE EXISTS
    A plan is a promise: this tool, with these arguments, and a read-only
    check that says it worked. `core/task_plan.py` owns the promise — which
    steps a task has, whether each one is allowed to run, and what proves it
    worked. The engine owns the doing — the budgets, the checkpoints, the
    record of what ran. This file is where the two meet: the one place a
    planned tool call is made, the one place its result is recorded, the one
    place the budget is charged, the one place a checkpoint is written.

    While the plan carried its own copy of that, a step could be started
    from two directions and charged to two budgets. Now the plan drives this
    runner, and the runner is the only way a planned step runs.

WHAT A STEP IS
    One trusted tool call with its arguments. A plan that proves itself
    makes a second call, read-only, to check the first. Both go through
    `PlanRunner.call`, so both are timed, charged and recorded alike.

WHAT THE RUNNER DOES NOT DO
    It never repeats a call. Whether an action whose result was ambiguous may
    be tried again is the plan's decision, not the runner's: an action
    repeated is an effect done twice. The runner hands the plan what
    happened and waits to be told what happens next.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class StepOutcome:
    """What one planned call did, and what it cost."""

    tool: str
    arguments: dict
    kind: str = "action"
    result: Any = None
    error: str = ""
    seconds: float = 0.0


@dataclass
class PlanBudget:
    """The engine's budget for a plan: a deadline, the active time, the rounds.

    The deadline is the limit the plan promises the user — five minutes of
    active time — and the plan checks it at its safe boundaries. The active
    time and the round count are what the calls actually cost, kept beside
    the deadline so a checkpoint says what the plan spent, not only where it
    was.

    A person waiting for the plan spends none of it: the deadline is put
    aside while the plan waits and what was left of it is given back
    afterwards. That is why the deadline lives here and not in the plan's
    own bookkeeping — there is one budget, and both halves of it are read
    from the same place.
    """

    seconds: float = 300.0
    rounds: int = 12
    deadline_at: float = 0.0
    spent: float = 0.0
    used_rounds: int = 0

    def remaining(self) -> float:
        return max(0.0, self.deadline_at - time.monotonic())

    def pause(self) -> float:
        """Put the deadline aside while the plan waits; return what was left."""
        left = self.remaining()
        self.deadline_at = 0.0
        return left

    def resume(self, left: float) -> None:
        """Give the deadline back, with the waiting time not counted."""
        self.deadline_at = time.monotonic() + left

    def expired(self) -> bool:
        """Whether the plan has run out of time.

        The deadline is the limit the plan promised. The active time is the
        same limit measured by what the calls cost, so a plan whose steps
        were slow is stopped by either.
        """
        if self.deadline_at and time.monotonic() >= self.deadline_at:
            return True
        return self.spent > self.seconds

    def charge(self, seconds: float, *, step: bool = False) -> None:
        """Charge what a call cost against the budget."""
        self.spent += max(0.0, float(seconds))
        if step:
            self.used_rounds += 1

    def snapshot(self) -> dict:
        return {
            "seconds": round(self.spent, 1),
            "rounds": self.used_rounds,
            "limit_seconds": self.seconds,
            "limit_rounds": self.rounds,
        }


class PlanRunner:
    """The engine's runner for a verified plan.

    THE ONE CALL SITE. `call` is the only place in the codebase where a
    planned tool call is made: the plan hands the runner a tool and its
    arguments, the runner calls the trusted executor, times it, charges the
    budget and keeps the result. What the result means — done, ambiguous,
    failed — and whether the step may be tried again are the plan's
    decisions, taken from what the runner hands back.
    """

    def __init__(self, executor: Callable[[str, dict], Any], budget: PlanBudget | None = None,
                 checkpoint: Callable[[dict], None] | None = None):
        self.executor = executor
        self.budget = budget if budget is not None else PlanBudget()
        self.checkpoint_writer = checkpoint
        self.trail: list[StepOutcome] = []

    def call(self, tool: str, arguments: dict, kind: str = "action") -> StepOutcome:
        """Run one planned tool call and report what it did."""
        arguments = dict(arguments or {})
        began = time.monotonic()
        result: Any = None
        error = ""
        try:
            result = self.executor(tool, arguments)
        except Exception as exc:
            # The kind of failure is kept and its detail is not: what went
            # wrong belongs in the log, not in the answer to the person who
            # asked for the plan.
            error = type(exc).__name__
        elapsed = max(0.0, time.monotonic() - began)
        self.budget.charge(elapsed, step=(kind == "action"))
        outcome = StepOutcome(tool=tool, arguments=arguments, kind=kind,
                              result=result, error=error, seconds=elapsed)
        self.trail.append(outcome)
        return outcome

    def checkpoint(self, state: dict) -> None:
        """Write the plan's checkpoint.

        The runner owns the writing, so a plan's progress is saved the same
        way whether it was started by the desktop, by a plugin or by a test.
        The plan says what the state is; the runner hands it to whoever
        keeps the records.
        """
        if self.checkpoint_writer is None:
            return
        try:
            self.checkpoint_writer(dict(state))
        except Exception as exc:
            print(f"[PlanRunner] the checkpoint could not be written ({type(exc).__name__})")
