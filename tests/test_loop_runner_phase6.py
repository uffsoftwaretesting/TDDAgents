"""Phase 6: the runner drives each sub-requirement through a `QueryEngine` — the transcript
on disk is a uuid chain, the events carry the ledger, and cost is tracked per run."""

from __future__ import annotations

import asyncio
import dataclasses
import itertools
import json
from typing import Any

from langchain_core.messages import AIMessage

from app.cost_tracker import CostTracker
from app.query.deps import get_production_deps
from app.tdd.event_log import EventLog
from app.utils.session_storage_portable import get_project_dir
from tests.session_run_helpers import run_item


def _run(make_fake_workspace, model, tmp_path) -> tuple[Any, Any]:
    """A workspace without a root: the session cwd is the project root."""
    deps = dataclasses.replace(get_production_deps(), call_model=model)
    result = asyncio.run(run_item(
        "the spec", 4, {"session_id": "s"}, workspace=make_fake_workspace(), deps=deps, project_root=tmp_path,
        home=tmp_path / "home", managed_dir=tmp_path / "etc"))
    return tmp_path, result


def _session_files(cwd) -> tuple[list[dict[str, Any]], EventLog]:
    project = get_project_dir(str(cwd))
    (transcript,) = project.glob("loop-*.jsonl")
    entries = [json.loads(line) for line in transcript.read_text().splitlines()]
    return entries, EventLog.load_jsonl(project / transcript.stem / "events.jsonl")


def test_the_transcript_is_a_chain_starting_with_the_spec(make_fake_workspace, tmp_path):
    async def model(state, config):
        yield AIMessage(content="done")

    ws, _ = _run(make_fake_workspace, model, tmp_path)
    entries, _ = _session_files(ws)
    assert entries[0]["parentUuid"] is None
    assert entries[0]["message"]["data"]["content"] == "the spec"
    for parent, child in itertools.pairwise(entries):
        assert child["parentUuid"] == parent["uuid"]
    assert {e["sessionId"] for e in entries} == {entries[0]["sessionId"]}
    assert entries[0]["sessionId"].startswith("loop-")
    # The TDD-state attachment is not persisted (external build's `isLoggableMessage`).
    assert all("attachment" not in e["message"]["data"]["additional_kwargs"] for e in entries)


def test_events_carry_the_plan_index_and_end_with_the_terminal(make_fake_workspace, tmp_path):
    async def model(state, config):
        yield AIMessage(content="done")

    ws, result = _run(make_fake_workspace, model, tmp_path)
    _, events = _session_files(ws)
    transitions = events.get_transitions()
    assert {e.payload["plan_index"] for e in transitions} == {4}
    assert transitions[-1].payload["terminal"] is True
    assert transitions[-1].transition_reason == result.terminal_reason
    # Barrier 3 refused the unobserved cycle once before the latch ended the run.
    assert "stop_hook_blocking" in [e.transition_reason for e in transitions]
    assert all(e.red_confirmed is False for e in transitions)


def test_the_run_tracks_cost_on_its_tool_context(make_fake_workspace, tmp_path):
    seen: list[Any] = []

    async def model(state, config):
        seen.append(state.tool_context.cost_tracker)
        yield AIMessage(content="done")

    _run(make_fake_workspace, model, tmp_path)
    assert isinstance(seen[0], CostTracker)
    assert seen[0] is seen[-1]


def test_the_tool_context_knows_the_transcript_path(make_fake_workspace, tmp_path):
    seen: list[Any] = []

    async def model(state, config):
        seen.append(state.tool_context.transcript_path)
        yield AIMessage(content="done")

    _run(make_fake_workspace, model, tmp_path)
    (transcript,) = get_project_dir(str(tmp_path)).glob("loop-*.jsonl")
    assert seen[0] == str(transcript)
