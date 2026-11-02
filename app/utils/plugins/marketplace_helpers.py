"""
Marketplace display helpers and the enterprise marketplace policy (allowlist / blocklist).

Ported from `reference/claude-code/src/utils/plugins/marketplaceHelpers.ts` -> `formatFailureDetails`,
`getMarketplaceSourceDisplay`, `createPluginId`, `loadMarketplacesWithGracefulDegradation`,
`formatMarketplaceLoadingErrors`, `formatFailureNames`, `formatFailureErrors`,
`getStrictKnownMarketplaces`, `getBlockedMarketplaces`, `getPluginTrustMessage`, `areSourcesEqual`,
`extractHostFromSource`, `doesSourceMatchHostPattern`, `doesSourceMatchPathPattern`,
`getHostPatternsFromAllowlist`, `extractGitHubRepoFromGitUrl`, `blockedConstraintMatches`,
`areSourcesEquivalentForBlocklist`, `isSourceInBlocklist`, `isSourceAllowedByPolicy`,
`formatSourceForDisplay`, `EmptyMarketplaceReason`, `detectEmptyMarketplaceReason`.

The policy reads policy settings (`strictKnownMarketplaces`, `blockedMarketplaces`,
`pluginTrustMessage`), so every function that reaches them takes the run's `locations`
(upstream reads its process-wide settings). The `hostPattern` / `pathPattern` regexes are
JavaScript `RegExp` sources tested with `re.search`; syntax both engines share behaves the same.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias, cast
from urllib.parse import urlsplit

from app.types.permissions import PermissionRuleSource
from app.utils.plugins.git_availability import check_git_available
from app.utils.plugins.schemas import KnownMarketplace, PluginMarketplace
from app.utils.settings.settings import SettingsLocations, get_settings_for_source
from app.utils.string_utils import plural

logger = logging.getLogger("TDDOrchestrator.Plugins")

_SSH_HOST = re.compile(r"^[^@]+@([^:]+):")
_GITHUB_SSH = re.compile(r"^git@github\.com:([^/]+/[^/]+?)(?:\.git)?\Z", re.ASCII)
_GITHUB_HTTPS = re.compile(r"^https?://github\.com/([^/]+/[^/]+?)(?:\.git)?\Z", re.ASCII)


def format_failure_details(failures: Sequence[Mapping[str, Any]], include_reasons: bool) -> str:
    """`formatFailureDetails`: the first two failures, then ` and N more`."""
    max_show = 2
    parts: list[str] = []
    for failure in failures[:max_show]:
        reason = failure.get("reason") or failure.get("error") or "unknown error"
        parts.append(f"{failure['name']} ({reason})" if include_reasons else str(failure["name"]))
    details = ("; " if include_reasons else ", ").join(parts)
    remaining = len(failures) - max_show
    more = f" and {remaining} more" if remaining > 0 else ""
    return f"{details}{more}"


def get_marketplace_source_display(source: Mapping[str, Any]) -> str:
    """`getMarketplaceSourceDisplay`."""
    kind = source.get("source")
    if kind == "github":
        return str(source["repo"])
    if kind in ("url", "git"):
        return str(source["url"])
    if kind in ("directory", "file"):
        return str(source["path"])
    if kind == "settings":
        return f"settings:{source['name']}"
    return "Unknown source"


def create_plugin_id(plugin_name: str, marketplace_name: str) -> str:
    """`createPluginId`."""
    return f"{plugin_name}@{marketplace_name}"


@dataclass
class LoadedMarketplace:
    """One `loadMarketplacesWithGracefulDegradation` entry: `data` is `None` when it failed."""

    name: str
    config: KnownMarketplace
    data: PluginMarketplace | None


@dataclass
class MarketplaceFailure:
    """`{name, error}`."""

    name: str
    error: str


@dataclass
class GracefulLoadResult:
    marketplaces: list[LoadedMarketplace]
    failures: list[MarketplaceFailure]


async def load_marketplaces_with_graceful_degradation(
    config: Mapping[str, KnownMarketplace], locations: SettingsLocations
) -> GracefulLoadResult:
    """`loadMarketplacesWithGracefulDegradation`: blocked marketplaces are left out; failures are collected."""
    # Function-level: marketplace_manager imports this module.
    from app.utils.plugins.marketplace_manager import get_marketplace

    marketplaces: list[LoadedMarketplace] = []
    failures: list[MarketplaceFailure] = []
    for name, marketplace_config in config.items():
        if not is_source_allowed_by_policy(marketplace_config["source"], locations):
            continue
        data: PluginMarketplace | None = None
        try:
            data = await get_marketplace(name)
        except Exception as exc:  # noqa: BLE001 - one marketplace's failure must not stop the others
            failures.append(MarketplaceFailure(name=name, error=str(exc)))
            logger.error("Failed to load marketplace %s: %s", name, exc)
        marketplaces.append(LoadedMarketplace(name=name, config=marketplace_config, data=data))
    return GracefulLoadResult(marketplaces=marketplaces, failures=failures)


@dataclass(frozen=True)
class LoadingErrorMessage:
    """`{type, message}`."""

    type: Literal["warning", "error"]
    message: str


def format_marketplace_loading_errors(
    failures: Sequence[MarketplaceFailure], success_count: int
) -> LoadingErrorMessage | None:
    """`formatMarketplaceLoadingErrors`: a warning when some loaded, an error when none did."""
    if not failures:
        return None
    if success_count > 0:
        if len(failures) == 1:
            message = f"Warning: Failed to load marketplace '{failures[0].name}': {failures[0].error}"
        else:
            names = ", ".join(f.name for f in failures)
            message = f"Warning: Failed to load {len(failures)} marketplaces: {names}"
        return LoadingErrorMessage(type="warning", message=message)
    errors = "; ".join(f"{f.name}: {f.error}" for f in failures)
    return LoadingErrorMessage(type="error", message=f"Failed to load all marketplaces. Errors: {errors}")


def _policy(locations: SettingsLocations) -> dict[str, Any]:
    return get_settings_for_source(PermissionRuleSource.POLICY_SETTINGS, locations) or {}


def get_strict_known_marketplaces(locations: SettingsLocations) -> list[dict[str, Any]] | None:
    """`getStrictKnownMarketplaces`: the policy allowlist, `None` when unrestricted."""
    value = _policy(locations).get("strictKnownMarketplaces")
    if value is None or not isinstance(value, list):
        return None
    return cast(list[dict[str, Any]], value)


def get_blocked_marketplaces(locations: SettingsLocations) -> list[dict[str, Any]] | None:
    """`getBlockedMarketplaces`: the policy blocklist, `None` when there is none."""
    value = _policy(locations).get("blockedMarketplaces")
    if value is None or not isinstance(value, list):
        return None
    return cast(list[dict[str, Any]], value)


def get_plugin_trust_message(locations: SettingsLocations) -> str | None:
    """`getPluginTrustMessage`: policy settings only."""
    value = _policy(locations).get("pluginTrustMessage")
    return value if isinstance(value, str) else None


def _opt(value: Any) -> Any:
    """JavaScript's `value || undefined`: an empty value counts as absent."""
    return value or None


