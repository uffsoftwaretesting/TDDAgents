"""
Run-wide app state: the value a tool may read and propose a replacement for.

Ported from `reference/claude-code/src/state/AppStateStore.ts` -> `AppState` and the store's
`getAppState`/`setAppState`. Upstream's `AppState` is the interactive session's whole UI
state; this is the subset the loop and its tools read.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.tdd.ledger import PhaseLedger, TestRunRecord
from app.tdd.plan import TddPlan
from app.types.permissions import ToolPermissionContext
from app.utils.permissions.denial_tracking import DenialTrackingState


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

    `todos` is `TodoWrite`'s store, keyed by agent exactly as upstream keys it by
    `agentId ?? sessionId`, so a subagent's checklist never overwrites the coordinator's.
    """

    phase_ledger: PhaseLedger = field(default_factory=PhaseLedger)
    todos: Mapping[str, tuple[Any, ...]] = field(default_factory=dict)
    #: Upstream `appState.toolPermissionContext`: the run's rules and mode, built once at
    #: entry by `build_tool_permission_context`. It lives here rather than on the
    #: `ToolContext` so a synchronous worker, which reads its parent's app state, is held
    #: to the same rules (`ToolContext.permission_context` still overrides it when set).
    tool_permission_context: ToolPermissionContext = field(default_factory=ToolPermissionContext)
    #: Upstream `appState.denialTracking`: the auto-mode classifier's denial streak and total
    #: (`utils/permissions/denial_tracking.py`), `None` until auto mode first records one.
    denial_tracking: DenialTrackingState | None = None
    #: The last test run the ledger observed (`ledger.TestRunRecord`), written by `RunTests`
    #: together with the ledger; the TDD state is re-attached from it after a compaction.
    last_test_run: TestRunRecord | None = None
    #: Upstream `appState.tasks` (`Record<string, TaskState>`): the run's background tasks by
    #: id (`app/tasks/`). Written through the root setter even by a background worker,
    #: as upstream's `setAppStateForTasks` is (Phase 8).
    tasks: Mapping[str, Any] = field(default_factory=dict)
    #: The session's sub-requirements (`app/tdd/plan.py`, Phase 11): which are done, which is
    #: current. Shared with every worker, as the ledger is.
    tdd_plan: TddPlan = field(default_factory=TddPlan)
    #: `/pause` (Phase 11): the stop hook lets a turn end without finishing the plan, and the
    #: run terminates `stop_hook_prevented`, never `completed`.
    tdd_paused: bool = False
    #: The fingerprint of the workspace's test files when the current sub-requirement started
    #: (Phase 12, `app/tdd/suite_fingerprint.py`): RunTests ends RED only once they changed.
    #: `None` when it could not be taken.
    tdd_item_test_baseline: str | None = None


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
