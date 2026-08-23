"""
The sync engine's four checkpoints.

The sandbox side is a FakeWorkspace so a test can stub `find` output and inject write
failures; the local side is a real LocalWorkspace on tmp_path wherever possible, so the
engine is exercised against a genuine filesystem rather than against two mocks agreeing
with each other.
"""

from __future__ import annotations

import re

import pytest

from app.sync import events
from app.sync.engine import SYNC_MARKER, SyncEngine
from app.sync.events import SyncCheckpoint, SyncConflict
from app.workspace.base import CommandResult


def engine_for(sandbox, local, baseline_path, **kwargs) -> SyncEngine:
    return SyncEngine(sandbox, local, baseline_path, **kwargs)


def conflicts_emitted() -> list[SyncConflict]:
    return [e for e in events.drain() if isinstance(e, SyncConflict)]


# ── Seed: run start ──────────────────────────────────────────────────────────

def test_seed_pushes_local_files_into_the_sandbox(fake_sandbox, local_ws, baseline_path):
    local_ws.write_file("src/main.py", "print(1)")
    engine = engine_for(fake_sandbox, local_ws, baseline_path)

    checkpoint = engine.seed()

    assert fake_sandbox.files["src/main.py"] == "print(1)"
    assert checkpoint.kind == "run_start"
    assert checkpoint.direction == "local_to_sandbox"
    assert checkpoint.files_written == 1
    assert baseline_path.exists()


def test_seed_is_a_noop_when_both_sides_already_agree(fake_sandbox, local_ws, baseline_path):
    local_ws.write_file("a.py", "same")
    fake_sandbox.files["a.py"] = "same"
    engine = engine_for(fake_sandbox, local_ws, baseline_path)

    checkpoint = engine.seed()

    assert checkpoint.files_written == 0
    assert checkpoint.conflicts == 0


# ── Flush: sub-requirement boundary and run end ──────────────────────────────

def test_a_deeply_nested_tree_syncs_in_full(fake_sandbox, local_ws, baseline_path):
    """
    The snapshot that drives every decision walks the whole tree. At a shallow depth it
    would see only top-level files, silently treat the rest as absent, and never sync
    a single generated package.
    """
    fake_sandbox.files["top.py"] = "T"
    fake_sandbox.files["src/domain/entities/order.py"] = "O"
    fake_sandbox.files["tests/unit/domain/test_order.py"] = "TO"

    checkpoint = engine_for(fake_sandbox, local_ws, baseline_path).flush()

    assert local_ws.read_file("src/domain/entities/order.py") == "O"
    assert local_ws.read_file("tests/unit/domain/test_order.py") == "TO"
    assert checkpoint.files_written == 3


def test_flush_pulls_sandbox_files_to_local(fake_sandbox, local_ws, baseline_path):
    fake_sandbox.files["src/main.py"] = "print(1)"
    engine = engine_for(fake_sandbox, local_ws, baseline_path)

    checkpoint = engine.flush()

    assert local_ws.read_file("src/main.py") == "print(1)"
    assert checkpoint.kind == "sub_req_boundary"
    assert checkpoint.direction == "sandbox_to_local"


def test_sandbox_edit_propagates_to_local(fake_sandbox, local_ws, baseline_path):
    fake_sandbox.files["a.py"] = "v1"
    engine = engine_for(fake_sandbox, local_ws, baseline_path)
    engine.seed()

    fake_sandbox.files["a.py"] = "v2"
    engine.flush()

    assert local_ws.read_file("a.py") == "v2"


def test_local_edit_propagates_to_the_sandbox(fake_sandbox, local_ws, baseline_path):
    fake_sandbox.files["a.py"] = "v1"
    engine = engine_for(fake_sandbox, local_ws, baseline_path)
    engine.seed()

    local_ws.write_file("a.py", "edited-locally")
    engine.flush()

    assert fake_sandbox.files["a.py"] == "edited-locally"


