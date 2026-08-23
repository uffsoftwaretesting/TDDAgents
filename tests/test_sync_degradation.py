"""
How the sync engine behaves when individual operations fail.

The engine is deliberately forgiving in several places: a marker it cannot write, a
file it cannot delete, a lifetime it cannot extend, a backup it cannot take. Each of
those is a `try/except` that swallows the error and logs instead — and until now not one
of those branches was exercised by any test.

That matters more than it looks. A sync that half-fails silently is the worst outcome
available: the baseline advances, the next checkpoint skips the delta, and a file is
quietly lost. These tests pin the two things that keep that from happening — the engine
keeps going, and it says what it could not do.

The log assertions are deliberate. For an operation whose whole contract is "degrade
and report", the report *is* the behavior.
"""

from __future__ import annotations

import pytest

from app.sync import events
from app.sync.engine import SYNC_MARKER, SyncEngine
from app.sync.events import SyncCheckpoint, SyncConflict
from app.workspace.base import CommandResult, WorkspaceError, WorkspaceTransportError

SYNC_LOGGER = "TDDOrchestrator.Sync"


def messages(caplog) -> list[str]:
    """The rendered log lines from the sync logger, in order."""
    return [r.getMessage() for r in caplog.records if r.name == SYNC_LOGGER]


class BrokenWorkspace:
    """A workspace whose chosen operations raise WorkspaceError."""

    def __init__(self, inner, fail_on: set[str]) -> None:
        self._inner = inner
        self._fail_on = fail_on
        self.kind = inner.kind

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if name not in self._fail_on:
            return attr

        def _boom(*args, **kwargs):
            raise WorkspaceTransportError(f"injected {name} failure")

        return _boom


# ── The sync marker ──────────────────────────────────────────────────────────

def test_a_marker_that_cannot_be_written_warns_but_does_not_abort(
    make_fake_workspace, local_ws, baseline_path, caplog
):
    """
    The marker is an optimisation for the next ledger scan. Losing it costs a full
    rescan, which is why it must not take the whole sync down with it.
    """
    sandbox = make_fake_workspace("sandbox", {"a.py": "A"})
    sandbox.fail_write_on = {SYNC_MARKER}
    # FakeWorkspace raises RuntimeError; the engine only catches WorkspaceError, so wrap
    # the write to raise the right family.
    broken = BrokenWorkspace(sandbox, {"write_file"})

    engine = SyncEngine(broken, local_ws, baseline_path)
    with caplog.at_level("WARNING", logger=SYNC_LOGGER):
        engine.reconcile_ledger({})

    assert messages(caplog) == [
        "Could not update the sync marker: injected write_file failure"
    ]


# ── Deletion ─────────────────────────────────────────────────────────────────

def test_a_delete_that_fails_is_logged_and_the_sync_continues(
    make_fake_workspace, baseline_path, caplog
):
    """
    A propagated deletion that the other side refuses must not abort the pass — the
    remaining files still need to move.
    """
    sandbox = make_fake_workspace("sandbox", {"gone.py": "x"})
    local = make_fake_workspace("local")

    SyncEngine(sandbox, local, baseline_path).seed()
    assert local.files["gone.py"] == "x"

    del sandbox.files["gone.py"]
    sandbox.files["still-here.py"] = "new"
    broken_local = BrokenWorkspace(local, {"delete_file"})

    with caplog.at_level("DEBUG", logger=SYNC_LOGGER):
        checkpoint = SyncEngine(sandbox, broken_local, baseline_path).flush()

    assert isinstance(checkpoint, SyncCheckpoint)
    assert messages(caplog)[0] == (
        "Delete of 'gone.py' skipped: injected delete_file failure"
    )
    # The pass carried on and still moved the file that could be moved.
    assert local.files["still-here.py"] == "new"


def test_a_skipped_deletion_says_why(fake_sandbox, local_ws, baseline_path, caplog):
    """The operator needs to know a delete did not propagate, and on what grounds."""
    fake_sandbox.files["f.py"] = "v1"
    engine = SyncEngine(fake_sandbox, local_ws, baseline_path)
    engine.seed()

    del fake_sandbox.files["f.py"]
    local_ws.write_file("f.py", "edited locally")

    with caplog.at_level("INFO", logger=SYNC_LOGGER):
        engine.flush()

    assert messages(caplog)[0] == (
        "↔️  Deletion of 'f.py' not propagated: deleted on one side, modified on the other"
    )


# ── Conflict backup ──────────────────────────────────────────────────────────

def test_a_backup_that_cannot_be_written_warns_and_emits_no_conflict(
    make_fake_workspace, baseline_path, caplog
):
    """
    The SyncConflict event promises a recoverable copy at `backup_path`. If the backup
    could not be taken there is no such copy, so emitting the event anyway would be a
    lie to Phase 8's collector.
    """
    sandbox = make_fake_workspace("sandbox", {"a.py": "v1"})
    local = make_fake_workspace("local")

    engine = SyncEngine(sandbox, local, baseline_path)
    engine.seed()
    events.drain()

    sandbox.files["a.py"] = "sandbox-version"
    local.files["a.py"] = "local-version"
    broken_local = BrokenWorkspace(local, {"write_file"})

    with caplog.at_level("WARNING", logger=SYNC_LOGGER):
        with pytest.raises(WorkspaceError):
            SyncEngine(sandbox, broken_local, baseline_path).flush()

    assert messages(caplog) == [
        "Could not back up local 'a.py' before overwrite: injected write_file failure"
    ]
    assert [e for e in events.drain() if isinstance(e, SyncConflict)] == []


