"""
Short labels for completed tool batches, for SDK progress rows.

Ported from `reference/claude-code/src/services/toolUseSummary/toolUseSummaryGenerator.ts` ->
`generateToolUseSummary`, `truncateJson`, with its system prompt verbatim in `prompt.md`. The
small fast model is asked through `query_haiku` (`services/api/claude.ts` -> `queryHaiku`); a
failure is logged (`E_TOOL_USE_SUMMARY_GENERATION_FAILED`) and yields no summary, because
summaries are non-critical. The loop starts it after a tool batch when
`TDDAGENTS_EMIT_TOOL_USE_SUMMARIES` is set and yields it during the next model call
(`query.ts` -> `pendingToolUseSummary`).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).with_name("prompt.md")
#: `constants/errorIds.ts` -> `E_TOOL_USE_SUMMARY_GENERATION_FAILED`.
E_TOOL_USE_SUMMARY_GENERATION_FAILED = 344

#: (system prompt, user prompt) -> the model's text.
QueryHaiku = Callable[[str, str], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class ToolInfo:
    """`ToolInfo`: one completed call."""

    name: str
    input: Any
    output: Any


def tool_use_summary_system_prompt() -> str:
    """`TOOL_USE_SUMMARY_SYSTEM_PROMPT`."""
    return PROMPT_PATH.read_text(encoding="utf-8").strip()


def truncate_json(value: Any, max_length: int) -> str:
    """`truncateJson`: `JSON.stringify`, cut to `max_length` with `...`."""
    try:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return "[unable to serialize]"
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


async def generate_tool_use_summary(
    tools: Sequence[ToolInfo], query: QueryHaiku, *, last_assistant_text: str | None = None
) -> str | None:
    """`generateToolUseSummary`: the label, or `None` (no tools, an empty reply, or a failure)."""
    if not tools:
        return None
    try:
        summaries = "\n\n".join(
            f"Tool: {tool.name}\nInput: {truncate_json(tool.input, 300)}\nOutput: {truncate_json(tool.output, 300)}"
            for tool in tools
        )
        prefix = (f"User's intent (from assistant's last message): {last_assistant_text[:200]}\n\n"
                  if last_assistant_text else "")
        reply = await query(tool_use_summary_system_prompt(), f"{prefix}Tools completed:\n\n{summaries}\n\nLabel:")
        return reply.strip() or None
    except Exception as exc:  # noqa: BLE001 - upstream: log, never fail; summaries are non-critical
        logger.error("Tool use summary generation failed (errorId %s): %s", E_TOOL_USE_SUMMARY_GENERATION_FAILED,
                     exc)
        return None


__all__ = [
    "E_TOOL_USE_SUMMARY_GENERATION_FAILED",
    "QueryHaiku",
    "ToolInfo",
    "generate_tool_use_summary",
    "tool_use_summary_system_prompt",
    "truncate_json",
]
