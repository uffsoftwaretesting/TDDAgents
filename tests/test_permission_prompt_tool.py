"""`--permission-prompt-tool <mcp tool>` (`createCanUseToolWithPermissionPrompt`, `getCanUseToolFn`)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.cli.print import (
    PERMISSION_PROMPT_ABORTED,
    PermissionPromptToolError,
    create_can_use_tool_with_permission_prompt,
    get_permission_prompt_tool_can_use_tool,
)
from app.tool import CancelToken, ToolResult
from app.types.permissions import PermissionBehavior, PermissionResult

ASK = PermissionResult(behavior=PermissionBehavior.ASK, message="ask")


class PromptTool:
    name = "mcp__perm__approve"
    is_mcp = True

    def __init__(self, answer: Any, wait: bool = False) -> None:
        self.answer = answer
        self.wait = wait
        self.calls: list[dict[str, Any]] = []

    async def call(self, args: dict[str, Any], context: Any) -> ToolResult:
        self.calls.append(args)
        if self.wait:
            await asyncio.sleep(10)
        return ToolResult(content=self.answer if isinstance(self.answer, str) else json.dumps(self.answer))


def context() -> Any:
    return SimpleNamespace(cancel=CancelToken())


def decide(tool: Any, result: PermissionResult = ASK, ctx: Any = None, input_args: Any = None) -> Any:
    can_use_tool = create_can_use_tool_with_permission_prompt(tool)
    target = SimpleNamespace(name="Bash")
    return asyncio.run(can_use_tool(target, input_args or {"command": "ls"}, result, ctx or context(), "tu1"))


def test_allow_and_deny_pass_through() -> None:
    tool = PromptTool({"behavior": "allow", "updatedInput": {}})
    allowed = PermissionResult(behavior=PermissionBehavior.ALLOW)
    assert decide(tool, allowed) is allowed and tool.calls == []


def test_the_tool_decides_an_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_recheck(*args: Any) -> None:
        return None

    monkeypatch.setattr("app.utils.permissions.permissions.check_rule_based_permissions", no_recheck)
    tool = PromptTool({"behavior": "allow", "updatedInput": {"command": "pwd"}})
    decision = decide(tool)
    assert tool.calls == [{"tool_name": "Bash", "input": {"command": "ls"}, "tool_use_id": "tu1"}]
    assert decision.behavior == PermissionBehavior.ALLOW and decision.updated_input == {"command": "pwd"}
    assert decision.decision_reason["permissionPromptToolName"] == "mcp__perm__approve"
    denied = decide(PromptTool({"behavior": "deny", "message": "no way"}))
    assert (denied.behavior, denied.message) == (PermissionBehavior.DENY, "no way")


def test_a_changed_input_is_rechecked(monkeypatch: pytest.MonkeyPatch) -> None:
    phase = PermissionResult(behavior=PermissionBehavior.DENY, message="RED writes tests only")

    async def recheck(*args: Any) -> PermissionResult:
        return phase

    monkeypatch.setattr("app.utils.permissions.permissions.check_rule_based_permissions", recheck)
    assert decide(PromptTool({"behavior": "allow", "updatedInput": {"command": "rm"}})) is phase


def test_an_abort_denies() -> None:
    ctx = context()
    ctx.cancel.cancel()
    tool = PromptTool({"behavior": "allow", "updatedInput": {}})
    assert decide(tool, ctx=ctx).message == PERMISSION_PROMPT_ABORTED and tool.calls == []

    async def aborting() -> Any:
        ctx2 = context()
        can_use_tool = create_can_use_tool_with_permission_prompt(PromptTool({}, wait=True))
        task = asyncio.ensure_future(can_use_tool(SimpleNamespace(name="Bash"), {}, ASK, ctx2, "tu"))
        await asyncio.sleep(0.01)
        ctx2.cancel.cancel()
        return await task

    result = asyncio.run(aborting())
    assert (result.behavior, result.message) == (PermissionBehavior.DENY, PERMISSION_PROMPT_ABORTED)
    assert result.decision_reason == {"type": "permissionPromptTool", "permissionPromptToolName": "mcp__perm__approve",
                                      "toolResult": None}


def test_an_invalid_answer_is_an_error() -> None:
    with pytest.raises(PermissionPromptToolError, match="Permission prompt tool returned an invalid result"):
        decide(PromptTool(""))


def test_the_tool_is_looked_up_at_the_first_ask(capsys: pytest.CaptureFixture[str]) -> None:
    tools: list[Any] = []
    tool = PromptTool({"behavior": "deny", "message": "x"})
    can_use_tool = get_permission_prompt_tool_can_use_tool("mcp__perm__approve", lambda: tools)
    tools.append(tool)
    target = SimpleNamespace(name="Bash")
    assert asyncio.run(can_use_tool(target, {}, ASK, context(), "tu")).message == "x"
    missing = get_permission_prompt_tool_can_use_tool("mcp__none", lambda: [tool])
    with pytest.raises(SystemExit) as exited:
        asyncio.run(missing(target, {}, ASK, context(), "tu"))
    assert exited.value.code == 1
    assert capsys.readouterr().err == ("Error: MCP tool mcp__none (passed via --permission-prompt-tool) not found. "
                                       "Available MCP tools: mcp__perm__approve\n")
    builtin = SimpleNamespace(name="Bash", is_mcp=False)
    not_mcp = get_permission_prompt_tool_can_use_tool("Bash", lambda: [builtin])
    with pytest.raises(SystemExit):
        asyncio.run(not_mcp(target, {}, ASK, context(), "tu"))
    assert "must be an MCP tool" in capsys.readouterr().err
    none = get_permission_prompt_tool_can_use_tool("x", lambda: [])
    with pytest.raises(SystemExit):
        asyncio.run(none(target, {}, ASK, context(), "tu"))
    assert "Available MCP tools: none" in capsys.readouterr().err


def test_the_prompt_tool_is_hidden_from_the_model(python_workspace: Any, tmp_path: Any) -> None:
    from langchain_core.messages import AIMessage

    from app.cli.print import HeadlessOptions, run_headless
    from tests.test_stream_json import Model, run_options

    model = Model([[AIMessage(content="ok")]])
    written: list[str] = []
    asyncio.run(run_headless("ppt-hidden", "go", workspace=python_workspace,
                             options=HeadlessOptions(permission_prompt_tool="Bash"), write_stdout=written.append,
                             **run_options(tmp_path, model)))
    assert "Bash" not in [t.name for t in model.seen[0].tool_context.tools]
