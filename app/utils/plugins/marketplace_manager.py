"""
Marketplace manager: known marketplaces, their caches, and adding / removing / refreshing them.

Ported from `reference/claude-code/src/utils/plugins/marketplaceManager.ts` -> `LoadedPluginMarketplace`,
`getKnownMarketplacesFile`, `getMarketplacesCacheDir`, `clearMarketplacesCache`,
`KnownMarketplacesConfig`, `DeclaredMarketplace`, `getDeclaredMarketplaces`,
`getMarketplaceDeclaringSource`, `saveMarketplaceToSettings`, `loadKnownMarketplacesConfig`,
`loadKnownMarketplacesConfigSafe`, `saveKnownMarketplacesConfig`, `registerSeedMarketplaces`,
`readSeedKnownMarketplaces`, `findSeedMarketplaceLocation`, `seedDirFor`, `GIT_NO_PROMPT_ENV`,
`DEFAULT_PLUGIN_GIT_TIMEOUT_MS`, `getPluginGitTimeoutMs`, `gitPull`, `gitSubmoduleUpdate`,
`enhanceGitPullErrorMessages`, `isGitHubSshLikelyConfigured`, `isAuthenticationError`,
`extractSshHost`, `gitClone`, `MarketplaceProgressCallback`, `safeCallProgress`,
`reconcileSparseCheckout`, `cacheMarketplaceFromGit`, `redactHeaders`, `redactUrlCredentials`,
`cacheMarketplaceFromUrl`, `getCachePathForSource`, `parseFileWithSchema`,
`loadAndCacheMarketplace`, `addMarketplaceSource`, `removeMarketplaceSource`,
`readCachedMarketplace`, `getMarketplaceCacheOnly`, `getMarketplace`, `getPluginByIdCacheOnly`,
`getPluginById`, `refreshAllMarketplaces`, `refreshMarketplace`, `setMarketplaceAutoUpdate`.

Layout, under the plugins directory (`<config home>/plugins`, upstream `~/.claude/plugins`):
`known_marketplaces.json` (the state layer) and `marketplaces/<name>` (a git checkout or local
copy holding `<PLUGIN_PACKAGE_DIR>/marketplace.json`, or the downloaded `marketplace.json` itself for
a `url` source). The package folder is upstream's `.claude-plugin/` renamed `.tddagents-plugin/`
(recorded divergence, see `PLUGIN_PACKAGE_DIR` in `plugin_directories.py`).

Functions that read settings take the run's `locations` (upstream reads its process-wide
settings): `get_declared_marketplaces`, `get_marketplace_declaring_source`,
`save_marketplace_to_settings`, `add_marketplace_source` (the policy check),
`remove_marketplace_source` and `set_marketplace_auto_update`.

Product substitutions: `CLAUDE_CODE_PLUGIN_GIT_TIMEOUT_MS` and `CLAUDE_CODE_REMOTE` are
`TDDAGENTS_PLUGIN_GIT_TIMEOUT_MS` and `TDDAGENTS_REMOTE`; the user agent is
`TDDAgents-Plugin-Manager`; "Claude Code" in messages is "TDDAgents".

Not ported: fetch telemetry (`logPluginFetch`, `classifyFetchError`) and the official
marketplace's GCS mirror (`fetchOfficialMarketplaceFromGcs`; user decision 2026-11-01: the official
marketplace is constants only). For the
official marketplace the refresh therefore always takes the git path, which is where upstream goes
when the GCS fetch fails and `tengu_plugin_official_mkt_git_fallback` is at its default (true).

`get_marketplace` memoizes the load per name as a task, as lodash's memoize keeps the promise:
concurrent first calls share one load, and a failed load keeps failing until the memo is cleared.

Divergences: the HTTP download uses httpx (axios upstream) with axios's settings (10 s timeout,
five redirects, anything outside 2xx rejected); a non-JSON body is kept as text, as axios does,
and fails the schema. axios's error codes are read off the exception chain (`ECONNREFUSED`,
`ENOTFOUND`, an OS-level `ETIMEDOUT`); httpx's own timeout is axios's `ECONNABORTED`.
"""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import os
import re
import shutil
import socket
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NotRequired, TypedDict, cast
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.types.permissions import PermissionRuleSource
from app.utils.claudemd import is_env_truthy
from app.utils.errors import ConfigParseError
from app.utils.exec_file_no_throw import (
    ExecResult,
    exec_file_no_throw,
    exec_file_no_throw_with_cwd,
)
from app.utils.git.git import git_exe
from app.utils.permissions.permission_update import update_settings_for_source
from app.utils.plugins.add_dir_plugin_settings import (
    get_add_dir_enabled_plugins,
    get_add_dir_extra_marketplaces,
)
from app.utils.plugins.marketplace_helpers import (
    extract_host_from_source,
    format_source_for_display,
    get_host_patterns_from_allowlist,
    get_strict_known_marketplaces,
    is_source_allowed_by_policy,
    is_source_in_blocklist,
)
from app.utils.plugins.official_marketplace import (
    OFFICIAL_MARKETPLACE_NAME,
    OFFICIAL_MARKETPLACE_SOURCE,
)
from app.utils.plugins.plugin_directories import (
    PLUGIN_PACKAGE_DIR,
    delete_plugin_data_dir,
    get_plugin_seed_dirs,
    get_plugins_directory,
)
from app.utils.plugins.plugin_identifier import parse_plugin_identifier
from app.utils.plugins.plugin_options_storage import delete_plugin_options
from app.utils.plugins.schemas import (
    KnownMarketplacesFile,
    KnownMarketplacesFileSchema,
    PluginMarketplace,
    PluginMarketplaceEntry,
    PluginMarketplaceSchema,
    Schema,
    SchemaValidationError,
    format_issues,
    is_local_marketplace_source,
    safe_parse,
    validate_official_name_source,
)
from app.utils.settings.settings import (
    SettingsLocations,
    get_initial_settings,
    get_settings_for_source,
)

logger = logging.getLogger("TDDOrchestrator.Plugins")

#: `KnownMarketplacesConfig`.
KnownMarketplacesConfig = KnownMarketplacesFile

#: `MarketplaceProgressCallback`.
MarketplaceProgressCallback = Callable[[str], None]

KNOWN_MARKETPLACES_FILENAME = "known_marketplaces.json"
MARKETPLACES_DIRNAME = "marketplaces"
#: The manifest's place inside a marketplace repository.
MARKETPLACE_MANIFEST_RELATIVE = f"{PLUGIN_PACKAGE_DIR}/marketplace.json"
PLUGIN_GIT_TIMEOUT_ENV = "TDDAGENTS_PLUGIN_GIT_TIMEOUT_MS"
REMOTE_ENV = "TDDAGENTS_REMOTE"
#: `GIT_NO_PROMPT_ENV`: no terminal credential prompts, no askpass GUI.
GIT_NO_PROMPT_ENV: Mapping[str, str] = {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": ""}
#: `DEFAULT_PLUGIN_GIT_TIMEOUT_MS`.
DEFAULT_PLUGIN_GIT_TIMEOUT_MS = 120 * 1000
#: The URL download timeout (axios `timeout: 10000`).
URL_FETCH_TIMEOUT_MS = 10000
#: axios's default `maxRedirects`.
AXIOS_MAX_REDIRECTS = 5
USER_AGENT = "TDDAgents-Plugin-Manager"
_SSH_COMMAND = "core.sshCommand=ssh -o BatchMode=yes -o StrictHostKeyChecking=yes"

_EDITABLE_SOURCES_BY_PRECEDENCE: tuple[PermissionRuleSource, ...] = (
    PermissionRuleSource.LOCAL_SETTINGS,
    PermissionRuleSource.PROJECT_SETTINGS,
    PermissionRuleSource.USER_SETTINGS,
)
_EDITABLE_SOURCES: tuple[PermissionRuleSource, ...] = (
    PermissionRuleSource.USER_SETTINGS,
    PermissionRuleSource.PROJECT_SETTINGS,
    PermissionRuleSource.LOCAL_SETTINGS,
)


class DeclaredMarketplace(TypedDict):
    """
    `DeclaredMarketplace` (intent layer): a settings `extraKnownMarketplaces` entry plus
    `sourceIsFallback`, set only in code for the implicit official marketplace (presence suffices).
    """

    source: dict[str, Any]
    installLocation: NotRequired[str]
    autoUpdate: NotRequired[bool]
    sourceIsFallback: NotRequired[bool]


@dataclass(frozen=True)
class LoadedPluginMarketplace:
    """`LoadedPluginMarketplace`."""

    marketplace: PluginMarketplace
    cache_path: str


@dataclass(frozen=True)
class AddMarketplaceResult:
    """`addMarketplaceSource`'s result."""

    name: str
    already_materialized: bool
    resolved_source: dict[str, Any]


@dataclass(frozen=True)
class PluginByIdResult:
    """`getPluginById`'s result: the entry and its marketplace's install location."""

    entry: PluginMarketplaceEntry
    marketplace_install_location: str


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _source(entry: Mapping[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], entry["source"])


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def _rm(path: str) -> None:
    """`fs.rm(path, {recursive: true, force: true})`."""
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path, ignore_errors=False)
    elif os.path.lexists(path):
        os.unlink(path)


