"""
The transcript entry format (Phase 6).

Ported from `reference/claude-code/src/utils/sessionStorage.ts` -> `insertMessageChain`, which
writes one `TranscriptMessage` per line: the message itself, spread, plus the chain links
(`parentUuid`, `logicalParentUuid` on a compact boundary, `isSidechain`, `agentId`) and the
session stamp (`userType`, `cwd`, `sessionId`, `version`, `gitBranch`) written *after* the
spread so a resumed message is re-stamped.

The port's one structural difference: upstream's messages are plain objects whose fields can
be spread into the entry, ours are LangChain messages. The message travels whole under
`message` in LangChain's own dict form (`message_to_dict`), which round-trips losslessly; the
fields readers branch on (`subtype`, `isMeta`, `isCompactSummary`, `isApiErrorMessage`,
`sourceToolAssistantUUID`) are mirrored at the top level, where upstream has them.

A message's `uuid` lives in `additional_kwargs`, beside the other loop flags (`is_meta`,
`compact_boundary`). It is assigned once, the first time the message is recorded, and is
what `recordTranscript`'s dedup and the `parentUuid` chain are keyed on.

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

Reading a transcript back (Phase 6).

Ported from `reference/claude-code/src/utils/sessionStorage.ts` -> `loadTranscriptFile`,
`buildConversationChain`, `recoverOrphanedParallelToolResults`, `findLatestMessage` and
`getAgentTranscript`, and `reference/claude-code/src/utils/conversationRecovery.ts` ->
`loadMessagesFromJsonlPath`.

A transcript is an append-only JSONL file whose message entries form a tree through
`parentUuid`. The conversation is the path from the newest non-sidechain leaf back to a
root; a compact boundary is a root (its `parentUuid` is null), so resuming after a compaction
loads only the post-boundary history, exactly as upstream.

Leaves follow the external build: `tengu_pebble_leaf_prune` is a GrowthBook flag that
defaults off, so a terminal entry walks back to its nearest user/assistant ancestor
unconditionally.

`recoverOrphanedParallelToolResults` matters here for the same reason it does upstream: the
model emits one assistant message per content block (`services/api/claude.py`), so parallel tool
calls become sibling assistant messages sharing one provider id, and each tool result is
chained to its own assistant. The single-parent walk keeps one branch; the post-pass splices
the others back after the group's last on-chain member.

Not ported: the >5 MB pre-boundary skip and chain-walk-before-parse (allocation optimizations
with the same result), legacy progress bridging, preserved-segment relinks and snip removals
(no subsystems here), and the session metadata entries (titles, tags, PR links, file-history
and attribution snapshots, content replacements, context-collapse records).
"""

from __future__ import annotations

import json
import logging
import math
import os
import subprocess
import time
import uuid as uuid_module
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import ToolMessage, message_to_dict, messages_from_dict

from app.services.compact.compact import BOUNDARY_KEY, is_compact_boundary_message
from app.utils.messages import ATTACHMENT_KEY, Message, tool_calls_in
from app.utils.session_storage_portable import (
    get_agent_transcript_path,
    get_transcript_path,
)

UUID_KEY = "uuid"

#: `getUserType()` in the external build.
USER_TYPE = "external"

#: `isLoggableMessage`'s opt-in env for hook additional context.
SAVE_HOOK_ADDITIONAL_CONTEXT_ENV = "TDDAGENTS_SAVE_HOOK_ADDITIONAL_CONTEXT"

_TRUTHY = ("1", "true", "yes", "on")

_ENTRY_TYPES = {"human": "user", "tool": "user", "ai": "assistant", "system": "system"}


@dataclass(frozen=True)
class EntryStamp:
    """The session fields `insertMessageChain` stamps on every entry."""

    session_id: str
    cwd: str
    version: str
    git_branch: str | None


def message_uuid(message: Any) -> str | None:
    kwargs = getattr(message, "additional_kwargs", None)
    value = kwargs.get(UUID_KEY) if isinstance(kwargs, Mapping) else None
    return value if isinstance(value, str) else None


def ensure_message_uuid(message: Message, new_uuid: Callable[[], str]) -> str:
    """The message's uuid, assigned on first sight and never changed afterwards."""
    existing = message_uuid(message)
    if existing is not None:
        return existing
    assigned = new_uuid()
    message.additional_kwargs[UUID_KEY] = assigned
    return assigned


