"""
The message that ends a turn, in an orange box.

TDDAgents' styling of `reference/claude-code/src/components/messages/AssistantTextMessage.tsx`, whose text
upstream renders as Markdown after the `⏺` dot. The message a turn ends on (the model's answer with no tool
call: a summary, or the agent waiting for the user) is drawn in a rounded box with a strong orange border
titled ` ⚠ TDDAgents `, its Markdown rendered, so it stands out from the tool rows. User decision 2026-11-23,
a divergence: upstream boxes no assistant text. Text written between tool calls stays a `●` row, and a sink
without colour (headless, the logs, the tests) keeps the plain `● …` text.

Which message ends the turn is read from the response's stop reason. A response's content blocks arrive as
separate messages, and only its last block carries the stop reason (`app/services/api/claude.py`
`BlockAccumulator` folds the closing metadata frame into the open block), so text followed by a tool call
has none. A response the output-token limit cut is resumed (`max_tokens`), so it does not end the turn.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage
from rich.box import ROUNDED
from rich.panel import Panel

from app.components.design_system import theme
from app.components.design_system.question_box import PlanMarkdown
from app.components.design_system.rich_render import to_ansi

#: The box's title.
FINAL_MESSAGE_TITLE = " ⚠ TDDAgents "
#: The stop reasons of a response that ends the turn: Anthropic's, then OpenAI's `finish_reason`.
TURN_ENDING_STOP_REASONS = frozenset({"end_turn", "stop_sequence", "stop"})


def stop_reason_of(message: Any) -> str | None:
    """The response's stop reason, as `response_metadata` carries it (`stop_reason` or `finish_reason`)."""
    metadata = getattr(message, "response_metadata", None)
    if not isinstance(metadata, dict):
        return None
    reason = metadata.get("stop_reason") or metadata.get("finish_reason")
    return reason if isinstance(reason, str) else None


def is_turn_final_text(message: Any, text: str) -> bool:
    """`message` is an answer with `text`, no tool call, and a stop reason that ends the turn."""
    return (isinstance(message, AIMessage) and bool(text.strip()) and not message.tool_calls
            and stop_reason_of(message) in TURN_ENDING_STOP_REASONS)


def style_final_message(text: str, width: int) -> str:
    """`text` as Markdown in the orange ⚠ box, `width` columns wide, as ANSI text; headings as the plan box
    draws them (`PlanMarkdown`)."""
    panel = Panel(PlanMarkdown(text.strip()), box=ROUNDED, title=FINAL_MESSAGE_TITLE, title_align="left",
                  border_style=f"bold {theme.FINAL_MESSAGE}", padding=(0, 1), width=width)
    return to_ansi(panel, width)


__all__ = ["FINAL_MESSAGE_TITLE", "TURN_ENDING_STOP_REASONS", "is_turn_final_text", "stop_reason_of",
           "style_final_message"]