def get_known_marketplaces_file() -> str:
    """`getKnownMarketplacesFile`."""
    return os.path.join(get_plugins_directory(), KNOWN_MARKETPLACES_FILENAME)


def get_marketplaces_cache_dir() -> str:
    """`getMarketplacesCacheDir`."""
    return os.path.join(get_plugins_directory(), MARKETPLACES_DIRNAME)


# ── intent layer (settings) ───────────────────────────────────────────────────

def get_declared_marketplaces(locations: SettingsLocations) -> dict[str, DeclaredMarketplace]:
    """
    `getDeclaredMarketplaces`: what SHOULD exist. Precedence: the implicit official marketplace
    (when an enabled plugin references it) < `--add-dir` settings < the merged settings.
    """
    implicit: dict[str, DeclaredMarketplace] = {}
    settings = get_initial_settings(locations)
    merged_enabled = settings.get("enabledPlugins")
    enabled_plugins = {
        **get_add_dir_enabled_plugins(locations),
        **(merged_enabled if isinstance(merged_enabled, dict) else {}),
    }
    for plugin_id, value in enabled_plugins.items():
        # JS truthiness: `true` or a version-constraint array (even an empty one).
        enabled = value is True or isinstance(value, list)
        if enabled and parse_plugin_identifier(plugin_id).marketplace == OFFICIAL_MARKETPLACE_NAME:
            implicit[OFFICIAL_MARKETPLACE_NAME] = {
                "source": dict(OFFICIAL_MARKETPLACE_SOURCE),
                "sourceIsFallback": True,
            }
            break
    extra = settings.get("extraKnownMarketplaces")
    return {
        **implicit,
        **cast(dict[str, DeclaredMarketplace], get_add_dir_extra_marketplaces(locations)),
        **cast(dict[str, DeclaredMarketplace], extra if isinstance(extra, dict) else {}),
    }


def get_marketplace_declaring_source(name: str, locations: SettingsLocations) -> PermissionRuleSource | None:
    """`getMarketplaceDeclaringSource`: the highest-precedence editable source declaring `name`."""
    for source in _EDITABLE_SOURCES_BY_PRECEDENCE:
        settings = get_settings_for_source(source, locations) or {}
        extra = settings.get("extraKnownMarketplaces")
        if isinstance(extra, dict) and extra.get(name):
            return source
    return None


def save_marketplace_to_settings(
    name: str,
    entry: Mapping[str, Any],
    locations: SettingsLocations,
    setting_source: PermissionRuleSource = PermissionRuleSource.USER_SETTINGS,
) -> None:
    """`saveMarketplaceToSettings`: the intent layer only; known_marketplaces.json is untouched."""
    existing = get_settings_for_source(setting_source, locations) or {}
    current = dict(existing.get("extraKnownMarketplaces") or {})
    current[name] = dict(entry)
    update_settings_for_source(setting_source, locations, {"extraKnownMarketplaces": current})


# ── state layer (known_marketplaces.json) ─────────────────────────────────────

async def load_known_marketplaces_config() -> KnownMarketplacesConfig:
    """
    `loadKnownMarketplacesConfig`: `{}` when the file is missing; a schema failure raises
    `ConfigParseError`; unreadable or malformed JSON raises with a helpful message.
    """
    config_file = get_known_marketplaces_file()
    try:
        content = Path(config_file).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        message = f"Failed to load marketplace configuration: {exc}"
        logger.error(message)
        raise RuntimeError(message) from exc
    try:
        data = json.loads(content)
    except ValueError as exc:
        message = f"Failed to load marketplace configuration: {exc}"
        logger.error(message)
        raise RuntimeError(message) from exc
    parsed = safe_parse(KnownMarketplacesFileSchema, data)
    if not parsed.success:
        message = f"Marketplace configuration file is corrupted: {format_issues(parsed.issues)}"
        logger.error(message)
        raise ConfigParseError(message, config_file, data)
    return cast(KnownMarketplacesConfig, parsed.data)


async def load_known_marketplaces_config_safe() -> KnownMarketplacesConfig:
    """
    `loadKnownMarketplacesConfigSafe`: `{}` on any error. Read-only paths only: a
    load-mutate-save path must use the raising variant, or a corrupted file would be overwritten.
    """
    try:
        return await load_known_marketplaces_config()
    except Exception:  # noqa: BLE001 - a corrupted user config degrades to no marketplaces here
        return {}


async def save_known_marketplaces_config(config: Mapping[str, Any]) -> None:
    """`saveKnownMarketplacesConfig`: validated, then written with two-space indentation."""
    parsed = safe_parse(KnownMarketplacesFileSchema, dict(config))
    config_file = get_known_marketplaces_file()
    if not parsed.success:
        raise ConfigParseError(
            f"Invalid marketplace config: {SchemaValidationError(parsed.issues)}", config_file, config
        )
    os.makedirs(os.path.dirname(config_file), exist_ok=True)
    Path(config_file).write_text(_json(parsed.data), encoding="utf-8")


# ── seed directories ──────────────────────────────────────────────────────────

async def register_seed_marketplaces() -> bool:
    """
    `registerSeedMarketplaces`: register the read-only seed directories' marketplaces into the
    primary known_marketplaces.json. Seed entries win; the first seed claiming a name wins;
    `installLocation` is recomputed under the seed and `autoUpdate` forced off. True when
    anything was written.
    """
    seed_dirs = get_plugin_seed_dirs()
    if not seed_dirs:
        return False
    primary: dict[str, Any] = dict(await load_known_marketplaces_config())
    claimed: set[str] = set()
    changed = 0
    for seed_dir in seed_dirs:
        seed_config = await _read_seed_known_marketplaces(seed_dir)
        if seed_config is None:
            continue
        for name, seed_entry in seed_config.items():
            if name in claimed:
                continue
            resolved = await _find_seed_marketplace_location(seed_dir, name)
            if resolved is None:
                logger.warning("Seed marketplace '%s' not found under %s/marketplaces/, skipping", name, seed_dir)
                continue
            claimed.add(name)
            desired = {
                "source": seed_entry["source"],
                "installLocation": resolved,
                "lastUpdated": seed_entry["lastUpdated"],
                "autoUpdate": False,
            }
            if primary.get(name) == desired:
                continue
            primary[name] = desired
            changed += 1
    if changed > 0:
        await save_known_marketplaces_config(primary)
        logger.debug("Synced %d marketplace(s) from seed dir(s)", changed)
        return True
    return False


async def _read_seed_known_marketplaces(seed_dir: str) -> KnownMarketplacesConfig | None:
    path = os.path.join(seed_dir, KNOWN_MARKETPLACES_FILENAME)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.warning("Failed to read seed known_marketplaces.json at %s: %s", seed_dir, exc)
        return None
    parsed = safe_parse(KnownMarketplacesFileSchema, data)
    if not parsed.success:
        logger.warning(
            "Seed known_marketplaces.json invalid at %s: %s", seed_dir, SchemaValidationError(parsed.issues)
        )
        return None
    return parsed.data