def entry_type_of(message: Any) -> str | None:
    """Upstream's `type`: a tool result is a `user` message, as on the API."""
    return _ENTRY_TYPES.get(getattr(message, "type", ""))


def is_loggable_message(message: Any) -> bool:
    """
    `isLoggableMessage` for the external build: attachments are not persisted, except hook
    additional context when `SAVE_HOOK_ADDITIONAL_CONTEXT_ENV` opts in.
    """
    kwargs = getattr(message, "additional_kwargs", None)
    attachment = kwargs.get(ATTACHMENT_KEY) if isinstance(kwargs, Mapping) else None
    if not isinstance(attachment, Mapping):
        return True
    opted_in = os.environ.get(SAVE_HOOK_ADDITIONAL_CONTEXT_ENV, "").strip().lower() in _TRUTHY
    return attachment.get("type") == "hook_additional_context" and opted_in


def iso_timestamp(epoch_seconds: float) -> str:
    """`new Date().toISOString()`: UTC, millisecond precision, `Z` suffix."""
    moment = datetime.fromtimestamp(epoch_seconds, tz=UTC)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def build_entry(
    message: Message,
    parent_uuid: str | None,
    is_sidechain: bool,
    stamp: EntryStamp,
    timestamp: str,
    *,
    agent_id: str | None = None,
    source_tool_assistant_uuid: str | None = None,
) -> dict[str, Any]:
    """One `TranscriptMessage`. The message must already carry its uuid."""
    kwargs = message.additional_kwargs
    boundary = is_compact_boundary_message(message)
    entry: dict[str, Any] = {
        "parentUuid": None if boundary else parent_uuid,
        "isSidechain": is_sidechain,
    }
    if boundary:
        entry["logicalParentUuid"] = parent_uuid
    if agent_id is not None:
        entry["agentId"] = agent_id
    entry["type"] = entry_type_of(message)
    if boundary:
        entry["subtype"] = "compact_boundary"
        entry["compactMetadata"] = dict(kwargs[BOUNDARY_KEY])
    for flag, key in (("isMeta", "is_meta"), ("isCompactSummary", "is_compact_summary"),
                      ("isApiErrorMessage", "is_api_error_message")):
        if kwargs.get(key) is True:
            entry[flag] = True
    if source_tool_assistant_uuid is not None:
        entry["sourceToolAssistantUUID"] = source_tool_assistant_uuid
    entry["message"] = message_to_dict(message)
    entry["uuid"] = message_uuid(message)
    entry["timestamp"] = timestamp
    entry["userType"] = USER_TYPE
    entry["cwd"] = stamp.cwd
    entry["sessionId"] = stamp.session_id
    entry["version"] = stamp.version
    if stamp.git_branch is not None:
        entry["gitBranch"] = stamp.git_branch
    return entry


def entry_to_message(entry: Mapping[str, Any]) -> Message | None:
    """The message an entry carries, uuid restored; `None` for a non-message entry."""
    payload = entry.get("message")
    if not isinstance(payload, Mapping):
        return None
    try:
        (message,) = messages_from_dict([dict(payload)])
    except (KeyError, TypeError, ValueError):
        return None
    uuid = entry.get("uuid")
    if isinstance(uuid, str):
        message.additional_kwargs[UUID_KEY] = uuid
    return message


SKIP_PROMPT_HISTORY_ENV = "TDDAGENTS_SKIP_PROMPT_HISTORY"

_TRUTHY = ("1", "true", "yes", "on")

#: The TDDAgents checkout, whose commit stands in for upstream's build-time `VERSION`.
_APP_ROOT = Path(__file__).resolve().parents[2]

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

    def agent_metadata_path(self, agent_id: str) -> Path:
        """`getAgentMetadataPath`: the agent transcript's `.jsonl` swapped for `.meta.json`."""
        return self.agent_transcript_path(agent_id).with_suffix(".meta.json")

    def write_agent_metadata(self, agent_id: str, metadata: Mapping[str, Any]) -> None:
        """`writeAgentMetadata`: the agent type (and description) a resume routes on."""
        path = self.agent_metadata_path(agent_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(metadata), separators=(",", ":"), ensure_ascii=False), encoding="utf-8")

    def read_agent_metadata(self, agent_id: str) -> dict[str, Any] | None:
        """`readAgentMetadata`: `None` when the sidecar is absent."""
        try:
            raw = self.agent_metadata_path(agent_id).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        loaded: dict[str, Any] = json.loads(raw)
        return loaded

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


