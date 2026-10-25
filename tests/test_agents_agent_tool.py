"""Phase 7: the Agent tool (`AgentTool.tsx` -> `call`, `checkPermissions`,
`mapToolResultToToolResultBlockParam`; `agentToolUtils.ts` -> `finalizeAgentTool`), with the
TDD phase-on-spawn."""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.loop.agents.definition import AgentDefinition
from app.loop.agents.tool import (
    AGENT_TOOL_NAME,
    NO_OUTPUT_MARKER,
    AgentToolResult,
    build_agent_tool,
    finalize_agent_tool,
    map_agent_result,
    phase_on_spawn,
)
from app.loop.context import AppState, AppStateStore, tool_context_for
from app.loop.ledger import PhaseLedger, TddPhase
from app.loop.model_aliases import MODEL_ALIASES
from app.loop.permissions.types import (
    PermissionBehavior,
    PermissionRule,
    PermissionRuleSource,
    ToolPermissionContext,
)
from tests.test_loop_engine import CONFIG, FakeModel, asks_for, make_fake_deps

TESTER = AgentDefinition(name="tester", description="Writes failing tests.", prompt="You write tests.",
                         phase=TddPhase.RED)
DEVELOPER = AgentDefinition(name="developer", description="Makes them pass.", prompt="You implement.",
                            phase=TddPhase.GREEN)
REFACTORER = AgentDefinition(name="refactorer", description="Cleans up.", prompt="You refactor.",
                             phase=TddPhase.REFACTOR)
EXPLORE = AgentDefinition(name="Explore", description="Searches.", prompt="You search.")
DEFS = {d.name: d for d in (TESTER, DEVELOPER, REFACTORER, EXPLORE)}


def _tool(model: FakeModel | None = None, definitions=DEFS, **kw: Any):
    deps = make_fake_deps(model or FakeModel([[AIMessage(content="report")]]))
    return build_agent_tool(definitions=definitions, worker_tools=(), config=CONFIG, deps=deps, **kw)


def _ctx(ledger: PhaseLedger | None = None, perm: ToolPermissionContext | None = None):
    store = AppStateStore(AppState(phase_ledger=ledger or PhaseLedger(),
                                   tool_permission_context=perm or ToolPermissionContext()))
    return store, tool_context_for(store, messages=(HumanMessage(content="coordinate"),))


def _call(tool, args: dict[str, Any], ctx):
    return asyncio.run(tool.call(args, ctx))


ARGS = {"description": "write the test", "prompt": "Write a failing test for add()", "subagent_type": "tester"}


class TestSchema:
    def test_the_fields_are_upstreams_with_subagent_type_required(self, monkeypatch):
        monkeypatch.delenv("TDDAGENTS_DISABLE_BACKGROUND_TASKS", raising=False)
        schema = _tool().input_schema
        assert schema["required"] == ["description", "prompt", "subagent_type"]
        assert set(schema["properties"]) == {"description", "prompt", "subagent_type", "model", "run_in_background"}
        assert schema["properties"]["run_in_background"] == {
            "type": "boolean",
            "description": "Set to true to run this agent in the background. You will be notified when it completes.",
        }
        assert schema["properties"]["model"]["enum"] == sorted(MODEL_ALIASES)
        assert schema["additionalProperties"] is False

    def test_the_tool_is_read_only_concurrency_safe_and_allowed(self):
        tool = _tool()
        assert tool.name == AGENT_TOOL_NAME
        assert tool.is_read_only(ARGS) is True
        assert tool.is_concurrency_safe(ARGS) is True
        _, ctx = _ctx()
        result = asyncio.run(tool.check_permissions(ARGS, ctx)) if asyncio.iscoroutinefunction(
            tool.check_permissions) else tool.check_permissions(ARGS, ctx)
        assert result.behavior == PermissionBehavior.ALLOW

    def test_the_prompt_lists_the_agents_without_a_default_type(self):
        prompt = _tool().prompt
        assert "- tester: Writes failing tests. (Tools: All tools)" in prompt
        assert "general-purpose" not in prompt


