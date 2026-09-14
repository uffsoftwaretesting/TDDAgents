"""
The loop. Everything else in this package is a system arranged around it.

Ported from `reference/claude-code/src/query.ts` -> `queryLoop`, which is the architecture
rather than a component of it: *"The core of the system is a simple while-loop that calls
the model, runs tools, and repeats. Most of the code, however, lives in the systems around
this loop."*

**This part ships the skeleton and one continue site.** The model is called, its answer is
streamed out, and if it asked for tools they run and their results go back in — `next_turn`,
the only reason that advances `turn_count`. A model that answers without asking for a tool
ends the run at `completed`. The other three continue reasons and the other seven terminal
reasons are named in `app/loop/transitions.py` and produced by later parts; the loop is
runnable and fully testable now, against fakes, with no sandbox and no network.

**One forced divergence, and it is a language constraint rather than a choice.** Upstream's
loop is an async generator that yields messages and *returns* a `Terminal`, which callers
read with `yield*`. Python forbids a value in an async generator's `return` (PEP 525), so
the terminal reason is yielded as a final, distinctly typed `Terminated` event. `drain`
below is the stand-in for `yield*`'s return value, and it raises rather than returning
`None` if a run ever ends without one — that would mean the loop fell out of `while True`,
which no code path may do.
"""

from __future__ import annotations

from dataclasses import replace
from typing import AsyncIterator, TypeAlias

from app.loop.config import RunConfig
from app.loop.deps import LoopDeps
from app.loop.messages import Message, ToolCall, tool_calls_in
from app.loop.state import LoopState
from app.loop.transitions import Continue, Terminal, Terminated, Transition

#: What a run emits: every message as it is produced, then exactly one `Terminated`.
LoopEvent: TypeAlias = Message | Terminated


class LoopNeverTerminated(RuntimeError):
    """
    A run's events ended without a `Terminated`.

    Not a recoverable condition and not a user-facing error: `while True` has no exit that
    is not a terminal reason, so reaching this means an edit introduced one.
    """


async def run_loop(
    state: LoopState, config: RunConfig, deps: LoopDeps
) -> AsyncIterator[LoopEvent]:
    """
    Run until the model stops asking for tools.

    The iteration reads its inputs from `state` at the top and, at the single continue site
    at the bottom, assigns a **complete new** `LoopState`. Nothing is mutated in place and
    no field is left unstated: the reset-or-preserve decision for every one of them is
    visible in that one literal, which is the whole reason the record is shaped this way.
    """
    while True:
        # Everything after the compact boundary. Identical to the full history until Part
        # E2 puts a real slice here; named now so that part changes one expression rather
        # than threading a new variable through the iteration.
        messages_for_query = state.messages

        # The one field upstream reassigns *within* an iteration rather than at a continue
        # site: `toolUseContext = {...toolUseContext, messages: messagesForQuery}`. A tool
        # must see the conversation as it stands now, not as it stood when the turn began.
        state = replace(
            state, tool_context=replace(state.tool_context, messages=messages_for_query)
        )

        assistant_messages: list[Message] = []
        tool_calls: list[ToolCall] = []
        needs_follow_up = False

        async for message in deps.call_model(state, config):
            yield message
            assistant_messages.append(message)
            calls = tool_calls_in(message)
            if calls:
                tool_calls.extend(calls)
                needs_follow_up = True

        if not needs_follow_up:
            # The model answered instead of acting. Upstream reaches its recovery waterfall
            # and its stop hooks here — Parts D5, E7 and F6 — and only then `completed`.
            yield Terminated(reason=Terminal.COMPLETED)
            return

        tool_results: list[Message] = []
        async for result in deps.run_tools(
            tuple(tool_calls), tuple(assistant_messages), state, config
        ):
            yield result
            tool_results.append(result)

        state = LoopState(
            messages=(*messages_for_query, *assistant_messages, *tool_results),
            tool_context=state.tool_context,
            # Preserved: no part of this iteration observed the ledger, and the tool that
            # will write it (Part D2) writes through app state, not through here.
            phase_ledger=state.phase_ledger,
            compaction_tracking=state.compaction_tracking,
            # Reset: a full round of tool calls has gone by, so the model may have changed
            # the context enough for compaction to be worth trying again. Cleared *here*
            # and nowhere else — the stop-hook continue preserves it, and resetting it
            # there is the documented cause of a compact/hook loop that burned thousands
            # of API calls.
            has_attempted_reactive_compact=False,
            # Preserved: whether a hook already blocked this turn is not something running
            # tools answers.
            stop_hook_active=state.stop_hook_active,
            # The asymmetry the whole design rests on: this is the only site that advances
            # the count, so an iteration spent recovering costs nothing against anything
            # that later reads it.
            turn_count=state.turn_count + 1,
            transition=Transition(reason=Continue.NEXT_TURN),
        )


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
