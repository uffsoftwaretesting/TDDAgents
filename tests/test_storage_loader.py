"""Phase 6: reading a transcript back (`sessionStorage.ts` -> `loadTranscriptFile`,
`buildConversationChain`, `recoverOrphanedParallelToolResults`, `getAgentTranscript`;
`conversationRecovery.ts` -> `loadMessagesFromJsonlPath`)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.loop.compact.compact import create_compact_boundary_message
from app.loop.storage.entries import EntryStamp, build_entry, message_uuid
from app.loop.storage.loader import (
    LoadedTranscript,
    build_conversation_chain,
    get_agent_transcript,
    load_messages_from_jsonl_path,
    load_transcript_file,
)

STAMP = EntryStamp(session_id="s1", cwd="/w", version="v", git_branch=None)


def entry(
    message: Any,
    uuid: str,
    parent: str | None,
    ts: str,
    *,
    sidechain: bool = False,
    agent_id: str | None = None,
) -> dict[str, Any]:
    message.additional_kwargs["uuid"] = uuid
    return build_entry(message, parent, sidechain, STAMP, ts, agent_id=agent_id)


def write(path: Path, *entries: dict[str, Any] | str) -> Path:
    lines = [e if isinstance(e, str) else json.dumps(e) for e in entries]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def ts(n: int) -> str:
    return f"2026-10-22T00:00:{n:02d}.000Z"


def test_a_linear_chain_loads_in_order(tmp_path):
    path = write(
        tmp_path / "s.jsonl",
        entry(HumanMessage(content="task"), "u1", None, ts(1)),
        entry(AIMessage(content="ok"), "u2", "u1", ts(2)),
    )
    loaded = load_transcript_file(path)
    assert isinstance(loaded, LoadedTranscript)
    assert set(loaded.messages) == {"u1", "u2"}
    assert loaded.leaf_uuids == frozenset({"u2"})
    assert [e["uuid"] for e in loaded.chain] == ["u1", "u2"]
    messages = load_messages_from_jsonl_path(path)
    assert [m.content for m in messages] == ["task", "ok"]
    assert [message_uuid(m) for m in messages] == ["u1", "u2"]


def test_malformed_lines_and_non_message_entries_are_skipped(tmp_path):
    path = write(
        tmp_path / "s.jsonl",
        "{not json",
        json.dumps({"type": "summary", "summary": "x", "leafUuid": "u1"}),
        json.dumps({"type": "user"}),  # no uuid
        json.dumps(["a", "list"]),
        "",
        entry(HumanMessage(content="task"), "u1", None, ts(1)),
    )
    loaded = load_transcript_file(path)
    assert list(loaded.messages) == ["u1"]
    assert [m.content for m in load_messages_from_jsonl_path(path)] == ["task"]


def test_a_missing_file_is_an_empty_transcript(tmp_path):
    loaded = load_transcript_file(tmp_path / "absent.jsonl")
    assert (loaded.messages, loaded.leaf_uuids, loaded.chain) == ({}, frozenset(), [])
    assert load_messages_from_jsonl_path(tmp_path / "absent.jsonl") == []


def test_a_system_tip_anchors_on_its_nearest_user_or_assistant_ancestor(tmp_path):
    """Only user/assistant messages are leaves; a trailing system record walks back."""
    path = write(
        tmp_path / "s.jsonl",
        entry(HumanMessage(content="task"), "u1", None, ts(1)),
        entry(AIMessage(content="ok"), "u2", "u1", ts(2)),
        entry(SystemMessage(content="note"), "u3", "u2", ts(3)),
    )
    loaded = load_transcript_file(path)
    assert loaded.leaf_uuids == frozenset({"u2"})
    assert [e["uuid"] for e in loaded.chain] == ["u1", "u2"]


def test_the_newest_non_sidechain_leaf_is_the_main_conversation(tmp_path):
    path = write(
        tmp_path / "s.jsonl",
        entry(HumanMessage(content="task"), "u1", None, ts(1)),
        entry(AIMessage(content="old branch"), "u2", "u1", ts(2)),
        entry(AIMessage(content="new branch"), "u3", "u1", ts(3)),
        entry(AIMessage(content="worker"), "w1", None, ts(9), sidechain=True, agent_id="a"),
    )
    loaded = load_transcript_file(path)
    assert loaded.leaf_uuids == frozenset({"u2", "u3", "w1"})
    assert [m.content for m in load_messages_from_jsonl_path(path)] == ["task", "new branch"]


def test_ties_keep_the_first_leaf_written(tmp_path):
    path = write(
        tmp_path / "s.jsonl",
        entry(HumanMessage(content="task"), "u1", None, ts(1)),
        entry(AIMessage(content="first"), "u2", "u1", ts(2)),
        entry(AIMessage(content="second"), "u3", "u1", ts(2)),
    )
    assert [m.content for m in load_messages_from_jsonl_path(path)][-1] == "first"


def test_an_unparseable_timestamp_never_wins(tmp_path):
    path = write(
        tmp_path / "s.jsonl",
        entry(HumanMessage(content="task"), "u1", None, ts(1)),
        entry(AIMessage(content="good"), "u2", "u1", ts(2)),
        entry(AIMessage(content="bad"), "u3", "u1", "not a time"),
    )
    assert [m.content for m in load_messages_from_jsonl_path(path)][-1] == "good"


def test_a_compact_boundary_starts_a_new_chain(tmp_path):
    boundary = create_compact_boundary_message("auto", 10)
    path = write(
        tmp_path / "s.jsonl",
        entry(HumanMessage(content="old"), "u1", None, ts(1)),
        entry(boundary, "b1", "u1", ts(2)),
        entry(HumanMessage(content="summary"), "u2", "b1", ts(3)),
    )
    messages = load_messages_from_jsonl_path(path)
    assert [m.content for m in messages][1:] == ["summary"]
    assert message_uuid(messages[0]) == "b1"


def test_a_parent_cycle_returns_the_partial_chain(caplog):
    a = entry(HumanMessage(content="a"), "a", "b", ts(1))
    b = entry(AIMessage(content="b"), "b", "a", ts(2))
    with caplog.at_level(logging.ERROR, logger="app.loop.storage.loader"):
        chain = build_conversation_chain({"a": a, "b": b}, b)
    assert [e["uuid"] for e in chain] == ["a", "b"]
    assert "Cycle detected in parentUuid chain at message b" in caplog.text


def test_a_cyclic_file_still_loads(tmp_path):
    path = write(
        tmp_path / "s.jsonl",
        entry(HumanMessage(content="a"), "a", "b", ts(1)),
        entry(AIMessage(content="b"), "b", "a", ts(2)),
        entry(HumanMessage(content="c"), "c", None, ts(3)),
    )
    loaded = load_transcript_file(path)
    assert loaded.leaf_uuids == frozenset({"c"})


def test_a_missing_parent_ends_the_chain():
    tip = entry(AIMessage(content="b"), "b", "gone", ts(2))
    assert [e["uuid"] for e in build_conversation_chain({"b": tip}, tip)] == ["b"]


def _parallel_tool_use(tmp_path: Path, *, later: list[dict[str, Any]] | None = None) -> Path:
    """
    What the recorder writes for one answer with two parallel tool calls: one assistant
    message per content block (same provider id), each tool result chained to its own
    assistant (`sourceToolAssistantUUID`), and the next message chained sequentially.
    """
    a1 = AIMessage(content="", id="msg-1", tool_calls=[{"name": "ReadFile", "args": {}, "id": "c1"}])
    a2 = AIMessage(content="", id="msg-1", tool_calls=[{"name": "ReadFile", "args": {}, "id": "c2"}])
    return write(
        tmp_path / "s.jsonl",
        entry(HumanMessage(content="task"), "u1", None, ts(1)),
        entry(a1, "a1", "u1", ts(2)),
        entry(a2, "a2", "a1", ts(3)),
        entry(ToolMessage(content="r1", tool_call_id="c1"), "t1", "a1", ts(4)),
        entry(ToolMessage(content="r2", tool_call_id="c2"), "t2", "a2", ts(5)),
        *(later or []),
    )


def test_orphaned_parallel_tool_results_are_recovered_after_their_group(tmp_path):
    later = [entry(AIMessage(content="done", id="msg-2"), "n1", "t2", ts(6))]
    path = _parallel_tool_use(tmp_path, later=later)
    uuids = [message_uuid(m) for m in load_messages_from_jsonl_path(path)]
    assert uuids == ["u1", "a1", "a2", "t1", "t2", "n1"]


def test_an_orphaned_sibling_assistant_is_recovered(tmp_path):
    """The walk follows one branch: a2's sibling a3 (same id, off-chain) and its result
    come back right after the group's last on-chain member."""
    a1 = AIMessage(content="", id="msg-1", tool_calls=[{"name": "ReadFile", "args": {}, "id": "c1"}])
    a3 = AIMessage(content="", id="msg-1", tool_calls=[{"name": "ReadFile", "args": {}, "id": "c3"}])
    path = write(
        tmp_path / "s.jsonl",
        entry(HumanMessage(content="task"), "u1", None, ts(1)),
        entry(a1, "a1", "u1", ts(2)),
        entry(a3, "a3", "u1", ts(4)),
        entry(ToolMessage(content="r3", tool_call_id="c3"), "t3", "a3", ts(5)),
        entry(ToolMessage(content="r1", tool_call_id="c1"), "t1", "a1", ts(3)),
        entry(AIMessage(content="done", id="msg-2"), "n1", "t1", ts(6)),
    )
    uuids = [message_uuid(m) for m in load_messages_from_jsonl_path(path)]
    assert uuids == ["u1", "a1", "a3", "t3", "t1", "n1"]


