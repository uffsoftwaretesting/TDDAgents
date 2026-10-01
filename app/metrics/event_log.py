"""
Event Log system conforming to §3.6 and Part L1.

Provides structured, queryable in-memory recording and JSONL persistence
for loop transitions, ledger changes, stop hooks, and plan items.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
import json
import logging
from pathlib import Path
import time
from typing import Any, Iterator
import uuid

logger = logging.getLogger(__name__)


class EventType(StrEnum):
    """Categorisation of observability events across the TDD loop."""

    TRANSITION = "transition"
    TOOL_EXECUTION = "tool_execution"
    LEDGER_CHANGE = "ledger_change"
    STOP_HOOK = "stop_hook"
    TOKEN_USAGE = "token_usage"
    PLAN_ITEM = "plan_item"
    SESSION = "session"


@dataclass(frozen=True)
class EventEntry:
    """An immutable record of a single event in the TDD loop execution."""

    event_type: EventType
    session_id: str
    turn_count: int = 0
    phase: str = ""
    transition_reason: str | None = None
    red_confirmed: bool = False
    green_passed: bool = False
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """Convert entry to a JSON-serializable dictionary."""
        d = asdict(self)
        d["event_type"] = str(self.event_type)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EventEntry:
        """Construct entry from a dictionary."""
        d = dict(data)
        if "event_type" in d and isinstance(d["event_type"], str):
            d["event_type"] = EventType(d["event_type"])
        return cls(**d)


class EventLog:
    """
    In-memory queryable event log with JSONL persistence and transition extraction.
    """

    def __init__(self, session_id: str = "") -> None:
        self.session_id = session_id
        self._events: list[EventEntry] = []

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self) -> Iterator[EventEntry]:
        return iter(self._events)

    def append(self, event: EventEntry) -> None:
        """Append an event entry to the log."""
        self._events.append(event)

    def record(
        self,
        event_type: EventType,
        *,
        turn_count: int = 0,
        phase: str = "",
        transition_reason: str | None = None,
        red_confirmed: bool = False,
        green_passed: bool = False,
        payload: dict[str, Any] | None = None,
        session_id: str = "",
    ) -> EventEntry:
        """Construct and append a new event entry."""
        entry = EventEntry(
            event_type=event_type,
            session_id=session_id or self.session_id,
            turn_count=turn_count,
            phase=phase,
            transition_reason=transition_reason,
            red_confirmed=red_confirmed,
            green_passed=green_passed,
            payload=dict(payload or {}),
        )
        self.append(entry)
        return entry

    def record_transition(
        self,
        *,
        turn_count: int,
        phase: str,
        reason: str,
        red_confirmed: bool,
        green_passed: bool,
        payload: dict[str, Any] | None = None,
    ) -> EventEntry:
        """Convenience method to record a loop transition."""
        return self.record(
            EventType.TRANSITION,
            turn_count=turn_count,
            phase=phase,
            transition_reason=reason,
            red_confirmed=red_confirmed,
            green_passed=green_passed,
            payload=payload,
        )

    def filter_by_type(self, event_type: EventType) -> list[EventEntry]:
        """Return all events matching event_type."""
        return [e for e in self._events if e.event_type == event_type]

    def get_transitions(self) -> list[EventEntry]:
        """Return all transition events in chronological order."""
        return self.filter_by_type(EventType.TRANSITION)

    def all_events(self) -> list[EventEntry]:
        """Return a copy of all events."""
        return list(self._events)

    def clear(self) -> None:
        """Clear all events in the log."""
        self._events.clear()

    def export_jsonl(self, file_path: Path | str) -> int:
        """
        Write all events to a JSONL file. Returns number of lines written.
        """
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with open(path, "w", encoding="utf-8") as f:
            for ev in self._events:
                f.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")
                count += 1
        logger.debug("Exported %d events to %s", count, path)
        return count

    @classmethod
    def load_jsonl(cls, file_path: Path | str) -> EventLog:
        """
        Load an EventLog from a JSONL file.
        """
        path = Path(file_path)
        log = cls()
        if not path.is_file():
            return log

        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if not line_str:
                    continue
                data = json.loads(line_str)
                entry = EventEntry.from_dict(data)
                if not log.session_id and entry.session_id:
                    log.session_id = entry.session_id
                log.append(entry)
        return log
