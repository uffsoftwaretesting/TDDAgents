"""Phase 7: `run_agent` — the port of `runAgent.ts` — drives a worker through `run_loop`."""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from app.state.app_state_store import AppState, AppStateStore
from app.tdd.ledger import PhaseLedger, TddPhase
from app.tool import ToolResult, build_tool, tool_context_for
from app.tools.agent_tool.load_agents_dir import AgentDefinition
from app.tools.agent_tool.run_agent import create_agent_id, run_agent
from app.types.permissions import PermissionMode, ToolPermissionContext
from app.utils.session_storage import TranscriptStore
from tests.test_loop_engine import CONFIG, FakeModel, asks_for, make_fake_deps

TESTER = AgentDefinition(name="tester", description="writes tests", prompt="You write tests.", phase=TddPhase.RED)


def _tool(name: str, *, test_writer: bool = False, impl_writer: bool = False, call=None):
    async def default(args, ctx):
        return ToolResult(content=f"ran {name}")

    return build_tool(name=name, prompt=name, call=call or default, is_test_writer=test_writer,
                      is_implementation_writer=impl_writer)


def _parent(store: AppStateStore | None = None, **kw: Any):
    return tool_context_for(store or AppStateStore(), messages=(HumanMessage(content="coordinator"),), **kw)


def _collect(gen) -> list[Any]:
    async def go():
        return [m async for m in gen]

    return asyncio.run(go())


def _run(definition=TESTER, model=None, tools=(), parent=None, **kw):
    model = model or FakeModel([[AIMessage(content="done")]])
    deps = kw.pop("deps", None) or make_fake_deps(model)
    events = _collect(run_agent(
        agent_definition=definition,
        prompt_messages=[HumanMessage(content="write a failing test")],
        tool_context=parent or _parent(),
        config=CONFIG,
        deps=deps,
        available_tools=tools,
        system_prompt=("You write tests.",),
        **kw,
    ))
    return model, events


def test_agent_ids_follow_upstreams_format():
    agent_id = create_agent_id()
    assert agent_id.startswith("a") and len(agent_id) == 17
    int(agent_id[1:], 16)
    assert create_agent_id("x").startswith("ax-")


def test_the_worker_starts_from_its_prompt_with_its_own_identity():
    model, events = _run(agent_id="a1")
    state = model.seen[0]
    ctx = state.tool_context
    assert [m.content for m in state.messages] == ["write a failing test"]
    assert (ctx.agent_id, ctx.agent_type) == ("a1", "tester")
    assert ctx.agent_system_prompt == ("You write tests.",)
    assert ctx.read_file_state == {}
    assert [m.content for m in events if isinstance(m, AIMessage)] == ["done"]


def test_a_sync_worker_shares_the_parents_ledger():
    store = AppStateStore(AppState(phase_ledger=PhaseLedger()))

    async def run_tests(args, ctx):
        ctx.set_app_state(lambda s: dataclasses.replace(s, phase_ledger=s.phase_ledger.with_test_result(1)))
        return ToolResult(content="1 failed")

    from app.services.tools.tool_orchestration import run_tools

    model = FakeModel([[asks_for("RunTests", "c1")], [AIMessage(content="red")]])
    deps = dataclasses.replace(make_fake_deps(model), run_tools=run_tools)
    _run(model=model, deps=deps, tools=(_tool("RunTests", call=run_tests),), parent=_parent(store))
    assert store.get().phase_ledger.red_confirmed is True


def test_an_async_worker_cannot_write_the_parents_state():
    store = AppStateStore(AppState(phase_ledger=PhaseLedger()))
    model, _ = _run(parent=_parent(store), is_async=True)
    ctx = model.seen[0].tool_context
    ctx.set_app_state(lambda s: dataclasses.replace(s, phase_ledger=PhaseLedger(phase=TddPhase.GREEN)))
    assert store.get().phase_ledger.phase == TddPhase.RED