def test_recovered_entries_are_ordered_by_timestamp():
    a1 = entry(AIMessage(content="", id="m", tool_calls=[{"name": "X", "args": {}, "id": "c1"}]), "a1", None, ts(1))
    t_late = entry(ToolMessage(content="late", tool_call_id="c1"), "tl", "a1", ts(9))
    t_early = entry(ToolMessage(content="early", tool_call_id="c1"), "te", "a1", ts(5))
    by_uuid = {"a1": a1, "tl": t_late, "te": t_early}
    assert [e["uuid"] for e in build_conversation_chain(by_uuid, a1)] == ["a1", "te", "tl"]


def test_assistants_without_a_provider_id_are_not_grouped():
    a1 = entry(AIMessage(content="", tool_calls=[{"name": "X", "args": {}, "id": "c1"}]), "a1", None, ts(1))
    t1 = entry(ToolMessage(content="r", tool_call_id="c1"), "t1", "a1", ts(2))
    assert [e["uuid"] for e in build_conversation_chain({"a1": a1, "t1": t1}, a1)] == ["a1"]


def test_a_chain_without_assistants_is_returned_as_is():
    u1 = entry(HumanMessage(content="x"), "u1", None, ts(1))
    assert build_conversation_chain({"u1": u1}, u1) == [u1]


