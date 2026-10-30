"""
`ExitPlanMode`: the Analyst presents the plan file for the user's approval.

Ported from `reference/claude-code/src/tools/ExitPlanModeTool/ExitPlanModeV2Tool.ts`
(`ExitPlanModeV2Tool`: `validateInput`, `checkPermissions`, `requiresUserInteraction`, `call`,
`mapToolResultToToolResultBlockParam`) and `prompt.ts` (`EXIT_PLAN_MODE_V2_TOOL_PROMPT`, the
external stub). The plan is read from the session's plan file (`app/utils/plans.py`), not taken
as a parameter. `checkPermissions` always asks and `requiresUserInteraction` keeps that ask under
every mode, so the REPL shows the plan with upstream's choices
(`app/hooks/use_can_use_tool.py`); a run with nobody to ask denies it, as upstream's `-p` does.
An edit the user made at the approval arrives as `input["plan"]`, is written back to the file,
and labels the result "Approved Plan (edited by user)".

*TDD addition:* the approved plan's `Implementation Plan` section becomes the session's
sub-requirements (`app/tdd/plan.py`: `parse_implementation_plan`, `replace_pending` through
`update_tdd_plan`, which restarts the ledger in RED): the items already done stay done, the
pending ones are replaced. The result then ends with how development works and the TDD plan
(`development_approved_prompt`). A plan with no such items is refused before the approval (`validate_input`)
and at the call, so the model keeps planning.

*Divergences:* no teammates or team-lead approval (`isTeammate`, `isPlanModeRequired`, the mailbox),
no `allowedPrompts` permission rules (upstream adds them only with the classifier permissions
on), no auto-mode circuit breaker on restore (plan with auto is not ported), no Ultraplan, and no
TeamCreate hint.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.tdd.plan import (
    IMPLEMENTATION_PLAN_HEADING,
    parse_implementation_plan,
    update_tdd_plan,
)
from app.tool import BuiltTool, ToolContext, ToolResult, ValidationResult, build_tool
from app.types.permissions import PermissionBehavior, PermissionMode, PermissionResult
from app.utils.frontmatter_parser import parse_markdown_frontmatter
from app.utils.permissions.permission_setup import set_permission_mode

EXIT_PLAN_MODE_TOOL_NAME = "ExitPlanMode"

PROMPT_PATH = Path(__file__).with_name("prompt.md")

#: `validateInput` outside plan mode.
NOT_IN_PLAN_MODE = (
    "You are not in plan mode. This tool is only for exiting plan mode after writing a plan. If your plan was "
    "already approved, continue with implementation."
)

#: `checkPermissions`' ask.
EXIT_PLAN_MODE_QUESTION = "Exit plan mode?"

#: `mapToolResultToToolResultBlockParam` for a subagent.
AGENT_APPROVED = 'User has approved the plan. There is nothing else needed from you now. Please respond with "ok"'

#: `mapToolResultToToolResultBlockParam` for an empty plan.
EMPTY_PLAN_APPROVED = "User has approved exiting plan mode. You can now proceed."

_ALLOWED_PROMPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tool": {"type": "string", "enum": ["Bash"], "description": "The tool this prompt applies to"},
        "prompt": {"type": "string", "description": (
            'Semantic description of the action, e.g. "run tests", "install dependencies"')},
    },
    "required": ["tool", "prompt"],
}

#: `inputSchema` (a strict object with passthrough): only `allowedPrompts` is the model's.
EXIT_PLAN_MODE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "allowedPrompts": {
            "type": "array",
            "items": _ALLOWED_PROMPT_SCHEMA,
            "description": ("Prompt-based permissions needed to implement the plan. These describe categories of "
                            "actions rather than specific commands."),
        },
    },
}


def no_sub_requirements_message(file_path: str) -> str:
    """Why a plan without sub-requirements cannot be approved (the TDD addition)."""
    return (
        f"The plan file at {file_path} has no numbered sub-requirements under a "
        f'"{IMPLEMENTATION_PLAN_HEADING}" heading. TDDAgents runs each numbered item of that section as one '
        "sub-requirement, so the plan cannot be approved without it."
    )


def approved_plan_text(plan: str | None, file_path: str, *, is_agent: bool, edited: bool) -> str:
    """`mapToolResultToToolResultBlockParam` (no teammate, no TeamCreate hint)."""
    if is_agent:
        return AGENT_APPROVED
    if not plan or not plan.strip():
        return EMPTY_PLAN_APPROVED
    label = "Approved Plan (edited by user)" if edited else "Approved Plan"
    return (
        "User has approved your plan. You can now start coding. Start with updating your todo list if applicable\n\n"
        f"Your plan has been saved to: {file_path}\n"
        "You can refer back to it if needed during implementation.\n\n"
        f"## {label}:\n{plan}"
    )


def _mode(context: ToolContext) -> PermissionMode:
    from app.utils.permissions.permissions import extract_permission_context

    return extract_permission_context(context).mode


def build_exit_plan_mode_tool() -> BuiltTool:
    def validate(input_args: dict[str, Any], context: ToolContext) -> ValidationResult:
        if _mode(context) != PermissionMode.PLAN:
            return ValidationResult(valid=False, message=NOT_IN_PLAN_MODE, error_code=1)
        plans = context.session_plans
        if context.agent_id or plans is None:
            return ValidationResult(valid=True)
        plan = plans.read()
        if not parse_implementation_plan(plan or ""):
            return ValidationResult(valid=False, message=no_sub_requirements_message(str(plans.file_path())),
                                    error_code=2)
        return ValidationResult(valid=True)

    def check(input_args: dict[str, Any], context: ToolContext) -> PermissionResult:
        return PermissionResult(behavior=PermissionBehavior.ASK, message=EXIT_PLAN_MODE_QUESTION,
                                updated_input=input_args)

    async def call(input_args: dict[str, Any], context: ToolContext) -> ToolResult:
        agent_id = context.agent_id
        plans = context.session_plans
        if plans is None:
            raise RuntimeError("This session has no plan file.")
        file_path = plans.file_path(agent_id)
        input_plan = input_args.get("plan") if isinstance(input_args.get("plan"), str) else None
        plan = input_plan if input_plan is not None else plans.read(agent_id)
        items = parse_implementation_plan(plan or "") if not agent_id else []
        if not agent_id and not items:
            raise RuntimeError(no_sub_requirements_message(str(file_path)))
        if input_plan is not None:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(input_plan, encoding="utf-8")

        def restore(state: Any) -> Any:
            context_ = state.tool_permission_context
            if context_.mode != PermissionMode.PLAN:
                return state
            return set_permission_mode(state, context_.pre_plan_mode or PermissionMode.DEFAULT)

        context.set_app_state(restore)
        content = approved_plan_text(plan, str(file_path), is_agent=bool(agent_id), edited=input_plan is not None)
        if items:
            from app.session.prompts import development_approved_prompt

            installed = update_tdd_plan(context, lambda current: current.replace_pending(items))
            content = f"{content}\n\n{development_approved_prompt(installed)}"
        return ToolResult(
            content=content,
            metadata={"plan": plan, "filePath": str(file_path), "isAgent": bool(agent_id),
                      "planWasEdited": input_plan is not None, "subRequirements": items},
        )

    return build_tool(
        name=EXIT_PLAN_MODE_TOOL_NAME,
        prompt=parse_markdown_frontmatter(PROMPT_PATH.read_text(encoding="utf-8"))[1],
        input_schema=EXIT_PLAN_MODE_INPUT_SCHEMA,
        description=lambda args: "Prompts the user to exit plan mode and start coding",
        is_concurrency_safe=lambda args: True,
        is_read_only=lambda args: False,
        requires_user_interaction=lambda: True,
        validate_input=validate,
        check_permissions=check,
        call=call,
        max_result_size_chars=100_000,
    )


__all__ = [
    "AGENT_APPROVED",
    "EMPTY_PLAN_APPROVED",
    "EXIT_PLAN_MODE_INPUT_SCHEMA",
    "EXIT_PLAN_MODE_QUESTION",
    "EXIT_PLAN_MODE_TOOL_NAME",
    "NOT_IN_PLAN_MODE",
    "approved_plan_text",
    "build_exit_plan_mode_tool",
    "no_sub_requirements_message",
]
