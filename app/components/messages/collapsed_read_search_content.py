"""
The REPL's rendering of consecutive search/read tool uses.

Ported from `reference/claude-code/src/components/messages/CollapsedReadSearchContent.tsx` (the
collapsed row, its `⎿` hint and the verbose `VerboseToolUse` view) and the grouping
`components/Messages.tsx` applies through `collapseReadSearchGroups`, for a terminal that prints
lines instead of re-rendering a row (user decision 2026-11-01):

* each collapsible call prints its hint as it arrives (`  ⎿  <hint>`, a multi-line hint indented
  under the `⎿`), and its result is absorbed;
* when the group closes (assistant text, a non-collapsible tool, a result outside the group, a
  user message, or the end of the turn) the past-tense summary prints (`  Searched for 2
  patterns, read 3 files`);
* with `verbose` (`--verbose`), each collapsed call prints as `VerboseToolUse` instead:
  `● Name(input)`, then `  ⎿  ` and its result (`render_tool_result_message`, nothing for an
  errored call), and the recalled memories after the group.

Attachments, meta and system messages never close a group (upstream defers them past it), and a
tool-use summary is not shown (the REPL ignores it upstream too).
"""

from __future__ import annotations

import posixpath
from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.tool import Tool, find_tool_by_name
from app.utils.attachments import attachment_of
from app.utils.collapse_read_search import (
    ReadSearchGroup,
    collapsed_summary_text,
    get_tool_search_or_read_info,
)

#: The `MessageResponse` gutter and its continuation indent.
RESPONSE_GUTTER = "  ⎿  "
CONTINUATION = "     "


def response_lines(text: str) -> list[str]:
    """`MessageResponse`: the first line after `  ⎿  `, the rest indented under it."""
    lines = text.split("\n")
    return [RESPONSE_GUTTER + lines[0], *(CONTINUATION + line for line in lines[1:])]


def _text_of(message: AIMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text")


class CollapsedReadSearchRenderer:
    """Feeds conversation events through the read/search collapse; returns the lines to print."""

    def __init__(
        self, tools: Callable[[], Sequence[Tool]], context: Callable[[], Any], render: Callable[[Any], str | None],
        *, verbose: bool = False,
    ) -> None:
        self._tools = tools
        self._context = context
        self._render = render
        self.verbose = verbose
        self.group: ReadSearchGroup | None = None
        self._shown_hint: str | None = None

    def feed(self, event: Any) -> list[str]:
        """The lines `event` adds to the terminal."""
        if isinstance(event, AIMessage):
            return [] if event.additional_kwargs.get("is_meta") else self._assistant(event)
        if isinstance(event, ToolMessage):
            return self._tool_result(event)
        if isinstance(event, BaseMessage):
            attachment = attachment_of(event)
            if attachment is not None or event.additional_kwargs.get("is_meta") or event.type == "system":
                if attachment is not None and attachment.get("type") == "relevant_memories" and self.group:
                    self.group.absorb_relevant_memories(attachment.get("memories") or ())
                return self._plain(event)
            if isinstance(event, HumanMessage):
                return [*self.flush(), *self._plain(event)]
        if getattr(event, "type", None) == "tool_use_summary":
            return []
        # Anything else (the turn's verdict, its result) closes the group.
        return [*self.flush(), *self._plain(event)]

    def flush(self) -> list[str]:
        """Close the open group: its summary (or, verbose, its recalled memories)."""
        group, self.group, self._shown_hint = self.group, None, None
        if group is None or group.is_empty:
            return []
        if self.verbose:
            lines: list[str] = []
            for memory in group.relevant_memories:
                lines.append(f"{RESPONSE_GUTTER}Recalled {posixpath.basename(str(memory.get('path', '')))}")
                lines.extend(CONTINUATION + line for line in str(memory.get("content", "")).split("\n"))
            return lines
        summary = collapsed_summary_text(group, active=False)
        return [f"  {summary}"] if summary else []

    def _plain(self, event: Any) -> list[str]:
        text = self._render(event)
        return [text] if text is not None else []

    def _assistant(self, message: AIMessage) -> list[str]:
        lines: list[str] = []
        text = _text_of(message)
        if text.strip():
            lines.extend(self.flush())
            lines.append(f"● {text.strip()}")
        tools, context = self._tools(), self._context()
        for call in message.tool_calls:
            args = call.get("args") or {}
            info = get_tool_search_or_read_info(str(call.get("name")), args, tools, context)
            if not info.is_collapsible:
                lines.extend(self.flush())
                rendered = self._render(AIMessage(content="", tool_calls=[call]))
                if rendered is not None:
                    lines.append(rendered)
                continue
            if self.group is None:
                self.group = ReadSearchGroup(context=context)
            self.group.add_tool_use(call, info)
            if self.verbose:
                lines.append(self._verbose_header(call, tools))
                continue
            hint = self.group.displayed_hint
            if hint is not None and hint != self._shown_hint:
                self._shown_hint = hint
                lines.extend(response_lines(hint))
        return lines

    def _verbose_header(self, call: Any, tools: Sequence[Tool]) -> str:
        """`VerboseToolUse`'s header: `● <userFacingName>(<renderToolUseMessage(input, verbose)>)`."""
        name, args = str(call.get("name")), call.get("args") or {}
        tool = find_tool_by_name(tools, name)
        if tool is None:
            return f"● {name}"
        rendered = tool.render_tool_use_message(args, True)
        return f"● {tool.user_facing_name(args)}({rendered or ''})"

    def _tool_result(self, message: ToolMessage) -> list[str]:
        if self.group is None or not self.group.owns(message.tool_call_id):
            return [*self.flush(), *self._plain(message)]
        output = message.additional_kwargs.get("tool_use_result")
        self.group.absorb_result(message.tool_call_id, output)
        if not self.verbose or message.status == "error":
            return []
        index = self.group.tool_use_ids.index(message.tool_call_id)
        tool = find_tool_by_name(self._tools(), str(self.group.calls[index].get("name")))
        rendered = tool.render_tool_result_message(output, True) if tool is not None else None
        return response_lines(rendered) if rendered else []


__all__ = ["CONTINUATION", "RESPONSE_GUTTER", "CollapsedReadSearchRenderer", "response_lines"]
