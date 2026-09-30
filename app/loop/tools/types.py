"""
Core dataclasses for the tool layer (Part B1).

Ported from:
- `reference/claude-code/src/Tool.ts` -> `ValidationResult`
- `reference/claude-code/src/utils/permissions/PermissionResult.ts` -> `PermissionResult`
- `reference/claude-code/src/types/tools.ts` -> Tool results and execution outcomes
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from app.loop.permissions.types import PermissionResult

if TYPE_CHECKING:
    from app.loop.context import ToolContext

__all__ = [
    "ContextModifier",
    "PermissionResult",
    "ToolResult",
    "ValidationResult",
]


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """
    Outcome of validating tool arguments before permission checks and execution.
    """

    valid: bool
    message: str = ""
    error_code: int = 0


@dataclass(frozen=True, slots=True)
class ToolResult:
    """
    Standard result returned by a tool call.
    """

    content: str
    is_error: bool = False
    tool_use_id: str = ""
    system_reminder: str | None = None
    hook_stopped_continuation: bool = False
    context_modifier: Callable[[ToolContext], ToolContext] | None = None


@dataclass(frozen=True, slots=True)
class ContextModifier:
    """
    Context modification queued during concurrent or serial tool execution.
    """

    tool_use_id: str
    modify_context: Callable[[ToolContext], ToolContext]
