"""
`EnterPlanMode`: the model moves the session into plan mode (the Analyst).

Ported from `reference/claude-code/src/tools/EnterPlanModeTool/EnterPlanModeTool.ts`
(`EnterPlanModeTool`, `call`, `mapToolResultToToolResultBlockParam`) and `prompt.ts`
(`getEnterPlanModeToolPromptExternal`, `WHAT_HAPPENS_SECTION`). As upstream it has no permission
check of its own, so the default allow applies, and a worker cannot use it. `call` stashes the
current mode as `prePlanMode` and sets mode `plan` (`app/utils/permissions/permission_setup.py`).

*Divergence:* the KAIROS-channels `isEnabled` gate has no equivalent (always enabled).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.tool import BuiltTool, ToolContext, ToolResult, build_tool
from app.types.permissions import PermissionMode
from app.utils.frontmatter_parser import parse_markdown_frontmatter, render_prompt
from app.utils.permissions.permission_setup import set_permission_mode
from app.utils.plan_mode_v2 import is_plan_mode_interview_phase_enabled

ENTER_PLAN_MODE_TOOL_NAME = "EnterPlanMode"

PROMPT_PATH = Path(__file__).with_name("prompt.md")
WHAT_HAPPENS_PATH = Path(__file__).with_name("what_happens.md")

#: `call`'s message.
ENTERED_MESSAGE = (
    "Entered plan mode. You should now focus on exploring the codebase and designing an implementation approach."
)

#: `mapToolResultToToolResultBlockParam` with the interview phase on.
INTERVIEW_INSTRUCTIONS = (
    "DO NOT write or edit any files except the plan file. Detailed workflow instructions will follow."
)

#: `mapToolResultToToolResultBlockParam` otherwise.
WORKFLOW_INSTRUCTIONS = """In plan mode, you should:
1. Thoroughly explore the codebase to understand existing patterns
2. Identify similar features and architectural approaches
3. Consider multiple approaches and their trade-offs
4. Use AskUserQuestion if you need to clarify the approach
5. Design a concrete implementation strategy
6. When ready, use ExitPlanMode to present your plan for approval

Remember: DO NOT write or edit any files yet. This is a read-only exploration and planning phase."""

AGENT_CONTEXT_ERROR = "EnterPlanMode tool cannot be used in agent contexts"


def get_enter_plan_mode_tool_prompt() -> str:
    """`getEnterPlanModeToolPromptExternal`: the "What Happens" section only without the interview phase."""
    what_happens = "" if is_plan_mode_interview_phase_enabled() else WHAT_HAPPENS_PATH.read_text(encoding="utf-8")
    body = parse_markdown_frontmatter(PROMPT_PATH.read_text(encoding="utf-8"))[1]
    return render_prompt(body, {"WHAT_HAPPENS": what_happens})


def enter_plan_mode_result_text() -> str:
    """`mapToolResultToToolResultBlockParam`."""
    instructions = INTERVIEW_INSTRUCTIONS if is_plan_mode_interview_phase_enabled() else WORKFLOW_INSTRUCTIONS
    return f"{ENTERED_MESSAGE}\n\n{instructions}"


def build_enter_plan_mode_tool() -> BuiltTool:
    async def call(input_args: dict[str, Any], context: ToolContext) -> ToolResult:
        if context.agent_id:
            raise RuntimeError(AGENT_CONTEXT_ERROR)
        context.set_app_state(lambda state: set_permission_mode(state, PermissionMode.PLAN))
        return ToolResult(content=enter_plan_mode_result_text())

    return build_tool(
        name=ENTER_PLAN_MODE_TOOL_NAME,
        prompt=get_enter_plan_mode_tool_prompt(),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        description=lambda args: "Requests permission to enter plan mode for complex tasks requiring exploration "
                                 "and design",
        is_concurrency_safe=lambda args: True,
        is_read_only=lambda args: True,
        call=call,
        max_result_size_chars=100_000,
    )


__all__ = [
    "AGENT_CONTEXT_ERROR",
    "ENTERED_MESSAGE",
    "ENTER_PLAN_MODE_TOOL_NAME",
    "INTERVIEW_INSTRUCTIONS",
    "WORKFLOW_INSTRUCTIONS",
    "build_enter_plan_mode_tool",
    "enter_plan_mode_result_text",
    "get_enter_plan_mode_tool_prompt",
]
