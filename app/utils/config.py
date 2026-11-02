"""
The global config's per-project entries, and whether a project folder is trusted.

Ported from `reference/claude-code/src/utils/config.ts` -> `getGlobalConfig` (read only),
`getCurrentProjectConfig`, `saveCurrentProjectConfig`, `checkHasTrustDialogAccepted`,
`computeTrustDialogAccepted`, `isPathTrusted`, `normalizePathForConfigKey`,
`resetTrustDialogAcceptedCacheForTesting`.

The global config is `~/.tddagents.json` (`$TDDAGENTS_CONFIG_DIR/.tddagents.json`), upstream's
`~/.claude.json` (`app/services/mcp/config.py` -> `get_global_config_path`); a project's entry
sits under `projects[<project key>]`, the key being the folder's git root, else the folder
(`get_project_config_key`, upstream's `getProjectPathForConfig`).

Upstream reads the session's cwd from module state; here every function takes the project folder
(`cwd`), since a TDDAgents process may hold several sessions. For the same reason the trust latch
(`_trustAccepted`: once true, never recomputed) is kept per folder. Not ported: the config cache
and its file lock (`saveConfigWithLock`), the auth-loss guard, and the other global config keys.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.bootstrap.state import get_session_trust_accepted
from app.services.mcp.config import get_global_config_path, get_project_config_key

logger = logging.getLogger(__name__)

#: `PROJECT_CONFIG_KEYS`'s trust flag.
HAS_TRUST_DIALOG_ACCEPTED = "hasTrustDialogAccepted"

ProjectConfig = dict[str, Any]

#: `_trustAccepted`, per project folder.
_TRUST_ACCEPTED: set[str] = set()


def normalize_path_for_config_key(path: str | Path) -> str:
    """`normalizePathForConfigKey`: the path normalized, with forward slashes on every platform."""
    return os.path.normpath(str(path)).replace("\\", "/")


def get_global_config() -> dict[str, Any]:
    """`getGlobalConfig`: the global config file's document; `{}` when missing or unreadable."""
    try:
        data = json.loads(get_global_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _projects(config: dict[str, Any]) -> dict[str, Any]:
    projects = config.get("projects")
    return projects if isinstance(projects, dict) else {}


def get_project_path_for_config(cwd: str | Path) -> str:
    """`getProjectPathForConfig`: the git root above `cwd`, else `cwd`, as a config key."""
    return normalize_path_for_config_key(get_project_config_key(cwd))


def get_current_project_config(cwd: str | Path) -> ProjectConfig:
    """`getCurrentProjectConfig`: the project's entry, `{}` when it has none."""
    entry = _projects(get_global_config()).get(get_project_path_for_config(cwd))
    return dict(entry) if isinstance(entry, dict) else {}


def save_current_project_config(cwd: str | Path, updater: Callable[[ProjectConfig], ProjectConfig]) -> None:
    """
    `saveCurrentProjectConfig`: the project's entry replaced by `updater(entry)`; nothing is
    written when the updater returns the entry unchanged. A write that fails is logged.
    """
    key = get_project_path_for_config(cwd)
    config = get_global_config()
    projects = _projects(config)
    current = projects.get(key)
    current_entry: ProjectConfig = dict(current) if isinstance(current, dict) else {}
    updated = updater(dict(current_entry))
    if updated == current_entry:
        return
    config["projects"] = {**projects, key: updated}
    path = get_global_config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    except OSError as error:
        logger.error("Failed to save config with lock: %s", error)


def reset_trust_dialog_accepted_cache_for_testing() -> None:
    """`resetTrustDialogAcceptedCacheForTesting`."""
    _TRUST_ACCEPTED.clear()


def check_has_trust_dialog_accepted(cwd: str | Path) -> bool:
    """
    `checkHasTrustDialogAccepted`: whether the trust dialog was accepted for `cwd`, for its
    project, or for any folder above it. A `True` is latched; a `False` is checked again on
    every call, so an acceptance made meanwhile is seen.
    """
    key = normalize_path_for_config_key(Path(cwd).resolve())
    if key in _TRUST_ACCEPTED:
        return True
    if _compute_trust_dialog_accepted(cwd):
        _TRUST_ACCEPTED.add(key)
        return True
    return False


def _compute_trust_dialog_accepted(cwd: str | Path) -> bool:
    """`computeTrustDialogAccepted`: the session's trust, the project's entry, then every parent folder's."""
    if get_session_trust_accepted():
        return True
    projects = _projects(get_global_config())
    if _accepted(projects.get(get_project_path_for_config(cwd))):
        return True
    return _walk_up(projects, Path(cwd).resolve())


def is_path_trusted(directory: str | Path) -> bool:
    """`isPathTrusted`: trust persisted for `directory` or a folder above it (no session trust)."""
    return _walk_up(_projects(get_global_config()), Path(directory).resolve())


def _accepted(entry: Any) -> bool:
    return isinstance(entry, dict) and bool(entry.get(HAS_TRUST_DIALOG_ACCEPTED))


def _walk_up(projects: dict[str, Any], start: Path) -> bool:
    return any(_accepted(projects.get(normalize_path_for_config_key(folder))) for folder in (start, *start.parents))


__all__ = [
    "HAS_TRUST_DIALOG_ACCEPTED",
    "ProjectConfig",
    "check_has_trust_dialog_accepted",
    "get_current_project_config",
    "get_global_config",
    "get_project_path_for_config",
    "is_path_trusted",
    "normalize_path_for_config_key",
    "reset_trust_dialog_accepted_cache_for_testing",
    "save_current_project_config",
]
