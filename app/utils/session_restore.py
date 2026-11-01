"""
Restoring a resumed session's state from its transcript.

Ported from `reference/claude-code/src/utils/sessionRestore.ts` -> `restoreSessionStateFromLog`,
its file history branch: the snapshots the transcript recorded become the session's
`AppState.file_history` (`fileHistoryRestoreStateFromLog`), so a resumed session can rewind its
files to a prompt sent before the restart.

Not ported: the attribution branch and the context-collapse restore (behind the
`COMMIT_ATTRIBUTION` and `CONTEXT_COLLAPSE` build flags, off in the external build) and the
TodoWrite restore from the transcript (outside the file history port).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace

from app.bootstrap.state import SessionBootstrap
from app.state.app_state_store import AppState
from app.utils.file_history import (
    FileHistorySnapshot,
    FileHistoryState,
    file_history_restore_state_from_log,
)


def restore_session_state_from_log(
    file_history_snapshots: Sequence[FileHistorySnapshot] | None,
    set_app_state: Callable[[Callable[[AppState], AppState]], None],
    bootstrap: SessionBootstrap,
) -> None:
    """`restoreSessionStateFromLog`: the file history state, when the transcript recorded any."""
    if file_history_snapshots:

        def on_update_state(new_state: FileHistoryState) -> None:
            set_app_state(lambda prev: replace(prev, file_history=new_state))

        file_history_restore_state_from_log(file_history_snapshots, on_update_state, bootstrap)


__all__ = ["restore_session_state_from_log"]
