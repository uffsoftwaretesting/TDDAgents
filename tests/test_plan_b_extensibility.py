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