async def _find_seed_marketplace_location(seed_dir: str, name: str) -> str | None:
    for candidate in (
        os.path.join(seed_dir, MARKETPLACES_DIRNAME, name),
        os.path.join(seed_dir, MARKETPLACES_DIRNAME, f"{name}.json"),
    ):
        try:
            await read_cached_marketplace(candidate)
            return candidate
        except Exception as exc:  # noqa: BLE001 - any failure means "try the next candidate"
            logger.debug("Seed marketplace candidate %s is unusable: %s", candidate, exc)
    return None


def seed_dir_for(install_location: str) -> str | None:
    """`seedDirFor`: the seed directory an install location lies in (admin-managed), or `None`."""
    for seed in get_plugin_seed_dirs():
        if install_location == seed or install_location.startswith(seed + os.sep):
            return seed
    return None


# ── git ───────────────────────────────────────────────────────────────────────

_LEADING_INT = re.compile(r"^\s*([+-]?\d+)", re.ASCII)


def get_plugin_git_timeout_ms() -> int:
    """`getPluginGitTimeoutMs`: the env override (a positive `parseInt`), else 120 s."""
    value = os.environ.get(PLUGIN_GIT_TIMEOUT_ENV)
    if value:
        match = _LEADING_INT.match(value)
        if match:
            parsed = int(match.group(1))
            if parsed > 0:
                return parsed
    return DEFAULT_PLUGIN_GIT_TIMEOUT_MS


def _git_env() -> dict[str, str]:
    return {**os.environ, **GIT_NO_PROMPT_ENV}


async def git_pull(
    cwd: str,
    ref: str | None = None,
    *,
    disable_credential_helper: bool = False,
    sparse_paths: Sequence[str] | None = None,
) -> ExecResult:
    """`gitPull`: fetch + checkout + pull of `ref`, or `pull origin HEAD`; then the submodules."""
    logger.debug("git pull: cwd=%s ref=%s", cwd, ref or "default")
    env = _git_env()
    credential_args = ["-c", "credential.helper="] if disable_credential_helper else []
    timeout = get_plugin_git_timeout_ms()
    if ref:
        for args in (["fetch", "origin", ref], ["checkout", ref]):
            step = await exec_file_no_throw_with_cwd(
                git_exe(), [*credential_args, *args], cwd=cwd, timeout_ms=timeout, stdin="ignore", env=env
            )
            if step.code != 0:
                return enhance_git_pull_error_messages(step)
        pull = await exec_file_no_throw_with_cwd(
            git_exe(), [*credential_args, "pull", "origin", ref], cwd=cwd, timeout_ms=timeout, stdin="ignore", env=env
        )
        if pull.code != 0:
            return enhance_git_pull_error_messages(pull)
        await _git_submodule_update(cwd, credential_args, env, sparse_paths)
        return pull
    result = await exec_file_no_throw_with_cwd(
        git_exe(), [*credential_args, "pull", "origin", "HEAD"], cwd=cwd, timeout_ms=timeout, stdin="ignore", env=env
    )
    if result.code != 0:
        return enhance_git_pull_error_messages(result)
    await _git_submodule_update(cwd, credential_args, env, sparse_paths)
    return result


async def _git_submodule_update(
    cwd: str, credential_args: Sequence[str], env: Mapping[str, str], sparse_paths: Sequence[str] | None
) -> None:
    """`gitSubmoduleUpdate`: only when `.gitmodules` exists and the clone is not sparse; non-fatal."""
    if sparse_paths:
        return
    if not os.path.exists(os.path.join(cwd, ".gitmodules")):
        return
    result = await exec_file_no_throw_with_cwd(
        git_exe(),
        ["-c", _SSH_COMMAND, *credential_args, "submodule", "update", "--init", "--recursive", "--depth", "1"],
        cwd=cwd,
        timeout_ms=get_plugin_git_timeout_ms(),
        stdin="ignore",
        env=env,
    )
    if result.code != 0:
        logger.warning("git submodule update failed (non-fatal): %s", result.stderr)


def enhance_git_pull_error_messages(result: ExecResult) -> ExecResult:
    """`enhanceGitPullErrorMessages`."""
    if result.code == 0:
        return result
    stderr = result.stderr
    if result.error and "timed out" in result.error:
        timeout_sec = round(get_plugin_git_timeout_ms() / 1000)
        return replace(result, stderr=(
            f"Git pull timed out after {timeout_sec}s. Try increasing the timeout via {PLUGIN_GIT_TIMEOUT_ENV} "
            f"environment variable.\n\nOriginal error: {stderr}"
        ))
    if "REMOTE HOST IDENTIFICATION HAS CHANGED" in stderr:
        return replace(result, stderr=(
            "SSH host key for this marketplace's git host has changed (server key rotation or possible MITM). "
            "Remove the stale entry with: ssh-keygen -R <host>\nThen connect once manually to accept the new key."
            f"\n\nOriginal error: {stderr}"
        ))
    if "Host key verification failed" in stderr:
        return replace(result, stderr=(
            "SSH host key verification failed while updating marketplace. The host key is not in your known_hosts "
            "file. Connect once manually to add it (e.g., ssh -T git@<host>), or remove and re-add the marketplace "
            f"with an HTTPS URL.\n\nOriginal error: {stderr}"
        ))
    if "Permission denied (publickey)" in stderr or "Could not read from remote repository" in stderr:
        return replace(result, stderr=(
            "SSH authentication failed while updating marketplace. Please ensure your SSH keys are configured."
            f"\n\nOriginal error: {stderr}"
        ))
    if "timed out" in stderr or "Could not resolve host" in stderr:
        return replace(result, stderr=(
            "Network error while updating marketplace. Please check your internet connection."
            f"\n\nOriginal error: {stderr}"
        ))
    return result


async def is_github_ssh_likely_configured() -> bool:
    """`isGitHubSshLikelyConfigured`: a 2 s BatchMode probe; unknown host keys fail closed."""
    try:
        result = await exec_file_no_throw(
            "ssh",
            ["-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=2", "-o", "StrictHostKeyChecking=yes",
             "git@github.com"],
            timeout_ms=3000,
        )
    except Exception as exc:  # noqa: BLE001 - any failure means SSH is not configured
        logger.warning("SSH configuration check failed: %s", exc)
        return False
    configured = result.code == 1 and (
        "successfully authenticated" in result.stderr or "successfully authenticated" in result.stdout
    )
    logger.debug("SSH config check: code=%s configured=%s", result.code, configured)
    return configured


def is_authentication_error(stderr: str) -> bool:
    """`isAuthenticationError`."""
    return any(marker in stderr for marker in (
        "Authentication failed", "could not read Username", "terminal prompts disabled", "403", "401",
    ))


_SSH_HOST = re.compile(r"^[^@]+@([^:]+):")


def extract_ssh_host(git_url: str) -> str | None:
    """`extractSshHost`: the host of `user@host:path`."""
    match = _SSH_HOST.match(git_url)
    return match.group(1) if match else None


