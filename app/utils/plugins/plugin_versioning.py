"""
Plugin version calculation, for versioned cache paths and update detection.

Ported from `reference/claude-code/src/utils/plugins/pluginVersioning.ts` -> `calculatePluginVersion`,
`getGitCommitSha`, `getVersionFromPath`, `isVersionedPath`.

Order of preference: the manifest's `version`, the provided (marketplace) version, a pre-resolved
git SHA (12 chars; for `git-subdir` with an 8-char hash of the normalized subdir path appended),
the install path's HEAD SHA, else `unknown`.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Mapping
from typing import Any

from app.utils.git.git_filesystem import get_head_for_dir

logger = logging.getLogger("TDDOrchestrator.Plugins")

_LEADING_DOT_SLASH = re.compile(r"^\./")
_TRAILING_SLASHES = re.compile(r"/+\Z", re.ASCII)


async def calculate_plugin_version(
    plugin_id: str,
    source: Any,
    manifest: Mapping[str, Any] | None = None,
    install_path: str | None = None,
    provided_version: str | None = None,
    git_commit_sha: str | None = None,
) -> str:
    """`calculatePluginVersion`."""
    if manifest and manifest.get("version"):
        logger.debug("Using manifest version for %s: %s", plugin_id, manifest["version"])
        return str(manifest["version"])
    if provided_version:
        logger.debug("Using provided version for %s: %s", plugin_id, provided_version)
        return provided_version
    if git_commit_sha:
        short_sha = git_commit_sha[:12]
        if isinstance(source, Mapping) and source.get("source") == "git-subdir":
            # Must match the squashfs cron byte for byte: backslashes to slashes, one leading
            # `./` stripped, trailing slashes stripped, sha256 of the UTF-8, first 8 hex chars.
            norm_path = _TRAILING_SLASHES.sub("", _LEADING_DOT_SLASH.sub("", str(source["path"]).replace("\\", "/")))
            path_hash = hashlib.sha256(norm_path.encode("utf-8")).hexdigest()[:8]
            version = f"{short_sha}-{path_hash}"
            logger.debug("Using git-subdir SHA+path version for %s: %s (path=%s)", plugin_id, version, norm_path)
            return version
        logger.debug("Using pre-resolved git SHA for %s: %s", plugin_id, short_sha)
        return short_sha
    if install_path:
        sha = await get_git_commit_sha(install_path)
        if sha:
            logger.debug("Using git SHA for %s: %s", plugin_id, sha[:12])
            return sha[:12]
    logger.debug("No version found for %s, using 'unknown'", plugin_id)
    return "unknown"


async def get_git_commit_sha(dir_path: str) -> str | None:
    """`getGitCommitSha`: the directory's HEAD SHA, or `None` outside a repository."""
    return await get_head_for_dir(dir_path)


def get_version_from_path(install_path: str) -> str | None:
    """`getVersionFromPath`: `.../plugins/cache/<marketplace>/<plugin>/<version>` -> `<version>`."""
    parts = [p for p in install_path.split("/") if p]
    cache_index = -1
    for i, part in enumerate(parts):
        if part == "cache" and i > 0 and parts[i - 1] == "plugins":
            cache_index = i
            break
    if cache_index == -1:
        return None
    after = parts[cache_index + 1:]
    if len(after) >= 3:
        return after[2] or None
    return None


def is_versioned_path(path: str) -> bool:
    """`isVersionedPath`."""
    return get_version_from_path(path) is not None


__all__ = ["calculate_plugin_version", "get_git_commit_sha", "get_version_from_path", "is_versioned_path"]
