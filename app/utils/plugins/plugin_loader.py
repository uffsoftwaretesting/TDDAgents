"""
Plugin loader, the install/cache half: cache paths, seed probing, copying and fetching plugin
sources, and the manifest read.

Ported from `reference/claude-code/src/utils/plugins/pluginLoader.ts` -> `getPluginCachePath`,
`getVersionedCachePathIn`, `getVersionedCachePath`, `getVersionedZipCachePath`, `probeSeedCache`,
`probeSeedCacheAnyVersion`, `getLegacyCachePath`, `resolvePluginPath`, `copyDir`,
`copyPluginToVersionedCache`, `validateGitUrl`, `installFromNpm`, `gitClone`, `installFromGit`,
`installFromGitHub`, `resolveGitSubdirUrl`, `installFromGitSubdir`, `installFromLocal`,
`generateTemporaryCacheNameForPlugin`, `cachePlugin`, `loadPluginManifest`.

**Stage 2 appends the load half to this module**: `loadPluginHooks`, `validatePluginPaths`,
`createPluginFromPath`, the settings/hook merges, `loadPluginsFromMarketplaces`,
`loadPluginFromMarketplaceEntry[CacheOnly]`, `finishLoadingPluginFromPath`,
`loadSessionOnlyPlugins`, `mergePluginSources`, `loadAllPlugins`, `loadAllPluginsCacheOnly`,
`assemblePluginLoadResult`, `clearPluginCache`, `cachePluginSettings`.

Product substitutions: `CLAUDE_CODE_REMOTE` is `TDDAGENTS_REMOTE` (HTTPS GitHub URLs instead of
SSH). The plugin format directory `.claude-plugin/` is kept: it is the plugin package format,
shared with upstream's marketplaces.

Divergences: the fetch telemetry (`logPluginFetch`, `classifyFetchError`) is not ported. Git and
npm run in the process directory (upstream's `useCwd`), see `app/utils/exec_file_no_throw.py`.
The zip cache is stage 2 (`app/utils/plugins/zip_cache.py`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import shutil
import string
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from app.utils.claudemd import is_env_truthy
from app.utils.exec_file_no_throw import exec_file_no_throw, exec_file_no_throw_with_cwd
from app.utils.fs_operations import rm
from app.utils.git.git import git_exe
from app.utils.plugins.git_availability import check_git_available
from app.utils.plugins.plugin_directories import (
    get_plugin_seed_dirs,
    get_plugins_directory,
)
from app.utils.plugins.plugin_identifier import parse_plugin_identifier
from app.utils.plugins.schemas import (
    PluginManifest,
    PluginManifestSchema,
    is_url,
    safe_parse,
)
from app.utils.plugins.zip_cache import (
    convert_directory_to_zip_in_place,
    is_plugin_zip_cache_enabled,
    require_zip_cache_port,
)

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

    require_zip_cache_port()
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
    (`.claude-plugin/plugin.json`, then `plugin.json`, else `manifest`, else a minimal one),
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

    manifest_path = os.path.join(temp_path, ".claude-plugin", "plugin.json")
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


__all__ = [
    "REMOTE_ENV",
    "CachedPlugin",
    "cache_plugin",
    "copy_dir",
    "copy_plugin_to_versioned_cache",
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
    "load_plugin_manifest",
    "probe_seed_cache",
    "probe_seed_cache_any_version",
    "resolve_git_subdir_url",
    "resolve_plugin_path",
    "validate_git_url",
]