async def git_clone(
    git_url: str, target_path: str, ref: str | None = None, sparse_paths: Sequence[str] | None = None
) -> ExecResult:
    """
    `gitClone`: a shallow clone (`--depth 1`; partial and sparse with `sparse_paths`, else with
    shallow submodules), credentials redacted from the result, common failures explained.
    """
    use_sparse = bool(sparse_paths)
    args = ["-c", _SSH_COMMAND, "clone", "--depth", "1"]
    if use_sparse:
        args += ["--filter=blob:none", "--no-checkout"]
    else:
        args += ["--recurse-submodules", "--shallow-submodules"]
    if ref:
        args += ["--branch", ref]
    args += [git_url, target_path]
    timeout_ms = get_plugin_git_timeout_ms()
    logger.debug("git clone: url=%s ref=%s timeout=%sms", redact_url_credentials(git_url), ref or "default", timeout_ms)
    result = await exec_file_no_throw_with_cwd(git_exe(), args, timeout_ms=timeout_ms, stdin="ignore", env=_git_env())

    redacted = redact_url_credentials(git_url)
    if git_url != redacted:
        result = replace(
            result,
            error=result.error.replace(git_url, redacted) if result.error else result.error,
            stderr=result.stderr.replace(git_url, redacted) if result.stderr else result.stderr,
        )

    if result.code == 0:
        if use_sparse and sparse_paths:
            sparse = await exec_file_no_throw_with_cwd(
                git_exe(), ["sparse-checkout", "set", "--cone", "--", *sparse_paths],
                cwd=target_path, timeout_ms=timeout_ms, stdin="ignore", env=_git_env(),
            )
            if sparse.code != 0:
                return ExecResult(
                    stdout="", stderr=f"git sparse-checkout set failed: {sparse.stderr}", code=sparse.code
                )
            checkout = await exec_file_no_throw_with_cwd(
                git_exe(), ["checkout", "HEAD"], cwd=target_path, timeout_ms=timeout_ms, stdin="ignore", env=_git_env()
            )
            if checkout.code != 0:
                return ExecResult(
                    stdout="",
                    stderr=f"git checkout after sparse-checkout failed: {checkout.stderr}",
                    code=checkout.code,
                )
        logger.debug("git clone succeeded: %s", redacted)
        return result

    logger.warning(
        "git clone failed: url=%s code=%s error=%s stderr=%s",
        redacted, result.code, result.error or "none", result.stderr,
    )
    stderr = result.stderr
    if result.error and "timed out" in result.error:
        return replace(result, stderr=(
            f"Git clone timed out after {round(timeout_ms / 1000)}s. The repository may be too large for the current "
            f"timeout. Set {PLUGIN_GIT_TIMEOUT_ENV} to increase it (e.g., 300000 for 5 minutes)."
            f"\n\nOriginal error: {stderr}"
        ))
    if stderr:
        if "REMOTE HOST IDENTIFICATION HAS CHANGED" in stderr:
            host = extract_ssh_host(git_url)
            hint = f"ssh-keygen -R {host}" if host else "ssh-keygen -R <host>"
            return replace(result, stderr=(
                "SSH host key has changed (server key rotation or possible MITM). Remove the stale known_hosts "
                f"entry:\n  {hint}\nThen connect once manually to verify and accept the new key."
                f"\n\nOriginal error: {stderr}"
            ))
        if "Host key verification failed" in stderr:
            host = extract_ssh_host(git_url)
            hint = f"ssh -T git@{host}" if host else "ssh -T git@<host>"
            return replace(result, stderr=(
                "SSH host key is not in your known_hosts file. To add it, connect once manually (this will show "
                f"the fingerprint for you to verify):\n  {hint}\n\nOr use an HTTPS URL instead (recommended for "
                f"public repos).\n\nOriginal error: {stderr}"
            ))
        if "Permission denied (publickey)" in stderr or "Could not read from remote repository" in stderr:
            return replace(result, stderr=(
                "SSH authentication failed. Please ensure your SSH keys are configured for GitHub, or use an HTTPS "
                f"URL instead.\n\nOriginal error: {stderr}"
            ))
        if is_authentication_error(stderr):
            return replace(result, stderr=(
                "HTTPS authentication failed. Please ensure your credential helper is configured (e.g., gh auth "
                f"login).\n\nOriginal error: {stderr}"
            ))
        if "timed out" in stderr or "timeout" in stderr or "Could not resolve host" in stderr:
            return replace(result, stderr=(
                "Network error or timeout while cloning repository. Please check your internet connection and try "
                f"again.\n\nOriginal error: {stderr}"
            ))
    if not stderr:
        return ExecResult(
            stdout=result.stdout,
            stderr=result.error
            or f"git clone exited with code {result.code} (no stderr output). Run with --debug to see the full "
            "command.",
            code=result.code,
        )
    return result


def safe_call_progress(on_progress: MarketplaceProgressCallback | None, message: str) -> None:
    """`safeCallProgress`: a progress callback's exception is logged, never raised."""
    if on_progress is None:
        return
    try:
        on_progress(message)
    except Exception as exc:  # noqa: BLE001 - a progress callback must not abort the operation
        logger.warning("Progress callback error: %s", exc)


async def reconcile_sparse_checkout(cwd: str, sparse_paths: Sequence[str] | None) -> ExecResult:
    """
    `reconcileSparseCheckout`: set the cone when sparse paths are configured; a sparse repository
    whose config dropped them returns non-zero so the caller re-clones (never `disable`).
    """
    env = _git_env()
    if sparse_paths:
        return await exec_file_no_throw_with_cwd(
            git_exe(), ["sparse-checkout", "set", "--cone", "--", *sparse_paths],
            cwd=cwd, timeout_ms=get_plugin_git_timeout_ms(), stdin="ignore", env=env,
        )
    if not os.path.isdir(cwd):
        # execa reports a missing cwd as a failed spawn; the caller then re-clones.
        return ExecResult(stdout="", stderr="", code=1)
    check = await exec_file_no_throw_with_cwd(
        git_exe(), ["config", "--get", "core.sparseCheckout"], cwd=cwd, stdin="ignore", env=env
    )
    if check.code == 0 and check.stdout.strip() == "true":
        return ExecResult(
            stdout="",
            stderr="sparsePaths removed from config but repository is sparse; re-cloning for full checkout",
            code=1,
        )
    return ExecResult(stdout="", stderr="", code=0)


async def cache_marketplace_from_git(
    git_url: str,
    cache_path: str,
    ref: str | None = None,
    sparse_paths: Sequence[str] | None = None,
    on_progress: MarketplaceProgressCallback | None = None,
    *,
    disable_credential_helper: bool = False,
) -> None:
    """`cacheMarketplaceFromGit`: pull in place; on any failure remove the directory and clone once."""
    timeout_sec = round(get_plugin_git_timeout_ms() / 1000)
    safe_call_progress(on_progress, f"Refreshing marketplace cache (timeout: {timeout_sec}s)…")
    reconcile = await reconcile_sparse_checkout(cache_path, sparse_paths)
    if reconcile.code == 0:
        pull = await git_pull(
            cache_path, ref, disable_credential_helper=disable_credential_helper, sparse_paths=sparse_paths
        )
        if pull.code == 0:
            return
        logger.warning("git pull failed, will re-clone: %s", pull.stderr)
    else:
        logger.debug("sparse-checkout reconcile requires re-clone: %s", reconcile.stderr)

    if os.path.lexists(cache_path):
        try:
            _rm(cache_path)
        except OSError as exc:
            raise RuntimeError(
                "Failed to clean up existing marketplace directory. Please manually delete the directory at "
                f"{cache_path} and try again.\n\nTechnical details: {exc}"
            ) from exc
        logger.warning("Found stale marketplace directory at %s, cleaning up to allow re-clone", cache_path)
        safe_call_progress(on_progress, "Found stale directory, cleaning up and re-cloning…")

    ref_message = f" (ref: {ref})" if ref else ""
    safe_call_progress(
        on_progress, f"Cloning repository (timeout: {timeout_sec}s): {redact_url_credentials(git_url)}{ref_message}"
    )
    result = await git_clone(git_url, cache_path, ref, sparse_paths)
    if result.code != 0:
        try:
            _rm(cache_path)
        except OSError:
            pass
        raise RuntimeError(f"Failed to clone marketplace repository: {result.stderr}")
    safe_call_progress(on_progress, "Clone complete, validating marketplace…")


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """`redactHeaders`."""
    return {key: "***REDACTED***" for key in headers}


def redact_url_credentials(url_string: str) -> str:
    """`redactUrlCredentials`: http(s) userinfo becomes `***`; anything else passes through."""
    try:
        parts = urlsplit(url_string)
    except ValueError:
        return url_string
    if parts.scheme not in ("http", "https") or "@" not in parts.netloc:
        return url_string
    userinfo, _, hostport = parts.netloc.rpartition("@")
    username, has_password, password = userinfo.partition(":")
    if not username and not password:
        return url_string
    redacted = ("***" if username else "") + ((":" + ("***" if password else "")) if has_password and password else "")
    return urlunsplit((parts.scheme, f"{redacted}@{hostport}", parts.path, parts.query, parts.fragment))


