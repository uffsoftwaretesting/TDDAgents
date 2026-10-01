"""
Metrics reporting and artifact export conforming to Part L.

Generates `subreq_results.txt` and `resilience_metrics.txt` reports
from the EventLog and derived flow metrics.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Mapping, Sequence

from app.metrics.derivation import FlowType, compute_resilience_metrics, derive_flow_type
from app.metrics.event_log import EventEntry, EventLog

logger = logging.getLogger(__name__)


def generate_subreq_report(
    plan: Sequence[str],
    subreq_events: Mapping[int, Sequence[EventEntry]],
    artifacts_dir: Path | str,
    filename: str = "subreq_results.txt",
) -> Path:
    """
    Writes the standard `subreq_results.txt` report from plan items and event sequences.
    """
    out_dir = Path(artifacts_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / filename

    total = len(plan)
    f1_count = 0
    f2_count = 0
    failure_count = 0
    lines: list[str] = []

    for idx, item in enumerate(plan):
        events = subreq_events.get(idx, [])
        flow = derive_flow_type(events)
        if flow == FlowType.F1:
            f1_count += 1
            status = "SUCCESS"
        elif flow == FlowType.F2:
            f2_count += 1
            status = "SUCCESS"
        else:
            failure_count += 1
            status = "FAILED"

        lines.append(f"Sub-requirement {idx + 1}: {item}")
        lines.append(f"  Status: {status} | Flow: {flow}")
        lines.append("")

    f1_rate = (f1_count / total * 100.0) if total > 0 else 0.0
    f2_rate = (f2_count / total * 100.0) if total > 0 else 0.0
    fail_rate = (failure_count / total * 100.0) if total > 0 else 0.0

    header = [
        "SUBREQ RESULTS REPORT",
        "=" * 80,
        "EXPLANATION OF METRICS:",
        (
            "F1 (Clean TDD): The sub-requirement was successfully implemented with a proper TDD cycle "
            "(failed tests first, then fixed)."
        ),
        (
            "F2 (Green in Red): The tests passed immediately during the Red phase, "
            "but the sub-requirement was still considered successful."
        ),
        (
            "FAILURE: The sub-requirement failed to meet the criteria, max retries were exceeded "
            "or infrastructure issues occurred."
        ),
        "-" * 80,
        f"Total sub-requirements: {total}",
        f"F1 (Clean TDD): {f1_count} ({f1_rate:.2f}%)",
        f"F2 (Green in Red): {f2_count} ({f2_rate:.2f}%)",
        f"Failures: {failure_count} ({fail_rate:.2f}%)",
        "=" * 80,
        "",
    ]

    content = "\n".join(header + lines)
    report_path.write_text(content, encoding="utf-8")
    logger.info("Saved subreq results report to %s", report_path)
    return report_path


def generate_resilience_report(
    events: Sequence[EventEntry],
    artifacts_dir: Path | str,
    filename: str = "resilience_metrics.txt",
) -> Path:
    """
    Writes the standard `resilience_metrics.txt` report from an event sequence.
    """
    out_dir = Path(artifacts_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / filename

    m = compute_resilience_metrics(events)

    content = (
        "RESILIENCE METRICS REPORT (TDD)\n"
        + "=" * 80 + "\n"
        + f"Total Detected Failures (Overall):         {m.total_failures}\n"
        + f"Autonomously Corrected Failures:           {m.corrected_failures}\n"
        + f"Self-Correction Success Rate:              {m.self_correction_rate:.2f}%\n"
        + "-" * 80 + "\n"
        + "FAILURE TYPE RATIO (Tests vs Implementation)\n"
        + f"Test Failures (Runner Red):                {m.test_faults} ({m.test_fault_ratio:.2f}%)\n"
        + (
            f"Implementation Failures (Runner Green):    {m.implementation_faults} "
            f"({m.implementation_fault_ratio:.2f}%)\n"
        )
        + "=" * 80 + "\n"
    )

    report_path.write_text(content, encoding="utf-8")
    logger.info("Saved resilience metrics report to %s", report_path)
    return report_path


def export_full_metrics_artifacts(
    event_log: EventLog,
    plan: Sequence[str],
    artifacts_dir: Path | str,
) -> dict[str, Path]:
    """
    Export all metric artifacts (events.jsonl, subreq_results.txt, resilience_metrics.txt).
    """
    out_dir = Path(artifacts_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    jsonl_path = out_dir / "events.jsonl"
    event_log.export_jsonl(jsonl_path)

    # Partition events by plan index
    subreq_events: dict[int, list[EventEntry]] = {}
    for ev in event_log.all_events():
        idx_val = ev.payload.get("plan_index")
        if isinstance(idx_val, int):
            subreq_events.setdefault(idx_val, []).append(ev)

    subreq_path = generate_subreq_report(plan, subreq_events, out_dir)
    resilience_path = generate_resilience_report(event_log.all_events(), out_dir)

    return {
        "events_jsonl": jsonl_path,
        "subreq_results": subreq_path,
        "resilience_metrics": resilience_path,
    }
