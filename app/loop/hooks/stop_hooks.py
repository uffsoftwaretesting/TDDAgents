"""
Stop hooks at the loop boundary, with the TDD barrier first.

Ported from `reference/claude-code/src/query/stopHooks.ts` -> `handleStopHooks` (the blocking
`executeStopHooks` part) and `src/utils/hooks.ts` -> `getStopHookMessage`.

`handle_stop_hooks` is upstream's: run the configured `Stop` hooks (`SubagentStop` inside a
worker), turn each blocking error into a meta user message reading `Stop hook feedback:
...`, and let any `continue: false` win over every blocking error. An abort during the hooks
prevents continuation. Upstream's background bookkeeping (prompt suggestion, memory
extraction, auto-dream, job classification), the computer-use cleanup and the teammate hooks
have no equivalent here; the summary message and notifications are UI.

`build_stop_hooks_runner` is what `LoopDeps.stop_hooks` gets, and it is where barrier 3 lives:

1. **`tdd_phase_incomplete_hook` runs first**, on the main thread. Its latch is
   `stop_hook_active`: once it has blocked, a second refusal prevents continuation rather
   than looping. A worker (an `agent_id`) is not guarded by it; the coordinator is.
2. **Configured hooks can add a block, never clear one.** Their blocking errors are added to
   the TDD hook's; a configured `continue: false` stops the run as `stop_hook_prevented`,
   which the runner records as a failed sub-requirement, not as `completed`. Nothing a
   configured hook returns can make an incomplete cycle terminate `completed`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Sequence

from langchain_core.messages import HumanMessage

from app.loop.deps import StopHookResult, StopHooks
from app.loop.hooks.execute import execute_stop_hooks
from app.loop.hooks.output import get_stop_hook_message
from app.loop.hooks.process import is_aborted
from app.loop.messages import Message
from app.loop.tdd.hooks import tdd_phase_incomplete_hook

if TYPE_CHECKING:
    from app.loop.config import RunConfig
    from app.loop.state import LoopState

logger = logging.getLogger("TDDOrchestrator.Hooks")


def _permission_mode(state: LoopState) -> str | None:
    try:
        return str(state.tool_context.get_app_state().tool_permission_context.mode)
    except Exception:  # noqa: BLE001 - a context without permissions reports no mode
        return None


async def handle_stop_hooks(
    state: LoopState, messages: Sequence[Message], stop_hook_active: bool
) -> StopHookResult:
    """
    `handleStopHooks`. `messages` is the history plus this turn's assistant messages, which
    is where a hook's `last_assistant_message` comes from. Upstream also yields a
    `hook_stopped_continuation` record when a hook stops the run; with no transcript of
    attachments yet (Phase 6) the run's terminal reason is that record.
    """
    context = state.tool_context
    cancel = getattr(context, "cancel", None)
    blocking: list[Message] = []
    prevented = False
    try:
        async for result in execute_stop_hooks(
            context, _permission_mode(state), cancel, stop_hook_active, messages
        ):
            if result.blocking_error is not None:
                blocking.append(
                    HumanMessage(
                        content=get_stop_hook_message(result.blocking_error),
                        additional_kwargs={"is_meta": True},
                    )
                )
            if result.prevent_continuation:
                prevented = True
                logger.info("Stop hook prevented continuation: %s", result.stop_reason)
            if is_aborted(cancel):
                return StopHookResult(prevent_continuation=True)
    except Exception:
        logger.exception("Stop hooks failed")
        return StopHookResult()
    if prevented:
        return StopHookResult(prevent_continuation=True)
    return StopHookResult(blocking_errors=tuple(blocking))


def build_stop_hooks_runner() -> StopHooks:
    """The `LoopDeps.stop_hooks` of a production run: the TDD hook, then configured hooks."""

    async def run_stop_hooks(state: LoopState, config: RunConfig) -> StopHookResult:
        is_worker = bool(getattr(state.tool_context, "agent_id", None))
        tdd = StopHookResult()
        if not is_worker:
            tdd = await tdd_phase_incomplete_hook(state, config)
            if tdd.prevent_continuation:
                return tdd
        configured = await handle_stop_hooks(state, state.messages, bool(state.stop_hook_active))
        if configured.prevent_continuation:
            return configured
        return StopHookResult(blocking_errors=(*tdd.blocking_errors, *configured.blocking_errors))

    return run_stop_hooks


__all__ = ["build_stop_hooks_runner", "handle_stop_hooks"]
