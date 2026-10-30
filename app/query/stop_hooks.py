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

1. **The TDD step runs first**, on the main thread: the plan's advance
   (`app/tdd/plan_advance.py`, Phase 11) and then, for the current item,
   `tdd_phase_incomplete_hook`. It has no latch (Phase 12, user decision, as upstream's Stop
   hooks): every stop of an incomplete cycle is sent back with what is missing, until RunTests
   observes the cycle, the user pauses or aborts, or an error ends the turn. A worker (an
   `agent_id`) is not guarded by it; the coordinator is. Before it, a question nobody could
   answer (a denied `AskUserQuestion`, `awaiting_user_answer`) ends the turn as
   `stop_hook_prevented`: the session waits for the user, as upstream's `-p` ends once the
   model stops after that denial.
2. **Configured hooks can add a block, never clear one.** Their blocking errors are added to
   the TDD hook's; a configured `continue: false` stops the run as `stop_hook_prevented`,
   which the runner records as a failed sub-requirement, not as `completed`. Nothing a
   configured hook returns can make an incomplete cycle terminate `completed`.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias

from langchain_core.messages import HumanMessage

from app.types.hooks import get_stop_hook_message
from app.utils.hooks.hooks import execute_stop_hooks
from app.utils.messages import Message
from app.utils.shell_command import is_aborted

if TYPE_CHECKING:
    from app.query.config import RunConfig
    from app.query.state import LoopState


@dataclass(frozen=True, slots=True)
class StopHookResult:
    """
    The outcome of running stop hooks at turn exit.

    Ported from `StopHookResult` in `reference/claude-code/src/query/stopHooks.ts`.

    Precedence rule (§3.3 of `docs/transition_elaboration_plan.md`):
    `prevent_continuation` wins over `blocking_errors` and blanks them — a hook cannot
    both stop the turn and request a retry.
    """

    blocking_errors: tuple[Message, ...] = ()
    prevent_continuation: bool = False

    def __post_init__(self) -> None:
        if self.prevent_continuation and self.blocking_errors:
            object.__setattr__(self, "blocking_errors", ())


#: Evaluate stop hooks when the model stops without asking for tools.
StopHooks: TypeAlias = Callable[["LoopState", "RunConfig"], Awaitable[StopHookResult]]

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


def _in_plan_mode(state: LoopState) -> bool:
    from app.types.permissions import PermissionMode

    try:
        return bool(state.tool_context.get_app_state().tool_permission_context.mode == PermissionMode.PLAN)
    except Exception:  # noqa: BLE001 - a context without permissions is not planning
        return False


def build_stop_hooks_runner() -> StopHooks:
    """
    The `LoopDeps.stop_hooks` of a production run: the TDD hook, then configured hooks. In plan
    mode the plan advance and the TDD hook are skipped (there is no sub-requirement yet).
    """
    # Imported here: the TDD modules return this module's `StopHookResult`.
    from app.tdd.plan_advance import advance_plan
    from app.tdd.stop_hook import tdd_phase_incomplete_hook
    from app.tools.ask_user_question_tool.ask_user_question_tool import (
        awaiting_user_answer,
    )

    async def run_stop_hooks(state: LoopState, config: RunConfig) -> StopHookResult:
        is_worker = bool(getattr(state.tool_context, "agent_id", None))
        tdd = StopHookResult()
        if not is_worker:
            if awaiting_user_answer(state.messages) is not None:
                # The model asked the user and nobody could answer: the turn ends and waits,
                # as upstream's `-p` ends when the model stops after the denial.
                return StopHookResult(prevent_continuation=True)
            if not _in_plan_mode(state):
                # Plan mode (the Analyst) has no sub-requirement to finish yet: a planning turn
                # ends as upstream's does, on AskUserQuestion or ExitPlanMode.
                advanced = advance_plan(state)
                tdd = advanced if advanced is not None else await tdd_phase_incomplete_hook(state, config)
                if tdd.prevent_continuation:
                    return tdd
        configured = await handle_stop_hooks(state, state.messages, bool(state.stop_hook_active))
        if configured.prevent_continuation:
            return configured
        return StopHookResult(blocking_errors=(*tdd.blocking_errors, *configured.blocking_errors))

    return run_stop_hooks


__all__ = ["build_stop_hooks_runner", "handle_stop_hooks"]
