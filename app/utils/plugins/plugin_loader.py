"""
The plugin loader: cache paths, seed probing, copying and fetching plugin sources, the manifest
read (the install/cache half), and discovering, loading and merging every plugin (the load half).

Ported from `reference/claude-code/src/utils/plugins/pluginLoader.ts` -> `getPluginCachePath`,
`getVersionedCachePathIn`, `getVersionedCachePath`, `getVersionedZipCachePath`, `probeSeedCache`,
`probeSeedCacheAnyVersion`, `getLegacyCachePath`, `resolvePluginPath`, `copyDir`,
`copyPluginToVersionedCache`, `validateGitUrl`, `installFromNpm`, `gitClone`, `installFromGit`,
`installFromGitHub`, `resolveGitSubdirUrl`, `installFromGitSubdir`, `installFromLocal`,
`generateTemporaryCacheNameForPlugin`, `cachePlugin`, `loadPluginManifest`; and `loadPluginHooks`,
`validatePluginPaths`, `createPluginFromPath`, `PluginSettingsSchema` / `parsePluginSettings`,
`loadPluginSettings`, `mergeHooksSettings`, `loadPluginsFromMarketplaces`,
`loadPluginFromMarketplaceEntryCacheOnly`, `loadPluginFromMarketplaceEntry`,
`finishLoadingPluginFromPath`, `loadSessionOnlyPlugins`, `mergePluginSources`, `loadAllPlugins`,
`loadAllPluginsCacheOnly`, `assemblePluginLoadResult`, `clearPluginCache`, `mergePluginSettings`,
`cachePluginSettings`.

Product substitutions: `CLAUDE_CODE_REMOTE` is `TDDAGENTS_REMOTE` (HTTPS GitHub URLs instead of
SSH), `CLAUDE_CODE_SYNC_PLUGIN_INSTALL` is `TDDAGENTS_SYNC_PLUGIN_INSTALL`. The plugin package
folder is upstream's `.claude-plugin/` renamed `.tddagents-plugin/` (recorded divergence, see
`PLUGIN_PACKAGE_DIR` in `plugin_directories.py`).

The load half reads the run's settings: `load_all_plugins(locations)` and
`load_all_plugins_cache_only(locations)` take the run's `SettingsLocations`
(`app/cli/print.py` `run_settings_locations`) where upstream reads its process-wide settings,
and each is memoized per `locations` (upstream's argument-less lodash `memoize`) until
`clear_plugin_cache`. The `--plugin-dir` directories are the process-wide
`app/bootstrap/state.py` `get_inline_plugins`, as upstream. Upstream's `path.join` normalizes,
so the load half joins with `_join` (`os.path.normpath`): a `./x` entry path reads `<dir>/x`.

Divergences: the fetch telemetry (`logPluginFetch`, `classifyFetchError`) is not ported. Git and
npm run in the process directory (upstream's `useCwd`), see `app/utils/exec_file_no_throw.py`.
The path checks run one after the other (upstream runs them in parallel, then reads the results
in order, so the errors and their order are the same). `clearPluginCache`'s `resetSettingsCache`
has nothing to reset: settings are read from disk on every load here.
"""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import os
import random
import re
import shutil
import string
import time
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from app.bootstrap.state import get_inline_plugins
from app.plugins.builtin_plugins import BUILTIN_MARKETPLACE_NAME, get_builtin_plugins
from app.types.plugin import (
    LoadedPlugin,
    PathNotFoundError,
    PluginComponent,
    PluginError,
    PluginLoadResult,
)
from app.utils.claudemd import is_env_truthy
from app.utils.exec_file_no_throw import exec_file_no_throw, exec_file_no_throw_with_cwd
from app.utils.fs_operations import rm
from app.utils.git.git import git_exe
from app.utils.plugins.add_dir_plugin_settings import get_add_dir_enabled_plugins
from app.utils.plugins.dependency_resolver import verify_and_demote
from app.utils.plugins.git_availability import check_git_available
from app.utils.plugins.managed_plugins import get_managed_plugin_names
from app.utils.plugins.marketplace_helpers import (
    format_source_for_display,
    get_blocked_marketplaces,
    get_strict_known_marketplaces,
    is_source_allowed_by_policy,
    is_source_in_blocklist,
)
from app.utils.plugins.marketplace_manager import (
    PluginByIdResult,
    get_marketplace_cache_only,
    get_plugin_by_id_cache_only,
    load_known_marketplaces_config_safe,
)
from app.utils.plugins.plugin_directories import (
    PLUGIN_PACKAGE_DIR,
    get_plugin_seed_dirs,
    get_plugins_directory,
)
from app.utils.plugins.plugin_identifier import parse_plugin_identifier
from app.utils.plugins.plugin_versioning import calculate_plugin_version
from app.utils.plugins.schemas import (
    CommandMetadata,
    PluginHooksSchema,
    PluginIdSchema,
    PluginManifest,
    PluginManifestSchema,
    PluginMarketplaceEntry,
    is_url,
    parse,
    safe_parse,
)
from app.utils.plugins.zip_cache import (
    convert_directory_to_zip_in_place,
    extract_zip_to_directory,
    get_session_plugin_cache_path,
    is_plugin_zip_cache_enabled,
)
from app.utils.settings.settings import SettingsLocations, get_initial_settings
from app.utils.settings.settings_cache import clear_plugin_settings_base, set_plugin_settings_base

logger = logging.getLogger("TDDOrchestrator.Plugins")

REMOTE_ENV = "TDDAGENTS_REMOTE"

_UNSAFE_SEGMENT = re.compile(r"[^a-zA-Z0-9\-_]")
_UNSAFE_VERSION = re.compile(r"[^a-zA-Z0-9\-_.]")
_GIT_SSH = re.compile(r"^git@[a-zA-Z0-9.-]+:")
_GITHUB_REPO = re.compile(r"^[a-zA-Z0-9-_.]+/[a-zA-Z0-9-_.]+\Z", re.ASCII)
_BASE36 = string.digits + string.ascii_lowercase


def _path_exists(path: str) -> bool:
    """`pathExists`."""
    return os.path.exists(path)


def get_plugin_cache_path() -> str:
    """`getPluginCachePath`: `<plugins dir>/cache`."""
    return os.path.join(get_plugins_directory(), "cache")


def get_versioned_cache_path_in(base_dir: str, plugin_id: str, version: str) -> str:
    """`getVersionedCachePathIn`: `<base>/cache/<marketplace>/<plugin>/<version>`, each part sanitized."""
    parsed = parse_plugin_identifier(plugin_id)
    marketplace = _UNSAFE_SEGMENT.sub("-", parsed.marketplace or "unknown")
    plugin = _UNSAFE_SEGMENT.sub("-", parsed.name or plugin_id)
    safe_version = _UNSAFE_VERSION.sub("-", version)
    return os.path.join(base_dir, "cache", marketplace, plugin, safe_version)


def get_versioned_cache_path(plugin_id: str, version: str) -> str:
    """`getVersionedCachePath`: under the primary plugins directory."""
    return get_versioned_cache_path_in(get_plugins_directory(), plugin_id, version)


def get_versioned_zip_cache_path(plugin_id: str, version: str) -> str:
    """`getVersionedZipCachePath`."""
    return f"{get_versioned_cache_path(plugin_id, version)}.zip"


def _list_dir(path: str) -> list[str]:
    return os.listdir(path)


async def probe_seed_cache(plugin_id: str, version: str) -> str | None:
    """`probeSeedCache`: the first seed holding a populated directory at this version."""
    for seed_dir in get_plugin_seed_dirs():
        seed_path = get_versioned_cache_path_in(seed_dir, plugin_id, version)
        try:
            if _list_dir(seed_path):
                return seed_path
        except OSError:
            continue
    return None


async def probe_seed_cache_any_version(plugin_id: str) -> str | None:
    """`probeSeedCacheAnyVersion`: a seed whose plugin directory holds exactly one populated version."""
    for seed_dir in get_plugin_seed_dirs():
        plugin_dir = os.path.dirname(get_versioned_cache_path_in(seed_dir, plugin_id, "_"))
        try:
            versions = _list_dir(plugin_dir)
            if len(versions) != 1:
                continue
            version_dir = os.path.join(plugin_dir, versions[0])
            if _list_dir(version_dir):
                return version_dir
        except OSError:
            continue
    return None


def get_legacy_cache_path(plugin_name: str) -> str:
    """`getLegacyCachePath`: `<cache>/<plugin-name>` (pre-versioning layout)."""
    return os.path.join(get_plugin_cache_path(), _UNSAFE_SEGMENT.sub("-", plugin_name))


