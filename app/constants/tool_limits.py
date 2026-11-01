"""
Tool result and summary limits.

Ported from `reference/claude-code/src/constants/toolLimits.ts`.
"""

from __future__ import annotations

#: `DEFAULT_MAX_RESULT_SIZE_CHARS`: results above it are persisted to disk; a ceiling on every
#: tool's own figure.
DEFAULT_MAX_RESULT_SIZE_CHARS = 50_000

#: `MAX_TOOL_RESULT_TOKENS`.
MAX_TOOL_RESULT_TOKENS = 100_000

#: `BYTES_PER_TOKEN`: the conservative bytes-per-token estimate.
BYTES_PER_TOKEN = 4

#: `MAX_TOOL_RESULT_BYTES`: the fallback threshold.
MAX_TOOL_RESULT_BYTES = MAX_TOOL_RESULT_TOKENS * BYTES_PER_TOKEN

#: `MAX_TOOL_RESULTS_PER_MESSAGE_CHARS`: the budget for one message's parallel tool results.
MAX_TOOL_RESULTS_PER_MESSAGE_CHARS = 200_000

#: `TOOL_SUMMARY_MAX_LENGTH`: the longest tool summary in compact views, in columns.
TOOL_SUMMARY_MAX_LENGTH = 50

__all__ = [
    "BYTES_PER_TOKEN",
    "DEFAULT_MAX_RESULT_SIZE_CHARS",
    "MAX_TOOL_RESULTS_PER_MESSAGE_CHARS",
    "MAX_TOOL_RESULT_BYTES",
    "MAX_TOOL_RESULT_TOKENS",
    "TOOL_SUMMARY_MAX_LENGTH",
]
