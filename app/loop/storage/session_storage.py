"""
The transcript write path (Phase 6).

Ported from `reference/claude-code/src/utils/sessionStorage.ts`:

* `recordTranscript` — the growing-array caller re-sends the whole history every time.
  Already-recorded messages are skipped, and are tracked as the parent of the new ones
  **only while they form a prefix**: after a compaction the new boundary and summary come
  first and the kept messages after, so the kept ones do not re-parent the chain.
* `recordSidechainTranscript` — a worker's messages go to its own file, never deduped
  against the main file (a fork-inherited parent message shares its uuid with the main
  transcript and must still be written there).
* `Project.insertMessageChain` — the `parentUuid` cursor (every recorded message is a chain
  participant: `isChainParticipant` excludes only progress, which this port never records); a compact boundary gets
  `parentUuid: null` plus `logicalParentUuid`; a tool result chains to the assistant message
  that called it (`sourceToolAssistantUUID`), which per-block emission makes a different
  message for each parallel call.
* `materializeSessionFile` — nothing is written until the first user or assistant message;
  entries before it are buffered and flushed in order.
* `appendEntry` / `appendToFile` — append only, never rewrite; files `0o600`, directories
  `0o700`.
* `shouldSkipPersistence` — a `persist` flag (`--no-session-persistence`) and
  `TDDAGENTS_SKIP_PROMPT_HISTORY` (`CLAUDE_CODE_SKIP_PROMPT_HISTORY`).

Writes are synchronous: upstream batches them on a timer and flushes before the result
message, and a synchronous append gives the same on-disk order without a flush to forget.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid as uuid_module
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from langchain_core.messages import ToolMessage

from app.loop.messages import Message, tool_calls_in
from app.loop.storage.entries import (
    EntryStamp,
    build_entry,
    ensure_message_uuid,
    is_loggable_message,
    iso_timestamp,
)
from app.loop.storage.paths import get_agent_transcript_path, get_transcript_path

#: `CLAUDE_CODE_SKIP_PROMPT_HISTORY`, under this project's prefix.
SKIP_PROMPT_HISTORY_ENV = "TDDAGENTS_SKIP_PROMPT_HISTORY"

_TRUTHY = ("1", "true", "yes", "on")

#: The TDDAgents checkout, whose commit stands in for upstream's build-time `VERSION`.
_APP_ROOT = Path(__file__).resolve().parents[3]

_RESOLVE: Any = object()


def _git(args: list[str], cwd: str | Path) -> str | None:
    """One `git` query, or `None` when it fails (no repository, no git)."""
    try:
        done = subprocess.run(["git", *args], check=False, cwd=str(cwd), capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError):
        return None
    out = done.stdout.strip()
    return out if done.returncode == 0 and out else None


def _new_uuid() -> str:
    return str(uuid_module.uuid4())


class TranscriptStore:
    """
    One run's transcript (upstream's `Project`, scoped to a single session).

    `record_transcript` and `record_sidechain_transcript` return the uuid of the last
    message they chained, which is what a caller continues the chain from.
    """

    def __init__(
        self,
        session_id: str,
        cwd: str | Path,
        *,
        uuid: Callable[[], str] = _new_uuid,
        now: Callable[[], float] = time.time,
        persist: bool = True,
        version: str | None = None,
        git_branch: str | None = _RESOLVE,
    ) -> None:
        self.session_id = session_id
        self.cwd = str(cwd)
        self.path = get_transcript_path(session_id, cwd)
        self._uuid = uuid
        self._now = now
        self._persist = persist
        self._version = version
        self._git_branch = git_branch
        self._stamp: EntryStamp | None = None
        self._materialized = False
        self._pending: list[tuple[Path, dict[str, Any]]] = []
        self._recorded = self._uuids_on_disk(self.path)
        self._tool_sources: dict[str, str] = {}

    def agent_transcript_path(self, agent_id: str) -> Path:
        return get_agent_transcript_path(self.session_id, agent_id, self.cwd)

    def record_transcript(self, messages: Sequence[Message]) -> str | None:
        """`recordTranscript`."""
        cleaned = [m for m in messages if is_loggable_message(m)]
        new: list[Message] = []
        starting_parent: str | None = None
        seen_new = False
        for message in cleaned:
            uid = ensure_message_uuid(message, self._uuid)
            if uid in self._recorded:
                if not seen_new:
                    starting_parent = uid
                self._remember_tool_calls(message, uid)
            else:
                new.append(message)
                seen_new = True
        last = self._insert_message_chain(new, False, None, starting_parent)
        return last if last is not None else starting_parent

    def record_sidechain_transcript(
        self, messages: Sequence[Message], agent_id: str, starting_parent_uuid: str | None = None
    ) -> str | None:
        """`recordSidechainTranscript`."""
        cleaned = [m for m in messages if is_loggable_message(m)]
        for message in cleaned:
            ensure_message_uuid(message, self._uuid)
        return self._insert_message_chain(cleaned, True, agent_id, starting_parent_uuid)

    def _insert_message_chain(
        self, messages: Sequence[Message], is_sidechain: bool, agent_id: str | None, starting_parent: str | None
    ) -> str | None:
        """`Project.insertMessageChain`."""
        if not messages:
            return None
        parent = starting_parent
        if not self._materialized and any(getattr(m, "type", "") in ("human", "tool", "ai") for m in messages):
            self._materialize()
        # Only a sidechain names an agent, so the agent id alone picks the file.
        target = self.agent_transcript_path(agent_id) if agent_id is not None else self.path
        for message in messages:
            source = self._tool_sources.get(message.tool_call_id) if isinstance(message, ToolMessage) else None
            entry = build_entry(
                message,
                source if source is not None else parent,
                is_sidechain,
                self._session_stamp(),
                iso_timestamp(self._now()),
                agent_id=agent_id,
                source_tool_assistant_uuid=source,
            )
            self._append_entry(target, entry, is_sidechain)
            # Both callers assigned every uuid before chaining, so the entry carries it.
            uid: str = entry["uuid"]
            self._remember_tool_calls(message, uid)
            parent = uid
        return parent

    def _remember_tool_calls(self, message: Message, uid: str) -> None:
        for call in tool_calls_in(message):
            call_id = call.get("id")
            if call_id:
                self._tool_sources[call_id] = uid

    def _materialize(self) -> None:
        """`materializeSessionFile`: flush what was buffered, in order."""
        self._materialized = True
        buffered, self._pending = self._pending, []
        for target, entry in buffered:
            self._write(target, entry)

    def _append_entry(self, target: Path, entry: dict[str, Any], is_sidechain: bool) -> None:
        """`appendEntry`: main-file dedup only; sidechain writes bypass it."""
        if self._skip_persistence():
            return
        if not is_sidechain:
            self._recorded.add(entry["uuid"])
        if not self._materialized:
            self._pending.append((target, entry))
            return
        self._write(target, entry)

    def _skip_persistence(self) -> bool:
        """`shouldSkipPersistence`."""
        return not self._persist or os.environ.get(SKIP_PROMPT_HISTORY_ENV, "").strip().lower() in _TRUTHY

    @staticmethod
    def _write(target: Path, entry: dict[str, Any]) -> None:
        """`appendToFile`."""
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")

    def _session_stamp(self) -> EntryStamp:
        if self._stamp is None:
            version = self._version if self._version is not None else (
                _git(["rev-parse", "--short", "HEAD"], _APP_ROOT) or "unknown"
            )
            branch = self._git_branch
            if branch is _RESOLVE:
                branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], self.cwd)
            self._stamp = EntryStamp(session_id=self.session_id, cwd=self.cwd, version=version, git_branch=branch)
        return self._stamp

    @staticmethod
    def _uuids_on_disk(path: Path) -> set[str]:
        """`getSessionMessages`: the uuids a resumed session has already written."""
        found: set[str] = set()
        if not path.is_file():
            return found
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                uid = json.loads(line).get("uuid")
            except (ValueError, AttributeError):
                continue
            if isinstance(uid, str):
                found.add(uid)
        return found


__all__ = ["SKIP_PROMPT_HISTORY_ENV", "TranscriptStore"]
