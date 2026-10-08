"""
Unit tests for Plan B: Extensibility Mechanisms (MCP & Skills).

Verifies:
1. TDD PreToolUse and PostToolUse hook dispatchers mapped to TDD Ledger.
2. MCP router and external server integration into assemble_tool_pool.
3. MCP prefix deny rules (e.g. mcp__server).
4. Full skills discovery from skill-prompts registry and SkillTool execution.
"""

from pathlib import Path
from typing import Sequence
import pytest

from app.loop.context import AppState, AppStateStore, CancelToken, ToolContext
from app.loop.ledger import PhaseLedger, TddPhase
from app.loop.skills.loader import SkillRegistry, discover_skills
from app.loop.skills.tool import build_skill_tool
from app.loop.tdd.hooks import tdd_post_tool_use_hook, tdd_pre_tool_use_hook
from app.loop.tools.base import Tool, build_tool
from app.loop.tools.mcp import MCPRouter, build_mcp_tool
from app.loop.tools.pool import assemble_tool_pool
from app.loop.tools.types import ToolResult


def _make_dummy_tool(name: str) -> Tool:
    return build_tool(
        name=name,
        prompt=f"Dummy {name}",
        call=lambda args, ctx: ToolResult(content=f"executed {name}"),
    )


def _make_context(phase: TddPhase = TddPhase.RED) -> ToolContext:
    ledger = PhaseLedger(phase=phase)
    store = AppStateStore(AppState(phase_ledger=ledger))
    return ToolContext(
        cancel=CancelToken(),
        get_app_state=store.get,
        set_app_state=store.update,
    )


# ── Plan B.1: TDD Ledger Hooks ───────────────────────────────────────────────

def test_tdd_pre_tool_use_hook_red_phase():
    """In RED phase, writing implementation code is blocked; test writing is allowed."""
    ledger = PhaseLedger(phase=TddPhase.RED)

    # 1. Implementation writing tool -> DENIED
    outcome = tdd_pre_tool_use_hook("WriteImplementation", {}, ledger)
    assert outcome.denied is True
    assert "RED denies" in outcome.reason

    # 2. Generic writer writing to production file -> DENIED
    outcome = tdd_pre_tool_use_hook("WriteFile", {"path": "app/core.py"}, ledger)
    assert outcome.denied is True
    assert "RED denies writing to production file" in outcome.reason

    # 3. Generic writer writing to test file -> ALLOWED
    outcome = tdd_pre_tool_use_hook("WriteFile", {"path": "tests/test_core.py"}, ledger)
    assert outcome.denied is False

    # 4. RunTests -> ALWAYS ALLOWED
    outcome = tdd_pre_tool_use_hook("RunTests", {}, ledger)
    assert outcome.denied is False


def test_tdd_pre_tool_use_hook_green_phase():
    """In GREEN phase, modifying tests is blocked; implementation writing is allowed."""
    ledger = PhaseLedger(phase=TddPhase.GREEN)

    # 1. Test writer tool -> DENIED
    outcome = tdd_pre_tool_use_hook("WriteTest", {}, ledger)
    assert outcome.denied is True
    assert "GREEN denies" in outcome.reason

    # 2. Generic writer modifying test file -> DENIED
    outcome = tdd_pre_tool_use_hook("WriteFile", {"path": "tests/test_core.py"}, ledger)
    assert outcome.denied is True
    assert "GREEN denies modifying test file" in outcome.reason

    # 3. Generic writer modifying production file -> ALLOWED
    outcome = tdd_pre_tool_use_hook("WriteFile", {"path": "app/core.py"}, ledger)
    assert outcome.denied is False


def test_tdd_post_tool_use_hook_feedback():
    """PostToolUse hook provides structured feedback on test outcomes."""
    ledger_red = PhaseLedger(phase=TddPhase.RED)
    outcome_fail = tdd_post_tool_use_hook(
        "RunTests", {}, "FAILED tests/test_core.py - AssertionError", ledger_red
    )
    assert outcome_fail.additional_context != ""
    assert "RED" in outcome_fail.additional_context

    ledger_green = PhaseLedger(phase=TddPhase.GREEN)
    outcome_pass = tdd_post_tool_use_hook(
        "RunTests", {}, "1 passed in 0.05s", ledger_green
    )
    assert outcome_pass.additional_context != ""
    assert "GREEN" in outcome_pass.additional_context


# ── Plan B.2: MCP Router & External Server Integration ───────────────────────

@pytest.mark.anyio
async def test_mcp_router_and_assemble_tool_pool():
    """MCP router registers tools, formats names as mcp__<server>__<tool>, and assemble_tool_pool sorts them."""
    router = MCPRouter()

    async def dummy_handler(args, ctx):
        return ToolResult(content="mcp response")

    router.register_tool(
        server_name="db",
        tool_name="query",
        description="Run sql query",
        input_schema={"type": "object", "properties": {"sql": {"type": "string"}}},
        handler=dummy_handler,
    )
    router.register_tool(
        server_name="github",
        tool_name="create_issue",
        description="Create issue",
        input_schema={"type": "object"},
        handler=dummy_handler,
    )

    t_read = _make_dummy_tool("ReadFile")
    t_write = _make_dummy_tool("WriteFile")

    # Pass router directly to assemble_tool_pool
    pool = assemble_tool_pool(
        built_in_tools=(t_read, t_write),
        mcp_tools=router,
    )

    names = [t.name for t in pool]
    # Built-ins come first alphabetically
    assert names[0] == "ReadFile"
    assert names[1] == "WriteFile"
    # MCP tools come after built-ins alphabetically
    assert "mcp__db__query" in names
    assert "mcp__github__create_issue" in names
    assert names.index("mcp__db__query") < names.index("mcp__github__create_issue")

    # Test MCP prefix deny rule
    filtered_pool = assemble_tool_pool(
        built_in_tools=(t_read,),
        mcp_tools=router,
        deny_rules=["mcp__db"],
    )
    filtered_names = [t.name for t in filtered_pool]
    assert "mcp__db__query" not in filtered_names
    assert "mcp__github__create_issue" in filtered_names


# ── Plan B.3: Skills Execution & Registry Connection ─────────────────────────

def test_skills_discovery_from_registry():
    """discover_skills loads skills from skill-prompts registry (71 skills available)."""
    registry = discover_skills()
    assert len(registry) >= 70

    # Can find canonical skill name
    skill = registry.get("artifact-diagramming") or registry.get("Skill: Artifact diagramming")
    assert skill is not None
    assert "SVG" in skill.body or "diagram" in skill.body.lower()


@pytest.mark.anyio
async def test_skill_tool_execution():
    """SkillTool can execute discovered skills and substitute arguments."""
    registry = discover_skills()
    skill_tool = build_skill_tool(registry)
    ctx = _make_context()

    # Look up any valid skill in the registry
    first_skill_name = registry.names()[0]
    result = await skill_tool.call(
        {"skill_name": first_skill_name, "args": "my-custom-argument"},
        ctx,
    )
    assert result.is_error is False
    assert f'<command-message name="{first_skill_name}">' in result.content
    assert "</command-message>" in result.content
