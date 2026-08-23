"""
Sync events.

Phase 8 replaces the sink below with the real event bus and collector; the emission
points in the sync engine are already where they belong, so that change is a swap of
`emit`'s body rather than a hunt through the engine.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger("TDDOrchestrator.Sync")

CheckpointKind = Literal["run_start", "tool_call", "sub_req_boundary", "run_end"]


@dataclass(frozen=True)
class SyncCheckpoint:
    """One completed sync at one of the four deterministic checkpoints."""

    kind: CheckpointKind
    direction: Literal["local_to_sandbox", "sandbox_to_local", "sandbox_to_ledger"]
    files_written: int
    files_deleted: int
    conflicts: int
    duration: float
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class SyncConflict:
    """
    A file that changed on both sides since the last baseline.

    The sandbox copy won and the local copy was preserved at `backup_path`.
    """

    path: str
    backup_path: str
    checkpoint: CheckpointKind
    timestamp: float = field(default_factory=time.time)


SyncEvent = SyncCheckpoint | SyncConflict

_SINK: list[SyncEvent] = []


def emit(event: SyncEvent) -> None:
    """Records an event. Phase 8 swaps this for the real collector."""
    _SINK.append(event)

    if isinstance(event, SyncConflict):
        logger.warning(
            "⚠️ Sync conflict on '%s' — sandbox copy won, local backed up to '%s'.",
            event.path,
            event.backup_path,
        )
    elif isinstance(event, SyncCheckpoint):
        logger.info(
            "🔄 Sync %s (%s): %d written, %d deleted, %d conflicts in %.2fs.",
            event.kind,
            event.direction,
            event.files_written,
            event.files_deleted,
            event.conflicts,
            event.duration,
        )


def drain() -> list[SyncEvent]:
    """Returns and clears the recorded events. Used by tests and, later, by reports."""
    events = list(_SINK)
    _SINK.clear()
    return events
