"""
Tool protocol and build_tool factory with fail-closed defaults (Part B2).

Ported from:
- `reference/claude-code/src/Tool.ts` -> `Tool`, `toolMatchesName`, `findToolByName`
- `app/tools/base.py` (Phase 1B) -> fail-closed defaults and protocol adaptation
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Protocol, Sequence, runtime_checkable

from langchain_core.messages import ToolMessage

from app.loop.tools.types import PermissionResult, ToolResult, ValidationResult

if TYPE_CHECKING:
    from app.loop.context import ToolContext


def default_map_result(result: ToolResult, tool_use_id: str) -> ToolMessage:
    """
    Default mapping from a ToolResult to a LangChain ToolMessage.
    """
    status = "error" if result.is_error else "success"
    additional_kwargs: dict[str, Any] = {}
    if result.hook_stopped_continuation:
        additional_kwargs["hook_stopped_continuation"] = True
    if result.system_reminder:
        additional_kwargs["system_reminder"] = result.system_reminder
    if result.is_error:
        additional_kwargs["is_error"] = True

    return ToolMessage(
        content=result.content,
        tool_call_id=tool_use_id,
        status=status,
        additional_kwargs=additional_kwargs,
    )


@runtime_checkable
class Tool(Protocol):
    """
    Capability surface exposed by a loop tool.
    """

    @property
    def name(self) -> str: ...

    @property
    def prompt(self) -> str: ...

    @property
    def input_schema(self) -> dict[str, Any]: ...

    @property
    def aliases(self) -> tuple[str, ...]: ...

    @property
    def is_mcp(self) -> bool: ...

    def description(self, input: dict[str, Any]) -> str: ...
    def is_enabled(self) -> bool: ...
    def is_concurrency_safe(self, input: dict[str, Any]) -> bool: ...
    def is_read_only(self, input: dict[str, Any]) -> bool: ...
    def is_destructive(self, input: dict[str, Any]) -> bool: ...
    def validate_input(self, input: dict[str, Any], context: ToolContext) -> ValidationResult: ...

    def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> Awaitable[PermissionResult] | PermissionResult: ...
    def call(self, input: dict[str, Any], context: ToolContext) -> Awaitable[ToolResult]: ...
    def map_result(self, result: ToolResult, tool_use_id: str) -> ToolMessage: ...


@dataclass(frozen=True, slots=True)
class BuiltTool:
    """
    Concrete tool assembled by `build_tool` with fail-closed exception guards.
    """

    name: str
    prompt: str
    _call: Callable[[dict[str, Any], ToolContext], Awaitable[ToolResult] | ToolResult]
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})
    aliases: tuple[str, ...] = ()
    is_mcp: bool = False
    _description: Callable[[dict[str, Any]], str] = field(default_factory=lambda: (lambda args: ""))
    _is_enabled: Callable[[], bool] = field(default_factory=lambda: (lambda: True))
    _is_concurrency_safe: Callable[[dict[str, Any]], bool] = field(default_factory=lambda: (lambda args: False))
    _is_read_only: Callable[[dict[str, Any]], bool] = field(default_factory=lambda: (lambda args: False))
    _is_destructive: Callable[[dict[str, Any]], bool] = field(default_factory=lambda: (lambda args: False))
    _requires_user_interaction: Callable[[dict[str, Any]], bool] | Callable[[], bool] = field(
        default_factory=lambda: (lambda args: False)
    )
    _validate_input: Callable[[dict[str, Any], ToolContext], ValidationResult] = field(
        default_factory=lambda: (lambda args, ctx: ValidationResult(valid=True))
    )
    _check_permissions: Callable[[dict[str, Any], ToolContext], Awaitable[PermissionResult] | PermissionResult] = (
        field(default_factory=lambda: (lambda args, ctx: PermissionResult(behavior="allow")))
    )
    _map_result: Callable[[ToolResult, str], ToolMessage] = default_map_result

    def description(self, input: dict[str, Any]) -> str:
        return self._description(input)

    def is_enabled(self) -> bool:
        try:
            return bool(self._is_enabled())
        except Exception:
            return False

    def is_concurrency_safe(self, input: dict[str, Any]) -> bool:
        """
        Fail-closed predicate: defaults to False and any exception is caught and treated as False.
        """
        try:
            return bool(self._is_concurrency_safe(input))
        except Exception:
            return False

    def is_read_only(self, input: dict[str, Any]) -> bool:
        """
        Fail-closed predicate: defaults to False and any exception is caught and treated as False.
        """
        try:
            return bool(self._is_read_only(input))
        except Exception:
            return False

    def is_destructive(self, input: dict[str, Any]) -> bool:
        try:
            return bool(self._is_destructive(input))
        except Exception:
            return False

    def requires_user_interaction(self, input: dict[str, Any] | None = None) -> bool:
        fn = self._requires_user_interaction
        try:
            if input is not None:
                try:
                    return bool(fn(input))  # type: ignore[call-arg]
                except TypeError:
                    return bool(fn())  # type: ignore[call-arg]
            return bool(fn())  # type: ignore[call-arg]
        except Exception:
            return False

    def validate_input(self, input: dict[str, Any], context: ToolContext) -> ValidationResult:
        try:
            return self._validate_input(input, context)
        except Exception as exc:
            return ValidationResult(valid=False, message=str(exc))

    async def check_permissions(self, input: dict[str, Any], context: ToolContext) -> PermissionResult:
        res = self._check_permissions(input, context)
        if inspect.isawaitable(res):
            return await res
        return res

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        res = self._call(input, context)
        if inspect.isawaitable(res):
            return await res
        return res

    def map_result(self, result: ToolResult, tool_use_id: str) -> ToolMessage:
        return self._map_result(result, tool_use_id)


def build_tool(
    *,
    name: str,
    prompt: str,
    call: Callable[[dict[str, Any], ToolContext], Awaitable[ToolResult] | ToolResult],
    input_schema: dict[str, Any] | None = None,
    description: Callable[[dict[str, Any]], str] | None = None,
    is_enabled: Callable[[], bool] | None = None,
    is_concurrency_safe: Callable[[dict[str, Any]], bool] | None = None,
    is_read_only: Callable[[dict[str, Any]], bool] | None = None,
    is_destructive: Callable[[dict[str, Any]], bool] | None = None,
    requires_user_interaction: (
        Callable[[dict[str, Any]], bool] | Callable[[], bool] | None
    ) = None,
    validate_input: Callable[[dict[str, Any], ToolContext], ValidationResult] | None = None,
    check_permissions: Callable[[dict[str, Any], ToolContext], Awaitable[PermissionResult] | PermissionResult]
    | None = None,
    map_result: Callable[[ToolResult, str], ToolMessage] | None = None,
    aliases: Sequence[str] = (),
    is_mcp: bool = False,
) -> BuiltTool:
    """
    Builds a Tool with fail-closed defaults for omitted members.
    """
    return BuiltTool(
        name=name,
        prompt=prompt,
        _call=call,
        input_schema=input_schema if input_schema is not None else {"type": "object"},
        aliases=tuple(aliases),
        is_mcp=is_mcp,
        _description=description if description is not None else (lambda args: name),
        _is_enabled=is_enabled if is_enabled is not None else (lambda: True),
        _is_concurrency_safe=is_concurrency_safe if is_concurrency_safe is not None else (lambda args: False),
        _is_read_only=is_read_only if is_read_only is not None else (lambda args: False),
        _is_destructive=is_destructive if is_destructive is not None else (lambda args: False),
        _requires_user_interaction=(
            requires_user_interaction if requires_user_interaction is not None else (lambda args: False)
        ),
        _validate_input=(
            validate_input if validate_input is not None else (lambda args, ctx: ValidationResult(valid=True))
        ),
        _check_permissions=(
            check_permissions
            if check_permissions is not None
            else (lambda args, ctx: PermissionResult(behavior="allow"))
        ),
        _map_result=map_result if map_result is not None else default_map_result,
    )


def tool_matches_name(tool: Tool, name: str) -> bool:
    """
    Check if a tool matches a given name (primary name or aliases).
    """
    return tool.name == name or name in getattr(tool, "aliases", ())


def find_tool_by_name(tools: Sequence[Tool], name: str) -> Tool | None:
    """
    Find a tool by name in a sequence of tools. Primary names match first; aliases match second.
    """
    for tool in tools:
        if tool.name == name:
            return tool
    for tool in tools:
        if name in getattr(tool, "aliases", ()):
            return tool
    return None
