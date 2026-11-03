"""
File checkpointing: a backup of every file the agents edit, taken before the edit, and a
snapshot of the tracked files at every user prompt, so the files can be rewound to the state
they were in when that prompt was sent (upstream's "rewind code").

Ported from `reference/claude-code/src/utils/fileHistory.ts` -> `FileHistoryBackup`,
`FileHistorySnapshot`, `FileHistoryState`, `MAX_SNAPSHOTS`, `DiffStats`, `fileHistoryEnabled`,
`fileHistoryEnabledSdk`, `fileHistoryTrackEdit`, `fileHistoryMakeSnapshot`, `fileHistoryRewind`,
`fileHistoryCanRestore`, `fileHistoryGetDiffStats`, `fileHistoryHasAnyChanges`, `applySnapshot`,
`checkOriginFileChanged`, `compareStatsAndContent`, `computeDiffStatsForFile`,
`getBackupFileName`, `resolveBackupPath`, `createBackup`, `restoreBackup`,
`getBackupFileNameFirstVersion`, `maybeShortenFilePath`, `maybeExpandFilePath`,
`fileHistoryRestoreStateFromLog`, `copyFileHistoryForResume` and `readFileAsyncOrNull`;
`reference/claude-code/src/cli/print.ts` -> `handleRewindFiles` (and the `RewindFilesResult`
shape of `entrypoints/sdk/coreSchemas.ts` -> `RewindFilesResultSchema`); and the
`updateFileHistoryState` wrapper `QueryEngine.ts` / `REPL.tsx` build over `setAppState`.

Backups live under the config home, `<config home>/file-history/<session id>/<hash>@v<n>`
(upstream `~/.claude/file-history/...`), outside the project, where no agent can read or edit
them. A snapshot is persisted to the transcript as a `file-history-snapshot` entry
(`app/utils/session_storage.py`) and restored from it on resume.

**The run's identity.** Upstream reads `getSessionId`, `getOriginalCwd` and
`getIsNonInteractiveSession` from the process-wide `STATE`; here they are the run's
`SessionBootstrap` (`app/bootstrap/state.py`, kept on `AppState.bootstrap`), passed to every
function that reads them. `recordFileHistorySnapshot` is upstream's session-global `Project`
write; here the caller passes the transcript's writer (`record`), `None` when nothing persists.

**The setting.** Interactively the feature is on unless the global config
(`<config home>/.tddagents.json`, upstream `~/.claude.json`) says `"fileCheckpointingEnabled":
false` or `TDDAGENTS_DISABLE_FILE_CHECKPOINTING` is set; a headless run needs
`TDDAGENTS_ENABLE_SDK_FILE_CHECKPOINTING` (upstream `CLAUDE_CODE_*`).

Divergences, each with its reason:

* **IO is synchronous** inside the `async` functions, as this port's transcript writes are, and
  the per-file `Promise.all` fan-outs run one file after another: the result is the same set of
  backups. The snapshot record is written right after the state update instead of from inside
  the updater (`AppStateStore.update` runs an updater exactly once, so the order is the same).
* **The line counts of `fileHistoryGetDiffStats`** come from `difflib.SequenceMatcher` over
  newline-kept lines, not the `diff` package's Myers `diffLines`; on unusual inputs the two can
  pick a different (equally valid) alignment and so different counts.
* **Not ported**: analytics (`logEvent`), `notifyVscodeSnapshotFilesUpdated` (no IDE
  integration here) and `maybeDumpStateForDebug` (behind `ENABLE_DUMP_STATE = false`, dead).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from app.bootstrap.state import SessionBootstrap
from app.utils.tool_result_storage import config_home

if TYPE_CHECKING:
    from app.state.app_state_store import AppState

logger = logging.getLogger(__name__)

#: `fileHistory.ts` -> `MAX_SNAPSHOTS`: the snapshots kept; older ones are evicted.
MAX_SNAPSHOTS = 100

#: `envUtils.ts`'s `CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING`: file history in a headless run.
ENABLE_SDK_FILE_CHECKPOINTING_ENV = "TDDAGENTS_ENABLE_SDK_FILE_CHECKPOINTING"
#: `CLAUDE_CODE_DISABLE_FILE_CHECKPOINTING`: no file history, in any run.
DISABLE_FILE_CHECKPOINTING_ENV = "TDDAGENTS_DISABLE_FILE_CHECKPOINTING"
#: `GlobalConfig.fileCheckpointingEnabled` (`utils/config.ts`, default `true`), the global
#: config key the settings dialog and `ConfigTool` write.
FILE_CHECKPOINTING_ENABLED_KEY = "fileCheckpointingEnabled"
#: The backups' directory under the config home (`resolveBackupPath`).
FILE_HISTORY_DIR = "file-history"

#: `handleRewindFiles`'s refusals and the snapshot-missing error of `fileHistoryRewind`.
REWIND_NOT_ENABLED_ERROR = "File rewinding is not enabled."
NO_CHECKPOINT_ERROR = "No file checkpoint found for this message."
SNAPSHOT_NOT_FOUND_ERROR = "The selected snapshot was not found"


@dataclass(frozen=True, slots=True)
class FileHistoryBackup:
    """`FileHistoryBackup`: `backup_file_name` is `None` when the file did not exist."""

    backup_file_name: str | None
    version: int
    backup_time: datetime


@dataclass(frozen=True, slots=True)
class FileHistorySnapshot:
    """`FileHistorySnapshot`: the tracked files' backups when user message `message_id` was sent."""

    message_id: str
    tracked_file_backups: Mapping[str, FileHistoryBackup]
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class FileHistoryState:
    """
    `FileHistoryState`. `snapshot_sequence` grows with every snapshot, even once old ones are
    evicted (upstream's activity signal; `len(snapshots)` plateaus at `MAX_SNAPSHOTS`).
    """

    snapshots: tuple[FileHistorySnapshot, ...] = ()
    tracked_files: frozenset[str] = frozenset()
    snapshot_sequence: int = 0