def are_sources_equal(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """`areSourcesEqual`: same type and the fields that identify it."""
    kind = a.get("source")
    if kind != b.get("source"):
        return False
    if kind == "url":
        return a.get("url") == b.get("url")
    if kind in ("github", "git"):
        key = "repo" if kind == "github" else "url"
        return (
            a.get(key) == b.get(key)
            and _opt(a.get("ref")) == _opt(b.get("ref"))
            and _opt(a.get("path")) == _opt(b.get("path"))
        )
    if kind == "npm":
        return a.get("package") == b.get("package")
    if kind in ("file", "directory"):
        return a.get("path") == b.get("path")
    if kind == "settings":
        return bool(a.get("name") == b.get("name") and a.get("plugins") == b.get("plugins"))
    return False


def _url_hostname(url: str) -> str | None:
    """`new URL(url).hostname`, or `None` when the URL does not parse."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return None
    if not parts.scheme:
        return None
    return host or ""


def extract_host_from_source(source: Mapping[str, Any]) -> str | None:
    """`extractHostFromSource`: github -> github.com; git SSH/HTTPS and url hosts; anything else `None`."""
    kind = source.get("source")
    if kind == "github":
        return "github.com"
    if kind == "git":
        url = str(source.get("url", ""))
        ssh = _SSH_HOST.match(url)
        if ssh and ssh.group(1):
            return ssh.group(1)
        return _url_hostname(url)
    if kind == "url":
        return _url_hostname(str(source.get("url", "")))
    return None


def _search(pattern: str, value: str, label: str) -> bool:
    try:
        return re.search(pattern, value) is not None
    except re.error:
        logger.error("Invalid %s regex: %s", label, pattern)
        return False


def does_source_match_host_pattern(source: Mapping[str, Any], pattern: Mapping[str, Any]) -> bool:
    """`doesSourceMatchHostPattern`."""
    host = extract_host_from_source(source)
    if not host:
        return False
    return _search(str(pattern.get("hostPattern", "")), host, "hostPattern")


def does_source_match_path_pattern(source: Mapping[str, Any], pattern: Mapping[str, Any]) -> bool:
    """`doesSourceMatchPathPattern`: file and directory sources only."""
    if source.get("source") not in ("file", "directory"):
        return False
    return _search(str(pattern.get("pathPattern", "")), str(source.get("path", "")), "pathPattern")


def get_host_patterns_from_allowlist(locations: SettingsLocations) -> list[str]:
    """`getHostPatternsFromAllowlist`."""
    allowlist = get_strict_known_marketplaces(locations)
    if not allowlist:
        return []
    return [str(e["hostPattern"]) for e in allowlist if e.get("source") == "hostPattern"]


def extract_github_repo_from_git_url(url: str) -> str | None:
    """`extractGitHubRepoFromGitUrl`: `owner/repo` from a GitHub SSH or HTTPS URL."""
    for pattern in (_GITHUB_SSH, _GITHUB_HTTPS):
        match = pattern.match(url)
        if match and match.group(1):
            return match.group(1)
    return None


def blocked_constraint_matches(blocked_value: str | None, source_value: str | None) -> bool:
    """`blockedConstraintMatches`: an unset blocklist constraint is a wildcard."""
    if not blocked_value:
        return True
    return bool(_opt(blocked_value) == _opt(source_value))


def are_sources_equivalent_for_blocklist(source: Mapping[str, Any], blocked: Mapping[str, Any]) -> bool:
    """`areSourcesEquivalentForBlocklist`, including github <-> git-with-a-GitHub-URL."""
    kind = source.get("source")
    if kind == blocked.get("source"):
        if kind in ("github", "git"):
            key = "repo" if kind == "github" else "url"
            if source.get(key) != blocked.get(key):
                return False
            return blocked_constraint_matches(blocked.get("ref"), source.get("ref")) and blocked_constraint_matches(
                blocked.get("path"), source.get("path")
            )
        if kind == "url":
            return source.get("url") == blocked.get("url")
        if kind == "npm":
            return source.get("package") == blocked.get("package")
        if kind in ("file", "directory"):
            return source.get("path") == blocked.get("path")
        if kind == "settings":
            return source.get("name") == blocked.get("name")
        return False
    # A git URL on GitHub and the github shorthand name the same repository, in either direction.
    if kind == "git" and blocked.get("source") == "github":
        same_repo = extract_github_repo_from_git_url(str(source.get("url", ""))) == blocked.get("repo")
    elif kind == "github" and blocked.get("source") == "git":
        same_repo = extract_github_repo_from_git_url(str(blocked.get("url", ""))) == source.get("repo")
    else:
        same_repo = False
    if same_repo:
        return blocked_constraint_matches(blocked.get("ref"), source.get("ref")) and blocked_constraint_matches(
            blocked.get("path"), source.get("path")
        )
    return False


def is_source_in_blocklist(source: Mapping[str, Any], locations: SettingsLocations) -> bool:
    """`isSourceInBlocklist`."""
    blocklist = get_blocked_marketplaces(locations)
    if blocklist is None:
        return False
    return any(are_sources_equivalent_for_blocklist(source, blocked) for blocked in blocklist)


def is_source_allowed_by_policy(source: Mapping[str, Any], locations: SettingsLocations) -> bool:
    """`isSourceAllowedByPolicy`: the blocklist first, then the allowlist (patterns or exact sources)."""
    if is_source_in_blocklist(source, locations):
        return False
    allowlist = get_strict_known_marketplaces(locations)
    if allowlist is None:
        return True
    for allowed in allowlist:
        if allowed.get("source") == "hostPattern":
            if does_source_match_host_pattern(source, allowed):
                return True
        elif allowed.get("source") == "pathPattern":
            if does_source_match_path_pattern(source, allowed):
                return True
        elif are_sources_equal(source, allowed):
            return True
    return False


def format_source_for_display(source: Mapping[str, Any]) -> str:
    """`formatSourceForDisplay`."""
    kind = source.get("source")
    ref = f"@{source['ref']}" if source.get("ref") else ""
    if kind == "github":
        return f"github:{source['repo']}{ref}"
    if kind == "url":
        return str(source["url"])
    if kind == "git":
        return f"git:{source['url']}{ref}"
    if kind == "npm":
        return f"npm:{source['package']}"
    if kind == "file":
        return f"file:{source['path']}"
    if kind == "directory":
        return f"dir:{source['path']}"
    if kind == "hostPattern":
        return f"hostPattern:{source['hostPattern']}"
    if kind == "pathPattern":
        return f"pathPattern:{source['pathPattern']}"
    if kind == "settings":
        count = len(source.get("plugins") or [])
        return f"settings:{source['name']} ({count} {plural(count, 'plugin')})"
    return "unknown source"


#: `EmptyMarketplaceReason`.
EmptyMarketplaceReason: TypeAlias = Literal[
    "git-not-installed",
    "all-blocked-by-policy",
    "policy-restricts-sources",
    "all-marketplaces-failed",
    "no-marketplaces-configured",
    "all-plugins-installed",
]


async def detect_empty_marketplace_reason(
    *, configured_marketplace_count: int, failed_marketplace_count: int, locations: SettingsLocations
) -> EmptyMarketplaceReason:
    """`detectEmptyMarketplaceReason`: git, then policy, then configuration, then failures."""
    if not await check_git_available():
        return "git-not-installed"
    allowlist = get_strict_known_marketplaces(locations)
    if allowlist is not None:
        if len(allowlist) == 0:
            return "all-blocked-by-policy"
        if configured_marketplace_count == 0:
            return "policy-restricts-sources"
    if configured_marketplace_count == 0:
        return "no-marketplaces-configured"
    if failed_marketplace_count > 0 and failed_marketplace_count == configured_marketplace_count:
        return "all-marketplaces-failed"
    return "all-plugins-installed"


__all__ = [
    "EmptyMarketplaceReason",
    "GracefulLoadResult",
    "LoadedMarketplace",
    "LoadingErrorMessage",
    "MarketplaceFailure",
    "are_sources_equal",
    "are_sources_equivalent_for_blocklist",
    "blocked_constraint_matches",
    "create_plugin_id",
    "detect_empty_marketplace_reason",
    "does_source_match_host_pattern",
    "does_source_match_path_pattern",
    "extract_github_repo_from_git_url",
    "extract_host_from_source",
    "format_failure_details",
    "format_marketplace_loading_errors",
    "format_source_for_display",
    "get_blocked_marketplaces",
    "get_host_patterns_from_allowlist",
    "get_marketplace_source_display",
    "get_plugin_trust_message",
    "get_strict_known_marketplaces",
    "is_source_allowed_by_policy",
    "is_source_in_blocklist",
    "load_marketplaces_with_graceful_degradation",
]
