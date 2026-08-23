"""
The sync engine: bidirectional local <-> sandbox reconciliation at four deterministic
checkpoints, and nowhere else.

    run start                  seed()                  local -> sandbox
    write-capable tool call    reconcile_ledger()      sandbox -> file_system
    sub-requirement boundary   flush()                 sandbox -> local
    run end                    flush(full=True)        sandbox -> local

No filesystem watchers and no background threads. A run's sync behavior has to be
replayable from its own call sequence, or the three-runs-per-task research design stops
comparing like with like.

The direction names above describe each checkpoint's dominant flow. The reconciliation
itself is genuinely bidirectional — every checkpoint compares both sides against the
baseline and moves whichever files actually changed, which is what makes the conflict
rule meaningful rather than decorative.

**Idempotent, not transactional.** The baseline advances only after a pass completes,
so a sync that fails halfway leaves it unadvanced and the next checkpoint retries the
same delta. That is the achievable guarantee here; a partial write cannot be rolled
back out of a remote sandbox.

Phase 1A ships this as a library. Nothing in the running pipeline calls it yet: the
tool-call checkpoint needs the Phase 1B tool layer, and the other three land with the
graph changes in Phase 2.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

from app.config.config import Config
from app.sync.baseline import (
    Action,
    Baseline,
    IgnoreRules,
    classify,
    content_hash,
    snapshot,
)
from app.sync.events import CheckpointKind, SyncCheckpoint, SyncConflict, emit
from app.workspace.base import Workspace, WorkspaceError

logger = logging.getLogger("TDDOrchestrator.Sync")

# Touched after every successful sync; `find -newer` against it is how files created by
# a shell command (rather than by a write tool) are discovered for the ledger.
SYNC_MARKER = ".tddagents_sync_marker"


class SyncEngine:
    """Reconciles one sandbox workspace with one local workspace."""

    def __init__(
        self,
        sandbox: Workspace,
        local: Workspace,
        baseline_path: str | Path,
        exclude_fallback: list[str] | None = None,
    ) -> None:
        self.sandbox = sandbox
        self.local = local
        self.baseline_path = Path(baseline_path)
        self.exclude_fallback = (
            exclude_fallback if exclude_fallback is not None else list(Config.SYNC_EXCLUDE_FALLBACK)
        )

    # ── Checkpoints ──────────────────────────────────────────────────────────

    def sync_at_checkpoint(
        self,
        kind: CheckpointKind,
        ledger: dict[str, str] | None = None,
    ) -> SyncCheckpoint | tuple[dict[str, str], SyncCheckpoint]:
        """
        Single entry point. Dispatches to the right operation for `kind` and refreshes
        the sandbox lifetime, so a long run cannot expire between checkpoints.
        """
        self._refresh_sandbox_lifetime()

        if kind == "run_start":
            return self.seed()
        if kind == "tool_call":
            return self.reconcile_ledger(ledger if ledger is not None else {})
        if kind in ("sub_req_boundary", "run_end"):
            return self.flush(kind=kind)

        raise ValueError(f"Unknown sync checkpoint '{kind}'.")

    def seed(self) -> SyncCheckpoint:
        """Run start: bring the sandbox up to date with the local tree."""
        return self._reconcile("run_start")

    def flush(self, kind: CheckpointKind = "sub_req_boundary", full: bool = False) -> SyncCheckpoint:
        """
        Sub-requirement boundary or run end: bring the local tree up to date with the
        sandbox.

        `full` forces a baseline-blind pass, mirroring every sandbox file regardless of
        whether it appears changed. Used at run end so the exported workspace cannot be
        missing a file because of a stale baseline.
        """
        if full:
            return self._reconcile("run_end", ignore_baseline=True)
        return self._reconcile(kind)

    def reconcile_ledger(self, ledger: dict[str, str]) -> tuple[dict[str, str], SyncCheckpoint]:
        """
        Folds files the sandbox has gained since the last sync into the export ledger
        (`AgentState["file_system"]`).

        This is the fix for the lost-files bug: a file an agent creates with a shell
        command has never entered the ledger, so it never reached
        `workspace_output_*` and vanished with the sandbox. Discovery lives here rather
        than in each write-capable tool because a tool only knows about writes it made
        itself.

        Returns:
            (updated_ledger, checkpoint) — the ledger is a new dict; the caller decides
            whether to commit it to state.
        """
        started = time.monotonic()
        ignore = self._ignore_rules()
        updated = dict(ledger)
        written = 0

        for path in self._paths_changed_since_marker():
            if ignore.matches(path):
                continue
            try:
                content = self.sandbox.read_file(path)
            except WorkspaceError as exc:
                logger.debug("Ledger reconciliation skipped '%s': %s", path, exc)
                continue

            if updated.get(path) != content:
                updated[path] = content
                written += 1

        self._touch_marker()

        checkpoint = SyncCheckpoint(
            kind="tool_call",
            direction="sandbox_to_ledger",
            files_written=written,
            files_deleted=0,
            conflicts=0,
            duration=time.monotonic() - started,
        )
        emit(checkpoint)
        return updated, checkpoint

    # ── The reconciliation pass ──────────────────────────────────────────────

    def _reconcile(self, kind: CheckpointKind, ignore_baseline: bool = False) -> SyncCheckpoint:
        started = time.monotonic()
        ignore = self._ignore_rules()

        baseline = Baseline(entries={}, taken_at=0.0) if ignore_baseline else Baseline.load(self.baseline_path)
        sandbox_state = snapshot(self.sandbox, ignore)
        local_state = snapshot(self.local, ignore)

        decisions = classify(baseline, sandbox_state, local_state, sandbox_wins=True)

        written = deleted = conflicts = 0
        next_entries = dict(baseline.entries)

        for decision in decisions:
            path = decision.path

            if decision.action is Action.NOOP:
                if path in sandbox_state:
                    next_entries[path] = sandbox_state[path]
                continue

            if decision.action is Action.SKIP_DELETE:
                logger.info("↔️  Deletion of '%s' not propagated: %s", path, decision.reason)
                continue

            if decision.action is Action.DELETE:
                target = self.local if path in local_state else self.sandbox
                self._delete(target, path)
                next_entries.pop(path, None)
                deleted += 1
                continue

            if decision.action is Action.CONFLICT:
                self._back_up_local(path, kind)
                conflicts += 1
                self._copy(self.sandbox, self.local, path)
                next_entries[path] = sandbox_state[path]
                written += 1
                continue

            # PROPAGATE — whichever side differs from the baseline is the source.
            if path not in sandbox_state:
                source, destination = self.local, self.sandbox
            elif path not in local_state:
                source, destination = self.sandbox, self.local
            elif sandbox_state[path] != baseline.entries.get(path):
                source, destination = self.sandbox, self.local
            else:
                source, destination = self.local, self.sandbox

            content = self._copy(source, destination, path)
            next_entries[path] = content_hash(content)
            written += 1

        # Only now, with every decision applied, does the baseline move. A crash above
        # leaves it where it was and the next checkpoint retries the same delta.
        Baseline(entries=next_entries, taken_at=time.time()).save(self.baseline_path)
        self._touch_marker()

        checkpoint = SyncCheckpoint(
            kind=kind,
            direction="local_to_sandbox" if kind == "run_start" else "sandbox_to_local",
            files_written=written,
            files_deleted=deleted,
            conflicts=conflicts,
            duration=time.monotonic() - started,
        )
        emit(checkpoint)
        return checkpoint

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _ignore_rules(self) -> IgnoreRules:
        """
        Recomputed at each checkpoint, because a generated `.gitignore` can appear
        partway through a run — the agents write the project, including its ignore file.
        """
        rules = IgnoreRules.from_workspace(self.sandbox, self.exclude_fallback)
        rules.add(SYNC_MARKER)
        return rules

    def _copy(self, source: Workspace, destination: Workspace, path: str) -> str:
        content = source.read_file(path)
        destination.write_file(path, content)
        return content

    def _delete(self, workspace: Workspace, path: str) -> None:
        try:
            workspace.delete_file(path)
        except WorkspaceError as exc:
            logger.debug("Delete of '%s' skipped: %s", path, exc)

    def _back_up_local(self, path: str, kind: CheckpointKind) -> None:
        """
        Preserves the local copy before the sandbox overwrites it.

        The sandbox winning is what keeps a run coherent; losing the developer's
        concurrent edit without a trace is not part of that bargain.
        """
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = f"{path}.local.{stamp}"
        try:
            self.local.write_file(backup_path, self.local.read_file(path))
        except WorkspaceError as exc:
            logger.warning("Could not back up local '%s' before overwrite: %s", path, exc)
            return

        emit(SyncConflict(path=path, backup_path=backup_path, checkpoint=kind))

    def _paths_changed_since_marker(self) -> list[str]:
        """
        Lists sandbox files newer than the marker, falling back to the full tree the
        first time through (when no marker exists yet).
        """
        if not self.sandbox.exists(SYNC_MARKER):
            return [e.path for e in self.sandbox.list_files(".", depth=64) if not e.is_dir]

        result = self.sandbox.execute(f"find . -type f -newer {SYNC_MARKER}")
        if result.exit_code != 0:
            logger.warning("Could not list changed files (exit %d); falling back to a full scan.", result.exit_code)
            return [e.path for e in self.sandbox.list_files(".", depth=64) if not e.is_dir]

        paths = []
        for line in result.stdout.splitlines():
            candidate = line.strip()
            if not candidate:
                continue
            if candidate.startswith("./"):
                candidate = candidate[2:]
            if candidate:
                paths.append(candidate)
        return paths

    def _touch_marker(self) -> None:
        try:
            self.sandbox.write_file(SYNC_MARKER, str(time.time()))
        except WorkspaceError as exc:
            logger.warning("Could not update the sync marker: %s", exc)

    def _refresh_sandbox_lifetime(self) -> None:
        adapter = getattr(self.sandbox, "adapter", None)
        if adapter is not None:
            try:
                adapter.refresh_timeout(force=True)
            except WorkspaceError as exc:
                logger.warning("Could not extend the sandbox lifetime at a checkpoint: %s", exc)
