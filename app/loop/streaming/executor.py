"""
Streaming tool executor with fine-grained concurrency and three-controller abort tree (Parts F2, F3, F4).

Ported from `reference/claude-code/src/services/tools/StreamingToolExecutor.ts`.

Key Invariants:
- F2: Dispatch at content-block close, not on partial parse.
- F3: Three-controller abort tree (turn, sibling, per-tool) with only Bash errors
  cascading to siblings and exactly one deliberate upward bubble (permission rejection
  or user cancellation bubbles up to the turn controller to terminate the turn, regression #21056).
- F4: Ordered emission and discard() semantics.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import (
    AsyncIterator,
    Callable,
    Iterator,
    Literal,
    Sequence,
)

from langchain_core.messages import ToolMessage

from app.loop.context import ToolContext
from app.loop.messages import Message, ToolCall
from app.loop.streaming.abort import (
    AbortController,
    create_child_abort_controller,
)
from app.loop.tools.base import Tool, find_tool_by_name
from app.loop.tools.execution import CANCEL_MESSAGE, run_tool_use
from app.loop.tools.orchestration import apply_context_modifier

logger = logging.getLogger(__name__)

ToolStatus = Literal["queued", "executing", "completed", "yielded"]

BASH_TOOL_NAMES = frozenset({"bash", "Bash", "bash_tool"})


@dataclass(slots=True)
class TrackedTool:
    """
    State tracking for a single tool call through its streaming lifecycle.
    """

    id: str
    tool_call: ToolCall
    assistant_message: Message
    status: ToolStatus
    is_concurrency_safe: bool
    task: asyncio.Task[None] | None = None
    abort_controller: AbortController | None = None
    results: list[Message] = field(default_factory=list)
    pending_progress: list[Message] = field(default_factory=list)
    context_modifiers: list[Callable[[ToolContext], ToolContext]] = field(
        default_factory=list
    )


class StreamingToolExecutor:
    """
    Executes tools as they stream in with concurrency control.

    - Concurrent-safe tools execute in parallel with other concurrent-safe tools.
    - Non-concurrent tools execute alone (exclusive access).
    - Results are buffered and emitted in the order tools were received.
    - Progress messages are emitted immediately as they become available.
    """

    def __init__(
        self,
        tools: Sequence[Tool],
        tool_context: ToolContext,
        turn_abort_controller: AbortController | None = None,
    ) -> None:
        self.tool_definitions = tuple(tools)
        self.tool_context = tool_context
        self.turn_abort_controller = (
            turn_abort_controller
            if turn_abort_controller is not None
            else AbortController()
        )

        # Child of turn controller: fires when a Bash tool errors so sibling
        # subprocesses die immediately. Aborting this does NOT abort the parent.
        self.sibling_abort_controller = create_child_abort_controller(
            self.turn_abort_controller
        )

        self._tools: list[TrackedTool] = []
        self.has_errored = False
        self.errored_tool_description = ""
        self.discarded = False
        self._progress_event = asyncio.Event()

    def discard(self) -> None:
        """
        Discards all pending and in-progress tools (F4).
        Called when streaming fallback occurs and results from the failed attempt
        should be abandoned.
        """
        self.discarded = True
        self._progress_event.set()

    def add_tool(self, tool_call: ToolCall, assistant_message: Message) -> None:
        """
        Add a tool call to the execution queue at content-block close (F2).
        Starts execution immediately if concurrency conditions allow.
        """
        call_id = str(tool_call.get("id") or "")
        tool_name = str(tool_call.get("name") or "")
        args = tool_call.get("args") or {}

        tool_def = find_tool_by_name(self.tool_definitions, tool_name)
        if tool_def is None:
            # Immediate synthetic error for unknown tool
            error_msg = ToolMessage(
                content=f"<tool_use_error>Error: No such tool available: {tool_name}</tool_use_error>",
                tool_call_id=call_id,
                status="error",
                additional_kwargs={"is_error": True},
            )
            self._tools.append(
                TrackedTool(
                    id=call_id,
                    tool_call=tool_call,
                    assistant_message=assistant_message,
                    status="completed",
                    is_concurrency_safe=True,
                    results=[error_msg],
                )
            )
            self._progress_event.set()
            return

        try:
            is_concurrency_safe = bool(tool_def.is_concurrency_safe(args))
        except Exception:
            is_concurrency_safe = False

        tracked = TrackedTool(
            id=call_id,
            tool_call=tool_call,
            assistant_message=assistant_message,
            status="queued",
            is_concurrency_safe=is_concurrency_safe,
        )
        self._tools.append(tracked)
        self._process_queue()

    def can_execute_tool(self, is_concurrency_safe: bool) -> bool:
        """
        Check if a tool can execute based on current concurrency state.
        Safe tools can run alongside other safe tools. Non-safe tools require exclusivity.
        """
        executing = [t for t in self._tools if t.status == "executing"]
        if not executing:
            return True
        return is_concurrency_safe and all(t.is_concurrency_safe for t in executing)

    def _process_queue(self) -> None:
        """
        Start executing queued tools whose concurrency requirements are met.
        """
        for tool in self._tools:
            if tool.status != "queued":
                continue

            if self.can_execute_tool(tool.is_concurrency_safe):
                tool.status = "executing"
                self.tool_context.in_flight_tool_ids.add(tool.id)
                tool.task = asyncio.create_task(self._execute_tool(tool))
            else:
                # To maintain order for non-concurrent tools, stop here
                if not tool.is_concurrency_safe:
                    break

    def get_abort_reason(
        self, tool: TrackedTool
    ) -> Literal["sibling_error", "user_interrupted", "streaming_fallback"] | None:
        """
        Determine why a tool should be cancelled before or during execution.
        """
        if self.discarded:
            return "streaming_fallback"
        if self.has_errored:
            return "sibling_error"
        if (
            self.turn_abort_controller.signal.aborted
            or self.tool_context.cancel.cancelled
        ):
            return "user_interrupted"
        return None

    def create_synthetic_error_message(
        self,
        tool_id: str,
        reason: Literal["sibling_error", "user_interrupted", "streaming_fallback"],
        assistant_message: Message,
    ) -> ToolMessage:
        """
        Build synthetic tool result messages for cancelled tools (F3/F4).
        """
        if reason == "user_interrupted":
            return ToolMessage(
                content=CANCEL_MESSAGE,
                tool_call_id=tool_id,
                status="error",
                additional_kwargs={"is_error": True},
            )
        if reason == "streaming_fallback":
            return ToolMessage(
                content="<tool_use_error>Error: Streaming fallback - tool execution discarded</tool_use_error>",
                tool_call_id=tool_id,
                status="error",
                additional_kwargs={"is_error": True},
            )

        desc = self.errored_tool_description
        msg = (
            f"Cancelled: parallel tool call {desc} errored"
            if desc
            else "Cancelled: parallel tool call errored"
        )
        return ToolMessage(
            content=f"<tool_use_error>{msg}</tool_use_error>",
            tool_call_id=tool_id,
            status="error",
            additional_kwargs={"is_error": True},
        )

    def _get_tool_description(self, tool: TrackedTool) -> str:
        name = str(tool.tool_call.get("name") or "")
        args = tool.tool_call.get("args") or {}
        summary = args.get("command") or args.get("file_path") or args.get("pattern") or ""
        if isinstance(summary, str) and summary:
            truncated = summary[:40] + "…" if len(summary) > 40 else summary
            return f"{name}({truncated})"
        return name

    async def _execute_tool(self, tool: TrackedTool) -> None:
        """
        Execute tool call under the three-controller abort tree (F3).
        """
        initial_abort = self.get_abort_reason(tool)
        if initial_abort:
            tool.results = [
                self.create_synthetic_error_message(
                    tool.id, initial_abort, tool.assistant_message
                )
            ]
            tool.status = "completed"
            self.tool_context.in_flight_tool_ids.discard(tool.id)
            self._progress_event.set()
            self._process_queue()
            return

        # Per-tool child controller under sibling controller
        tool_abort_controller = create_child_abort_controller(
            self.sibling_abort_controller
        )
        tool.abort_controller = tool_abort_controller

        # Crucial upward bubble (regression #21056):
        # Permission rejection or non-sibling abort bubbles up to turn controller
        def on_tool_abort(reason: str | None) -> None:
            if (
                reason != "sibling_error"
                and not self.turn_abort_controller.signal.aborted
                and not self.discarded
            ):
                self.turn_abort_controller.abort(reason)
                self.tool_context.cancel.cancel()

        tool_abort_controller.signal.add_listener(on_tool_abort)

        messages: list[Message] = []
        context_modifiers: list[Callable[[ToolContext], ToolContext]] = []
        this_tool_errored = False

        try:
            outcome = await run_tool_use(
                tool.tool_call,
                tool.assistant_message,
                self.tool_context,
                self.tool_definitions,
            )
            msg = outcome.message
            messages.append(msg)

            if outcome.context_modifier is not None:
                context_modifiers.append(outcome.context_modifier.modify_context)

            is_error = getattr(msg, "status", None) == "error" or (
                isinstance(getattr(msg, "additional_kwargs", None), dict)
                and msg.additional_kwargs.get("is_error") is True
            )

            if is_error:
                this_tool_errored = True
                # Upstream invariant F3: ONLY Bash errors cascade to siblings!
                # Bash commands have implicit dependencies (e.g. mkdir fails -> subsequent fail).
                # Read, Grep, etc. are independent; one failure must not nuke the rest.
                if str(tool.tool_call.get("name") or "") in BASH_TOOL_NAMES:
                    self.has_errored = True
                    self.errored_tool_description = self._get_tool_description(tool)
                    self.sibling_abort_controller.abort("sibling_error")

        except Exception as exc:
            this_tool_errored = True
            logger.warning("Tool execution raised unexpected exception: %s", exc)
            err_msg = ToolMessage(
                content=f"<tool_use_error>Error calling tool ({tool.tool_call.get('name')}): {exc}</tool_use_error>",
                tool_call_id=tool.id,
                status="error",
                additional_kwargs={"is_error": True},
            )
            messages.append(err_msg)
            if str(tool.tool_call.get("name") or "") in BASH_TOOL_NAMES:
                self.has_errored = True
                self.errored_tool_description = self._get_tool_description(tool)
                self.sibling_abort_controller.abort("sibling_error")

        # Check if cancelled mid-execution (F3/F4)
        abort_reason = self.get_abort_reason(tool)
        if abort_reason and not this_tool_errored:
            messages = [
                self.create_synthetic_error_message(
                    tool.id, abort_reason, tool.assistant_message
                )
            ]

        tool.results = messages
        tool.context_modifiers = context_modifiers
        tool.status = "completed"
        self.tool_context.in_flight_tool_ids.discard(tool.id)

        # Apply context modifiers in order for non-concurrent tools
        if not tool.is_concurrency_safe and context_modifiers:
            for mod in context_modifiers:
                apply_context_modifier(self.tool_context, mod)

        self._progress_event.set()
        self._process_queue()

    def get_completed_results(self) -> Iterator[Message]:
        """
        Get completed results that have not been yielded yet in deterministic order (F4).
        Non-blocking. Progress messages are yielded immediately.
        """
        if self.discarded:
            return

        for tool in self._tools:
            # Yield any pending progress messages immediately
            while tool.pending_progress:
                yield tool.pending_progress.pop(0)

            if tool.status == "yielded":
                continue

            if tool.status == "completed":
                tool.status = "yielded"
                for msg in tool.results:
                    yield msg
            else:
                # Invariant F4: Maintain strictly ordered emission of completed results
                break

    def has_unfinished_tools(self) -> bool:
        return any(t.status != "yielded" for t in self._tools)

    def has_executing_tools(self) -> bool:
        return any(t.status == "executing" for t in self._tools)

    async def get_remaining_results(self) -> AsyncIterator[Message]:
        """
        Wait for all remaining tools to finish and yield their results in order (F4).
        """
        if self.discarded:
            return

        while self.has_unfinished_tools():
            self._process_queue()

            for result in self.get_completed_results():
                yield result

            if self.has_executing_tools():
                executing_tasks = [
                    t.task
                    for t in self._tools
                    if t.status == "executing" and t.task is not None
                ]
                if executing_tasks:
                    await asyncio.wait(
                        executing_tasks,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
            elif self.has_unfinished_tools():
                await asyncio.sleep(0.001)

        for result in self.get_completed_results():
            yield result
