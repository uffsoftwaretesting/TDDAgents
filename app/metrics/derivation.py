"""
Flow derivation and resilience metrics conforming to §3.6 and Part L2.

Derives TDD flow classification (F1/F2/FAILURE) from transition histories
and computes autonomous self-correction resilience ratios without relying
on legacy wrapper functions or sparse state lists.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import logging
from typing import Sequence

from app.loop.transitions import Continue, Terminal
from app.metrics.event_log import EventEntry, EventType

logger = logging.getLogger(__name__)


class FlowType(StrEnum):
    """
    TDD flow classification derived from transition history (§3.6).

    - F1 (Clean TDD): RED phase confirmed failing (red_confirmed=True)
      before GREEN phase passed (green_passed=True).
    - F2 (Green in Red / Defensive): red_confirmed was still False at the first
      Stop-hook block, or green passed prematurely without a confirmed failing test.
    - FAILURE: Sub-requirement halted on error, max iterations reached, or
      never achieved a passing green state.
    - INCOMPLETE: Sub-requirement execution stopped before reaching a terminal outcome.
    """

    F1 = "F1"
    F2 = "F2"
    FAILURE = "FAILURE"
    INCOMPLETE = "INCOMPLETE"


@dataclass(frozen=True)
class ResilienceMetrics:
    """Summary of resilience and self-correction performance across a run."""

    total_failures: int
    corrected_failures: int
    self_correction_rate: float
    test_faults: int
    implementation_faults: int
    test_fault_ratio: float
    implementation_fault_ratio: float
    f1_count: int
    f2_count: int
    failure_count: int
    total_subreqs: int


def derive_flow_type(events: Sequence[EventEntry]) -> FlowType:
    """
    Derives the TDD flow type (F1, F2, FAILURE, INCOMPLETE) from a sequence of events (§3.6).

    Rules:
    1. If any event indicates a terminal error, failure, or unfulfilled green -> FAILURE.
    2. If there are no transition/terminal events -> INCOMPLETE.
    3. F1 requires that `red_confirmed` became True at or before the transition to GREEN,
       and that `green_passed` subsequently became True.
    4. F2 occurs if:
       - `red_confirmed` was still False at the first Stop-hook blocking event, OR
       - `green_passed` became True without `red_confirmed` having ever been True.
    """
    if not events:
        return FlowType.INCOMPLETE

    transitions = [e for e in events if e.event_type == EventType.TRANSITION]
    if not transitions:
        # Fall back to checking any events with transition_reason or phase
        transitions = [e for e in events if e.transition_reason or e.phase]

    if not transitions:
        return FlowType.INCOMPLETE

    # Check terminal state
    last_event = transitions[-1]
    last_reason = str(last_event.transition_reason or "")

    is_completed = (
        last_reason == str(Terminal.COMPLETED)
        or last_event.green_passed
    )

    is_failed = (
        last_reason in (
            str(Terminal.BLOCKING_LIMIT),
            str(Terminal.MODEL_ERROR),
            str(Terminal.PROMPT_TOO_LONG),
            str(Terminal.ABORTED_STREAMING),
            str(Terminal.ABORTED_TOOLS),
            str(Terminal.STOP_HOOK_PREVENTED),
            str(Terminal.HOOK_STOPPED),
            "error",
            "failed",
            "max_turns",
        )
        or (not is_completed and not any(e.green_passed for e in transitions))
    )

    if is_failed:
        return FlowType.FAILURE

    # Trace history of red_confirmed and green_passed
    ever_red_confirmed = False
    red_confirmed_before_green = False
    stop_hook_blocked_without_red = False

    for ev in transitions:
        reason = str(ev.transition_reason or "")

        # Check if stop-hook blocked while red was still unconfirmed
        if reason == str(Continue.STOP_HOOK_BLOCKING) and not ever_red_confirmed:
            stop_hook_blocked_without_red = True

        if ev.red_confirmed:
            ever_red_confirmed = True

        if ev.green_passed:
            if ever_red_confirmed:
                red_confirmed_before_green = True
            break

    # Also check if any stop_hook events in the broader log blocked without red
    stop_hook_events = [e for e in events if e.event_type == EventType.STOP_HOOK]
    for sh in stop_hook_events:
        if sh.payload.get("blocking") and not sh.red_confirmed:
            stop_hook_blocked_without_red = True

    if stop_hook_blocked_without_red:
        return FlowType.F2

    if red_confirmed_before_green:
        return FlowType.F1

    # If green passed but red was never confirmed, it is F2 (Green in Red)
    if any(e.green_passed for e in transitions) and not ever_red_confirmed:
        return FlowType.F2

    if is_completed and ever_red_confirmed:
        return FlowType.F1

    return FlowType.FAILURE


def compute_resilience_metrics(events: Sequence[EventEntry]) -> ResilienceMetrics:
    """
    Computes autonomous self-correction resilience ratios and fault breakdowns.
    """
    total_failures = 0
    corrected_failures = 0
    test_faults = 0
    implementation_faults = 0

    # Group events by plan item if tagged, or evaluate as single sequence
    items: dict[str, list[EventEntry]] = {}
    for ev in events:
        item_id = str(ev.payload.get("plan_index", ev.payload.get("sub_req", "0")))
        items.setdefault(item_id, []).append(ev)

    f1_count = 0
    f2_count = 0
    failure_count = 0

    for item_events in items.values():
        flow = derive_flow_type(item_events)
        if flow == FlowType.F1:
            f1_count += 1
        elif flow == FlowType.F2:
            f2_count += 1
        else:
            failure_count += 1

        # Count faults and corrections within this item's transitions
        had_impl_fault = False
        had_test_fault = False

        for ev in item_events:
            phase = ev.phase.lower()
            reason = str(ev.transition_reason or "")
            payload = ev.payload

            # Implementation failure during GREEN phase
            if phase == "green" and (
                payload.get("test_failed")
                or payload.get("status") == "failed"
                or reason == str(Continue.STOP_HOOK_BLOCKING)
            ):
                implementation_faults += 1
                total_failures += 1
                had_impl_fault = True

            # Test failure during RED phase that was unexpected or malformed
            if phase == "red" and payload.get("test_syntax_error"):
                test_faults += 1
                total_failures += 1
                had_test_fault = True

        # If an implementation fault occurred but green eventually passed, it was corrected
        if had_impl_fault and any(e.green_passed for e in item_events):
            corrected_failures += 1

        # If a test fault occurred but red was subsequently confirmed, it was corrected
        if had_test_fault and any(e.red_confirmed for e in item_events):
            corrected_failures += 1

    total_subreqs = len(items)
    self_correction_rate = (
        100.0 if total_failures == 0 else (corrected_failures / total_failures) * 100.0
    )

    total_faults = test_faults + implementation_faults
    test_fault_ratio = (test_faults / total_faults * 100.0) if total_faults > 0 else 0.0
    impl_fault_ratio = (
        (implementation_faults / total_faults * 100.0) if total_faults > 0 else 0.0
    )

    return ResilienceMetrics(
        total_failures=total_failures,
        corrected_failures=corrected_failures,
        self_correction_rate=self_correction_rate,
        test_faults=test_faults,
        implementation_faults=implementation_faults,
        test_fault_ratio=test_fault_ratio,
        implementation_fault_ratio=impl_fault_ratio,
        f1_count=f1_count,
        f2_count=f2_count,
        failure_count=failure_count,
        total_subreqs=total_subreqs,
    )
