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
(`tdd/phase_permissions.py::check_tdd_phase_permission`). The duplicate PreToolUse and PostToolUse
phase checks that used to live here were deleted in Phase 2: two enforcement points meant two
places to get RED wrong, and the gate is the one a hook cannot talk its way past.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from langchain_core.messages import HumanMessage, ToolMessage

from app.services.tools.tool_hooks import (
    PERMISSION_DENIED_RETRY_MESSAGE,
    resolve_hook_permission_decision,
    run_permission_denied_hooks,
    run_post_tool_use_failure_hooks,
    run_post_tool_use_hooks,
    run_pre_tool_use_hooks,
)
from app.tool import ContextModifier, Tool, ToolResult, find_tool_by_name
from app.utils.messages import (
    HookAttachment,
    Message,
    ToolCall,
    create_attachment_message,
    messages_for,
)
from app.utils.tool_errors import INPUT_VALIDATION_ERROR, validate_against_schema
from app.utils.tool_result_storage import (
    MAX_TOOL_RESULT_BYTES,
    maybe_persist_large_result,
)

if TYPE_CHECKING:
    from app.tool import ToolContext

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
    #: Hook messages (upstream's attachments) to emit right after the tool result.
    extra_messages: tuple[Message, ...] = ()


async def run_tool_use(
    tool_call: ToolCall,
    assistant_message: Message,
    context: ToolContext,
    tools: Sequence[Tool],
) -> ToolExecutionOutcome:
    """
    Execute a single tool call (Part B4): resolve -> validate -> check permissions -> call -> map result.
    """
    try:
        return await _run_tool_use(tool_call, assistant_message, context, tools)
    except Exception as exc:  # noqa: BLE001 - deliberate catch-all (fail-closed / upstream catch)
        # `runToolUse`'s outer `catch`: anything thrown outside the tool's own call —
        # validation, hooks, the permission gate (an auto-mode `AbortError`) — becomes the
        # call's error result. The turn goes on.
        # Upstream names the tool, which always resolved: an unknown tool returns earlier.
        detailed = f"Error calling tool ({tool_call.get('name')}): {exc}"
        msg = ToolMessage(
            content=f"<tool_use_error>{detailed}</tool_use_error>",
            tool_call_id=str(tool_call.get("id") or ""),
            status="error",
            additional_kwargs={"is_error": True, "tool_use_result": detailed},
        )
        return ToolExecutionOutcome(message=msg)


