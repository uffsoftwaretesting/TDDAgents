"""Phase 8: a tool's `newMessages` (`toolExecution.ts`: pushed after PostToolUse's attachments,
before the `hook_stopped_continuation` stop), which is how the Skill tool injects a skill."""

from __future__ import annotations

import asyncio

from langchain_core.messages import AIMessage, HumanMessage

from app.services.tools.tool_execution import run_tool_use
from app.state.app_state_store import AppStateStore
from app.tool import ToolResult, build_tool, tool_context_for
from app.utils.messages import ToolCall

SKILL_BODY = HumanMessage(content="skill instructions", additional_kwargs={"is_meta": True})


def _run(result: ToolResult, ctx=None):
    tool = build_tool(name="Skill", prompt="p", call=lambda args, c: result)
    call: ToolCall = {"name": "Skill", "args": {}, "id": "c1"}
    return asyncio.run(run_tool_use(call, AIMessage(content=""), ctx or tool_context_for(AppStateStore()), [tool]))


def test_new_messages_follow_the_tool_result():
    outcome = _run(ToolResult(content="Launching skill: x", new_messages=(SKILL_BODY,)))
    assert outcome.message.content == "Launching skill: x"
    assert outcome.extra_messages == (SKILL_BODY,)


def test_no_new_messages_by_default():
    assert ToolResult(content="x").new_messages == ()
    assert _run(ToolResult(content="x")).extra_messages == ()


def test_new_messages_come_before_a_preventing_hooks_stop(monkeypatch):
    import app.services.tools.tool_execution as execution
    from app.services.tools.tool_hooks import PreToolHooksOutcome

    async def pre(*args, **kwargs):
        return PreToolHooksOutcome(prevent_continuation=True, stop_reason="halt")

    monkeypatch.setattr(execution, "run_pre_tool_use_hooks", pre)
    outcome = _run(ToolResult(content="ok", new_messages=(SKILL_BODY,)))
    assert outcome.extra_messages[0] is SKILL_BODY
    assert outcome.extra_messages[1].additional_kwargs["attachment"]["type"] == "hook_stopped_continuation"