@dataclass(frozen=True)
class HttpResponse:
    """The part of a response `cache_marketplace_from_url` reads."""

    status: int
    data: Any


def _network_error_code(exc: BaseException) -> str | None:
    """The Node error code axios would carry: refused, DNS failure or an OS-level connect timeout."""
    seen: BaseException | None = exc
    while seen is not None:
        if isinstance(seen, ConnectionRefusedError):
            return "ECONNREFUSED"
        if isinstance(seen, socket.gaierror):
            return "ENOTFOUND"
        if isinstance(seen, OSError) and seen.errno == errno.ETIMEDOUT:
            return "ETIMEDOUT"
        seen = seen.__cause__ or seen.__context__
    return None


async def http_get(url: str, headers: Mapping[str, str], timeout_ms: int) -> HttpResponse:
    """
    `axios.get(url, {timeout, headers})`: follows redirects, raises on a non-2xx status, parses
    a JSON body (else keeps the text). The seam tests replace.
    """
    async with httpx.AsyncClient(
        timeout=timeout_ms / 1000, follow_redirects=True, max_redirects=AXIOS_MAX_REDIRECTS
    ) as client:
        response = await client.get(url, headers=dict(headers))
    if not 200 <= response.status_code < 300:
        # axios's `validateStatus` rejects anything outside 2xx.
        raise httpx.HTTPStatusError(
            f"Request failed with status code {response.status_code}", request=response.request, response=response
        )
    try:
        data: Any = response.json()
    except ValueError:
        data = response.text
    return HttpResponse(status=response.status_code, data=data)


async def cache_marketplace_from_url(
    url: str,
    cache_path: str,
    custom_headers: Mapping[str, str] | None = None,
    on_progress: MarketplaceProgressCallback | None = None,
) -> None:
    """`cacheMarketplaceFromUrl`: download marketplace.json, validate it, write it to `cache_path`."""
    redacted_url = redact_url_credentials(url)
    safe_call_progress(on_progress, f"Downloading marketplace from {redacted_url}")
    logger.debug("Downloading marketplace from URL: %s", redacted_url)
    if custom_headers:
        logger.debug("Using custom headers: %s", json.dumps(redact_headers(custom_headers)))
    headers = {**(custom_headers or {}), "User-Agent": USER_AGENT}
    try:
        response = await http_get(url, headers, URL_FETCH_TIMEOUT_MS)
    except httpx.TimeoutException as exc:
        # axios reports its own timeout as ECONNABORTED, which takes the generic message.
        raise RuntimeError(
            f"Failed to download marketplace from {redacted_url}: timeout of {URL_FETCH_TIMEOUT_MS}ms exceeded"
        ) from exc
    except httpx.TransportError as exc:
        code = _network_error_code(exc)
        if code in ("ECONNREFUSED", "ENOTFOUND"):
            raise RuntimeError(
                f"Could not connect to {redacted_url}. Please check your internet connection and verify the URL is "
                f"correct.\n\nTechnical details: {exc}"
            ) from exc
        if code == "ETIMEDOUT":
            raise RuntimeError(
                f"Request timed out while downloading marketplace from {redacted_url}. The server may be slow or "
                f"unreachable.\n\nTechnical details: {exc}"
            ) from exc
        raise RuntimeError(f"Failed to download marketplace from {redacted_url}: {exc}") from exc
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        raise RuntimeError(
            f"HTTP {status} error while downloading marketplace from {redacted_url}. The marketplace file may not "
            f"exist at this URL.\n\nTechnical details: Request failed with status code {status}"
        ) from exc
    except Exception as exc:
        raise RuntimeError(f"Failed to download marketplace from {redacted_url}: {exc}") from exc

    safe_call_progress(on_progress, "Validating marketplace data")
    result = safe_parse(PluginMarketplaceSchema, response.data)
    if not result.success:
        raise ConfigParseError(
            f"Invalid marketplace schema from URL: {format_issues(result.issues)}", redacted_url, response.data
        )
    safe_call_progress(on_progress, "Saving marketplace to cache")
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    Path(cache_path).write_text(_json(result.data), encoding="utf-8")


def get_cache_path_for_source(source: Mapping[str, Any]) -> str:
    """`getCachePathForSource`: a temporary cache name (JavaScript's first-occurrence `replace`)."""
    kind = source.get("source")
    if kind == "github":
        return str(source["repo"]).replace("/", "-", 1)
    if kind == "npm":
        return str(source["package"]).replace("@", "", 1).replace("/", "-", 1)
    if kind == "file":
        return os.path.basename(str(source["path"])).replace(".json", "", 1)
    if kind == "directory":
        return os.path.basename(str(source["path"]))
    return f"temp_{int(time.time() * 1000)}"


async def parse_file_with_schema(file_path: str, schema: Schema[Any]) -> Any:
    """`parseFileWithSchema`: read (I/O errors propagate), parse JSON, validate; `ConfigParseError` otherwise."""
    content = Path(file_path).read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(content)
    except ValueError as exc:
        raise ConfigParseError(f"Invalid JSON in {file_path}: {exc}", file_path, content) from exc
    result = safe_parse(schema, data)
    if not result.success:
        raise ConfigParseError(f"Invalid schema: {file_path} {format_issues(result.issues)}", file_path, data)
    return result.data


async def _clone_github(
    source: Mapping[str, Any], cache_path: str, on_progress: MarketplaceProgressCallback | None
) -> None:
    """The `github` arm of `loadAndCacheMarketplace`: the likely protocol first, the other as fallback."""
    repo = source["repo"]
    ssh_url = f"git@github.com:{repo}.git"
    https_url = f"https://github.com/{repo}.git"
    ssh_configured = await is_github_ssh_likely_configured()
    if ssh_configured:
        safe_call_progress(on_progress, f"Cloning via SSH: {ssh_url}")
        primary, fallback = ssh_url, https_url
        retry_message = f"SSH clone failed, retrying with HTTPS: {https_url}"
    else:
        safe_call_progress(on_progress, f"SSH not configured, cloning via HTTPS: {https_url}")
        logger.info("SSH not configured for GitHub, using HTTPS for %s", repo)
        primary, fallback = https_url, ssh_url
        retry_message = f"HTTPS clone failed, retrying with SSH: {ssh_url}"
    try:
        await cache_marketplace_from_git(primary, cache_path, source.get("ref"), source.get("sparsePaths"), on_progress)
        return
    except Exception as exc:  # noqa: BLE001 - the other protocol is tried before giving up
        logger.error("GitHub clone failed via %s: %s", "SSH" if ssh_configured else "HTTPS", exc)
        safe_call_progress(on_progress, retry_message)
        logger.info("Clone failed for %s, falling back to %s", repo, "HTTPS" if ssh_configured else "SSH")
        _rm(cache_path)
    await cache_marketplace_from_git(fallback, cache_path, source.get("ref"), source.get("sparsePaths"), on_progress)


