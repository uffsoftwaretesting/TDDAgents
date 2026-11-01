"""
File checkpointing (`app/utils/file_history.py`, upstream `utils/fileHistory.ts` and
`print.ts` -> `handleRewindFiles`): the setting, edit tracking, snapshots, rewind, diff stats,
the transcript form and the resume copy.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import app.utils.file_history as fh
from app.bootstrap.state import SessionBootstrap
from app.state.app_state_store import AppState, AppStateStore
from app.utils.file_history import (
    FileHistoryBackup,
    FileHistorySnapshot,
    FileHistoryState,
    RewindFilesResult,
)
from app.utils.tool_result_storage import config_home

T0 = datetime(2026, 1, 2, 3, 4, 5, 678000, tzinfo=UTC)


class Harness:
    """A session over `root`: its store, its updater and a recorder of snapshot writes."""

    def __init__(self, root: Path, *, interactive: bool = True, session_id: str = "sess-1") -> None:
        self.root = root
        self.bootstrap = SessionBootstrap(session_id=session_id, original_cwd=str(root), is_interactive=interactive)
        self.store = AppStateStore(AppState(bootstrap=self.bootstrap))
        self.update = fh.update_file_history_state_for(self.store.update)
        self.records: list[tuple[str, FileHistorySnapshot, bool]] = []

    def record(self, message_id: str, snapshot: FileHistorySnapshot, update: bool) -> None:
        self.records.append((message_id, snapshot, update))

    @property
    def state(self) -> FileHistoryState:
        return self.store.get().file_history

    def snapshot(self, message_id: str) -> None:
        asyncio.run(fh.file_history_make_snapshot(self.update, message_id, self.bootstrap, self.record))

    def track(self, path: Path, message_id: str = "assistant-1") -> None:
        asyncio.run(fh.file_history_track_edit(self.update, str(path), message_id, self.bootstrap, self.record))

    def backup_dir(self) -> Path:
        return config_home() / "file-history" / self.bootstrap.session_id


@pytest.fixture
def h(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    monkeypatch.delenv(fh.DISABLE_FILE_CHECKPOINTING_ENV, raising=False)
    monkeypatch.delenv(fh.ENABLE_SDK_FILE_CHECKPOINTING_ENV, raising=False)
    root = tmp_path / "project"
    root.mkdir()
    return Harness(root)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ── the setting ──────────────────────────────────────────────────────────────


def test_constants_follow_upstream() -> None:
    assert fh.MAX_SNAPSHOTS == 100
    assert fh.ENABLE_SDK_FILE_CHECKPOINTING_ENV == "TDDAGENTS_ENABLE_SDK_FILE_CHECKPOINTING"
    assert fh.DISABLE_FILE_CHECKPOINTING_ENV == "TDDAGENTS_DISABLE_FILE_CHECKPOINTING"
    assert fh.FILE_CHECKPOINTING_ENABLED_KEY == "fileCheckpointingEnabled"
    assert fh.REWIND_NOT_ENABLED_ERROR == "File rewinding is not enabled."
    assert fh.NO_CHECKPOINT_ERROR == "No file checkpoint found for this message."
    assert fh.SNAPSHOT_NOT_FOUND_ERROR == "The selected snapshot was not found"


def test_interactive_is_enabled_unless_the_global_config_or_env_disables_it(h: Harness, monkeypatch: Any) -> None:
    assert fh.file_history_enabled(h.bootstrap) is True
    global_config = config_home() / ".tddagents.json"
    global_config.parent.mkdir(parents=True, exist_ok=True)
    global_config.write_text(json.dumps({"fileCheckpointingEnabled": True}), encoding="utf-8")
    assert fh.file_history_enabled(h.bootstrap) is True
    global_config.write_text(json.dumps({"fileCheckpointingEnabled": False}), encoding="utf-8")
    assert fh.file_history_enabled(h.bootstrap) is False
    global_config.write_text("not json", encoding="utf-8")
    assert fh.file_history_enabled(h.bootstrap) is True
    global_config.write_text("[]", encoding="utf-8")
    assert fh.file_history_enabled(h.bootstrap) is True
    monkeypatch.setenv(fh.DISABLE_FILE_CHECKPOINTING_ENV, "1")
    assert fh.file_history_enabled(h.bootstrap) is False


def test_a_headless_run_needs_the_sdk_env_and_the_disable_env_wins(h: Harness, monkeypatch: Any) -> None:
    headless = replace(h.bootstrap, is_interactive=False)
    assert fh.file_history_enabled(headless) is False
    monkeypatch.setenv(fh.ENABLE_SDK_FILE_CHECKPOINTING_ENV, "true")
    assert fh.file_history_enabled(headless) is True
    monkeypatch.setenv(fh.DISABLE_FILE_CHECKPOINTING_ENV, "yes")
    assert fh.file_history_enabled(headless) is False


def test_disabled_makes_every_entry_point_a_no_op(h: Harness, monkeypatch: Any) -> None:
    monkeypatch.setenv(fh.DISABLE_FILE_CHECKPOINTING_ENV, "1")
    h.snapshot("u1")
    assert h.state == FileHistoryState()
    state = FileHistoryState(snapshots=(FileHistorySnapshot("u1", {}, T0),))
    assert fh.file_history_can_restore(state, "u1", h.bootstrap) is False
    assert asyncio.run(fh.file_history_get_diff_stats(state, "u1", h.bootstrap)) is None
    assert asyncio.run(fh.file_history_has_any_changes(state, "u1", h.bootstrap)) is False
    asyncio.run(fh.file_history_rewind(h.update, "missing", h.bootstrap))  # does not raise
    seen: list[Any] = []
    fh.file_history_restore_state_from_log(state.snapshots, seen.append, h.bootstrap)
    assert seen == []


# ── snapshots and tracking ───────────────────────────────────────────────────


def test_the_first_snapshot_is_empty_and_recorded(h: Harness) -> None:
    h.snapshot("u1")
    (snapshot,) = h.state.snapshots
    assert (snapshot.message_id, dict(snapshot.tracked_file_backups)) == ("u1", {})
    assert h.state.snapshot_sequence == 1
    assert h.records == [("u1", snapshot, False)]


def test_tracking_an_edit_backs_up_the_pre_edit_content_once(h: Harness) -> None:
    target = write(h.root / "src" / "a.py", "before\n")
    os.chmod(target, 0o640)
    h.snapshot("u1")
    h.track(target, "assistant-1")
    backup = h.state.snapshots[-1].tracked_file_backups["src/a.py"]
    assert (backup.backup_file_name, backup.version) == (fh.get_backup_file_name(str(target), 1), 1)
    backup_path = h.backup_dir() / str(backup.backup_file_name)
    assert backup_path.read_text(encoding="utf-8") == "before\n"
    assert backup_path.stat().st_mode & 0o777 == 0o640
    assert h.state.tracked_files == frozenset({"src/a.py"})
    assert h.records[-1] == ("assistant-1", h.state.snapshots[-1], True)

    # The file is edited; a second track before the next snapshot must not overwrite v1.
    write(target, "after\n")
    count = len(h.records)
    h.track(target, "assistant-2")
    assert backup_path.read_text(encoding="utf-8") == "before\n"
    assert len(h.records) == count


def test_tracking_a_new_file_records_that_it_did_not_exist(h: Harness) -> None:
    h.snapshot("u1")
    h.track(h.root / "new.py")
    backup = h.state.snapshots[-1].tracked_file_backups["new.py"]
    assert (backup.backup_file_name, backup.version) == (None, 1)


def test_tracking_needs_a_snapshot_and_a_real_updater(h: Harness) -> None:
    target = write(h.root / "a.py", "x")
    h.track(target)
    assert h.state == FileHistoryState() and h.records == []
    asyncio.run(fh.file_history_track_edit(fh.noop_update_file_history_state, str(target), "m", h.bootstrap))
    asyncio.run(fh.file_history_make_snapshot(fh.noop_update_file_history_state, "u1", h.bootstrap))
    assert h.state == FileHistoryState()


def test_a_backup_failure_tracks_nothing(h: Harness, monkeypatch: Any) -> None:
    h.snapshot("u1")

    def broken(*args: Any) -> Any:
        raise PermissionError("denied")

    monkeypatch.setattr(fh, "_create_backup", broken)
    h.track(write(h.root / "a.py", "x"))
    assert h.state.tracked_files == frozenset()


def test_a_snapshot_reuses_an_unchanged_backup_and_versions_a_changed_one(h: Harness) -> None:
    a = write(h.root / "a.py", "a1\n")
    b = write(h.root / "b.py", "b1\n")
    h.snapshot("u1")
    h.track(a)
    h.track(b)
    write(a, "a2 longer\n")
    h.snapshot("u2")
    latest = h.state.snapshots[-1].tracked_file_backups
    assert latest["a.py"].version == 2
    assert (h.backup_dir() / str(latest["a.py"].backup_file_name)).read_text(encoding="utf-8") == "a2 longer\n"
    assert latest["b.py"] == h.state.snapshots[0].tracked_file_backups["b.py"]
    assert h.state.snapshot_sequence == 2
    assert h.records[-1] == ("u2", h.state.snapshots[-1], False)


def test_a_deleted_tracked_file_gets_a_missing_backup_with_the_next_version(h: Harness) -> None:
    a = write(h.root / "a.py", "a\n")
    h.snapshot("u1")
    h.track(a)
    a.unlink()
    h.snapshot("u2")
    backup = h.state.snapshots[-1].tracked_file_backups["a.py"]
    assert (backup.backup_file_name, backup.version) == (None, 2)


def test_a_file_that_fails_to_back_up_inherits_its_last_backup(h: Harness, monkeypatch: Any) -> None:
    a = write(h.root / "a.py", "a\n")
    h.snapshot("u1")
    h.track(a)
    first = h.state.snapshots[-1].tracked_file_backups["a.py"]

    def broken(*args: Any) -> Any:
        raise OSError("io")

    monkeypatch.setattr(fh, "_next_backup", broken)
    h.snapshot("u2")
    assert h.state.snapshots[-1].tracked_file_backups["a.py"] == first


def test_snapshots_are_capped_and_the_sequence_keeps_counting(h: Harness, monkeypatch: Any) -> None:
    monkeypatch.setattr(fh, "MAX_SNAPSHOTS", 3)
    for index in range(5):
        h.snapshot(f"u{index}")
    assert [s.message_id for s in h.state.snapshots] == ["u2", "u3", "u4"]
    assert h.state.snapshot_sequence == 5


def test_a_failed_record_is_logged_never_raised(h: Harness, caplog: Any) -> None:
    def broken(message_id: str, snapshot: FileHistorySnapshot, update: bool) -> None:
        raise OSError("disk full")

    asyncio.run(fh.file_history_make_snapshot(h.update, "u1", h.bootstrap, broken))
    assert len(h.state.snapshots) == 1
    assert "FileHistory: Failed to record snapshot: disk full" in caplog.text


def test_the_updater_keeps_the_app_state_when_nothing_changed(h: Harness) -> None:
    calls: list[int] = []
    h.store.subscribe(lambda: calls.append(1))
    before = h.store.get()
    h.update(lambda state: state)
    assert h.store.get() is before and calls == []
    h.update(lambda state: replace(state, snapshot_sequence=9))
    assert h.state.snapshot_sequence == 9 and calls == [1]


# ── rewind ───────────────────────────────────────────────────────────────────


def _session_with_edits(h: Harness) -> tuple[Path, Path]:
    """u1: `a.py` = "one", no `b.py`; then the agent edits `a.py` and creates `b.py`; u2."""
    a = write(h.root / "a.py", "one\n")
    h.snapshot("u1")
    h.track(a)
    write(a, "one\ntwo\nthree\n")
    b = h.root / "pkg" / "b.py"
    h.track(b)
    write(b, "new\n")
    h.snapshot("u2")
    return a, b


def test_rewind_restores_edited_files_and_deletes_created_ones(h: Harness) -> None:
    a, b = _session_with_edits(h)
    os.chmod(a, 0o600)
    asyncio.run(fh.file_history_rewind(h.update, "u1", h.bootstrap))
    assert a.read_text(encoding="utf-8") == "one\n"
    assert not b.exists()
    # The rewind does not change the state.
    assert [s.message_id for s in h.state.snapshots] == ["u1", "u2"]
    # Forward again, to the later snapshot.
    asyncio.run(fh.file_history_rewind(h.update, "u2", h.bootstrap))
    assert a.read_text(encoding="utf-8") == "one\ntwo\nthree\n"
    assert b.read_text(encoding="utf-8") == "new\n"


def test_rewind_recreates_a_deleted_file_and_its_directory(h: Harness) -> None:
    a = write(h.root / "d" / "a.py", "keep\n")
    h.snapshot("u1")
    h.track(a)
    a.unlink()
    a.parent.rmdir()
    asyncio.run(fh.file_history_rewind(h.update, "u1", h.bootstrap))
    assert a.read_text(encoding="utf-8") == "keep\n"


def test_rewind_to_an_unknown_message_raises(h: Harness, caplog: Any) -> None:
    h.snapshot("u1")
    with pytest.raises(fh.FileHistoryError, match="^The selected snapshot was not found$"):
        asyncio.run(fh.file_history_rewind(h.update, "nope", h.bootstrap))
    assert "FileHistory: Snapshot for nope not found" in caplog.text


def test_rewind_before_a_file_was_tracked_uses_its_first_version(h: Harness) -> None:
    h.snapshot("u0")
    a = write(h.root / "a.py", "v1\n")
    h.snapshot("u1")
    h.track(a)
    write(a, "v2\n")
    asyncio.run(fh.file_history_rewind(h.update, "u0", h.bootstrap))
    assert a.read_text(encoding="utf-8") == "v1\n"


def test_a_file_with_no_first_version_is_left_alone(h: Harness, caplog: Any) -> None:
    a = write(h.root / "a.py", "x\n")
    state = FileHistoryState(
        snapshots=(FileHistorySnapshot("u0", {}, T0),
                   FileHistorySnapshot("u1", {"a.py": FileHistoryBackup("h@v2", 2, T0)}, T0)),
        tracked_files=frozenset({"a.py"}),
    )
    h.store.update(lambda s: replace(s, file_history=state))
    asyncio.run(fh.file_history_rewind(h.update, "u0", h.bootstrap))
    assert a.read_text(encoding="utf-8") == "x\n"
    assert "Error finding the backup file to apply" in caplog.text
    assert asyncio.run(fh.file_history_has_any_changes(state, "u0", h.bootstrap)) is False
    stats = asyncio.run(fh.file_history_get_diff_stats(state, "u0", h.bootstrap))
    assert stats == fh.DiffStats(insertions=0, deletions=0, files_changed=[])


def test_a_missing_backup_file_is_not_restored(h: Harness, caplog: Any) -> None:
    a, _ = _session_with_edits(h)
    for backup in h.backup_dir().iterdir():
        backup.unlink()
    asyncio.run(fh.file_history_rewind(h.update, "u1", h.bootstrap))
    assert a.read_text(encoding="utf-8") == "one\ntwo\nthree\n"
    assert "Backup file not found" in caplog.text


def test_can_restore_only_a_snapshotted_message(h: Harness) -> None:
    h.snapshot("u1")
    assert fh.file_history_can_restore(h.state, "u1", h.bootstrap) is True
    assert fh.file_history_can_restore(h.state, "u2", h.bootstrap) is False


# ── diff stats and change detection ──────────────────────────────────────────


def test_diff_stats_count_the_lines_a_rewind_would_change(h: Harness) -> None:
    a, b = _session_with_edits(h)
    stats = asyncio.run(fh.file_history_get_diff_stats(h.state, "u1", h.bootstrap))
    assert stats is not None
    assert sorted(stats.files_changed or []) == sorted([str(a), str(b)])
    # a.py loses "two" and "three"; b.py loses "new".
    assert (stats.insertions, stats.deletions) == (0, 3)
    assert asyncio.run(fh.file_history_get_diff_stats(h.state, "u2", h.bootstrap)) == fh.DiffStats(
        insertions=0, deletions=0, files_changed=[])
    assert asyncio.run(fh.file_history_get_diff_stats(h.state, "nope", h.bootstrap)) is None


def test_diff_stats_count_insertions_and_a_new_empty_file(h: Harness) -> None:
    a = write(h.root / "a.py", "x\ny\nz\n")
    h.snapshot("u1")
    h.track(a)
    write(a, "x\nQ\n")
    empty = h.root / "empty.py"
    h.track(empty)
    write(empty, "")
    h.snapshot("u2")
    stats = asyncio.run(fh.file_history_get_diff_stats(h.state, "u1", h.bootstrap))
    assert stats is not None
    assert sorted(stats.files_changed or []) == sorted([str(a), str(empty)])
    assert (stats.insertions, stats.deletions) == (2, 1)


def test_has_any_changes(h: Harness) -> None:
    a, b = _session_with_edits(h)
    assert asyncio.run(fh.file_history_has_any_changes(h.state, "u1", h.bootstrap)) is True
    assert asyncio.run(fh.file_history_has_any_changes(h.state, "u2", h.bootstrap)) is False
    assert asyncio.run(fh.file_history_has_any_changes(h.state, "nope", h.bootstrap)) is False
    b.unlink()
    write(a, "one\n")
    os.utime(a, ns=(10**18 * 2, 10**18 * 2))
    assert asyncio.run(fh.file_history_has_any_changes(h.state, "u1", h.bootstrap)) is False
    write(b, "back\n")
    assert asyncio.run(fh.file_history_has_any_changes(h.state, "u1", h.bootstrap)) is True


def test_check_origin_file_changed(h: Harness) -> None:
    backup_name = "abc@v1"
    backup = write(h.backup_dir() / backup_name, "same\n")
    original = write(h.root / "o.py", "same\n")
    os.chmod(original, backup.stat().st_mode & 0o777)
    os.utime(original, ns=(backup.stat().st_mtime_ns + 1,) * 2)
    assert fh.check_origin_file_changed(str(original), backup_name, h.bootstrap) is False
    write(original, "diff\n")  # same size, newer: the content decides
    os.utime(original, ns=(backup.stat().st_mtime_ns + 1,) * 2)
    assert fh.check_origin_file_changed(str(original), backup_name, h.bootstrap) is True
    # Older than the backup: no content comparison.
    os.utime(original, ns=(backup.stat().st_mtime_ns - 10**9,) * 2)
    assert fh.check_origin_file_changed(str(original), backup_name, h.bootstrap) is False
    write(original, "longer content\n")
    assert fh.check_origin_file_changed(str(original), backup_name, h.bootstrap) is True
    os.chmod(original, 0o700)
    write(original, "same\n")
    assert fh.check_origin_file_changed(str(original), backup_name, h.bootstrap) is True
    original.unlink()
    assert fh.check_origin_file_changed(str(original), backup_name, h.bootstrap) is True
    assert fh.check_origin_file_changed(str(original), "missing@v1", h.bootstrap) is False
    assert fh.check_origin_file_changed(str(backup), "missing@v1", h.bootstrap) is True


def test_backup_names_hash_the_path() -> None:
    import hashlib

    name = fh.get_backup_file_name("/p/a.py", 3)
    assert name == hashlib.sha256(b"/p/a.py").hexdigest()[:16] + "@v3"
    assert fh.get_file_history_dir("s") == config_home() / "file-history" / "s"


def test_tracking_paths_are_shortened_under_the_original_directory(h: Harness) -> None:
    outside = write(h.root.parent / "elsewhere.txt", "x")
    h.snapshot("u1")
    h.track(outside)
    h.track(Path("rel.py"))
    assert h.state.tracked_files == frozenset({str(outside), "rel.py"})


# ── resume ───────────────────────────────────────────────────────────────────


def test_restore_state_from_log_shortens_paths_and_rebuilds_tracked_files(h: Harness) -> None:
    absolute = str(h.root / "a.py")
    snapshots = [
        FileHistorySnapshot("u1", {absolute: FileHistoryBackup("x@v1", 1, T0)}, T0),
        FileHistorySnapshot("u2", {"a.py": FileHistoryBackup("x@v2", 2, T0), "/abs/b.py":
                                   FileHistoryBackup(None, 1, T0)}, T0),
    ]
    seen: list[FileHistoryState] = []
    fh.file_history_restore_state_from_log(snapshots, seen.append, h.bootstrap)
    (state,) = seen
    assert state.tracked_files == frozenset({"a.py", "/abs/b.py"})
    assert [dict(s.tracked_file_backups) for s in state.snapshots] == [
        {"a.py": FileHistoryBackup("x@v1", 1, T0)},
        {"a.py": FileHistoryBackup("x@v2", 2, T0), "/abs/b.py": FileHistoryBackup(None, 1, T0)},
    ]
    assert state.snapshot_sequence == 2


class Log:
    def __init__(self, snapshots: Any, messages: Any, session_id: str | None) -> None:
        self.file_history_snapshots = snapshots
        self.messages = messages
        self.session_id = session_id


def test_copy_for_resume_links_the_previous_sessions_backups(h: Harness) -> None:
    old = Harness(h.root, session_id="old")
    a = write(h.root / "a.py", "a\n")
    old.snapshot("u1")
    old.track(a)
    snapshot = old.state.snapshots[-1]
    missing = FileHistorySnapshot("u2", {"z.py": FileHistoryBackup("gone@v1", 1, T0)}, T0)
    asyncio.run(fh.copy_file_history_for_resume(Log([snapshot, missing], ["m"], "old"), h.bootstrap, h.record))
    name = str(snapshot.tracked_file_backups["a.py"].backup_file_name)
    assert (h.backup_dir() / name).read_text(encoding="utf-8") == "a\n"
    # Only the snapshot whose backups all arrived is recorded.
    assert h.records == [("u1", snapshot, False)]
    # Already migrated: linked again without error.
    asyncio.run(fh.copy_file_history_for_resume(Log([snapshot], ["m"], "old"), h.bootstrap, h.record))
    assert len(h.records) == 2


def test_copy_for_resume_falls_back_to_a_copy(h: Harness, monkeypatch: Any) -> None:
    old = Harness(h.root, session_id="old")
    a = write(h.root / "a.py", "a\n")
    old.snapshot("u1")
    old.track(a)

    def no_links(src: Any, dst: Any) -> None:
        raise OSError(18, "cross-device")

    monkeypatch.setattr(os, "link", no_links)
    asyncio.run(fh.copy_file_history_for_resume(Log(old.state.snapshots, ["m"], "old"), h.bootstrap, h.record))
    assert len(list(h.backup_dir().iterdir())) == 1
    assert len(h.records) == 1


@pytest.mark.parametrize("log", [
    Log(None, ["m"], "old"), Log([], ["m"], "old"), Log([FileHistorySnapshot("u", {}, T0)], [], "old"),
    Log([FileHistorySnapshot("u", {}, T0)], ["m"], None), Log([FileHistorySnapshot("u", {}, T0)], ["m"], "sess-1"),
])
def test_copy_for_resume_has_nothing_to_do(h: Harness, log: Log) -> None:
    asyncio.run(fh.copy_file_history_for_resume(log, h.bootstrap, h.record))
    assert h.records == [] and not h.backup_dir().exists()


# ── the transcript form ──────────────────────────────────────────────────────


def test_a_snapshot_round_trips_through_its_json_form() -> None:
    snapshot = FileHistorySnapshot("u1", {"a.py": FileHistoryBackup("h@v1", 1, T0),
                                          "b.py": FileHistoryBackup(None, 2, T0)}, T0)
    data = fh.snapshot_to_json(snapshot)
    assert data == {
        "messageId": "u1",
        "trackedFileBackups": {
            "a.py": {"backupFileName": "h@v1", "version": 1, "backupTime": "2026-01-02T03:04:05.678Z"},
            "b.py": {"backupFileName": None, "version": 2, "backupTime": "2026-01-02T03:04:05.678Z"},
        },
        "timestamp": "2026-01-02T03:04:05.678Z",
    }
    assert fh.snapshot_from_json(json.loads(json.dumps(data))) == snapshot


def test_unreadable_snapshot_records() -> None:
    assert fh.snapshot_from_json(None) is None
    assert fh.snapshot_from_json({"messageId": 3}) is None
    parsed = fh.snapshot_from_json({"messageId": "u", "trackedFileBackups": {
        "ok": {"backupFileName": "n", "version": 1, "backupTime": "bad"},
        "bad-name": {"backupFileName": 4, "version": 1}, "bad-version": {"backupFileName": "n", "version": "1"},
        "not-a-dict": 7}, "timestamp": 5})
    assert parsed is not None
    assert list(parsed.tracked_file_backups) == ["ok"]
    assert parsed.timestamp == datetime.fromtimestamp(0, tz=UTC)
    assert parsed.tracked_file_backups["ok"].backup_time == datetime.fromtimestamp(0, tz=UTC)
    assert fh.snapshot_from_json({"messageId": "u"}) == FileHistorySnapshot("u", {}, datetime.fromtimestamp(0, UTC))


# ── handle_rewind_files (print.ts) ───────────────────────────────────────────


def test_handle_rewind_files_refuses_when_disabled_or_unknown(h: Harness, monkeypatch: Any) -> None:
    h.snapshot("u1")
    result = asyncio.run(fh.handle_rewind_files("u9", h.store.get(), h.store.update, False))
    assert result == RewindFilesResult(can_rewind=False, error="No file checkpoint found for this message.")
    monkeypatch.setenv(fh.DISABLE_FILE_CHECKPOINTING_ENV, "1")
    result = asyncio.run(fh.handle_rewind_files("u1", h.store.get(), h.store.update, False))
    assert result.to_json() == {"canRewind": False, "error": "File rewinding is not enabled."}


def test_handle_rewind_files_dry_run_reports_without_touching_files(h: Harness) -> None:
    a, b = _session_with_edits(h)
    result = asyncio.run(fh.handle_rewind_files("u1", h.store.get(), h.store.update, True))
    assert result.can_rewind is True and result.error is None
    assert sorted(result.files_changed or []) == sorted([str(a), str(b)])
    assert (result.insertions, result.deletions) == (0, 3)
    assert b.exists()
    assert set(result.to_json()) == {"canRewind", "filesChanged", "insertions", "deletions"}


def test_handle_rewind_files_rewinds(h: Harness) -> None:
    a, b = _session_with_edits(h)
    before = h.store.get()
    result = asyncio.run(fh.handle_rewind_files("u1", h.store.get(), h.store.update, False))
    assert result == RewindFilesResult(can_rewind=True)
    assert result.to_json() == {"canRewind": True}
    assert a.read_text(encoding="utf-8") == "one\n" and not b.exists()
    # The TDD seam leaves the state as it is (pending decision), as upstream.
    assert h.store.get() == before


def test_handle_rewind_files_reports_a_failed_rewind(h: Harness, monkeypatch: Any) -> None:
    h.snapshot("u1")

    async def broken(*args: Any) -> None:
        raise fh.FileHistoryError("boom")

    monkeypatch.setattr(fh, "file_history_rewind", broken)
    result = asyncio.run(fh.handle_rewind_files("u1", h.store.get(), h.store.update, False))
    assert result == RewindFilesResult(can_rewind=False, error="Failed to rewind: boom")
