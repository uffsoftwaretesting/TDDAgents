"""Phase 6: the transcript entry format (`sessionStorage.ts` -> `insertMessageChain`'s
`TranscriptMessage`) — one whole message per JSONL line, lossless both ways."""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.loop.compact.compact import create_compact_boundary_message
from app.loop.storage import entries
from app.loop.storage.entries import (
    EntryStamp,
    build_entry,
    ensure_message_uuid,
    entry_to_message,
    entry_type_of,
    is_loggable_message,
    message_uuid,
)

STAMP = EntryStamp(session_id="s1", cwd="/w", version="abc123", git_branch="main")


def test_a_message_gets_one_stable_uuid():
    m = HumanMessage(content="hi")
    assert message_uuid(m) is None
    first = ensure_message_uuid(m, lambda: "u-1")
    assert first == "u-1" and message_uuid(m) == "u-1"
    assert ensure_message_uuid(m, lambda: "u-2") == "u-1"


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        (HumanMessage(content="x"), "user"),
        (ToolMessage(content="r", tool_call_id="c"), "user"),  # tool results are user messages
        (AIMessage(content="a"), "assistant"),
        (SystemMessage(content="s"), "system"),
    ],
)
def test_entry_types_follow_upstreams_vocabulary(message, kind):
    assert entry_type_of(message) == kind


def test_an_entry_carries_the_session_stamp_and_round_trips():
    m = AIMessage(content="", tool_calls=[{"name": "RunTests", "args": {"path": "t.py"}, "id": "c1"}])
    ensure_message_uuid(m, lambda: "u-a")
    entry = build_entry(m, parent_uuid="u-p", is_sidechain=False, stamp=STAMP, timestamp="T")
    assert entry["parentUuid"] == "u-p"
    assert entry["uuid"] == "u-a"
    assert entry["type"] == "assistant"
    assert (entry["isSidechain"], entry["userType"], entry["sessionId"]) == (False, "external", "s1")
    assert (entry["cwd"], entry["version"], entry["gitBranch"], entry["timestamp"]) == ("/w", "abc123", "main", "T")
    assert "logicalParentUuid" not in entry and "agentId" not in entry
    back = entry_to_message(json.loads(json.dumps(entry)))
    assert isinstance(back, AIMessage)
    assert back.tool_calls == m.tool_calls
    assert message_uuid(back) == "u-a"


def test_a_compact_boundary_cuts_the_chain_but_keeps_its_logical_parent():
    b = create_compact_boundary_message("auto", 1234)
    ensure_message_uuid(b, lambda: "u-b")
    entry = build_entry(b, parent_uuid="u-p", is_sidechain=False, stamp=STAMP, timestamp="T")
    assert entry["parentUuid"] is None
    assert entry["logicalParentUuid"] == "u-p"
    assert entry["subtype"] == "compact_boundary"
    assert entry["compactMetadata"] == {"trigger": "auto", "preTokens": 1234}


def test_flags_are_mirrored_at_the_top_level():
    meta = HumanMessage(content="m", additional_kwargs={"is_meta": True})
    summary = HumanMessage(content="s", additional_kwargs={"is_compact_summary": True})
    err = AIMessage(content="e", additional_kwargs={"is_api_error_message": True})
    for m in (meta, summary, err):
        ensure_message_uuid(m, lambda: "u")
    assert build_entry(meta, None, False, STAMP, "T")["isMeta"] is True
    assert build_entry(summary, None, False, STAMP, "T")["isCompactSummary"] is True
    assert build_entry(err, None, False, STAMP, "T")["isApiErrorMessage"] is True
    plain = build_entry(HumanMessage(content="p", additional_kwargs={"uuid": "u"}), None, False, STAMP, "T")
    assert not {"isMeta", "isCompactSummary", "isApiErrorMessage", "subtype"} & plain.keys()


def test_a_sidechain_entry_names_its_agent_and_a_tool_result_its_source():
    m = ToolMessage(content="ok", tool_call_id="c1", additional_kwargs={"uuid": "u-t"})
    entry = build_entry(m, "u-p", True, STAMP, "T", agent_id="a1", source_tool_assistant_uuid="u-a")
    assert (entry["isSidechain"], entry["agentId"]) == (True, "a1")
    assert entry["sourceToolAssistantUUID"] == "u-a"


def test_no_git_branch_is_omitted_not_null():
    stamp = EntryStamp(session_id="s1", cwd="/w", version="v", git_branch=None)
    entry = build_entry(HumanMessage(content="x", additional_kwargs={"uuid": "u"}), None, False, stamp, "T")
    assert "gitBranch" not in entry


def test_attachments_are_not_logged_in_the_external_build(monkeypatch):
    """`isLoggableMessage`: non-ant builds drop attachments, except hook additional context
    when its env opt-in is set."""
    monkeypatch.delenv("TDDAGENTS_SAVE_HOOK_ADDITIONAL_CONTEXT", raising=False)
    tdd = HumanMessage(content="r", additional_kwargs={"is_meta": True, "attachment": {"type": "tdd_state"}})
    hook = HumanMessage(content="r", additional_kwargs={"attachment": {"type": "hook_additional_context"}})
    assert is_loggable_message(HumanMessage(content="x")) is True
    assert is_loggable_message(tdd) is False
    assert is_loggable_message(hook) is False
    monkeypatch.setenv("TDDAGENTS_SAVE_HOOK_ADDITIONAL_CONTEXT", "1")
    assert is_loggable_message(hook) is True
    assert is_loggable_message(tdd) is False


def test_an_entry_without_a_message_payload_is_not_a_message():
    assert entry_to_message({"type": "summary"}) is None
    assert entry_to_message({"type": "user", "message": "garbage"}) is None


def test_the_timestamp_is_utc_iso_with_milliseconds():
    stamp = entries.iso_timestamp(0.0)
    assert stamp == "1970-01-01T00:00:00.000Z"
