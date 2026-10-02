"""
Unit and mutation booster tests for Part L: Metrics, Event Log, and Flow Derivation.

Covers:
- Part L1: EventType, EventEntry, EventLog collection and JSONL serialization.
- Part L2: TDD flow derivation (F1, F2, FAILURE, INCOMPLETE) and resilience ratios.
- Part L3: Deletion of dissolved graph modules and artifact export.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from app.loop.transitions import Continue, Terminal
from app.metrics.derivation import (
    FlowType,
    compute_resilience_metrics,
    derive_flow_type,
)
from app.metrics.event_log import EventEntry, EventLog, EventType
from app.metrics.report import (
    export_full_metrics_artifacts,
    generate_resilience_report,
    generate_subreq_report,
)


# ==============================================================================
# Part L1: EventEntry & EventLog Tests
# ==============================================================================

class TestEventEntryAndLog:
    def test_event_type_str_enum_values(self) -> None:
        assert str(EventType.TRANSITION) == "transition"
        assert str(EventType.TOOL_EXECUTION) == "tool_execution"
        assert str(EventType.LEDGER_CHANGE) == "ledger_change"
        assert str(EventType.STOP_HOOK) == "stop_hook"
        assert str(EventType.TOKEN_USAGE) == "token_usage"
        assert str(EventType.PLAN_ITEM) == "plan_item"
        assert str(EventType.SESSION) == "session"

    def test_event_entry_defaults_and_to_dict(self) -> None:
        entry = EventEntry(
            event_type=EventType.TRANSITION,
            session_id="sess-100",
            turn_count=2,
            phase="red",
            transition_reason=str(Continue.NEXT_TURN),
            red_confirmed=True,
            green_passed=False,
            payload={"tool": "WriteFile"},
        )
        assert entry.event_type == EventType.TRANSITION
        assert entry.session_id == "sess-100"
        assert entry.turn_count == 2
        assert entry.phase == "red"
        assert entry.transition_reason == str(Continue.NEXT_TURN)
        assert entry.red_confirmed is True
        assert entry.green_passed is False
        assert entry.payload == {"tool": "WriteFile"}
        assert len(entry.event_id) >= 10
        assert entry.timestamp > 0.0

        d = entry.to_dict()
        assert d["event_type"] == "transition"
        assert d["session_id"] == "sess-100"
        assert d["turn_count"] == 2
        assert d["red_confirmed"] is True
        assert d["payload"] == {"tool": "WriteFile"}

    def test_event_entry_from_dict(self) -> None:
        raw: dict[str, Any] = {
            "event_type": "tool_execution",
            "session_id": "sess-200",
            "turn_count": 5,
            "phase": "green",
            "transition_reason": None,
            "red_confirmed": True,
            "green_passed": True,
            "payload": {"duration": 1.2},
            "event_id": "custom-uuid-42",
            "timestamp": 1234567.89,
        }
        entry = EventEntry.from_dict(raw)
        assert entry.event_type == EventType.TOOL_EXECUTION
        assert entry.session_id == "sess-200"
        assert entry.event_id == "custom-uuid-42"
        assert entry.timestamp == 1234567.89
        assert entry.red_confirmed is True
        assert entry.green_passed is True

    def test_event_log_collection_and_queries(self) -> None:
        log = EventLog(session_id="log-sess-1")
        assert len(log) == 0
        assert list(log) == []

        e1 = log.record_transition(
            turn_count=1,
            phase="red",
            reason=str(Continue.NEXT_TURN),
            red_confirmed=True,
            green_passed=False,
            payload={"turn": 1},
        )
        assert len(log) == 1
        assert e1.session_id == "log-sess-1"
        assert e1.phase == "red"

        e2 = log.record(
            EventType.TOOL_EXECUTION,
            turn_count=1,
            phase="red",
            payload={"tool_name": "RunTests"},
        )
        assert len(log) == 2
        assert e2.event_type == EventType.TOOL_EXECUTION

        transitions = log.get_transitions()
        assert len(transitions) == 1
        assert transitions[0].event_type == EventType.TRANSITION

        tool_events = log.filter_by_type(EventType.TOOL_EXECUTION)
        assert len(tool_events) == 1
        assert tool_events[0].payload["tool_name"] == "RunTests"

        all_ev = log.all_events()
        assert len(all_ev) == 2

        log.clear()
        assert len(log) == 0

    def test_event_log_jsonl_export_and_load(self, tmp_path: Path) -> None:
        file_path = tmp_path / "events" / "events.jsonl"
        log = EventLog(session_id="export-sess")
        log.record_transition(
            turn_count=1,
            phase="red",
            reason=str(Continue.NEXT_TURN),
            red_confirmed=True,
            green_passed=False,
        )
        log.record_transition(
            turn_count=2,
            phase="green",
            reason=str(Terminal.COMPLETED),
            red_confirmed=True,
            green_passed=True,
        )

        count = log.export_jsonl(file_path)
        assert count == 2
        assert file_path.is_file()

        # Load back
        loaded = EventLog.load_jsonl(file_path)
        assert len(loaded) == 2
        assert loaded.session_id == "export-sess"
        t0 = loaded.get_transitions()[0]
        assert t0.phase == "red"
        assert t0.red_confirmed is True
        t1 = loaded.get_transitions()[1]
        assert t1.phase == "green"
        assert t1.green_passed is True

    def test_event_log_load_missing_file_returns_empty(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.jsonl"
        loaded = EventLog.load_jsonl(missing)
        assert len(loaded) == 0

    def test_event_log_load_skips_empty_lines(self, tmp_path: Path) -> None:
        file_path = tmp_path / "with_blanks.jsonl"
        entry_dict = EventEntry(
            event_type=EventType.SESSION,
            session_id="blank-sess",
        ).to_dict()
        file_path.write_text(
            f"\n   \n{json.dumps(entry_dict)}\n\n",
            encoding="utf-8",
        )
        loaded = EventLog.load_jsonl(file_path)
        assert len(loaded) == 1
        assert loaded.session_id == "blank-sess"


# ==============================================================================
# Part L2: Flow Derivation Tests (F1, F2, FAILURE, INCOMPLETE)
# ==============================================================================

class TestFlowDerivation:
    def test_derive_flow_empty_returns_incomplete(self) -> None:
        assert derive_flow_type([]) == FlowType.INCOMPLETE

    def test_derive_flow_no_transitions_returns_incomplete(self) -> None:
        events = [
            EventEntry(
                event_type=EventType.TOKEN_USAGE,
                session_id="s1",
                payload={"tokens": 50},
            )
        ]
        assert derive_flow_type(events) == FlowType.INCOMPLETE

    def test_derive_flow_f1_clean_tdd(self) -> None:
        events = [
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                turn_count=1,
                phase="red",
                transition_reason=str(Continue.NEXT_TURN),
                red_confirmed=True,
                green_passed=False,
            ),
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                turn_count=2,
                phase="green",
                transition_reason=str(Terminal.COMPLETED),
                red_confirmed=True,
                green_passed=True,
            ),
        ]
        assert derive_flow_type(events) == FlowType.F1

    def test_derive_flow_f2_green_in_red_premature(self) -> None:
        # Green passed without red ever confirmed
        events = [
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                turn_count=1,
                phase="red",
                transition_reason=str(Continue.NEXT_TURN),
                red_confirmed=False,
                green_passed=True,
            ),
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                turn_count=2,
                phase="green",
                transition_reason=str(Terminal.COMPLETED),
                red_confirmed=False,
                green_passed=True,
            ),
        ]
        assert derive_flow_type(events) == FlowType.F2

    def test_derive_flow_f2_stop_hook_blocked_without_red(self) -> None:
        events = [
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                turn_count=1,
                phase="red",
                transition_reason=str(Continue.STOP_HOOK_BLOCKING),
                red_confirmed=False,
                green_passed=False,
            ),
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                turn_count=2,
                phase="green",
                transition_reason=str(Terminal.COMPLETED),
                red_confirmed=True,
                green_passed=True,
            ),
        ]
        assert derive_flow_type(events) == FlowType.F2

    def test_derive_flow_f2_via_stop_hook_event_payload(self) -> None:
        events = [
            EventEntry(
                event_type=EventType.STOP_HOOK,
                session_id="s1",
                turn_count=1,
                payload={"blocking": True},
                red_confirmed=False,
            ),
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                turn_count=2,
                phase="green",
                transition_reason=str(Terminal.COMPLETED),
                red_confirmed=True,
                green_passed=True,
            ),
        ]
        assert derive_flow_type(events) == FlowType.F2

    def test_derive_flow_failure_terminal_error(self) -> None:
        events = [
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                turn_count=1,
                phase="red",
                transition_reason=str(Terminal.MODEL_ERROR),
                red_confirmed=False,
                green_passed=False,
            )
        ]
        assert derive_flow_type(events) == FlowType.FAILURE

    def test_derive_flow_failure_blocking_limit(self) -> None:
        events = [
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                turn_count=20,
                phase="red",
                transition_reason=str(Terminal.BLOCKING_LIMIT),
                red_confirmed=True,
                green_passed=False,
            )
        ]
        assert derive_flow_type(events) == FlowType.FAILURE

    def test_derive_flow_fallback_to_non_transition_events_with_phase(self) -> None:
        events = [
            EventEntry(
                event_type=EventType.PLAN_ITEM,
                session_id="s1",
                phase="green",
                transition_reason=str(Terminal.COMPLETED),
                red_confirmed=True,
                green_passed=True,
            )
        ]
        assert derive_flow_type(events) == FlowType.F1

    def test_derive_flow_completed_with_red_confirmed_fallback_to_f1(self) -> None:
        events = [
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                phase="refactor",
                transition_reason=str(Terminal.COMPLETED),
                red_confirmed=True,
                green_passed=False,
            )
        ]
        assert derive_flow_type(events) == FlowType.F1

    def test_derive_flow_completed_without_red_or_green_fails(self) -> None:
        events = [
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                phase="red",
                transition_reason="some_custom_continue",
                red_confirmed=False,
                green_passed=False,
            )
        ]
        assert derive_flow_type(events) == FlowType.FAILURE


# ==============================================================================
# Part L2: Resilience Metrics Computation
# ==============================================================================

class TestResilienceMetricsComputation:
    def test_compute_resilience_zero_failures(self) -> None:
        events = [
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                phase="red",
                transition_reason=str(Continue.NEXT_TURN),
                red_confirmed=True,
                payload={"plan_index": 0},
            ),
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                phase="green",
                transition_reason=str(Terminal.COMPLETED),
                red_confirmed=True,
                green_passed=True,
                payload={"plan_index": 0},
            ),
        ]
        res = compute_resilience_metrics(events)
        assert res.total_failures == 0
        assert res.corrected_failures == 0
        assert res.self_correction_rate == 100.0
        assert res.test_faults == 0
        assert res.implementation_faults == 0
        assert res.test_fault_ratio == 0.0
        assert res.implementation_fault_ratio == 0.0
        assert res.f1_count == 1
        assert res.f2_count == 0
        assert res.failure_count == 0
        assert res.total_subreqs == 1

    def test_compute_resilience_with_implementation_correction(self) -> None:
        events = [
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                phase="red",
                transition_reason=str(Continue.NEXT_TURN),
                red_confirmed=True,
                payload={"plan_index": 0},
            ),
            # Green failure: implementation bug detected
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                phase="green",
                transition_reason=str(Continue.STOP_HOOK_BLOCKING),
                red_confirmed=True,
                green_passed=False,
                payload={"plan_index": 0, "test_failed": True},
            ),
            # Green correction: developer fixed bug, tests pass
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                phase="green",
                transition_reason=str(Terminal.COMPLETED),
                red_confirmed=True,
                green_passed=True,
                payload={"plan_index": 0},
            ),
        ]
        res = compute_resilience_metrics(events)
        assert res.total_failures == 1
        assert res.implementation_faults == 1
        assert res.test_faults == 0
        assert res.corrected_failures == 1
        assert res.self_correction_rate == 100.0
        assert res.implementation_fault_ratio == 100.0
        assert res.test_fault_ratio == 0.0
        assert res.f1_count == 1

    def test_compute_resilience_with_test_syntax_correction(self) -> None:
        events = [
            # Red syntax failure
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                phase="red",
                transition_reason=str(Continue.NEXT_TURN),
                red_confirmed=False,
                payload={"plan_index": 0, "test_syntax_error": True},
            ),
            # Red correction
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                phase="red",
                transition_reason=str(Continue.NEXT_TURN),
                red_confirmed=True,
                payload={"plan_index": 0},
            ),
            # Green pass
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                phase="green",
                transition_reason=str(Terminal.COMPLETED),
                red_confirmed=True,
                green_passed=True,
                payload={"plan_index": 0},
            ),
        ]
        res = compute_resilience_metrics(events)
        assert res.total_failures == 1
        assert res.test_faults == 1
        assert res.implementation_faults == 0
        assert res.corrected_failures == 1
        assert res.self_correction_rate == 100.0
        assert res.test_fault_ratio == 100.0
        assert res.implementation_fault_ratio == 0.0

    def test_compute_resilience_multiple_subreqs_breakdown(self) -> None:
        # Item 0: F1 Clean
        ev_item_0 = [
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                phase="red",
                transition_reason=str(Continue.NEXT_TURN),
                red_confirmed=True,
                payload={"plan_index": 0},
            ),
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                phase="green",
                transition_reason=str(Terminal.COMPLETED),
                red_confirmed=True,
                green_passed=True,
                payload={"plan_index": 0},
            ),
        ]
        # Item 1: F2 Defensive
        ev_item_1 = [
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                phase="red",
                transition_reason=str(Continue.STOP_HOOK_BLOCKING),
                red_confirmed=False,
                payload={"plan_index": 1},
            ),
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                phase="green",
                transition_reason=str(Terminal.COMPLETED),
                red_confirmed=False,
                green_passed=True,
                payload={"plan_index": 1},
            ),
        ]
        # Item 2: Failed
        ev_item_2 = [
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                phase="red",
                transition_reason=str(Terminal.MODEL_ERROR),
                red_confirmed=False,
                payload={"plan_index": 2},
            )
        ]
        res = compute_resilience_metrics(ev_item_0 + ev_item_1 + ev_item_2)
        assert res.total_subreqs == 3
        assert res.f1_count == 1
        assert res.f2_count == 1
        assert res.failure_count == 1


# ==============================================================================
# Reporting & Artifact Export Tests
# ==============================================================================

class TestMetricsReporting:
    
    def test_export_jsonl_logging_and_encoding(self, tmp_path, caplog) -> None:
        import logging
        from unittest.mock import patch
        
        caplog.set_level(logging.DEBUG)
        log = EventLog(session_id="log-sess")
        log.record(EventType.SESSION)
        out_file = tmp_path / "log.jsonl"
        
        with patch("builtins.open") as mock_open:
            with patch("app.metrics.event_log.json.dumps", return_value="{}") as mock_dumps:
                log.export_jsonl(out_file)
                
                mock_open.assert_called_once_with(out_file, "w", encoding="utf-8")
                
                # Check ensure_ascii=False
                mock_dumps.assert_called_once()
                args, kwargs = mock_dumps.call_args
                assert kwargs.get("ensure_ascii") is False
                
        assert caplog.messages[-1] == f"Exported 1 events to {out_file}"

    def test_generate_subreq_report(self, tmp_path: Path) -> None:
        plan = ["Setup module", "Implement logic"]
        subreq_events = {
            0: [
                EventEntry(
                    event_type=EventType.TRANSITION,
                    session_id="s1",
                    phase="red",
                    red_confirmed=True,
                ),
                EventEntry(
                    event_type=EventType.TRANSITION,
                    session_id="s1",
                    phase="green",
                    transition_reason=str(Terminal.COMPLETED),
                    green_passed=True,
                ),
            ],
            1: [
                EventEntry(
                    event_type=EventType.TRANSITION,
                    session_id="s1",
                    phase="red",
                    transition_reason=str(Terminal.MODEL_ERROR),
                )
            ],
        }
        report_file = generate_subreq_report(plan, subreq_events, tmp_path)
        assert report_file.is_file()
        content = report_file.read_text(encoding="utf-8")
        assert "SUBREQ RESULTS REPORT" in content
        assert "Total sub-requirements: 2" in content
        assert "F1 (Clean TDD): 1 (50.00%)" in content
        assert "Failures: 1 (50.00%)" in content
        assert "Sub-requirement 1: Setup module" in content
        assert "Status: SUCCESS | Flow: F1" in content
        assert "Sub-requirement 2: Implement logic" in content
        assert "Status: FAILED | Flow: FAILURE" in content

    def test_generate_resilience_report(self, tmp_path: Path) -> None:
        events = [
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                phase="green",
                transition_reason=str(Continue.STOP_HOOK_BLOCKING),
                payload={"test_failed": True},
            ),
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                phase="green",
                transition_reason=str(Terminal.COMPLETED),
                green_passed=True,
            ),
        ]
        rep = generate_resilience_report(events, tmp_path)
        assert rep.is_file()
        content = rep.read_text(encoding="utf-8")
        assert "RESILIENCE METRICS REPORT (TDD)" in content
        assert "Total Detected Failures (Overall):         1" in content
        assert "Autonomously Corrected Failures:           1" in content
        assert "Self-Correction Success Rate:              100.00%" in content
        assert "Implementation Failures (Runner Green):    1 (100.00%)" in content

    def test_export_full_metrics_artifacts(self, tmp_path: Path) -> None:
        log = EventLog(session_id="full-export-test")
        log.record_transition(
            turn_count=1,
            phase="red",
            reason=str(Continue.NEXT_TURN),
            red_confirmed=True,
            green_passed=False,
            payload={"plan_index": 0},
        )
        log.record_transition(
            turn_count=2,
            phase="green",
            reason=str(Terminal.COMPLETED),
            red_confirmed=True,
            green_passed=True,
            payload={"plan_index": 0},
        )
        plan = ["Epic 1: Calculator Addition"]
        artifacts = export_full_metrics_artifacts(log, plan, tmp_path)

        assert artifacts["events_jsonl"].is_file()
        assert artifacts["subreq_results"].is_file()
        assert artifacts["resilience_metrics"].is_file()

        lines = artifacts["events_jsonl"].read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        subreq_txt = artifacts["subreq_results"].read_text(encoding="utf-8")
        assert "Epic 1: Calculator Addition" in subreq_txt
        assert "F1 (Clean TDD): 1 (100.00%)" in subreq_txt


# ==============================================================================
# Mutation Booster Tests for Part L
# ==============================================================================

class TestMetricsBooster:

    def test_record_signature_defaults(self) -> None:
        import inspect
        sig = inspect.signature(EventLog.record)
        assert sig.parameters["turn_count"].default == 0
        assert sig.parameters["phase"].default == ""
        assert sig.parameters["red_confirmed"].default is False
        assert sig.parameters["green_passed"].default is False
        assert sig.parameters["session_id"].default == ""
        
        # Test EventLog.__init__ defaults too
        sig_init = inspect.signature(EventLog.__init__)
        assert sig_init.parameters["session_id"].default == ""


    def test_record_defaults(self) -> None:
        log_empty = EventLog()
        assert log_empty.session_id == ""
        
        log = EventLog(session_id="def-sess")
        ev = log.record(EventType.SESSION)
        assert ev.turn_count == 0
        assert ev.phase == ""
        assert ev.transition_reason is None
        assert ev.red_confirmed is False
        assert ev.green_passed is False
        assert ev.payload == {}
        assert ev.session_id == "def-sess"
        assert ev.event_type == EventType.SESSION
        
        # Test default session id fallback
        ev_empty = log_empty.record(EventType.SESSION)
        assert ev_empty.session_id == ""

    def test_record_transition_all_fields(self) -> None:
        log = EventLog(session_id="trans-sess")
        ev = log.record_transition(
            turn_count=3,
            phase="green",
            reason=str(Terminal.COMPLETED),
            red_confirmed=True,
            green_passed=True,
            payload={"custom_k": "custom_v"},
        )
        assert ev.turn_count == 3
        assert ev.phase == "green"
        assert ev.transition_reason == str(Terminal.COMPLETED)
        assert ev.red_confirmed is True
        assert ev.green_passed is True
        assert ev.payload == {"custom_k": "custom_v"}
        assert ev.session_id == "trans-sess"
        assert ev.event_type == EventType.TRANSITION

    def test_event_log_export_jsonl_with_unicode(self, tmp_path: Path) -> None:
        log = EventLog(session_id="unicode-sess")
        log.record(
            EventType.TOOL_EXECUTION,
            payload={"message": "テスト ✅ unicode"},
        )
        out_file = tmp_path / "unicode.jsonl"
        count = log.export_jsonl(out_file)
        assert count == 1
        content = out_file.read_text(encoding="utf-8")
        assert "テスト ✅ unicode" in content

    @pytest.mark.parametrize(
        "reason",
        [
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
        ],
    )
    def test_derive_flow_all_failure_reasons(self, reason: str) -> None:
        events = [
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                turn_count=1,
                phase="red",
                transition_reason=reason,
                red_confirmed=False,
                green_passed=False,
            )
        ]
        assert derive_flow_type(events) == FlowType.FAILURE

    def test_compute_resilience_multi_count_and_sub_req_payload(self) -> None:
        events: list[EventEntry] = []
        for i in range(2):
            events.extend([
                EventEntry(
                    event_type=EventType.TRANSITION,
                    session_id="s1",
                    phase="red",
                    red_confirmed=True,
                    payload={"sub_req": f"f1_{i}"},
                ),
                EventEntry(
                    event_type=EventType.TRANSITION,
                    session_id="s1",
                    phase="green",
                    transition_reason=str(Terminal.COMPLETED),
                    green_passed=True,
                    payload={"sub_req": f"f1_{i}"},
                ),
            ])
        for i in range(2):
            events.extend([
                EventEntry(
                    event_type=EventType.TRANSITION,
                    session_id="s1",
                    phase="red",
                    transition_reason=str(Continue.STOP_HOOK_BLOCKING),
                    red_confirmed=False,
                    payload={"sub_req": f"f2_{i}"},
                ),
                EventEntry(
                    event_type=EventType.TRANSITION,
                    session_id="s1",
                    phase="green",
                    transition_reason=str(Terminal.COMPLETED),
                    green_passed=True,
                    payload={"sub_req": f"f2_{i}"},
                ),
            ])
        for i in range(2):
            events.append(
                EventEntry(
                    event_type=EventType.TRANSITION,
                    session_id="s1",
                    phase="red",
                    transition_reason=str(Terminal.MODEL_ERROR),
                    red_confirmed=False,
                    payload={"sub_req": f"fail_{i}"},
                )
            )
        res = compute_resilience_metrics(events)
        assert res.f1_count == 2
        assert res.f2_count == 2
        assert res.failure_count == 2
        assert res.total_subreqs == 6

    def test_compute_resilience_uncorrected_impl_fault(self) -> None:
        events = [
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                phase="green",
                transition_reason=str(Continue.STOP_HOOK_BLOCKING),
                payload={"plan_index": 0, "status": "failed"},
                green_passed=False,
            ),
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                phase="green",
                transition_reason=str(Terminal.MODEL_ERROR),
                payload={"plan_index": 0},
                green_passed=False,
            ),
        ]
        res = compute_resilience_metrics(events)
        assert res.total_failures == 1
        assert res.implementation_faults == 1
        assert res.corrected_failures == 0
        assert res.self_correction_rate == 0.0

    def test_compute_resilience_uncorrected_test_fault(self) -> None:
        events = [
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                phase="red",
                payload={"plan_index": 0, "test_syntax_error": True},
                red_confirmed=False,
            ),
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                phase="red",
                transition_reason=str(Terminal.MODEL_ERROR),
                payload={"plan_index": 0},
                red_confirmed=False,
            ),
        ]
        res = compute_resilience_metrics(events)
        assert res.total_failures == 1
        assert res.test_faults == 1
        assert res.corrected_failures == 0
        assert res.self_correction_rate == 0.0

    def test_generate_subreq_report_all_flows_and_explanations(self, tmp_path: Path) -> None:
        plan = ["Item F1", "Item F2", "Item Fail"]
        subreq_events = {
            0: [
                EventEntry(event_type=EventType.TRANSITION, session_id="s", phase="red", red_confirmed=True),
                EventEntry(
                    event_type=EventType.TRANSITION,
                    session_id="s",
                    phase="green",
                    transition_reason=str(Terminal.COMPLETED),
                    green_passed=True,
                ),
            ],
            1: [
                EventEntry(
                    event_type=EventType.TRANSITION,
                    session_id="s",
                    phase="red",
                    red_confirmed=False,
                    transition_reason=str(Continue.STOP_HOOK_BLOCKING),
                ),
                EventEntry(
                    event_type=EventType.TRANSITION,
                    session_id="s",
                    phase="green",
                    transition_reason=str(Terminal.COMPLETED),
                    green_passed=True,
                ),
            ],
            2: [
                EventEntry(
                    event_type=EventType.TRANSITION,
                    session_id="s",
                    phase="red",
                    red_confirmed=False,
                    transition_reason=str(Terminal.MODEL_ERROR),
                ),
            ],
        }
        report_file = generate_subreq_report(plan, subreq_events, tmp_path)
        content = report_file.read_text(encoding="utf-8")
        assert "SUBREQ RESULTS REPORT" in content
        assert "F1 (Clean TDD): The sub-requirement was successfully implemented" in content
        assert "F2 (Green in Red): The tests passed immediately during the Red phase" in content
        assert "FAILURE: The sub-requirement failed to meet the criteria" in content
        assert "Total sub-requirements: 3" in content
        assert "F1 (Clean TDD): 1 (33.33%)" in content
        assert "F2 (Green in Red): 1 (33.33%)" in content
        assert "Failures: 1 (33.33%)" in content

    def test_generate_subreq_report_empty_plan(self, tmp_path: Path) -> None:
        report_file = generate_subreq_report([], {}, tmp_path)
        content = report_file.read_text(encoding="utf-8")
        assert "Total sub-requirements: 0" in content
        assert "F1 (Clean TDD): 0 (0.00%)" in content
        assert "F2 (Green in Red): 0 (0.00%)" in content
        assert "Failures: 0 (0.00%)" in content

    def test_generate_resilience_report_all_fields(self, tmp_path: Path) -> None:
        events = [
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                phase="red",
                payload={"plan_index": 0, "test_syntax_error": True},
                red_confirmed=False,
            ),
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                phase="green",
                transition_reason=str(Continue.STOP_HOOK_BLOCKING),
                payload={"plan_index": 0, "test_failed": True},
                green_passed=False,
            ),
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                phase="green",
                transition_reason=str(Terminal.COMPLETED),
                payload={"plan_index": 0},
                red_confirmed=True,
                green_passed=True,
            ),
        ]
        rep = generate_resilience_report(events, tmp_path)
        content = rep.read_text(encoding="utf-8")
        assert "Total Detected Failures (Overall):         2" in content
        assert "Autonomously Corrected Failures:           2" in content
        assert "Self-Correction Success Rate:              100.00%" in content
        assert "Test Failures (Runner Red):                1 (50.00%)" in content
        assert "Implementation Failures (Runner Green):    1 (50.00%)" in content

    @pytest.mark.parametrize(
        "fail_reason",
        [
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
        ],
    )
    def test_derive_flow_failure_even_if_green_passed_earlier(self, fail_reason: str) -> None:
        # Green passed in turn 2, but turn 3 suffered an abort/terminal failure.
        # This isolates the `last_reason in (...)` branch because `green_passed` is True.
        events = [
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                turn_count=1,
                phase="red",
                red_confirmed=True,
                green_passed=False,
            ),
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                turn_count=2,
                phase="green",
                red_confirmed=True,
                green_passed=True,
            ),
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                turn_count=3,
                phase="green",
                transition_reason=fail_reason,
                red_confirmed=True,
                green_passed=False,
            ),
        ]
        assert derive_flow_type(events) == FlowType.FAILURE

    def test_derive_flow_stop_hook_with_red_confirmed_is_f1(self) -> None:
        # Red was already confirmed when stop-hook blocked -> NOT premature green, clean F1
        events = [
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                phase="red",
                red_confirmed=True,
                green_passed=False,
            ),
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                phase="green",
                transition_reason=str(Continue.STOP_HOOK_BLOCKING),
                red_confirmed=True,
                green_passed=False,
            ),
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                phase="green",
                transition_reason=str(Terminal.COMPLETED),
                red_confirmed=True,
                green_passed=True,
            ),
        ]
        assert derive_flow_type(events) == FlowType.F1

    def test_derive_flow_stop_hook_event_non_blocking_does_not_trigger_f2(self) -> None:
        events = [
            EventEntry(
                event_type=EventType.STOP_HOOK,
                session_id="s1",
                payload={"blocking": False},
                red_confirmed=False,
            ),
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                phase="red",
                red_confirmed=True,
                green_passed=False,
            ),
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                phase="green",
                transition_reason=str(Terminal.COMPLETED),
                red_confirmed=True,
                green_passed=True,
            ),
        ]
        assert derive_flow_type(events) == FlowType.F1

    def test_compute_resilience_empty_events_list(self) -> None:
        res = compute_resilience_metrics([])
        assert res.total_failures == 0
        assert res.corrected_failures == 0
        assert res.self_correction_rate == 100.0
        assert res.test_faults == 0
        assert res.implementation_faults == 0
        assert res.test_fault_ratio == 0.0
        assert res.implementation_fault_ratio == 0.0
        assert res.f1_count == 0
        assert res.f2_count == 0
        assert res.failure_count == 0
        assert res.total_subreqs == 0

    def test_compute_resilience_asymmetric_fault_ratios(self) -> None:
        # 1 test fault (corrected) + 3 implementation faults (1 corrected, 2 uncorrected)
        events = [
            # Item 0: test fault corrected, then green passes
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s",
                phase="red",
                payload={"plan_index": 0, "test_syntax_error": True},
                red_confirmed=False,
            ),
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s",
                phase="red",
                red_confirmed=True,
                payload={"plan_index": 0},
            ),
            # Item 0: 1 impl fault corrected
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s",
                phase="green",
                payload={"plan_index": 0, "status": "failed"},
                green_passed=False,
            ),
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s",
                phase="green",
                transition_reason=str(Terminal.COMPLETED),
                green_passed=True,
                payload={"plan_index": 0},
            ),
            # Item 1: 2 impl faults uncorrected
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s",
                phase="green",
                payload={"plan_index": 1, "test_failed": True},
                green_passed=False,
            ),
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s",
                phase="green",
                transition_reason=str(Continue.STOP_HOOK_BLOCKING),
                payload={"plan_index": 1},
                green_passed=False,
            ),
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s",
                phase="green",
                transition_reason=str(Terminal.MODEL_ERROR),
                payload={"plan_index": 1},
                green_passed=False,
            ),
        ]
        res = compute_resilience_metrics(events)
        assert res.total_subreqs == 2
        assert res.test_faults == 1
        assert res.implementation_faults == 3
        assert res.total_failures == 4
        # 1 test fault corrected + 1 impl fault corrected = 2 corrected
        assert res.corrected_failures == 2
        assert res.self_correction_rate == 50.0
        assert res.test_fault_ratio == 25.0
        assert res.implementation_fault_ratio == 75.0
        assert res.f1_count == 1
        assert res.failure_count == 1

    def test_event_log_custom_session_id_override(self) -> None:
        log = EventLog(session_id="default-sess")
        ev = log.record(EventType.SESSION, session_id="override-sess")
        assert ev.session_id == "override-sess"
        ev_def = log.record(EventType.SESSION)
        assert ev_def.session_id == "default-sess"

    def test_report_custom_filenames_and_str_paths(self, tmp_path: Path) -> None:
        plan = ["Step 1"]
        events = [
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                phase="red",
                red_confirmed=True,
            ),
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s1",
                phase="green",
                transition_reason=str(Terminal.COMPLETED),
                green_passed=True,
            ),
        ]
        sub_events = {0: events}
        str_dir = str(tmp_path / "custom_reports")
        subreq_path = generate_subreq_report(
            plan,
            sub_events,
            str_dir,
            filename="custom_subreq.txt",
        )
        assert subreq_path.name == "custom_subreq.txt"
        assert subreq_path.is_file()

        resilience_path = generate_resilience_report(
            events,
            str_dir,
            filename="custom_resilience.txt",
        )
        assert resilience_path.name == "custom_resilience.txt"
        assert resilience_path.is_file()

    def test_export_full_metrics_artifacts_with_str_path(self, tmp_path: Path) -> None:
        log = EventLog(session_id="str-sess")
        log.record_transition(
            turn_count=1,
            phase="green",
            reason=str(Terminal.COMPLETED),
            red_confirmed=True,
            green_passed=True,
            payload={"plan_index": 0},
        )
        str_dir = str(tmp_path / "export_str")
        artifacts = export_full_metrics_artifacts(log, ["Task A"], str_dir)
        assert artifacts["events_jsonl"].exists()
        assert artifacts["subreq_results"].exists()
        assert artifacts["resilience_metrics"].exists()

    def test_subreq_report_multi_counts_and_formatting(self, tmp_path: Path) -> None:
        plan = [
            "F1_first", "F1_second",
            "F2_first", "F2_second",
            "Fail_first", "Fail_second",
            "Unmapped_plan_item",
        ]
        sub_events = {
            0: [
                EventEntry(event_type=EventType.TRANSITION, session_id="s", phase="red", red_confirmed=True),
                EventEntry(
                    event_type=EventType.TRANSITION, session_id="s", phase="green",
                    transition_reason=str(Terminal.COMPLETED), green_passed=True,
                ),
            ],
            1: [
                EventEntry(event_type=EventType.TRANSITION, session_id="s", phase="red", red_confirmed=True),
                EventEntry(
                    event_type=EventType.TRANSITION, session_id="s", phase="green",
                    transition_reason=str(Terminal.COMPLETED), green_passed=True,
                ),
            ],
            2: [
                EventEntry(
                    event_type=EventType.TRANSITION, session_id="s", phase="red",
                    red_confirmed=False, transition_reason=str(Continue.STOP_HOOK_BLOCKING),
                ),
                EventEntry(
                    event_type=EventType.TRANSITION, session_id="s", phase="green",
                    transition_reason=str(Terminal.COMPLETED), green_passed=True,
                ),
            ],
            3: [
                EventEntry(
                    event_type=EventType.TRANSITION, session_id="s", phase="red",
                    red_confirmed=False, transition_reason=str(Continue.STOP_HOOK_BLOCKING),
                ),
                EventEntry(
                    event_type=EventType.TRANSITION, session_id="s", phase="green",
                    transition_reason=str(Terminal.COMPLETED), green_passed=True,
                ),
            ],
            4: [
                EventEntry(
                    event_type=EventType.TRANSITION, session_id="s", phase="red",
                    red_confirmed=False, transition_reason=str(Terminal.MODEL_ERROR),
                ),
            ],
            5: [
                EventEntry(
                    event_type=EventType.TRANSITION, session_id="s", phase="red",
                    red_confirmed=False, transition_reason=str(Terminal.BLOCKING_LIMIT),
                ),
            ],
            # index 6 is deliberately omitted from sub_events to test subreq_events.get(idx, [])
        }
        # Nested directory to verify parents=True
        nested_dir = tmp_path / "deep1" / "deep2" / "subreq_reports"
        report_file = generate_subreq_report(plan, sub_events, nested_dir)
        # Call again on existing directory to verify exist_ok=True
        report_file2 = generate_subreq_report(plan, sub_events, nested_dir)
        assert report_file == report_file2

        content = report_file.read_text(encoding="utf-8")
        assert "SUBREQ RESULTS REPORT" in content
        assert "=" * 80 in content
        assert "-" * 80 in content
        assert "EXPLANATION OF METRICS:" in content
        assert "Total sub-requirements: 7" in content
        assert "F1 (Clean TDD): 2 (28.57%)" in content
        assert "F2 (Green in Red): 2 (28.57%)" in content
        assert "Failures: 3 (42.86%)" in content
        assert "Status: SUCCESS | Flow: F1" in content
        assert "Status: SUCCESS | Flow: F2" in content
        assert "Status: FAILED | Flow: FAILURE" in content
        assert "Sub-requirement 1: F1_first" in content
        assert "Sub-requirement 3: F2_first" in content
        assert "Sub-requirement 7: Unmapped_plan_item" in content

    def test_resilience_report_nested_dirs_and_full_formatting(self, tmp_path: Path) -> None:
        nested_dir = tmp_path / "nested1" / "nested2" / "resilience_reports"
        events = [
            EventEntry(
                event_type=EventType.TRANSITION, session_id="s", phase="green",
                payload={"test_failed": True}, green_passed=False,
            ),
            EventEntry(
                event_type=EventType.TRANSITION, session_id="s", phase="green",
                transition_reason=str(Terminal.COMPLETED), green_passed=True,
            ),
        ]
        rep1 = generate_resilience_report(events, nested_dir)
        rep2 = generate_resilience_report(events, nested_dir)
        assert rep1 == rep2
        content = rep1.read_text(encoding="utf-8")
        assert "RESILIENCE METRICS REPORT (TDD)" in content
        assert "=" * 80 in content
        assert "-" * 80 in content
        assert "FAILURE TYPE RATIO (Tests vs Implementation)" in content
        assert "Total Detected Failures (Overall):         1" in content
        assert "Autonomously Corrected Failures:           1" in content
        assert "Self-Correction Success Rate:              100.00%" in content
        assert "Test Failures (Runner Red):                0 (0.00%)" in content
        assert "Implementation Failures (Runner Green):    1 (100.00%)" in content

    def test_export_jsonl_nested_dirs_and_non_ascii(self, tmp_path: Path) -> None:
        log = EventLog(session_id="utf8-sess")
        log.record(EventType.TOOL_EXECUTION, payload={"msg": "テスト 日本語 🚀 Unicode"})
        nested_path = tmp_path / "level1" / "level2" / "events.jsonl"
        count1 = log.export_jsonl(nested_path)
        assert count1 == 1
        # Export again to test exist_ok=True
        count2 = log.export_jsonl(nested_path)
        assert count2 == 1
        content = nested_path.read_text(encoding="utf-8")
        assert "テスト 日本語 🚀 Unicode" in content
        assert "\\u" not in content

    def test_derive_flow_type_filtering_and_fallback_branches(self) -> None:
        # Fallback event with phase but no transition reason
        ev_phase_only = [
            EventEntry(event_type=EventType.PLAN_ITEM, session_id="s", phase="green", green_passed=True)
        ]
        assert derive_flow_type(ev_phase_only) == FlowType.F2

        # Fallback event with transition reason but no phase
        ev_reason_only = [
            EventEntry(
                event_type=EventType.PLAN_ITEM, session_id="s",
                transition_reason=str(Terminal.MODEL_ERROR),
            )
        ]
        assert derive_flow_type(ev_reason_only) == FlowType.FAILURE

        # Transition event with transition_reason=None
        ev_none_reason = [
            EventEntry(
                event_type=EventType.TRANSITION, session_id="s",
                phase="red", transition_reason=None, red_confirmed=True,
            ),
            EventEntry(
                event_type=EventType.TRANSITION, session_id="s",
                phase="green", transition_reason=str(Terminal.COMPLETED),
                green_passed=True, red_confirmed=True,
            ),
        ]
        assert derive_flow_type(ev_none_reason) == FlowType.F1

        # Red confirmed, but neither green passed nor terminal completed
        ev_red_not_completed = [
            EventEntry(
                event_type=EventType.TRANSITION, session_id="s",
                phase="red", transition_reason=str(Continue.NEXT_TURN),
                red_confirmed=True, green_passed=False,
            )
        ]
        assert derive_flow_type(ev_red_not_completed) == FlowType.FAILURE

        # Red confirmed before green -> must be F1, NOT F2
        ev_clean_f1 = [
            EventEntry(
                event_type=EventType.TRANSITION, session_id="s",
                phase="red", red_confirmed=True, green_passed=False,
            ),
            EventEntry(
                event_type=EventType.TRANSITION, session_id="s",
                phase="green", transition_reason=str(Terminal.COMPLETED),
                green_passed=True, red_confirmed=True,
            ),
        ]
        assert derive_flow_type(ev_clean_f1) == FlowType.F1

    def test_compute_resilience_multi_faults_and_exact_metrics(self) -> None:
        # Item 0: 2 test faults in red, corrected by red_confirmed=True, clean green
        ev_item_0 = [
            EventEntry(
                event_type=EventType.TRANSITION, session_id="s", phase="red",
                payload={"plan_index": 0, "test_syntax_error": True}, red_confirmed=False,
            ),
            EventEntry(
                event_type=EventType.TRANSITION, session_id="s", phase="red",
                payload={"plan_index": 0, "test_syntax_error": True}, red_confirmed=False,
            ),
            EventEntry(
                event_type=EventType.TRANSITION, session_id="s", phase="red",
                payload={"plan_index": 0}, red_confirmed=True,
            ),
            EventEntry(
                event_type=EventType.TRANSITION, session_id="s", phase="green",
                transition_reason=str(Terminal.COMPLETED), green_passed=True,
                payload={"plan_index": 0}, red_confirmed=True,
            ),
        ]
        # Item 1: 3 impl faults in green (test_failed, status=failed, stop_hook_blocking),
        # corrected by green_passed=True
        ev_item_1 = [
            EventEntry(
                event_type=EventType.TRANSITION, session_id="s", phase="green",
                payload={"plan_index": 1, "status": "failed"}, green_passed=False,
            ),
            EventEntry(
                event_type=EventType.TRANSITION, session_id="s", phase="green",
                payload={"plan_index": 1, "test_failed": True}, green_passed=False,
            ),
            EventEntry(
                event_type=EventType.TRANSITION, session_id="s", phase="green",
                transition_reason=str(Continue.STOP_HOOK_BLOCKING),
                payload={"plan_index": 1}, green_passed=False,
            ),
            EventEntry(
                event_type=EventType.TRANSITION, session_id="s", phase="green",
                transition_reason=str(Terminal.COMPLETED), green_passed=True,
                payload={"plan_index": 1}, red_confirmed=False,
            ),
        ]
        # Item 2: Failure, no faults
        ev_item_2 = [
            EventEntry(
                event_type=EventType.TRANSITION, session_id="s", phase="red",
                transition_reason=str(Terminal.MODEL_ERROR),
                payload={"plan_index": 2}, red_confirmed=False,
            )
        ]
        all_events = ev_item_0 + ev_item_1 + ev_item_2
        res = compute_resilience_metrics(all_events)
        assert res.total_failures == 5
        assert res.corrected_failures == 2
        assert res.self_correction_rate == 40.0
        assert res.test_faults == 2
        assert res.implementation_faults == 3
        assert res.test_fault_ratio == 40.0
        assert res.implementation_fault_ratio == 60.0
        assert res.f1_count == 1
        assert res.f2_count == 1
        assert res.failure_count == 1
        assert res.total_subreqs == 3

    def test_compute_resilience_default_item_grouping(self) -> None:
        # Events without plan_index or sub_req group under "0"
        events = [
            EventEntry(
                event_type=EventType.TRANSITION, session_id="s", phase="red",
                red_confirmed=True,
            ),
            EventEntry(
                event_type=EventType.TRANSITION, session_id="s", phase="green",
                transition_reason=str(Terminal.COMPLETED), green_passed=True,
                red_confirmed=True,
            ),
        ]
        res = compute_resilience_metrics(events)
        assert res.total_subreqs == 1
        assert res.f1_count == 1

    def test_reports_logging_and_debug_output(
        self,
        caplog: pytest.LogCaptureFixture,
        tmp_path: Path,
    ) -> None:
        rep_dir = tmp_path / "logged_reports"
        with caplog.at_level(logging.INFO):
            generate_subreq_report(["Sub 1"], {}, rep_dir)
            generate_resilience_report([], rep_dir)
        assert "Saved subreq results report to " in caplog.text
        assert "Saved resilience metrics report to " in caplog.text

        with caplog.at_level(logging.DEBUG):
            log = EventLog(session_id="log-dbg")
            log.export_jsonl(tmp_path / "dbg.jsonl")
        assert "Exported 0 events to " in caplog.text

    def test_exact_subreq_report_lines_and_default_filename(self, tmp_path: Path) -> None:
        ev = [
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s",
                phase="green",
                transition_reason=str(Terminal.COMPLETED),
                green_passed=True,
                red_confirmed=True,
            )
        ]
        rep = generate_subreq_report(["Sub 1"], {0: ev}, tmp_path)
        assert rep.name == "subreq_results.txt"
        lines = rep.read_text(encoding="utf-8").split("\n")
        assert lines[0] == "SUBREQ RESULTS REPORT"
        assert lines[1] == "=" * 80
        assert lines[2] == "EXPLANATION OF METRICS:"
        assert lines[3] == (
            "F1 (Clean TDD): The sub-requirement was successfully implemented with a proper TDD cycle "
            "(failed tests first, then fixed)."
        )
        assert lines[4] == (
            "F2 (Green in Red): The tests passed immediately during the Red phase, "
            "but the sub-requirement was still considered successful."
        )
        assert lines[5] == (
            "FAILURE: The sub-requirement failed to meet the criteria, max retries were exceeded "
            "or infrastructure issues occurred."
        )
        assert lines[6] == "-" * 80
        assert lines[7] == "Total sub-requirements: 1"
        assert lines[8] == "F1 (Clean TDD): 1 (100.00%)"
        assert lines[9] == "F2 (Green in Red): 0 (0.00%)"
        assert lines[10] == "Failures: 0 (0.00%)"
        assert lines[11] == "=" * 80
        assert lines[12] == ""
        assert lines[13] == "Sub-requirement 1: Sub 1"
        assert lines[14] == "  Status: SUCCESS | Flow: F1"
        assert lines[15] == ""

    def test_exact_resilience_report_output_string(self, tmp_path: Path) -> None:
        rep = generate_resilience_report([], tmp_path)
        assert rep.name == "resilience_metrics.txt"
        text = rep.read_text(encoding="utf-8")
        expected = (
            "RESILIENCE METRICS REPORT (TDD)\n"
            + "=" * 80 + "\n"
            + "Total Detected Failures (Overall):         0\n"
            + "Autonomously Corrected Failures:           0\n"
            + "Self-Correction Success Rate:              100.00%\n"
            + "-" * 80 + "\n"
            + "FAILURE TYPE RATIO (Tests vs Implementation)\n"
            + "Test Failures (Runner Red):                0 (0.00%)\n"
            + "Implementation Failures (Runner Green):    0 (0.00%)\n"
            + "=" * 80 + "\n"
        )
        assert text == expected

    def test_export_full_metrics_artifacts_default_names_and_parents(self, tmp_path: Path) -> None:
        deep_dir = tmp_path / "deep_arts" / "sub"
        artifacts = export_full_metrics_artifacts(EventLog(), ["Task 1"], deep_dir)
        assert artifacts["events_jsonl"].name == "events.jsonl"
        assert artifacts["subreq_results"].name == "subreq_results.txt"
        assert artifacts["resilience_metrics"].name == "resilience_metrics.txt"
        assert artifacts["events_jsonl"].parent == deep_dir

    def test_export_jsonl_exact_line_encoding_and_newline(self, tmp_path: Path) -> None:
        log = EventLog(session_id="enc-test")
        log.record(EventType.SESSION, payload={"val": "日本語 text"})
        out_file = tmp_path / "enc_out.jsonl"
        log.export_jsonl(out_file)
        raw_bytes = out_file.read_bytes()
        assert "日本語".encode("utf-8") in raw_bytes
        assert raw_bytes.endswith(b"\n")

    def test_derive_flow_type_transitions_comprehension_isolation(self) -> None:
        e = EventEntry(
            event_type=EventType.TRANSITION,
            session_id="s",
            phase="",
            transition_reason=None,
        )
        assert derive_flow_type([e]) == FlowType.FAILURE

    def test_derive_flow_f1_without_terminal_completed(self) -> None:
        events = [
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s",
                phase="red",
                red_confirmed=True,
                green_passed=False,
            ),
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s",
                phase="green",
                red_confirmed=True,
                green_passed=True,
                transition_reason=str(Continue.NEXT_TURN),
            ),
        ]
        assert derive_flow_type(events) == FlowType.F1

    def test_derive_flow_never_red_never_green_is_failure_not_f2(self) -> None:
        events = [
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s",
                phase="red",
                transition_reason=str(Continue.NEXT_TURN),
                red_confirmed=False,
                green_passed=False,
            )
        ]
        assert derive_flow_type(events) == FlowType.FAILURE

    def test_derive_flow_red_confirmed_but_not_completed_is_failure_not_f1(self) -> None:
        events = [
            EventEntry(
                event_type=EventType.TRANSITION,
                session_id="s",
                phase="red",
                transition_reason=str(Continue.NEXT_TURN),
                red_confirmed=True,
                green_passed=False,
            )
        ]
        assert derive_flow_type(events) == FlowType.FAILURE

    def test_compute_resilience_sub_req_fallback_grouping(self) -> None:
        ev1 = EventEntry(
            event_type=EventType.TRANSITION,
            session_id="s",
            phase="green",
            green_passed=True,
            payload={"sub_req": "sub1"},
        )
        ev2 = EventEntry(
            event_type=EventType.TRANSITION,
            session_id="s",
            phase="green",
            green_passed=True,
            payload={},
        )
        res = compute_resilience_metrics([ev1, ev2])
        assert res.total_subreqs == 2