async def load_and_cache_marketplace(
    source: Mapping[str, Any], on_progress: MarketplaceProgressCallback | None = None
) -> LoadedPluginMarketplace:
    """
    `loadAndCacheMarketplace`: fetch the source into the cache, validate marketplace.json, and
    rename the cache entry to the marketplace's own name (remote sources only). A temporary
    cache is removed on failure; a user's local path never is.
    """
    cache_dir = get_marketplaces_cache_dir()
    os.makedirs(cache_dir, exist_ok=True)
    temporary_cache_path: str | None = None
    cleanup_needed = False
    temp_name = get_cache_path_for_source(source)
    kind = source.get("source")
    try:
        if kind == "url":
            temporary_cache_path = os.path.join(cache_dir, f"{temp_name}.json")
            cleanup_needed = True
            await cache_marketplace_from_url(source["url"], temporary_cache_path, source.get("headers"), on_progress)
            marketplace_path = temporary_cache_path
        elif kind == "github":
            temporary_cache_path = os.path.join(cache_dir, temp_name)
            cleanup_needed = True
            await _clone_github(source, temporary_cache_path, on_progress)
            marketplace_path = os.path.join(temporary_cache_path, source.get("path") or MARKETPLACE_MANIFEST_RELATIVE)
        elif kind == "git":
            temporary_cache_path = os.path.join(cache_dir, temp_name)
            cleanup_needed = True
            await cache_marketplace_from_git(
                source["url"], temporary_cache_path, source.get("ref"), source.get("sparsePaths"), on_progress
            )
            marketplace_path = os.path.join(temporary_cache_path, source.get("path") or MARKETPLACE_MANIFEST_RELATIVE)
        elif kind == "npm":
            raise RuntimeError("NPM marketplace sources not yet implemented")
        elif kind == "file":
            abs_path = os.path.abspath(source["path"])
            marketplace_path = abs_path
            temporary_cache_path = os.path.dirname(os.path.dirname(abs_path))
        elif kind == "directory":
            abs_path = os.path.abspath(source["path"])
            marketplace_path = os.path.join(abs_path, PLUGIN_PACKAGE_DIR, "marketplace.json")
            temporary_cache_path = abs_path
        elif kind == "settings":
            temporary_cache_path = os.path.join(cache_dir, source["name"])
            marketplace_path = os.path.join(temporary_cache_path, PLUGIN_PACKAGE_DIR, "marketplace.json")
            os.makedirs(os.path.dirname(marketplace_path), exist_ok=True)
            synthetic = {
                "name": source["name"],
                "owner": source.get("owner") or {"name": "settings"},
                "plugins": source["plugins"],
            }
            Path(marketplace_path).write_text(_json(synthetic), encoding="utf-8")
        else:
            raise RuntimeError("Unsupported marketplace source type")

        logger.debug("Reading marketplace from %s", marketplace_path)
        try:
            parsed = await parse_file_with_schema(marketplace_path, PluginMarketplaceSchema)
            marketplace = cast(PluginMarketplace, parsed)
        except FileNotFoundError as exc:
            raise RuntimeError(f"Marketplace file not found at {marketplace_path}") from exc
        except Exception as exc:
            raise RuntimeError(f"Failed to parse marketplace file at {marketplace_path}: {exc}") from exc

        final_cache_path = os.path.join(cache_dir, marketplace["name"])
        resolved_final = os.path.abspath(final_cache_path)
        resolved_cache_dir = os.path.abspath(cache_dir)
        if not resolved_final.startswith(resolved_cache_dir + os.sep):
            raise RuntimeError(
                f"Marketplace name '{marketplace['name']}' resolves to a path outside the cache directory"
            )
        if temporary_cache_path != final_cache_path and not is_local_marketplace_source(source):
            try:
                safe_call_progress(on_progress, "Cleaning up old marketplace cache…")
                _rm(final_cache_path)
                os.rename(cast(str, temporary_cache_path), final_cache_path)
                temporary_cache_path = final_cache_path
                cleanup_needed = False
            except OSError as exc:
                raise RuntimeError(
                    "Failed to finalize marketplace cache. Please manually delete the directory at "
                    f"{final_cache_path} if it exists and try again.\n\nTechnical details: {exc}"
                ) from exc
        return LoadedPluginMarketplace(marketplace=marketplace, cache_path=cast(str, temporary_cache_path))
    except BaseException:
        if cleanup_needed and temporary_cache_path and not is_local_marketplace_source(source):
            try:
                _rm(temporary_cache_path)
            except OSError as cleanup_error:
                logger.warning(
                    "Warning: Failed to clean up temporary marketplace cache at %s: %s",
                    temporary_cache_path, cleanup_error,
                )
        raise


# ── add / remove ──────────────────────────────────────────────────────────────

def _policy_block_message(source: Mapping[str, Any], locations: SettingsLocations) -> str:
    if is_source_in_blocklist(source, locations):
        return f"Marketplace source '{format_source_for_display(source)}' is blocked by enterprise policy."
    allowlist = get_strict_known_marketplaces(locations) or []
    host_patterns = get_host_patterns_from_allowlist(locations)
    source_host = extract_host_from_source(source)
    message = f"Marketplace source '{format_source_for_display(source)}'"
    if source_host:
        message += f" ({source_host})"
    message += " is blocked by enterprise policy."
    if allowlist:
        message += f" Allowed sources: {', '.join(format_source_for_display(s) for s in allowlist)}"
    else:
        message += " No external marketplaces are allowed."
    if source.get("source") == "github" and host_patterns:
        repo = source["repo"]
        message += (
            f'\n\nTip: The shorthand "{repo}" assumes github.com. For internal GitHub Enterprise, use the full URL:\n'
            f"  git@your-github-host.com:{repo}.git"
        )
    return message


async def add_marketplace_source(
    source: Mapping[str, Any],
    locations: SettingsLocations,
    on_progress: MarketplaceProgressCallback | None = None,
) -> AddMarketplaceResult:
    """
    `addMarketplaceSource`: policy first (before any network or disk work), then source
    idempotency (an identical source already materialized is not re-cloned), then fetch,
    reserved-name check, and the config entry under the marketplace's own name.
    """
    resolved: dict[str, Any] = dict(source)
    if is_local_marketplace_source(source) and not os.path.isabs(str(source["path"])):
        resolved["path"] = os.path.abspath(str(source["path"]))

    if not is_source_allowed_by_policy(resolved, locations):
        raise RuntimeError(_policy_block_message(resolved, locations))

    existing = await load_known_marketplaces_config()
    for existing_name, existing_entry in existing.items():
        if _source(existing_entry) == resolved:
            logger.debug("Source already materialized as '%s', skipping clone", existing_name)
            return AddMarketplaceResult(name=existing_name, already_materialized=True, resolved_source=resolved)

    loaded = await load_and_cache_marketplace(resolved, on_progress)
    marketplace, cache_path = loaded.marketplace, loaded.cache_path
    validation_error = validate_official_name_source(marketplace["name"], resolved)
    if validation_error:
        raise ValueError(validation_error)

    config: dict[str, Any] = dict(await load_known_marketplaces_config())
    old_entry = config.get(marketplace["name"])
    if old_entry:
        seed_dir = seed_dir_for(old_entry["installLocation"])
        if seed_dir:
            raise ValueError(
                f"Marketplace '{marketplace['name']}' is seed-managed ({seed_dir}). To use a different source, ask "
                "your admin to update the seed, or use a different marketplace name."
            )
        logger.debug("Marketplace '%s' exists with different source — overwriting", marketplace["name"])
        if not is_local_marketplace_source(_source(old_entry)):
            cache_dir = os.path.abspath(get_marketplaces_cache_dir())
            resolved_old = os.path.abspath(old_entry["installLocation"])
            resolved_new = os.path.abspath(cache_path)
            if resolved_old == resolved_new:
                pass
            elif resolved_old == cache_dir or resolved_old.startswith(cache_dir + os.sep):
                _rm(old_entry["installLocation"])
            else:
                logger.warning(
                    "Skipping cleanup of old installLocation (%s) — outside %s. The path is corrupted; leaving it "
                    "alone and overwriting the config entry.", old_entry["installLocation"], cache_dir,
                )
    config[marketplace["name"]] = {"source": resolved, "installLocation": cache_path, "lastUpdated": _now_iso()}
    await save_known_marketplaces_config(config)
    logger.debug("Added marketplace source: %s", marketplace["name"])
    return AddMarketplaceResult(name=marketplace["name"], already_materialized=False, resolved_source=resolved)


