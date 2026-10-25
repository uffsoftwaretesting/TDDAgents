"""
Hooks that run in the background, and what they report once they finish.

Ported from `reference/claude-code/src/utils/hooks/AsyncHookRegistry.ts` ->
`registerPendingAsyncHook`, `getPendingAsyncHooks`, `checkForAsyncHookResponses`,
`removeDeliveredAsyncHooks`, `finalizePendingAsyncHooks`, `clearAllAsyncHooks`, and
`src/utils/hooks.ts` -> `executeInBackground` (the `asyncRewake` branch).

A hook goes to the background when its config says `async`/`asyncRewake`, or when the first
line it prints is `{"async": true, ...}`. Its caller gets an immediate success; the process
keeps running. Between turns the loop asks the registry for finished hooks
(`check_for_responses`, upstream's `async_hook_responses` attachment source) and hands their
`systemMessage`/`additionalContext` to the model.

`asyncRewake` hooks are not registered: when one exits 2 its output is queued as a
notification (upstream's `enqueuePendingNotification`, mode `task-notification`), which the
loop delivers at the same point.

Upstream keeps one module-level registry per process; here it is **one per run**, owned by
the run's dispatcher, so concurrent runs cannot see each other's hooks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.loop.command_queue import CommandQueue, QueuedCommand
from app.loop.hooks.process import CapturedProcess
from app.loop.messages import wrap_in_system_reminder

logger = logging.getLogger("TDDOrchestrator.Hooks")

#: `registerPendingAsyncHook`: `asyncResponse.asyncTimeout || 15000`.
DEFAULT_ASYNC_TIMEOUT_MS = 15000


@dataclass
class PendingAsyncHook:
    """`PendingAsyncHook`."""

    process_id: str
    hook_id: str
    hook_name: str
    hook_event: str
    command: str
    timeout: int
    capture: CapturedProcess | None
    tool_name: str | None = None
    start_time: float = field(default_factory=time.time)
    response_attachment_sent: bool = False


@dataclass(frozen=True, slots=True)
class AsyncHookResponse:
    """One element of `checkForAsyncHookResponses`' result."""

    process_id: str
    response: Mapping[str, Any]
    hook_name: str
    hook_event: str
    stdout: str
    stderr: str
    exit_code: int | None
    tool_name: str | None = None

    def attachment(self) -> dict[str, Any]:
        """The `async_hook_response` attachment upstream builds from this response."""
        return {
            "type": "async_hook_response",
            "processId": self.process_id,
            "hookName": self.hook_name,
            "hookEvent": self.hook_event,
            "toolName": self.tool_name,
            "response": dict(self.response),
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exitCode": self.exit_code,
        }