def test_full_flush_mirrors_everything_regardless_of_the_baseline(
    fake_sandbox, local_ws, baseline_path
):
    """
    Run end ignores the baseline on purpose, so the exported workspace cannot be
    missing a file because of a stale snapshot. A normal flush would read the absent
    local copy as a deletion and propagate it the other way.
    """
    fake_sandbox.files["a.py"] = "A"
    engine = engine_for(fake_sandbox, local_ws, baseline_path)
    engine.seed()

    (local_ws.root / "a.py").unlink()

    engine.flush(full=True)

    assert local_ws.read_file("a.py") == "A"
    assert fake_sandbox.files["a.py"] == "A"


# ── Conflicts ────────────────────────────────────────────────────────────────

def test_both_sides_changed_sandbox_wins_and_local_is_backed_up(
    fake_sandbox, local_ws, baseline_path
):
    fake_sandbox.files["a.py"] = "v1"
    engine = engine_for(fake_sandbox, local_ws, baseline_path)
    engine.seed()
    events.drain()

    fake_sandbox.files["a.py"] = "sandbox-wins"
    local_ws.write_file("a.py", "local-loses")

    checkpoint = engine.flush()

    assert local_ws.read_file("a.py") == "sandbox-wins"
    assert checkpoint.conflicts == 1

    conflicts = conflicts_emitted()
    assert len(conflicts) == 1
    assert conflicts[0].path == "a.py"
    assert conflicts[0].checkpoint == "sub_req_boundary"

    # The losing edit survives rather than being silently discarded.
    assert local_ws.read_file(conflicts[0].backup_path) == "local-loses"
    # The timestamp is what keeps successive conflicts on one path from overwriting
    # each other's backups, so its shape is part of the contract.
    assert re.fullmatch(r"a\.py\.local\.\d{8}-\d{6}", conflicts[0].backup_path)


def test_no_conflict_is_emitted_when_only_one_side_changed(
    fake_sandbox, local_ws, baseline_path
):
    fake_sandbox.files["a.py"] = "v1"
    engine = engine_for(fake_sandbox, local_ws, baseline_path)
    engine.seed()
    events.drain()

    fake_sandbox.files["a.py"] = "v2"
    engine.flush()

    assert conflicts_emitted() == []


# ── Deletions ────────────────────────────────────────────────────────────────

def test_deletion_propagates_when_the_other_side_is_untouched(
    fake_sandbox, local_ws, baseline_path
):
    fake_sandbox.files["gone.py"] = "x"
    engine = engine_for(fake_sandbox, local_ws, baseline_path)
    engine.seed()
    assert local_ws.exists("gone.py")

    del fake_sandbox.files["gone.py"]
    checkpoint = engine.flush()

    assert not local_ws.exists("gone.py")
    assert checkpoint.files_deleted == 1


def test_deletion_is_skipped_when_the_other_side_was_edited(
    fake_sandbox, local_ws, baseline_path
):
    """Honoring the delete here would silently discard the local edit."""
    fake_sandbox.files["f.py"] = "v1"
    engine = engine_for(fake_sandbox, local_ws, baseline_path)
    engine.seed()

    del fake_sandbox.files["f.py"]
    local_ws.write_file("f.py", "edited")

    checkpoint = engine.flush()

    assert local_ws.read_file("f.py") == "edited"
    assert checkpoint.files_deleted == 0


# ── Idempotency ──────────────────────────────────────────────────────────────

def test_abort_mid_flush_leaves_the_baseline_unadvanced(make_fake_workspace, baseline_path):
    """
    The engine is idempotent, not transactional: a partial write cannot be rolled back
    out of a remote sandbox, so the guarantee is that the baseline only moves after a
    pass completes and the next checkpoint retries the very same delta.
    """
    sandbox = make_fake_workspace("sandbox", {"a.py": "A", "b.py": "B"})
    local = make_fake_workspace("local")
    local.fail_write_on = {"b.py"}

    engine = engine_for(sandbox, local, baseline_path)

    with pytest.raises(RuntimeError):
        engine.flush()

    assert not baseline_path.exists(), "a failed pass must not advance the baseline"

    local.fail_write_on = set()
    engine.flush()

    assert local.files["a.py"] == "A"
    assert local.files["b.py"] == "B"
    assert baseline_path.exists()


