"""
How a tool call's result reads under its row in the REPL.

Ported from `reference/claude-code/src/components/messages/UserToolResultMessage/`:

* `UserToolResultMessage.tsx` -> `UserToolResultMessage`, the dispatch: a result starting with
  `CANCEL_MESSAGE` is `UserToolCanceledMessage`; one starting with `REJECT_MESSAGE`, or exactly
  `INTERRUPT_MESSAGE_FOR_TOOL_USE`, is `UserToolRejectMessage`; an error is `UserToolErrorMessage`; anything
  else is `UserToolSuccessMessage`;
* `UserToolCanceledMessage.tsx`, `UserToolRejectMessage.tsx` (through `FallbackToolUseRejectedMessage.tsx`:
  no tool here renders its own rejection) and `components/InterruptedByUser.tsx`: "Interrupted · What
  should … do instead?";
* `UserToolErrorMessage.tsx` -> `UserToolErrorMessage`: a result holding `INTERRUPT_MESSAGE_FOR_TOOL_USE`
  is "Interrupted", one starting with `REJECT_MESSAGE_WITH_REASON_PREFIX` is `RejectedToolUseMessage`
  ("Tool use rejected"); any other is `FallbackToolUseErrorMessage` (no tool here renders its own error);
* `UserToolSuccessMessage.tsx` -> `UserToolSuccessMessage`: the tool's `renderToolResultMessage` of the
  call's structured output (`tool_use_result`); an empty rendering shows nothing.

The caller puts each text under the `MessageResponse` gutter (`  ⎿  `).

*Divergences:* a successful result whose tool renders nothing (`null`), has no `renderToolResultMessage`,
or has no structured output shows its result's first line (upstream shows nothing; several of TDDAgents'
tools, `RunTests` first, have no rendering of their own, and a silent call reads as stuck, 2026-11-14).
`RejectedPlanMessage` and the auto-mode classifier's denial have no counterpart here. "Claude" in
`InterruptedByUser` reads "TDDAgents", the product substitution of the prompts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.messages import ToolMessage

from app.components.fallback_tool_use_error_message import fallback_tool_use_error_text
from app.services.tools.tool_execution import CANCEL_MESSAGE
from app.tool import Tool, find_tool_by_name
from app.utils.messages import (
    INTERRUPT_MESSAGE_FOR_TOOL_USE,
    REJECT_MESSAGE,
    REJECT_MESSAGE_WITH_REASON_PREFIX,
)

#: `InterruptedByUser`'s text (external build).
INTERRUPTED_BY_USER = "Interrupted · What should TDDAgents do instead?"
#: `RejectedToolUseMessage`'s text.
TOOL_USE_REJECTED = "Tool use rejected"
#: How much of a result's first line the divergent fallback shows.
RESULT_PREVIEW_CHARS = 200


def _content_text(message: ToolMessage) -> str | None:
    """`param.content` when it is a string (upstream checks `typeof param.content === 'string'`)."""
    return message.content if isinstance(message.content, str) else None


def _first_line(message: ToolMessage) -> str:
    content = message.content if isinstance(message.content, str) else " ".join(
        str(block.get("text", "")) for block in message.content
        if isinstance(block, dict) and block.get("type") == "text")
    content = content.strip()
    return content.splitlines()[0][:RESULT_PREVIEW_CHARS] if content else "(no output)"


def _is_error(message: ToolMessage) -> bool:
    return message.status == "error" or bool(message.additional_kwargs.get("is_error"))


def tool_result_text(message: ToolMessage, call: Mapping[str, Any] | None, tools: Sequence[Tool],
                     *, verbose: bool = False, context: Any = None) -> str | None:
    """What `UserToolResultMessage` shows for `message`, the result of `call`; `None` for nothing."""
    content = _content_text(message)
    if content is not None and content.startswith(CANCEL_MESSAGE):
        return INTERRUPTED_BY_USER
    if content is not None and (content.startswith(REJECT_MESSAGE) or content == INTERRUPT_MESSAGE_FOR_TOOL_USE):
        return INTERRUPTED_BY_USER
    if _is_error(message):
        if content is not None and INTERRUPT_MESSAGE_FOR_TOOL_USE in content:
            return "Interrupted"
        if content is not None and content.startswith(REJECT_MESSAGE_WITH_REASON_PREFIX):
            return TOOL_USE_REJECTED
        return fallback_tool_use_error_text(message.content, verbose)
    tool = find_tool_by_name(tools, str(call.get("name"))) if call is not None else None
    output = message.additional_kwargs.get("tool_use_result")
    if tool is not None and output is not None:
        rendered = tool.render_tool_result_message(output, verbose, context)
        if rendered is not None:
            return rendered or None
    return _first_line(message)


__all__ = [
    "INTERRUPTED_BY_USER",
    "RESULT_PREVIEW_CHARS",
    "TOOL_USE_REJECTED",
    "tool_result_text",
]