async def resolve_plugin_path(plugin_id: str, version: str | None = None) -> str:
    """`resolvePluginPath`: the versioned path if present, else the legacy one, else the versioned (new install)."""
    if version:
        versioned = get_versioned_cache_path(plugin_id, version)
        if _path_exists(versioned):
            return versioned
    plugin_name = parse_plugin_identifier(plugin_id).name or plugin_id
    legacy = get_legacy_cache_path(plugin_name)
    if _path_exists(legacy):
        return legacy
    return get_versioned_cache_path(plugin_id, version) if version else legacy


async def copy_dir(src: str, dest: str) -> None:
    """
    `copyDir`: a recursive copy. A symlink pointing inside the source tree becomes a relative
    link to the same place in the copy; one pointing outside becomes an absolute link to the
    resolved target; a broken one is copied as-is.
    """
    os.makedirs(dest, exist_ok=True)
    with os.scandir(src) as iterator:
        entries = list(iterator)
    for entry in entries:
        src_path = os.path.join(src, entry.name)
        dest_path = os.path.join(dest, entry.name)
        if entry.is_symlink():
            link_target = os.readlink(src_path)
            if not os.path.exists(src_path):
                os.symlink(link_target, dest_path)
                continue
            resolved_target = os.path.realpath(src_path)
            try:
                resolved_src = os.path.realpath(src)
            except OSError:
                resolved_src = src
            prefix = resolved_src if resolved_src.endswith(os.sep) else resolved_src + os.sep
            if resolved_target.startswith(prefix) or resolved_target == resolved_src:
                target_relative = os.path.relpath(resolved_target, resolved_src)
                dest_target = os.path.join(dest, target_relative)
                os.symlink(os.path.relpath(dest_target, os.path.dirname(dest_path)), dest_path)
            else:
                os.symlink(resolved_target, dest_path)
        elif entry.is_dir(follow_symlinks=False):
            await copy_dir(src_path, dest_path)
        elif entry.is_file(follow_symlinks=False):
            shutil.copy(src_path, dest_path)


async def copy_plugin_to_versioned_cache(
    source_path: str,
    plugin_id: str,
    version: str,
    entry: Mapping[str, Any] | None = None,
    marketplace_dir: str | None = None,
) -> str:
    """
    `copyPluginToVersionedCache`: an existing non-empty cache (or zip) is returned as is, then a
    seed hit in place; otherwise a local entry's `source` (inside `marketplace_dir`) or
    `source_path` is copied, without `.git`. An empty result raises.
    """
    # Breaks the import cycle with plugin_installation_helpers (it imports this module).
    from app.utils.plugins.plugin_installation_helpers import validate_path_within_base

    zip_mode = is_plugin_zip_cache_enabled()
    cache_path = get_versioned_cache_path(plugin_id, version)
    zip_path = get_versioned_zip_cache_path(plugin_id, version)
    if zip_mode:
        if _path_exists(zip_path):
            logger.debug("Plugin %s version %s already cached at %s", plugin_id, version, zip_path)
            return zip_path
    elif _path_exists(cache_path):
        if _list_dir(cache_path):
            logger.debug("Plugin %s version %s already cached at %s", plugin_id, version, cache_path)
            return cache_path
        logger.debug("Removing empty cache directory for %s at %s", plugin_id, cache_path)
        os.rmdir(cache_path)

    seed_path = await probe_seed_cache(plugin_id, version)
    if seed_path:
        logger.debug("Using seed cache for %s@%s at %s", plugin_id, version, seed_path)
        return seed_path

    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    entry_source = entry.get("source") if entry is not None else None
    if isinstance(entry_source, str) and marketplace_dir:
        source_dir = validate_path_within_base(marketplace_dir, entry_source)
        logger.debug("Copying source directory %s for plugin %s", entry_source, plugin_id)
        try:
            await copy_dir(source_dir, cache_path)
        except FileNotFoundError as exc:
            if exc.filename == source_dir:
                raise RuntimeError(
                    f"Plugin source directory not found: {source_dir} (from entry.source: {entry_source})"
                ) from exc
            raise
    else:
        logger.debug("Copying plugin %s to versioned cache (fallback to full copy)", plugin_id)
        await copy_dir(source_path, cache_path)

    rm(os.path.join(cache_path, ".git"), recursive=True, force=True)
    if not _list_dir(cache_path):
        raise RuntimeError(
            f"Failed to copy plugin {plugin_id} to versioned cache: destination is empty after copy"
        )
    if zip_mode:
        await convert_directory_to_zip_in_place(cache_path, zip_path)
        logger.debug("Successfully cached plugin %s as ZIP at %s", plugin_id, zip_path)
        return zip_path
    logger.debug("Successfully cached plugin %s at %s", plugin_id, cache_path)
    return cache_path


def validate_git_url(url: str) -> str:
    """
    `validateGitUrl`: an https, http or file URL, or SSH (`git@host:`). Upstream's protocol
    error is thrown inside its own `try`, so every rejection reads `Invalid git URL: <url>`.
    """
    # `new URL()` ignores surrounding whitespace; the URL itself is returned unchanged.
    match = re.match(r"^([A-Za-z][A-Za-z0-9+.\-]*):", url.strip())
    if is_url(url.strip()) and match is not None and match.group(1).lower() in ("https", "http", "file"):
        return url
    if _GIT_SSH.match(url):
        return url
    raise ValueError(f"Invalid git URL: {url}")


async def install_from_npm(
    package_name: str,
    target_path: str,
    *,
    registry: str | None = None,
    version: str | None = None,
) -> None:
    """`installFromNpm`: install once into `<plugins dir>/npm-cache`, then copy the package out."""
    npm_cache_path = os.path.join(get_plugins_directory(), "npm-cache")
    os.makedirs(npm_cache_path, exist_ok=True)
    package_spec = f"{package_name}@{version}" if version else package_name
    package_path = os.path.join(npm_cache_path, "node_modules", package_name)
    if not _path_exists(package_path):
        logger.debug("Installing npm package %s to cache", package_spec)
        args = ["install", package_spec, "--prefix", npm_cache_path]
        if registry:
            args.extend(["--registry", registry])
        result = await exec_file_no_throw_with_cwd("npm", args)
        if result.code != 0:
            raise RuntimeError(f"Failed to install npm package: {result.stderr}")
    await copy_dir(package_path, target_path)
    logger.debug("Copied npm package %s from cache to %s", package_name, target_path)


async def git_clone(git_url: str, target_path: str, ref: str | None = None, sha: str | None = None) -> None:
    """
    `gitClone`: a shallow clone with submodules (`--branch` for a ref); with a SHA, a
    `--no-checkout` clone, a shallow fetch of the SHA (falling back to `--unshallow`), then
    a checkout of it.
    """
    args = ["clone", "--depth", "1", "--recurse-submodules", "--shallow-submodules"]
    if ref:
        args.extend(["--branch", ref])
    if sha:
        args.append("--no-checkout")
    args.extend([git_url, target_path])
    clone = await exec_file_no_throw(git_exe(), args)
    if clone.code != 0:
        raise RuntimeError(f"Failed to clone repository: {clone.stderr}")
    if sha:
        shallow = await exec_file_no_throw_with_cwd(
            git_exe(), ["fetch", "--depth", "1", "origin", sha], cwd=target_path
        )
        if shallow.code != 0:
            logger.debug("Shallow fetch of SHA %s failed, falling back to unshallow fetch", sha)
            unshallow = await exec_file_no_throw_with_cwd(git_exe(), ["fetch", "--unshallow"], cwd=target_path)
            if unshallow.code != 0:
                raise RuntimeError(f"Failed to fetch commit {sha}: {unshallow.stderr}")
        checkout = await exec_file_no_throw_with_cwd(git_exe(), ["checkout", sha], cwd=target_path)
        if checkout.code != 0:
            raise RuntimeError(f"Failed to checkout commit {sha}: {checkout.stderr}")


async def install_from_git(git_url: str, target_path: str, ref: str | None = None, sha: str | None = None) -> None:
    """`installFromGit`."""
    safe_url = validate_git_url(git_url)
    await git_clone(safe_url, target_path, ref, sha)
    ref_message = f" (ref: {ref})" if ref else ""
    logger.debug("Cloned repository from %s%s to %s", safe_url, ref_message, target_path)


def _github_url(repo: str) -> str:
    if is_env_truthy(os.environ.get(REMOTE_ENV)):
        return f"https://github.com/{repo}.git"
    return f"git@github.com:{repo}.git"


async def install_from_github(repo: str, target_path: str, ref: str | None = None, sha: str | None = None) -> None:
    """`installFromGitHub`: `owner/repo` over SSH (HTTPS on a remote run)."""
    if _GITHUB_REPO.match(repo) is None:
        raise ValueError(f"Invalid GitHub repository format: {repo}. Expected format: owner/repo")
    await install_from_git(_github_url(repo), target_path, ref, sha)


