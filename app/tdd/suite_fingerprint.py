"""
The fingerprint of a workspace's test files: the evidence that RED wrote tests (Phase 12).

RED no longer needs a failing run (user decision, 2026-10-28). It ends with a RunTests run in
which the sub-requirement's tests changed since the item started. "Changed" is observed by
`RunTests` itself, never claimed: when an item starts, its baseline is the fingerprint below.
RunTests in RED compares the current one with it, so writes by any tool count: WriteFile,
Edit, Bash, MCP or a worker.

The fingerprint hashes the sorted path and content of every test file, as
`app/tdd/phase_permissions.is_test_path` classifies them. Caches are left out (a run writes
`__pycache__/*.pyc` under `tests/`, which would otherwise change it on every run), and so is
TDDAgents' own `.tddagents/` directory. It goes through the `Workspace` protocol, so it is the
same locally, in E2B and in the in-memory test double.
"""

from __future__ import annotations

import hashlib
from typing import Any

from app.tdd.ledger import PhaseLedger, TddPhase
from app.tdd.phase_permissions import is_test_path

#: Directory names whose contents are never test files.
IGNORED_SEGMENTS = frozenset({"__pycache__", ".pytest_cache", ".tddagents", ".git", ".mypy_cache", ".venv"})

#: Deep enough for any project tree (`list_files` counts depth in entries).
LISTING_DEPTH = 64

#: The fingerprint of a workspace with no test files.
EMPTY_FINGERPRINT = hashlib.sha256(b"").hexdigest()


def _counts(path: str) -> bool:
    segments = path.replace("\\", "/").split("/")
    if any(segment in IGNORED_SEGMENTS for segment in segments) or path.endswith(".pyc"):
        return False
    return is_test_path(path)


def fingerprint_test_files(workspace: Any) -> str | None:
    """The test files' fingerprint, or `None` when the workspace cannot be listed."""
    lister = getattr(workspace, "list_files", None)
    if not callable(lister):
        return None
    try:
        entries = lister(".", depth=LISTING_DEPTH)
    except Exception:  # noqa: BLE001 - an unlistable workspace gives no evidence either way
        return None
    paths = sorted(str(entry.path) for entry in entries if not entry.is_dir and _counts(str(entry.path)))
    digest = hashlib.sha256()
    for path in paths:
        try:
            content = str(workspace.read_file(path))
        except Exception:  # noqa: BLE001 - a binary or vanished file still counts by its path
            content = "\x00<unreadable>"
        digest.update(f"{path}\x00{content}\x00".encode("utf-8", "surrogateescape"))
    return digest.hexdigest() if paths else EMPTY_FINGERPRINT


def have_tests_changed(current: str | None, baseline: str | None) -> bool:
    """
    Whether the item's tests changed. No current fingerprint is no evidence. No baseline (it
    could not be taken when the item started) counts any test file as new.
    """
    if current is None:
        return False
    if baseline is None:
        return current != EMPTY_FINGERPRINT
    return current != baseline


def spawn_test_baseline(spawn_ledger: PhaseLedger, context: Any) -> str | None:
    """
    The test baseline after a phase-on-spawn: a spawn back to RED restarts the item's cycle,
    so its tests are fingerprinted afresh; any other spawn keeps the item's baseline.
    """
    if spawn_ledger.phase == TddPhase.RED:
        return fingerprint_test_files(getattr(context, "workspace", None))
    baseline: str | None = context.get_app_state().tdd_item_test_baseline
    return baseline
