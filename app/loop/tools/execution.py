"""
Tool call execution and history repair invariant (Parts B4 and B7).

Ported from:
- `reference/claude-code/src/services/tools/toolExecution.ts` -> `runToolUse`
- `reference/claude-code/src/utils/messages.ts` -> `ensureToolResultPairing`, `SYNTHETIC_TOOL_RESULT_PLACEHOLDER`
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator, Sequence

from langchain_core.messages import ToolMessage

from app.loop.messages import Message, ToolCall
from app.loop.permissions.gate import has_permissions_to_use_tool
from app.loop.tools.base import Tool, find_tool_by_name
from app.loop.tools.types import ContextModifier, ToolResult

if TYPE_CHECKING:
    from app.loop.context import ToolContext

SYNTHETIC_TOOL_RESULT_PLACEHOLDER = "[Tool result missing due to internal error]"
CANCEL_MESSAGE = (
    "The user doesn't want to take this action right now. STOP what you are doing and wait "
    "for the user to tell you how to proceed."
)


@dataclass(frozen=True, slots=True)
class ToolExecutionOutcome:
    """
    The outcome of executing a single tool call: its emitted message and optional context modifier.
    """

    message: ToolMessage
    context_modifier: ContextModifier | None = None


async def run_tool_use(
    tool_call: ToolCall,
    assistant_message: Message,
    context: ToolContext,
    tools: Sequence[Tool],
) -> ToolExecutionOutcome:
    """
    Execute a single tool call (Part B4): resolve -> validate -> check permissions -> call -> map result.
    """
    call_id = str(tool_call.get("id") or "")
    tool_name = str(tool_call.get("name") or "")
    tool_args = tool_call.get("args") or {}

    if context.cancel.cancelled:
        msg = ToolMessage(
            content=CANCEL_MESSAGE,
            tool_call_id=call_id,
            status="error",
            additional_kwargs={"is_error": True},
        )
        return ToolExecutionOutcome(message=msg)

    tool = find_tool_by_name(tools, tool_name)
    if tool is None:
        msg = ToolMessage(
            content=f"<tool_use_error>Error: No such tool available: {tool_name}</tool_use_error>",
            tool_call_id=call_id,
            status="error",
            additional_kwargs={"is_error": True},
        )
        return ToolExecutionOutcome(message=msg)

    # Validate input arguments
    validation = tool.validate_input(tool_args, context)
    if not validation.valid:
        error_msg = validation.message or f"Input validation failed for tool {tool_name}"
        msg = ToolMessage(
            content=f"<tool_use_error>{error_msg}</tool_use_error>",
            tool_call_id=call_id,
            status="error",
            additional_kwargs={"is_error": True, "error_code": validation.error_code},
        )
        return ToolExecutionOutcome(message=msg)

    # Run PreToolUse hooks before permission checks
    hook_dispatcher = getattr(context, "hook_dispatcher", None)
    if hook_dispatcher is not None:
        cmd_arg = tool_args.get("command") if isinstance(tool_args, dict) else None
        pre_outcome = hook_dispatcher.run(
            "PreToolUse",
            tool_name=tool_name,
            tool_input=tool_args if isinstance(tool_args, dict) else {},
            command=str(cmd_arg) if cmd_arg is not None else None,
        )
        if pre_outcome.denied:
            denied_msg = pre_outcome.reason or f"Blocked by hook for tool: {tool_name}"
            msg = ToolMessage(
                content=denied_msg,
                tool_call_id=call_id,
                status="error",
                additional_kwargs={"is_error": True, "permission_behavior": "deny"},
            )
            return ToolExecutionOutcome(message=msg)
        if pre_outcome.updated_input is not None:
            tool_args = pre_outcome.updated_input

    # Check tool permissions through the permission gate
    permission = await has_permissions_to_use_tool(tool, tool_args, context)
    if permission.behavior != "allow":
        denied_msg = permission.message or f"Permission denied for tool: {tool_name}"
        msg = ToolMessage(
            content=denied_msg,
            tool_call_id=call_id,
            status="error",
            additional_kwargs={"is_error": True, "permission_behavior": permission.behavior},
        )
        return ToolExecutionOutcome(message=msg)

    effective_args = permission.updated_input if permission.updated_input is not None else tool_args

    # Execute tool call
    try:
        result = await tool.call(effective_args, context)
    except Exception as exc:
        result = ToolResult(
            content=f"<tool_use_error>Error calling tool ({tool.name}): {exc}</tool_use_error>",
            is_error=True,
            tool_use_id=call_id,
        )

    # Run PostToolUse hooks
    if hook_dispatcher is not None:
        cmd_arg = effective_args.get("command") if isinstance(effective_args, dict) else None
        post_outcome = hook_dispatcher.run(
            "PostToolUse",
            tool_name=tool_name,
            tool_input=effective_args if isinstance(effective_args, dict) else {},
            tool_response=str(result.content),
            command=str(cmd_arg) if cmd_arg is not None else None,
        )
        if post_outcome.additional_context:
            extra = f"\n\n[Hook feedback]: {post_outcome.additional_context}"
            result = ToolResult(
                content=f"{result.content}{extra}",
                is_error=result.is_error,
                tool_use_id=result.tool_use_id,
                context_modifier=result.context_modifier,
            )

    # Map to ToolMessage
    message = tool.map_result(result, call_id)

    modifier: ContextModifier | None = None
    if result.context_modifier is not None:
        modifier = ContextModifier(tool_use_id=call_id, modify_context=result.context_modifier)

    return ToolExecutionOutcome(message=message, context_modifier=modifier)


def yield_missing_tool_results(
    tool_calls: Sequence[ToolCall],
    completed_tool_ids: set[str],
    *,
    cancelled: bool = False,
    message: str | None = None,
) -> Iterator[ToolMessage]:
    """
    Yield synthetic error results for any tool call missing a result (Part B7).

    The history-repair invariant: every emitted tool_use must receive a tool_result on
    every abnormal exit, or the next API request is malformed.
    """
    default_msg = CANCEL_MESSAGE if cancelled else SYNTHETIC_TOOL_RESULT_PLACEHOLDER
    content = message if message is not None else default_msg

    for call in tool_calls:
        call_id = str(call.get("id") or "")
        if call_id not in completed_tool_ids:
            yield ToolMessage(
                content=content,
                tool_call_id=call_id,
                status="error",
                additional_kwargs={"is_error": True, "synthetic_repair": True},
            )