async def _run_tool_use(
    tool_call: ToolCall,
    assistant_message: Message,
    context: ToolContext,
    tools: Sequence[Tool],
) -> ToolExecutionOutcome:
    """`streamedCheckPermissionsAndCallTool` and the unknown-tool/cancel checks before it."""
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
    #    raw one goes to the transcript. An MCP tool's schema is `z.object({}).passthrough()`
    #    (`MCPTool.ts`): its server's JSON schema reaches the model, its server validates.
    schema = None if getattr(tool, "is_mcp", False) else getattr(tool, "input_schema", None)
    schema_error = validate_against_schema(tool_name, schema, tool_args)
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
    #    opinion — which the gate then still gets to overrule. Their messages are
    #    collected and emitted after the tool result, upstream's order once attachments
    #    are reordered past tool results for the API.
    pre = await run_pre_tool_use_hooks(context, tool_name, tool_args if isinstance(tool_args, dict) else {}, call_id)
    hook_messages: list[Message] = list(messages_for(pre.attachments))
    if pre.stopped:
        msg = ToolMessage(
            content=CANCEL_MESSAGE,
            tool_call_id=call_id,
            status="error",
            additional_kwargs={"is_error": True, "tool_use_result": f"Error: {pre.stop_reason}"},
        )
        return ToolExecutionOutcome(message=msg, extra_messages=tuple(hook_messages))
    hook_verdict = pre.verdict
    if pre.updated_input is not None:
        # A rewrite with no permission opinion: upstream's `hookUpdatedInput` passthrough,
        # after which the ordinary permission flow continues.
        tool_args = pre.updated_input

    # 4. Permission. The single phase-enforcement point lives inside this gate
    #    (`check_tdd_phase_permission`), which is why the duplicate pre-tool phase check
    #    that used to sit here is gone: two checks meant two places to get it wrong, and
    #    the gate is the one a hook cannot talk its way past.
    resolved = await resolve_hook_permission_decision(hook_verdict, tool, tool_args, context, call_id)
    permission = resolved.decision
    if permission.behavior != "allow":
        denied_msg = permission.message
        if pre.prevent_continuation and not denied_msg:
            denied_msg = "Execution stopped by PreToolUse hook" + (f": {pre.stop_reason}" if pre.stop_reason else "")
        msg = ToolMessage(
            content=denied_msg or f"Permission denied for tool: {tool_name}",
            tool_call_id=call_id,
            status="error",
            additional_kwargs={"is_error": True, "permission_behavior": permission.behavior},
        )
        reason = permission.decision_reason or {}
        if reason.get("type") == "classifier" and reason.get("classifier") == "auto-mode":
            # Upstream: an auto-mode denial gives PermissionDenied hooks the chance to say
            # the call is now approved, which the model hears as a meta message.
            retry = await run_permission_denied_hooks(
                context, tool_name, resolved.input, call_id, str(reason.get("reason") or "Permission denied")
            )
            if retry:
                hook_messages.append(
                    HumanMessage(content=PERMISSION_DENIED_RETRY_MESSAGE, additional_kwargs={"is_meta": True})
                )
        return ToolExecutionOutcome(message=msg, extra_messages=tuple(hook_messages))

    effective_args = permission.updated_input if permission.updated_input is not None else resolved.input

    # Execute tool call. A tool that raises takes upstream's failure path: PostToolUseFailure
    # hooks, no governance and no PostToolUse.
    try:
        # `tool.call(input, { ...toolUseContext, toolUseId })`: the call sees its own id.
        result = await tool.call(effective_args, replace(context, tool_use_id=call_id))
    except Exception as exc:  # noqa: BLE001 - deliberate catch-all (fail-closed / upstream catch)
        content = f"<tool_use_error>Error calling tool ({tool.name}): {exc}</tool_use_error>"
        failure = await run_post_tool_use_failure_hooks(
            context, tool_name, effective_args if isinstance(effective_args, dict) else {}, str(exc), call_id,
            bool(context.cancel.cancelled),
        )
        msg = ToolMessage(
            content=content,
            tool_call_id=call_id,
            status="error",
            additional_kwargs={"is_error": True},
        )
        return ToolExecutionOutcome(
            message=msg, extra_messages=(*hook_messages, *messages_for(failure.attachments))
        )

    # 5. Result governance, before any hook sees the output. Upstream persists and commits a
    #    non-MCP result *before* PostToolUse precisely so a hook cannot rewrite it: the tool
    #    already ran, and its output is the ground truth of what happened.
    result = _persist_if_oversized(result, tool, tool_name, call_id, context)

    # 6. PostToolUse hooks. Additive only — they cannot un-run the tool. What they add
    #    (blocking errors, additional context, a stop) follows the result as messages.
    post = await run_post_tool_use_hooks(
        context, tool_name, effective_args if isinstance(effective_args, dict) else {}, str(result.content),
        call_id, bool(getattr(tool, "is_mcp", False)),
    )
    hook_messages.extend(messages_for(post.attachments))
    if post.updated_mcp_tool_output is not None:
        # MCP tools only (`run_post_tool_use_hooks` drops it otherwise): upstream commits an
        # MCP result *after* PostToolUse, so a hook's `updatedMCPToolOutput` replaces it.
        replaced = post.updated_mcp_tool_output
        result = replace(result, content=replaced if isinstance(replaced, str) else json.dumps(replaced))
    hook_messages.extend(result.new_messages)
    if pre.prevent_continuation:
        stopped = create_attachment_message(
            HookAttachment(
                type="hook_stopped_continuation",
                message=pre.stop_reason or "Execution stopped by hook",
                hook_name=f"PreToolUse:{tool_name}",
                tool_use_id=call_id,
                hook_event="PreToolUse",
            )
        )
        if stopped is not None:
            hook_messages.append(stopped)

    # Map to ToolMessage
    message = tool.map_result(result, call_id)

    modifier: ContextModifier | None = None
    if result.context_modifier is not None:
        modifier = ContextModifier(tool_use_id=call_id, modify_context=result.context_modifier)

    return ToolExecutionOutcome(message=message, context_modifier=modifier, extra_messages=tuple(hook_messages))


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