@dataclass(frozen=True, slots=True)
class DiffStats:
    """`DiffStats` (the `undefined` case is `None` at the call sites)."""

    insertions: int
    deletions: int
    files_changed: list[str] | None = None


@dataclass(frozen=True, slots=True)
class RewindFilesResult:
    """`RewindFilesResult` (`RewindFilesResultSchema`): what `handle_rewind_files` answers."""

    can_rewind: bool
    error: str | None = None
    files_changed: list[str] | None = None
    insertions: int | None = None
    deletions: int | None = None

    def to_json(self) -> dict[str, Any]:
        """The SDK's wire form: camelCase, the optional fields left out when unset."""
        out: dict[str, Any] = {"canRewind": self.can_rewind}
        for key, value in (("error", self.error), ("filesChanged", self.files_changed),
                           ("insertions", self.insertions), ("deletions", self.deletions)):
            if value is not None:
                out[key] = value
        return out


class FileHistoryError(Exception):
    """`fileHistoryRewind`'s `new Error(...)`."""


#: `(prev: FileHistoryState) => FileHistoryState`.
FileHistoryUpdater = Callable[[FileHistoryState], FileHistoryState]
#: `ToolUseContext.updateFileHistoryState`.
UpdateFileHistoryState = Callable[[FileHistoryUpdater], None]
#: `recordFileHistorySnapshot(messageId, snapshot, isSnapshotUpdate)`.
SnapshotRecorder = Callable[[str, FileHistorySnapshot, bool], None]


def noop_update_file_history_state(updater: FileHistoryUpdater) -> None:
    """
    The `() => {}` stub upstream hands a subagent (`createSubagentContext`), `mcp.ts` and
    `queryContext.ts`: the updater never runs, so every file history function sees no state and
    returns without doing anything.
    """


def update_file_history_state_for(set_app_state: Callable[[Callable[[AppState], AppState]], None]) -> (
        UpdateFileHistoryState):
    """
    `updateFileHistoryState` as `QueryEngine.ts` and `REPL.tsx` build it over `setAppState`: an
    updater that returns the same state leaves the app state untouched (no listener runs).
    """

    def update(updater: FileHistoryUpdater) -> None:
        def apply(prev: AppState) -> AppState:
            updated = updater(prev.file_history)
            if updated is prev.file_history:
                return prev
            return replace(prev, file_history=updated)

        set_app_state(apply)

    return update


# ── the setting ──────────────────────────────────────────────────────────────


def _is_env_truthy(name: str) -> bool:
    # Function-level import: `claudemd` pulls in the settings stack, which imports `AppState`.
    from app.utils.claudemd import is_env_truthy

    return is_env_truthy(os.environ.get(name))


