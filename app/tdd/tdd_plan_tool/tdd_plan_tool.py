"""
`UpdatePlan`: the model's way to change the session's TDD plan (Phase 11, TDDAgents).

Modelled on `TodoWrite` (`app/tools/todo_write_tool/todo_write_tool.py`) for the state write,
and on upstream's `ExitPlanModeV2Tool` (`reference/claude-code/src/tools/ExitPlanModeTool/
ExitPlanModeV2Tool.ts`) for the approval: `checkPermissions` returns an 'ask' carrying the
change, and `requiresUserInteraction` keeps that ask even under `bypassPermissions`. An
interactive session shows it to the user; a headless one denies it, so a run with nobody to
approve cannot rewrite its own plan (user decision 2026-10-27).

There is no operation that marks an item done: only `complete_current`, driven by the
ledger, does that (`app/tdd/plan_advance.py`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.tdd.plan import PlanError, TddPlan, render_plan, update_tdd_plan
from app.tool import BuiltTool, ToolContext, ToolResult, ValidationResult, build_tool
from app.types.permissions import PermissionBehavior, PermissionResult
from app.utils.frontmatter_parser import parse_markdown_frontmatter

UPDATE_PLAN_TOOL_NAME = "UpdatePlan"

PROMPT_PATH = Path(__file__).with_name("prompt.md")

OPERATIONS = ("add", "edit", "remove", "move")

UPDATE_PLAN_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "operations": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": list(OPERATIONS)},
                    "id": {"type": "string", "description": "The sub-requirement to edit, remove or move"},
                    "text": {"type": "string", "description": "The sub-requirement's text (add, edit)"},
                    "position": {"type": "integer", "minimum": 1, "description": "1-based place (add, move)"},
                },
                "required": ["op"],
                "additionalProperties": False,
            },
        },
        "reason": {"type": "string", "description": "Why the plan changes, shown to the user"},
    },
    "required": ["operations"],
    "additionalProperties": False,
}


def _required(operation: Mapping[str, Any], key: str) -> Any:
    if operation.get(key) is None:
        raise PlanError(f"'{operation.get('op')}' needs '{key}'.")
    return operation[key]


def apply_operations(plan: TddPlan, operations: Sequence[Mapping[str, Any]]) -> TddPlan:
    """The plan after `operations`, in order; a `PlanError` if any of them cannot apply."""
    for operation in operations:
        op = operation.get("op")
        if op == "add":
            plan = plan.add(str(_required(operation, "text")), operation.get("position"))
        elif op == "edit":
            plan = plan.edit(str(_required(operation, "id")), str(_required(operation, "text")))
        elif op == "remove":
            plan = plan.remove(str(_required(operation, "id")))
        elif op == "move":
            plan = plan.move(str(_required(operation, "id")), int(_required(operation, "position")))
        else:
            raise PlanError(f"Unknown plan operation {op!r}.")
    return plan


def describe_change(before: TddPlan, after: TddPlan, reason: str | None = None) -> str:
    """What the user approves: the plan before and after, and why."""
    parts = ["Update the TDD plan?"]
    if reason:
        parts.append(f"Reason: {reason}")
    parts += ["Before:", render_plan(before), "After:", render_plan(after)]
    return "\n\n".join(parts)


def build_update_plan_tool() -> BuiltTool:
    def proposed(input_args: Mapping[str, Any], context: ToolContext) -> tuple[TddPlan, TddPlan]:
        before = context.get_app_state().tdd_plan
        return before, apply_operations(before, input_args.get("operations") or [])

    def validate(input_args: dict[str, Any], context: ToolContext) -> ValidationResult:
        try:
            proposed(input_args, context)
        except PlanError as exc:
            return ValidationResult(valid=False, message=str(exc), error_code=1)
        return ValidationResult(valid=True)

    def check(input_args: dict[str, Any], context: ToolContext) -> PermissionResult:
        before, after = proposed(input_args, context)
        return PermissionResult(
            behavior=PermissionBehavior.ASK,
            message=describe_change(before, after, input_args.get("reason")),
            decision_reason={"type": "other", "reason": "A plan change needs the user's approval."},
        )

    async def call(input_args: dict[str, Any], context: ToolContext) -> ToolResult:
        operations = input_args.get("operations") or []
        updated = update_tdd_plan(context, lambda plan: apply_operations(plan, operations))
        return ToolResult(content=f"The TDD plan is now:\n{render_plan(updated)}")

    return build_tool(
        name=UPDATE_PLAN_TOOL_NAME,
        prompt=parse_markdown_frontmatter(PROMPT_PATH.read_text(encoding="utf-8"))[1],
        input_schema=UPDATE_PLAN_INPUT_SCHEMA,
        description=lambda args: f"Update the TDD plan ({len(args.get('operations') or [])} changes)",
        requires_user_interaction=lambda: True,
        validate_input=validate,
        check_permissions=check,
        call=call,
    )
