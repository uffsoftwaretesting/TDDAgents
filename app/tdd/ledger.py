"""
The TDD phase ledger.

This is the record the TDD cycle is enforced against (§3.3 of
`docs/transition_elaboration_plan.md`). Its facts are observations, and the crucial property
is *who is allowed to write them*: nothing here is an agent's claim about its own progress.
`RunTests` is the only writer of evidence (Part D2), because the test runner is the only
component that observes ground truth. Phase-on-spawn and a plan change may only move the
phase or erase evidence (`transition_to`).

The cycle, since Phase 12 (user decision, 2026-10-28):

* **RED** writes robust tests; they need not fail. It ends with a RunTests run (pass or fail)
  in which the item's test files changed since it started (`tests_written`).
* **GREEN** implements; it ends with a passing run (`green_passed`).
* **REFACTOR** is mandatory: it ends with a passing run (`refactor_passed`). Its work is to
  improve the code and the tests and to raise coverage, with `COVERAGE_RULE_OF_THUMB` as a good
  rule of thumb; the coverage of each run is recorded (`coverage`) but gates nothing (user
  decision, 2026-10-28).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from app.tdd.coverage_gate import UNAVAILABLE, CoverageReading


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
    Where the run stands in the Red -> Green -> Refactor cycle.

    `phase` is typed `TddPhase` and validates against the explicit list of phases,
    failing loudly on any unexpected string value.

    `tests_written`, `green_passed` and `refactor_passed` are observations, not intentions:
    a RED run saw changed tests, a GREEN run passed, and a REFACTOR run passed with enough
    coverage. `coverage` is the last measured figure (`None` before one, or when there was no
    production code to measure).
    """

    phase: TddPhase | str = TddPhase.RED
    tests_written: bool = False
    green_passed: bool = False
    refactor_passed: bool = False
    coverage: float | None = None

    def __post_init__(self) -> None:
        if isinstance(self.phase, str) and not isinstance(self.phase, TddPhase):
            try:
                object.__setattr__(self, "phase", TddPhase(self.phase))
            except ValueError:
                raise ValueError(
                    f"Invalid TDD phase: {self.phase!r}. Must be one of: "
                    f"{', '.join(p.value for p in TddPhase)}"
                )

    def with_test_result(
        self, exit_code: int, *, tests_changed: bool = False, coverage: CoverageReading = UNAVAILABLE,
    ) -> PhaseLedger:
        """
        The ledger after RunTests observes a run: exit 0 passed, anything else failed (Part D2).

        RED moves to GREEN only when the item's tests changed, whatever the outcome. GREEN moves
        to REFACTOR on a pass. REFACTOR is passed by a passing run, whose coverage is recorded;
        a failing run un-passes it.
        """
        if self.phase == TddPhase.RED:
            if not tests_changed:
                return self
            return replace(self, phase=TddPhase.GREEN, tests_written=True, green_passed=False)
        if self.phase == TddPhase.GREEN:
            if exit_code != 0:
                return replace(self, green_passed=False)
            return replace(self, phase=TddPhase.REFACTOR, green_passed=True, refactor_passed=False,
                           coverage=coverage.percent)
        if exit_code != 0:
            return replace(self, refactor_passed=False)
        return replace(self, refactor_passed=True, coverage=coverage.percent)

    def transition_to(self, new_phase: TddPhase | str) -> PhaseLedger:
        """
        Explicitly transition to a new phase: the Agent tool's phase-on-spawn (Phase 7).

        The guards are the cycle's order, so a spawn can never skip an observation: GREEN
        needs written tests and REFACTOR a passing GREEN. Going back to RED (a tester
        re-spawned to rewrite the tests, user decision 2026-10-22) restarts the cycle: the
        observations belonged to tests that are about to change, so none of them survive.
        """
        target = TddPhase(new_phase) if isinstance(new_phase, str) else new_phase
        if target == TddPhase.RED:
            return PhaseLedger()
        if target == TddPhase.GREEN and not self.tests_written:
            raise ValueError("Cannot transition to GREEN before RED's tests were written and run.")
        if target == TddPhase.REFACTOR and not self.green_passed:
            raise ValueError("Cannot transition to REFACTOR before GREEN's tests passed.")
        return replace(self, phase=target)

    @property
    def is_cycle_complete(self) -> bool:
        """True when RED wrote tests, GREEN passed them and a REFACTOR run passed."""
        return self.tests_written and self.green_passed and self.refactor_passed


@dataclass(frozen=True, slots=True)
class TestRunRecord:
    """
    The last `RunTests` run the ledger observed (exit 0, or a failing run), kept beside the
    ledger by the same single writer. It is the evidence the TDD state is re-attached from
    after a compaction (Phase 5): the run's own output, not a model's summary of it.
    """

    test_path: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    __test__ = False