class TestSelection:
    def test_an_unknown_type_names_the_available_ones(self):
        _, ctx = _ctx()
        with pytest.raises(ValueError, match="^Agent type 'nope' not found. Available agents: "
                                             "tester, developer, refactorer, Explore$"):
            _call(_tool(), {**ARGS, "subagent_type": "nope"}, ctx)

    def test_a_denied_type_names_its_rule(self):
        rule = PermissionRule(tool_name="Agent", rule_behavior=PermissionBehavior.DENY, rule_content="Explore",
                              source=PermissionRuleSource.POLICY_SETTINGS)
        _, ctx = _ctx(perm=ToolPermissionContext(always_deny_rules=(rule,)))
        with pytest.raises(ValueError, match="^Agent type 'Explore' has been denied by permission rule "
                                             "'Agent\\(Explore\\)' from policySettings\\.$"):
            _call(_tool(), {**ARGS, "subagent_type": "Explore"}, ctx)


class TestPhaseOnSpawn:
    def test_an_agent_without_a_phase_leaves_the_ledger(self):
        ledger = PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True)
        assert phase_on_spawn(ledger, EXPLORE) is ledger

    def test_the_same_phase_leaves_the_ledger(self):
        ledger = PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True)
        assert phase_on_spawn(ledger, DEVELOPER) is ledger
        assert phase_on_spawn(PhaseLedger(), TESTER) == PhaseLedger()

    def test_a_tester_after_red_restarts_the_cycle(self):
        ledger = PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True, green_passed=True)
        assert phase_on_spawn(ledger, TESTER) == PhaseLedger()

    def test_a_developer_needs_a_confirmed_red(self):
        with pytest.raises(ValueError, match="^Agent 'developer' cannot start: Cannot transition to GREEN"):
            phase_on_spawn(PhaseLedger(), DEVELOPER)

    def test_a_refactorer_needs_a_completed_cycle(self):
        done = PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True, green_passed=True)
        assert phase_on_spawn(done, REFACTORER).phase == TddPhase.REFACTOR
        with pytest.raises(ValueError, match="^Agent 'refactorer' cannot start"):
            phase_on_spawn(PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True), REFACTORER)

    def test_the_call_writes_the_spawn_phase_before_the_worker_runs(self):
        seen: list[Any] = []

        class Model(FakeModel):
            async def __call__(self, state, config):
                seen.append(state.tool_context.get_app_state().phase_ledger)
                async for m in super().__call__(state, config):
                    yield m

        store, ctx = _ctx(PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True, green_passed=True))
        _call(_tool(Model([[AIMessage(content="ok")]])), {**ARGS, "subagent_type": "refactorer"}, ctx)
        assert seen[0].phase == TddPhase.REFACTOR
        assert store.get().phase_ledger.phase == TddPhase.REFACTOR

    def test_a_refused_spawn_runs_no_worker_and_keeps_the_ledger(self):
        model = FakeModel([])
        store, ctx = _ctx()
        with pytest.raises(ValueError):
            _call(_tool(model), {**ARGS, "subagent_type": "developer"}, ctx)
        assert model.seen == [] and store.get().phase_ledger == PhaseLedger()


class TestRun:
    def test_the_worker_gets_its_prompt_and_its_system_prompt(self):
        model = FakeModel([[AIMessage(content="report")]])
        _, ctx = _ctx()
        _call(_tool(model), ARGS, ctx)
        state = model.seen[0]
        assert [m.content for m in state.messages] == ["Write a failing test for add()"]
        system = state.tool_context.agent_system_prompt
        assert system is not None
        assert system[0] == "You write tests."
        assert any("<env>" in s for s in system)
        assert state.tool_context.agent_type == "tester"

    def test_run_memory_is_appended_to_the_agents_prompt(self, tmp_path):
        from app.loop.agents.memory import AgentMemoryStore

        model = FakeModel([[AIMessage(content="report")]])
        store = AppStateStore(AppState())
        ctx = tool_context_for(store, run_id="r1", agent_memory_store=AgentMemoryStore(base_dir=tmp_path))
        defs = {"tester": dataclasses.replace(TESTER, memory="run")}
        _call(_tool(model, definitions=defs), ARGS, ctx)
        system = model.seen[0].tool_context.agent_system_prompt
        assert system is not None
        first = system[0]
        assert first.startswith("You write tests.\n\n")
        assert "run-memory/r1/tester" in first

    def test_the_result_is_the_last_text_with_upstreams_trailer(self):
        model = FakeModel([[asks_for("Nothing", "c1")], [AIMessage(content="final report")]])
        _, ctx = _ctx()
        result = _call(_tool(model), ARGS, ctx)
        assert result.content.startswith("final report\n")
        agent_id = result.metadata["agent_id"]
        assert f"agentId: {agent_id} (use SendMessage with to: '{agent_id}' to continue this agent)" in result.content
        assert "<usage>total_tokens: 0\ntool_uses: 1\nduration_ms: " in result.content
        assert result.metadata["agent_type"] == "tester"
        assert result.metadata["status"] == "completed"