class AsyncHookRegistry:
    """The run's background hooks and its queue of rewake notifications."""

    def __init__(self, queue: CommandQueue | None = None) -> None:
        self._pending: dict[str, PendingAsyncHook] = {}
        #: The run's command queue (`messageQueueManager.ts`), which the runner shares as
        #: `ToolContext.command_queue`; a rewake notification is enqueued into it.
        self.queue = queue if queue is not None else CommandQueue()
        self._rewake_tasks: set[asyncio.Future[Any]] = set()
        self._rewake_captures: list[CapturedProcess] = []

    # ── registration ─────────────────────────────────────────────────────────

    def register_pending(
        self,
        *,
        process_id: str,
        hook_id: str,
        async_response: Mapping[str, Any],
        hook_name: str,
        hook_event: str,
        command: str,
        capture: CapturedProcess | None,
        tool_name: str | None = None,
    ) -> None:
        """`registerPendingAsyncHook`."""
        timeout = async_response.get("asyncTimeout") or DEFAULT_ASYNC_TIMEOUT_MS
        logger.debug("Hooks: Registering async hook %s (%s) with timeout %sms", process_id, hook_name, timeout)
        self._pending[process_id] = PendingAsyncHook(
            process_id=process_id,
            hook_id=hook_id,
            hook_name=hook_name,
            hook_event=hook_event,
            command=command,
            timeout=int(timeout),
            capture=capture,
            tool_name=tool_name,
        )

    def register_rewake(self, *, hook_name: str, capture: CapturedProcess) -> None:
        """
        `executeInBackground`'s `asyncRewake` branch: when the process exits 2, queue its
        output as a blocking-error notification for the model.
        """

        async def watch() -> None:
            code = await capture.result()
            if code == 2:
                self.queue.enqueue_pending_notification(QueuedCommand(
                    value=wrap_in_system_reminder(
                        f'Stop hook blocking error from command "{hook_name}": {capture.stderr or capture.stdout}'
                    ),
                    mode="task-notification",
                ))

        task = asyncio.ensure_future(watch())
        self._rewake_captures.append(capture)
        self._rewake_tasks.add(task)
        task.add_done_callback(self._rewake_tasks.discard)

    # ── queries ──────────────────────────────────────────────────────────────

    def get_pending(self) -> list[PendingAsyncHook]:
        """`getPendingAsyncHooks`: those whose response has not been delivered."""
        return [hook for hook in self._pending.values() if not hook.response_attachment_sent]

    async def check_for_responses(self) -> list[AsyncHookResponse]:
        """
        `checkForAsyncHookResponses`: every completed hook's first non-async JSON line,
        delivered once. A hook with no capture, a killed one, or one that finished with no
        stdout is dropped; a running one is left alone.
        """
        responses: list[AsyncHookResponse] = []
        for hook in list(self._pending.values()):
            capture = hook.capture
            if capture is None or capture.status == "killed":
                if capture is not None:
                    logger.debug("Hooks: Hook %s is killed, removing from registry", hook.process_id)
                del self._pending[hook.process_id]
                continue
            if capture.status != "completed":
                continue
            stdout = capture.stdout
            if hook.response_attachment_sent or not stdout.strip():
                del self._pending[hook.process_id]
                continue
            response: Mapping[str, Any] = {}
            for line in stdout.split("\n"):
                stripped = line.strip()
                if not stripped.startswith("{"):
                    continue
                try:
                    parsed = json.loads(stripped)
                except ValueError:
                    logger.debug("Hooks: Failed to parse JSON from %s: %s", hook.process_id, stripped)
                    continue
                if isinstance(parsed, dict) and "async" not in parsed:
                    response = parsed
                    break
            hook.response_attachment_sent = True
            responses.append(
                AsyncHookResponse(
                    process_id=hook.process_id,
                    response=response,
                    hook_name=hook.hook_name,
                    hook_event=hook.hook_event,
                    stdout=stdout,
                    stderr=capture.stderr,
                    exit_code=capture.exit_code,
                    tool_name=hook.tool_name,
                )
            )
            del self._pending[hook.process_id]
        return responses

    def remove_delivered(self, process_ids: list[str]) -> None:
        """`removeDeliveredAsyncHooks`."""
        for process_id in process_ids:
            hook = self._pending.get(process_id)
            if hook is not None and hook.response_attachment_sent:
                del self._pending[process_id]

    # ── shutdown ─────────────────────────────────────────────────────────────

    async def finalize_pending(self) -> None:
        """
        `finalizePendingAsyncHooks`: kill what is still running, then forget everything. A
        rewake hook still running when the run ends is killed too: upstream's process
        outlives the turn, but nothing would be left here to deliver its notification to.
        """
        captures = [hook.capture for hook in self._pending.values() if hook.capture is not None]
        for capture in [*captures, *self._rewake_captures]:
            if capture.status == "running":
                capture.kill()
                await capture.result()
        for task in list(self._rewake_tasks):
            task.cancel()
        self._rewake_captures.clear()
        self._pending.clear()

    def clear(self) -> None:
        """`clearAllAsyncHooks`."""
        self._pending.clear()


__all__ = [
    "DEFAULT_ASYNC_TIMEOUT_MS",
    "AsyncHookRegistry",
    "AsyncHookResponse",
    "PendingAsyncHook",
]
