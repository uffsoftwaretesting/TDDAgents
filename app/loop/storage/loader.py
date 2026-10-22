"""
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
model emits one assistant message per content block (`streaming/blocks.py`), so parallel tool
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
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.loop.messages import Message
from app.loop.storage.entries import entry_to_message

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
    "TRANSCRIPT_MESSAGE_TYPES",
    "LoadedTranscript",
    "build_conversation_chain",
    "get_agent_transcript",
    "load_messages_from_jsonl_path",
    "load_transcript_file",
]
