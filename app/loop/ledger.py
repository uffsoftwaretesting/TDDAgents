"""
The TDD phase ledger.

This is the record the Red->Green invariant is enforced against (§3.3 of
`docs/transition_elaboration_plan.md`). Three facts, and the crucial property is *who is
allowed to write them*: nothing here is an agent's claim about its own progress. Part D2
makes `RunTests` the only writer, because the test runner is the only component that
observes ground truth.

Part A1 ships the shape and nothing else. The ledger is carried by `LoopState` and
mirrored into `AppState` (`app/loop/context.py`) so that a tool can update it, but in this
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

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PhaseLedger:
    """
    Where the run stands in the Red->Green->Refactor cycle.

    `phase` is typed `str` only until the phase vocabulary lands with the rest of the TDD
    enforcement in Part D; it is a closed set of three values, and it is the one
    definition field that must fail a load loudly rather than default (§4.5), so it gets a
    real type there rather than a validated string here.

    `red_confirmed` and `green_passed` are observations, not intentions: each records that
    a test run was seen to fail, and that the same test was later seen to pass. They are
    monotonic within one sub-requirement and are reset by whatever advances the run to the
    next one.
    """

    phase: str = "RED"
    red_confirmed: bool = False
    green_passed: bool = False