def test_repeating_a_checkpoint_with_no_changes_writes_nothing(
    fake_sandbox, local_ws, baseline_path
):
    fake_sandbox.files["a.py"] = "A"
    engine = engine_for(fake_sandbox, local_ws, baseline_path)
    engine.flush()

    second = engine.flush()

    assert second.files_written == 0
    assert second.files_deleted == 0
    assert second.conflicts == 0


def test_an_empty_workspace_pair_produces_an_all_zero_checkpoint(
    fake_sandbox, local_ws, baseline_path
):
    """Counters start at zero; a no-op sync must report exactly that."""
    checkpoint = engine_for(fake_sandbox, local_ws, baseline_path).flush()

    assert (checkpoint.files_written, checkpoint.files_deleted, checkpoint.conflicts) == (0, 0, 0)
    assert 0.0 <= checkpoint.duration < 60.0


def test_flush_defaults_to_the_sub_requirement_boundary(fake_sandbox, local_ws, baseline_path):
    """The no-argument call is what a node will make; its defaults are the contract."""
    checkpoint = engine_for(fake_sandbox, local_ws, baseline_path).flush()

    assert checkpoint.kind == "sub_req_boundary"
    assert checkpoint.direction == "sandbox_to_local"


def test_flush_defaults_to_honouring_the_baseline(fake_sandbox, local_ws, baseline_path):
    """
    Only the run-end flush ignores the baseline. If the ordinary flush did too, a local
    deletion would be read as a brand-new sandbox file and silently resurrected.
    """
    fake_sandbox.files["gone.py"] = "x"
    engine = engine_for(fake_sandbox, local_ws, baseline_path)
    engine.seed()

    (local_ws.root / "gone.py").unlink()
    checkpoint = engine.flush()

    assert "gone.py" not in fake_sandbox.files
    assert checkpoint.files_deleted == 1
    assert checkpoint.files_written == 0


def test_the_run_end_flush_is_labelled_run_end(fake_sandbox, local_ws, baseline_path):
    fake_sandbox.files["a.py"] = "A"
    checkpoint = engine_for(fake_sandbox, local_ws, baseline_path).flush(full=True)

    assert checkpoint.kind == "run_end"


def test_counters_report_the_actual_totals(fake_sandbox, local_ws, baseline_path):
    fake_sandbox.files["a.py"] = "A"
    fake_sandbox.files["b.py"] = "B"
    fake_sandbox.files["c.py"] = "C"

    checkpoint = engine_for(fake_sandbox, local_ws, baseline_path).flush()

    assert checkpoint.files_written == 3


def test_a_checkpoint_is_emitted_to_the_event_sink(fake_sandbox, local_ws, baseline_path):
    fake_sandbox.files["a.py"] = "A"
    engine_for(fake_sandbox, local_ws, baseline_path).flush()

    emitted = [e for e in events.drain() if isinstance(e, SyncCheckpoint)]
    assert len(emitted) == 1
    assert emitted[0].files_written == 1


def test_the_baseline_records_what_was_synced(fake_sandbox, local_ws, baseline_path):
    from app.sync.baseline import Baseline, content_hash

    fake_sandbox.files["a.py"] = "A"
    engine_for(fake_sandbox, local_ws, baseline_path).flush()

    assert Baseline.load(baseline_path).entries["a.py"] == content_hash("A")


def test_a_deleted_path_leaves_the_baseline(fake_sandbox, local_ws, baseline_path):
    from app.sync.baseline import Baseline

    fake_sandbox.files["gone.py"] = "x"
    engine = engine_for(fake_sandbox, local_ws, baseline_path)
    engine.seed()
    assert "gone.py" in Baseline.load(baseline_path).entries

    del fake_sandbox.files["gone.py"]
    engine.flush()

    assert "gone.py" not in Baseline.load(baseline_path).entries


# ── Ignore rules ─────────────────────────────────────────────────────────────

