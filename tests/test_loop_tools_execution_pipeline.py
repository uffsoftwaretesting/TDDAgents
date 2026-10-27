"""
The tool-execution pipeline order: `app/services/tools/tool_execution.py`.

The individual steps have their own tests. What these pin is the *sequence*, because that
is what upstream's ordering decisions actually encode:

* schema validation before anything else, so a malformed call never reaches a hook or the
  permission gate;
* result governance before PostToolUse, so a hook annotates the output rather than
  rewriting what the tool really produced;
* one phase check, inside the permission gate.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from langchain_core.messages import AIMessage

from app.services.tools.tool_execution import run_tool_use
from app.state.app_state_store import AppState, AppStateStore
from app.tdd.ledger import PhaseLedger, TddPhase
from app.tool import ToolResult, ValidationResult, build_tool, tool_context_for
from app.types.hooks import AggregatedHookResult
from app.types.permissions import ToolPermissionContext
from app.utils.messages import HookBlockingError
from tests.hook_doubles import ScriptedDispatcher

SCHEMA = {
    "type": "object",
    "properties": {"file_path": {"type": "string"}},
    "required": ["file_path"],
}


def call(tool_name: str = "WriteFile", call_id: str = "c1", **args: Any) -> dict[str, Any]:
    return {"name": tool_name, "args": args, "id": call_id}


def context(ledger: PhaseLedger | None = None, **kwargs: Any) -> Any:
    store = AppStateStore(AppState(phase_ledger=ledger)) if ledger is not None else AppStateStore()
    return tool_context_for(
        store,
        permission_context=ToolPermissionContext(should_avoid_permission_prompts=True),
        **kwargs,
    )


def execute(tool: Any, tool_call: Any, ctx: Any) -> Any:
    return asyncio.run(run_tool_use(tool_call, AIMessage(content=""), ctx, [tool]))


def recording_tool(log: list[str], **kwargs: Any) -> Any:
    async def do_call(args: dict[str, Any], ctx: Any) -> ToolResult:
        log.append("call")
        return ToolResult(content="ran")

    def validate(args: dict[str, Any], ctx: Any) -> ValidationResult:
        log.append("validate_input")
        return ValidationResult(valid=True)

    defaults: dict[str, Any] = {
        "name": "WriteFile", "prompt": "p", "call": do_call,
        "input_schema": SCHEMA, "validate_input": validate,
    }
    defaults.update(kwargs)
    return build_tool(**defaults)


def RecordingDispatcher(log: list[str], script: dict[str, Any] | None = None) -> ScriptedDispatcher:
    """A real dispatcher that records the order it was consulted in and replays `script`."""
    return ScriptedDispatcher(log, script)


# -- schema validation comes first ------------------------------------------

def test_a_malformed_call_is_rejected_before_any_hook_or_the_tool():
    log: list[str] = []
    tool = recording_tool(log)
    dispatcher = RecordingDispatcher(log)
    outcome = execute(tool, call(file_path=7), context(hook_dispatcher=dispatcher))

    assert log == []
    assert outcome.message.additional_kwargs["is_error"] is True
    assert "InputValidationError" in str(outcome.message.content)
    assert "`file_path` type is expected as `string`" in str(outcome.message.content)


def test_an_mcp_tools_input_is_left_to_its_server():
    """`MCPTool.inputSchema` is `z.object({}).passthrough()`: the server, not us, validates it."""
    log: list[str] = []
    tool = recording_tool(log, name="mcp__demo__add", is_mcp=True)
    execute(tool, call("mcp__demo__add", file_path=7), context())
    assert log[0] == "validate_input"


def test_the_call_sees_its_own_tool_use_id():
    """`toolExecution.ts`: `tool.call(input, { ...toolUseContext, toolUseId })`."""
    seen: list[Any] = []

    async def do_call(args: dict[str, Any], ctx: Any) -> ToolResult:
        seen.append(ctx.tool_use_id)
        return ToolResult(content="ran")

    ctx = context()
    execute(recording_tool([], name="Probe", call=do_call), call("Probe", call_id="toolu_9", file_path="a.py"), ctx)
    assert seen == ["toolu_9"]
    assert ctx.tool_use_id is None


def test_the_transcript_keeps_the_raw_message_alongside_the_humanized_one():
    tool = recording_tool([])
    outcome = execute(tool, call(), context())
    kwargs = outcome.message.additional_kwargs
    assert kwargs["tool_use_result"].startswith("InputValidationError:")
    assert "<tool_use_error>" in str(outcome.message.content)


def test_a_valid_call_reaches_semantic_validation_then_the_tool():
    log: list[str] = []
    tool = recording_tool(log)
    execute(tool, call(file_path="tests/a.py"), context())
    assert log == ["validate_input", "call"]


def test_a_semantic_failure_stops_before_the_tool_and_hides_the_error_code():
    log: list[str] = []

    def refuse(args: dict[str, Any], ctx: Any) -> ValidationResult:
        log.append("validate_input")
        return ValidationResult(valid=False, message="file was not read first", error_code=4)

    tool = recording_tool(log, validate_input=refuse)
    outcome = execute(tool, call(file_path="tests/a.py"), context())

    assert log == ["validate_input"]
    assert "file was not read first" in str(outcome.message.content)
    # `errorCode` is analytics-only upstream and never shown to the model.
    assert "4" not in str(outcome.message.content)
    assert outcome.message.additional_kwargs["error_code"] == 4


# -- hooks, then permissions -------------------------------------------------

def test_the_pre_hook_runs_before_the_tool_and_the_post_hook_after_it():
    log: list[str] = []
    tool = recording_tool(log)
    dispatcher = RecordingDispatcher(log)
    execute(tool, call(file_path="tests/a.py"), context(hook_dispatcher=dispatcher))
    assert log == ["validate_input", "PreToolUse", "call", "PostToolUse"]


def test_a_hook_veto_stops_the_call():
    log: list[str] = []
    tool = recording_tool(log)
    dispatcher = RecordingDispatcher(log, {"PreToolUse": [AggregatedHookResult(
        blocking_error=HookBlockingError(blocking_error="policy says no", command="vet.sh"))]})
    outcome = execute(tool, call(file_path="tests/a.py"), context(hook_dispatcher=dispatcher))

    assert "call" not in log
    assert str(outcome.message.content) == "PreToolUse:WriteFile hook error: policy says no"
    assert outcome.message.additional_kwargs["permission_behavior"] == "deny"


def test_a_hook_rewrite_without_a_verdict_reaches_the_tool():
    seen: dict[str, Any] = {}

    async def do_call(args: dict[str, Any], ctx: Any) -> ToolResult:
        seen.update(args)
        return ToolResult(content="ran")

    tool = recording_tool([], call=do_call)
    dispatcher = RecordingDispatcher([], {"PreToolUse": [AggregatedHookResult(
        updated_input={"file_path": "tests/rewritten.py"})]})
    execute(tool, call(file_path="tests/a.py"), context(hook_dispatcher=dispatcher))
    assert seen == {"file_path": "tests/rewritten.py"}


def test_a_hook_allow_cannot_defeat_the_phase_rules():
    """The one that matters: barrier 2 is not negotiable by a hook."""
    log: list[str] = []
    tool = recording_tool(log)
    dispatcher = RecordingDispatcher(log, {"PreToolUse": [AggregatedHookResult(permission_behavior="allow")]})
    outcome = execute(
        tool, call(file_path="calculator.py"),
        context(PhaseLedger(phase=TddPhase.RED), hook_dispatcher=dispatcher),
    )
    assert "call" not in log
    assert "RED denies" in str(outcome.message.content)


def test_the_phase_is_checked_exactly_once_in_the_gate():
    """
    The duplicate PreToolUse phase check is gone, so a context with no hook dispatcher is
    still protected — the gate is the single enforcement point.
    """
    log: list[str] = []
    tool = recording_tool(log)
    outcome = execute(tool, call(file_path="calculator.py"), context(PhaseLedger(phase=TddPhase.RED)))
    assert "call" not in log
    assert "RED denies" in str(outcome.message.content)


# -- governance before PostToolUse ------------------------------------------

def test_an_oversized_result_is_filed_before_a_hook_sees_it(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "config"))
    big = "x" * 3000

    async def do_call(args: dict[str, Any], ctx: Any) -> ToolResult:
        return ToolResult(content=big)

    tool = recording_tool([], call=do_call, max_result_size_chars=1000)
    dispatcher = RecordingDispatcher([])
    outcome = execute(
        tool, call(file_path="tests/a.py"),
        context(hook_dispatcher=dispatcher, run_id="run-1"),
    )

    assert "<persisted-output>" in str(outcome.message.content)
    # The hook was shown the governed output, not the raw 3000 characters.
    post = dispatcher.payload("PostToolUse")
    assert "<persisted-output>" in post["tool_response"]
    assert big not in post["tool_response"]


def test_a_hook_can_annotate_a_result_but_not_replace_it():
    async def do_call(args: dict[str, Any], ctx: Any) -> ToolResult:
        return ToolResult(content="the real output")

    tool = recording_tool([], call=do_call)
    dispatcher = RecordingDispatcher([], {"PostToolUse": [AggregatedHookResult(
        additional_contexts=("mind the style guide",))]})
    outcome = execute(tool, call(file_path="tests/a.py"), context(hook_dispatcher=dispatcher))

    # The result stands untouched; the hook's context follows it as its own message.
    assert str(outcome.message.content) == "the real output"
    (extra,) = outcome.extra_messages
    assert extra.content == (
        "<system-reminder>\nPostToolUse:WriteFile hook additional context: mind the style guide\n</system-reminder>"
    )


def test_a_result_under_the_limit_is_left_exactly_as_the_tool_produced_it():
    async def do_call(args: dict[str, Any], ctx: Any) -> ToolResult:
        return ToolResult(content="small")

    tool = recording_tool([], call=do_call, max_result_size_chars=1000)
    outcome = execute(tool, call(file_path="tests/a.py"), context())
    assert str(outcome.message.content) == "small"


def test_a_tool_that_opted_out_is_never_filed(tmp_path, monkeypatch):
    import math

    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "config"))
    big = "y" * 100_000

    async def do_call(args: dict[str, Any], ctx: Any) -> ToolResult:
        return ToolResult(content=big)

    tool = recording_tool([], call=do_call, max_result_size_chars=math.inf)
    outcome = execute(tool, call(file_path="tests/a.py"), context(run_id="run-1"))
    assert str(outcome.message.content) == big


# -- failures -----------------------------------------------------------------

def test_an_unknown_tool_is_reported_without_running_anything():
    outcome = execute(recording_tool([]), call("NoSuchTool"), context())
    assert "No such tool available: NoSuchTool" in str(outcome.message.content)


def test_a_tool_that_raises_becomes_an_error_result_not_an_exception():
    async def explode(args: dict[str, Any], ctx: Any) -> ToolResult:
        raise RuntimeError("disk on fire")

    tool = recording_tool([], call=explode)
    outcome = execute(tool, call(file_path="tests/a.py"), context())
    assert "disk on fire" in str(outcome.message.content)
    assert outcome.message.additional_kwargs["is_error"] is True


def test_a_cancelled_context_refuses_before_validation():
    log: list[str] = []
    tool = recording_tool(log)
    ctx = context()
    ctx.cancel.cancelled = True
    outcome = execute(tool, call(file_path="tests/a.py"), ctx)
    assert log == []
    assert "STOP what you are doing" in str(outcome.message.content)


# ── how governance locates the store ────────────────────────────────────────

def test_the_result_is_filed_under_the_contexts_run_and_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "config"))
    from app.utils.tool_result_storage import tool_results_dir

    async def do_call(args: dict[str, Any], ctx: Any) -> ToolResult:
        return ToolResult(content="z" * 3000)

    tool = recording_tool([], call=do_call, max_result_size_chars=1000)
    workspace = SimpleNamespace(root=tmp_path / "ws")
    ctx = context(run_id="run-9", workspace=workspace)
    execute(tool, call(file_path="tests/a.py"), ctx)

    expected = tool_results_dir("run-9", str(tmp_path / "ws")) / "c1.txt"
    assert expected.exists() and expected.read_text() == "z" * 3000


def test_a_context_without_a_run_id_still_files_the_result(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "config"))
    from app.utils.tool_result_storage import tool_results_dir

    async def do_call(args: dict[str, Any], ctx: Any) -> ToolResult:
        return ToolResult(content="z" * 3000)

    tool = recording_tool([], call=do_call, max_result_size_chars=1000)
    execute(tool, call(file_path="tests/a.py"), context())
    assert (tool_results_dir("run", ".") / "c1.txt").exists()


def test_a_tool_that_declares_no_limit_is_governed_by_the_global_budget():
    """The fallback must be a number, not None — `math.isfinite(None)` would raise."""
    from app.services.tools.tool_execution import _persist_if_oversized
    from app.utils.tool_result_storage import MAX_TOOL_RESULT_BYTES

    bare = SimpleNamespace(name="Bare")
    assert not hasattr(bare, "max_result_size_chars")
    result = _persist_if_oversized(ToolResult(content="small"), bare, "Bare", "c1", context())
    assert result.content == "small"
    assert MAX_TOOL_RESULT_BYTES > 0


def test_the_persisted_file_is_readable_back_through_read_file(tmp_path, monkeypatch):
    """
    The preview advertises a path; ReadFile must be able to open it, or the path is
    decoration. Ours is workspace-bounded, so the store is an explicit extra read root.
    """
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "config"))
    from app.tools.utils import read_text
    from app.utils.tool_result_storage import persist_tool_result

    path = persist_tool_result("the whole thing", "toolu_1", "run-1", "/proj")
    assert path is not None
    assert read_text(None, str(path)) == "the whole thing"


def test_a_path_outside_the_store_is_not_read_off_the_host(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "config"))
    from app.tools.utils import is_persisted_result_path

    secret = tmp_path / "secret.txt"
    secret.write_text("private")
    assert is_persisted_result_path(str(secret)) is False


# ── what the hooks are actually told ────────────────────────────────────────

def test_the_pre_hook_payload_names_the_tool_and_its_input():
    dispatcher = RecordingDispatcher([])
    tool = recording_tool([])
    execute(tool, call(file_path="tests/a.py"), context(hook_dispatcher=dispatcher))

    event, payload = dispatcher.seen[0]
    assert event == "PreToolUse"
    assert payload["tool_name"] == "WriteFile"
    assert payload["tool_input"] == {"file_path": "tests/a.py"}
    assert payload["tool_use_id"] == "c1"
    assert "command" not in payload


def test_a_bash_call_hands_the_hook_the_command_itself():
    """A hook that vets shell commands needs the command, not just the tool name."""
    async def do_call(args: dict[str, Any], ctx: Any) -> ToolResult:
        return ToolResult(content="ok")

    tool = recording_tool([], name="Bash", call=do_call, input_schema={
        "type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]})
    dispatcher = RecordingDispatcher([])
    execute(tool, call("Bash", command="ls -la"), context(hook_dispatcher=dispatcher))

    pre = dispatcher.payload("PreToolUse")
    assert pre["tool_input"] == {"command": "ls -la"}
    assert pre["tool_name"] == "Bash"


def test_the_post_hook_payload_names_the_tool_and_carries_the_result():
    async def do_call(args: dict[str, Any], ctx: Any) -> ToolResult:
        return ToolResult(content="what the tool produced")

    tool = recording_tool([], call=do_call)
    dispatcher = RecordingDispatcher([])
    execute(tool, call(file_path="tests/a.py"), context(hook_dispatcher=dispatcher))

    post = dispatcher.payload("PostToolUse")
    assert post["tool_name"] == "WriteFile"
    assert post["tool_response"] == "what the tool produced"
    assert post["tool_input"] == {"file_path": "tests/a.py"}


def test_the_post_hook_sees_the_input_the_tool_was_actually_called_with():
    dispatcher = RecordingDispatcher([], {"PreToolUse": [AggregatedHookResult(
        updated_input={"file_path": "tests/rewritten.py"})]})
    tool = recording_tool([])
    execute(tool, call(file_path="tests/a.py"), context(hook_dispatcher=dispatcher))

    post = dispatcher.payload("PostToolUse")
    assert post["tool_input"] == {"file_path": "tests/rewritten.py"}


def test_an_empty_result_is_named_rather_than_left_blank(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "config"))

    async def do_call(args: dict[str, Any], ctx: Any) -> ToolResult:
        return ToolResult(content="")

    tool = recording_tool([], call=do_call, max_result_size_chars=1000)
    outcome = execute(tool, call(file_path="tests/a.py"), context(run_id="r"))
    assert str(outcome.message.content) == "(WriteFile completed with no output)"
