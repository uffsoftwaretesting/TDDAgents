"""
Plugin dependency resolution: pure functions, no I/O beyond the settings read.

Ported from `reference/claude-code/src/utils/plugins/dependencyResolver.ts` -> `INLINE_MARKETPLACE`,
`qualifyDependency`, `DependencyLookupResult`, `ResolutionResult`, `resolveDependencyClosure`,
`verifyAndDemote`, `findReverseDependents`, `getEnabledPluginIdsForScope`,
`formatDependencyCountSuffix`, `formatReverseDependentsSuffix`.

Semantics are apt-style: a dependency is a presence guarantee. `resolve_dependency_closure` is the
install-time DFS (cycles, missing plugins, cross-marketplace blocks); `verify_and_demote` is the
load-time fixed point that demotes enabled plugins whose dependencies are not enabled.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.types.permissions import PermissionRuleSource
from app.types.plugin import DependencyUnsatisfiedError, LoadedPlugin, PluginError
from app.utils.plugins.plugin_identifier import parse_plugin_identifier
from app.utils.settings.settings import SettingsLocations, get_settings_for_source

#: `INLINE_MARKETPLACE`: the synthetic marketplace of `--plugin-dir` plugins.
INLINE_MARKETPLACE = "inline"


def qualify_dependency(dep: str, declaring_plugin_id: str) -> str:
    """`qualifyDependency`: a bare name inherits the declaring plugin's marketplace (never `inline`)."""
    if parse_plugin_identifier(dep).marketplace:
        return dep
    marketplace = parse_plugin_identifier(declaring_plugin_id).marketplace
    if not marketplace or marketplace == INLINE_MARKETPLACE:
        return dep
    return f"{dep}@{marketplace}"


@dataclass(frozen=True)
class DependencyLookupResult:
    """`DependencyLookupResult`: the entry's dependencies (bare names allowed)."""

    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolutionResult:
    """
    `ResolutionResult`: `ok` with the `closure`, or a failure `reason` (`cycle` with `chain`,
    `not-found` with `missing` / `required_by`, `cross-marketplace` with `dependency` / `required_by`).
    """

    ok: bool
    closure: tuple[str, ...] = ()
    reason: Literal["cycle", "not-found", "cross-marketplace"] | None = None
    chain: tuple[str, ...] = ()
    missing: str | None = None
    dependency: str | None = None
    required_by: str | None = None


async def resolve_dependency_closure(
    root_id: str,
    lookup: Callable[[str], Awaitable[DependencyLookupResult | None]],
    already_enabled: frozenset[str] | set[str],
    allowed_cross_marketplaces: frozenset[str] | set[str] = frozenset(),
) -> ResolutionResult:
    """
    `resolveDependencyClosure`: the root plus every transitive dependency not already enabled,
    dependencies first. The root is never skipped. Dependencies from another marketplace are
    blocked unless the root marketplace's allowlist names it (no transitive trust).
    """
    root_marketplace = parse_plugin_identifier(root_id).marketplace
    closure: list[str] = []
    visited: set[str] = set()
    stack: list[str] = []

    async def walk(plugin_id: str, required_by: str) -> ResolutionResult | None:
        if plugin_id != root_id and plugin_id in already_enabled:
            return None
        marketplace = parse_plugin_identifier(plugin_id).marketplace
        if marketplace != root_marketplace and not (marketplace and marketplace in allowed_cross_marketplaces):
            return ResolutionResult(ok=False, reason="cross-marketplace", dependency=plugin_id, required_by=required_by)
        if plugin_id in stack:
            return ResolutionResult(ok=False, reason="cycle", chain=(*stack, plugin_id))
        if plugin_id in visited:
            return None
        visited.add(plugin_id)
        entry = await lookup(plugin_id)
        if entry is None:
            return ResolutionResult(ok=False, reason="not-found", missing=plugin_id, required_by=required_by)
        stack.append(plugin_id)
        for raw in entry.dependencies:
            error = await walk(qualify_dependency(raw, plugin_id), plugin_id)
            if error is not None:
                return error
        stack.pop()
        closure.append(plugin_id)
        return None

    error = await walk(root_id, root_id)
    if error is not None:
        return error
    return ResolutionResult(ok=True, closure=tuple(closure))