def test_a_gitignore_in_the_workspace_excludes_files_from_sync(
    fake_sandbox, local_ws, baseline_path
):
    fake_sandbox.files[".gitignore"] = "build/\n"
    fake_sandbox.files["build/out.js"] = "compiled"
    fake_sandbox.files["src/main.py"] = "source"

    engine_for(fake_sandbox, local_ws, baseline_path).flush()

    assert local_ws.exists("src/main.py")
    assert not local_ws.exists("build/out.js")


def test_fallback_exclusions_apply_when_there_is_no_gitignore(
    fake_sandbox, local_ws, baseline_path
):
    fake_sandbox.files["__pycache__/x.pyc"] = "bytecode"
    fake_sandbox.files["src/main.py"] = "source"

    engine_for(
        fake_sandbox, local_ws, baseline_path, exclude_fallback=["__pycache__/"]
    ).flush()

    assert local_ws.exists("src/main.py")
    assert not local_ws.exists("__pycache__/x.pyc")


def test_the_sync_marker_never_syncs(fake_sandbox, local_ws, baseline_path):
    fake_sandbox.files["src/main.py"] = "source"
    engine_for(fake_sandbox, local_ws, baseline_path).flush()

    assert not local_ws.exists(SYNC_MARKER)


# ── Ledger reconciliation ────────────────────────────────────────────────────

def test_reconcile_ledger_captures_files_created_by_a_shell_command(
    fake_sandbox, local_ws, baseline_path
):
    """
    The lost-files fix. A file an agent creates with a shell command never entered the
    ledger, so it never reached workspace_output_* and vanished with the sandbox.
    """
    fake_sandbox.files["src/main.py"] = "written by a tool"
    fake_sandbox.files["generated/schema.sql"] = "created by bash"
    engine = engine_for(fake_sandbox, local_ws, baseline_path)

    ledger = {"src/main.py": "written by a tool"}
    updated, checkpoint = engine.reconcile_ledger(ledger)

    assert updated["generated/schema.sql"] == "created by bash"
    assert checkpoint.files_written == 1
    assert checkpoint.kind == "tool_call"
    assert checkpoint.direction == "sandbox_to_ledger"


def test_reconcile_ledger_reports_zero_when_the_ledger_is_already_current(
    fake_sandbox, local_ws, baseline_path
):
    fake_sandbox.files["a.py"] = "A"
    engine = engine_for(fake_sandbox, local_ws, baseline_path)

    _, checkpoint = engine.reconcile_ledger({"a.py": "A"})

    assert checkpoint.files_written == 0
    assert checkpoint.files_deleted == 0
    assert checkpoint.conflicts == 0


def test_reconcile_ledger_counts_every_new_file(fake_sandbox, local_ws, baseline_path):
    fake_sandbox.files["a.py"] = "A"
    fake_sandbox.files["b.py"] = "B"

    _, checkpoint = engine_for(fake_sandbox, local_ws, baseline_path).reconcile_ledger({})

    assert checkpoint.files_written == 2


def test_reconcile_ledger_updates_a_changed_file(fake_sandbox, local_ws, baseline_path):
    fake_sandbox.files["a.py"] = "new content"

    updated, checkpoint = engine_for(fake_sandbox, local_ws, baseline_path).reconcile_ledger(
        {"a.py": "stale content"}
    )

    assert updated["a.py"] == "new content"
    assert checkpoint.files_written == 1


def test_reconcile_ledger_emits_its_checkpoint(fake_sandbox, local_ws, baseline_path):
    fake_sandbox.files["a.py"] = "A"
    engine_for(fake_sandbox, local_ws, baseline_path).reconcile_ledger({})

    emitted = [e for e in events.drain() if isinstance(e, SyncCheckpoint)]
    assert [e.kind for e in emitted] == ["tool_call"]


def test_reconcile_ledger_does_not_mutate_the_ledger_it_was_given(
    fake_sandbox, local_ws, baseline_path
):
    fake_sandbox.files["new.py"] = "x"
    original: dict[str, str] = {}

    updated, _ = engine_for(fake_sandbox, local_ws, baseline_path).reconcile_ledger(original)

    assert original == {}
    assert "new.py" in updated


