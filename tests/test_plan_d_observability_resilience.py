"""
Unit tests for Plan D: Observability & Resilience Metrics.

Verifies:
1. GlobalTokenTracker integration in stream_call_model and token metrics adapter.
2. ResilienceTracker recording sub-requirements, transitions, and terminal events.
3. Accurate structured pass/fail rates for every sub-requirement.
4. Structured resilience log generation and scorecard output.
"""

import json
import pytest

from app.loop.ledger import TddPhase
from app.loop.model import get_global_token_tracker, get_token_usage_summary
from app.loop.resilience import ResilienceReport, ResilienceTracker, SubRequirementStatus
from app.loop.transitions import Continue, Terminal, Terminated, Transition


def test_token_tracker_adapter():
    """Verify GlobalTokenTracker adapter surfaces metrics."""
    tracker = get_global_token_tracker()
    assert tracker is not None

    summary = get_token_usage_summary()
    assert "totals" in summary
    assert "total_tokens" in summary["totals"]
    assert "prompt_tokens" in summary["totals"]
    assert "completion_tokens" in summary["totals"]


def test_resilience_tracker_sub_requirements_pass_rate():
    """Verify ResilienceTracker computes exact pass/fail rates across sub-requirements."""
    tracker = ResilienceTracker(run_id="run_test_tdd")

    # Record 3 sub-requirements: 2 passed, 1 failed
    tracker.record_sub_requirement(
        req_id="SUB_REQ_1",
        description="Verify failing test for user registration",
        phase=TddPhase.RED,
        passed=True,
    )
    tracker.record_sub_requirement(
        req_id="SUB_REQ_2",
        description="Implement user registration service",
        phase=TddPhase.GREEN,
        passed=True,
    )
    tracker.record_sub_requirement(
        req_id="SUB_REQ_3",
        description="Refactor user registration validation",
        phase=TddPhase.REFACTOR,
        passed=False,
        details="Validation logic failed boundary tests",
    )

    tracker.record_transition(Transition(reason=Continue.NEXT_TURN))
    tracker.record_transition(Transition(reason=Continue.STOP_HOOK_BLOCKING))
    tracker.record_terminal(Terminated(reason=Terminal.COMPLETED))

    report = tracker.build_report()
    assert isinstance(report, ResilienceReport)
    assert report.total_sub_requirements == 3
    assert report.passed_sub_requirements == 2
    assert report.failed_sub_requirements == 1
    # 2/3 = ~0.6667
    assert pytest.approx(report.pass_rate, 0.01) == 0.6667
    assert report.terminal_reason == Terminal.COMPLETED
    assert "stop_hook_blocking" in report.recovery_events


def test_resilience_tracker_structured_log_output():
    """Verify ResilienceTracker emits structured JSON resilience log."""
    tracker = ResilienceTracker(run_id="run_log_test")
    tracker.record_sub_requirement(
        req_id="AUTH_1",
        description="Token expiration handling",
        phase=TddPhase.GREEN,
        passed=True,
    )
    tracker.record_terminal(Terminal.COMPLETED)

    log_json_str = tracker.emit_json_log()
    log_data = json.loads(log_json_str)

    assert log_data["run_id"] == "run_log_test"
    assert log_data["pass_rate"] == 1.0
    assert log_data["terminal_reason"] == "completed"
    assert "AUTH_1" in log_data["sub_requirements"]
    assert log_data["sub_requirements"]["AUTH_1"]["status"] == "PASSED"

    scorecard = tracker.render_scorecard()
    assert "Resilience Scorecard" in scorecard
    assert "100.0%" in scorecard
