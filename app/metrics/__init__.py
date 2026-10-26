"""
The run's event log: the `events.jsonl` entry format (`app/loop/events.py` writes it).
"""

from __future__ import annotations

from app.metrics.event_log import EventEntry, EventLog, EventType

__all__ = ["EventEntry", "EventLog", "EventType"]
