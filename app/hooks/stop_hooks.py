"""
Unified Stop hooks execution and TDD lifecycle integration (Part H5).

Ported from:
- `reference/claude-code/src/query/stopHooks.ts` -> `handleStopHooks`
- `docs/transition_elaboration_plan.md` -> §3.3 (Red->Green invariant and stop_hook_active contract)

Precedence rule:
`prevent_continuation` wins over `blocking_errors` and blanks them — a hook cannot
both stop the turn and request a retry.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence

from langchain_core.messages import HumanMessage

from app.hooks.config import HookSettings
from app.hooks.dispatcher import HookDispatcher
from app.hooks.events import HookEvent
from app.loop.deps import StopHookResult, StopHooks
from app.loop.messages import Message
from app.loop.tdd.hooks import tdd_phase_incomplete_hook

if TYPE_CHECKING:
    from app.loop.config import RunConfig
    from app.loop.state import LoopState

logger = logging.getLogger("TDDOrchestrator.Hooks")


def build_stop_hooks_runner(
    settings: HookSettings | None = None,
    dispatcher: HookDispatcher | None = None,
    extra_stop_hooks: Sequence[StopHooks] = (),
    project_root: Path | str = ".",
) -> StopHooks:
    """
    Constructs a unified StopHooks callable suitable for injection into `LoopDeps`.

    Execution order:
    1. Builtin TDD cycle validation hook (`tdd_phase_incomplete_hook`), enforcing the Red->Green cycle
       and respecting the `stop_hook_active` latch contract.
    2. Any extra stop hook callables.
    3. Configured Stop hooks from settings/dispatcher (Command, Prompt, Agent, HTTP),
       threading `stop_hook_active` into the hook payload.
    """
    active_dispatcher = (
        dispatcher
        or (HookDispatcher(settings, project_root=project_root) if settings is not None else None)
    )

    async def _run_stop_hooks(state: LoopState, config: RunConfig) -> StopHookResult:
        all_blocking: list[Message] = []
        prevent_continuation = False

        # 1. Builtin TDD cycle hook
        tdd_result = await tdd_phase_incomplete_hook(state, config)
        if tdd_result.prevent_continuation:
            prevent_continuation = True
        elif tdd_result.blocking_errors:
            all_blocking.extend(tdd_result.blocking_errors)

        # If TDD hook already prevented continuation, short-circuit
        if prevent_continuation:
            return StopHookResult(prevent_continuation=True)

        # 2. Extra programmatic stop hooks
        for hook_fn in extra_stop_hooks:
            res = await hook_fn(state, config)
            if res.prevent_continuation:
                return StopHookResult(prevent_continuation=True)
            if res.blocking_errors:
                all_blocking.extend(res.blocking_errors)

        # 3. Configured Stop hooks from settings
        if active_dispatcher is not None:
            ledger = state.tool_context.get_app_state().phase_ledger
            stop_payload: dict[str, Any] = {
                "hook_event_name": HookEvent.STOP.value,
                "stop_hook_active": bool(state.stop_hook_active),
                "turn_count": state.turn_count,
                "phase": str(ledger.phase),
                "red_confirmed": ledger.red_confirmed,
                "green_passed": ledger.green_passed,
                "messages_count": len(state.messages),
            }

            outcome = active_dispatcher.run_event(HookEvent.STOP, stop_payload)
            if outcome.prevent_continuation:
                return StopHookResult(prevent_continuation=True)
            if outcome.blocking_errors:
                for err_str in outcome.blocking_errors:
                    all_blocking.append(HumanMessage(content=err_str))

        if prevent_continuation:
            return StopHookResult(prevent_continuation=True)

        if all_blocking:
            return StopHookResult(blocking_errors=tuple(all_blocking))

        return StopHookResult()

    return _run_stop_hooks


__all__ = [
    "build_stop_hooks_runner",
    "tdd_phase_incomplete_hook",
]
