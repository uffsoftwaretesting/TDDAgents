"""
The REPL's prompt history: what the user typed, kept across sessions, for ↑.

Ported from `reference/claude-code/src/history.ts` -> `MAX_HISTORY_ITEMS`, `addToHistory`,
`makeLogEntryReader`, `getHistory`, and the `history.jsonl` log under the config home (one JSON
line per submission: `display`, `pastedContents`, `timestamp` in ms, `project`, `sessionId`).
`get_history` returns this project's entries, the current session's first, newest first, within
the last `MAX_HISTORY_ITEMS` of them (concurrent sessions do not interleave their ↑ history).

Divergences: an entry is appended at once under a file lock (upstream buffers and flushes in the
background, retrying, and flushes at exit); `TDDAGENTS_SKIP_PROMPT_HISTORY` is upstream's
`CLAUDE_CODE_SKIP_PROMPT_HISTORY`. Not ported: pasted-content references and the paste store (the
REPL does not collapse pastes), `getTimestampedHistory` (prompt_toolkit's Ctrl-R searches the same
strings) and `removeLastFromHistory` (the REPL has no restore-on-interrupt to undo).
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.tools.bash_tool.bash_security import is_env_truthy
from app.utils.tool_result_storage import config_home

logger = logging.getLogger(__name__)

#: `MAX_HISTORY_ITEMS`.
MAX_HISTORY_ITEMS = 100
HISTORY_FILE_NAME = "history.jsonl"
SKIP_VAR = "TDDAGENTS_SKIP_PROMPT_HISTORY"


@dataclass(frozen=True, slots=True)
class LogEntry:
    """`LogEntry`."""

    display: str
    timestamp: float
    project: str
    session_id: str | None = None

    def as_json(self) -> str:
        return json.dumps({"display": self.display, "pastedContents": {}, "timestamp": self.timestamp,
                           "project": self.project, "sessionId": self.session_id}, ensure_ascii=False)


def _parse(line: str) -> LogEntry | None:
    try:
        data: Mapping[str, Any] = json.loads(line)
    except json.JSONDecodeError as exc:
        logger.debug("Failed to parse history line: %s", exc)
        return None
    project, display = data.get("project"), data.get("display")
    if not isinstance(project, str) or not isinstance(display, str):
        return None
    session = data.get("sessionId")
    return LogEntry(display, float(data.get("timestamp") or 0), project, session if isinstance(session, str) else None)


class PromptHistory:
    """One session's view of the history log: it appends this session's prompts and reads the project's."""

    def __init__(self, project_root: str, session_id: str, path: Path | None = None,
                 now: Any = time.time, env: Mapping[str, str] | None = None) -> None:
        self.project_root = project_root
        self.session_id = session_id
        self.path = path if path is not None else config_home() / HISTORY_FILE_NAME
        self._now = now
        self._env = env

    def _skipped(self) -> bool:
        return is_env_truthy((os.environ if self._env is None else self._env).get(SKIP_VAR))

    def add_to_history(self, display: str) -> None:
        """`addToHistory`: append the prompt (a failure to write is logged, never raised)."""
        if self._skipped():
            return
        entry = LogEntry(display, self._now() * 1000, self.project_root, self.session_id)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as handle:
                with contextlib.suppress(OSError):
                    os.chmod(self.path, 0o600)
                fcntl.flock(handle, fcntl.LOCK_EX)
                try:
                    handle.write(entry.as_json() + "\n")
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)
        except OSError as exc:
            logger.debug("Failed to write prompt history: %s", exc)

    def _entries_newest_first(self) -> Iterator[LogEntry]:
        """
        `makeLogEntryReader`: the log, newest first. A missing log is empty; so is an unreadable one
        (upstream rethrows it, but a broken history must not take the prompt down with it).
        """
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in reversed(lines):
            entry = _parse(line) if line.strip() else None
            if entry is not None:
                yield entry

    def get_history(self) -> list[str]:
        """`getHistory`: this project's prompts, the current session's first, newest first."""
        current: list[str] = []
        others: list[str] = []
        for entry in self._entries_newest_first():
            if entry.project != self.project_root:
                continue
            (current if entry.session_id == self.session_id else others).append(entry.display)
            if len(current) + len(others) >= MAX_HISTORY_ITEMS:
                break
        return [*current, *others][:MAX_HISTORY_ITEMS]
