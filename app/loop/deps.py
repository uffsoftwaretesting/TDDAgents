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

from dataclasses import dataclass
from typing import TYPE_CHECKING, AsyncIterator, Callable

from app.loop.config import RunConfig
from app.loop.messages import Message, ToolCall

if TYPE_CHECKING:  # pragma: no cover - import cycle: state imports nothing from here
    from app.loop.state import LoopState

#: Call the model for one iteration and stream back what it says.
#:
#: Takes the whole `LoopState` and the run's `RunConfig` — the `(state, config)` shape the
#: upstream source anticipates when it says the split *"makes future step() extraction
#: tractable — a pure reducer can take (state, event, config)"*. Yielding rather than
#: returning is what lets Part F2 dispatch a tool the moment its content block closes,
#: while the rest of the answer is still streaming.
CallModel = Callable[["LoopState", RunConfig], AsyncIterator[Message]]

#: Execute the tool calls of one iteration and stream back their results.
#:
#: Mirrors `runTools(toolUseBlocks, assistantMessages, canUseTool, toolUseContext)`: the
#: calls to run, the assistant messages that asked for them — a result has to be able to
#: name the message it answers — and the state and config that carry the rest.
RunTools = Callable[
    [tuple[ToolCall, ...], tuple[Message, ...], "LoopState", RunConfig],
    AsyncIterator[Message],
]


@dataclass(frozen=True, slots=True)
class LoopDeps:
    """
    Everything the loop reaches the outside world through.

    Frozen: the seam is chosen once per run. A dependency that could be swapped mid-run
    would make a transcript unexplainable — half the turns answered by one model call and
    half by another, with nothing in the record saying where the switch happened.
    """

    call_model: CallModel
    run_tools: RunTools
