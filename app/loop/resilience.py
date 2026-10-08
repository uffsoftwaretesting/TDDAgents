"""
Observability and Resilience Metrics Subsystem (Plan D).

Ported from:
- `reference/claude-code/src/query/transitions.ts`
- `reference/claude-code/src/services/analytics/index.ts`
- `docs/refactoring_transition_plan.md` -> Plan D (Observability & Resilience Metrics)

Provides:
- Sub-requirement tracking across TDD phases (RED, GREEN, REFACTOR).
- Structured pass/fail rates for every sub-requirement.
- Resilience event tracking (recoveries, stop hook blocks, compaction retries).
- Scorecard and JSON resilience log generation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.loop.ledger import TddPhase
from app.loop.model import get_token_usage_summary
from app.loop.transitions import Continue, Terminal, Terminated, Transition


class SubRequirementStatus(StrEnum):
    PENDING = "PENDING"
    PASSED = "PASSED"
    FAILED = "FAILED"


@dataclass
class SubRequirementMetric:
    """Metrics recorded for a single sub-requirement."""

    req_id: str
    description: str
    phase: TddPhase
    status: SubRequirementStatus = SubRequirementStatus.PENDING
    iterations: int = 1
    details: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "req_id": self.req_id,
            "description": self.description,
            "phase": self.phase.value if hasattr(self.phase, "value") else str(self.phase),
            "status": self.status.value,
            "iterations": self.iterations,
            "details": self.details,
        }


@dataclass(frozen=True)
class ResilienceReport:
    """Structured resilience summary for a loop session."""

    run_id: str
    total_sub_requirements: int
    passed_sub_requirements: int
    failed_sub_requirements: int
    pass_rate: float
    terminal_reason: Terminal | str | None
    total_turns: int
    recovery_events: list[str] = field(default_factory=list)
    sub_requirements: dict[str, dict[str, Any]] = field(default_factory=dict)
    token_metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        term_str = (
            self.terminal_reason.value
            if hasattr(self.terminal_reason, "value")
            else str(self.terminal_reason or "")
        )
        return {
            "run_id": self.run_id,
            "total_sub_requirements": self.total_sub_requirements,
            "passed_sub_requirements": self.passed_sub_requirements,
            "failed_sub_requirements": self.failed_sub_requirements,
            "pass_rate": round(self.pass_rate, 4),
            "terminal_reason": term_str,
            "total_turns": self.total_turns,
            "recovery_events": self.recovery_events,
            "sub_requirements": self.sub_requirements,
            "token_metrics": self.token_metrics,
        }


class ResilienceTracker:
    """
    Tracks and analyzes resilience metrics during a TDD run.
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.sub_requirements: dict[str, SubRequirementMetric] = {}
        self.transitions: list[Transition] = []
        self.terminal_event: Terminal | str | None = None
        self.turn_count: int = 0

    def record_sub_requirement(
        self,
        req_id: str,
        description: str,
        phase: TddPhase,
        passed: bool,
        iterations: int = 1,
        details: str = "",
    ) -> SubRequirementMetric:
        """Record or update the outcome of a specific sub-requirement."""
        status = SubRequirementStatus.PASSED if passed else SubRequirementStatus.FAILED
        metric = SubRequirementMetric(
            req_id=req_id,
            description=description,
            phase=phase,
            status=status,
            iterations=iterations,
            details=details,
        )
        self.sub_requirements[req_id] = metric
        return metric

    def record_transition(self, transition: Transition) -> None:
        """Record an iteration continuation reason."""
        self.transitions.append(transition)
        if getattr(transition, "reason", None) == Continue.NEXT_TURN:
            self.turn_count += 1

    def record_terminal(self, terminal: Terminal | Terminated | str) -> None:
        """Record the terminal exit reason for the loop."""
        if isinstance(terminal, Terminated):
            self.terminal_event = terminal.reason
        else:
            self.terminal_event = terminal

    def build_report(self) -> ResilienceReport:
        """Compute the final structured resilience report."""
        total = len(self.sub_requirements)
        passed = sum(
            1 for m in self.sub_requirements.values() if m.status == SubRequirementStatus.PASSED
        )
        failed = sum(
            1 for m in self.sub_requirements.values() if m.status == SubRequirementStatus.FAILED
        )

        if total > 0:
            pass_rate = passed / total
        else:
            term_val = (
                self.terminal_event.value
                if hasattr(self.terminal_event, "value")
                else str(self.terminal_event or "")
            )
            pass_rate = 1.0 if term_val == Terminal.COMPLETED.value else 0.0

        recovery_events: list[str] = []
        for t in self.transitions:
            reason = getattr(t, "reason", None)
            if reason is not None and reason != Continue.NEXT_TURN:
                reason_str = reason.value if hasattr(reason, "value") else str(reason)
                recovery_events.append(reason_str)

        sub_req_dicts = {k: v.to_dict() for k, v in self.sub_requirements.items()}
        tokens = get_token_usage_summary()

        return ResilienceReport(
            run_id=self.run_id,
            total_sub_requirements=total,
            passed_sub_requirements=passed,
            failed_sub_requirements=failed,
            pass_rate=pass_rate,
            terminal_reason=self.terminal_event,
            total_turns=self.turn_count,
            recovery_events=recovery_events,
            sub_requirements=sub_req_dicts,
            token_metrics=tokens,
        )

    def emit_json_log(self, indent: int = 2) -> str:
        """Serialize the resilience report into JSON log format."""
        report = self.build_report()
        return json.dumps(report.to_dict(), indent=indent)

    def render_scorecard(self) -> str:
        """Render a human-readable TDD resilience scorecard."""
        report = self.build_report()
        term_str = (
            report.terminal_reason.value
            if hasattr(report.terminal_reason, "value")
            else str(report.terminal_reason or "N/A")
        )
        rate_percent = f"{report.pass_rate * 100:.1f}%"

        lines = [
            f"=== TDD Resilience Scorecard [{report.run_id}] ===",
            f"Terminal Exit   : {term_str}",
            f"Pass Rate       : {rate_percent} ({report.passed_sub_requirements}/{report.total_sub_requirements})",
            f"Total Turns     : {report.total_turns}",
            f"Recovery Events : {len(report.recovery_events)} ({', '.join(report.recovery_events) or 'none'})",
            "--- Sub-Requirements ---",
        ]
        for req_id, data in report.sub_requirements.items():
            mark = "✓" if data["status"] == SubRequirementStatus.PASSED.value else "✗"
            lines.append(f"  [{mark}] {req_id} ({data['phase']}): {data['description']}")
            if data.get("details"):
                lines.append(f"      Note: {data['details']}")

        lines.append("==========================================")
        return "\n".join(lines)
