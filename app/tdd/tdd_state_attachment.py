"""
TDDAgents' own `tdd_state` source: the phase ledger, as observed.

The ledger is written by `RunTests` alone (barrier 1), so announcing it is an observation, not
a claim the model made. Between turns it is attached only when it changed since the last
announcement found in the history — the same scan-the-history dedup upstream's delta
attachments use, which is why the announcement must be persisted into `state.messages`.

After a compaction the summary is the model's account of the run and cannot be trusted with
the ledger. `create_tdd_state_attachment_message(..., after_compaction=True)` re-attaches it
from authoritative state: the ledger plus the last run `RunTests` recorded
(`ledger.TestRunRecord`) — its path, exit code and the tail of its output. The tail is bounded
by `PREVIEW_SIZE_BYTES` (`toolResultStorage.ts` -> `PREVIEW_SIZE_BYTES`, the preview a
persisted tool result keeps); the tail, not the head, because pytest prints its failure
summary last.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import HumanMessage

from app.tdd.ledger import PhaseLedger, TestRunRecord
from app.tdd.plan import TddPlan, render_plan
from app.utils.attachments import Attachment, attachment_of, tdd_state_lines
from app.utils.messages import ATTACHMENT_KEY, Message, wrap_in_system_reminder
from app.utils.tool_result_storage import PREVIEW_SIZE_BYTES

_LEDGER_FIELDS = ("phase", "tests_written", "green_passed", "refactor_passed", "coverage", "sub_requirement",
                  "plan_progress")


def tdd_state_data(ledger: PhaseLedger, plan: TddPlan | None = None) -> dict[str, Any]:
    """The ledger, and with a plan (Phase 11) the current sub-requirement and the progress."""
    data: dict[str, Any] = {
        "phase": str(ledger.phase), "tests_written": ledger.tests_written, "green_passed": ledger.green_passed,
        "refactor_passed": ledger.refactor_passed, "coverage": ledger.coverage,
    }
    if plan is not None and plan.items:
        current = plan.current
        data["sub_requirement"] = f"{current.id}. {current.text}" if current is not None else None
        data["plan_progress"] = f"{plan.done_count}/{len(plan.items)}"
    return data


def last_announced_tdd_state(messages: Sequence[Message]) -> dict[str, Any] | None:
    """The ledger fields of the most recent `tdd_state` attachment in the history, if any."""
    for message in reversed(list(messages)):
        attachment = attachment_of(message)
        if attachment and attachment.get("type") == "tdd_state":
            return {name: attachment[name] for name in _LEDGER_FIELDS if name in attachment}
    return None


def get_tdd_state_attachments(messages: Sequence[Message], context: Any) -> list[Attachment]:
    """The ledger, when it changed since it was last announced."""
    app_state = context.get_app_state()
    data = tdd_state_data(app_state.phase_ledger, app_state.tdd_plan)
    if last_announced_tdd_state(messages) == data:
        return []
    return [Attachment("tdd_state", data)]


def _tail(text: str, max_bytes: int = PREVIEW_SIZE_BYTES) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    return raw[-max_bytes:].decode("utf-8", errors="ignore")


def create_tdd_state_attachment_message(
    ledger: PhaseLedger, last_test_run: TestRunRecord | None, *, after_compaction: bool,
    plan: TddPlan | None = None,
) -> Message:
    """
    The TDD state as one meta message; after a compaction it also carries the RunTests evidence
    and the whole plan, from authoritative state rather than the summary.
    """
    data = tdd_state_data(ledger, plan)
    lines = tdd_state_lines(data)
    record: dict[str, Any] = {"type": "tdd_state", **data}
    if after_compaction:
        record["afterCompaction"] = True
        if plan is not None and plan.items:
            lines += ["", "Plan (recorded by the system, not by the model):", render_plan(plan)]
        lines.append("")
        if last_test_run is None:
            lines.append("No RunTests run has been observed yet.")
        else:
            lines.append(
                f"Last RunTests run (recorded by RunTests, not by the model): {last_test_run.test_path} "
                f"exited {last_test_run.exit_code}"
            )
            output = last_test_run.stdout
            if last_test_run.stderr.strip():
                output = f"{output}\n--- STDERR ---\n{last_test_run.stderr}"
            lines.append(_tail(output))
    return HumanMessage(
        content=wrap_in_system_reminder("\n".join(lines)),
        additional_kwargs={"is_meta": True, ATTACHMENT_KEY: record},
    )


__all__ = [
    "create_tdd_state_attachment_message",
    "get_tdd_state_attachments",
    "last_announced_tdd_state",
    "tdd_state_data",
]
