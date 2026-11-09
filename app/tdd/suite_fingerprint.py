"""
The fingerprint of a workspace's test files: the evidence that RED wrote tests (Phase 12).

RED no longer needs a failing run (user decision, 2026-10-28). It ends with a RunTests run in
which the sub-requirement's tests changed since the item started. "Changed" is observed by
`RunTests` itself, never claimed: when an item starts, its baseline is the fingerprint below.
RunTests in RED compares the current one with it, so writes by any tool count: WriteFile,
Edit, Bash, MCP or a worker.

The fingerprint hashes the sorted path and content of every test file and test-support file the
project declares (`testFiles` and `testSupportFiles` in `.tddagents/project.json`, 2026-11-03). The
walk is pruned on the declared `exclude` patterns, `.git/` and `.tddagents/` (`project_files`), so a
dependency tree (`node_modules/x/test/y.js`) or a cache the runner writes never counts. With no
declaration there is no fingerprint (`None`): no evidence either way. It goes through the
`Workspace` protocol, so it is the same locally, in E2B and in the in-memory test double.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from app.tdd.ledger import PhaseLedger, TddPhase
from app.tdd.project_config import ProjectConfig, TestFileRules

#: Deep enough for any project tree (`list_files` counts depth in entries).
LISTING_DEPTH = 64

#: The fingerprint of a workspace with no test files.
EMPTY_FINGERPRINT = hashlib.sha256(b"").hexdigest()


def project_files(workspace: Any, rules: TestFileRules) -> list[str] | None:
    """
    The project's own files, root-relative: the workspace's pruned walk (`LocalWorkspace.walk_files`)
    when it has one, else its full listing, filtered. `None` when the workspace cannot be listed.
    """
    try:
        walker = getattr(workspace, "walk_files", None)
        if callable(walker):
            listed = [str(path) for path in walker(rules.is_excluded)]
        else:
            lister = getattr(workspace, "list_files", None)
            if not callable(lister):
                return None
            listed = [str(entry.path) for entry in lister(".", depth=LISTING_DEPTH) if not entry.is_dir]
    except Exception:  # noqa: BLE001 - an unlistable workspace gives no evidence either way
        return None
    return sorted(path for path in listed if not rules.is_excluded(path))


def _test_code(workspace: Any, config: ProjectConfig | None) -> list[tuple[str, str]] | None:
    """Each declared test file and test-support file with its content; `None` as `fingerprint_test_files`."""
    if config is None:
        return None
    rules = config.rules
    files = project_files(workspace, rules)
    if files is None:
        return None
    contents = []
    for path in (path for path in files if rules.is_test_code(path)):
        try:
            content = str(workspace.read_file(path))
        except Exception:  # noqa: BLE001 - a binary or vanished file still counts by its path
            content = "\x00<unreadable>"
        contents.append((path, content))
    return contents


def fingerprint_test_files(workspace: Any, config: ProjectConfig | None) -> str | None:
    """The declared test files' fingerprint, or `None` with no declaration or an unlistable workspace."""
    contents = _test_code(workspace, config)
    if contents is None:
        return None
    digest = hashlib.sha256()
    for path, content in contents:
        digest.update(f"{path}\x00{content}\x00".encode("utf-8", "surrogateescape"))
    return digest.hexdigest() if contents else EMPTY_FINGERPRINT


def hash_test_files(workspace: Any, config: ProjectConfig | None) -> dict[str, str] | None:
    """
    Each declared test file's content hash, by path (2026-11-08): what RED compares file by file to lower the
    session's test floor only for the files the item changed. `None` as `fingerprint_test_files`.
    """
    contents = _test_code(workspace, config)
    if contents is None:
        return None
    return {path: hashlib.sha256(content.encode("utf-8", "surrogateescape")).hexdigest()
            for path, content in contents}


def context_test_file_hashes(context: Any) -> dict[str, str] | None:
    """`hash_test_files` for a tool context: its workspace and its app state's declaration."""
    config = getattr(context.get_app_state(), "project_config", None)
    return hash_test_files(getattr(context, "workspace", None), config)


def context_test_fingerprint(context: Any) -> str | None:
    """`fingerprint_test_files` for a tool context: its workspace and its app state's declaration."""
    config = getattr(context.get_app_state(), "project_config", None)
    return fingerprint_test_files(getattr(context, "workspace", None), config)


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


def file_changed(path: str, current: Mapping[str, str] | None, baseline: Mapping[str, str] | None) -> bool:
    """
    Whether `path`'s content changed since the item started: created, edited or deleted. With no hashes on
    either side every file counts as changed, as `have_tests_changed` counts any test file as new.
    """
    if current is None or baseline is None:
        return True
    return current.get(path) != baseline.get(path)


def spawn_test_hashes(spawn_ledger: PhaseLedger, context: Any) -> Mapping[str, str] | None:
    """`spawn_test_baseline`'s per-file hashes: taken afresh on a spawn back to RED, else the item's."""
    if spawn_ledger.phase == TddPhase.RED:
        return context_test_file_hashes(context)
    hashes: Mapping[str, str] | None = context.get_app_state().tdd_item_test_hashes
    return hashes


def spawn_test_baseline(spawn_ledger: PhaseLedger, context: Any) -> str | None:
    """
    The test baseline after a phase-on-spawn: a spawn back to RED restarts the item's cycle,
    so its tests are fingerprinted afresh; any other spawn keeps the item's baseline.
    """
    if spawn_ledger.phase == TddPhase.RED:
        return context_test_fingerprint(context)
    baseline: str | None = context.get_app_state().tdd_item_test_baseline
    return baseline