class TestFinalize:
    def test_the_last_assistant_text_is_the_content(self):
        result = finalize_agent_tool([AIMessage(content="a"), AIMessage(content="b")], "a1", "tester", 0.0, now=1.5)
        assert result == AgentToolResult(agent_id="a1", agent_type="tester", content=("b",), total_duration_ms=1500,
                                         total_tokens=0, total_tool_use_count=0)

    def test_a_trailing_tool_call_falls_back_to_the_last_text(self):
        messages = [AIMessage(content="found it"), asks_for("ReadFile", "c")]
        assert finalize_agent_tool(messages, "a1", "t", 0.0, now=0.0).content == ("found it",)
        assert finalize_agent_tool(messages, "a1", "t", 0.0, now=0.0).total_tool_use_count == 1

    def test_tokens_come_from_the_last_assistants_usage(self):
        last = AIMessage(content="x", usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
        assert finalize_agent_tool([last], "a1", "t", 0.0, now=0.0).total_tokens == 15

    def test_no_assistant_is_an_error(self):
        with pytest.raises(ValueError, match="^No assistant messages found$"):
            finalize_agent_tool([HumanMessage(content="x")], "a1", "t", 0.0, now=0.0)

    def test_list_content_keeps_only_text_blocks(self):
        message = AIMessage(content=[{"type": "thinking", "thinking": "t"}, {"type": "text", "text": "out"}])
        assert finalize_agent_tool([message], "a1", "t", 0.0, now=0.0).content == ("out",)


class TestMapResult:
    RESULT = AgentToolResult(agent_id="a1", agent_type="tester", content=("one", "two"), total_duration_ms=7,
                             total_tokens=3, total_tool_use_count=2)

    def test_the_trailer_follows_the_content(self):
        assert map_agent_result(self.RESULT) == (
            "one\ntwo\nagentId: a1 (use SendMessage with to: 'a1' to continue this agent)\n"
            "<usage>total_tokens: 3\ntool_uses: 2\nduration_ms: 7</usage>"
        )

    def test_one_shot_agents_get_no_trailer(self):
        for agent_type in ("Explore", "Plan"):
            assert map_agent_result(dataclasses.replace(self.RESULT, agent_type=agent_type)) == "one\ntwo"

    def test_no_content_gets_the_marker(self):
        empty = dataclasses.replace(self.RESULT, content=(), agent_type="Explore")
        assert map_agent_result(empty) == NO_OUTPUT_MARKER == "(Subagent completed but returned no output.)"


def test_run_memory_needs_the_runs_store():
    defs = {"tester": dataclasses.replace(TESTER, memory="run")}
    _, ctx = _ctx()
    with pytest.raises(ValueError, match="^Run-scoped agent memory needs the run's memory store$"):
        _call(_tool(definitions=defs), ARGS, ctx)
    # An agent without memory needs no store.
    _call(_tool(definitions={"tester": dataclasses.replace(TESTER, memory="none")}), ARGS, ctx)


class TestCallWiring:
    def test_the_description_and_alias_are_upstreams(self):
        tool = _tool()
        assert tool.description(ARGS) == "write the test"
        assert tool.description({}) == ""
        assert tool.aliases == ("Task",)

    def test_a_coordinator_gets_the_slim_prompt(self):
        from app.loop.agents.prompt import get_prompt

        coordinator = _tool(is_coordinator=True).prompt
        assert coordinator == get_prompt(list(DEFS.values()), True, default_agent_type=None)
        assert coordinator != _tool().prompt

    def test_the_model_override_reaches_the_worker_and_its_env(self):
        from types import SimpleNamespace

        model = FakeModel([[AIMessage(content="r")]])
        store = AppStateStore(AppState())
        ctx = tool_context_for(store, workspace=SimpleNamespace(root="/proj"))
        _call(_tool(model), {**ARGS, "model": "haiku"}, ctx)
        from app.loop.model_aliases import parse_user_specified_model

        assert model.seen_configs[0].model == parse_user_specified_model("haiku")
        system = model.seen[0].tool_context.agent_system_prompt
        assert system is not None
        assert system[-1].endswith(f"You are powered by the model {parse_user_specified_model('haiku')}.")
        assert "Working directory: /proj" in system[-1]

    def test_the_definition_model_and_the_run_model_resolve(self):
        model = FakeModel([[AIMessage(content="r")]])
        defs = {"tester": dataclasses.replace(TESTER, model="sonnet")}
        _, ctx = _ctx()
        _call(_tool(model, definitions=defs), ARGS, ctx)
        system = model.seen[0].tool_context.agent_system_prompt
        assert system is not None and system[-1].endswith("You are powered by the model claude-sonnet-4-6.")
        model = FakeModel([[AIMessage(content="r")]])
        _call(_tool(model), ARGS, ctx)
        system = model.seen[0].tool_context.agent_system_prompt
        assert system is not None and system[-1].endswith(f"You are powered by the model {CONFIG.model}.")

    def test_the_reported_agent_id_is_the_workers(self):
        model = FakeModel([[AIMessage(content="r")]])
        _, ctx = _ctx()
        result = _call(_tool(model), ARGS, ctx)
        agent_id = model.seen[0].tool_context.agent_id
        assert agent_id and result.metadata["agent_id"] == agent_id
        assert f"agentId: {agent_id} " in result.content

    def test_a_one_shot_agent_gets_no_trailer(self):
        _, ctx = _ctx()
        result = _call(_tool(FakeModel([[AIMessage(content="found")]])), {**ARGS, "subagent_type": "Explore"}, ctx)
        assert result.content == "found"

    def test_a_worker_that_says_nothing_is_an_error(self):
        _, ctx = _ctx()
        with pytest.raises(ValueError, match="^No assistant messages found$"):
            _call(_tool(FakeModel([[]])), ARGS, ctx)

    def test_a_worker_error_before_any_answer_propagates(self):
        class BrokenStore:
            def record_sidechain_transcript(self, messages, agent_id, starting_parent_uuid=None):
                raise OSError("disk full")

        _, ctx = _ctx()
        ctx = dataclasses.replace(ctx, transcript_store=BrokenStore())
        with pytest.raises(OSError, match="^disk full$"):
            _call(_tool(FakeModel([[AIMessage(content="r")]])), ARGS, ctx)

    def test_a_worker_error_after_an_answer_keeps_the_partial_result(self):
        class FlakyStore:
            calls = 0

            def record_sidechain_transcript(self, messages, agent_id, starting_parent_uuid=None):
                FlakyStore.calls += 1
                if FlakyStore.calls > 2:
                    raise OSError("disk full")

        _, ctx = _ctx()
        ctx = dataclasses.replace(ctx, transcript_store=FlakyStore())
        model = FakeModel([[AIMessage(content="partial findings"), AIMessage(content="more")]])
        result = _call(_tool(model), ARGS, ctx)
        assert result.content.startswith("partial findings\n")

    def test_a_cancelled_run_is_an_abort(self):
        from app.loop.agents.tool import AbortError

        _, ctx = _ctx()
        ctx.cancel.cancel()
        with pytest.raises(AbortError):
            _call(_tool(FakeModel([[AIMessage(content="r")]])), ARGS, ctx)


def test_finalize_edges():
    no_text = finalize_agent_tool([asks_for("ReadFile", "c")], "a", "t", 0.0, now=0.0)
    assert no_text.content == ()
    timed = finalize_agent_tool([AIMessage(content="x")], "a", "t", 10.0, now=10.25)
    assert timed.total_duration_ms == 250
    untexted = AIMessage(content=[{"type": "text"}, {"type": "text", "text": "y"}])
    assert finalize_agent_tool([untexted], "a", "t", 0.0, now=0.0).content == ("", "y")


# ── Phase 8: background agents ───────────────────────────────────────────────


def test_disabling_background_tasks_removes_the_field(monkeypatch):
    """Upstream's `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS`, read when the tool is built."""
    monkeypatch.setenv("TDDAGENTS_DISABLE_BACKGROUND_TASKS", "1")
    assert "run_in_background" not in _tool().input_schema["properties"]


@pytest.mark.parametrize("definition_background", [False, True])
def test_a_phase_agent_never_runs_in_the_background(definition_background):
    defs = {"tester": dataclasses.replace(TESTER, background=definition_background or None)}
    args = {**ARGS, "run_in_background": not definition_background}
    model = FakeModel([])
    store, ctx = _ctx(PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True))
    with pytest.raises(ValueError, match="^Agent 'tester' declares TDD phase RED and cannot run in the background"):
        _call(_tool(model, definitions=defs), args, ctx)
    assert model.seen == []
    assert store.get().phase_ledger.phase == TddPhase.GREEN
    assert store.get().tasks == {}


