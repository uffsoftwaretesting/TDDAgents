"""
The ambient context threaded through the loop and into every tool call.

This is the third of the three kinds in §3.1 of `docs/transition_elaboration_plan.md`, and
the only mutable one. `RunConfig` is frozen at entry and `LoopState` is replaced wholesale
at each continue site; `ToolContext` is the thing that is *re-spread* — copied with one or
two members changed — as an iteration proceeds. Upstream writes that as an object spread
(`{...toolUseContext, messages}`); here it is `dataclasses.replace`, which is the same
operation with a name.

Ported from `reference/claude-code/src/Tool.ts` -> `ToolUseContext`, which carries roughly
fifty members. This is the minimum surface named in Part B3 — abort, app state, messages,
in-flight ids — and it is deliberately the whole of it. The tool-facing members (workspace,
permission mode, hook dispatcher, result counters) arrive with the tool layer that needs
them, so that each one enters against a call site rather than against a guess.

Two members are callables rather than data, and that is load-bearing. Upstream injects
`getAppState`/`setAppState` because an async subagent is handed a *no-op* setter: its
writes must not reach the parent's store. Making them parameters rather than a reference to
a module-level store is what lets Part I3 assemble an isolated worker without a conditional
anywhere in the tool path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from app.loop.ledger import PhaseLedger
from app.loop.messages import Message


@dataclass(slots=True)
class CancelToken:
    """
    Cooperative cancellation, checked between units of work.

    Deliberately not a thread-kill. A tool already in flight against the sandbox has paid
    for its round trip, and abandoning it mid-write would leave the workspace in a state no
    record describes. Part F3 builds the three-controller abort tree — turn, siblings,
    single tool — out of separate instances of this, which is why cancellation is a token
    passed by reference rather than a flag on the state record.
    """

    cancelled: bool = False

    def cancel(self) -> None:
        self.cancelled = True


@dataclass(frozen=True, slots=True)
class AppState:
    """
    Run-wide state a tool is allowed to read and write, as a value.

    Frozen on purpose: writes go through `AppStateStore.update`, which takes a function
    from the old state to the new one. A tool therefore cannot mutate what it was handed —
    it can only propose a replacement — and a subagent whose setter is a no-op silently
    fails to write rather than silently corrupting a shared object.

    The phase ledger is the mirror described in §3.3: `LoopState` carries the copy the loop
    reads, and this is the copy a tool can write. Part D1 settles which way the mirror is
    reconciled; Part A1 only guarantees both sides exist and hold the same type.
    """

    phase_ledger: PhaseLedger = PhaseLedger()


@dataclass(slots=True)
class AppStateStore:
    """
    Holds the current `AppState` and applies updates as replacements.

    `update` takes the updater function rather than the new value so that a caller who has
    not seen the latest state cannot clobber it — the same reason upstream's `setAppState`
    is `(prev) => next`. The store is per run, not per process: nothing here is global, so
    two runs in one interpreter cannot see each other's state.
    """

    state: AppState = AppState()

    def get(self) -> AppState:
        return self.state

    def update(self, updater: Callable[[AppState], AppState]) -> None:
        self.state = updater(self.state)


def discard_app_state_update(updater: Callable[[AppState], AppState]) -> None:
    """
    A setter that drops the write.

    This is what an isolated worker is handed (§3.5): its writes must not reach the parent
    store. Named rather than written as a lambda at the call site so the intent shows up in
    a stack trace and so exactly one place has to be audited when asking whether isolation
    holds.
    """


@dataclass(slots=True)
class ToolContext:
    """
    What the loop threads through a tool call.

    Mutable by design — it is the one member of the three-way split that is. `messages` is
    an immutable snapshot rather than a live list, so a tool cannot append to the
    conversation behind the loop's back; the loop hands down a new context when the
    conversation moves on.

    `in_flight_tool_ids` is the exception and is mutable: tools start and finish inside a
    single iteration, and the set is the record of which ones are outstanding right now.
    Part B5 decides how concurrent updates to it are coordinated, when there is a scheduler
    to coordinate them.
    """

    cancel: CancelToken
    get_app_state: Callable[[], AppState]
    set_app_state: Callable[[Callable[[AppState], AppState]], None]
    messages: tuple[Message, ...] = ()
    in_flight_tool_ids: set[str] = field(default_factory=set)


def tool_context_for(store: AppStateStore, *, messages: tuple[Message, ...] = ()) -> ToolContext:
    """
    Build a context wired to a store, which is what the main loop wants.

    A worker that must not write the parent's state is built by hand instead, with
    `discard_app_state_update` as its setter; that asymmetry is the isolation boundary and
    is meant to be visible at the call site.
    """
    return ToolContext(
        cancel=CancelToken(),
        get_app_state=store.get,
        set_app_state=store.update,
        messages=messages,
    )
