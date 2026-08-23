"""
Sync events.

Small surface, but it is the seam Phase 8 replaces: the collector swaps `emit`'s body
and the emission points in the engine stay where they are. These tests pin the contract
that swap has to honor.
"""

from __future__ import annotations

import pytest

from app.sync import events
from app.sync.events import SyncCheckpoint, SyncConflict, drain, emit


def checkpoint(**overrides) -> SyncCheckpoint:
    defaults = dict(
        kind="run_start",
        direction="local_to_sandbox",
        files_written=1,
        files_deleted=0,
        conflicts=0,
        duration=0.5,
    )
    return SyncCheckpoint(**{**defaults, **overrides})


def test_emit_records_an_event():
    emit(checkpoint())
    assert len(drain()) == 1


def test_drain_clears_the_sink():
    emit(checkpoint())
    drain()
    assert drain() == []


def test_drain_preserves_emission_order():
    emit(checkpoint(kind="run_start"))
    emit(checkpoint(kind="sub_req_boundary"))
    emit(checkpoint(kind="run_end"))
    recorded = [e for e in drain() if isinstance(e, SyncCheckpoint)]
    assert [e.kind for e in recorded] == ["run_start", "sub_req_boundary", "run_end"]


def test_conflict_and_checkpoint_share_the_sink():
    emit(checkpoint())
    emit(SyncConflict(path="a.py", backup_path="a.py.local.1", checkpoint="run_end"))
    recorded = drain()
    assert isinstance(recorded[0], SyncCheckpoint)
    assert isinstance(recorded[1], SyncConflict)


def test_events_are_timestamped_automatically():
    event = SyncConflict(path="a.py", backup_path="a.py.local.1", checkpoint="run_end")
    assert event.timestamp > 0


def test_events_are_immutable():
    """Frozen so a collected event cannot be rewritten after the fact."""
    event = checkpoint()
    with pytest.raises(Exception):
        event.files_written = 99  # type: ignore[misc]


def test_conflict_records_where_the_losing_copy_went():
    event = SyncConflict(
        path="src/main.py",
        backup_path="src/main.py.local.20260823-120000",
        checkpoint="sub_req_boundary",
    )
    assert event.path == "src/main.py"
    assert event.backup_path.startswith("src/main.py.local.")
    assert event.checkpoint == "sub_req_boundary"


def test_emit_logs_a_conflict_as_a_warning(caplog):
    """
    A conflict silently overwrites one side. The warning is the only thing that tells a
    human it happened and where the losing copy went, so its wording is pinned.
    """
    with caplog.at_level("WARNING", logger="TDDOrchestrator.Sync"):
        emit(SyncConflict(path="a.py", backup_path="a.py.local.1", checkpoint="run_end"))

    assert caplog.records[0].levelname == "WARNING"
    assert caplog.records[0].getMessage() == (
        "⚠️ Sync conflict on 'a.py' — sandbox copy won, local backed up to 'a.py.local.1'."
    )


def test_emit_logs_a_checkpoint_at_info(caplog):
    with caplog.at_level("INFO", logger="TDDOrchestrator.Sync"):
        emit(
            checkpoint(
                kind="run_end",
                direction="sandbox_to_local",
                files_written=3,
                files_deleted=1,
                conflicts=2,
                duration=1.5,
            )
        )

    assert caplog.records[0].levelname == "INFO"
    assert caplog.records[0].getMessage() == (
        "🔄 Sync run_end (sandbox_to_local): 3 written, 1 deleted, 2 conflicts in 1.50s."
    )


def test_the_sink_is_module_level_state_the_collector_will_replace():
    emit(checkpoint())
    assert len(events._SINK) == 1
    drain()
    assert events._SINK == []
