"""
Skill tool implementation conforming to §4.6 and Part J4.

Ported from:
- `reference/claude-code/src/tools/SkillTool/SkillTool.ts` -> `SkillTool`, execution semantics
- `reference/claude-code/src/tools/SkillTool/prompt.ts` -> `getPrompt`, progressive disclosure instructions
"""

from __future__ import annotations

import logging
from typing import Any

from app.loop.context import ToolContext
from app.loop.skills.loader import SkillRegistry
from app.loop.tools.base import BuiltTool, ToolResult, build_tool

logger = logging.getLogger(__name__)

SKILL_TOOL_PROMPT = """Execute a skill within the conversation.

When tasks match any of the available skills, invoke this tool. Skills provide
specialized engineering capabilities, domain knowledge, and step-by-step procedures.

How to invoke:
- Use this tool with the skill name and optional arguments.
- Example: `skill_name: "tdd-test-design", args: "tests/test_calculator.py"`
- Example: `skill_name: "tdd-refactor-clean"`

Important:
- When a skill matches the current task, invoking this tool loads its specialized instructions
  and reference guidance into your context window.
- The instructions will appear in the tool response. Follow them directly once loaded."""


def substitute_arguments(body: str, args: str) -> str:
    """
    Substitutes $ARGUMENTS and {{args}} with the provided argument string.
    """
    res = body.replace("$ARGUMENTS", args)
    res = res.replace("{{args}}", args)
    res = res.replace("{{ARGUMENTS}}", args)
    return res


def build_skill_tool(registry: SkillRegistry) -> BuiltTool:
    """
    Build the Skill tool wired to a SkillRegistry (Part J4).
    """

    async def call_skill(input_dict: dict[str, Any], context: ToolContext) -> ToolResult:
        skill_name = str(input_dict.get("skill_name") or input_dict.get("skill") or "").strip()
        args = str(input_dict.get("args") or "").strip()

        if not skill_name:
            return ToolResult(
                content="Error: 'skill_name' parameter is required.",
                is_error=True,
            )

        skill = registry.get(skill_name)
        if skill is None:
            available = ", ".join(f"'{s}'" for s in registry.names()) or "none"
            return ToolResult(
                content=f"Skill '{skill_name}' not found. Available skills: {available}.",
                is_error=True,
            )

        # Build execution output
        final_body = substitute_arguments(skill.body, args)

        parts: list[str] = [
            f'<command-message name="{skill.name}">',
        ]

        if skill.base_dir:
            parts.append(f"Base directory for this skill: {skill.base_dir}\n")

        parts.append(final_body)

        # Include loaded reference materials if present
        if skill.references:
            parts.append("\n---\n### Reference Documentation:")
            for ref_name, ref_content in sorted(skill.references.items()):
                parts.append(f"\n#### {ref_name}\n{ref_content}")

        parts.append("</command-message>")

        content = "\n".join(parts)
        return ToolResult(content=content, is_error=False)

    return build_tool(
        name="Skill",
        prompt=SKILL_TOOL_PROMPT,
        call=call_skill,
        input_schema={
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "The unique name of the skill to execute.",
                },
                "args": {
                    "type": "string",
                    "description": "Optional arguments or parameters for the skill.",
                },
            },
            "required": ["skill_name"],
        },
        is_read_only=lambda a: True,
        is_concurrency_safe=lambda a: True,
    )
