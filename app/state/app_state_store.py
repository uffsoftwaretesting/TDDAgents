"""
Run-wide app state: the value a tool may read and propose a replacement for.

Ported from `reference/claude-code/src/state/AppStateStore.ts` -> `AppState` and the store's
`getAppState`/`setAppState`. Upstream's `AppState` is the interactive session's whole UI
state; this is the subset the loop and its tools read.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from app.bootstrap.state import SessionBootstrap
from app.tdd.ledger import PhaseLedger, TestRunRecord
from app.tdd.plan import TddPlan
from app.tdd.project_config import ProjectConfig
from app.types.permissions import ToolPermissionContext
from app.utils.file_history import FileHistoryState
from app.utils.permissions.denial_tracking import DenialTrackingState
from app.utils.sdk_event_queue import SdkEventQueue

if TYPE_CHECKING:
    from app.types.plugin import LoadedPlugin, PluginError

#: An installation's progress (`installationStatus` entries).
InstallationState = Literal["pending", "installing", "installed", "failed"]


@dataclass(frozen=True, slots=True)
class MarketplaceInstallationStatus:
    """`AppState.plugins.installationStatus.marketplaces[]`."""

    name: str
    status: InstallationState
    error: str | None = None


@dataclass(frozen=True, slots=True)
class PluginInstallationStatus:
    """`AppState.plugins.installationStatus.plugins[]`."""

    id: str
    name: str
    status: InstallationState
    error: str | None = None


@dataclass(frozen=True, slots=True)
class InstallationStatus:
    """`AppState.plugins.installationStatus`: the background marketplace/plugin installations."""

    marketplaces: tuple[MarketplaceInstallationStatus, ...] = ()
    plugins: tuple[PluginInstallationStatus, ...] = ()


@dataclass(frozen=True, slots=True)
class PluginsState:
    """
    Upstream `appState.plugins`: the loaded plugins, their commands, the plugin errors, the
    background installation status, and `needs_refresh` (plugin state on disk changed and the
    active components are stale until `/reload-plugins` or `refresh_active_plugins`).
    `commands` stays empty until the stage-3 command integration fills it.
    """

    enabled: tuple[LoadedPlugin, ...] = ()
    disabled: tuple[LoadedPlugin, ...] = ()
    commands: tuple[Any, ...] = ()
    errors: tuple[PluginError, ...] = ()
    installation_status: InstallationStatus = field(default_factory=InstallationStatus)
    needs_refresh: bool = False


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
    #: The default is `getDefaultAppState`'s `{...getEmptyToolPermissionContext(), mode}`: the
    #: dataclass's own defaults are exactly that empty context (`app.tool` imports this module,
    #: so the factory cannot be `get_empty_tool_permission_context` itself).
    tool_permission_context: ToolPermissionContext = field(default_factory=ToolPermissionContext)
    #: Upstream `appState.denialTracking`: the auto-mode classifier's denial streak and total
    #: (`utils/permissions/denial_tracking.py`), `None` until auto mode first records one.
    denial_tracking: DenialTrackingState | None = None
    #: The last test run the ledger observed (`ledger.TestRunRecord`), written by `RunTests`
    #: together with the ledger; the TDD state is re-attached from it after a compaction.
    last_test_run: TestRunRecord | None = None
    #: The session's test floor (2026-11-08, user decision): the latest `RunTests` run that recorded the
    #: most tests so far (`TestRunRecord.tests`, with its test files' fingerprint). It carries across
    #: sub-requirements (a change of the current item never resets it) and is never restored on resume.
    #: `RunTests` is its only writer: it rises with every run whose report records as many tests or more,
    #: and drops only on an accepted decrease (a REFACTOR run met with changed test files, or a RED run that
    #: counts, which re-bases it to its count).
    test_floor: TestRunRecord | None = None
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
    #: The project's approved configuration (`app/tdd/project_config.py`, 2026-11-03): its setup and
    #: test commands and which files are tests. Written by `ExitPlanMode` with the plan and loaded
    #: from `.tddagents/project.json` when a session opens. `None` while undeclared: RunTests and the
    #: phase tier then fail closed whenever a sub-requirement is current.
    project_config: ProjectConfig | None = None
    #: Why `.tddagents/project.json` gave no configuration when it was edited into an invalid one during
    #: the session (`app/tdd/project_config_watcher.py`, 2026-11-08): the refusals name these.
    project_config_issues: tuple[str, ...] = ()
    #: Upstream `STATE.hasExitedPlanMode` (`bootstrap/state.ts`), per run here: plan mode was left
    #: this session, so re-entering it attaches `plan_mode_reentry`.
    has_exited_plan_mode: bool = False
    #: Upstream `STATE.needsPlanModeExitAttachment`: plan mode was just left, so the next turn
    #: attaches `plan_mode_exit` once.
    needs_plan_mode_exit_attachment: bool = False
    #: Upstream `appState.fileHistory` (`utils/fileHistory.ts`): the session's file checkpoints,
    #: a snapshot per user prompt and the files tracked since. Written only through
    #: `update_file_history_state_for` (a worker's updater is the no-op stub).
    file_history: FileHistoryState = field(default_factory=FileHistoryState)
    #: Upstream's process-wide `STATE` reads (`getSessionId`, `getOriginalCwd`,
    #: `getIsNonInteractiveSession`), per run here: set once when the run is opened.
    bootstrap: SessionBootstrap = field(default_factory=SessionBootstrap)
    #: `sdkEventQueue.ts`'s queue, the run's: task and session-state events for the SDK stream.
    sdk_events: SdkEventQueue = field(default_factory=SdkEventQueue, compare=False)
    #: Upstream `appState.plugins` (`app/utils/plugins/refresh.py` writes it).
    plugins: PluginsState = field(default_factory=PluginsState)
    #: Upstream `appState.mcp.pluginReconnectKey`: bumped by `refresh_active_plugins` so the MCP
    #: connections pick up plugin servers (the stage-3 MCP integration reads it).
    mcp_plugin_reconnect_key: int = 0


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

    #: `createStore`'s listeners, called after every change.
    listeners: list[Callable[[], None]] = field(default_factory=list)

    def update(self, updater: Callable[[AppState], AppState]) -> None:
        """`setState`: a replacement, and the listeners told when it changed anything."""
        previous = self.state
        self.state = updater(previous)
        if self.state is not previous:
            for listener in tuple(self.listeners):
                listener()

    def subscribe(self, listener: Callable[[], None]) -> Callable[[], None]:
        """`subscribe`: `listener` runs after each change; the result unsubscribes."""
        self.listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self.listeners:
                self.listeners.remove(listener)

        return unsubscribe


def discard_app_state_update(updater: Callable[[AppState], AppState]) -> None:
    """
    A setter that drops the write.

    This is what an isolated worker is handed (§3.5): its writes must not reach the parent
    store. Named rather than written as a lambda at the call site so the intent shows up in
    a stack trace and so exactly one place has to be audited when asking whether isolation
    holds.
    """
