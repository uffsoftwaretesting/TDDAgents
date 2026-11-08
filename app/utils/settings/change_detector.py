"""
Watching the settings files for changes during a session.

Ported from `reference/claude-code/src/utils/settings/changeDetector.ts` -> `initialize`,
`dispose`, `subscribe`, `notifyChange`, `resetForTesting` (the constructor's timing overrides),
`getWatchTargets`, `settingSourceToConfigChangeSource`, `handleChange`, `handleAdd`,
`handleDelete`, `getSourceForPath`, `fanOut`, and the timing constants.

**What is watched** (`getWatchTargets`): every settings source but `flagSettings` (given on the
command line, it does not change during the session), and only in the directories that held at
least one settings file when the watch started — in those, every potential settings file, so one
created later (`settings.local.json` beside `settings.json`) is seen. The policy drop-in directory
(`managed-settings.d/`) is watched for its `*.json` files when it exists. A directory with no
settings file at the start is never watched: a project whose `.tddagents/` appears mid-session
needs a restart, as upstream's (the `update-config` skill's "watcher caveat").

**When a change counts**: once the file has stayed the same for `FILE_STABILITY_THRESHOLD_MS`,
looked at every `FILE_STABILITY_POLL_INTERVAL_MS` (chokidar's `awaitWriteFinish`), so a partial
write is never read. A write TDDAgents made itself (`update_settings_for_source` marks it,
`app/utils/settings/internal_writes.py`) within `INTERNAL_WRITE_WINDOW_MS` is not a change. A
deletion waits `DELETION_GRACE_MS`, and a file back by then is a change, not a deletion.

**What a change does**: the ConfigChange hooks run first (`executeConfigChangeHooks`); a blocking
result keeps the change from the session (never for policy settings). Otherwise every subscriber
is told the source (`fanOut`) — the run's `apply_settings_change`
(`app/utils/settings/apply_settings_change.py`), which re-reads the settings, the permission rules
and the hooks snapshot. `notify_change` is the same notification for a change made without a file
(the SDK's `apply_flag_settings`).

*Divergences:* one detector per run instead of upstream's process-wide module state (a TDDAgents
process can hold several sessions, each over its own project), started by the run
(`app/cli/print.py` -> `open_session_run`) instead of `main.tsx`. chokidar's native file events
are replaced by a stat poll at the stability poll interval on the run's event loop (a change is
seen within the same window chokidar's `awaitWriteFinish` waits). The MDM / HKCU poll, remote mode
and the settings cache reset are not ported: there are no MDM settings here, and the settings
files are read on every load (`app/utils/settings/settings_cache.py`).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

from app.types.permissions import PermissionRuleSource
from app.utils.debug import log_for_debugging
from app.utils.settings.internal_writes import clear_internal_writes, consume_internal_write
from app.utils.settings.settings import (
    MANAGED_DROP_IN_DIRNAME,
    SETTING_SOURCES,
    SettingsLocations,
    settings_file_path_for_source,
)

logger = logging.getLogger("TDDOrchestrator.Settings")

#: `FILE_STABILITY_THRESHOLD_MS`: how long a file must stay unchanged before its change counts.
FILE_STABILITY_THRESHOLD_MS = 1000
#: `FILE_STABILITY_POLL_INTERVAL_MS`: how often the files are looked at.
FILE_STABILITY_POLL_INTERVAL_MS = 500
#: `INTERNAL_WRITE_WINDOW_MS`: how long a write of TDDAgents' own is recognized.
INTERNAL_WRITE_WINDOW_MS = 5000
#: `DELETION_GRACE_MS`: a deleted file recreated within this window is a change.
DELETION_GRACE_MS = FILE_STABILITY_THRESHOLD_MS + FILE_STABILITY_POLL_INTERVAL_MS + 200

#: What a subscriber is told: the source whose settings changed.
SettingsListener = Callable[[PermissionRuleSource], None]
#: `executeConfigChangeHooks(source, path)`, bound to the run's hooks.
ConfigChangeHooks = Callable[[str, str], Awaitable[Sequence[Any]]]

#: `settingSourceToConfigChangeSource`.
CONFIG_CHANGE_SOURCE: dict[PermissionRuleSource, str] = {
    PermissionRuleSource.USER_SETTINGS: "user_settings",
    PermissionRuleSource.PROJECT_SETTINGS: "project_settings",
    PermissionRuleSource.LOCAL_SETTINGS: "local_settings",
    PermissionRuleSource.FLAG_SETTINGS: "policy_settings",
    PermissionRuleSource.POLICY_SETTINGS: "policy_settings",
}

#: A regular file's identity for change detection: its mtime, size and inode.
FileIdentity = tuple[int, int, int]


def setting_source_to_config_change_source(source: PermissionRuleSource) -> str:
    """`settingSourceToConfigChangeSource`."""
    return CONFIG_CHANGE_SOURCE[source]


def file_identity(path: str) -> FileIdentity | None:
    """A regular file's identity for change detection (`None`: absent or not a file)."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    if not os.path.isfile(path):
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def _resolved(path: Path) -> str:
    return str(path.resolve())