@dataclass
class DemotionResult:
    """`verifyAndDemote`'s result: the plugin ids to demote and the errors for `/doctor`."""

    demoted: set[str] = field(default_factory=set)
    errors: list[PluginError] = field(default_factory=list)


def _dependencies(plugin: LoadedPlugin) -> list[str]:
    deps = plugin.manifest.get("dependencies")
    return list(deps) if deps else []


def verify_and_demote(plugins: Sequence[LoadedPlugin]) -> DemotionResult:
    """
    `verifyAndDemote`: demote every enabled plugin with an unsatisfied dependency, to a fixed
    point. Bare dependencies (from `@inline` plugins) match by name. The input is not mutated.
    """
    known = {p.source for p in plugins}
    enabled = {p.source for p in plugins if p.enabled}
    known_by_name = {parse_plugin_identifier(p.source).name for p in plugins}
    enabled_by_name: dict[str, int] = {}
    for plugin_id in enabled:
        name = parse_plugin_identifier(plugin_id).name
        enabled_by_name[name] = enabled_by_name.get(name, 0) + 1
    errors: list[PluginError] = []

    changed = True
    while changed:
        changed = False
        for plugin in plugins:
            if plugin.source not in enabled:
                continue
            for raw in _dependencies(plugin):
                dep = qualify_dependency(raw, plugin.source)
                is_bare = not parse_plugin_identifier(dep).marketplace
                satisfied = enabled_by_name.get(dep, 0) > 0 if is_bare else dep in enabled
                if satisfied:
                    continue
                enabled.discard(plugin.source)
                count = enabled_by_name.get(plugin.name, 0)
                if count <= 1:
                    enabled_by_name.pop(plugin.name, None)
                else:
                    enabled_by_name[plugin.name] = count - 1
                exists = dep in known_by_name if is_bare else dep in known
                error: DependencyUnsatisfiedError = {
                    "type": "dependency-unsatisfied",
                    "source": plugin.source,
                    "plugin": plugin.name,
                    "dependency": dep,
                    "reason": "not-enabled" if exists else "not-found",
                }
                errors.append(error)
                changed = True
                break

    demoted = {p.source for p in plugins if p.enabled and p.source not in enabled}
    return DemotionResult(demoted=demoted, errors=errors)


def find_reverse_dependents(plugin_id: str, plugins: Sequence[LoadedPlugin]) -> list[str]:
    """`findReverseDependents`: names of enabled plugins that depend on `plugin_id`."""
    target_name = parse_plugin_identifier(plugin_id).name

    def depends(plugin: LoadedPlugin) -> bool:
        for raw in _dependencies(plugin):
            qualified = qualify_dependency(raw, plugin.source)
            if parse_plugin_identifier(qualified).marketplace:
                if qualified == plugin_id:
                    return True
            elif qualified == target_name:
                return True
        return False

    return [p.name for p in plugins if p.enabled and p.source != plugin_id and depends(p)]


def get_enabled_plugin_ids_for_scope(setting_source: PermissionRuleSource, locations: SettingsLocations) -> set[str]:
    """`getEnabledPluginIdsForScope`: `true` entries and version-constraint arrays both count as enabled."""
    settings = get_settings_for_source(setting_source, locations) or {}
    enabled = settings.get("enabledPlugins")
    if not isinstance(enabled, dict):
        return set()
    return {k for k, v in enabled.items() if v is True or isinstance(v, list)}


def format_dependency_count_suffix(installed_deps: Sequence[str]) -> str:
    """`formatDependencyCountSuffix`: ` (+ N dependency|dependencies)`, empty for none."""
    if not installed_deps:
        return ""
    n = len(installed_deps)
    return f" (+ {n} {'dependency' if n == 1 else 'dependencies'})"


def format_reverse_dependents_suffix(rdeps: Sequence[str] | None) -> str:
    """`formatReverseDependentsSuffix`: ` — warning: required by X, Y`, empty for none."""
    if not rdeps:
        return ""
    return f" — warning: required by {', '.join(rdeps)}"


__all__ = [
    "INLINE_MARKETPLACE",
    "DemotionResult",
    "DependencyLookupResult",
    "ResolutionResult",
    "find_reverse_dependents",
    "format_dependency_count_suffix",
    "format_reverse_dependents_suffix",
    "get_enabled_plugin_ids_for_scope",
    "qualify_dependency",
    "resolve_dependency_closure",
    "verify_and_demote",
]