def test_the_definitions_permission_mode_applies_unless_the_parent_is_permissive():
    plan_agent = dataclasses.replace(TESTER, permission_mode="plan")
    for parent_mode, expected in (
        (PermissionMode.DEFAULT, PermissionMode.PLAN),
        (PermissionMode.ACCEPT_EDITS, PermissionMode.ACCEPT_EDITS),
        (PermissionMode.BYPASS_PERMISSIONS, PermissionMode.BYPASS_PERMISSIONS),
    ):
        store = AppStateStore(AppState(tool_permission_context=ToolPermissionContext(mode=parent_mode)))
        model, _ = _run(definition=plan_agent, parent=_parent(store))
        assert model.seen[0].tool_context.get_app_state().tool_permission_context.mode == expected
        assert store.get().tool_permission_context.mode == parent_mode


def test_an_async_worker_avoids_permission_prompts():
    model, _ = _run(is_async=True)
    assert model.seen[0].tool_context.get_app_state().tool_permission_context.should_avoid_permission_prompts


def test_the_worker_pool_is_resolved_and_phase_filtered():
    tools = (_tool("ReadFile"), _tool("WriteFile", impl_writer=True), _tool("Agent"), _tool("Grep"))
    only = dataclasses.replace(TESTER, tools=("ReadFile", "WriteFile", "Agent"))
    model, _ = _run(definition=only, tools=tools)
    names = [t.name for t in model.seen[0].tool_context.tools]
    # Agent is disallowed for every subagent (external build); Grep is not in the allowlist;
    # in RED the implementation writer is denied by the phase rules.
    assert "Agent" not in names and "Grep" not in names
    assert "ReadFile" in names


def test_the_worker_pool_follows_the_phase_between_turns():
    store = AppStateStore(AppState(phase_ledger=PhaseLedger()))
    tools = (_tool("ReadFile"), _tool("SrcWriter", impl_writer=True))
    model, _ = _run(tools=tools, parent=_parent(store))
    refresh = model.seen[0].tool_context.refresh_tools
    before = refresh()
    assert refresh() is before
    store.update(lambda s: dataclasses.replace(s, phase_ledger=PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True)))
    after = refresh()
    assert after is not before and "SrcWriter" in [t.name for t in after]


def test_the_worker_is_recorded_as_a_sidechain(tmp_path):
    store = TranscriptStore("sess", tmp_path, version="v", git_branch=None)
    _run(agent_id="a9", transcript=store)
    import json

    path = store.agent_transcript_path("a9")
    entries = [json.loads(line) for line in path.read_text().splitlines()]
    assert [e["message"]["data"]["content"] for e in entries] == ["write a failing test", "done"]
    assert all(e["isSidechain"] and e["agentId"] == "a9" for e in entries)
    assert entries[1]["parentUuid"] == entries[0]["uuid"]
    assert not store.path.exists()


def test_the_workers_transitions_never_reach_the_parents_sink():
    seen: list[Any] = []
    deps = dataclasses.replace(make_fake_deps(FakeModel([[asks_for("ReadFile", "c")], [AIMessage(content="d")]])),
                               emit_event=seen.append)
    _run(deps=deps, tools=(_tool("ReadFile"),))
    assert seen == []


def test_max_turns_ends_the_worker():
    """`maxTurns`: upstream's own optional ceiling, only when the definition declares it."""
    bounded = dataclasses.replace(TESTER, max_turns=1)
    model = FakeModel([[asks_for("ReadFile", "c1")], [asks_for("ReadFile", "c2")], [AIMessage(content="d")]])
    _, events = _run(definition=bounded, model=model, tools=(_tool("ReadFile"),))
    assert len(model.seen) == 1
    assert not any(isinstance(e, AIMessage) and e.content == "d" for e in events)


def test_the_resolved_model_is_the_workers_model():
    haiku = dataclasses.replace(TESTER, model="haiku")
    model, _ = _run(definition=haiku)
    from app.utils.model.model import parse_user_specified_model

    assert model.seen_configs[0].model == parse_user_specified_model("haiku")
    model, _ = _run(definition=haiku, model_override="o4-mini")
    assert model.seen_configs[0].model == "o4-mini"


