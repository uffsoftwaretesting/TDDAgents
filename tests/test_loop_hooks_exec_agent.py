"""
Tests for the agent-hook executor (`app/utils/hooks/exec_agent_hook.py`), a port of
`execAgentHook.ts`. The runner is a fake, and the production runner is driven with a fake model.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.schemas.hooks import AgentHook
from app.state.app_state_store import AppState, AppStateStore
from app.tdd.ledger import PhaseLedger, TddPhase
from app.tool import CancelToken, tool_context_for
from app.types.permissions import (
    PermissionBehavior,
    PermissionMode,
    PermissionRule,
    PermissionRuleSource,
    ToolPermissionContext,
)
from app.utils.hooks.exec_agent_hook import (
    DEFAULT_AGENT_HOOK_TIMEOUT_MS,
    HOOK_AGENT_DISALLOWED_TOOLS,
    MAX_AGENT_TURNS,
    STRUCTURED_OUTPUT_ENFORCEMENT_MESSAGE,
    STRUCTURED_OUTPUT_INPUT_SCHEMA,
    SYNTHETIC_OUTPUT_TOOL_NAME,
    AgentHookRequest,
    AgentHookRunOutcome,
    StructuredOutputHolder,
    agent_hook_system_prompt,
    build_agent_hook_runner,
    create_structured_output_tool,
    disallowed_hook_agent_tools,
    exec_agent_hook,
    hook_agent_app_state,
    hook_agent_tools,
    structured_output_enforcement,
)
from app.utils.hooks.exec_prompt_hook import SMALL_FAST_MODEL_ENV


def _run(awaitable: Any) -> Any:
    """`asyncio.run` for an awaitable that is not typed as a coroutine."""
    async def wrapper() -> Any:
        return await awaitable

    return asyncio.run(wrapper())


class FakeRunner:
    def __init__(self, outcome: AgentHookRunOutcome | None = None, delay: float = 0.0,
                 error: Exception | None = None, abort_then_raise: bool = False) -> None:
        self.outcome = outcome or AgentHookRunOutcome(structured_output={"ok": True})
        self.delay = delay
        self.error = error
        self.abort_then_raise = abort_then_raise
        self.requests: list[AgentHookRequest] = []

    async def __call__(self, request: AgentHookRequest) -> AgentHookRunOutcome:
        self.requests.append(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.abort_then_raise:
            request.abort.abort("x")
            raise RuntimeError("aborted inside")
        if self.error is not None:
            raise self.error
        return self.outcome


def run(hook: AgentHook, runner: Any, *, signal: Any = None, tool_use_id: str = "tu-1",
        context: Any = "ctx", transcript_path: str = "/tmp/tr.jsonl", default_model: str | None = "small") -> Any:
    return asyncio.run(
        exec_agent_hook(
            hook, "Stop", "Stop", '{"a": 1}', signal, context, tool_use_id, None, "agentX",
            agent_runner=runner, transcript_path=transcript_path, default_model=default_model,
        )
    )


def test_constants_follow_upstream():
    assert MAX_AGENT_TURNS == 50
    assert DEFAULT_AGENT_HOOK_TIMEOUT_MS == 60000
    assert SYNTHETIC_OUTPUT_TOOL_NAME == "StructuredOutput"
    assert STRUCTURED_OUTPUT_INPUT_SCHEMA["required"] == ["ok"]
    assert STRUCTURED_OUTPUT_INPUT_SCHEMA["additionalProperties"] is False


def test_system_prompt_names_transcript_and_tool():
    prompt = agent_hook_system_prompt("/x/t.jsonl")
    assert "available at: /x/t.jsonl\nYou can read this file" in prompt
    assert "using the StructuredOutput tool" in prompt
    assert prompt.startswith("You are verifying a stop condition in TDDAgents.")


def test_request_shape(monkeypatch):
    monkeypatch.delenv(SMALL_FAST_MODEL_ENV, raising=False)
    runner = FakeRunner()
    run(AgentHook(prompt="verify $ARGUMENTS"), runner)
    req = runner.requests[0]
    assert req.prompt == 'verify {"a": 1}'
    assert req.model == "small"
    assert req.tool_context == "ctx"
    assert req.transcript_path == "/tmp/tr.jsonl"
    assert req.system_prompt == agent_hook_system_prompt("/tmp/tr.jsonl")
    assert req.max_turns == MAX_AGENT_TURNS
    assert req.abort.signal.aborted is False


def test_hook_model_overrides_default():
    runner = FakeRunner()
    run(AgentHook(prompt="p", model="big"), runner)
    assert runner.requests[0].model == "big"


def test_ok_true_is_success():
    hook = AgentHook(prompt="p")
    result = run(hook, FakeRunner())
    assert result.outcome == "success" and result.hook is hook
    att = result.attachment
    assert (att.type, att.hook_name, att.tool_use_id, att.hook_event, att.content) == (
        "hook_success", "Stop", "tu-1", "Stop", "")


def test_generated_tool_use_id():
    assert run(AgentHook(prompt="p"), FakeRunner(), tool_use_id="").attachment.tool_use_id.startswith("hook-")


def test_ok_false_is_blocking_without_prevent_continuation():
    hook = AgentHook(prompt="the prompt")
    result = run(hook, FakeRunner(AgentHookRunOutcome(structured_output={"ok": False, "reason": "no tests"})))
    assert result.outcome == "blocking"
    assert result.blocking_error.blocking_error == "Agent hook condition was not met: no tests"
    assert result.blocking_error.command == "the prompt"
    assert result.prevent_continuation is False
    assert result.attachment is None


@pytest.mark.parametrize(
    "outcome",
    [
        AgentHookRunOutcome(structured_output=None),
        AgentHookRunOutcome(structured_output=None, hit_max_turns=True, turn_count=50),
        AgentHookRunOutcome(structured_output={"ok": "maybe"}),
    ],
)
def test_no_valid_output_is_cancelled(outcome):
    result = run(AgentHook(prompt="p"), FakeRunner(outcome))
    assert result.outcome == "cancelled"
    assert result.attachment is None and result.blocking_error is None


def test_runner_error_is_non_blocking():
    result = run(AgentHook(prompt="p"), FakeRunner(error=RuntimeError("kaput")))
    assert result.outcome == "non_blocking_error"
    att = result.attachment
    assert (att.type, att.stderr, att.stdout, att.exit_code) == (
        "hook_non_blocking_error", "Error executing agent hook: kaput", "", 1)


def test_error_after_abort_is_cancelled():
    assert run(AgentHook(prompt="p"), FakeRunner(abort_then_raise=True)).outcome == "cancelled"


def test_timeout_aborts_and_cancels():
    runner = FakeRunner(delay=3)
    result = run(AgentHook(prompt="p", timeout=0.05), runner)
    assert result.outcome == "cancelled"
    assert runner.requests[0].abort.signal.aborted is True


def test_pre_aborted_signal_never_runs():
    token = CancelToken()
    token.cancel()
    runner = FakeRunner()
    assert run(AgentHook(prompt="p"), runner, signal=token).outcome == "cancelled"
    assert runner.requests == []


def test_parent_abort_mid_run_cancels():
    token = CancelToken()
    runner = FakeRunner(delay=3)

    async def go():
        async def later():
            await asyncio.sleep(0.1)
            token.cancel()
        asyncio.ensure_future(later())
        return await exec_agent_hook(
            AgentHook(prompt="p"), "Stop", "Stop", "{}", token, None, "t", None, None,
            agent_runner=runner, default_model="m",
        )

    assert asyncio.run(go()).outcome == "cancelled"
    assert runner.requests[0].abort.signal.aborted is True


# ── production runner pieces ─────────────────────────────────────────────────


def test_structured_output_tool_records_and_allows():
    holder = StructuredOutputHolder()
    tool = create_structured_output_tool(holder)
    assert tool.name == "StructuredOutput"
    assert tool.input_schema == STRUCTURED_OUTPUT_INPUT_SCHEMA
    assert tool.is_read_only({}) is True and tool.is_concurrency_safe({}) is True
    assert tool.max_result_size_chars == 100_000
    perm = asyncio.run(tool.check_permissions({"ok": True}, None))
    assert perm.behavior == "allow" and perm.updated_input == {"ok": True}
    result = asyncio.run(tool.call({"ok": True, "reason": "r"}, None))
    assert result.content == "Structured output provided successfully"
    assert holder.data == {"ok": True, "reason": "r"}
    assert holder.calls == [{"ok": True, "reason": "r"}]


class _T:
    def __init__(self, name: str) -> None:
        self.name = name


def test_hook_agent_tools_drops_disallowed_keeps_the_rest():
    ours = _T("StructuredOutput")
    names_in = ("ReadFile", "WriteFile", "Agent", "Task", "Bash", "AskUserQuestion", "TaskStop",
                "TaskOutput", "EnterPlanMode", "ExitPlanMode", "RunTests", "StructuredOutput", "Edit")
    pool = hook_agent_tools([_T(n) for n in names_in], ours)
    assert [t.name for t in pool] == ["ReadFile", "WriteFile", "Bash", "RunTests", "Edit", "StructuredOutput"]
    assert pool[-1] is ours
    assert HOOK_AGENT_DISALLOWED_TOOLS == frozenset({"Agent", "Task"})
    assert disallowed_hook_agent_tools() >= {"Agent", "Task", "AskUserQuestion", "TaskStop", "TaskOutput",
                                             "EnterPlanMode", "ExitPlanMode"}


def test_hook_agent_app_state_forces_dont_ask_and_transcript_rule():
    existing = PermissionRule(tool_name="Bash", rule_content="ls")
    base = AppState(tool_permission_context=ToolPermissionContext(
        mode=PermissionMode.ACCEPT_EDITS, always_allow_rules=(existing,)))
    state = hook_agent_app_state(lambda: base, "/tmp/t.jsonl")()
    perms = state.tool_permission_context
    assert perms.mode == PermissionMode.DONT_ASK
    assert perms.always_allow_rules[0] == existing
    assert perms.always_allow_rules[1] == PermissionRule(
        tool_name="Read", rule_behavior=PermissionBehavior.ALLOW,
        rule_content="//tmp/t.jsonl", source=PermissionRuleSource.SESSION)
    assert base.tool_permission_context.mode == PermissionMode.ACCEPT_EDITS


def test_enforcement_blocks_until_output():
    holder = StructuredOutputHolder()
    hook = structured_output_enforcement(holder)
    res = _run(hook(None, None))
    assert res.prevent_continuation is False
    assert [m.content for m in res.blocking_errors] == [STRUCTURED_OUTPUT_ENFORCEMENT_MESSAGE]
    assert STRUCTURED_OUTPUT_ENFORCEMENT_MESSAGE == (
        "You MUST call the StructuredOutput tool to complete this request. Call this tool now.")
    holder.data = {"ok": True}
    res2 = _run(hook(None, None))
    assert res2.blocking_errors == () and res2.prevent_continuation is False


# ── production runner, driven with a scripted model ──────────────────────────


def _parent_context(ledger: PhaseLedger | None = None):
    from app.cli.print import build_builtin_tools

    store = AppStateStore(AppState(phase_ledger=ledger or PhaseLedger()))
    return store, tool_context_for(store, tools=tuple(build_builtin_tools()), agent_id=None)


def _scripted(turns: list[list[AIMessage]], seen: list[Any]):
    def factory(system_prompt: str):
        async def call(state, config):
            seen.append((system_prompt, config.model, state))
            for m in turns.pop(0) if turns else [AIMessage(content="still thinking")]:
                yield m
        return call
    return factory


def _so_call(cid: str, args: dict[str, Any]) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"id": cid, "name": "StructuredOutput", "args": args}])


def test_runner_returns_structured_output_and_isolates_parent():
    store, parent = _parent_context()
    seen: list[Any] = []
    runner = build_agent_hook_runner(_scripted([[_so_call("c1", {"ok": False, "reason": "r"})]], seen))
    result = asyncio.run(exec_agent_hook(
        AgentHook(prompt="check $ARGUMENTS", model="m1"), "Stop", "Stop", "{}", None, parent, "t", None, None,
        agent_runner=runner, transcript_path="/tmp/t.jsonl",
    ))
    assert result.outcome == "blocking"
    system_prompt, model, state = seen[0]
    assert system_prompt == agent_hook_system_prompt("/tmp/t.jsonl")
    assert model == "m1"
    ctx = state.tool_context
    parent_names = [t.name for t in parent.tools]
    assert [t.name for t in ctx.tools] == [*parent_names, "StructuredOutput"]
    assert ctx.get_app_state().tool_permission_context.mode == PermissionMode.DONT_ASK
    assert ctx.agent_id.startswith("hook-agent-")
    assert ctx.cancel is not parent.cancel
    assert isinstance(state.messages[0], HumanMessage) and state.messages[0].content == "check {}"
    assert store.get().tool_permission_context.mode == PermissionMode.DEFAULT
    assert parent.cancel.cancelled is False


def test_runner_outcome_fields_and_abort():
    _, parent = _parent_context()
    runner = build_agent_hook_runner(_scripted([[_so_call("c1", {"ok": True})]], []))
    req = AgentHookRequest(prompt="p", system_prompt="s", model="m", tool_context=parent,
                           transcript_path="/t", abort=__import__("app.utils.abort_controller",
                                                                  fromlist=["AbortController"]).AbortController())
    out = _run(runner(req))
    assert out.structured_output == {"ok": True}
    assert out.hit_max_turns is False
    assert out.turn_count == 1
    assert req.abort.signal.aborted is True


def test_runner_enforcement_nudges_until_tool_called():
    _, parent = _parent_context()
    seen: list[Any] = []
    turns = [[AIMessage(content="done, I think")], [_so_call("c2", {"ok": True})]]
    runner = build_agent_hook_runner(_scripted(turns, seen))
    result = asyncio.run(exec_agent_hook(
        AgentHook(prompt="p"), "Stop", "Stop", "{}", None, parent, "t", None, None,
        agent_runner=runner, default_model="m",
    ))
    assert result.outcome == "success"
    second_state = seen[1][2]
    assert second_state.messages[-1].content == STRUCTURED_OUTPUT_ENFORCEMENT_MESSAGE


def test_runner_tdd_stop_hook_does_not_apply():
    """A RED ledger would make the TDD stop hook block; the verifier must not be held to it."""
    _, parent = _parent_context(PhaseLedger(phase=TddPhase.RED))
    runner = build_agent_hook_runner(_scripted([[_so_call("c1", {"ok": True})]], []))
    result = asyncio.run(exec_agent_hook(
        AgentHook(prompt="p"), "Stop", "Stop", "{}", None, parent, "t", None, None,
        agent_runner=runner, default_model="m",
    ))
    assert result.outcome == "success"


def test_runner_hits_max_turns():
    from app.utils.abort_controller import AbortController

    _, parent = _parent_context()
    runner = build_agent_hook_runner(_scripted([], []))
    req = AgentHookRequest(prompt="p", system_prompt="s", model="m", tool_context=parent,
                           transcript_path="/t", abort=AbortController(), max_turns=3)
    out = _run(runner(req))
    assert out.structured_output is None
    assert out.hit_max_turns is True
    assert out.turn_count == 3
    assert req.abort.signal.aborted is True


def test_runner_invalid_structured_output_is_rejected_by_schema():
    _, parent = _parent_context()
    turns = [[_so_call("c1", {"ok": "yes"})], [_so_call("c2", {"ok": True})]]
    holder_seen: list[Any] = []
    runner = build_agent_hook_runner(_scripted(turns, holder_seen))
    result = asyncio.run(exec_agent_hook(
        AgentHook(prompt="p"), "Stop", "Stop", "{}", None, parent, "t", None, None,
        agent_runner=runner, default_model="m",
    ))
    assert result.outcome == "success"
    tool_msgs = [m for m in holder_seen[1][2].messages if isinstance(m, ToolMessage)]
    assert tool_msgs and tool_msgs[0].status == "error"


# ── TDD barriers hold inside the verifier ────────────────────────────────────


def _call(cid: str, name: str, args: dict[str, Any]) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"id": cid, "name": name, "args": args}])


def test_verifier_reads_parent_ledger_live():
    store, parent = _parent_context(PhaseLedger(phase=TddPhase.RED))
    get_state = hook_agent_app_state(parent.get_app_state, "/t")
    assert get_state().phase_ledger.phase == TddPhase.RED
    store.update(lambda s: __import__("dataclasses").replace(
        s, phase_ledger=PhaseLedger(phase=TddPhase.GREEN, tests_written=True)))
    assert get_state().phase_ledger.phase == TddPhase.GREEN


def test_verifier_cannot_write_production_path_in_red(tmp_path):
    from app.workspace.local import LocalWorkspace

    ws = LocalWorkspace(tmp_path / "ws")
    from app.cli.print import build_builtin_tools

    store = AppStateStore(AppState(
        phase_ledger=PhaseLedger(phase=TddPhase.RED),
        tool_permission_context=ToolPermissionContext(mode=PermissionMode.BYPASS_PERMISSIONS),
    ))
    parent = tool_context_for(store, tools=tuple(build_builtin_tools()), workspace=ws)
    seen: list[Any] = []
    turns = [
        [_call("w1", "WriteFile", {"file_path": "calculator.py", "content": "def add(a, b):\n    return a + b\n"})],
        [_call("w2", "Bash", {"command": "echo 'x = 1' > calculator.py"})],
        [_so_call("s1", {"ok": True})],
    ]
    runner = build_agent_hook_runner(_scripted(turns, seen))
    result = asyncio.run(exec_agent_hook(
        AgentHook(prompt="p"), "Stop", "Stop", "{}", None, parent, "t", None, None,
        agent_runner=runner, default_model="m",
    ))
    assert result.outcome == "success"
    assert not (tmp_path / "ws" / "calculator.py").exists()
    results = {m.tool_call_id: m for m in seen[-1][2].messages if isinstance(m, ToolMessage)}
    assert results["w1"].status == "error"
    assert str(results["w1"].content).startswith("TDD phase RED denies writing to production file 'calculator.py'")
    assert results["w2"].status == "error"
    assert "through a shell redirection" in results["w2"].content
    assert store.get().phase_ledger.phase == TddPhase.RED


def test_verifier_run_tests_never_moves_parent_ledger():
    from app.cli.print import build_builtin_tools
    from app.tdd.run_tests_tool.run_tests_tool import (
        SuiteExecutionResult,
        build_run_tests_tool,
    )

    runs: list[str] = []

    def failing_suite(path: str, ctx: Any = None) -> SuiteExecutionResult:
        runs.append(path)
        return SuiteExecutionResult(exit_code=1, stdout="1 failed")

    tools = tuple(t for t in build_builtin_tools() if t.name != "RunTests") + (
        build_run_tests_tool(test_runner=failing_suite),)
    store = AppStateStore(AppState(phase_ledger=PhaseLedger(phase=TddPhase.RED)))
    parent = tool_context_for(store, tools=tools, workspace=object())
    before = store.get().phase_ledger
    seen: list[Any] = []
    turns = [[_call("r1", "RunTests", {"test_path": "tests/test_calc.py"})], [_so_call("s1", {"ok": True})]]
    runner = build_agent_hook_runner(_scripted(turns, seen))
    result = asyncio.run(exec_agent_hook(
        AgentHook(prompt="p"), "Stop", "Stop", "{}", None, parent, "t", None, None,
        agent_runner=runner, default_model="m",
    ))
    assert result.outcome == "success"
    assert runs, "the verifier's RunTests must actually have run"
    assert store.get().phase_ledger == before
    assert store.get().phase_ledger.tests_written is False
