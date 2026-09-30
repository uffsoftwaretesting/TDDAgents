"""
The loop's I/O seam, so it can be driven by fakes.

Ported from `reference/claude-code/src/query/deps.ts` -> `QueryDeps` / `productionDeps`.
Explicit constructor injection rather than a container or a patching library, and the
in-source note explains why the record starts small: its scope is *"intentionally narrow
(4 deps) to prove the pattern"*, with followups adding `runTools`, `handleStopHooks`,
`logEvent` and the rest. The same applies here in reverse order — this part needs the model
call and the tool runner, and Part A4 widens the record to the full seam (compaction, uuid,
now, stop hooks, the event sink) without changing `run_loop`'s signature.

There is no `production_deps()` yet, and that absence is deliberate: the real model call is
Part F1 and the real tool runner is Part B5. A factory today could only return placeholders,
which is how a placeholder ends up in a run.
"""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, TypeAlias

from app.loop.config import RunConfig
from app.loop.messages import Message, ToolCall
from app.loop.state import CompactionTracking

if TYPE_CHECKING:  # pragma: no cover - import cycle: state imports nothing from here
    from app.loop.state import LoopState

#: Call the model for one iteration and stream back what it says.
#:
#: Takes the whole `LoopState` and the run's `RunConfig` — the `(state, config)` shape the
#: upstream source anticipates when it says the split *"makes future step() extraction
#: tractable — a pure reducer can take (state, event, config)"*. Yielding rather than
#: returning is what lets Part F2 dispatch a tool the moment its content block closes,
#: while the rest of the answer is still streaming.
CallModel: TypeAlias = Callable[["LoopState", RunConfig], AsyncIterator[Message]]

#: Execute the tool calls of one iteration and stream back their results.
#:
#: Mirrors `runTools(toolUseBlocks, assistantMessages, canUseTool, toolUseContext)`: the
#: calls to run, the assistant messages that asked for them — a result has to be able to
#: name the message it answers — and the state and config that carry the rest.
RunTools: TypeAlias = Callable[
    [tuple[ToolCall, ...], tuple[Message, ...], "LoopState", RunConfig],
    AsyncIterator[Message],
]


@dataclass(frozen=True, slots=True)
class CompactionResult:
    """
    The outcome of a compaction evaluation or pass.

    Ported from `CompactionResult` in
    `reference/claude-code/src/services/compact/compact.ts` and `autoCompact.ts`.
    """

    compacted: bool
    messages: tuple[Message, ...]
    tracking: CompactionTracking | None = None


#: Attempt auto-compaction on conversation history if needed or requested.
Compact: TypeAlias = Callable[["LoopState", RunConfig], Awaitable[CompactionResult]]


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
StopHooks: TypeAlias = Callable[["LoopState", RunConfig], Awaitable[StopHookResult]]

#: Generate a unique identifier string (e.g. turn_id, call_id).
UuidGenerator: TypeAlias = Callable[[], str]

#: Provide the current timestamp in seconds.
NowProvider: TypeAlias = Callable[[], float]

#: Sink for telemetry, transitions, and the event log (Part L1).
EventSink: TypeAlias = Callable[[Any], None]


@dataclass(frozen=True, slots=True)
class LoopDeps:
    """
    Everything the loop reaches the outside world through.

    Widened in Part A4 to the full seam: model calls, tool execution, compaction,
    stop hooks, uuid generation, clock provider, and the event sink.

    Frozen: the seam is chosen once per run. A dependency that could be swapped mid-run
    would make a transcript unexplainable — half the turns answered by one model call and
    half by another, with nothing in the record saying where the switch happened.
    """

    call_model: CallModel
    run_tools: RunTools
    compact: Compact
    stop_hooks: StopHooks
    uuid: UuidGenerator
    now: NowProvider
    emit_event: EventSink