async def remove_marketplace_source(name: str, locations: SettingsLocations) -> None:
    """
    `removeMarketplaceSource`: the config entry, both cache formats, the settings declarations
    and enabled plugins in every editable source, then the installed plugins (orphaned paths
    marked, options and data directories deleted).
    """
    # Function-level: cache_utils and installed_plugins_manager import this module.
    from app.utils.plugins.cache_utils import mark_plugin_version_orphaned
    from app.utils.plugins.installed_plugins_manager import (
        remove_all_plugins_for_marketplace,
    )
    config: dict[str, Any] = dict(await load_known_marketplaces_config())
    if name not in config:
        raise ValueError(f"Marketplace '{name}' not found")
    seed_dir = seed_dir_for(config[name]["installLocation"])
    if seed_dir:
        raise ValueError(
            f"Marketplace '{name}' is registered from the read-only seed directory ({seed_dir}) and will be "
            f"re-registered on next startup. To stop using its plugins: claude plugin disable <plugin>@{name}"
        )
    del config[name]
    await save_known_marketplaces_config(config)

    cache_dir = get_marketplaces_cache_dir()
    _rm(os.path.join(cache_dir, name))
    _rm(os.path.join(cache_dir, f"{name}.json"))

    for source in _EDITABLE_SOURCES:
        settings = get_settings_for_source(source, locations)
        if not settings:
            continue
        updates: dict[str, Any] = {}
        extra = settings.get("extraKnownMarketplaces")
        if isinstance(extra, dict) and extra.get(name):
            updates["extraKnownMarketplaces"] = {**extra, name: None}
        enabled = settings.get("enabledPlugins")
        if isinstance(enabled, dict):
            suffix = f"@{name}"
            removed = {plugin_id: None for plugin_id in enabled if plugin_id.endswith(suffix)}
            if removed:
                updates["enabledPlugins"] = {**enabled, **removed}
        if updates:
            error = update_settings_for_source(source, locations, updates)
            if error:
                logger.error("Failed to clean up marketplace '%s' from %s settings: %s", name, source, error)
            else:
                logger.debug("Cleaned up marketplace '%s' from %s settings", name, source)

    removed_plugins = remove_all_plugins_for_marketplace(name)
    for install_path in removed_plugins.orphaned_paths:
        await mark_plugin_version_orphaned(install_path)
    for plugin_id in removed_plugins.removed_plugin_ids:
        delete_plugin_options(plugin_id, locations)
        await delete_plugin_data_dir(plugin_id)
    logger.debug("Removed marketplace source: %s", name)


# ── reading ───────────────────────────────────────────────────────────────────

async def read_cached_marketplace(install_location: str) -> PluginMarketplace:
    """
    `readCachedMarketplace`: `<location>/<PLUGIN_PACKAGE_DIR>/marketplace.json`, else the location
    itself when it is a file (ENOTDIR) or the nested file is missing (ENOENT).
    """
    nested = os.path.join(install_location, PLUGIN_PACKAGE_DIR, "marketplace.json")
    try:
        return cast(PluginMarketplace, await parse_file_with_schema(nested, PluginMarketplaceSchema))
    except (FileNotFoundError, NotADirectoryError):
        pass
    return cast(PluginMarketplace, await parse_file_with_schema(install_location, PluginMarketplaceSchema))


def _read_raw_known_marketplaces() -> dict[str, Any]:
    """The config file parsed without the schema (upstream's `jsonParse(content) as ...` on cache-only paths)."""
    data = json.loads(Path(get_known_marketplaces_file()).read_text(encoding="utf-8", errors="replace"))
    return data if isinstance(data, dict) else {}


async def get_marketplace_cache_only(name: str) -> PluginMarketplace | None:
    """`getMarketplaceCacheOnly`: from the cache only, never the network; `None` when missing or broken."""
    try:
        entry = _read_raw_known_marketplaces().get(name)
        if not entry:
            return None
        return await read_cached_marketplace(entry["installLocation"])
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001 - a broken cache reads as absent on startup paths
        logger.warning("Failed to read cached marketplace %s: %s", name, exc)
        return None


_marketplace_cache: dict[str, asyncio.Future[PluginMarketplace]] = {}


def clear_marketplaces_cache() -> None:
    """`clearMarketplacesCache`."""
    _marketplace_cache.clear()


async def _get_marketplace_uncached(name: str) -> PluginMarketplace:
    config: dict[str, Any] = dict(await load_known_marketplaces_config())
    entry = config.get(name)
    if not entry:
        raise ValueError(
            f"Marketplace '{name}' not found in configuration. Available marketplaces: {', '.join(config)}"
        )
    source = _source(entry)
    if is_local_marketplace_source(source) and not os.path.isabs(str(source["path"])):
        raise ValueError(
            f'Marketplace "{name}" has a relative source path ({source["path"]}) in known_marketplaces.json — this '
            "is stale state from an older TDDAgents version. Run 'claude marketplace remove "
            f"{name}' and re-add it from the original project directory."
        )
    try:
        return await read_cached_marketplace(entry["installLocation"])
    except Exception as exc:  # noqa: BLE001 - a missing or corrupt cache falls back to the source
        logger.warning("Cache corrupted or missing for marketplace %s, re-fetching from source: %s", name, exc)
    try:
        loaded = await load_and_cache_marketplace(source)
    except Exception as exc:
        raise RuntimeError(f'Failed to load marketplace "{name}" from source ({source["source"]}): {exc}') from exc
    config[name] = {**entry, "lastUpdated": _now_iso()}
    await save_known_marketplaces_config(config)
    return loaded.marketplace


async def get_marketplace(name: str) -> PluginMarketplace:
    """
    `getMarketplace` (memoized per name): the disk cache, else a fetch from the source (which
    updates `lastUpdated`). `refresh_marketplace` / `clear_marketplaces_cache` forget the memo.
    """
    task = _marketplace_cache.get(name)
    if task is None or (not task.done() and task.get_loop() is not asyncio.get_running_loop()):
        task = asyncio.ensure_future(_get_marketplace_uncached(name))
        _marketplace_cache[name] = task
    # Shielded: one caller's cancellation must not cancel the load the others share.
    return await asyncio.shield(task)


def _find_plugin(marketplace: PluginMarketplace, plugin_name: str) -> PluginMarketplaceEntry | None:
    for plugin in marketplace["plugins"]:
        if plugin["name"] == plugin_name:
            return plugin
    return None


async def get_plugin_by_id_cache_only(plugin_id: str) -> PluginByIdResult | None:
    """`getPluginByIdCacheOnly`: no network; `None` when anything is missing."""
    parsed = parse_plugin_identifier(plugin_id)
    if not parsed.name or not parsed.marketplace:
        return None
    try:
        marketplace_config = _read_raw_known_marketplaces().get(parsed.marketplace)
        if not marketplace_config:
            return None
        marketplace = await get_marketplace_cache_only(parsed.marketplace)
        if marketplace is None:
            return None
        plugin = _find_plugin(marketplace, parsed.name)
        if plugin is None:
            return None
        return PluginByIdResult(entry=plugin, marketplace_install_location=marketplace_config["installLocation"])
    except Exception:  # noqa: BLE001 - cache-only lookups never raise
        return None


async def get_plugin_by_id(plugin_id: str) -> PluginByIdResult | None:
    """`getPluginById`: the cache first, then the source on a miss; `None` when not found."""
    cached = await get_plugin_by_id_cache_only(plugin_id)
    if cached is not None:
        return cached
    parsed = parse_plugin_identifier(plugin_id)
    if not parsed.name or not parsed.marketplace:
        return None
    try:
        config = await load_known_marketplaces_config()
        marketplace_config = config.get(parsed.marketplace)
        if not marketplace_config:
            return None
        marketplace = await get_marketplace(parsed.marketplace)
        plugin = _find_plugin(marketplace, parsed.name)
        if plugin is None:
            return None
        return PluginByIdResult(entry=plugin, marketplace_install_location=marketplace_config["installLocation"])
    except Exception as exc:  # noqa: BLE001 - "not found" covers every failure here, as upstream
        logger.debug("Could not find plugin %s: %s", plugin_id, exc)
        return None


# ── refresh ───────────────────────────────────────────────────────────────────