class SettingsChangeDetector:
    """`settingsChangeDetector` for one run's settings files."""

    def __init__(
        self,
        locations: SettingsLocations,
        *,
        config_change_hooks: ConfigChangeHooks | None = None,
        stability_threshold_ms: int | None = None,
        poll_interval_ms: int | None = None,
        deletion_grace_ms: int | None = None,
    ) -> None:
        """The timings default to the module's constants (`resetForTesting`'s overrides otherwise)."""
        self.locations = locations
        self.config_change_hooks = config_change_hooks
        self.stability_threshold_ms = (
            stability_threshold_ms if stability_threshold_ms is not None else FILE_STABILITY_THRESHOLD_MS)
        self.poll_interval_ms = poll_interval_ms if poll_interval_ms is not None else FILE_STABILITY_POLL_INTERVAL_MS
        self.deletion_grace_ms = deletion_grace_ms if deletion_grace_ms is not None else DELETION_GRACE_MS
        self.initialized = False
        self.disposed = False
        self.dirs: list[str] = []
        self.settings_files: set[str] = set()
        self.drop_in_dir: str | None = None
        self._listeners: list[SettingsListener] = []
        self._confirmed: dict[str, FileIdentity | None] = {}
        self._pending: dict[str, tuple[FileIdentity | None, float]] = {}
        self._pending_deletions: dict[str, asyncio.TimerHandle] = {}
        self._poll_task: asyncio.Task[None] | None = None
        self._work: set[asyncio.Task[None]] = set()

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """`initialize`: start watching (once; never after `dispose`)."""
        if self.initialized or self.disposed:
            return
        self.initialized = True
        self.dirs, self.settings_files, self.drop_in_dir = self.get_watch_targets()
        if not self.dirs:
            return
        drop_in = f" and drop-in directory {self.drop_in_dir}" if self.drop_in_dir else ""
        log_for_debugging(f"Watching for changes in setting files {', '.join(sorted(self.settings_files))}...{drop_in}")
        self._confirmed = {path: file_identity(path) for path in self._watched_paths()}
        self._poll_task = asyncio.ensure_future(self._poll_loop())

    async def dispose(self) -> None:
        """`dispose`: stop watching and forget every subscriber; resolves once the poll has stopped."""
        self.disposed = True
        for timer in self._pending_deletions.values():
            timer.cancel()
        self._pending_deletions.clear()
        clear_internal_writes()
        self._listeners.clear()
        task, self._poll_task = self._poll_task, None
        pending = [t for t in (task, *self._work) if t is not None and not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def subscribe(self, listener: SettingsListener) -> Callable[[], None]:
        """`subscribe`: returns the unsubscribe function."""
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def notify_change(self, source: PermissionRuleSource) -> None:
        """`notifyChange`: a settings change made without a file (the SDK's inline flag settings)."""
        log_for_debugging(f"Programmatic settings change notification for {source}")
        self._fan_out(source)

    def _fan_out(self, source: PermissionRuleSource) -> None:
        """`fanOut`: every subscriber, in order (upstream resets the settings cache first; none here)."""
        for listener in list(self._listeners):
            try:
                listener(source)
            except Exception as exc:  # noqa: BLE001 - one subscriber's failure never starves the others
                logger.error("Settings change listener failed: %s", exc)

    # ── what is watched ──────────────────────────────────────────────────────

    def get_watch_targets(self) -> tuple[list[str], set[str], str | None]:
        """`getWatchTargets`: the watched directories, their potential settings files, the drop-in dir."""
        dir_to_files: dict[str, set[str]] = {}
        dirs_with_files: list[str] = []
        for source in SETTING_SOURCES:
            if source == PermissionRuleSource.FLAG_SETTINGS:
                continue
            path = settings_file_path_for_source(source, self.locations)
            if path is None:
                continue
            resolved = _resolved(path)
            directory = os.path.dirname(resolved)
            dir_to_files.setdefault(directory, set()).add(resolved)
            if os.path.isfile(resolved) and directory not in dirs_with_files:
                dirs_with_files.append(directory)
        settings_files = {file for directory in dirs_with_files for file in dir_to_files[directory]}
        drop_in: str | None = None
        managed_drop_in = _resolved(self.locations.managed_dir / MANAGED_DROP_IN_DIRNAME)
        if os.path.isdir(managed_drop_in):
            dirs_with_files.append(managed_drop_in)
            drop_in = managed_drop_in
        return dirs_with_files, settings_files, drop_in

    def _watched_paths(self) -> set[str]:
        paths = set(self.settings_files)
        if self.drop_in_dir is not None:
            try:
                names = os.listdir(self.drop_in_dir)
            except OSError:
                names = []
            paths.update(os.path.join(self.drop_in_dir, name) for name in names if name.endswith(".json"))
        return paths

    def get_source_for_path(self, path: str) -> PermissionRuleSource | None:
        """`getSourceForPath`: a drop-in file is policy settings."""
        drop_in = _resolved(self.locations.managed_dir / MANAGED_DROP_IN_DIRNAME)
        if path.startswith(drop_in + os.sep):
            return PermissionRuleSource.POLICY_SETTINGS
        for source in SETTING_SOURCES:
            candidate = settings_file_path_for_source(source, self.locations)
            if candidate is not None and _resolved(candidate) == path:
                return source
        return None

    # ── the poll (chokidar's events) ─────────────────────────────────────────

    async def _poll_loop(self) -> None:
        while not self.disposed:
            await asyncio.sleep(self.poll_interval_ms / 1000)
            try:
                self.poll_once()
            except Exception as exc:  # noqa: BLE001 - the watch outlives one failed look
                logger.error("Settings watch failed: %s", exc)

    def poll_once(self, now: float | None = None) -> None:
        """One look at every watched file: a stable change, a new file or a deletion is handled."""
        now = time.monotonic() if now is None else now
        paths = self._watched_paths() | set(self._confirmed) | set(self._pending)
        for path in sorted(paths):
            current = file_identity(path)
            confirmed = self._confirmed.get(path)
            if current == confirmed:
                self._pending.pop(path, None)
                continue
            if current is None:
                self._pending.pop(path, None)
                self._confirmed[path] = None
                self.handle_delete(path)
                continue
            seen = self._pending.get(path)
            if seen is None or seen[0] != current:
                self._pending[path] = (current, now)
                continue
            if (now - seen[1]) * 1000 < self.stability_threshold_ms:
                continue
            del self._pending[path]
            self._confirmed[path] = current
            if confirmed is None:
                self.handle_add(path)
            else:
                self.handle_change(path)

    # ── handlers ─────────────────────────────────────────────────────────────

    def handle_change(self, path: str) -> None:
        """`handleChange`."""
        source = self.get_source_for_path(path)
        if source is None:
            return
        timer = self._pending_deletions.pop(path, None)
        if timer is not None:
            timer.cancel()
            log_for_debugging(f"Cancelled pending deletion of {path} — file was recreated")
        if consume_internal_write(path, INTERNAL_WRITE_WINDOW_MS):
            return
        log_for_debugging(f"Detected change to {path}")
        self._spawn(self._apply(source, path, "change"))

    def handle_add(self, path: str) -> None:
        """`handleAdd`: a file (re)appeared; any pending deletion is cancelled, then it is a change."""
        source = self.get_source_for_path(path)
        if source is None:
            return
        timer = self._pending_deletions.pop(path, None)
        if timer is not None:
            timer.cancel()
            log_for_debugging(f"Cancelled pending deletion of {path} — file was re-added")
        self.handle_change(path)

    def handle_delete(self, path: str) -> None:
        """`handleDelete`: applied after the grace period, unless the file comes back first."""
        source = self.get_source_for_path(path)
        if source is None:
            return
        log_for_debugging(f"Detected deletion of {path}")
        if path in self._pending_deletions:
            return

        def fire() -> None:
            self._pending_deletions.pop(path, None)
            self._spawn(self._apply(source, path, "deletion"))

        loop = asyncio.get_running_loop()
        self._pending_deletions[path] = loop.call_later(self.deletion_grace_ms / 1000, fire)

    async def _apply(self, source: PermissionRuleSource, path: str, what: str) -> None:
        """The ConfigChange hooks first; a blocking result keeps the change from the session."""
        if self.config_change_hooks is not None:
            from app.utils.hooks.hooks import has_blocking_result

            results = await self.config_change_hooks(setting_source_to_config_change_source(source), path)
            if has_blocking_result(results):
                log_for_debugging(f"ConfigChange hook blocked {what} {'to' if what == 'change' else 'of'} {path}")
                return
        if not self.disposed:
            self._fan_out(source)

    def _spawn(self, work: Awaitable[None]) -> None:
        task: asyncio.Task[None] = asyncio.ensure_future(work)
        self._work.add(task)
        task.add_done_callback(self._work.discard)

    async def settle(self) -> None:
        """Wait for the hooks and notifications already started (tests, and a run's teardown)."""
        while self._work:
            await asyncio.gather(*list(self._work), return_exceptions=True)


__all__ = [
    "CONFIG_CHANGE_SOURCE",
    "ConfigChangeHooks",
    "DELETION_GRACE_MS",
    "FILE_STABILITY_POLL_INTERVAL_MS",
    "FILE_STABILITY_THRESHOLD_MS",
    "INTERNAL_WRITE_WINDOW_MS",
    "SettingsChangeDetector",
    "FileIdentity",
    "SettingsListener",
    "file_identity",
    "setting_source_to_config_change_source",
]