logger = logging.getLogger(__name__)

Entry = dict[str, Any]

#: `isTranscriptMessage`: the entry types that carry a message.
TRANSCRIPT_MESSAGE_TYPES = frozenset({"user", "assistant", "attachment", "system"})


@dataclass(frozen=True)
class LoadedTranscript:
    """What `loadTranscriptFile` returns that this port uses, plus the main chain."""

    messages: dict[str, Entry]
    leaf_uuids: frozenset[str]
    chain: list[Entry]


def _parse_time(timestamp: Any) -> float | None:
    """`Date.parse`, in seconds (only the order matters); `None` for upstream's `NaN`."""
    if not isinstance(timestamp, str):
        return None
    try:
        return datetime.fromisoformat(timestamp).timestamp()
    except ValueError:
        return None


def _find_latest(entries: Iterable[Entry], predicate: Callable[[Entry], bool]) -> Entry | None:
    """`findLatestMessage`: the strictly newest match; ties keep the first, `NaN` never wins."""
    latest: Entry | None = None
    max_time = -math.inf
    for entry in entries:
        if not predicate(entry):
            continue
        moment = _parse_time(entry.get("timestamp"))
        if moment is not None and moment > max_time:
            max_time, latest = moment, entry
    return latest


def _read_entries(path: str | Path) -> dict[str, Entry]:
    messages: dict[str, Entry] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return messages
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if (
            isinstance(entry, dict)
            and entry.get("type") in TRANSCRIPT_MESSAGE_TYPES
            and isinstance(entry.get("uuid"), str)
        ):
            messages[entry["uuid"]] = entry
    return messages


def _leaf_uuids(messages: Mapping[str, Entry]) -> frozenset[str]:
    """Each terminal entry's nearest user/assistant ancestor (itself included)."""
    parents = {e.get("parentUuid") for e in messages.values()}
    leaves: set[str] = set()
    for terminal in (e for e in messages.values() if e["uuid"] not in parents):
        seen: set[str] = set()
        current: Entry | None = terminal
        while current is not None and current["uuid"] not in seen:
            seen.add(current["uuid"])
            if current["type"] in ("user", "assistant"):
                leaves.add(current["uuid"])
                break
            parent = current.get("parentUuid")
            current = messages.get(parent) if parent else None
    return frozenset(leaves)


def _main_leaf(messages: Mapping[str, Entry], leaves: frozenset[str]) -> Entry | None:
    """`loadMessagesFromJsonlPath`'s tip: the newest non-sidechain leaf."""
    return _find_latest(messages.values(), lambda e: not e.get("isSidechain") and e["uuid"] in leaves)


def load_transcript_file(path: str | Path) -> LoadedTranscript:
    """`loadTranscriptFile`: entries by uuid, the chain tips, and the main conversation."""
    messages = _read_entries(path)
    leaves = _leaf_uuids(messages)
    tip = _main_leaf(messages, leaves)
    chain = build_conversation_chain(messages, tip) if tip is not None else []
    return LoadedTranscript(messages=messages, leaf_uuids=leaves, chain=chain)


def build_conversation_chain(messages: Mapping[str, Entry], leaf: Entry) -> list[Entry]:
    """`buildConversationChain`: root to leaf, stopping at a cycle with what it has."""
    transcript: list[Entry] = []
    seen: set[str] = set()
    current: Entry | None = leaf
    while current is not None:
        if current["uuid"] in seen:
            logger.error(
                "Cycle detected in parentUuid chain at message %s. Returning partial transcript.",
                current["uuid"],
            )
            break
        seen.add(current["uuid"])
        transcript.append(current)
        parent = current.get("parentUuid")
        current = messages.get(parent) if parent else None
    transcript.reverse()
    return _recover_orphaned_parallel_tool_results(messages, transcript, seen)


def _provider_id(entry: Entry) -> str | None:
    """The assistant's `message.id` — shared by every block of one API response."""
    data = (entry.get("message") or {}).get("data") or {}
    value = data.get("id")
    return value if isinstance(value, str) else None