def _background(tool, args, ctx, store):
    """Launch, then wait in the same event loop for the background task to finish."""

    async def go():
        result = await tool.call(args, ctx)
        for _ in range(200):
            await asyncio.sleep(0.01)
            if all(t.status != "running" for t in store.get().tasks.values()):
                break
        return result

    return asyncio.run(go())


def _bg_ctx(tmp_path, tools=()):
    from app.loop.command_queue import CommandQueue
    from app.loop.storage.session_storage import TranscriptStore

    store = AppStateStore()
    transcript = TranscriptStore("loop-1", tmp_path, version="v", git_branch=None)
    ctx = tool_context_for(store, run_id="loop-1", command_queue=CommandQueue(), transcript_store=transcript,
                           tools=tools)
    return store, ctx, transcript


def test_a_background_agent_launches_and_reports_through_the_queue(tmp_path, monkeypatch):
    monkeypatch.delenv("TDDAGENTS_DISABLE_BACKGROUND_TASKS", raising=False)
    store, ctx, transcript = _bg_ctx(tmp_path)
    tool = _tool(FakeModel([[AIMessage(content="found the module")]]))
    result = _background(tool, {**ARGS, "subagent_type": "Explore", "run_in_background": True}, ctx, store)
    (task,) = store.get().tasks.values()
    agent_id = task.id
    assert result.metadata == {"agent_id": agent_id, "agent_type": "Explore", "status": "async_launched"}
    output_file = transcript.path.parent / "loop-1" / "tasks" / f"{agent_id}.output"
    assert result.content == (
        f"Async agent launched successfully.\nagentId: {agent_id} (internal ID - do not mention to user. "
        f"Use SendMessage with to: '{agent_id}' to continue this agent.)\n"
        "The agent is working in the background. You will be notified automatically when it completes.\n"
        "Briefly tell the user what you launched and end your response. Do not generate any other text — "
        "agent results will arrive in a subsequent message."
    )
    assert (task.status, task.result, task.agent_type, task.description) == (
        "completed", "found the module", "Explore", "write the test")
    assert output_file.is_symlink() and output_file.resolve() == transcript.agent_transcript_path(agent_id).resolve()
    (note,) = ctx.command_queue.snapshot()
    assert f"<task-id>{agent_id}</task-id>" in note.value
    assert "<result>found the module</result>" in note.value and note.priority == "later"