async def refresh_all_marketplaces() -> None:
    """
    `refreshAllMarketplaces`: re-fetch every marketplace except seed-managed and settings-sourced
    ones; a failure is logged and the rest continue.
    """
    config: dict[str, Any] = dict(await load_known_marketplaces_config())
    for name, entry in list(config.items()):
        if seed_dir_for(entry["installLocation"]):
            logger.debug("Skipping seed-managed marketplace '%s' in bulk refresh", name)
            continue
        if _source(entry)["source"] == "settings":
            continue
        try:
            loaded = await load_and_cache_marketplace(_source(entry))
            config[name] = {**entry, "lastUpdated": _now_iso(), "installLocation": loaded.cache_path}
        except Exception as exc:  # noqa: BLE001 - one marketplace's failure must not stop the others
            logger.error("Failed to refresh marketplace %s: %s", name, exc)
    await save_known_marketplaces_config(config)


async def refresh_marketplace(
    name: str,
    on_progress: MarketplaceProgressCallback | None = None,
    *,
    disable_credential_helper: bool = False,
) -> None:
    """`refreshMarketplace`: an in-place update from the source, then `lastUpdated`."""
    config: dict[str, Any] = dict(await load_known_marketplaces_config())
    entry = config.get(name)
    if not entry:
        raise ValueError(f"Marketplace '{name}' not found. Available marketplaces: {', '.join(config)}")
    _marketplace_cache.pop(name, None)
    source = _source(entry)
    if source["source"] == "settings":
        logger.debug("Skipping refresh for settings-sourced marketplace '%s' — no upstream", name)
        return
    try:
        install_location = entry["installLocation"]
        seed_dir = seed_dir_for(install_location)
        if seed_dir:
            raise ValueError(
                f"Marketplace '{name}' is seed-managed ({seed_dir}) and its content is controlled by the seed "
                "image. To update: ask your admin to update the seed."
            )
        if not is_local_marketplace_source(source):
            cache_dir = os.path.abspath(get_marketplaces_cache_dir())
            resolved = os.path.abspath(install_location)
            if resolved != cache_dir and not resolved.startswith(cache_dir + os.sep):
                raise ValueError(
                    f"Marketplace '{name}' has a corrupted installLocation ({install_location}) — expected a path "
                    f"inside {cache_dir}. This can happen after cross-platform path writes or manual edits to "
                    f'known_marketplaces.json. Run: claude plugin marketplace remove "{name}" and re-add it.'
                )
        kind = source["source"]
        if kind in ("github", "git"):
            if kind == "github":
                repo = source["repo"]
                ssh_url = f"git@github.com:{repo}.git"
                https_url = f"https://github.com/{repo}.git"
                if is_env_truthy(os.environ.get(REMOTE_ENV)):
                    await cache_marketplace_from_git(
                        https_url, install_location, source.get("ref"), source.get("sparsePaths"), on_progress,
                        disable_credential_helper=disable_credential_helper,
                    )
                else:
                    ssh_configured = await is_github_ssh_likely_configured()
                    primary = ssh_url if ssh_configured else https_url
                    fallback = https_url if ssh_configured else ssh_url
                    try:
                        await cache_marketplace_from_git(
                            primary, install_location, source.get("ref"), source.get("sparsePaths"), on_progress,
                            disable_credential_helper=disable_credential_helper,
                        )
                    except Exception:  # noqa: BLE001 - the other protocol is tried before giving up
                        logger.info(
                            "Marketplace refresh failed with %s for %s, falling back to %s",
                            "SSH" if ssh_configured else "HTTPS", repo, "HTTPS" if ssh_configured else "SSH",
                        )
                        await cache_marketplace_from_git(
                            fallback, install_location, source.get("ref"), source.get("sparsePaths"), on_progress,
                            disable_credential_helper=disable_credential_helper,
                        )
            else:
                await cache_marketplace_from_git(
                    source["url"], install_location, source.get("ref"), source.get("sparsePaths"), on_progress,
                    disable_credential_helper=disable_credential_helper,
                )
            try:
                await read_cached_marketplace(install_location)
            except Exception as exc:
                display = source["repo"] if kind == "github" else redact_url_credentials(source["url"])
                reason = (
                    'We\'ve deprecated "claude-code-plugins" in favor of "claude-plugins-official".'
                    if name == "claude-code-plugins"
                    else "This marketplace may have been deprecated or moved to a new location."
                )
                raise RuntimeError(
                    "The marketplace.json file is no longer present in this repository.\n\n"
                    f"{reason}\nSource: {display}\n\n"
                    f'You can remove this marketplace with: claude plugin marketplace remove "{name}"'
                ) from exc
        elif kind == "url":
            await cache_marketplace_from_url(source["url"], install_location, source.get("headers"), on_progress)
        elif is_local_marketplace_source(source):
            safe_call_progress(on_progress, "Validating local marketplace")
            await read_cached_marketplace(install_location)
        else:
            raise RuntimeError("Unsupported marketplace source type for refresh")
        config[name] = {**entry, "lastUpdated": _now_iso()}
        await save_known_marketplaces_config(config)
        logger.debug("Successfully refreshed marketplace: %s", name)
    except Exception as exc:
        logger.error("Failed to refresh marketplace %s: %s", name, exc)
        raise RuntimeError(f"Failed to refresh marketplace '{name}': {exc}") from exc


async def set_marketplace_auto_update(name: str, auto_update: bool, locations: SettingsLocations) -> None:
    """`setMarketplaceAutoUpdate`: the state entry, and the declaring settings source's intent."""
    config: dict[str, Any] = dict(await load_known_marketplaces_config())
    entry = config.get(name)
    if not entry:
        raise ValueError(f"Marketplace '{name}' not found. Available marketplaces: {', '.join(config)}")
    seed_dir = seed_dir_for(entry["installLocation"])
    if seed_dir:
        raise ValueError(
            f"Marketplace '{name}' is seed-managed ({seed_dir}) and auto-update is always disabled for seed "
            "content. To update: ask your admin to update the seed."
        )
    if entry.get("autoUpdate") == auto_update:
        return
    config[name] = {**entry, "autoUpdate": auto_update}
    await save_known_marketplaces_config(config)
    declaring = get_marketplace_declaring_source(name, locations)
    if declaring is not None:
        settings = get_settings_for_source(declaring, locations) or {}
        declared = (settings.get("extraKnownMarketplaces") or {}).get(name)
        if declared:
            save_marketplace_to_settings(
                name, {"source": declared["source"], "autoUpdate": auto_update}, locations, declaring
            )
    logger.debug("Set autoUpdate=%s for marketplace: %s", auto_update, name)


__all__ = [
    "DEFAULT_PLUGIN_GIT_TIMEOUT_MS",
    "GIT_NO_PROMPT_ENV",
    "KNOWN_MARKETPLACES_FILENAME",
    "MARKETPLACES_DIRNAME",
    "AddMarketplaceResult",
    "DeclaredMarketplace",
    "HttpResponse",
    "KnownMarketplacesConfig",
    "LoadedPluginMarketplace",
    "MarketplaceProgressCallback",
    "PluginByIdResult",
    "add_marketplace_source",
    "cache_marketplace_from_git",
    "cache_marketplace_from_url",
    "clear_marketplaces_cache",
    "enhance_git_pull_error_messages",
    "extract_ssh_host",
    "get_cache_path_for_source",
    "get_declared_marketplaces",
    "get_known_marketplaces_file",
    "get_marketplace",
    "get_marketplace_cache_only",
    "get_marketplace_declaring_source",
    "get_marketplaces_cache_dir",
    "get_plugin_by_id",
    "get_plugin_by_id_cache_only",
    "get_plugin_git_timeout_ms",
    "git_clone",
    "git_pull",
    "http_get",
    "is_authentication_error",
    "is_github_ssh_likely_configured",
    "load_and_cache_marketplace",
    "load_known_marketplaces_config",
    "load_known_marketplaces_config_safe",
    "parse_file_with_schema",
    "read_cached_marketplace",
    "reconcile_sparse_checkout",
    "redact_headers",
    "redact_url_credentials",
    "refresh_all_marketplaces",
    "refresh_marketplace",
    "register_seed_marketplaces",
    "remove_marketplace_source",
    "safe_call_progress",
    "save_known_marketplaces_config",
    "save_marketplace_to_settings",
    "seed_dir_for",
    "set_marketplace_auto_update",
]