def _is_tool_result(entry: Entry) -> bool:
    return entry.get("type") == "user" and (entry.get("message") or {}).get("type") == "tool"


def _recover_orphaned_parallel_tool_results(
    messages: Mapping[str, Entry], chain: list[Entry], seen: set[str]
) -> list[Entry]:
    """`recoverOrphanedParallelToolResults`."""
    chain_assistants = [e for e in chain if e["type"] == "assistant"]
    if not chain_assistants:
        return chain

    anchor_by_id: dict[str, Entry] = {}
    for assistant in chain_assistants:
        provider_id = _provider_id(assistant)
        if provider_id:
            anchor_by_id[provider_id] = assistant

    siblings_by_id: dict[str, list[Entry]] = {}
    results_by_assistant: dict[str, list[Entry]] = {}
    for entry in messages.values():
        provider_id = _provider_id(entry) if entry["type"] == "assistant" else None
        if provider_id:
            siblings_by_id.setdefault(provider_id, []).append(entry)
        elif _is_tool_result(entry) and entry.get("parentUuid"):
            results_by_assistant.setdefault(entry["parentUuid"], []).append(entry)

    # A group is visited once per on-chain member; after the first visit everything it
    # recovered is in `seen`, so a later visit finds nothing orphaned and adds nothing.
    inserts: dict[str, list[Entry]] = {}
    for assistant in chain_assistants:
        provider_id = _provider_id(assistant)
        if not provider_id:
            continue
        group = siblings_by_id[provider_id]
        orphaned_siblings = [s for s in group if s["uuid"] not in seen]
        orphaned_results = [
            r for member in group for r in results_by_assistant.get(member["uuid"], []) if r["uuid"] not in seen
        ]
        if not orphaned_siblings and not orphaned_results:
            continue
        # Stable sorts on the ISO timestamp: content-block order, then write order on ties.
        orphaned_siblings.sort(key=lambda e: str(e.get("timestamp")))
        orphaned_results.sort(key=lambda e: str(e.get("timestamp")))
        recovered = [*orphaned_siblings, *orphaned_results]
        seen.update(r["uuid"] for r in recovered)
        inserts[anchor_by_id[provider_id]["uuid"]] = recovered

    if not inserts:
        return chain
    result: list[Entry] = []
    for entry in chain:
        result.append(entry)
        result.extend(inserts.get(entry["uuid"], []))
    return result


def _to_messages(chain: Iterable[Entry]) -> list[Message]:
    return [m for m in (entry_to_message(e) for e in chain) if m is not None]


def load_messages_from_jsonl_path(path: str | Path) -> list[Message]:
    """`loadMessagesFromJsonlPath`: the main conversation, deserialized, uuids kept."""
    return _to_messages(load_transcript_file(path).chain)


def get_agent_transcript(path: str | Path, agent_id: str | None = None) -> list[Message]:
    """
    `getAgentTranscript`: a worker's sidechain, read from its own file. Upstream returns
    `null` when there is none; here that is an empty list. Without an `agent_id`, the agent
    is the one owning the newest sidechain leaf.
    """
    messages = _read_entries(path)
    agent_messages = [
        e for e in messages.values()
        if e.get("isSidechain") and (agent_id is None or e.get("agentId") == agent_id)
    ]
    parents = {e.get("parentUuid") for e in agent_messages}
    leaf = _find_latest(agent_messages, lambda e: e["uuid"] not in parents)
    if leaf is None:
        return []
    owner = leaf.get("agentId")
    return _to_messages(e for e in build_conversation_chain(messages, leaf) if e.get("agentId") == owner)


__all__ = [
    "SAVE_HOOK_ADDITIONAL_CONTEXT_ENV",
    "SKIP_PROMPT_HISTORY_ENV",
    "TRANSCRIPT_MESSAGE_TYPES",
    "USER_TYPE",
    "UUID_KEY",
    "EntryStamp",
    "LoadedTranscript",
    "TranscriptStore",
    "build_conversation_chain",
    "build_entry",
    "ensure_message_uuid",
    "entry_to_message",
    "entry_type_of",
    "get_agent_transcript",
    "is_loggable_message",
    "iso_timestamp",
    "load_messages_from_jsonl_path",
    "load_transcript_file",
    "message_uuid",
]