def test_a_caller_that_can_read_gets_the_output_file_instructions(tmp_path):
    from app.loop.tools.base import build_tool
    from app.loop.tools.types import ToolResult

    reader = build_tool(name="ReadFile", prompt="r", call=lambda a, c: ToolResult(content=""))
    store, ctx, _transcript = _bg_ctx(tmp_path, tools=(reader,))
    result = _background(_tool(), {**ARGS, "subagent_type": "Explore", "run_in_background": True}, ctx, store)
    (task,) = store.get().tasks.values()
    assert result.content.endswith(
        "Do not duplicate this agent's work — avoid working with the same files or topics it is using. "
        "Work on non-overlapping tasks, or briefly tell the user what you launched and end your response.\n"
        f"output_file: {task.output_file}\n"
        "If asked, you can check progress before completion by using ReadFile or Bash tail on the output file."
    )


def test_coordinator_mode_backgrounds_agents_without_a_phase_only(tmp_path):
    store, ctx, _ = _bg_ctx(tmp_path)
    tool = _tool(FakeModel([[AIMessage(content="r1")], [AIMessage(content="r2")]]), is_coordinator=True)
    explore = _background(tool, {**ARGS, "subagent_type": "Explore"}, ctx, store)
    assert explore.metadata["status"] == "async_launched"
    tester = _background(tool, ARGS, ctx, store)
    assert tester.metadata["status"] == "completed"
    assert len(store.get().tasks) == 1