def test_subagent_start_context_is_attached():
    from app.types.hooks import AggregatedHookResult
    from tests.hook_doubles import ScriptedDispatcher

    d = ScriptedDispatcher(script={"SubagentStart": [AggregatedHookResult(additional_contexts=("use pytest",))]})
    model, _ = _run(agent_id="a2", parent=_parent(hook_dispatcher=d))
    event, hook_input = d.seen[0]
    assert (event, hook_input["agent_id"], hook_input["agent_type"]) == ("SubagentStart", "a2", "tester")
    first = model.seen[0].messages
    assert len(first) == 2
    assert first[1].additional_kwargs["attachment"]["type"] == "hook_additional_context"
    assert "use pytest" in str(first[1].content)


def test_cleanup_removes_the_workers_todos():
    store = AppStateStore(AppState(todos={"a3": ("x",), "main": ("y",)}))
    _run(agent_id="a3", parent=_parent(store))
    assert dict(store.get().todos) == {"main": ("y",)}


def test_a_worker_inherits_the_runs_services():
    """`createSubagentContext`: the run's dispatcher, workspace, cost and transcript are shared."""
    from types import SimpleNamespace

    from tests.hook_doubles import ScriptedDispatcher

    tracker, ws, dispatcher = object(), SimpleNamespace(root="/w"), ScriptedDispatcher()
    parent = _parent(run_id="run-7", hook_dispatcher=dispatcher, workspace=ws, cost_tracker=tracker,
                     transcript_path="/p/run-7.jsonl")
    model, _ = _run(parent=parent)
    ctx = model.seen[0].tool_context
    assert (ctx.run_id, ctx.hook_dispatcher, ctx.workspace) == ("run-7", dispatcher, ws)
    assert (ctx.cost_tracker, ctx.transcript_path) == (tracker, "/p/run-7.jsonl")
    assert ctx.cancel is parent.cancel


def test_the_worker_starts_from_fresh_per_agent_state():
    """`createSubagentContext`: fresh tracking sets and file state, the worker's own messages."""
    parent = _parent()
    parent.read_file_state["x.py"] = object()
    parent.in_flight_tool_ids.add("busy")
    parent.nested_memory_attachment_triggers.add("/t")
    parent.loaded_nested_memory_paths.add("/l")
    model, _ = _run(parent=parent)
    ctx = model.seen[0].tool_context
    assert ctx.read_file_state == {} and ctx.read_file_state is not parent.read_file_state
    assert ctx.in_flight_tool_ids == set() and ctx.in_flight_tool_ids is not parent.in_flight_tool_ids
    assert ctx.nested_memory_attachment_triggers == set()
    assert ctx.loaded_nested_memory_paths == set()
    assert [m.content for m in ctx.messages] == ["write a failing test"]


def test_denial_tracking_is_shared_only_by_a_sync_worker():
    from app.tool import DenialTrackingBox

    box = DenialTrackingBox()
    parent = dataclasses.replace(_parent(), local_denial_tracking=box)
    model, _ = _run(parent=parent)
    assert model.seen[0].tool_context.local_denial_tracking is box
    model, _ = _run(parent=parent, is_async=True)
    assert isinstance(model.seen[0].tool_context.local_denial_tracking, DenialTrackingBox)
    assert model.seen[0].tool_context.local_denial_tracking is not box


def test_an_async_worker_only_gets_the_async_allowed_tools():
    tools = (_tool("ReadFile"), _tool("Probe"))
    model, _ = _run(tools=tools, is_async=True)
    assert [t.name for t in model.seen[0].tool_context.tools] == ["ReadFile"]
    model, _ = _run(tools=tools)
    assert sorted(t.name for t in model.seen[0].tool_context.tools) == ["Probe", "ReadFile"]


def test_the_phase_denies_the_implementation_writer_in_red():
    tools = (_tool("ReadFile"), _tool("WriteFile", impl_writer=True))
    model, _ = _run(tools=tools)
    assert [t.name for t in model.seen[0].tool_context.tools] == ["ReadFile"]


