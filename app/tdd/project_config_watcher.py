"""
Watching `.tddagents/project.json` during a session, so an approved edit is always read (2026-11-08).

Ported from `reference/claude-code/src/utils/cronScheduler.ts` -> `createCronScheduler`'s file
watch (`chokidar.watch(path, { ignoreInitial: true, awaitWriteFinish: { stabilityThreshold:
FILE_STABILITY_MS } })` with `add` / `change` -> `load(false)` and `unlink` -> no tasks), the upstream
shape for one project file a tool writes and the user (or the model, with the user's approval) may
edit: `.claude/scheduled_tasks.json`. `FILE_STABILITY_MS` is upstream's constant.

Why (the md→pdf rerun, 2026-11-08): the protected `.tddagents/` folder asks before a write, as
upstream's `.claude/` does (`app/utils/permissions/filesystem.py`, the safety check), so an approved
Edit changed `project.json`, but the session had read it once, at open, and the edit had no effect.

**What a change does** (`apply_project_config_text`, user decisions 2026-11-08):

* the file is parsed as at session open (`parse_project_config_text`). A valid configuration that
  differs from the session's becomes `AppState.project_config`;
* an invalid file contributes nothing, as an invalid settings file does upstream
  (`parseSettingsFileUncached` -> `settings: null`): the configuration is `None`, so the phase tier and
  RunTests fail closed while a sub-requirement is current, and the file's issues are kept
  (`AppState.project_config_issues`) for the refusals to name (`invalid_project_config_note`);
* a deleted file is no configuration, as upstream's `unlink` leaves no tasks;
* when the declared test patterns change while a sub-requirement is current, RED's baseline is taken
  again under the new patterns (`apply_project_config`), so the change itself is never evidence that
  the tests changed. The ledger's phase and evidence stay.

The reload is idempotent, as cron's is: TDDAgents' own write (ExitPlanMode, `save_project_config`) is
not marked internal, and its echo is the configuration the session already holds, so nothing changes.

*Divergences:* one watcher per session, started by `open_session_run` (`app/cli/print.py`), instead of
upstream's per-scheduler watch; chokidar's events are a stat poll on the run's event loop
(`app/utils/settings/change_detector.py::file_identity` and its poll interval). Only a host folder is
watched: an E2B workspace has no file to stat.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.tdd.project_config import (
    PROJECT_CONFIG_FILE,
    ProjectConfig,
    parse_project_config_text,
)
from app.tdd.suite_fingerprint import fingerprint_test_files, hash_test_files
from app.utils.debug import log_for_debugging
from app.utils.settings.change_detector import (
    FILE_STABILITY_POLL_INTERVAL_MS,
    FileIdentity,
    file_identity,
)

logger = logging.getLogger("TDDOrchestrator.ProjectConfig")

#: `cronScheduler.ts`'s `FILE_STABILITY_MS`: how long the file must stay unchanged before it is read.
FILE_STABILITY_MS = 300

GetState = Callable[[], Any]
SetState = Callable[[Callable[[Any], Any]], None]


def _pattern_key(config: ProjectConfig | None) -> tuple[tuple[str, ...], ...] | None:
    """What decides which files are test code (the fingerprint's set): the three pattern lists."""
    if config is None:
        return None
    return (config.test_files, config.test_support_files, config.exclude)


def apply_project_config(get_state: GetState, set_state: SetState, workspace: Any,
                         config: ProjectConfig | None, issues: tuple[str, ...] = ()) -> bool:
    """
    Make `config` the session's configuration (with the file's `issues` when it is invalid). While a
    sub-requirement is current and the test patterns change, RED's baseline is taken again under the
    new ones. `False` when the session already holds exactly this.
    """
    state = get_state()
    if state.project_config == config and state.project_config_issues == issues:
        return False
    rebaseline = (state.tdd_plan.current is not None
                  and _pattern_key(state.project_config) != _pattern_key(config))
    baseline = fingerprint_test_files(workspace, config) if rebaseline else state.tdd_item_test_baseline
    hashes = hash_test_files(workspace, config) if rebaseline else state.tdd_item_test_hashes

    def update(current: Any) -> Any:
        return replace(current, project_config=config, project_config_issues=issues,
                       tdd_item_test_baseline=baseline, tdd_item_test_hashes=hashes)

    set_state(update)
    return True


def apply_project_config_text(get_state: GetState, set_state: SetState, workspace: Any,
                              text: str | None) -> bool:
    """`load(false)` for `project.json`: `None` (a deleted file) or invalid text is no configuration."""
    config: ProjectConfig | None = None
    issues: tuple[str, ...] = ()
    if text is not None:
        config, issues = parse_project_config_text(text)
    changed = apply_project_config(get_state, set_state, workspace, config, issues)
    if changed:
        what = "removed" if text is None else ("invalid: " + "; ".join(issues) if config is None else "applied")
        log_for_debugging(f"{PROJECT_CONFIG_FILE} changed during the session: {what}")
        logger.info("%s changed during the session: %s", PROJECT_CONFIG_FILE, what)
    return changed


class ProjectConfigWatcher:
    """The watch of one session's `.tddagents/project.json` (see the module docstring)."""

    def __init__(self, path: str | Path, on_change: Callable[[str | None], None], *,
                 stability_ms: int = FILE_STABILITY_MS,
                 poll_interval_ms: int = FILE_STABILITY_POLL_INTERVAL_MS) -> None:
        self.path = str(path)
        self.on_change = on_change
        self.stability_ms = stability_ms
        self.poll_interval_ms = poll_interval_ms
        self.disposed = False
        self._confirmed: FileIdentity | None = None
        self._pending: tuple[FileIdentity, float] | None = None
        self._task: asyncio.Future[None] | None = None

    async def initialize(self) -> None:
        """`ignoreInitial: true`: the file as it is now was read at session open."""
        self._confirmed = file_identity(self.path)
        log_for_debugging(f"Watching {self.path} for changes")
        self._task = asyncio.ensure_future(self._poll_loop())

    async def dispose(self) -> None:
        self.disposed = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - the watch is over either way
                pass
            self._task = None

    async def _poll_loop(self) -> None:
        while not self.disposed:
            await asyncio.sleep(self.poll_interval_ms / 1000)
            try:
                self.poll_once()
            except Exception as exc:  # noqa: BLE001 - the watch outlives one failed look
                logger.error("%s watch failed: %s", PROJECT_CONFIG_FILE, exc)

    def poll_once(self, now: float | None = None) -> None:
        """One look: `unlink` at once, `add` / `change` once the file stayed the same `stability_ms`."""
        now = time.monotonic() if now is None else now
        current = file_identity(self.path)
        if current == self._confirmed:
            self._pending = None
            return
        if current is None:
            self._pending = None
            self._confirmed = None
            self.on_change(None)
            return
        if self._pending is None or self._pending[0] != current:
            self._pending = (current, now)
            return
        if (now - self._pending[1]) * 1000 < self.stability_ms:
            return
        self._pending = None
        self._confirmed = current
        try:
            text = Path(self.path).read_text(encoding="utf-8")
        except OSError:
            # Unreadable now: the next look sees it again.
            self._confirmed = None
            return
        self.on_change(text)


async def start_project_config_watcher(workspace: Any, get_state: GetState,
                                       set_state: SetState) -> ProjectConfigWatcher | None:
    """The session's watcher over its workspace's `project.json`; `None` without a host folder."""
    from app.workspace.local import LocalWorkspace

    if not isinstance(workspace, LocalWorkspace):
        return None

    def on_change(text: str | None) -> None:
        apply_project_config_text(get_state, set_state, workspace, text)

    watcher = ProjectConfigWatcher(Path(str(workspace.root)) / PROJECT_CONFIG_FILE, on_change)
    await watcher.initialize()
    return watcher


__all__ = [
    "FILE_STABILITY_MS",
    "ProjectConfigWatcher",
    "apply_project_config",
    "apply_project_config_text",
    "start_project_config_watcher",
]
