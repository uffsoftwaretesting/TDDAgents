"""Phase 6: the transcript write path (`sessionStorage.ts` -> `recordTranscript`,
`recordSidechainTranscript`, `Project.insertMessageChain`, `materializeSessionFile`,
`appendEntry`, `shouldSkipPersistence`)."""

from __future__ import annotations

import itertools
import json
import stat
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.services.compact.compact import create_compact_boundary_message
from app.utils import session_storage
from app.utils.session_storage import TranscriptStore, message_uuid


@pytest.fixture(autouse=True)
def _config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv("TDDAGENTS_SKIP_PROMPT_HISTORY", raising=False)


def make_store(**kwargs: Any) -> TranscriptStore:
    counter = itertools.count(1)
    defaults: dict[str, Any] = {
        "uuid": lambda: f"u{next(counter)}",
        "now": lambda: 0.0,
        "version": "v1",
        "git_branch": "main",
    }
    defaults.update(kwargs)
    return TranscriptStore("s1", "/w", **defaults)


def read(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_the_store_writes_to_the_sessions_transcript_path(tmp_path):
    store = make_store()
    assert store.path == tmp_path / "cfg" / "projects" / "-w" / "s1.jsonl"
    agents = tmp_path / "cfg" / "projects" / "-w" / "s1" / "subagents"
    assert store.agent_transcript_path("a1") == agents / "agent-a1.jsonl"


def test_messages_form_a_parent_chain_with_the_session_stamp():
    store = make_store()
    h, a = HumanMessage(content="task"), AIMessage(content="ok")
    last = store.record_transcript([h, a])
    entries = read(store.path)
    assert [e["uuid"] for e in entries] == ["u1", "u2"]
    assert [e["parentUuid"] for e in entries] == [None, "u1"]
    assert last == "u2"
    assert (entries[0]["sessionId"], entries[0]["cwd"], entries[0]["version"], entries[0]["gitBranch"]) == (
        "s1", "/w", "v1", "main")
    assert entries[0]["timestamp"] == "1970-01-01T00:00:00.000Z"
    assert entries[0]["isSidechain"] is False
    assert message_uuid(h) == "u1"


def test_an_already_recorded_prefix_is_skipped_and_parents_the_new_messages():
    """The growing-array caller (QueryEngine): each call re-sends the whole history."""
    store = make_store()
    h, a = HumanMessage(content="task"), AIMessage(content="one")
    store.record_transcript([h])
    store.record_transcript([h, a])
    entries = read(store.path)
    assert [e["uuid"] for e in entries] == ["u1", "u2"]
    assert entries[1]["parentUuid"] == "u1"


def test_an_all_recorded_slice_returns_the_prefix_tail():
    store = make_store()
    h, a = HumanMessage(content="task"), AIMessage(content="one")
    store.record_transcript([h, a])
    assert store.record_transcript([h, a]) == "u2"
    assert len(read(store.path)) == 2
    assert store.record_transcript([]) is None


def test_a_recorded_message_after_a_new_one_does_not_move_the_parent():
    """Compaction: the new boundary and summary come first, kept messages after — the
    boundary gets `parentUuid: null` and nothing re-parents onto the old chain."""
    store = make_store()
    old = HumanMessage(content="old")
    store.record_transcript([old])
    boundary = create_compact_boundary_message("auto", 100)
    summary = HumanMessage(content="summary", additional_kwargs={"is_compact_summary": True})
    store.record_transcript([boundary, summary, old])
    entries = read(store.path)
    assert [e["uuid"] for e in entries] == ["u1", "u2", "u3"]
    assert entries[1]["parentUuid"] is None
    assert entries[1]["logicalParentUuid"] is None  # no recorded prefix before the boundary
    assert entries[2]["parentUuid"] == "u2"


def test_a_boundary_after_a_recorded_prefix_keeps_it_as_logical_parent():
    store = make_store()
    h = HumanMessage(content="t")
    store.record_transcript([h])
    boundary = create_compact_boundary_message("auto", 5)
    store.record_transcript([h, boundary, HumanMessage(content="s")])
    entries = read(store.path)
    assert (entries[1]["parentUuid"], entries[1]["logicalParentUuid"]) == (None, "u1")
    assert entries[2]["parentUuid"] == "u2"


def test_a_tool_result_chains_to_the_assistant_that_called_it():
    """Per-block emission: two parallel calls are two assistant messages; each result's
    parent is its own caller (`sourceToolAssistantUUID`), recorded across calls."""
    store = make_store()
    h = HumanMessage(content="t")
    a1 = AIMessage(content="", tool_calls=[{"name": "ReadFile", "args": {}, "id": "c1"}])
    a2 = AIMessage(content="", tool_calls=[{"name": "ReadFile", "args": {}, "id": "c2"}])
    store.record_transcript([h, a1, a2])
    r2 = ToolMessage(content="two", tool_call_id="c2")
    r1 = ToolMessage(content="one", tool_call_id="c1")
    nxt = AIMessage(content="done")
    store.record_transcript([h, a1, a2, r2, r1, nxt])
    entries = {e["uuid"]: e for e in read(store.path)}
    assert entries["u4"]["parentUuid"] == "u3" and entries["u4"]["sourceToolAssistantUUID"] == "u3"
    assert entries["u5"]["parentUuid"] == "u2" and entries["u5"]["sourceToolAssistantUUID"] == "u2"
    # The sequential cursor still advances through the results.
    assert entries["u6"]["parentUuid"] == "u5"


def test_an_unknown_tool_result_falls_back_to_the_sequential_parent():
    store = make_store()
    h = HumanMessage(content="t")
    r = ToolMessage(content="orphan", tool_call_id="zz")
    store.record_transcript([h, r])
    entries = read(store.path)
    assert entries[1]["parentUuid"] == "u1"
    assert "sourceToolAssistantUUID" not in entries[1]


def test_attachments_are_not_written_and_do_not_join_the_chain():
    store = make_store()
    h = HumanMessage(content="t")
    att = HumanMessage(content="r", additional_kwargs={"is_meta": True, "attachment": {"type": "tdd_state"}})
    a = AIMessage(content="ok")
    store.record_transcript([h, att, a])
    entries = read(store.path)
    assert [e["message"]["data"]["content"] for e in entries] == ["t", "ok"]
    assert entries[1]["parentUuid"] == entries[0]["uuid"]


def test_the_file_is_created_only_by_a_user_or_assistant_message():
    """`materializeSessionFile`: system-only writes stay buffered, then flush in order."""
    store = make_store()
    boundary = create_compact_boundary_message("auto", 1)
    store.record_transcript([boundary])
    assert not store.path.exists()
    h = HumanMessage(content="t")
    store.record_transcript([boundary, h])
    entries = read(store.path)
    assert [e["type"] for e in entries] == ["system", "user"]


def test_writes_append_and_never_rewrite():
    store = make_store()
    store.record_transcript([HumanMessage(content="a")])
    before = store.path.read_text()
    store.record_transcript([AIMessage(content="b")])
    assert store.path.read_text().startswith(before)


def test_file_and_directory_modes_are_private():
    store = make_store()
    store.record_transcript([HumanMessage(content="a")])
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700


def test_a_resumed_session_dedups_against_what_is_already_on_disk():
    first = make_store()
    h = HumanMessage(content="t")
    first.record_transcript([h])
    again = make_store(uuid=lambda: "r1")
    assert again.record_transcript([h, AIMessage(content="more")]) == "r1"
    entries = read(again.path)
    assert [e["message"]["data"]["content"] for e in entries] == ["t", "more"]
    assert entries[1]["parentUuid"] == "u1"


def test_persistence_can_be_switched_off():
    store = make_store(persist=False)
    store.record_transcript([HumanMessage(content="a")])
    store.record_sidechain_transcript([HumanMessage(content="w")], agent_id="a1")
    assert not store.path.exists()
    assert not store.agent_transcript_path("a1").exists()


def test_the_env_skip_switches_persistence_off(monkeypatch):
    monkeypatch.setenv("TDDAGENTS_SKIP_PROMPT_HISTORY", "1")
    store = make_store()
    store.record_transcript([HumanMessage(content="a")])
    assert not store.path.exists()


def test_a_sidechain_goes_to_the_agent_file_with_its_own_chain():
    store = make_store()
    main_h = HumanMessage(content="main")
    store.record_transcript([main_h])
    w1, w2 = HumanMessage(content="directive"), AIMessage(content="worker")
    last = store.record_sidechain_transcript([w1, w2], agent_id="a1")
    side = read(store.agent_transcript_path("a1"))
    assert [e["uuid"] for e in side] == ["u2", "u3"]
    assert [e["parentUuid"] for e in side] == [None, "u2"]
    assert all(e["isSidechain"] is True and e["agentId"] == "a1" for e in side)
    assert last == "u3"
    # The main file is untouched by the sidechain.
    assert [e["uuid"] for e in read(store.path)] == ["u1"]
    # Incremental: the caller passes the parent to continue from.
    w3 = AIMessage(content="more")
    store.record_sidechain_transcript([w3], agent_id="a1", starting_parent_uuid=last)
    assert read(store.agent_transcript_path("a1"))[-1]["parentUuid"] == "u3"


def test_sidechain_writes_are_not_deduped_against_the_main_file():
    """A fork-inherited parent message shares its uuid with the main transcript and must
    still be written to the agent file — and must not poison the main file's dedup."""
    store = make_store()
    shared = HumanMessage(content="shared")
    store.record_sidechain_transcript([shared], agent_id="a1")
    store.record_transcript([shared])
    assert [e["uuid"] for e in read(store.agent_transcript_path("a1"))] == ["u1"]
    assert [e["uuid"] for e in read(store.path)] == ["u1"]


def test_version_and_branch_are_resolved_once_when_not_given(monkeypatch):
    calls: list[tuple[str, ...]] = []

    def fake_git(args, cwd):
        calls.append((*args, str(cwd)))
        return {"--short": "abc1234", "--abbrev-ref": "feature"}[args[1]]

    monkeypatch.setattr(session_storage, "_git", fake_git)
    store = TranscriptStore("s1", "/w", uuid=lambda: "x", now=lambda: 0.0)
    store.record_transcript([HumanMessage(content="a")])
    entry = read(store.path)[0]
    assert (entry["version"], entry["gitBranch"]) == ("abc1234", "feature")
    assert calls[1] == ("rev-parse", "--abbrev-ref", "HEAD", "/w")
    assert len(calls) == 2


def test_git_failures_fall_back(monkeypatch):
    monkeypatch.setattr(session_storage, "_git", lambda args, cwd: None)
    store = TranscriptStore("s1", "/w", uuid=lambda: "x", now=lambda: 0.0)
    store.record_transcript([HumanMessage(content="a")])
    entry = read(store.path)[0]
    assert entry["version"] == "unknown"
    assert "gitBranch" not in entry


def test_an_explicit_none_branch_is_not_resolved(monkeypatch):
    monkeypatch.setattr(session_storage, "_git", lambda args, cwd: pytest.fail("git called"))
    store = make_store(git_branch=None)
    store.record_transcript([HumanMessage(content="a")])
    assert "gitBranch" not in read(store.path)[0]


def test_git_helper_runs_git(tmp_path):
    assert session_storage._git(["rev-parse", "--abbrev-ref", "HEAD"], tmp_path) is None
    import subprocess

    subprocess.run(["git", "init", "-q", "-b", "trunk", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.email=a@b", "-c", "user.name=n",
                    "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    assert session_storage._git(["rev-parse", "--abbrev-ref", "HEAD"], tmp_path) == "trunk"


def test_the_default_uuid_is_a_uuid4():
    store = TranscriptStore("s1", "/w", version="v", git_branch=None, now=lambda: 0.0)
    m = HumanMessage(content="a")
    store.record_transcript([m])
    import uuid

    assert uuid.UUID(message_uuid(m) or "").version == 4


def test_a_resumed_store_chains_a_tool_result_to_an_assistant_recorded_earlier():
    """The recorded prefix still teaches the store which assistant asked for which call."""
    first = make_store()
    ask = AIMessage(content="", tool_calls=[{"name": "RunTests", "args": {}, "id": "c1"}])
    note = HumanMessage(content="meanwhile")
    first.record_transcript([ask, note])
    resumed = make_store(uuid=lambda: "u9")
    result = ToolMessage(content="ok", tool_call_id="c1")
    resumed.record_transcript([ask, note, result])
    entry = read(resumed.path)[-1]
    assert (entry["parentUuid"], entry["sourceToolAssistantUUID"]) == (message_uuid(ask), message_uuid(ask))


def test_a_tool_result_in_the_same_batch_chains_to_its_assistant():
    store = make_store()
    ask = AIMessage(content="", tool_calls=[{"name": "RunTests", "args": {}, "id": "c1"}])
    store.record_transcript([ask, HumanMessage(content="between"), ToolMessage(content="ok", tool_call_id="c1")])
    entry = read(store.path)[-1]
    assert (entry["parentUuid"], entry["sourceToolAssistantUUID"]) == ("u1", "u1")


def test_a_tool_result_alone_materializes_the_file():
    store = make_store()
    store.record_transcript([ToolMessage(content="ok", tool_call_id="c1")])
    assert [e["type"] for e in read(store.path)] == ["user"]


def test_a_sidechain_drops_attachments_too():
    store = make_store()
    att = HumanMessage(content="r", additional_kwargs={"attachment": {"type": "tdd_state"}})
    store.record_sidechain_transcript([HumanMessage(content="go"), att], agent_id="a1")
    assert [e["message"]["data"]["content"] for e in read(store.agent_transcript_path("a1"))] == ["go"]


def test_the_env_skip_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("TDDAGENTS_SKIP_PROMPT_HISTORY", "TRUE")
    store = make_store()
    store.record_transcript([HumanMessage(content="a")])
    assert not store.path.exists()


def test_the_version_is_this_checkouts_head(monkeypatch):
    calls: list[tuple[Any, ...]] = []

    def fake_git(args, cwd):
        calls.append((tuple(args), cwd))
        return "abc"

    monkeypatch.setattr(session_storage, "_git", fake_git)
    TranscriptStore("s1", "/w", uuid=lambda: "x", now=lambda: 0.0, git_branch=None).record_transcript(
        [HumanMessage(content="a")])
    assert calls == [(("rev-parse", "--short", "HEAD"), session_storage._APP_ROOT)]


def test_a_failed_git_is_none_even_with_output(monkeypatch):
    import subprocess

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout="junk"))
    assert session_storage._git(["status"], "/") is None


def test_agent_metadata_is_a_sidecar_of_the_agent_transcript():
    """`writeAgentMetadata` / `readAgentMetadata` / `getAgentMetadataPath` (Phase 8)."""
    store = make_store()
    path = store.agent_metadata_path("a1")
    assert path == store.agent_transcript_path("a1").with_name("agent-a1.meta.json")
    assert store.read_agent_metadata("a1") is None
    store.write_agent_metadata("a1", {"agentType": "Explore", "description": "survey"})
    assert path.read_text() == '{"agentType":"Explore","description":"survey"}'
    assert store.read_agent_metadata("a1") == {"agentType": "Explore", "description": "survey"}