def test_the_context_omissions_follow_the_definition():
    model, _ = _run(definition=dataclasses.replace(TESTER, name="Explore", omit_claude_md=True))
    ctx = model.seen[0].tool_context
    assert (ctx.omit_claude_md, ctx.omit_git_status) == (True, True)
    model, _ = _run()
    ctx = model.seen[0].tool_context
    assert (ctx.omit_claude_md, ctx.omit_git_status) == (False, False)
    model, _ = _run(definition=dataclasses.replace(TESTER, name="Plan"))
    assert model.seen[0].tool_context.omit_git_status is True


def test_max_turns_allows_exactly_that_many_turns():
    bounded = dataclasses.replace(TESTER, max_turns=2)
    model = FakeModel([[asks_for("ReadFile", "c1")], [asks_for("ReadFile", "c2")], [AIMessage(content="d")]])
    _run(definition=bounded, model=model, tools=(_tool("ReadFile"),))
    assert len(model.seen) == 2


def test_only_next_turns_count_toward_max_turns():
    from app.query.stop_hooks import StopHookResult
    from tests.test_loop_engine import FakeStopHooks

    bounded = dataclasses.replace(TESTER, max_turns=1)
    model = FakeModel([[AIMessage(content="first")], [AIMessage(content="second")]])
    blocking = StopHookResult(blocking_errors=(HumanMessage(content="keep going"),))
    deps = dataclasses.replace(make_fake_deps(model), stop_hooks=FakeStopHooks([blocking]))
    _run(definition=bounded, model=model, deps=deps)
    assert len(model.seen) == 2


def test_closing_the_worker_closes_its_loop(monkeypatch):
    import app.tools.agent_tool.run_agent as run_agent_mod

    closed: list[bool] = []

    async def fake_loop(state, config, deps):
        try:
            yield AIMessage(content="one")
            yield AIMessage(content="two")
        finally:
            closed.append(True)

    monkeypatch.setattr(run_agent_mod, "run_loop", fake_loop)

    async def go():
        gen = run_agent(agent_definition=TESTER, prompt_messages=[HumanMessage(content="p")],
                        tool_context=_parent(), config=CONFIG, deps=make_fake_deps(FakeModel([])),
                        available_tools=(), system_prompt=("s",))
        first = await gen.__anext__()
        await gen.aclose()  # type: ignore[attr-defined]
        # Checked here: `asyncio.run` finalizes any leftover generator on its way out.
        assert closed == [True]
        return first

    assert asyncio.run(go()).content == "one"


def test_the_subagent_start_attachment_names_its_hook():
    from app.types.hooks import AggregatedHookResult
    from tests.hook_doubles import ScriptedDispatcher

    d = ScriptedDispatcher(script={"SubagentStart": [AggregatedHookResult(additional_contexts=("ctx",))]})
    model, _ = _run(parent=_parent(hook_dispatcher=d))
    attachment = model.seen[0].messages[1].additional_kwargs["attachment"]
    assert (attachment["hookName"], attachment["hookEvent"]) == ("SubagentStart", "SubagentStart")
    import uuid

    uuid.UUID(attachment["toolUseID"])


def test_a_compact_boundary_is_recorded_in_the_sidechain(tmp_path):
    import json

    from app.services.compact.auto_compact import AutoCompactOutcome
    from app.services.compact.compact import (
        CompactionResult,
        create_compact_boundary_message,
    )

    result = CompactionResult(boundary_marker=create_compact_boundary_message("auto", 5),
                              summary_messages=[HumanMessage(content="summary")], attachments=[], hook_results=[])
    calls: list[int] = []

    async def auto(messages, state, config):
        calls.append(1)
        if len(calls) == 1:
            return AutoCompactOutcome(was_compacted=True, compaction_result=result, consecutive_failures=0)
        return AutoCompactOutcome(was_compacted=False)

    store = TranscriptStore("sess", tmp_path, version="v", git_branch=None)
    deps = dataclasses.replace(make_fake_deps(FakeModel([[AIMessage(content="d")]])), autocompact=auto)
    _run(deps=deps, agent_id="a5", transcript=store)
    entries = [json.loads(line) for line in store.agent_transcript_path("a5").read_text().splitlines()]
    assert any(e.get("subtype") == "compact_boundary" for e in entries)