def test_a_successful_backup_names_the_file_it_saved(
    fake_sandbox, local_ws, baseline_path, caplog
):
    fake_sandbox.files["a.py"] = "v1"
    engine = SyncEngine(fake_sandbox, local_ws, baseline_path)
    engine.seed()
    events.drain()
    caplog.clear()

    fake_sandbox.files["a.py"] = "sandbox-version"
    local_ws.write_file("a.py", "local-version")

    with caplog.at_level("WARNING", logger=SYNC_LOGGER):
        engine.flush()

    conflict = [m for m in messages(caplog) if "conflict" in m.lower()]
    assert len(conflict) == 1
    assert conflict[0].startswith("⚠️ Sync conflict on 'a.py' — sandbox copy won")
    assert "a.py.local." in conflict[0]


# ── Sandbox lifetime ─────────────────────────────────────────────────────────

class _FailingAdapter:
    def refresh_timeout(self, force: bool = False) -> bool:
        raise WorkspaceTransportError("cannot reach the sandbox API")


def test_a_lifetime_extension_that_fails_does_not_stop_the_checkpoint(
    fake_sandbox, local_ws, baseline_path, caplog
):
    """
    Failing to extend the lifetime means the sandbox may die later. Refusing to sync
    now would guarantee losing the work immediately, which is strictly worse.
    """
    fake_sandbox.files["a.py"] = "A"
    fake_sandbox.adapter = _FailingAdapter()
    engine = SyncEngine(fake_sandbox, local_ws, baseline_path)

    with caplog.at_level("WARNING", logger=SYNC_LOGGER):
        checkpoint = engine.sync_at_checkpoint("sub_req_boundary")

    assert isinstance(checkpoint, SyncCheckpoint)
    assert messages(caplog)[0] == (
        "Could not extend the sandbox lifetime at a checkpoint: "
        "cannot reach the sandbox API"
    )
    assert local_ws.read_file("a.py") == "A"


# ── Changed-file discovery ───────────────────────────────────────────────────

def test_a_failed_find_warns_before_falling_back_to_a_full_scan(
    fake_sandbox, local_ws, baseline_path, caplog
):
    fake_sandbox.files[SYNC_MARKER] = "0"
    fake_sandbox.files["a.py"] = "A"
    find_cmd = f"find . -type f -newer {SYNC_MARKER}"
    fake_sandbox.command_results[find_cmd] = CommandResult(
        stdout="", stderr="find: broken", exit_code=3, duration=0.0, workspace="sandbox"
    )

    engine = SyncEngine(fake_sandbox, local_ws, baseline_path)
    with caplog.at_level("WARNING", logger=SYNC_LOGGER):
        updated, _ = engine.reconcile_ledger({})

    assert messages(caplog) == [
        "Could not list changed files (exit 3); falling back to a full scan."
    ]
    assert "a.py" in updated


def test_find_output_paths_are_normalised(fake_sandbox, local_ws, baseline_path):
    """`find .` prints './a.py'; the ledger is keyed on workspace-relative paths."""
    fake_sandbox.files[SYNC_MARKER] = "0"
    fake_sandbox.files["a.py"] = "A"
    fake_sandbox.files["pkg/b.py"] = "B"
    find_cmd = f"find . -type f -newer {SYNC_MARKER}"
    fake_sandbox.command_results[find_cmd] = CommandResult(
        stdout="./a.py\n\n  \n./pkg/b.py\n",
        stderr="",
        exit_code=0,
        duration=0.0,
        workspace="sandbox",
    )

    updated, _ = SyncEngine(fake_sandbox, local_ws, baseline_path).reconcile_ledger({})

    assert set(updated) == {"a.py", "pkg/b.py"}


def test_a_file_that_cannot_be_read_back_is_skipped_not_fatal(
    make_fake_workspace, local_ws, baseline_path, caplog
):
    """A file that vanished between the scan and the read must not abort the ledger."""
    sandbox = make_fake_workspace("sandbox", {SYNC_MARKER: "0", "a.py": "A"})
    find_cmd = f"find . -type f -newer {SYNC_MARKER}"
    sandbox.command_results[find_cmd] = CommandResult(
        stdout="./a.py\n./vanished.py\n", stderr="", exit_code=0, duration=0.0, workspace="sandbox"
    )

    with caplog.at_level("DEBUG", logger=SYNC_LOGGER):
        updated, checkpoint = SyncEngine(sandbox, local_ws, baseline_path).reconcile_ledger({})

    assert updated == {"a.py": "A"}
    assert checkpoint.files_written == 1
    assert messages(caplog)[0] == (
        "Ledger reconciliation skipped 'vanished.py': File not found: vanished.py"
    )