def test_disabled_background_tasks_run_synchronously(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_DISABLE_BACKGROUND_TASKS", "true")
    store, ctx, _ = _bg_ctx(tmp_path)
    tool = _tool(is_coordinator=True)
    result = _background(tool, {**ARGS, "subagent_type": "Explore", "run_in_background": True}, ctx, store)
    assert result.metadata["status"] == "completed" and store.get().tasks == {}


def test_a_definition_marked_background_runs_in_the_background(tmp_path):
    store, ctx, _ = _bg_ctx(tmp_path)
    defs = {"Explore": dataclasses.replace(EXPLORE, background=True)}
    result = _background(_tool(definitions=defs), {**ARGS, "subagent_type": "Explore"}, ctx, store)
    assert result.metadata["status"] == "async_launched"


def test_a_background_worker_is_async_and_cancellable(tmp_path):
    seen: list[Any] = []

    class Model(FakeModel):
        async def __call__(self, state, config):
            seen.append(state.tool_context)
            async for m in super().__call__(state, config):
                yield m

    store, ctx, _ = _bg_ctx(tmp_path)
    args = {**ARGS, "subagent_type": "Explore", "run_in_background": True}
    _background(_tool(Model([[AIMessage(content="r")]])), args, ctx, store)
    worker = seen[0]
    (task,) = store.get().tasks.values()
    assert worker.cancel is task.cancel and worker.cancel is not ctx.cancel
    assert worker.get_app_state().tool_permission_context.should_avoid_permission_prompts is True


# ── mutation-gate pins (Phase 8) ────────────────────────────────────────────

def test_a_background_launch_is_recorded_exactly(tmp_path, monkeypatch):
    monkeypatch.delenv("TDDAGENTS_DISABLE_BACKGROUND_TASKS", raising=False)
    seen: list[Any] = []

    class Model(FakeModel):
        async def __call__(self, state, config):
            seen.append(state.tool_context)
            async for m in super().__call__(state, config):
                yield m

    clock = iter([5.0, 6.0, 7.0, 8.0])
    store, ctx, transcript = _bg_ctx(tmp_path)
    ctx = dataclasses.replace(ctx, tool_use_id="toolu_1")
    tool = _tool(Model([[AIMessage(content="found")]]), now=lambda: next(clock))
    _background(tool, {**ARGS, "subagent_type": "Explore", "run_in_background": True}, ctx, store)
    (task,) = store.get().tasks.values()
    assert task.id == seen[0].agent_id
    # `registerAsyncAgent({ toolUseId: toolUseContext.toolUseId })`.
    assert task.tool_use_id == "toolu_1"
    output_file = transcript.path.parent / "loop-1" / "tasks" / f"{task.id}.output"
    assert (task.prompt, task.output_file, task.start_time, task.end_time) == (
        ARGS["prompt"], str(output_file), 5.0, 7.0)
    (note,) = ctx.command_queue.snapshot()
    assert f"<output-file>{output_file}</output-file>" in note.value
    assert "<tool-use-id>toolu_1</tool-use-id>" in note.value
    assert '<summary>Agent "write the test" completed</summary>' in note.value


def test_the_prompt_carries_the_background_notes_when_background_is_on(monkeypatch):
    from app.loop.agents.prompt import get_prompt

    monkeypatch.delenv("TDDAGENTS_DISABLE_BACKGROUND_TASKS", raising=False)
    assert _tool().prompt == get_prompt(list(DEFS.values()), False, default_agent_type=None,
                                        background_tasks_enabled=True)
    assert _tool().prompt != get_prompt(list(DEFS.values()), False, default_agent_type=None)


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", " ON "])
def test_every_truthy_value_disables_background_tasks(monkeypatch, value):
    monkeypatch.setenv("TDDAGENTS_DISABLE_BACKGROUND_TASKS", value)
    assert "run_in_background" not in _tool().input_schema["properties"]


def test_the_background_refusal_explains_itself():
    from app.loop.agents.tool import refuse_background_phase_agent

    with pytest.raises(ValueError) as err:
        refuse_background_phase_agent(TESTER, True)
    assert str(err.value) == (
        "Agent 'tester' declares TDD phase RED and cannot run in the background: a background worker's state "
        "changes are discarded, so its RunTests could not move the phase ledger. Run it in the foreground.")
