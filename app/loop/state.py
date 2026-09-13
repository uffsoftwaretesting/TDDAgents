"""
The per-iteration record, and the reason the previous iteration produced it.

Second of the three kinds in §3.1 of `docs/transition_elaboration_plan.md`, ported from
`reference/claude-code/src/query.ts` -> `State`. Two properties carry over from that port,
and both are enforced here by construction rather than by review.

**Replaced wholesale, never mutated.** Every continue site assigns a complete new record.
Upstream's reason is reviewability: with a full literal at each site, the
reset-or-preserve decision for every single field is visible in the diff instead of being
the absence of an assignment. The in-source comment records the bug that made this
non-negotiable — a flag reset at the wrong site produced `compact -> still too long ->
error -> stop hook blocking -> compact -> ...`, *"burning thousands of API calls"*. That
is what Part A7's matrix exists to keep from coming back.

Python only half-supports the discipline: `frozen=True` stops rebinding, not mutation of
what a field points at. So the collections here are immutable too. `state.messages.append`
raising `AttributeError` is the point — a partial in-place edit is exactly the failure
`frozen` appears to prevent and does not.

**No field has a default.** Every construction site must name every field, including the
ones it is preserving unchanged. Defaults would let a future continue site stay silent
about a field, which is precisely the silence this design is built to remove. The single
starting record is built by `initial_loop_state` below, so "what the run begins as" also
has exactly one definition.

Fields that existed only to be compared against a ceiling are absent, because there are no
ceilings: `output_limit_recovery_count` and `tdd_block_count` from §3.1's list, and
`consecutiveFailures` from the compaction record below. How often a recovery path fired is
recoverable from the transition history, which is where Part L2 reads it from anyway.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.loop.context import ToolContext
from app.loop.ledger import PhaseLedger
from app.loop.messages import Message


@dataclass(frozen=True, slots=True)
class CompactionTracking:
    """
    What the compaction system needs to remember between iterations.

    Ported from `AutoCompactTrackingState` in
    `reference/claude-code/src/services/compact/autoCompact.ts`. `compacted` says whether
    this turn compacted, and `turn_id` / `turn_counter` identify the turn the decision was
    made for; the compaction path of Part E7 reads all three.

    Upstream's fourth member, `consecutiveFailures`, is deliberately not here. Its only
    reader in the whole compaction path is the circuit-breaker comparison in
    `autoCompact.ts` -> `autoCompactIfNeeded`, against a maximum this architecture does not
    have; every other mention of it is plumbing that carries the number to that one
    comparison. Without the bound it would be a field nothing reads, and the loop is
    unbounded by decision, so it is dropped rather than carried.
    """

    compacted: bool
    turn_id: str
    turn_counter: int


@dataclass(frozen=True, slots=True)
class Transition:
    """
    Why the previous iteration continued.

    Carried on the state record purely so a test can assert that a recovery path fired,
    without reading message contents to infer it — upstream added it for the same reason
    and says so in-source. Part L2 then gets its flow classification for free: F1 and F2
    are shapes of transition history, not something a wrapper function has to record.

    `reason` is a plain string only until Part A2 replaces it with the closed `Continue`
    vocabulary; the whole point of that part is that this stops being a string. It is a
    record rather than a bare field so the shape stays open to a continue site that has
    something to say beyond its name — but nothing is declared here before a site sets it,
    which is why the record has exactly one member today.
    """

    reason: str


@dataclass(frozen=True, slots=True)
class LoopState:
    """
    Everything one iteration of the loop needs from the previous one.

    `phase_ledger` is the loop's copy of the ledger in `app/loop/ledger.py`; the tool-side
    copy lives in `AppState`. Both exist from this part on so that A7's matrix covers the
    ledger from the first continue site, even though nothing writes it until Part D.
    """

    messages: tuple[Message, ...]
    tool_context: ToolContext
    phase_ledger: PhaseLedger
    compaction_tracking: CompactionTracking | None
    has_attempted_reactive_compact: bool
    stop_hook_active: bool | None
    turn_count: int
    transition: Transition | None


def initial_loop_state(messages: tuple[Message, ...], tool_context: ToolContext) -> LoopState:
    """
    The record a run starts from.

    `turn_count` starts at 1 rather than 0, matching upstream: the first model call is a
    turn that is already under way, not a turn that has yet to happen. It matters for the
    asymmetry Part A6 rests on — the count advances only at `next_turn`, so an iteration
    spent recovering costs nothing against any limit that later reads it.

    `transition` is `None` on the first iteration and only there, which is what makes it a
    reliable "is this the first pass" test for code that needs one.
    """
    return LoopState(
        messages=messages,
        tool_context=tool_context,
        phase_ledger=PhaseLedger(),
        compaction_tracking=None,
        has_attempted_reactive_compact=False,
        stop_hook_active=None,
        turn_count=1,
        transition=None,
    )
