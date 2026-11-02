"""
Marketplace reconciler: make known_marketplaces.json consistent with the intent in settings.

Ported from `reference/claude-code/src/utils/plugins/reconciler.ts` -> `MarketplaceDiff`, `diffMarketplaces`,
`ReconcileOptions`, `ReconcileProgressEvent`, `ReconcileResult`, `reconcileMarketplaces`,
`normalizeSource`.

`diff_marketplaces` compares (declared `file`/`directory` paths are resolved against the
project's canonical git root first); `reconcile_marketplaces` installs what is missing and
re-materializes what changed source. It is idempotent and additive (never deletes) and does not
touch app state. `locations.project_root` is upstream's `getOriginalCwd()`.

Divergence: the canonical root comes from `app/memdir/paths.py`'s `find_canonical_git_root`, which
does not search above the project (TDDAgents isolates each project from enclosing repositories);
upstream's `findCanonicalGitRoot` walks up. A worktree still resolves to its main checkout.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from app.memdir.paths import find_canonical_git_root
from app.utils.plugins.marketplace_manager import (
    DeclaredMarketplace,
    add_marketplace_source,
    get_declared_marketplaces,
    load_known_marketplaces_config,
)
from app.utils.plugins.schemas import is_local_marketplace_source
from app.utils.settings.settings import SettingsLocations

logger = logging.getLogger("TDDOrchestrator.Plugins")


@dataclass(frozen=True)
class SourceChange:
    """One `sourceChanged` entry: settings (declared) wins over the JSON (materialized)."""

    name: str
    declared_source: dict[str, Any]
    materialized_source: dict[str, Any]


@dataclass
class MarketplaceDiff:
    """`MarketplaceDiff`."""

    missing: list[str] = field(default_factory=list)
    source_changed: list[SourceChange] = field(default_factory=list)
    up_to_date: list[str] = field(default_factory=list)


def normalize_source(source: Mapping[str, Any], project_root: str) -> dict[str, Any]:
    """
    `normalizeSource`: a relative `file`/`directory` path resolved against the project's
    canonical git root (the main checkout for a worktree), else the project root.
    """
    if source.get("source") in ("directory", "file") and not os.path.isabs(str(source.get("path", ""))):
        canonical = find_canonical_git_root(project_root)
        base = canonical or project_root
        return {**source, "path": os.path.normpath(os.path.join(base, str(source["path"])))}
    return dict(source)


def diff_marketplaces(
    declared: Mapping[str, DeclaredMarketplace],
    materialized: Mapping[str, Any],
    *,
    project_root: str,
) -> MarketplaceDiff:
    """
    `diffMarketplaces`: missing, source changed, up to date. A `sourceIsFallback` declaration
    only needs presence, whatever source materialized it.
    """
    diff = MarketplaceDiff()
    for name, intent in declared.items():
        state = materialized.get(name)
        normalized = normalize_source(intent["source"], project_root)
        if state is None:
            diff.missing.append(name)
        elif intent.get("sourceIsFallback"):
            diff.up_to_date.append(name)
        elif normalized != state["source"]:
            diff.source_changed.append(
                SourceChange(name=name, declared_source=normalized, materialized_source=dict(state["source"]))
            )
        else:
            diff.up_to_date.append(name)
    return diff


@dataclass(frozen=True)
class ReconcileProgressEvent:
    """
    `ReconcileProgressEvent`: `installing` (`action`, `index`, `total`), `installed`
    (`already_materialized`) or `failed` (`error`).
    """

    type: Literal["installing", "installed", "failed"]
    name: str
    action: Literal["install", "update"] | None = None
    index: int | None = None
    total: int | None = None
    already_materialized: bool | None = None
    error: str | None = None


@dataclass(frozen=True)
class ReconcileOptions:
    """`ReconcileOptions`: `skip` a declared marketplace; `on_progress` events."""

    skip: Callable[[str, dict[str, Any]], bool] | None = None
    on_progress: Callable[[ReconcileProgressEvent], None] | None = None


@dataclass(frozen=True)
class ReconcileFailure:
    name: str
    error: str


@dataclass
class ReconcileResult:
    """`ReconcileResult`."""

    installed: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    failed: list[ReconcileFailure] = field(default_factory=list)
    up_to_date: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _WorkItem:
    name: str
    source: dict[str, Any]
    action: Literal["install", "update"]


async def reconcile_marketplaces(
    locations: SettingsLocations, opts: ReconcileOptions | None = None
) -> ReconcileResult:
    """`reconcileMarketplaces`: install the missing, update the changed; idempotent and additive."""
    options = opts or ReconcileOptions()
    declared = get_declared_marketplaces(locations)
    if not declared:
        return ReconcileResult()
    try:
        materialized: Mapping[str, Any] = await load_known_marketplaces_config()
    except Exception as exc:  # noqa: BLE001 - a corrupted state file reconciles as empty, as upstream
        logger.error("Failed to load known marketplaces: %s", exc)
        materialized = {}
    project_root = str(locations.project_root)
    diff = diff_marketplaces(declared, materialized, project_root=project_root)

    work: list[_WorkItem] = [
        _WorkItem(name=name, source=normalize_source(declared[name]["source"], project_root), action="install")
        for name in diff.missing
    ]
    work += [_WorkItem(name=c.name, source=c.declared_source, action="update") for c in diff.source_changed]

    skipped: list[str] = []
    to_process: list[_WorkItem] = []
    for item in work:
        if options.skip is not None and options.skip(item.name, item.source):
            skipped.append(item.name)
            continue
        if (
            item.action == "update"
            and is_local_marketplace_source(item.source)
            and not os.path.exists(str(item.source["path"]))
        ):
            logger.debug("[reconcile] '%s' declared path does not exist; keeping materialized entry", item.name)
            skipped.append(item.name)
            continue
        to_process.append(item)

    if not to_process:
        return ReconcileResult(up_to_date=diff.up_to_date, skipped=skipped)

    logger.debug(
        "[reconcile] %d marketplace(s): %s", len(to_process), ", ".join(f"{w.name}({w.action})" for w in to_process)
    )
    result = ReconcileResult(up_to_date=diff.up_to_date, skipped=skipped)
    for i, item in enumerate(to_process):
        _emit(options, ReconcileProgressEvent(
            type="installing", name=item.name, action=item.action, index=i + 1, total=len(to_process)
        ))
        try:
            added = await add_marketplace_source(item.source, locations)
        except Exception as exc:  # noqa: BLE001 - one marketplace's failure must not stop the others
            error = str(exc)
            result.failed.append(ReconcileFailure(name=item.name, error=error))
            _emit(options, ReconcileProgressEvent(type="failed", name=item.name, error=error))
            logger.error("Failed to reconcile marketplace %s: %s", item.name, exc)
            continue
        (result.installed if item.action == "install" else result.updated).append(item.name)
        _emit(options, ReconcileProgressEvent(
            type="installed", name=item.name, already_materialized=added.already_materialized
        ))
    return result


def _emit(options: ReconcileOptions, event: ReconcileProgressEvent) -> None:
    if options.on_progress is not None:
        options.on_progress(event)


__all__ = [
    "MarketplaceDiff",
    "ReconcileFailure",
    "ReconcileOptions",
    "ReconcileProgressEvent",
    "ReconcileResult",
    "SourceChange",
    "diff_marketplaces",
    "normalize_source",
    "reconcile_marketplaces",
]
