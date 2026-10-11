"""
Tool call execution and history repair invariant (Parts B4 and B7).

Ported from:
- `reference/claude-code/src/services/tools/toolExecution.ts` -> `runToolUse`,
  `checkPermissionsAndCallTool`
- `reference/claude-code/src/utils/messages.ts` -> `ensureToolResultPairing`, `SYNTHETIC_TOOL_RESULT_PLACEHOLDER`

The pipeline order is upstream's and is load-bearing at two points. **Schema validation is
first**, before semantic validation, hooks and permissions. **Result governance runs before
PostToolUse**, because upstream commits a non-MCP result before the hooks see it — the tool
has already run, so its output is the record of what happened and a hook may annotate it but
never rewrite it.

There is exactly **one TDD phase check**, inside the permission gate
(`permissions/tdd.py::check_tdd_phase_permission`). The duplicate PreToolUse and PostToolUse
phase checks that used to live here were deleted in Phase 2: two enforcement points meant two
places to get RED wrong, and the gate is the one a hook cannot talk its way past.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Iterator, Sequence

from langchain_core.messages import ToolMessage

from app.loop.messages import Message, ToolCall
from app.loop.tools.base import Tool, find_tool_by_name
from app.loop.tools.hook_permissions import resolve_hook_permission_decision, verdict_from_hook_outcome
from app.loop.tools.result_storage import MAX_TOOL_RESULT_BYTES, maybe_persist_large_result
from app.loop.tools.validation import INPUT_VALIDATION_ERROR, validate_against_schema
from app.loop.tools.types import ContextModifier, ToolResult

if TYPE_CHECKING:
    from app.loop.context import ToolContext

SYNTHETIC_TOOL_RESULT_PLACEHOLDER = "[Tool result missing due to internal error]"
CANCEL_MESSAGE = (
    "The user doesn't want to take this action right now. STOP what you are doing and wait "
    "for the user to tell you how to proceed."
)


def _persist_if_oversized(
    result: ToolResult, tool: Tool, tool_name: str, call_id: str, context: ToolContext
) -> ToolResult:
    """
    Replace an oversized result with a preview plus the path to the whole thing.

    A tool declaring `math.inf` opts out entirely, which is how `ReadFile` avoids the
    ReadFile -> file -> ReadFile loop upstream names. Governance never fails a call: if the
    file cannot be written the original content is returned untouched.
    """
    content = str(result.content)
    declared = getattr(tool, "max_result_size_chars", MAX_TOOL_RESULT_BYTES)
    run_id = str(getattr(context, "run_id", "") or "run")
    root = getattr(getattr(context, "workspace", None), "root", None)
    governed = maybe_persist_large_result(
        content,
        tool_name=tool_name,
        tool_use_id=call_id,
        run_id=run_id,
        working_directory=str(root) if root is not None else ".",
        max_result_size_chars=declared,
    )
    if governed == content:
        return result
    return replace(result, content=governed)


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

    # 1. Schema validation. Upstream opens `checkPermissionsAndCallTool` with this, before
    #    `validateInput`, before hooks and before permissions: "surprisingly, the model is
    #    not great at generating valid input". The model sees the humanized message; the
    #    raw one goes to the transcript.
    schema_error = validate_against_schema(tool_name, getattr(tool, "input_schema", None), tool_args)
    if schema_error is not None:
        msg = ToolMessage(
            content=f"<tool_use_error>{INPUT_VALIDATION_ERROR}: {schema_error}</tool_use_error>",
            tool_call_id=call_id,
            status="error",
            additional_kwargs={"is_error": True, "tool_use_result": f"{INPUT_VALIDATION_ERROR}: {schema_error}"},
        )
        return ToolExecutionOutcome(message=msg)

    # 2. Tool-specific semantic validation. `error_code` is recorded but never shown: it is
    #    analytics-only upstream, read at exactly one place, and its values are per-tool
    #    opaque integers with no shared meaning.
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

    # 3. PreToolUse hooks. They may rewrite the input, and they may state a permission
    #    opinion — which the gate then still gets to overrule.
    hook_dispatcher = getattr(context, "hook_dispatcher", None)
    hook_verdict = None
    pre_outcome = None
    if hook_dispatcher is not None:
        cmd_arg = tool_args.get("command") if isinstance(tool_args, dict) else None
        pre_outcome = hook_dispatcher.run(
            "PreToolUse",
            tool_name=tool_name,
            tool_input=tool_args if isinstance(tool_args, dict) else {},
            command=str(cmd_arg) if cmd_arg is not None else None,
        )
        hook_verdict = verdict_from_hook_outcome(pre_outcome)
        if hook_verdict is None and pre_outcome.updated_input is not None:
            # A rewrite with no permission opinion: upstream's `hookUpdatedInput`
            # passthrough, after which the ordinary permission flow continues.
            tool_args = pre_outcome.updated_input

    # 4. Permission. The single phase-enforcement point lives inside this gate
    #    (`check_tdd_phase_permission`), which is why the duplicate pre-tool phase check
    #    that used to sit here is gone: two checks meant two places to get it wrong, and
    #    the gate is the one a hook cannot talk its way past.
    resolved = await resolve_hook_permission_decision(hook_verdict, tool, tool_args, context)
    permission = resolved.decision
    if permission.behavior != "allow":
        denied_msg = permission.message or f"Permission denied for tool: {tool_name}"
        msg = ToolMessage(
            content=denied_msg,
            tool_call_id=call_id,
            status="error",
            additional_kwargs={"is_error": True, "permission_behavior": permission.behavior},
        )
        return ToolExecutionOutcome(message=msg)

    effective_args = permission.updated_input if permission.updated_input is not None else resolved.input

    # Execute tool call
    try:
        result = await tool.call(effective_args, context)
    except Exception as exc:
        result = ToolResult(
            content=f"<tool_use_error>Error calling tool ({tool.name}): {exc}</tool_use_error>",
            is_error=True,
            tool_use_id=call_id,
        )

    # 5. Result governance, before any hook sees the output. Upstream persists and commits a
    #    non-MCP result *before* PostToolUse precisely so a hook cannot rewrite it: the tool
    #    already ran, and its output is the ground truth of what happened.
    result = _persist_if_oversized(result, tool, tool_name, call_id, context)

    # 6. PostToolUse hooks. Additive only — they cannot un-run the tool.
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