def resolve_git_subdir_url(url: str) -> str:
    """`resolveGitSubdirUrl`: GitHub shorthand, or any URL `validate_git_url` accepts."""
    if _GITHUB_REPO.match(url):
        return _github_url(url)
    return validate_git_url(url)


async def install_from_git_subdir(
    url: str,
    target_path: str,
    subdir_path: str,
    ref: str | None = None,
    sha: str | None = None,
) -> str | None:
    """
    `installFromGitSubdir`: a partial, sparse clone into `<target>.clone`, the subdirectory
    moved to `target_path`, the clone discarded. Returns the resolved commit SHA.
    """
    # Breaks the import cycle with plugin_installation_helpers (it imports this module).
    from app.utils.plugins.plugin_installation_helpers import validate_path_within_base

    if not await check_git_available():
        raise RuntimeError(
            "git-subdir plugin source requires git to be installed and on PATH. "
            "Install git (version 2.25 or later for sparse-checkout cone mode) and try again."
        )
    git_url = resolve_git_subdir_url(url)
    clone_dir = f"{target_path}.clone"
    clone_args = ["clone", "--depth", "1", "--filter=tree:0", "--no-checkout"]
    if ref:
        clone_args.extend(["--branch", ref])
    clone_args.extend([git_url, clone_dir])
    clone = await exec_file_no_throw(git_exe(), clone_args)
    if clone.code != 0:
        raise RuntimeError(f"Failed to clone repository for git-subdir source: {clone.stderr}")
    try:
        sparse = await exec_file_no_throw_with_cwd(
            git_exe(), ["sparse-checkout", "set", "--cone", "--", subdir_path], cwd=clone_dir
        )
        if sparse.code != 0:
            raise RuntimeError(
                f"git sparse-checkout set failed (git >= 2.25 required for cone mode): {sparse.stderr}"
            )
        resolved_sha: str | None = None
        if sha:
            fetched = await exec_file_no_throw_with_cwd(
                git_exe(), ["fetch", "--depth", "1", "origin", sha], cwd=clone_dir
            )
            if fetched.code != 0:
                logger.debug("Shallow fetch of SHA %s failed for git-subdir, falling back to unshallow fetch", sha)
                unshallow = await exec_file_no_throw_with_cwd(git_exe(), ["fetch", "--unshallow"], cwd=clone_dir)
                if unshallow.code != 0:
                    raise RuntimeError(f"Failed to fetch commit {sha}: {unshallow.stderr}")
            checkout = await exec_file_no_throw_with_cwd(git_exe(), ["checkout", sha], cwd=clone_dir)
            if checkout.code != 0:
                raise RuntimeError(f"Failed to checkout commit {sha}: {checkout.stderr}")
            resolved_sha = sha
        else:
            checkout, rev_parse = await asyncio.gather(
                exec_file_no_throw_with_cwd(git_exe(), ["checkout", "HEAD"], cwd=clone_dir),
                exec_file_no_throw_with_cwd(git_exe(), ["rev-parse", "HEAD"], cwd=clone_dir),
            )
            if checkout.code != 0:
                raise RuntimeError(f"git checkout after sparse-checkout failed: {checkout.stderr}")
            if rev_parse.code == 0:
                resolved_sha = rev_parse.stdout.strip()
        resolved_subdir = validate_path_within_base(clone_dir, subdir_path)
        try:
            os.rename(resolved_subdir, target_path)
        except FileNotFoundError as exc:
            ref_note = f" (ref: {ref})" if ref else ""
            raise RuntimeError(
                f"Subdirectory '{subdir_path}' not found in repository {git_url}{ref_note}. "
                "Check that the path is correct and exists at the specified ref/sha."
            ) from exc
        logger.debug(
            "Extracted subdir %s from %s%s%s to %s",
            subdir_path,
            git_url,
            f" ref={ref}" if ref else "",
            f" sha={resolved_sha}" if resolved_sha else "",
            target_path,
        )
        return resolved_sha
    finally:
        rm(clone_dir, recursive=True, force=True)


async def install_from_local(source_path: str, target_path: str) -> None:
    """`installFromLocal`: copy the directory, without `.git`."""
    if not _path_exists(source_path):
        # A plain Error upstream (no errno code), so nothing above mistakes it for ENOENT.
        raise RuntimeError(f"Source path does not exist: {source_path}")
    await copy_dir(source_path, target_path)
    rm(os.path.join(target_path, ".git"), recursive=True, force=True)


def _base36(number: float) -> str:
    """`Math.random().toString(36).substring(2, 8)`: six base-36 digits."""
    digits = []
    fraction = number
    for _ in range(6):
        fraction *= 36
        digit = int(fraction)
        digits.append(_BASE36[digit])
        fraction -= digit
    return "".join(digits)


def generate_temporary_cache_name_for_plugin(source: Any) -> str:
    """`generateTemporaryCacheNameForPlugin`: `temp_<kind>_<ms>_<random>`."""
    if isinstance(source, str):
        prefix = "local"
    else:
        prefix = {
            "npm": "npm",
            "pip": "pip",
            "github": "github",
            "url": "git",
            "git-subdir": "subdir",
        }.get(str(source.get("source")), "unknown")
    return f"temp_{prefix}_{int(time.time() * 1000)}_{_base36(random.random())}"


@dataclass(frozen=True)
class CachedPlugin:
    """`cachePlugin`'s result: `{path, manifest, gitCommitSha?}`."""

    path: str
    manifest: PluginManifest
    git_commit_sha: str | None = None


def _read_manifest_for_cache(path: str) -> PluginManifest:
    try:
        parsed = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError) as exc:
        logger.error("Failed to parse manifest at %s: %s", path, exc)
        raise RuntimeError(f"Plugin has a corrupt manifest file at {path}. JSON parse error: {exc}") from exc
    result = safe_parse(PluginManifestSchema, parsed)
    if result.success and result.data is not None:
        return result.data
    errors = ", ".join(f"{i.path_string()}: {i.message}" for i in result.issues)
    logger.error("Invalid manifest at %s: %s", path, errors)
    raise RuntimeError(f"Plugin has an invalid manifest file at {path}. Validation errors: {errors}")


async def cache_plugin(source: Any, *, manifest: Mapping[str, Any] | None = None) -> CachedPlugin:
    """
    `cachePlugin`: fetch `source` into a temporary cache directory, read its manifest
    (`<PLUGIN_PACKAGE_DIR>/plugin.json`, then `plugin.json`, else `manifest`, else a minimal one),
    then move it to `<cache>/<sanitized manifest name>`.
    """
    cache_path = get_plugin_cache_path()
    os.makedirs(cache_path, exist_ok=True)
    temp_name = generate_temporary_cache_name_for_plugin(source)
    temp_path = os.path.join(cache_path, temp_name)
    git_commit_sha: str | None = None
    try:
        logger.debug("Caching plugin from source: %s to temporary path %s", json.dumps(source), temp_path)
        if isinstance(source, str):
            await install_from_local(source, temp_path)
        else:
            kind = source.get("source")
            if kind == "npm":
                await install_from_npm(
                    source["package"], temp_path, registry=source.get("registry"), version=source.get("version")
                )
            elif kind == "github":
                await install_from_github(source["repo"], temp_path, source.get("ref"), source.get("sha"))
            elif kind == "url":
                await install_from_git(source["url"], temp_path, source.get("ref"), source.get("sha"))
            elif kind == "git-subdir":
                git_commit_sha = await install_from_git_subdir(
                    source["url"], temp_path, source["path"], source.get("ref"), source.get("sha")
                )
            elif kind == "pip":
                raise RuntimeError("Python package plugins are not yet supported")
            else:
                raise RuntimeError("Unsupported plugin source type")
    except BaseException:
        if _path_exists(temp_path):
            logger.debug("Cleaning up failed installation at %s", temp_path)
            try:
                rm(temp_path, recursive=True, force=True)
            except OSError as cleanup_error:
                logger.error("Failed to clean up installation: %s", cleanup_error)
        raise

    manifest_path = os.path.join(temp_path, PLUGIN_PACKAGE_DIR, "plugin.json")
    legacy_manifest_path = os.path.join(temp_path, "plugin.json")
    loaded: PluginManifest
    if _path_exists(manifest_path):
        loaded = _read_manifest_for_cache(manifest_path)
    elif _path_exists(legacy_manifest_path):
        loaded = _read_manifest_for_cache(legacy_manifest_path)
    elif manifest:
        loaded = cast(PluginManifest, dict(manifest))
    else:
        kind_label = source if isinstance(source, str) else source.get("source")
        loaded = {"name": temp_name, "description": f"Plugin cached from {kind_label}"}

    final_name = _UNSAFE_SEGMENT.sub("-", loaded["name"])
    final_path = os.path.join(cache_path, final_name)
    if _path_exists(final_path):
        logger.debug("Removing old cached version at %s", final_path)
        rm(final_path, recursive=True, force=True)
    os.rename(temp_path, final_path)
    logger.debug("Successfully cached plugin %s to %s", loaded["name"], final_path)
    return CachedPlugin(path=final_path, manifest=loaded, git_commit_sha=git_commit_sha or None)


