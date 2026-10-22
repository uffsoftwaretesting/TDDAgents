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


# ── Phase 6 gate: mutation triage ───────────────────────────────────────────


def test_a_non_message_terminal_whose_parent_is_missing_is_no_leaf(tmp_path):
    """The ancestor walk stops at a dangling parent instead of dereferencing it."""
    path = write(
        tmp_path / "s.jsonl",
        entry(HumanMessage(content="task"), "u1", None, ts(1)),
        entry(SystemMessage(content="note"), "s1", "gone", ts(2)),
    )
    assert load_transcript_file(path).leaf_uuids == frozenset({"u1"})


def test_a_cycle_behind_a_terminal_ends_the_ancestor_walk():
    from app.loop.storage.loader import _leaf_uuids

    t = entry(SystemMessage(content="t"), "t", "a", ts(1))
    a = entry(SystemMessage(content="a"), "a", "b", ts(2))
    b = entry(SystemMessage(content="b"), "b", "a", ts(3))
    assert _leaf_uuids({"t": t, "a": a, "b": b}) == frozenset()


def test_a_main_leaf_before_the_epoch_is_still_the_tip(tmp_path):
    """`findLatestMessage` starts from -Infinity: any parseable timestamp can win."""
    path = write(tmp_path / "s.jsonl", entry(HumanMessage(content="old"), "u1", None, "1969-12-31T23:59:59.000Z"))
    assert [e["uuid"] for e in load_transcript_file(path).chain] == ["u1"]


def _group(tmp_path: Path, *, first_has_id: bool) -> Path:
    """Two answers in the chain; only the second has an orphaned parallel result."""
    a1 = AIMessage(content="first", id="msg-1" if first_has_id else None)
    a2 = AIMessage(content="", id="msg-2", tool_calls=[
        {"name": "X", "args": {}, "id": "c1"}, {"name": "X", "args": {}, "id": "c2"}])
    return write(
        tmp_path / "s.jsonl",
        entry(HumanMessage(content="task"), "u1", None, ts(1)),
        entry(a1, "a1", "u1", ts(2)),
        entry(HumanMessage(content="more"), "u2", "a1", ts(3)),
        entry(a2, "a2", "u2", ts(4)),
        entry(ToolMessage(content="r2", tool_call_id="c2"), "t2", "a2", ts(6)),
        entry(ToolMessage(content="r1", tool_call_id="c1"), "t1", "a2", ts(5)),
    )


def test_recovery_reaches_a_later_group_past_one_without_a_provider_id(tmp_path):
    uuids = [message_uuid(m) for m in load_messages_from_jsonl_path(_group(tmp_path, first_has_id=False))]
    assert uuids == ["u1", "a1", "u2", "a2", "t1", "t2"]


def test_recovery_reaches_a_later_group_past_a_complete_one(tmp_path):
    uuids = [message_uuid(m) for m in load_messages_from_jsonl_path(_group(tmp_path, first_has_id=True))]
    assert uuids == ["u1", "a1", "u2", "a2", "t1", "t2"]


def test_orphaned_siblings_are_ordered_by_timestamp():
    """Written out of order, recovered in content-block (timestamp) order."""
    def asst(cid: str, uuid: str, t: int) -> dict[str, Any]:
        return entry(AIMessage(content="", id="m", tool_calls=[{"name": "X", "args": {}, "id": cid}]), uuid, "u1",
                     ts(t))

    u1 = entry(HumanMessage(content="task"), "u1", None, ts(1))
    a1, late, early = asst("c1", "a1", 2), asst("c3", "s3", 4), asst("c2", "s2", 3)
    by_uuid = {"u1": u1, "a1": a1, "s3": late, "s2": early}
    assert [e["uuid"] for e in build_conversation_chain(by_uuid, a1)] == ["u1", "a1", "s2", "s3"]


def test_an_agent_leaf_is_the_entry_nobody_points_at_not_the_newest(tmp_path):
    """Clock skew: the parent is stamped later than its child; the child is still the tip."""
    path = write(
        tmp_path / "agent.jsonl",
        entry(HumanMessage(content="go"), "a", None, ts(5), sidechain=True, agent_id="w"),
        entry(AIMessage(content="done"), "b", "a", ts(1), sidechain=True, agent_id="w"),
    )
    assert [message_uuid(m) for m in get_agent_transcript(path, "w")] == ["a", "b"]


def test_a_chain_of_non_messages_that_runs_out_of_parents_has_no_leaf():
    from app.loop.storage.loader import _leaf_uuids

    entries = {
        "s1": {"uuid": "s1", "type": "system", "parentUuid": None},
        "s2": {"uuid": "s2", "type": "system", "parentUuid": "s1"},
    }
    assert _leaf_uuids(entries) == frozenset()


def test_a_cycle_of_non_messages_behind_a_terminal_ends_the_walk():
    from app.loop.storage.loader import _leaf_uuids

    entries = {
        "t": {"uuid": "t", "type": "system", "parentUuid": "a"},
        "a": {"uuid": "a", "type": "system", "parentUuid": "b"},
        "b": {"uuid": "b", "type": "system", "parentUuid": "a"},
    }
    assert _leaf_uuids(entries) == frozenset()