def test_reconcile_ledger_scans_everything_before_a_marker_exists(
    fake_sandbox, local_ws, baseline_path
):
    fake_sandbox.files["a.py"] = "A"
    fake_sandbox.files["b.py"] = "B"

    updated, _ = engine_for(fake_sandbox, local_ws, baseline_path).reconcile_ledger({})

    assert set(updated) == {"a.py", "b.py"}


def test_the_full_scan_reaches_deeply_nested_files(
    fake_sandbox, local_ws, baseline_path
):
    """
    The pre-marker scan must walk the whole tree. At the default depth of 1 it would
    see only the top level, and every generated package would be missing from the
    ledger — the lost-files bug in a new disguise.
    """
    fake_sandbox.files["top.py"] = "T"
    fake_sandbox.files["src/domain/entities/deep.py"] = "D"

    updated, _ = engine_for(fake_sandbox, local_ws, baseline_path).reconcile_ledger({})

    assert set(updated) == {"top.py", "src/domain/entities/deep.py"}


def test_a_reconcile_ledger_checkpoint_records_a_plausible_duration(
    fake_sandbox, local_ws, baseline_path
):
    fake_sandbox.files["a.py"] = "A"
    _, checkpoint = engine_for(fake_sandbox, local_ws, baseline_path).reconcile_ledger({})
    assert 0.0 <= checkpoint.duration < 60.0


def test_reconcile_ledger_uses_find_newer_once_a_marker_exists(
    fake_sandbox, local_ws, baseline_path
):
    fake_sandbox.files[SYNC_MARKER] = "0"
    fake_sandbox.files["a.py"] = "A"
    fake_sandbox.files["b.py"] = "B"

    find_cmd = f"find . -type f -newer {SYNC_MARKER}"
    fake_sandbox.command_results[find_cmd] = CommandResult(
        stdout="./b.py\n", stderr="", exit_code=0, duration=0.0, workspace="sandbox"
    )

    updated, _ = engine_for(fake_sandbox, local_ws, baseline_path).reconcile_ledger({})

    assert find_cmd in fake_sandbox.command_log
    assert set(updated) == {"b.py"}


def test_reconcile_ledger_refreshes_the_marker(fake_sandbox, local_ws, baseline_path):
    fake_sandbox.files[SYNC_MARKER] = "0"
    find_cmd = f"find . -type f -newer {SYNC_MARKER}"
    fake_sandbox.command_results[find_cmd] = CommandResult(
        stdout="", stderr="", exit_code=0, duration=0.0, workspace="sandbox"
    )

    engine_for(fake_sandbox, local_ws, baseline_path).reconcile_ledger({})

    assert fake_sandbox.files[SYNC_MARKER] != "0"


def test_reconcile_ledger_falls_back_to_a_full_scan_when_find_fails(
    fake_sandbox, local_ws, baseline_path
):
    fake_sandbox.files[SYNC_MARKER] = "0"
    fake_sandbox.files["a.py"] = "A"

    find_cmd = f"find . -type f -newer {SYNC_MARKER}"
    fake_sandbox.command_results[find_cmd] = CommandResult(
        stdout="", stderr="find: broken", exit_code=1, duration=0.0, workspace="sandbox"
    )

    updated, _ = engine_for(fake_sandbox, local_ws, baseline_path).reconcile_ledger({})

    assert "a.py" in updated


def test_reconcile_ledger_skips_ignored_files(fake_sandbox, local_ws, baseline_path):
    fake_sandbox.files["src/main.py"] = "source"
    fake_sandbox.files["__pycache__/x.pyc"] = "junk"

    updated, _ = engine_for(
        fake_sandbox, local_ws, baseline_path, exclude_fallback=["__pycache__/"]
    ).reconcile_ledger({})

    assert "src/main.py" in updated
    assert "__pycache__/x.pyc" not in updated


# ── Checkpoint dispatch ──────────────────────────────────────────────────────

@pytest.mark.parametrize("kind", ["run_start", "sub_req_boundary", "run_end"])
def test_sync_at_checkpoint_dispatches_each_kind(fake_sandbox, local_ws, baseline_path, kind):
    fake_sandbox.files["a.py"] = "A"
    engine = engine_for(fake_sandbox, local_ws, baseline_path)

    checkpoint = engine.sync_at_checkpoint(kind)

    # The tool_call kind returns a (ledger, checkpoint) pair; the other three return a
    # bare checkpoint. Narrowing here is what a real caller has to do too.
    assert isinstance(checkpoint, SyncCheckpoint)
    assert checkpoint.kind == kind


