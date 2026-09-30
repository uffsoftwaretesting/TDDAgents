"""
Tool Layer package (Part B of transition elaboration plan).

Exports the Tool protocol, fail-closed builder, execution outcomes, history-repair invariant,
concurrency partitioning, context replay, and prompt-cache-stable pool assembly.
"""

from __future__ import annotations

from app.loop.tools.base import (
    BuiltTool,
    Tool,
    build_tool,
    default_map_result,
    find_tool_by_name,
    tool_matches_name,
)
from app.loop.tools.execution import (
    CANCEL_MESSAGE,
    SYNTHETIC_TOOL_RESULT_PLACEHOLDER,
    ToolExecutionOutcome,
    run_tool_use,
    yield_missing_tool_results,
)
from app.loop.tools.orchestration import (
    ToolBatch,
    apply_context_modifier,
    partition_tool_calls,
    run_tools,
)
from app.loop.tools.pool import (
    assemble_tool_pool,
    is_tool_denied,
)
from app.loop.tools.types import (
    ContextModifier,
    PermissionResult,
    ToolResult,
    ValidationResult,
)

__all__ = [
    # Types (B1)
    "ValidationResult",
    "PermissionResult",
    "ToolResult",
    "ContextModifier",
    # Tool protocol & builder (B2)
    "Tool",
    "BuiltTool",
    "build_tool",
    "default_map_result",
    "find_tool_by_name",
    "tool_matches_name",
    # Execution & history repair (B4, B7)
    "CANCEL_MESSAGE",
    "SYNTHETIC_TOOL_RESULT_PLACEHOLDER",
    "ToolExecutionOutcome",
    "run_tool_use",
    "yield_missing_tool_results",
    # Orchestration & context replay (B5, B6)
    "ToolBatch",
    "partition_tool_calls",
    "run_tools",
    "apply_context_modifier",
    # Pool assembly (B8)
    "assemble_tool_pool",
    "is_tool_denied",
]