def _global_config_file_checkpointing_enabled() -> Any:
    """`getGlobalConfig().fileCheckpointingEnabled` (`True` by default, as `DEFAULT_GLOBAL_CONFIG`)."""
    # Function-level import, for the same cycle as `_is_env_truthy`.
    from app.services.mcp.config import get_global_config_path

    try:
        data = json.loads(get_global_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    return data.get(FILE_CHECKPOINTING_ENABLED_KEY, True) if isinstance(data, dict) else True


def file_history_enabled(bootstrap: SessionBootstrap) -> bool:
    """`fileHistoryEnabled`."""
    if bootstrap.get_is_non_interactive_session():
        return _file_history_enabled_sdk()
    return (
        _global_config_file_checkpointing_enabled() is not False
        and not _is_env_truthy(DISABLE_FILE_CHECKPOINTING_ENV)
    )


def _file_history_enabled_sdk() -> bool:
    """`fileHistoryEnabledSdk`."""
    return _is_env_truthy(ENABLE_SDK_FILE_CHECKPOINTING_ENV) and not _is_env_truthy(DISABLE_FILE_CHECKPOINTING_ENV)


def _capture(update_file_history_state: UpdateFileHistoryState) -> FileHistoryState | None:
    """Upstream's no-op updater that captures the state (`None` from a no-op stub)."""
    captured: list[FileHistoryState] = []

    def no_op(state: FileHistoryState) -> FileHistoryState:
        captured.append(state)
        return state

    update_file_history_state(no_op)
    return captured[0] if captured else None


def _record(record: SnapshotRecorder | None, message_id: str, snapshot: FileHistorySnapshot, update: bool) -> None:
    """`void recordFileHistorySnapshot(...).catch(...)`: a failed write is logged, never raised."""
    if record is None:
        return
    try:
        record(message_id, snapshot, update)
    except Exception as error:  # noqa: BLE001 - upstream's `.catch(logError)` on a fire-and-forget write
        logger.error("FileHistory: Failed to record snapshot: %s", error)


# ── tracking, snapshots, rewind ──────────────────────────────────────────────


async def file_history_track_edit(
    update_file_history_state: UpdateFileHistoryState,
    file_path: str,
    message_id: str,
    bootstrap: SessionBootstrap,
    record: SnapshotRecorder | None = None,
) -> None:
    """
    `fileHistoryTrackEdit`: back up `file_path`'s current content (if necessary). It must be
    called before the file is added or edited, so the backup holds its content before the edit.
    """
    if not file_history_enabled(bootstrap):
        return

    tracking_path = _maybe_shorten_file_path(file_path, bootstrap)

    # Phase 1: check if a backup is needed. A speculative write would overwrite the
    # deterministic {hash}@v1 backup on every repeat call: a second trackEdit after an edit
    # would corrupt v1 with post-edit content.
    captured = _capture(update_file_history_state)
    if captured is None:
        return
    most_recent = captured.snapshots[-1] if captured.snapshots else None
    if most_recent is None:
        logger.error("FileHistory: Missing most recent snapshot")
        return
    if tracking_path in most_recent.tracked_file_backups:
        # Already tracked in the most recent snapshot; the next snapshot re-checks the file and
        # backs it up again if it changed. The v1 backup is not touched.
        return

    # Phase 2: the backup.
    try:
        backup = _create_backup(file_path, 1, bootstrap)
    except Exception as error:  # noqa: BLE001 - upstream logs any backup failure and gives up
        logger.error("%s", error)
        return

    # Phase 3: commit. Re-check that the file is untracked (another trackEdit may have raced).
    committed: list[FileHistorySnapshot] = []

    def commit(state: FileHistoryState) -> FileHistoryState:
        last = state.snapshots[-1] if state.snapshots else None
        if last is None or tracking_path in last.tracked_file_backups:
            return state
        # The file is not tracked in the most recent snapshot yet, so it is tracked there
        # retroactively.
        updated_last = replace(last, tracked_file_backups={**last.tracked_file_backups, tracking_path: backup})
        committed.append(updated_last)
        return replace(
            state,
            snapshots=(*state.snapshots[:-1], updated_last),
            tracked_files=state.tracked_files | {tracking_path},
        )

    update_file_history_state(commit)
    if committed:
        # The snapshot changed, so the update is recorded (`isSnapshotUpdate`).
        _record(record, message_id, committed[0], True)
        logger.debug("FileHistory: Tracked file modification for %s", file_path)


async def file_history_make_snapshot(
    update_file_history_state: UpdateFileHistoryState,
    message_id: str,
    bootstrap: SessionBootstrap,
    record: SnapshotRecorder | None = None,
) -> None:
    """`fileHistoryMakeSnapshot`: add a snapshot, backing up any tracked file that changed."""
    if not file_history_enabled(bootstrap):
        return

    # Phase 1: capture the state, to know which files to back up.
    captured = _capture(update_file_history_state)
    if captured is None:
        return  # update_file_history_state was a no-op stub

    # Phase 2: all the IO, outside the updater.
    tracked_file_backups: dict[str, FileHistoryBackup] = {}
    most_recent = captured.snapshots[-1] if captured.snapshots else None
    if most_recent is not None:
        logger.debug("FileHistory: Making snapshot for message %s", message_id)
        for tracking_path in captured.tracked_files:
            try:
                backup = _next_backup(tracking_path, most_recent, bootstrap)
            except Exception as error:  # noqa: BLE001 - upstream logs a failed file and goes on
                logger.error("%s", error)
                continue
            tracked_file_backups[tracking_path] = backup

    # Phase 3: commit the new snapshot. The tracked files are read fresh: a file
    # `file_history_track_edit` added meanwhile inherits its backup from the last snapshot.
    committed: list[FileHistorySnapshot] = []

    def commit(state: FileHistoryState) -> FileHistoryState:
        last = state.snapshots[-1] if state.snapshots else None
        if last is not None:
            for tracking_path in state.tracked_files:
                if tracking_path in tracked_file_backups:
                    continue
                inherited = last.tracked_file_backups.get(tracking_path)
                if inherited is not None:
                    tracked_file_backups[tracking_path] = inherited
        new_snapshot = FileHistorySnapshot(
            message_id=message_id, tracked_file_backups=dict(tracked_file_backups), timestamp=datetime.now(UTC)
        )
        committed.append(new_snapshot)
        all_snapshots = (*state.snapshots, new_snapshot)
        return replace(
            state,
            snapshots=all_snapshots[-MAX_SNAPSHOTS:] if len(all_snapshots) > MAX_SNAPSHOTS else all_snapshots,
            snapshot_sequence=state.snapshot_sequence + 1,
        )

    update_file_history_state(commit)
    if committed:
        # Recorded to the transcript for resume.
        _record(record, message_id, committed[0], False)
        logger.debug("FileHistory: Added snapshot for %s, tracking %d files",
                     message_id, len(captured.tracked_files))


def _next_backup(tracking_path: str, most_recent: FileHistorySnapshot, bootstrap: SessionBootstrap) -> (
        FileHistoryBackup):
    """One tracked file's backup in a new snapshot: missing, unchanged (reused), or a new version."""
    file_path = _maybe_expand_file_path(tracking_path, bootstrap)
    latest_backup = most_recent.tracked_file_backups.get(tracking_path)
    next_version = latest_backup.version + 1 if latest_backup is not None else 1

    # Stat the file once; ENOENT means the tracked file was deleted.
    try:
        file_stats: os.stat_result | None = os.stat(file_path)
    except FileNotFoundError:
        file_stats = None

    if file_stats is None:
        logger.debug("FileHistory: Missing tracked file: %s", tracking_path)
        return FileHistoryBackup(backup_file_name=None, version=next_version, backup_time=datetime.now(UTC))

    if (
        latest_backup is not None
        and latest_backup.backup_file_name is not None
        and not check_origin_file_changed(file_path, latest_backup.backup_file_name, bootstrap, file_stats)
    ):
        # The file has not changed since the latest version: reuse it.
        return latest_backup

    # The file is newer than the latest backup.
    return _create_backup(file_path, next_version, bootstrap)


def _find_last(state: FileHistoryState, message_id: str) -> FileHistorySnapshot | None:
    """`snapshots.findLast(s => s.messageId === messageId)`."""
    for snapshot in reversed(state.snapshots):
        if snapshot.message_id == message_id:
            return snapshot
    return None


async def file_history_rewind(
    update_file_history_state: UpdateFileHistoryState, message_id: str, bootstrap: SessionBootstrap
) -> None:
    """
    `fileHistoryRewind`: put the tracked files back as they were in the snapshot of
    `message_id`. A pure filesystem side effect: the state is not changed. Raises when there is
    no such snapshot, or when applying it fails.
    """
    if not file_history_enabled(bootstrap):
        return

    captured = _capture(update_file_history_state)
    if captured is None:
        return

    target = _find_last(captured, message_id)
    if target is None:
        logger.error("FileHistory: Snapshot for %s not found", message_id)
        raise FileHistoryError(SNAPSHOT_NOT_FOUND_ERROR)

    logger.debug("FileHistory: [Rewind] Rewinding to snapshot for %s", message_id)
    _apply_snapshot(captured, target, bootstrap)
    logger.debug("FileHistory: [Rewind] Finished rewinding to %s", message_id)


def file_history_can_restore(state: FileHistoryState, message_id: str, bootstrap: SessionBootstrap) -> bool:
    """`fileHistoryCanRestore`: there is a snapshot for `message_id`."""
    if not file_history_enabled(bootstrap):
        return False
    return any(snapshot.message_id == message_id for snapshot in state.snapshots)


def _target_backup_file_name(
    tracking_path: str, target: FileHistorySnapshot, state: FileHistoryState
) -> tuple[bool, str | None]:
    """
    The backup a rewind to `target` restores `tracking_path` from: the target's own, else the
    file's first version (it was tracked only later). `(False, None)` is upstream's `undefined`:
    no backup could be found, so the file is left alone.
    """
    target_backup = target.tracked_file_backups.get(tracking_path)
    if target_backup is not None:
        return True, target_backup.backup_file_name
    return _get_backup_file_name_first_version(tracking_path, state)


async def file_history_get_diff_stats(
    state: FileHistoryState, message_id: str, bootstrap: SessionBootstrap
) -> DiffStats | None:
    """
    `fileHistoryGetDiffStats`: which files a rewind to `message_id` would change, and the lines
    it would insert and delete. `None` when disabled or there is no such snapshot.
    """
    if not file_history_enabled(bootstrap):
        return None

    target = _find_last(state, message_id)
    if target is None:
        return None

    files_changed: list[str] = []
    insertions = 0
    deletions = 0
    for tracking_path in state.tracked_files:
        try:
            file_path = _maybe_expand_file_path(tracking_path, bootstrap)
            found, backup_file_name = _target_backup_file_name(tracking_path, target, state)
            if not found:
                # Error resolving the backup, so the file is not touched.
                logger.error("FileHistory: Error finding the backup file to apply")
                continue
            stats = _compute_diff_stats_for_file(file_path, backup_file_name, bootstrap)
            if stats.insertions or stats.deletions or (backup_file_name is None and os.path.exists(file_path)):
                # A zero-byte file created after the snapshot counts as changed, though the diff
                # reports 0/0.
                files_changed.append(file_path)
                insertions += stats.insertions
                deletions += stats.deletions
        except Exception as error:  # noqa: BLE001 - upstream logs a failed file and goes on
            logger.error("%s", error)
    return DiffStats(files_changed=files_changed, insertions=insertions, deletions=deletions)


async def file_history_has_any_changes(state: FileHistoryState, message_id: str, bootstrap: SessionBootstrap) -> bool:
    """
    `fileHistoryHasAnyChanges`: whether a rewind to `message_id` would change any file on disk,
    with the stat/content comparison of a rewind (no diff), stopping at the first changed file.
    """
    if not file_history_enabled(bootstrap):
        return False

    target = _find_last(state, message_id)
    if target is None:
        return False

    for tracking_path in state.tracked_files:
        try:
            file_path = _maybe_expand_file_path(tracking_path, bootstrap)
            found, backup_file_name = _target_backup_file_name(tracking_path, target, state)
            if not found:
                continue
            if backup_file_name is None:
                # The file did not exist in the backup.
                if os.path.exists(file_path):
                    return True
                continue
            if check_origin_file_changed(file_path, backup_file_name, bootstrap):
                return True
        except Exception as error:  # noqa: BLE001 - upstream logs a failed file and goes on
            logger.error("%s", error)
    return False


def _apply_snapshot(state: FileHistoryState, target: FileHistorySnapshot, bootstrap: SessionBootstrap) -> list[str]:
    """`applySnapshot`: write or delete the tracked files on disk; the paths that changed."""
    files_changed: list[str] = []
    for tracking_path in state.tracked_files:
        try:
            file_path = _maybe_expand_file_path(tracking_path, bootstrap)
            found, backup_file_name = _target_backup_file_name(tracking_path, target, state)
            if not found:
                # Error resolving the backup, so the file is not touched.
                logger.error("FileHistory: Error finding the backup file to apply")
                continue

            if backup_file_name is None:
                # The file did not exist at the target version: delete it if present.
                try:
                    os.unlink(file_path)
                except FileNotFoundError:
                    continue  # Already absent.
                logger.debug("FileHistory: [Rewind] Deleted %s", file_path)
                files_changed.append(file_path)
                continue

            # The file should exist at a specific version: restore it only if it differs.
            if check_origin_file_changed(file_path, backup_file_name, bootstrap):
                _restore_backup(file_path, backup_file_name, bootstrap)
                logger.debug("FileHistory: [Rewind] Restored %s from %s", file_path, backup_file_name)
                files_changed.append(file_path)
        except Exception as error:  # noqa: BLE001 - upstream logs a failed file and goes on
            logger.error("%s", error)
    return files_changed


# ── comparing, backing up, restoring ─────────────────────────────────────────


def check_origin_file_changed(
    original_file: str,
    backup_file_name: str,
    bootstrap: SessionBootstrap,
    original_stats_hint: os.stat_result | None = None,
) -> bool:
    """
    `checkOriginFileChanged`: whether `original_file` differs from the backup. A stat the caller
    already took can be passed as `original_stats_hint`.
    """
    backup_path = _resolve_backup_path(backup_file_name, bootstrap)

    original_stats = original_stats_hint
    if original_stats is None:
        try:
            original_stats = os.stat(original_file)
        except FileNotFoundError:
            pass
        except OSError:
            return True
    try:
        backup_stats: os.stat_result | None = os.stat(backup_path)
    except FileNotFoundError:
        backup_stats = None
    except OSError:
        return True

    def contents_differ() -> bool:
        try:
            return Path(original_file).read_text(encoding="utf-8") != Path(backup_path).read_text(encoding="utf-8")
        except (OSError, ValueError):
            # Deleted between the stat and the read: changed.
            return True

    return _compare_stats_and_content(original_stats, backup_stats, contents_differ)


def _compare_stats_and_content(
    original_stats: os.stat_result | None,
    backup_stats: os.stat_result | None,
    compare_content: Callable[[], bool],
) -> bool:
    """`compareStatsAndContent`: whether the file changed relative to the backup."""
    # One exists, one is missing: changed.
    if (original_stats is None) != (backup_stats is None):
        return True
    # Both missing: no change.
    if original_stats is None or backup_stats is None:
        return False
    # Permissions or size.
    if original_stats.st_mode != backup_stats.st_mode or original_stats.st_size != backup_stats.st_size:
        return True
    # An optimization on the modified time: a file last modified before the backup was taken
    # needs no content comparison.
    if original_stats.st_mtime_ns < backup_stats.st_mtime_ns:
        return False
    return compare_content()


def _read_text_or_none(path: str) -> str | None:
    """`readFileAsyncOrNull`: the file's text, or `None` on any error."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None


def _lines(text: str) -> list[str]:
    """The `diff` package's line tokens: split after each `\\n`, the newline kept with its line."""
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _compute_diff_stats_for_file(
    original_file: str, backup_file_name: str | None, bootstrap: SessionBootstrap
) -> DiffStats:
    """`computeDiffStatsForFile`: the lines a rewind would insert into and delete from the file."""
    files_changed: list[str] = []
    insertions = 0
    deletions = 0
    try:
        backup_path = _resolve_backup_path(backup_file_name, bootstrap) if backup_file_name else None
        original_content = _read_text_or_none(original_file)
        backup_content = _read_text_or_none(backup_path) if backup_path is not None else None
        if original_content is None and backup_content is None:
            return DiffStats(files_changed=files_changed, insertions=insertions, deletions=deletions)
        files_changed.append(original_file)
        before, after = _lines(original_content or ""), _lines(backup_content or "")
        for tag, i1, i2, j1, j2 in SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
            if tag in ("replace", "delete"):
                deletions += i2 - i1
            if tag in ("replace", "insert"):
                insertions += j2 - j1
    except Exception as error:  # noqa: BLE001 - upstream logs a failed diff and reports what it has
        logger.error("FileHistory: Error generating diffStats: %s", error)
    return DiffStats(files_changed=files_changed, insertions=insertions, deletions=deletions)


def get_backup_file_name(file_path: str, version: int) -> str:
    """`getBackupFileName`: the first 16 hex digits of the path's SHA-256, `@v` and the version."""
    file_name_hash = hashlib.sha256(file_path.encode("utf-8")).hexdigest()[:16]
    return f"{file_name_hash}@v{version}"


def get_file_history_dir(session_id: str) -> Path:
    """The session's backups directory: `<config home>/file-history/<session id>`."""
    return config_home() / FILE_HISTORY_DIR / session_id


def _resolve_backup_path(backup_file_name: str, bootstrap: SessionBootstrap, session_id: str | None = None) -> str:
    """`resolveBackupPath`: under the given session's directory, else the current session's."""
    return str(get_file_history_dir(session_id or bootstrap.session_id) / backup_file_name)


def _create_backup(file_path: str | None, version: int, bootstrap: SessionBootstrap) -> FileHistoryBackup:
    """
    `createBackup`: copy the file to its backup, with its permissions. A file that does not exist
    is recorded as a `None` backup (the did-not-exist marker). The directory is made lazily.
    """
    if file_path is None:
        return FileHistoryBackup(backup_file_name=None, version=version, backup_time=datetime.now(UTC))

    backup_file_name = get_backup_file_name(file_path, version)
    backup_path = _resolve_backup_path(backup_file_name, bootstrap)

    # Stat first: a missing source is a `None` backup, and no copy.
    try:
        src_stats = os.stat(file_path)
    except FileNotFoundError:
        return FileHistoryBackup(backup_file_name=None, version=version, backup_time=datetime.now(UTC))

    # Lazy mkdir: the directory almost always exists already; on ENOENT it is made, then the
    # copy retried.
    try:
        shutil.copyfile(file_path, backup_path)
    except FileNotFoundError:
        os.makedirs(os.path.dirname(backup_path), exist_ok=True)
        shutil.copyfile(file_path, backup_path)

    # Preserve the file's permissions on the backup.
    os.chmod(backup_path, src_stats.st_mode)
    return FileHistoryBackup(backup_file_name=backup_file_name, version=version, backup_time=datetime.now(UTC))


def _restore_backup(file_path: str, backup_file_name: str, bootstrap: SessionBootstrap) -> None:
    """`restoreBackup`: copy the backup over the file, with the backup's permissions."""
    backup_path = _resolve_backup_path(backup_file_name, bootstrap)

    # Stat first: a missing backup is logged, and nothing is copied.
    try:
        backup_stats = os.stat(backup_path)
    except FileNotFoundError:
        logger.error("FileHistory: [Rewind] Backup file not found: %s", backup_path)
        return

    # Lazy mkdir: the destination's directory almost always exists.
    try:
        shutil.copyfile(backup_path, file_path)
    except FileNotFoundError:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        shutil.copyfile(backup_path, file_path)

    # Restore the file's permissions.
    os.chmod(file_path, backup_stats.st_mode)


def _get_backup_file_name_first_version(tracking_path: str, state: FileHistoryState) -> tuple[bool, str | None]:
    """
    `getBackupFileNameFirstVersion`: the file's earliest backup (version 1), used when rewinding
    to a snapshot taken before the file was tracked. `(True, None)`: the file did not exist then;
    `(False, None)`: no first version could be found (upstream's `undefined`).
    """
    for snapshot in state.snapshots:
        backup = snapshot.tracked_file_backups.get(tracking_path)
        if backup is not None and backup.version == 1:
            return True, backup.backup_file_name
    return False, None


def _maybe_shorten_file_path(file_path: str, bootstrap: SessionBootstrap) -> str:
    """`maybeShortenFilePath`: the path relative to the original directory, to keep records small."""
    if not os.path.isabs(file_path):
        return file_path
    cwd = bootstrap.get_original_cwd()
    if file_path.startswith(cwd):
        return os.path.relpath(file_path, cwd)
    return file_path


def _maybe_expand_file_path(file_path: str, bootstrap: SessionBootstrap) -> str:
    """`maybeExpandFilePath`."""
    if os.path.isabs(file_path):
        return file_path
    return os.path.join(bootstrap.get_original_cwd(), file_path)


# ── resume ───────────────────────────────────────────────────────────────────


def file_history_restore_state_from_log(
    file_history_snapshots: Sequence[FileHistorySnapshot],
    on_update_state: Callable[[FileHistoryState], None],
    bootstrap: SessionBootstrap,
) -> None:
    """
    `fileHistoryRestoreStateFromLog`: the state rebuilt from a transcript's snapshots, with the
    tracking paths shortened (older records hold absolute paths) and the tracked files rebuilt.
    """
    if not file_history_enabled(bootstrap):
        return
    snapshots: list[FileHistorySnapshot] = []
    tracked_files: set[str] = set()
    for snapshot in file_history_snapshots:
        tracked_file_backups: dict[str, FileHistoryBackup] = {}
        for path, backup in snapshot.tracked_file_backups.items():
            tracking_path = _maybe_shorten_file_path(path, bootstrap)
            tracked_files.add(tracking_path)
            tracked_file_backups[tracking_path] = backup
        snapshots.append(replace(snapshot, tracked_file_backups=tracked_file_backups))
    on_update_state(FileHistoryState(
        snapshots=tuple(snapshots), tracked_files=frozenset(tracked_files), snapshot_sequence=len(snapshots)
    ))


class LogOption(Protocol):
    """What `copyFileHistoryForResume` reads of upstream's `LogOption`."""

    @property
    def file_history_snapshots(self) -> Sequence[FileHistorySnapshot] | None: ...

    @property
    def messages(self) -> Sequence[Any]: ...

    @property
    def session_id(self) -> str | None:
        """The last message's `sessionId`: the session that wrote the backups."""
        ...


async def copy_file_history_for_resume(
    log: LogOption, bootstrap: SessionBootstrap, record: SnapshotRecorder | None = None
) -> None:
    """
    `copyFileHistoryForResume`: a resumed session under a new id links the previous session's
    backups into its own directory (a copy when a hard link fails) and records each snapshot
    whose backups all arrived.
    """
    if not file_history_enabled(bootstrap):
        return

    file_history_snapshots = log.file_history_snapshots
    if not file_history_snapshots or len(log.messages) == 0:
        return
    previous_session_id = log.session_id
    if not previous_session_id:
        logger.error("FileHistory: Failed to copy backups on restore (no previous session id)")
        return

    session_id = bootstrap.session_id
    if previous_session_id == session_id:
        logger.debug("FileHistory: No need to copy file history for resuming with same session id: %s", session_id)
        return

    try:
        # Every backup shares one directory, made once up front.
        new_backup_dir = get_file_history_dir(session_id)
        new_backup_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        logger.error("%s", error)
        return

    for snapshot in file_history_snapshots:
        copy_failed = False
        for backup in snapshot.tracked_file_backups.values():
            if backup.backup_file_name is None:
                continue
            try:
                _link_backup(backup.backup_file_name, previous_session_id, new_backup_dir, bootstrap)
            except OSError:
                copy_failed = True
        # The snapshot is recorded only when its backups were migrated.
        if not copy_failed:
            _record(record, snapshot.message_id, snapshot, False)


def _link_backup(backup_file_name: str, previous_session_id: str, new_backup_dir: Path,
                 bootstrap: SessionBootstrap) -> None:
    """One backup of `copyFileHistoryForResume`: a hard link, else a copy; already there is fine."""
    old_backup_path = _resolve_backup_path(backup_file_name, bootstrap, previous_session_id)
    new_backup_path = new_backup_dir / backup_file_name
    try:
        os.link(old_backup_path, new_backup_path)
    except FileExistsError:
        return  # Already migrated.
    except FileNotFoundError:
        logger.error("FileHistory: Failed to copy backup %s on restore (backup file does not exist in %s)",
                     backup_file_name, previous_session_id)
        raise
    except OSError:
        logger.error("FileHistory: Error hard linking backup file from previous session")
        # Fall back to a copy when the hard link fails.
        try:
            shutil.copyfile(old_backup_path, new_backup_path)
        except OSError:
            logger.error("FileHistory: Error copying over backup from previous session")
            raise
    logger.debug("FileHistory: Copied backup %s from session %s to %s",
                 backup_file_name, previous_session_id, bootstrap.session_id)


# ── the transcript form ──────────────────────────────────────────────────────


def _iso(moment: datetime) -> str:
    """`JSON.stringify(date)`: `toISOString()`, UTC with milliseconds."""
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_time(value: Any) -> datetime:
    """A recorded date; an unreadable one becomes the epoch rather than dropping the snapshot."""
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.fromtimestamp(0, tz=UTC)


def snapshot_to_json(snapshot: FileHistorySnapshot) -> dict[str, Any]:
    """A snapshot as upstream serializes it in a `file-history-snapshot` entry."""
    return {
        "messageId": snapshot.message_id,
        "trackedFileBackups": {
            path: {"backupFileName": backup.backup_file_name, "version": backup.version,
                   "backupTime": _iso(backup.backup_time)}
            for path, backup in snapshot.tracked_file_backups.items()
        },
        "timestamp": _iso(snapshot.timestamp),
    }


def snapshot_from_json(data: Any) -> FileHistorySnapshot | None:
    """A recorded snapshot; `None` when the record is not one."""
    if not isinstance(data, Mapping) or not isinstance(data.get("messageId"), str):
        return None
    raw_backups = data.get("trackedFileBackups")
    backups: dict[str, FileHistoryBackup] = {}
    for path, raw in (raw_backups.items() if isinstance(raw_backups, Mapping) else ()):
        if not isinstance(raw, Mapping):
            continue
        name = raw.get("backupFileName")
        version = raw.get("version")
        if (name is not None and not isinstance(name, str)) or not isinstance(version, int):
            continue
        backups[str(path)] = FileHistoryBackup(
            backup_file_name=name, version=version, backup_time=_parse_time(raw.get("backupTime")))
    return FileHistorySnapshot(
        message_id=data["messageId"], tracked_file_backups=backups, timestamp=_parse_time(data.get("timestamp")))


# ── print.ts ─────────────────────────────────────────────────────────────────


async def handle_rewind_files(
    user_message_id: str,
    app_state: AppState,
    set_app_state: Callable[[Callable[[AppState], AppState]], None],
    dry_run: bool,
    *,
    workspace: Any = None,
) -> RewindFilesResult:
    """
    `print.ts` -> `handleRewindFiles`: the `--rewind-files` flag and the SDK's `rewind_files`
    control request. With `dry_run` it only reports what a rewind would change (`filesChanged`,
    `insertions`, `deletions`); otherwise it rewinds the files to the snapshot of
    `user_message_id` and answers `can_rewind=True`, or the reason it could not. After a rewind the
    TDD ledger restarts in RED over `workspace`'s test files (`app/tdd/file_rewind.py`).
    """
    bootstrap = app_state.bootstrap
    if not file_history_enabled(bootstrap):
        return RewindFilesResult(can_rewind=False, error=REWIND_NOT_ENABLED_ERROR)
    if not file_history_can_restore(app_state.file_history, user_message_id, bootstrap):
        return RewindFilesResult(can_rewind=False, error=NO_CHECKPOINT_ERROR)

    if dry_run:
        diff_stats = await file_history_get_diff_stats(app_state.file_history, user_message_id, bootstrap)
        return RewindFilesResult(
            can_rewind=True,
            files_changed=diff_stats.files_changed if diff_stats is not None else None,
            insertions=diff_stats.insertions if diff_stats is not None else None,
            deletions=diff_stats.deletions if diff_stats is not None else None,
        )

    try:
        await file_history_rewind(update_file_history_state_for(set_app_state), user_message_id, bootstrap)
    except Exception as error:  # noqa: BLE001 - upstream's catch: any failure is the result's error
        return RewindFilesResult(can_rewind=False, error=f"Failed to rewind: {error}")

    # TDD: what a rewind does to the ledger and the plan is decided in one place. Function-level
    # import: `app.tdd` imports `AppState`, whose module imports this one.
    from app.tdd.file_rewind import tdd_state_after_file_rewind
    from app.tdd.suite_fingerprint import fingerprint_test_files

    baseline = fingerprint_test_files(workspace, app_state.project_config)
    set_app_state(lambda state: tdd_state_after_file_rewind(state, baseline))
    return RewindFilesResult(can_rewind=True)


__all__ = [
    "DISABLE_FILE_CHECKPOINTING_ENV",
    "ENABLE_SDK_FILE_CHECKPOINTING_ENV",
    "FILE_CHECKPOINTING_ENABLED_KEY",
    "FILE_HISTORY_DIR",
    "MAX_SNAPSHOTS",
    "NO_CHECKPOINT_ERROR",
    "REWIND_NOT_ENABLED_ERROR",
    "SNAPSHOT_NOT_FOUND_ERROR",
    "DiffStats",
    "FileHistoryBackup",
    "FileHistoryError",
    "FileHistorySnapshot",
    "FileHistoryState",
    "FileHistoryUpdater",
    "LogOption",
    "RewindFilesResult",
    "SnapshotRecorder",
    "UpdateFileHistoryState",
    "check_origin_file_changed",
    "copy_file_history_for_resume",
    "file_history_can_restore",
    "file_history_enabled",
    "file_history_get_diff_stats",
    "file_history_has_any_changes",
    "file_history_make_snapshot",
    "file_history_restore_state_from_log",
    "file_history_rewind",
    "file_history_track_edit",
    "get_backup_file_name",
    "get_file_history_dir",
    "handle_rewind_files",
    "noop_update_file_history_state",
    "snapshot_from_json",
    "snapshot_to_json",
    "update_file_history_state_for",
]
