"""
Unit tests for Plan A: Subagent Orchestration & Delegation.

Verifies:
1. Dynamic instantiation via create_subagent from PromptRegistry.
2. Variable injection into subagent prompts.
3. Context isolation boundary: ToolContext receives discard_app_state_update.
4. Tool filtering and worker pool assembly for subagents.
5. TDD phase ledger validation.
6. Execution via AgentTool delegation.
"""

from pathlib import Path
from typing import Sequence
import pytest

from langchain_core.messages import HumanMessage

from app.loop.agents.definition import AgentDefinition
from app.loop.agents.subagent import SubagentInstance, create_subagent
from app.loop.agents.tool import (
    AGENT_TOOL_NAME,
    build_agent_tool,
    default_subagent_runner,
)
from app.loop.context import AppState, AppStateStore, CancelToken, ToolContext, discard_app_state_update
from app.loop.ledger import PhaseLedger, TddPhase
from app.loop.messages import Message
from app.loop.prompts.registry import global_prompt_registry
from app.loop.tools.base import Tool, build_tool
from app.loop.tools.types import ToolResult


def _make_dummy_tool(name: str) -> Tool:
    return build_tool(
        name=name,
        prompt=f"Dummy {name}",
        call=lambda args, ctx: ToolResult(content=f"executed {name}"),
    )


def _make_parent_context(
    messages: Sequence[Message] = (),
    tools: Sequence[Tool] = (),
    phase: TddPhase = TddPhase.RED,
) -> tuple[ToolContext, AppStateStore]:
    ledger = PhaseLedger(phase=phase)
    store = AppStateStore(AppState(phase_ledger=ledger))
    ctx = ToolContext(
        cancel=CancelToken(),
        get_app_state=store.get,
        set_app_state=store.update,
        messages=tuple(messages),
        tools=tuple(tools),
    )
    return ctx, store


@pytest.mark.anyio
async def test_dynamic_subagent_creation_from_registry():
    """Verify create_subagent can dynamically instantiate an agent from PromptRegistry."""
    t_read = _make_dummy_tool("ReadFile")
    t_grep = _make_dummy_tool("Grep")
    parent_ctx, parent_store = _make_parent_context(tools=(t_read, t_grep))

    # Look up an agent prompt name that exists in PromptRegistry
    registry_agent_names = global_prompt_registry.list_prompts("agent-prompts")
    assert len(registry_agent_names) > 0

    target_name = "agent-prompt-agent-hook"
    vars_to_inject = {
        "HOOK_EVALUATION_TASK_PROMPT": "Verify condition X",
        "STRUCTURED_OUTPUT_TOOL_NAME": "StructuredOutput",
    }

    subagent = create_subagent(
        target_name,
        parent_context=parent_ctx,
        vars=vars_to_inject,
    )

    assert isinstance(subagent, SubagentInstance)
    assert subagent.definition is not None
    # Check that variables were rendered into prompt
    assert "Verify condition X" in subagent.definition.prompt


@pytest.mark.anyio
async def test_subagent_context_isolation_boundary():
    """Verify child ToolContext has discard_app_state_update and cannot mutate parent state."""
    parent_ctx, parent_store = _make_parent_context(phase=TddPhase.RED)
    initial_phase = parent_store.get().phase_ledger.phase
    assert initial_phase == TddPhase.RED

    dev_def = AgentDefinition(
        name="test_worker",
        description="Worker agent",
        prompt="Do worker stuff",
        phase=TddPhase.RED,
        tools=("ReadFile",),
    )

    subagent = create_subagent(
        dev_def,
        parent_context=parent_ctx,
    )

    # Subagent context MUST have discard_app_state_update
    assert subagent.context.set_app_state is discard_app_state_update

    # Attempting to mutate child state must be discarded
    subagent.context.set_app_state(lambda s: s)
    assert parent_store.get().phase_ledger.phase == initial_phase


@pytest.mark.anyio
async def test_subagent_tool_filtering_and_worker_pool():
    """Verify subagent worker pool contains only allowed tools and never nests AgentTool."""
    t_read = _make_dummy_tool("ReadFile")
    t_write = _make_dummy_tool("WriteFile")
    t_bash = _make_dummy_tool("Bash")
    t_agent = _make_dummy_tool("Agent")

    parent_ctx, _ = _make_parent_context(
        tools=(t_read, t_write, t_bash, t_agent),
        phase=TddPhase.GREEN,
    )

    analyst_def = AgentDefinition(
        name="analyst",
        description="Read only analyst",
        prompt="Analyze code",
        tools=("ReadFile", "Bash"),
        disallowed_tools=("WriteFile",),
    )

    subagent = create_subagent(
        analyst_def,
        parent_context=parent_ctx,
        allow_nested_agent=False,
    )

    tool_names = [t.name for t in subagent.tools]
    assert "ReadFile" in tool_names
    assert "WriteFile" not in tool_names
    assert "Agent" not in tool_names


@pytest.mark.anyio
async def test_subagent_phase_validation_refusal():
    """Verify subagent creation fails or is denied when current phase does not match definition."""
    parent_ctx, _ = _make_parent_context(phase=TddPhase.RED)

    green_def = AgentDefinition(
        name="green_implementer",
        description="Implementer agent",
        prompt="Implement features",
        phase=TddPhase.GREEN,
    )

    with pytest.raises(ValueError, match="requires phase GREEN"):
        create_subagent(
            green_def,
            parent_context=parent_ctx,
        )


@pytest.mark.anyio
async def test_agent_tool_delegation_with_create_subagent():
    """Verify AgentTool delegates using create_subagent logic."""
    t_read = _make_dummy_tool("ReadFile")
    dev_def = AgentDefinition(
        name="developer",
        description="developer agent",
        prompt="Write code",
        phase=TddPhase.GREEN,
        tools=("ReadFile",),
    )
    definitions = {"developer": dev_def}

    agent_tool = build_agent_tool(
        available_tools=(t_read,),
        definitions=definitions,
    )

    parent_ctx, _ = _make_parent_context(tools=(t_read,), phase=TddPhase.GREEN)

    result = await agent_tool.call(
        {"prompt": "Run subagent task", "subagent_type": "developer"},
        parent_ctx,
    )

    assert result.is_error is False
    assert "developer" in result.content
    assert "Scope: Run subagent task" in result.content