def test_sync_at_checkpoint_handles_the_tool_call_kind(fake_sandbox, local_ws, baseline_path):
    fake_sandbox.files["a.py"] = "A"
    engine = engine_for(fake_sandbox, local_ws, baseline_path)

    result = engine.sync_at_checkpoint("tool_call", ledger={})

    assert isinstance(result, tuple)
    updated, checkpoint = result
    assert updated["a.py"] == "A"
    assert checkpoint.kind == "tool_call"


def test_sync_at_checkpoint_passes_the_ledger_through(fake_sandbox, local_ws, baseline_path):
    """A supplied ledger must reach reconcile_ledger, not be replaced with an empty one."""
    fake_sandbox.files["new.py"] = "N"
    engine = engine_for(fake_sandbox, local_ws, baseline_path)

    result = engine.sync_at_checkpoint("tool_call", ledger={"existing.py": "E"})

    assert isinstance(result, tuple)
    updated, _ = result
    assert updated == {"existing.py": "E", "new.py": "N"}


def test_a_mixed_pass_counts_writes_and_deletes_separately(
    fake_sandbox, local_ws, baseline_path
):
    fake_sandbox.files["keep.py"] = "K"
    fake_sandbox.files["gone.py"] = "G"
    engine = engine_for(fake_sandbox, local_ws, baseline_path)
    engine.seed()

    del fake_sandbox.files["gone.py"]
    fake_sandbox.files["added.py"] = "A"
    fake_sandbox.files["keep.py"] = "K2"

    checkpoint = engine.flush()

    assert checkpoint.files_written == 2   # added.py plus the changed keep.py
    assert checkpoint.files_deleted == 1
    assert checkpoint.conflicts == 0


def test_the_baseline_tracks_a_conflict_resolution(fake_sandbox, local_ws, baseline_path):
    """After the sandbox wins, the baseline must record the sandbox content."""
    from app.sync.baseline import Baseline, content_hash

    fake_sandbox.files["a.py"] = "v1"
    engine = engine_for(fake_sandbox, local_ws, baseline_path)
    engine.seed()

    fake_sandbox.files["a.py"] = "sandbox-wins"
    local_ws.write_file("a.py", "local-loses")
    engine.flush()

    assert Baseline.load(baseline_path).entries["a.py"] == content_hash("sandbox-wins")


def test_sync_at_checkpoint_rejects_an_unknown_kind(fake_sandbox, local_ws, baseline_path):
    engine = engine_for(fake_sandbox, local_ws, baseline_path)

    with pytest.raises(ValueError, match="Unknown sync checkpoint"):
        engine.sync_at_checkpoint("whenever")  # type: ignore[arg-type]


# ── Sandbox lifetime ─────────────────────────────────────────────────────────

class _AdapterSpy:
    def __init__(self) -> None:
        self.calls: list[bool] = []

    def refresh_timeout(self, force: bool = False) -> bool:
        self.calls.append(force)
        return True


def test_a_checkpoint_extends_the_sandbox_lifetime(fake_sandbox, local_ws, baseline_path):
    """
    A run longer than SANDBOX_TIMEOUT survives because each checkpoint slides the
    window forward, so the sandbox cannot expire between them.
    """
    spy = _AdapterSpy()
    fake_sandbox.adapter = spy

    engine_for(fake_sandbox, local_ws, baseline_path).sync_at_checkpoint("sub_req_boundary")

    assert spy.calls == [True]


def test_a_checkpoint_works_on_a_workspace_with_no_adapter(
    fake_sandbox, local_ws, baseline_path
):
    engine = engine_for(fake_sandbox, local_ws, baseline_path)

    checkpoint = engine.sync_at_checkpoint("sub_req_boundary")
    assert isinstance(checkpoint, SyncCheckpoint)
    assert checkpoint.kind == "sub_req_boundary"