async def load_plugin_manifest(manifest_path: str, plugin_name: str, source: str) -> PluginManifest:
    """
    `loadPluginManifest`: a missing file is a minimal manifest; corrupt JSON or a schema
    failure raises. Unknown keys are stripped.
    """
    if not _path_exists(manifest_path):
        return {"name": plugin_name, "description": f"Plugin from {source}"}
    try:
        parsed = json.loads(Path(manifest_path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError) as exc:
        logger.error(
            "Plugin %s has a corrupt manifest file at %s. Parse error: %s", plugin_name, manifest_path, exc
        )
        raise RuntimeError(
            f"Plugin {plugin_name} has a corrupt manifest file at {manifest_path}.\n\nJSON parse error: {exc}"
        ) from exc
    result = safe_parse(PluginManifestSchema, parsed)
    if result.success and result.data is not None:
        return result.data
    errors = ", ".join(
        f"{i.path_string()}: {i.message}" if i.path else i.message for i in result.issues
    )
    logger.error(
        "Plugin %s has an invalid manifest file at %s. Validation errors: %s", plugin_name, manifest_path, errors
    )
    raise RuntimeError(
        f"Plugin {plugin_name} has an invalid manifest file at {manifest_path}.\n\nValidation errors: {errors}"
    )


# ── the load half ─────────────────────────────────────────────────────────────

#: `isFsInaccessible`'s errno codes.
_FS_INACCESSIBLE = frozenset({errno.ENOENT, errno.EACCES, errno.EPERM, errno.ENOTDIR, errno.ELOOP})
#: `CLAUDE_CODE_SYNC_PLUGIN_INSTALL`: the cache-only loader runs the full one.
SYNC_PLUGIN_INSTALL_ENV = "TDDAGENTS_SYNC_PLUGIN_INSTALL"
_UNSAFE_EXTRACT_SEGMENT = re.compile(r"[^a-zA-Z0-9@\-_]")


def _join(*parts: str) -> str:
    """Node's `path.join`: joined and normalized (`./x` and `..` segments resolved lexically)."""
    return os.path.normpath(os.path.join(*parts))


def _read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.loads(handle.read())


async def load_plugin_hooks(hooks_config_path: str, plugin_name: str) -> dict[str, Any]:
    """`loadPluginHooks`: a `hooks.json` (`{description?, hooks}`) validated, its `hooks` returned; raises."""
    if not _path_exists(hooks_config_path):
        raise RuntimeError(
            f"Hooks file not found at {hooks_config_path} for plugin {plugin_name}. "
            "If the manifest declares hooks, the file must exist."
        )
    validated = parse(PluginHooksSchema, _read_json(hooks_config_path))
    return dict(validated["hooks"])


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else [value]


def _path_not_found(source: str, plugin: str, path: str, component: PluginComponent) -> PathNotFoundError:
    return {"type": "path-not-found", "source": source, "plugin": plugin, "path": path, "component": component}


async def validate_plugin_paths(
    rel_paths: list[str],
    plugin_path: str,
    plugin_name: str,
    source: str,
    component: PluginComponent,
    component_label: str,
    context_label: str,
    errors: list[PluginError],
) -> list[str]:
    """`validatePluginPaths`: the full paths that exist, in order; each missing one is a `path-not-found` error."""
    valid_paths: list[str] = []
    for rel_path in rel_paths:
        full_path = _join(plugin_path, rel_path)
        if _path_exists(full_path):
            valid_paths.append(full_path)
            continue
        logger.warning("%s path %s %s not found at %s for %s", component_label, rel_path, context_label, full_path,
                       plugin_name)
        logger.error("Plugin component file not found: %s for %s", full_path, plugin_name)
        errors.append(_path_not_found(source, plugin_name, full_path, component))
    return valid_paths


def _is_command_mapping(commands: Any) -> bool:
    """A `commands` object of `{name: {source|content, ...}}` (upstream checks the first value only)."""
    if not isinstance(commands, dict) or not commands:
        return False
    first = next(iter(commands.values()))
    return isinstance(first, dict) and bool(first) and ("source" in first or "content" in first)


@dataclass
class CreatedPlugin:
    """`createPluginFromPath`'s result: `{plugin, errors}`."""

    plugin: LoadedPlugin
    errors: list[PluginError]


def _realpath(path: str) -> str:
    try:
        return os.path.realpath(path, strict=True)
    except OSError:
        return path


async def create_plugin_from_path(
    plugin_path: str,
    source: str,
    enabled: bool,
    fallback_name: str,
    strict: bool = True,
) -> CreatedPlugin:
    """
    `createPluginFromPath`: the manifest (`<PLUGIN_PACKAGE_DIR>/plugin.json`, else a minimal
    one), the component directories and manifest paths, the merged hooks and the allowlisted
    settings. A missing component is an error in the result, never a failure; a corrupt or
    invalid manifest raises.
    """
    errors: list[PluginError] = []
    manifest_path = _join(plugin_path, PLUGIN_PACKAGE_DIR, "plugin.json")
    manifest = await load_plugin_manifest(manifest_path, fallback_name, source)
    name = manifest["name"]
    plugin = LoadedPlugin(name=name, manifest=manifest, path=plugin_path, source=source, repository=source,
                          enabled=enabled)

    commands_dir_exists = not manifest.get("commands") and _path_exists(_join(plugin_path, "commands"))
    agents_dir_exists = not manifest.get("agents") and _path_exists(_join(plugin_path, "agents"))
    skills_dir_exists = not manifest.get("skills") and _path_exists(_join(plugin_path, "skills"))
    output_styles_dir_exists = not manifest.get("outputStyles") and _path_exists(
        _join(plugin_path, "output-styles"))

    if commands_dir_exists:
        plugin.commands_path = _join(plugin_path, "commands")
    commands: Any = manifest.get("commands")
    if commands:
        if _is_command_mapping(commands):
            metadata_by_name: dict[str, CommandMetadata] = {}
            valid: list[str] = []
            for command_name, metadata in commands.items():
                if not metadata or not isinstance(metadata, dict):
                    continue
                if metadata.get("source"):
                    full_path = _join(plugin_path, metadata["source"])
                    if _path_exists(full_path):
                        valid.append(full_path)
                        metadata_by_name[command_name] = metadata
                    else:
                        logger.warning("Command %s path %s specified in manifest but not found at %s for %s",
                                       command_name, metadata["source"], full_path, name)
                        logger.error("Plugin component file not found: %s for %s", full_path, name)
                        errors.append(_path_not_found(source, name, full_path, "commands"))
                elif metadata.get("content"):
                    metadata_by_name[command_name] = metadata
            if valid:
                plugin.commands_paths = valid
            if metadata_by_name:
                plugin.commands_metadata = metadata_by_name
        else:
            valid = []
            for command_path in _as_list(commands):
                if not isinstance(command_path, str):
                    logger.error("Unexpected command format in manifest for %s", name)
                    continue
                full_path = _join(plugin_path, command_path)
                if _path_exists(full_path):
                    valid.append(full_path)
                else:
                    logger.warning("Command path %s specified in manifest but not found at %s for %s",
                                   command_path, full_path, name)
                    logger.error("Plugin component file not found: %s for %s", full_path, name)
                    errors.append(_path_not_found(source, name, full_path, "commands"))
            if valid:
                plugin.commands_paths = valid

    if agents_dir_exists:
        plugin.agents_path = _join(plugin_path, "agents")
    if manifest.get("agents"):
        valid = await validate_plugin_paths(_as_list(manifest["agents"]), plugin_path, name, source, "agents",
                                            "Agent", "specified in manifest but", errors)
        if valid:
            plugin.agents_paths = valid

    if skills_dir_exists:
        plugin.skills_path = _join(plugin_path, "skills")
    if manifest.get("skills"):
        valid = await validate_plugin_paths(_as_list(manifest["skills"]), plugin_path, name, source, "skills",
                                            "Skill", "specified in manifest but", errors)
        if valid:
            plugin.skills_paths = valid

    if output_styles_dir_exists:
        plugin.output_styles_path = _join(plugin_path, "output-styles")
    if manifest.get("outputStyles"):
        valid = await validate_plugin_paths(_as_list(manifest["outputStyles"]), plugin_path, name, source,
                                            "output-styles", "Output style", "specified in manifest but", errors)
        if valid:
            plugin.output_styles_paths = valid

    merged_hooks = await _load_all_hooks(plugin_path, manifest, source, strict, errors)
    if merged_hooks:
        plugin.hooks_config = merged_hooks

    plugin_settings = await _load_plugin_settings(plugin_path, manifest)
    if plugin_settings:
        plugin.settings = plugin_settings
    return CreatedPlugin(plugin=plugin, errors=errors)


async def _load_all_hooks(
    plugin_path: str, manifest: PluginManifest, source: str, strict: bool, errors: list[PluginError]
) -> dict[str, Any] | None:
    """Step 5 of `createPluginFromPath`: `hooks/hooks.json`, then each `manifest.hooks` file or inline object."""
    name = manifest["name"]
    merged: dict[str, Any] | None = None
    loaded_hook_paths: set[str] = set()
    standard_hooks_path = _join(plugin_path, "hooks", "hooks.json")
    if _path_exists(standard_hooks_path):
        try:
            merged = await load_plugin_hooks(standard_hooks_path, name)
            loaded_hook_paths.add(_realpath(standard_hooks_path))
            logger.debug("Loaded hooks from standard location for plugin %s: %s", name, standard_hooks_path)
        except Exception as exc:  # noqa: BLE001 - a broken hooks file is a plugin error, not a load failure
            message = _error_message(exc)
            logger.error("Failed to load hooks for %s: %s", name, message)
            errors.append({"type": "hook-load-failed", "source": source, "plugin": name,
                           "hookPath": standard_hooks_path, "reason": message})
    hooks_spec: Any = manifest.get("hooks")
    if not hooks_spec:
        return merged
    for hook_spec in _as_list(hooks_spec):
        if isinstance(hook_spec, str):
            hook_file_path = _join(plugin_path, hook_spec)
            if not _path_exists(hook_file_path):
                logger.error("Hooks file %s specified in manifest but not found at %s for %s",
                             hook_spec, hook_file_path, name)
                errors.append(_path_not_found(source, name, hook_file_path, "hooks"))
                continue
            normalized = _realpath(hook_file_path)
            if normalized in loaded_hook_paths:
                logger.debug("Skipping duplicate hooks file for plugin %s: %s (resolves to already-loaded file: %s)",
                             name, hook_spec, normalized)
                if strict:
                    message = (
                        f"Duplicate hooks file detected: {hook_spec} resolves to already-loaded file {normalized}. "
                        "The standard hooks/hooks.json is loaded automatically, so manifest.hooks should only "
                        "reference additional hook files."
                    )
                    logger.error("%s", message)
                    errors.append({"type": "hook-load-failed", "source": source, "plugin": name,
                                   "hookPath": hook_file_path, "reason": message})
                continue
            try:
                additional = await load_plugin_hooks(hook_file_path, name)
            except Exception as exc:  # noqa: BLE001 - a broken hooks file is a plugin error
                message = _error_message(exc)
                logger.error("Failed to load hooks from %s for %s: %s", hook_spec, name, message)
                errors.append({"type": "hook-load-failed", "source": source, "plugin": name,
                               "hookPath": hook_file_path, "reason": message})
                continue
            try:
                merged = merge_hooks_settings(merged, additional)
                loaded_hook_paths.add(normalized)
                logger.debug("Loaded and merged hooks from manifest for plugin %s: %s", name, hook_spec)
            except Exception as exc:  # noqa: BLE001 - upstream reports a merge failure as a hook error
                message = _error_message(exc)
                logger.error("Failed to merge hooks from %s for %s: %s", hook_spec, name, message)
                errors.append({"type": "hook-load-failed", "source": source, "plugin": name,
                               "hookPath": hook_file_path, "reason": f"Failed to merge: {message}"})
        elif isinstance(hook_spec, dict):
            merged = merge_hooks_settings(merged, hook_spec)
    return merged


def _error_message(exc: BaseException) -> str:
    """`errorMessage`: the exception's message."""
    return str(exc)


def _parse_plugin_settings(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    """
    `parsePluginSettings` (`PluginSettingsSchema`: `SettingsSchema.pick({agent}).strip()`): the
    allowlisted keys, `None` when invalid or empty.
    """
    data: dict[str, Any] = {}
    if "agent" in raw:
        agent = raw["agent"]
        if not isinstance(agent, str):
            return None
        data["agent"] = agent
    return data or None


async def _load_plugin_settings(plugin_path: str, manifest: PluginManifest) -> dict[str, Any] | None:
    """`loadPluginSettings`: the plugin's `settings.json`, else `manifest.settings`, allowlisted keys only."""
    name = manifest["name"]
    settings_json_path = _join(plugin_path, "settings.json")
    try:
        parsed = _read_json(settings_json_path)
        if isinstance(parsed, dict):
            filtered = _parse_plugin_settings(parsed)
            if filtered:
                logger.debug("Loaded settings from settings.json for plugin %s", name)
                return filtered
    except OSError as exc:
        if exc.errno not in _FS_INACCESSIBLE:
            logger.warning("Failed to parse settings.json for plugin %s: %s", name, exc)
    except ValueError as exc:
        logger.warning("Failed to parse settings.json for plugin %s: %s", name, exc)
    manifest_settings = manifest.get("settings")
    if manifest_settings:
        filtered = _parse_plugin_settings(manifest_settings)
        if filtered:
            logger.debug("Loaded settings from manifest for plugin %s", name)
            return filtered
    return None


def merge_hooks_settings(base: dict[str, Any] | None, additional: Mapping[str, Any]) -> dict[str, Any]:
    """`mergeHooksSettings`: each event's matchers concatenated, base first."""
    if not base:
        return dict(additional)
    merged = dict(base)
    for event, matchers in additional.items():
        if not merged.get(event):
            merged[event] = matchers
        else:
            merged[event] = [*(merged.get(event) or []), *matchers]
    return merged


@dataclass
class _LoadedPlugins:
    plugins: list[LoadedPlugin]
    errors: list[PluginError]


async def load_plugins_from_marketplaces(locations: SettingsLocations, *, cache_only: bool) -> _LoadedPlugins:
    """
    `loadPluginsFromMarketplaces`: resolve `enabledPlugins` (`--add-dir` ones underneath) to
    marketplace entries, apply the enterprise marketplace policy (fail-closed when a policy is
    set and the marketplace's source is unknown), then load each entry with the full or the
    cache-only loader, in parallel.
    """
    # Breaks the import cycle with installed_plugins_manager (it imports this module).
    from app.utils.plugins.installed_plugins_manager import get_in_memory_installed_plugins

    settings = get_initial_settings(locations)
    enabled_plugins: dict[str, Any] = {**get_add_dir_enabled_plugins(locations),
                                       **(settings.get("enabledPlugins") or {})}
    plugins: list[LoadedPlugin] = []
    errors: list[PluginError] = []

    entries: list[tuple[str, Any]] = []
    for key, value in enabled_plugins.items():
        if not safe_parse(PluginIdSchema, key).success or value is None:
            continue
        if parse_plugin_identifier(key).marketplace == BUILTIN_MARKETPLACE_NAME:
            continue
        entries.append((key, value))

    known_marketplaces = await load_known_marketplaces_config_safe()
    strict_allowlist = get_strict_known_marketplaces(locations)
    blocklist = get_blocked_marketplaces(locations)
    has_enterprise_policy = strict_allowlist is not None or bool(blocklist)

    unique_marketplaces = list(dict.fromkeys(
        m for m in (parse_plugin_identifier(plugin_id).marketplace for plugin_id, _ in entries) if m
    ))
    catalogs = dict(zip(
        unique_marketplaces,
        await asyncio.gather(*(get_marketplace_cache_only(name) for name in unique_marketplaces)),
        strict=True,
    ))
    installed_plugins_data = get_in_memory_installed_plugins()

    async def load_one(plugin_id: str, enabled_value: Any) -> LoadedPlugin | None:
        parsed = parse_plugin_identifier(plugin_id)
        plugin_name, marketplace_name = parsed.name, parsed.marketplace or ""
        marketplace_config = known_marketplaces.get(marketplace_name)
        if not marketplace_config and has_enterprise_policy:
            errors.append({
                "type": "marketplace-blocked-by-policy",
                "source": plugin_id,
                "plugin": plugin_name,
                "marketplace": marketplace_name,
                "blockedByBlocklist": strict_allowlist is None,
                "allowedSources": [format_source_for_display(s) for s in (strict_allowlist or [])],
            })
            return None
        if marketplace_config and not is_source_allowed_by_policy(marketplace_config["source"], locations):
            is_blocked = is_source_in_blocklist(marketplace_config["source"], locations)
            allowlist = get_strict_known_marketplaces(locations) or []
            errors.append({
                "type": "marketplace-blocked-by-policy",
                "source": plugin_id,
                "plugin": plugin_name,
                "marketplace": marketplace_name,
                "blockedByBlocklist": is_blocked,
                "allowedSources": [] if is_blocked else [format_source_for_display(s) for s in allowlist],
            })
            return None
        result: PluginByIdResult | None = None
        marketplace = catalogs.get(marketplace_name)
        if marketplace and marketplace_config:
            entry = next((p for p in marketplace["plugins"] if p["name"] == plugin_name), None)
            if entry is not None:
                result = PluginByIdResult(entry=entry,
                                          marketplace_install_location=marketplace_config["installLocation"])
        else:
            result = await get_plugin_by_id_cache_only(plugin_id)
        if result is None:
            errors.append({"type": "plugin-not-found", "source": plugin_id, "pluginId": plugin_name,
                           "marketplace": marketplace_name})
            return None
        installations = installed_plugins_data["plugins"].get(plugin_id) or []
        install_entry = installations[0] if installations else None
        if cache_only:
            return await _load_plugin_from_marketplace_entry_cache_only(
                result.entry, result.marketplace_install_location, plugin_id, enabled_value is True, errors,
                install_entry["installPath"] if install_entry else None,
            )
        return await _load_plugin_from_marketplace_entry(
            result.entry, result.marketplace_install_location, plugin_id, enabled_value is True, errors,
            install_entry.get("version") if install_entry else None,
        )

    results = await asyncio.gather(*(load_one(pid, value) for pid, value in entries), return_exceptions=True)
    for (plugin_id, _), outcome in zip(entries, results, strict=True):
        if isinstance(outcome, BaseException):
            if not isinstance(outcome, Exception):
                raise outcome
            logger.error("%s", outcome)
            errors.append({"type": "generic-error", "source": plugin_id, "plugin": plugin_id.split("@")[0],
                           "error": _error_message(outcome)})
        elif outcome is not None:
            plugins.append(outcome)
    return _LoadedPlugins(plugins=plugins, errors=errors)


def _marketplace_dir_of(install_location: str) -> str:
    """A marketplace's directory: its install location, or the parent of a marketplace JSON file."""
    return install_location if os.path.isdir(install_location) else _join(install_location, "..")


async def _extract_zip_for_session(plugin_path: str, plugin_id: str) -> str:
    session_dir = await get_session_plugin_cache_path()
    extract_dir = _join(session_dir, _UNSAFE_EXTRACT_SEGMENT.sub("-", plugin_id))
    await extract_zip_to_directory(plugin_path, extract_dir)
    return extract_dir


async def _load_plugin_from_marketplace_entry_cache_only(
    entry: PluginMarketplaceEntry,
    marketplace_install_location: str,
    plugin_id: str,
    enabled: bool,
    errors_out: list[PluginError],
    install_path: str | None,
) -> LoadedPlugin | None:
    """
    `loadPluginFromMarketplaceEntryCacheOnly`: no network, no copy. A local entry reads from the
    marketplace directory, an external one from its recorded install path; a missing one is a
    `plugin-cache-miss`. A zip-cached plugin is still extracted.
    """
    source: Any = entry["source"]
    if isinstance(source, str):
        try:
            os.stat(marketplace_install_location)
        except OSError:
            errors_out.append({"type": "plugin-cache-miss", "source": plugin_id, "plugin": entry["name"],
                               "installPath": marketplace_install_location})
            return None
        plugin_path = _join(_marketplace_dir_of(marketplace_install_location), source)
    else:
        if not install_path or not _path_exists(install_path):
            errors_out.append({"type": "plugin-cache-miss", "source": plugin_id, "plugin": entry["name"],
                               "installPath": install_path if install_path is not None else "(not recorded)"})
            return None
        plugin_path = install_path
    if is_plugin_zip_cache_enabled() and plugin_path.endswith(".zip"):
        try:
            plugin_path = await _extract_zip_for_session(plugin_path, plugin_id)
        except Exception as exc:  # noqa: BLE001 - a broken zip is a cache miss in cache-only mode
            logger.error("Failed to extract plugin ZIP %s: %s", plugin_path, exc)
            errors_out.append({"type": "plugin-cache-miss", "source": plugin_id, "plugin": entry["name"],
                               "installPath": plugin_path})
            return None
    return await _finish_loading_plugin_from_path(entry, plugin_id, enabled, errors_out, plugin_path)


async def _load_plugin_from_marketplace_entry(
    entry: PluginMarketplaceEntry,
    marketplace_install_location: str,
    plugin_id: str,
    enabled: bool,
    errors_out: list[PluginError],
    installed_version: str | None = None,
) -> LoadedPlugin | None:
    """
    `loadPluginFromMarketplaceEntry`: a local entry is copied to the versioned cache (its
    marketplace path on failure); an external one is read from the versioned cache, a seed, or
    fetched (`cache_plugin`) and copied there. A zip-cached plugin is extracted for the session.
    """
    logger.debug("Loading plugin %s from source: %s", entry["name"], json.dumps(entry["source"]))
    source: Any = entry["source"]
    if isinstance(source, str):
        # Upstream's `(await stat(location)).isDirectory()`: a missing location rejects the load.
        os.stat(marketplace_install_location)
        marketplace_dir = _marketplace_dir_of(marketplace_install_location)
        source_plugin_path = _join(marketplace_dir, source)
        if not _path_exists(source_plugin_path):
            logger.error("Plugin path not found: %s", source_plugin_path)
            errors_out.append({
                "type": "generic-error",
                "source": plugin_id,
                "error": f"Plugin directory not found at path: {source_plugin_path}. "
                         "Check that the marketplace entry has the correct path.",
            })
            return None
        try:
            manifest_path = _join(source_plugin_path, PLUGIN_PACKAGE_DIR, "plugin.json")
            plugin_manifest: PluginManifest | None
            try:
                plugin_manifest = await load_plugin_manifest(manifest_path, entry["name"], source)
            except Exception:  # noqa: BLE001 - falls back to the entry version or the git SHA, as upstream
                plugin_manifest = None
            version = await calculate_plugin_version(plugin_id, source, plugin_manifest, marketplace_dir,
                                                     entry.get("version"))
            plugin_path = await copy_plugin_to_versioned_cache(source_plugin_path, plugin_id, version,
                                                               entry, marketplace_dir)
            logger.debug("Resolved local plugin %s to versioned cache: %s", entry["name"], plugin_path)
        except Exception as exc:  # noqa: BLE001 - upstream falls back to the marketplace path
            logger.warning("Failed to copy plugin %s to versioned cache: %s. Using marketplace path.",
                           entry["name"], exc)
            plugin_path = source_plugin_path
    else:
        try:
            plugin_path = await _resolve_external_plugin_path(entry, plugin_id, installed_version)
        except Exception as exc:  # noqa: BLE001 - a failed download disables the plugin for the session
            message = _error_message(exc)
            logger.error("Failed to cache plugin %s: %s", entry["name"], message)
            errors_out.append({"type": "generic-error", "source": plugin_id,
                               "error": f"Failed to download/cache plugin {entry['name']}: {message}"})
            return None
    if is_plugin_zip_cache_enabled() and plugin_path.endswith(".zip"):
        try:
            extract_dir = await _extract_zip_for_session(plugin_path, plugin_id)
            logger.debug("Extracted plugin ZIP to session dir: %s", extract_dir)
            plugin_path = extract_dir
        except Exception as exc:
            logger.debug("Failed to extract plugin ZIP %s, deleting corrupt file: %s", plugin_path, exc)
            try:
                os.unlink(plugin_path)
            except OSError:
                pass
            raise
    return await _finish_loading_plugin_from_path(entry, plugin_id, enabled, errors_out, plugin_path)


async def _resolve_external_plugin_path(
    entry: PluginMarketplaceEntry, plugin_id: str, installed_version: str | None
) -> str:
    source: Any = entry["source"]
    version = await calculate_plugin_version(
        plugin_id, source, None, None, installed_version or entry.get("version"),
        source.get("sha") if "sha" in source else None,
    )
    versioned_path = get_versioned_cache_path(plugin_id, version)
    zip_path = get_versioned_zip_cache_path(plugin_id, version)
    if is_plugin_zip_cache_enabled() and _path_exists(zip_path):
        logger.debug("Using versioned cached plugin ZIP %s from %s", entry["name"], zip_path)
        return zip_path
    if _path_exists(versioned_path):
        logger.debug("Using versioned cached plugin %s from %s", entry["name"], versioned_path)
        return versioned_path
    seed_path = await probe_seed_cache(plugin_id, version)
    if seed_path is None and version == "unknown":
        seed_path = await probe_seed_cache_any_version(plugin_id)
    if seed_path:
        logger.debug("Using seed cache for external plugin %s at %s", entry["name"], seed_path)
        return seed_path
    cached = await cache_plugin(source, manifest={"name": entry["name"]})
    actual_version = version if version != "unknown" else await calculate_plugin_version(
        plugin_id, source, cached.manifest, cached.path, installed_version or entry.get("version"),
        cached.git_commit_sha,
    )
    plugin_path = await copy_plugin_to_versioned_cache(cached.path, plugin_id, actual_version, entry, None)
    if cached.path != plugin_path:
        rm(cached.path, recursive=True, force=True)
    return plugin_path


def _command_mapping_paths(
    commands: Mapping[str, Any], plugin_path: str, entry_name: str, plugin_id: str, errors: list[PluginError],
    metadata_by_name: dict[str, CommandMetadata],
) -> list[str]:
    """The marketplace entry's `{name: {source}}` commands: existing paths, their metadata recorded."""
    valid: list[str] = []
    for command_name, metadata in commands.items():
        if not metadata or not isinstance(metadata, dict) or not metadata.get("source"):
            continue
        full_path = _join(plugin_path, metadata["source"])
        if _path_exists(full_path):
            valid.append(full_path)
            metadata_by_name[command_name] = cast(CommandMetadata, metadata)
        else:
            logger.warning("Command %s path %s from marketplace entry not found at %s for %s",
                           command_name, metadata["source"], full_path, entry_name)
            logger.error("Plugin component file not found: %s for %s", full_path, entry_name)
            errors.append(_path_not_found(plugin_id, entry_name, full_path, "commands"))
    return valid


def _command_list_paths(
    commands: Any, plugin_path: str, entry_name: str, plugin_id: str, errors: list[PluginError]
) -> list[str]:
    """The marketplace entry's path (or paths) commands that exist."""
    valid: list[str] = []
    for command_path in _as_list(commands):
        if not isinstance(command_path, str):
            logger.error("Unexpected command format in marketplace entry for %s", entry_name)
            continue
        full_path = _join(plugin_path, command_path)
        if _path_exists(full_path):
            valid.append(full_path)
        else:
            logger.warning("Command path %s from marketplace entry not found at %s for %s",
                           command_path, full_path, entry_name)
            logger.error("Plugin component file not found: %s for %s", full_path, entry_name)
            errors.append(_path_not_found(plugin_id, entry_name, full_path, "commands"))
    return valid


async def _finish_loading_plugin_from_path(
    entry: PluginMarketplaceEntry,
    plugin_id: str,
    enabled: bool,
    errors_out: list[PluginError],
    plugin_path: str,
) -> LoadedPlugin | None:
    """
    `finishLoadingPluginFromPath`: `create_plugin_from_path`, then the marketplace entry: as the
    manifest when the plugin has none; a conflict (`generic-error`, plugin dropped) when the entry
    is non-strict and both declare components; otherwise it supplements the plugin's components.
    """
    errors: list[PluginError] = []
    entry_name = entry["name"]
    has_manifest = _path_exists(_join(plugin_path, PLUGIN_PACKAGE_DIR, "plugin.json"))
    created = await create_plugin_from_path(plugin_path, plugin_id, enabled, entry_name, entry.get("strict", True))
    plugin = created.plugin
    errors.extend(created.errors)
    source: Any = entry["source"]
    if isinstance(source, dict) and source.get("sha"):
        plugin.sha = source["sha"]

    has_components = any(entry.get(k) for k in ("commands", "agents", "skills", "hooks", "outputStyles"))
    commands: Any = entry.get("commands")
    if not has_manifest:
        plugin.manifest = cast(PluginManifest, {k: v for k, v in entry.items() if k not in ("id", "source", "strict")})
        plugin.name = plugin.manifest["name"]
        if commands:
            if _is_command_mapping(commands):
                metadata_by_name: dict[str, CommandMetadata] = {}
                valid = _command_mapping_paths(commands, plugin_path, entry_name, plugin_id, errors, metadata_by_name)
                if valid:
                    plugin.commands_paths = valid
                    plugin.commands_metadata = metadata_by_name
            else:
                valid = _command_list_paths(commands, plugin_path, entry_name, plugin_id, errors)
                if valid:
                    plugin.commands_paths = valid
        if entry.get("agents"):
            valid = await validate_plugin_paths(_as_list(entry["agents"]), plugin_path, entry_name, plugin_id,
                                                "agents", "Agent", "from marketplace entry", errors)
            if valid:
                plugin.agents_paths = valid
        if entry.get("skills"):
            skill_paths = _as_list(entry["skills"])
            logger.debug("Processing %d skill paths for plugin %s", len(skill_paths), entry_name)
            valid = []
            for skill_path in skill_paths:
                full_path = _join(plugin_path, skill_path)
                exists = _path_exists(full_path)
                logger.debug("Checking skill path: %s -> %s (exists: %s)", skill_path, full_path, exists)
                if exists:
                    valid.append(full_path)
                else:
                    logger.warning("Skill path %s from marketplace entry not found at %s for %s",
                                   skill_path, full_path, entry_name)
                    logger.error("Plugin component file not found: %s for %s", full_path, entry_name)
                    errors.append(_path_not_found(plugin_id, entry_name, full_path, "skills"))
            logger.debug("Found %d valid skill paths for plugin %s, setting skillsPaths", len(valid), entry_name)
            if valid:
                plugin.skills_paths = valid
        else:
            logger.debug("Plugin %s has no entry.skills defined", entry_name)
        if entry.get("outputStyles"):
            valid = await validate_plugin_paths(_as_list(entry["outputStyles"]), plugin_path, entry_name, plugin_id,
                                                "output-styles", "Output style", "from marketplace entry", errors)
            if valid:
                plugin.output_styles_paths = valid
        if entry.get("hooks"):
            plugin.hooks_config = cast(dict[str, Any], entry["hooks"])
    elif not entry.get("strict") and has_manifest and has_components:
        logger.error(
            "Plugin %s has both plugin.json and marketplace manifest entries for "
            "commands/agents/skills/hooks/outputStyles. This is a conflict.", entry_name,
        )
        errors_out.append({
            "type": "generic-error",
            "source": plugin_id,
            "error": f"Plugin {entry_name} has conflicting manifests: both plugin.json and marketplace entry "
                     "specify components. Set strict: true in marketplace entry or remove component specs from "
                     "one location.",
        })
        return None
    else:
        if commands:
            if _is_command_mapping(commands):
                metadata_by_name = dict(plugin.commands_metadata or {})
                valid = _command_mapping_paths(commands, plugin_path, entry_name, plugin_id, errors, metadata_by_name)
                if valid:
                    plugin.commands_paths = [*(plugin.commands_paths or []), *valid]
                    plugin.commands_metadata = metadata_by_name
            else:
                valid = _command_list_paths(commands, plugin_path, entry_name, plugin_id, errors)
                if valid:
                    plugin.commands_paths = [*(plugin.commands_paths or []), *valid]
        if entry.get("agents"):
            valid = await validate_plugin_paths(_as_list(entry["agents"]), plugin_path, entry_name, plugin_id,
                                                "agents", "Agent", "from marketplace entry", errors)
            if valid:
                plugin.agents_paths = [*(plugin.agents_paths or []), *valid]
        if entry.get("skills"):
            valid = await validate_plugin_paths(_as_list(entry["skills"]), plugin_path, entry_name, plugin_id,
                                                "skills", "Skill", "from marketplace entry", errors)
            if valid:
                plugin.skills_paths = [*(plugin.skills_paths or []), *valid]
        if entry.get("outputStyles"):
            valid = await validate_plugin_paths(_as_list(entry["outputStyles"]), plugin_path, entry_name, plugin_id,
                                                "output-styles", "Output style", "from marketplace entry", errors)
            if valid:
                plugin.output_styles_paths = [*(plugin.output_styles_paths or []), *valid]
        if entry.get("hooks"):
            plugin.hooks_config = {**(plugin.hooks_config or {}), **cast(dict[str, Any], entry["hooks"])}
    errors_out.extend(errors)
    return plugin


async def load_session_only_plugins(session_plugin_paths: list[str]) -> _LoadedPlugins:
    """
    `loadSessionOnlyPlugins`: the `--plugin-dir` directories, always enabled, sourced
    `<name>@inline`. A missing directory or a failed load is an error, never a failure.
    """
    plugins: list[LoadedPlugin] = []
    errors: list[PluginError] = []
    for index, plugin_path in enumerate(session_plugin_paths):
        try:
            resolved_path = os.path.abspath(plugin_path)
            if not _path_exists(resolved_path):
                logger.warning("Plugin path does not exist: %s, skipping", resolved_path)
                errors.append({"type": "path-not-found", "source": f"inline[{index}]", "path": resolved_path,
                               "component": "commands"})
                continue
            dir_name = os.path.basename(resolved_path)
            created = await create_plugin_from_path(resolved_path, f"{dir_name}@inline", True, dir_name)
            created.plugin.source = f"{created.plugin.name}@inline"
            created.plugin.repository = f"{created.plugin.name}@inline"
            plugins.append(created.plugin)
            errors.extend(created.errors)
            logger.debug("Loaded inline plugin from path: %s", created.plugin.name)
        except Exception as exc:  # noqa: BLE001 - one bad --plugin-dir must not stop the others
            message = _error_message(exc)
            logger.warning("Failed to load session plugin from %s: %s", plugin_path, message)
            errors.append({"type": "generic-error", "source": f"inline[{index}]",
                           "error": f"Failed to load plugin: {message}"})
    if plugins:
        logger.debug("Loaded %d session-only plugins from --plugin-dir", len(plugins))
    return _LoadedPlugins(plugins=plugins, errors=errors)


def merge_plugin_sources(
    session: list[LoadedPlugin],
    marketplace: list[LoadedPlugin],
    builtin: list[LoadedPlugin],
    managed_names: set[str] | None = None,
) -> tuple[list[LoadedPlugin], list[PluginError]]:
    """
    `mergePluginSources`: session plugins first (a managed-settings name drops the session copy,
    with an error), then marketplace plugins not overridden by a session one, then built-ins.
    """
    errors: list[PluginError] = []
    session_plugins: list[LoadedPlugin] = []
    for plugin in session:
        if managed_names and plugin.name in managed_names:
            logger.warning('Plugin "%s" from --plugin-dir is blocked by managed settings', plugin.name)
            errors.append({
                "type": "generic-error",
                "source": plugin.source,
                "plugin": plugin.name,
                "error": f'--plugin-dir copy of "{plugin.name}" ignored: plugin is locked by managed settings',
            })
            continue
        session_plugins.append(plugin)
    session_names = {p.name for p in session_plugins}
    marketplace_plugins: list[LoadedPlugin] = []
    for plugin in marketplace:
        if plugin.name in session_names:
            logger.debug('Plugin "%s" from --plugin-dir overrides installed version', plugin.name)
            continue
        marketplace_plugins.append(plugin)
    return [*session_plugins, *marketplace_plugins, *builtin], errors


class _AsyncMemo:
    """
    lodash `memoize` over an async function, keyed by the run's `SettingsLocations`: the first
    call's task is kept (a failure too, as a rejected promise is) until `clear`. A finished
    task's result is returned from any event loop.
    """

    def __init__(self) -> None:
        self.cache: dict[SettingsLocations, asyncio.Future[PluginLoadResult]] = {}

    async def call(
        self, key: SettingsLocations, factory: Callable[[], Coroutine[Any, Any, PluginLoadResult]]
    ) -> PluginLoadResult:
        future = self.cache.get(key)
        if future is not None:
            if future.done():
                return future.result()
            if future.get_loop() is asyncio.get_running_loop():
                return await asyncio.shield(future)
        task = asyncio.ensure_future(factory())
        self.cache[key] = task
        return await asyncio.shield(task)

    def set(self, key: SettingsLocations, result: PluginLoadResult) -> None:
        future: asyncio.Future[PluginLoadResult] = asyncio.get_running_loop().create_future()
        future.set_result(result)
        self.cache[key] = future

    def clear(self) -> None:
        self.cache.clear()


_load_all_plugins_memo = _AsyncMemo()
_load_all_plugins_cache_only_memo = _AsyncMemo()


async def load_all_plugins(locations: SettingsLocations) -> PluginLoadResult:
    """
    `loadAllPlugins` (memoized per run's settings locations until `clear_plugin_cache`): session
    (`--plugin-dir`), marketplace and built-in plugins, merged, dependency-checked, split into
    enabled and disabled. Its result also warms `load_all_plugins_cache_only`'s memo.
    """
    async def run() -> PluginLoadResult:
        result = await _assemble_plugin_load_result(
            locations, lambda: load_plugins_from_marketplaces(locations, cache_only=False))
        _load_all_plugins_cache_only_memo.set(locations, result)
        return result

    return await _load_all_plugins_memo.call(locations, run)


async def load_all_plugins_cache_only(locations: SettingsLocations) -> PluginLoadResult:
    """
    `loadAllPluginsCacheOnly` (memoized separately): the same merge, but marketplace plugins are
    read from what is on disk only (`plugin-cache-miss` otherwise), never fetched.
    `TDDAGENTS_SYNC_PLUGIN_INSTALL` runs the full loader instead.
    """
    async def run() -> PluginLoadResult:
        if is_env_truthy(os.environ.get(SYNC_PLUGIN_INSTALL_ENV)):
            return await load_all_plugins(locations)
        return await _assemble_plugin_load_result(
            locations, lambda: load_plugins_from_marketplaces(locations, cache_only=True))

    return await _load_all_plugins_cache_only_memo.call(locations, run)


async def _assemble_plugin_load_result(
    locations: SettingsLocations, marketplace_loader: Callable[[], Coroutine[Any, Any, _LoadedPlugins]]
) -> PluginLoadResult:
    """`assemblePluginLoadResult`: the shared body of both loaders."""
    inline_plugins = get_inline_plugins()
    marketplace_result, session_result = await asyncio.gather(
        marketplace_loader(),
        load_session_only_plugins(inline_plugins) if inline_plugins else _no_plugins(),
    )
    builtin_enabled, builtin_disabled = get_builtin_plugins(locations)
    all_plugins, merge_errors = merge_plugin_sources(
        session=session_result.plugins,
        marketplace=marketplace_result.plugins,
        builtin=[*builtin_enabled, *builtin_disabled],
        managed_names=get_managed_plugin_names(locations),
    )
    all_errors: list[PluginError] = [*marketplace_result.errors, *session_result.errors, *merge_errors]
    demotion = verify_and_demote(all_plugins)
    for plugin in all_plugins:
        if plugin.source in demotion.demoted:
            plugin.enabled = False
    all_errors.extend(demotion.errors)
    enabled_plugins = [p for p in all_plugins if p.enabled]
    logger.debug("Found %d plugins (%d enabled, %d disabled)", len(all_plugins), len(enabled_plugins),
                 len(all_plugins) - len(enabled_plugins))
    cache_plugin_settings(enabled_plugins)
    return PluginLoadResult(enabled=enabled_plugins, disabled=[p for p in all_plugins if not p.enabled],
                            errors=all_errors)


async def _no_plugins() -> _LoadedPlugins:
    return _LoadedPlugins(plugins=[], errors=[])


def clear_plugin_cache(reason: str | None = None) -> None:
    """
    `clearPluginCache`: forget both loaders' memoized results and the plugin settings base, so
    the next load rescans. Call after installs, removals or `enabledPlugins` changes.
    """
    if reason:
        logger.debug("clearPluginCache: invalidating loadAllPlugins cache (%s)", reason)
    _load_all_plugins_memo.clear()
    _load_all_plugins_cache_only_memo.clear()
    clear_plugin_settings_base()


def _merge_plugin_settings(plugins: list[LoadedPlugin]) -> dict[str, Any] | None:
    """`mergePluginSettings`: later plugins override earlier ones, key by key."""
    merged: dict[str, Any] | None = None
    for plugin in plugins:
        if not plugin.settings:
            continue
        if merged is None:
            merged = {}
        for key, value in plugin.settings.items():
            if key in merged:
                logger.debug('Plugin "%s" overrides setting "%s" (previously set by another plugin)', plugin.name, key)
            merged[key] = value
    return merged


def cache_plugin_settings(plugins: list[LoadedPlugin]) -> None:
    """`cachePluginSettings`: the enabled plugins' merged settings become the settings cascade's base."""
    settings = _merge_plugin_settings(plugins)
    set_plugin_settings_base(settings)
    if settings:
        logger.debug("Cached plugin settings with keys: %s", ", ".join(settings))


__all__ = [
    "REMOTE_ENV",
    "SYNC_PLUGIN_INSTALL_ENV",
    "CachedPlugin",
    "CreatedPlugin",
    "cache_plugin",
    "cache_plugin_settings",
    "clear_plugin_cache",
    "copy_dir",
    "copy_plugin_to_versioned_cache",
    "create_plugin_from_path",
    "generate_temporary_cache_name_for_plugin",
    "get_legacy_cache_path",
    "get_plugin_cache_path",
    "get_versioned_cache_path",
    "get_versioned_cache_path_in",
    "get_versioned_zip_cache_path",
    "git_clone",
    "install_from_git",
    "install_from_git_subdir",
    "install_from_github",
    "install_from_local",
    "install_from_npm",
    "load_all_plugins",
    "load_all_plugins_cache_only",
    "load_plugin_hooks",
    "load_plugin_manifest",
    "load_plugins_from_marketplaces",
    "load_session_only_plugins",
    "merge_hooks_settings",
    "merge_plugin_sources",
    "probe_seed_cache",
    "probe_seed_cache_any_version",
    "resolve_git_subdir_url",
    "resolve_plugin_path",
    "validate_git_url",
    "validate_plugin_paths",
]
