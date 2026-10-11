"""
The loop. Everything else in this package is a system arranged around it.

Ported from `reference/claude-code/src/query.ts` -> `queryLoop`, which is the architecture
rather than a component of it: *"The core of the system is a simple while-loop that calls
the model, runs tools, and repeats. Most of the code, however, lives in the systems around
this loop."*

**Parts A1–A7.** The model is called, its answer is streamed out, and if it asked for tools
they run and their results go back in (`next_turn`, the only reason that advances `turn_count`).
All terminal reasons and continue sites are handled according to the closed vocabulary in
`app/loop/transitions.py`.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, AsyncIterator, TypeAlias

from langchain_core.messages import HumanMessage, SystemMessage

from app.loop.config import RunConfig
from app.loop.deps import LoopDeps
from app.loop.messages import Message, ToolCall, tool_calls_in
from app.loop.retry import FallbackTriggeredError
from app.loop.state import LoopState
from app.loop.streaming.abort import AbortController, bridge_cancel_token
from app.loop.streaming.executor import StreamingToolExecutor
from app.loop.tools.execution import yield_missing_tool_results
from app.loop.streaming.withhold import (
    is_max_output_tokens,
    is_prompt_too_long,
    is_withheld_error,
    take_withhold_gate_snapshot,
)
from app.loop.transitions import Continue, Terminal, Terminated, Transition

#: What a run emits: every message as it is produced, then exactly one `Terminated`.
LoopEvent: TypeAlias = Message | Terminated

#: `query.ts`'s repair text when a model fallback abandons a turn's tool calls.
FALLBACK_REPAIR_MESSAGE = "Model fallback triggered"


class LoopNeverTerminated(RuntimeError):
    """
    A run's events ended without a `Terminated`.

    Not a recoverable condition and not a user-facing error: `while True` has no exit that
    is not a terminal reason, so reaching this means an edit introduced one.
    """


class BlockingLimitError(Exception):
    """Raised when context exceeds the blocking token limit."""


class PromptTooLongError(Exception):
    """Raised when the prompt exceeds the model context window limit."""


class ModelCallError(Exception):
    """Raised when the model provider call encounters an unrecoverable failure."""


def _is_api_error(message: Message, error_type: str) -> bool:
    if getattr(message, "api_error", None) == error_type:
        return True
    additional = getattr(message, "additional_kwargs", None)
    if isinstance(additional, dict) and additional.get("api_error") == error_type:
        return True
    return False


def _has_hook_stopped(message: Message) -> bool:
    if getattr(message, "hook_stopped_continuation", None) is True:
        return True
    additional = getattr(message, "additional_kwargs", None)
    return isinstance(additional, dict) and additional.get("hook_stopped_continuation") is True


def is_api_error_message(message: Message) -> bool:
    """
    Upstream's `lastMessage?.isApiErrorMessage` — a plain flag, not a helper predicate.

    `query.ts` reads the field directly on the last assistant message; the type guard
    `isSyntheticApiErrorMessage` in `utils/messages.ts` exists but is not what the loop
    consults, so this stays a flag read.
    """
    if getattr(message, "is_api_error_message", None) is True:
        return True
    additional = getattr(message, "additional_kwargs", None)
    if not isinstance(additional, dict):
        return False
    return additional.get("is_api_error_message") is True or bool(additional.get("api_error"))


def refresh_tool_pool(context: Any) -> Any:
    """
    Upstream's between-turns `refreshTools` (`query.ts`), identity check included.

    The context is replaced **only when the callback returns a different object**. An
    unchanged roster keeps its tuple, so the serialized tool block is byte-identical and
    the prompt cache survives. In this loop the callback is what re-derives the
    phase-filtered pool, so a `RunTests` call that moved the ledger changes which tools the
    next turn is offered — without it, a RED-filtered pool would outlive RED.
    """
    refresh = getattr(context, "refresh_tools", None)
    if refresh is None:
        return context
    try:
        refreshed = refresh()
    except Exception:
        return context
    if refreshed is None or refreshed is getattr(context, "tools", None):
        return context
    return replace(context, tools=tuple(refreshed))


async def run_loop(
    state: LoopState, config: RunConfig, deps: LoopDeps
) -> AsyncIterator[LoopEvent]:
    """
    Run until a terminal reason is reached.

    The iteration reads its inputs from `state` at the top and, at each continue site,
    assigns a **complete new** `LoopState`. Nothing is mutated in place and no field is
    left unstated: the reset-or-preserve decision for every one of them is visible in
    that one literal, which is the whole reason the record is shaped this way.
    """
    while True:
        if state.tool_context.cancel.cancelled:
            term = Terminated(reason=Terminal.ABORTED_STREAMING)
            deps.emit_event(term)
            yield term
            return

        messages_for_query = state.messages

        state = replace(
            state, tool_context=replace(state.tool_context, messages=messages_for_query)
        )

        assistant_messages: list[Message] = []
        tool_calls: list[ToolCall] = []
        needs_follow_up = False
        blocking_limit_raised = False
        prompt_too_long_raised = False

        # Invariant F5: Hoist recovery gate snapshot before the stream loop
        gate_snapshot = take_withhold_gate_snapshot(state, config)

        turn_controller = AbortController()
        bridge_cancel_token(state.tool_context.cancel, turn_controller)

        def new_streaming_executor() -> StreamingToolExecutor | None:
            if not getattr(state.tool_context, "tools", None):
                return None
            return StreamingToolExecutor(
                tools=state.tool_context.tools,
                tool_context=state.tool_context,
                turn_abort_controller=turn_controller,
            )

        streaming_executor: StreamingToolExecutor | None = new_streaming_executor()

        withheld_messages: list[Message] = []
        early_completed_tool_results: list[Message] = []

        # Upstream's `while (attemptWithFallback)` sits *inside* one loop iteration: a model
        # fallback retries the same request, it does not advance the turn. Keeping it here
        # rather than at a continue site is why no fallback reason joins the continue
        # vocabulary — upstream's has none either.
        attempt_with_fallback = True
        model_failed: Exception | None = None
        while attempt_with_fallback:
            attempt_with_fallback = False
            try:
                async for message in deps.call_model(state, config):
                    # Invariant F5: Withhold recoverable errors from caller emission
                    if is_withheld_error(message, gate_snapshot):
                        withheld_messages.append(message)
                    else:
                        yield message

                    assistant_messages.append(message)
                    calls = tool_calls_in(message)
                    if calls:
                        tool_calls.extend(calls)
                        needs_follow_up = True
                        # Invariant F2: Dispatch at content-block close
                        if streaming_executor is not None and not turn_controller.signal.aborted:
                            for call in calls:
                                streaming_executor.add_tool(call, message)
                            for early_res in streaming_executor.get_completed_results():
                                yield early_res
                                early_completed_tool_results.append(early_res)

            except BlockingLimitError:
                blocking_limit_raised = True
            except PromptTooLongError:
                prompt_too_long_raised = True
            except FallbackTriggeredError as fallback:
                # `query.ts`'s fallback branch, in its order: repair history for every
                # tool_use already emitted, clear the partial turn, discard the executor and
                # build a new one — a reused executor would later yield orphan results
                # carrying the abandoned call ids — then retry on the fallback model.
                for repair in yield_missing_tool_results(
                    tuple(tool_calls), set(), message=FALLBACK_REPAIR_MESSAGE
                ):
                    yield repair
                assistant_messages.clear()
                tool_calls.clear()
                withheld_messages.clear()
                early_completed_tool_results.clear()
                needs_follow_up = False
                if streaming_executor is not None:
                    streaming_executor.discard()
                    streaming_executor = new_streaming_executor()
                config = replace(config, model=fallback.fallback_model)
                # Upstream yields a system message rather than logging an event, so the
                # switch is visible in the transcript. It is not appended to the history
                # sent back to the provider.
                yield SystemMessage(
                    content=(
                        f"Switched to {fallback.fallback_model} due to high demand for "
                        f"{fallback.original_model}"
                    )
                )
                attempt_with_fallback = True
            except Exception as exc:
                model_failed = exc

        if model_failed is not None:
            # Upstream repairs before it reports: the stream may already have emitted a
            # tool_use whose tool_result will now never arrive, and the next request would
            # be malformed without one (invariant B7).
            if streaming_executor is not None:
                streaming_executor.discard()
            for repair in yield_missing_tool_results(
                tuple(tool_calls), set(), message=str(model_failed)
            ):
                yield repair
            term = Terminated(reason=Terminal.MODEL_ERROR)
            deps.emit_event(term)
            yield term
            return

        if state.tool_context.cancel.cancelled or turn_controller.signal.aborted:
            if streaming_executor is not None:
                async for res in streaming_executor.get_remaining_results():
                    yield res
                    early_completed_tool_results.append(res)
            term = Terminated(reason=Terminal.ABORTED_STREAMING)
            deps.emit_event(term)
            yield term
            return

        if blocking_limit_raised or any(_is_api_error(m, "blocking_limit") for m in assistant_messages):
            term = Terminated(reason=Terminal.BLOCKING_LIMIT)
            deps.emit_event(term)
            yield term
            return

        has_ptl = (
            prompt_too_long_raised
            or any(
                _is_api_error(m, "prompt_too_long") or is_prompt_too_long(m)
                for m in assistant_messages
            )
        )
        if has_ptl:
            # Invariant F5: Recovery decision reads the SAME gate snapshot
            if gate_snapshot.reactive_compact_enabled and not state.has_attempted_reactive_compact:
                compact_res = await deps.compact(state, config)
                if compact_res.compacted:
                    tracking = (
                        compact_res.tracking
                        if compact_res.tracking is not None
                        else state.compaction_tracking
                    )
                    state = LoopState(
                        messages=compact_res.messages,
                        tool_context=state.tool_context,
                        phase_ledger=state.tool_context.get_app_state().phase_ledger,
                        compaction_tracking=tracking,
                        has_attempted_reactive_compact=True,
                        stop_hook_active=None,
                        turn_count=state.turn_count,
                        transition=Transition(reason=Continue.REACTIVE_COMPACT_RETRY),
                    )
                    deps.emit_event(state.transition)
                    continue

            # Recovery exhausted/failed: surface withheld error if present and exit cleanly
            for wm in withheld_messages:
                yield wm
            term = Terminated(reason=Terminal.PROMPT_TOO_LONG)
            deps.emit_event(term)
            yield term
            return

        has_max_tokens = any(
            _is_api_error(m, "max_output_tokens") or is_max_output_tokens(m)
            for m in assistant_messages
        )
        if has_max_tokens:
            if gate_snapshot.max_output_tokens_recovery_enabled:
                resume_msg = HumanMessage(content="Please continue from where you left off.")
                state = LoopState(
                    messages=(*messages_for_query, *assistant_messages, resume_msg),
                    tool_context=state.tool_context,
                    phase_ledger=state.tool_context.get_app_state().phase_ledger,
                    compaction_tracking=state.compaction_tracking,
                    has_attempted_reactive_compact=state.has_attempted_reactive_compact,
                    stop_hook_active=None,
                    turn_count=state.turn_count,
                    transition=Transition(reason=Continue.MAX_OUTPUT_TOKENS_RECOVERY),
                )
                deps.emit_event(state.transition)
                continue

            for wm in withheld_messages:
                yield wm

        if state.tool_context.cancel.cancelled or turn_controller.signal.aborted:
            term = Terminated(reason=Terminal.ABORTED_STREAMING)
            deps.emit_event(term)
            yield term
            return

        if not needs_follow_up:
            # The anti-spiral guard, `query.ts`:
            #   "Skip stop hooks when the last message is an API error ... The model never
            #    produced a real response — hooks evaluating it create a death spiral:
            #    error → hook blocking → retry → error → …"
            # Upstream then returns `completed`, because for it the turn did end with an
            # assistant message. **This loop returns `model_error` instead**, at the user's
            # direction: the runner maps `completed` to a passed sub-requirement, so
            # reporting a provider outage that way would claim a TDD cycle nobody observed.
            # The guard's actual fix — never let stop hooks see an API error — is kept whole.
            if assistant_messages and is_api_error_message(assistant_messages[-1]):
                term = Terminated(reason=Terminal.MODEL_ERROR)
                deps.emit_event(term)
                yield term
                return

            stop_hook_res = await deps.stop_hooks(state, config)
            if stop_hook_res.prevent_continuation:
                term = Terminated(reason=Terminal.STOP_HOOK_PREVENTED)
                deps.emit_event(term)
                yield term
                return
            if stop_hook_res.blocking_errors:
                state = LoopState(
                    messages=(*messages_for_query, *assistant_messages, *stop_hook_res.blocking_errors),
                    tool_context=state.tool_context,
                    phase_ledger=state.tool_context.get_app_state().phase_ledger,
                    compaction_tracking=state.compaction_tracking,
                    has_attempted_reactive_compact=state.has_attempted_reactive_compact,
                    stop_hook_active=True,
                    turn_count=state.turn_count,
                    transition=Transition(reason=Continue.STOP_HOOK_BLOCKING),
                )
                deps.emit_event(state.transition)
                continue

            term = Terminated(reason=Terminal.COMPLETED)
            deps.emit_event(term)
            yield term
            return

        tool_results: list[Message] = list(early_completed_tool_results)
        if streaming_executor is not None:
            async for result in streaming_executor.get_remaining_results():
                yield result
                tool_results.append(result)
        else:
            async for result in deps.run_tools(
                tuple(tool_calls), tuple(assistant_messages), state, config
            ):
                yield result
                tool_results.append(result)

        if state.tool_context.cancel.cancelled or turn_controller.signal.aborted:
            term = Terminated(reason=Terminal.ABORTED_TOOLS)
            deps.emit_event(term)
            yield term
            return

        if any(_has_hook_stopped(res) for res in tool_results):
            term = Terminated(reason=Terminal.HOOK_STOPPED)
            deps.emit_event(term)
            yield term
            return

        # Upstream refreshes the roster here and nowhere else: once per iteration, after the
        # tools have run and immediately before the next turn is built — never on a recovery
        # continue. For this loop it is what re-derives the phase-filtered pool, so a
        # `RunTests` call that moved RED to GREEN changes the tools the next turn is offered.
        refreshed_context = refresh_tool_pool(state.tool_context)

        state = LoopState(
            messages=(*messages_for_query, *assistant_messages, *tool_results),
            tool_context=refreshed_context,
            phase_ledger=refreshed_context.get_app_state().phase_ledger,
            compaction_tracking=state.compaction_tracking,
            has_attempted_reactive_compact=False,
            stop_hook_active=state.stop_hook_active,
            turn_count=state.turn_count + 1,
            transition=Transition(reason=Continue.NEXT_TURN),
        )
        deps.emit_event(state.transition)


async def drain(events: AsyncIterator[LoopEvent]) -> Terminated:
    """
    Consume a run and return how it ended, discarding the messages.

    The Python stand-in for upstream's `const terminal = yield* queryLoop(...)`. Callers
    that want the messages iterate `run_loop` directly and watch for the `Terminated`;
    this is for the ones that only need the verdict, which is most tests and the session
    shell of Part K1.
    """
    async for event in events:
        if isinstance(event, Terminated):
            return event
    raise LoopNeverTerminated("the loop produced no Terminated event")
