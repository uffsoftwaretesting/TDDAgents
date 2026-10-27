"""
The TDD phase ledger.

This is the record the Red->Green invariant is enforced against (§3.3 of
`docs/transition_elaboration_plan.md`). Three facts, and the crucial property is *who is
allowed to write them*: nothing here is an agent's claim about its own progress. Part D2
makes `RunTests` the only writer, because the test runner is the only component that
observes ground truth.

Part A1 ships the shape and nothing else. The ledger is carried by `LoopState` and
mirrored into `AppState` (`app/state/app_state_store.py`) so that a tool can update it, but in this
part no component writes it and no rule reads it. That is deliberate: the reset/preserve
matrix of A7 has to cover every field of `LoopState` from the start, and a field added
later is a field whose continue-site behaviour was never asserted.

Later parts give it teeth:

* D1 moves the authoritative copy into app state and settles the direction of the mirror.
* D2 has `RunTests` write it.
* D3/D4 derive deny rules from `phase`, at pool assembly and again at the runtime gate.
* D5 has the Stop hook refuse the exit while the cycle is incomplete.

The "green in red" case (F2 in the paper) needs no special representation: a test that
passes on its first run leaves `red_confirmed` false with `green_passed` true, which is a
ledger state, not an edge.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum


class TddPhase(StrEnum):
    """
    Closed vocabulary of the three TDD phases (§3.3 and §4.5).

    `phase` is the one agent definition field that must fail a load loudly rather
    than default (§4.5), because silently ignoring it would silently disable the TDD invariant.
    """

    RED = "RED"
    GREEN = "GREEN"
    REFACTOR = "REFACTOR"


@dataclass(frozen=True, slots=True)
class PhaseLedger:
    """
    Where the run stands in the Red->Green->Refactor cycle.

    `phase` is typed `TddPhase` and validates against the explicit list of phases,
    failing loudly on any unexpected string value.

    `red_confirmed` and `green_passed` are observations, not intentions: each records that
    a test run was seen to fail, and that the same test was later seen to pass. They are
    monotonic within one cycle and are updated by `RunTests` (Part D2), the only writer.
    """

    phase: TddPhase | str = TddPhase.RED
    red_confirmed: bool = False
    green_passed: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.phase, str) and not isinstance(self.phase, TddPhase):
            try:
                object.__setattr__(self, "phase", TddPhase(self.phase))
            except ValueError:
                raise ValueError(
                    f"Invalid TDD phase: {self.phase!r}. Must be one of: "
                    f"{', '.join(p.value for p in TddPhase)}"
                )

    def with_test_result(self, exit_code: int) -> PhaseLedger:
        """
        Derive the successor ledger state after RunTests observes an exit code (Part D2).

        Exit code == 0: tests passed.
        Exit code != 0: tests failed.
        """
        if exit_code != 0:
            if self.phase == TddPhase.RED:
                # Failing test observed in RED: red_confirmed becomes True and phase advances to GREEN
                return replace(self, phase=TddPhase.GREEN, red_confirmed=True, green_passed=False)
            elif self.phase == TddPhase.GREEN:
                # Tests still failing in GREEN
                return replace(self, green_passed=False)
            else:
                # REFACTOR phase: behaviour was broken by refactor!
                return replace(self, green_passed=False)

        # exit_code == 0
        if self.red_confirmed:
            # Previously confirmed red test is now passing
            return replace(self, green_passed=True)

        # F2 "green in red" case: test passed on first run without prior failure.
        # red_confirmed stays False, green_passed becomes True, phase stays RED.
        return replace(self, green_passed=True, red_confirmed=False, phase=TddPhase.RED)

    def transition_to(self, new_phase: TddPhase | str) -> PhaseLedger:
        """
        Explicitly transition to a new phase: the Agent tool's phase-on-spawn (Phase 7).

        The guards are the cycle's order, so a spawn can never skip an observation: GREEN
        needs a confirmed RED and REFACTOR a completed cycle. Going back to RED (a tester
        re-spawned to rewrite the test, user decision 2026-10-22) restarts the cycle: the
        observations belonged to a test that is about to change, so none of them survive.
        """
        target = TddPhase(new_phase) if isinstance(new_phase, str) else new_phase
        if target == TddPhase.RED:
            return PhaseLedger()
        if target == TddPhase.GREEN and not self.red_confirmed:
            raise ValueError("Cannot transition to GREEN before a failing test was observed (RED).")
        if target == TddPhase.REFACTOR and not self.is_cycle_complete:
            raise ValueError("Cannot transition to REFACTOR before cycle has passed GREEN (red-then-green).")
        return replace(self, phase=target)

    @property
    def is_cycle_complete(self) -> bool:
        """
        True when a failing test has been confirmed and the test suite has subsequently passed.
        """
        return self.red_confirmed and self.green_passed


@dataclass(frozen=True, slots=True)
class TestRunRecord:
    """
    The last `RunTests` run the ledger observed (exit 0, or a failing run), kept beside the
    ledger by the same single writer. It is the evidence the TDD state is re-attached from
    after a compaction (Phase 5): the failing assertion, not a model's summary of it.
    """

    test_path: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    __test__ = False
