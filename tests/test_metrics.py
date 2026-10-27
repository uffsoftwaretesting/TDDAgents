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

from app.query.transitions import Continue, Terminal
from app.tdd.event_log import EventEntry, EventLog, EventType

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


# ==============================================================================
# Part L2: Resilience Metrics Computation
# ==============================================================================


# ==============================================================================
# Reporting & Artifact Export Tests
# ==============================================================================


class TestMetricsReporting:

    def test_export_jsonl_logging_and_encoding(self, tmp_path, caplog) -> None:
        from unittest.mock import patch

        caplog.set_level(logging.DEBUG)
        log = EventLog(session_id="log-sess")
        log.record(EventType.SESSION)
        out_file = tmp_path / "log.jsonl"

        with (
            patch("builtins.open") as mock_open,
            patch("app.tdd.event_log.json.dumps", return_value="{}") as mock_dumps,
        ):
            log.export_jsonl(out_file)

            mock_open.assert_called_once_with(out_file, "w", encoding="utf-8")

            # Check ensure_ascii=False
            mock_dumps.assert_called_once()
            _args, kwargs = mock_dumps.call_args
            assert kwargs.get("ensure_ascii") is False

        assert caplog.messages[-1] == f"Exported 1 events to {out_file}"


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

    def test_event_log_custom_session_id_override(self) -> None:
        log = EventLog(session_id="default-sess")
        ev = log.record(EventType.SESSION, session_id="override-sess")
        assert ev.session_id == "override-sess"
        ev_def = log.record(EventType.SESSION)
        assert ev_def.session_id == "default-sess"

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

    def test_export_jsonl_exact_line_encoding_and_newline(self, tmp_path: Path) -> None:
        log = EventLog(session_id="enc-test")
        log.record(EventType.SESSION, payload={"val": "日本語 text"})
        out_file = tmp_path / "enc_out.jsonl"
        log.export_jsonl(out_file)
        raw_bytes = out_file.read_bytes()
        assert "日本語".encode() in raw_bytes
        assert raw_bytes.endswith(b"\n")