def test_a_plain_user_message_off_chain_is_not_recovered():
    a1 = entry(AIMessage(content="hi", id="m"), "a1", None, ts(1))
    u_off = entry(HumanMessage(content="off"), "uo", "a1", ts(2))
    assert [e["uuid"] for e in build_conversation_chain({"a1": a1, "uo": u_off}, a1)] == ["a1"]


def test_an_agent_transcript_is_its_own_sidechain(tmp_path):
    path = write(
        tmp_path / "agent-a1.jsonl",
        entry(HumanMessage(content="do it"), "w1", None, ts(1), sidechain=True, agent_id="a1"),
        entry(AIMessage(content="done"), "w2", "w1", ts(2), sidechain=True, agent_id="a1"),
        entry(AIMessage(content="other"), "x1", None, ts(3), sidechain=True, agent_id="a2"),
        entry(AIMessage(content="main"), "m1", None, ts(4)),
    )
    assert [m.content for m in get_agent_transcript(path, "a1")] == ["do it", "done"]
    assert [m.content for m in get_agent_transcript(path, "a2")] == ["other"]
    assert get_agent_transcript(path, "nobody") == []
    assert get_agent_transcript(tmp_path / "absent.jsonl", "a1") == []


def test_an_agent_transcript_without_an_id_takes_the_newest_sidechain(tmp_path):
    path = write(
        tmp_path / "agent-a1.jsonl",
        entry(HumanMessage(content="do it"), "w1", None, ts(1), sidechain=True, agent_id="a1"),
        entry(AIMessage(content="done"), "w2", "w1", ts(2), sidechain=True, agent_id="a1"),
        entry(AIMessage(content="main"), "m1", None, ts(4)),
    )
    assert [m.content for m in get_agent_transcript(path)] == ["do it", "done"]


def test_an_agent_transcript_keeps_only_that_agents_entries(tmp_path):
    """`agentTranscript = transcript.filter(msg => msg.agentId === agentId)`."""
    path = write(
        tmp_path / "agent-a1.jsonl",
        entry(HumanMessage(content="parent"), "p1", None, ts(1), sidechain=True, agent_id="other"),
        entry(AIMessage(content="mine"), "w1", "p1", ts(2), sidechain=True, agent_id="a1"),
    )
    assert [m.content for m in get_agent_transcript(path, "a1")] == ["mine"]