def test_an_async_worker_writes_task_state_through_to_the_root():
    """Upstream's `setAppStateForTasks`: a background worker's task writes reach the root store,
    while everything else it writes (the ledger above all) is discarded."""
    store = AppStateStore(AppState(phase_ledger=PhaseLedger()))
    model, _ = _run(parent=_parent(store), is_async=True)
    ctx = model.seen[0].tool_context
    ctx.set_app_state(lambda s: dataclasses.replace(
        s, tasks={"a9": "task"}, phase_ledger=PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True)))
    assert store.get().tasks == {"a9": "task"}
    assert store.get().phase_ledger == PhaseLedger()
    before = store.get()
    ctx.set_app_state(lambda s: dataclasses.replace(s, todos={"x": ("y",)}))
    assert store.get() is before


def test_an_async_worker_has_its_own_cancel_token_unless_given_one():
    from app.tool import CancelToken

    parent = _parent()
    model, _ = _run(parent=parent, is_async=True)
    own = model.seen[0].tool_context.cancel
    assert own is not parent.cancel and own.cancelled is False
    given = CancelToken()
    model, _ = _run(parent=parent, is_async=True, cancel=given)
    assert model.seen[0].tool_context.cancel is given
    model, _ = _run(parent=parent)
    assert model.seen[0].tool_context.cancel is parent.cancel


def test_the_worker_writes_its_metadata_sidecar(tmp_path):
    """`runAgent`'s `writeAgentMetadata`: what a resume routes on (Phase 8)."""
    store = TranscriptStore("sess", tmp_path, version="v", git_branch=None)
    _run(agent_id="a9", transcript=store, description="write it")
    assert store.read_agent_metadata("a9") == {"agentType": "tester", "description": "write it"}
    _run(agent_id="a8", transcript=store)
    assert store.read_agent_metadata("a8") == {"agentType": "tester"}


# ── Phase 8: preloaded skills and the main-loop model ───────────────────────

def _registry(tmp_path):
    from app.skills.load_skills_dir import get_skill_registry

    skill = tmp_path / "bundled" / "probe"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\ndescription: a probe skill\n---\nPROBE BODY\n")
    return get_skill_registry(tmp_path / "project", tmp_path / "home", tmp_path / "etc",
                              bundled_dir=tmp_path / "bundled")


def test_preloaded_skills_follow_the_subagent_start_context(tmp_path):
    """`runAgent`: prompt, then SubagentStart context, then each `skills` entry as a meta message."""
    parent = dataclasses.replace(_parent(), skill_registry=_registry(tmp_path), run_id="r1")
    definition = dataclasses.replace(TESTER, skills=("probe", "missing"))
    model, _ = _run(definition, parent=parent)
    messages = model.seen[0].messages
    assert messages[0].content == "write a failing test"
    (preloaded,) = messages[1:]
    assert preloaded.additional_kwargs.get("is_meta") is True
    assert "PROBE BODY" in str(preloaded.content)


def test_a_worker_without_skills_or_registry_preloads_nothing(tmp_path):
    parent = dataclasses.replace(_parent(), skill_registry=None)
    model, _ = _run(dataclasses.replace(TESTER, skills=("probe",)), parent=parent)
    assert [m.content for m in model.seen[0].messages] == ["write a failing test"]


def test_a_worker_never_inherits_the_main_loops_model_override():
    """`createSubagentContext`: the worker's options carry its own model, not the parent's."""
    parent = dataclasses.replace(_parent(), main_loop_model="skill-model")
    model, _ = _run(parent=parent)
    assert model.seen[0].tool_context.main_loop_model is None


def test_preloaded_skills_see_the_runs_session_id(tmp_path):
    from app.skills.load_skills_dir import get_skill_registry

    skill = tmp_path / "bundled" / "sid"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\ndescription: d\n---\nsession=${TDDAGENTS_SESSION_ID}\n")
    registry = get_skill_registry(tmp_path / "p", tmp_path / "h", tmp_path / "e", bundled_dir=tmp_path / "bundled")
    parent = dataclasses.replace(_parent(), skill_registry=registry, run_id="r1")
    model, _ = _run(dataclasses.replace(TESTER, skills=("sid",)), parent=parent)
    assert "session=r1" in str(model.seen[0].messages[1].content)
