"""
Where auto memory lives, and whether it is on.

Ported from `reference/claude-code/src/memdir/paths.ts` -> `isAutoMemoryEnabled`,
`getMemoryBaseDir`, `validateMemoryPath`, `getAutoMemPathSetting`, `getAutoMemPath`,
`getAutoMemEntrypoint`, `isAutoMemPath`, and `utils/git.ts` -> `findCanonicalGitRoot`.

**Divergence (Part I5, user decision 2026-11-01): auto memory is opt-in.** Upstream enables it
unless something disables it; TDDAgents keeps every run an independent research sample, so it
is on only when `TDDAGENTS_ENABLE_AUTO_MEMORY` is truthy, `TDDAGENTS_DISABLE_AUTO_MEMORY` is
defined falsy (upstream's "0 means on"), or the settings say `autoMemoryEnabled: true`. A
truthy `TDDAGENTS_DISABLE_AUTO_MEMORY` and `autoMemoryEnabled: false` turn it off.

Not ported: `CLAUDE_CODE_SIMPLE`, the remote (`CLAUDE_CODE_REMOTE*`) and Cowork
(`CLAUDE_COWORK_MEMORY_PATH_OVERRIDE`) paths, `isExtractModeActive` (no extract-memories agent)
and `getAutoMemDailyLogPath` (KAIROS).
"""

from __future__ import annotations

import os
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.tools.bash_tool.bash_security import is_env_truthy
from app.types.permissions import PermissionRuleSource
from app.utils.settings.settings import SettingsLocations, get_settings_for_source
from app.utils.tool_result_storage import config_home, sanitize_path

AUTO_MEM_DIRNAME = "memory"
AUTO_MEM_ENTRYPOINT_NAME = "MEMORY.md"
ENABLE_VAR = "TDDAGENTS_ENABLE_AUTO_MEMORY"
DISABLE_VAR = "TDDAGENTS_DISABLE_AUTO_MEMORY"

#: `getAutoMemPathSetting`: the trusted sources, in order. `projectSettings` is left out on
#: purpose: a repository could otherwise point the write carve-out at `~/.ssh`.
TRUSTED_SETTING_SOURCES = (
    PermissionRuleSource.POLICY_SETTINGS,
    PermissionRuleSource.FLAG_SETTINGS,
    PermissionRuleSource.LOCAL_SETTINGS,
    PermissionRuleSource.USER_SETTINGS,
)


def _is_env_defined_falsy(value: str | None) -> bool:
    """`isEnvDefinedFalsy`."""
    return value is not None and value.strip().lower() in ("0", "false", "no", "off")


def is_auto_memory_enabled(env: Mapping[str, str] | None = None, settings: Mapping[str, Any] | None = None) -> bool:
    """`isAutoMemoryEnabled`, opt-in (see the module)."""
    source = os.environ if env is None else env
    disable = source.get(DISABLE_VAR)
    if is_env_truthy(disable):
        return False
    if _is_env_defined_falsy(disable) or is_env_truthy(source.get(ENABLE_VAR)):
        return True
    enabled = (settings or {}).get("autoMemoryEnabled")
    return enabled if isinstance(enabled, bool) else False


def get_memory_base_dir() -> str:
    """`getMemoryBaseDir`: the config home (`~/.tddagents`, `TDDAGENTS_CONFIG_DIR`)."""
    return str(config_home())


def validate_memory_path(raw: str | None, expand_tilde: bool) -> str | None:
    """
    `validateMemoryPath`: a usable absolute directory with one trailing `/`, or `None` for an
    empty, relative, near-root, UNC or null-byte path (a read-allowlist root must not be any of those).
    """
    if not raw:
        return None
    candidate = raw
    if expand_tilde and candidate.startswith(("~/", "~\\")):
        rest = candidate[2:]
        if os.path.normpath(rest or ".") in (".", ".."):
            return None
        candidate = os.path.join(os.path.expanduser("~"), rest)
    normalized = os.path.normpath(candidate).rstrip("/\\")
    if (not os.path.isabs(normalized) or len(normalized) < 3 or normalized.startswith(("\\\\", "//"))
            or "\0" in normalized):
        return None
    return unicodedata.normalize("NFC", normalized + os.sep)


def get_auto_mem_path_setting(locations: SettingsLocations | None) -> str | None:
    """`getAutoMemPathSetting`: `autoMemoryDirectory` from the first trusted source that sets it."""
    if locations is None:
        return None
    for source in TRUSTED_SETTING_SOURCES:
        value = (get_settings_for_source(source, locations) or {}).get("autoMemoryDirectory")
        if value is not None:
            return validate_memory_path(str(value), True)
    return None


def find_canonical_git_root(start: str) -> str | None:
    """
    `findCanonicalGitRoot`: the repository root holding `start`; a worktree resolves to the main
    repository's root, so all worktrees share one memory directory. The search stops at `start`:
    TDDAgents never lets git look above a project (`GIT_CEILING_DIRECTORIES`).
    """
    marker = Path(start) / ".git"
    if marker.is_dir():
        return str(Path(start))
    if not marker.is_file():
        return None
    text = marker.read_text(encoding="utf-8", errors="replace").strip()
    if not text.startswith("gitdir:"):
        return None
    git_dir = Path(start, text[len("gitdir:"):].strip()).resolve()
    common = git_dir / "commondir"
    if common.is_file():
        main_git = (git_dir / common.read_text(encoding="utf-8").strip()).resolve()
        return str(main_git.parent)
    return str(Path(start))


def get_auto_mem_path(project_root: str, locations: SettingsLocations | None = None) -> str:
    """`getAutoMemPath`: the setting's directory, else `<config>/projects/<sanitized root>/memory/`."""
    override = get_auto_mem_path_setting(locations)
    if override:
        return override
    base = find_canonical_git_root(project_root) or project_root
    path = os.path.join(get_memory_base_dir(), "projects", sanitize_path(base), AUTO_MEM_DIRNAME) + os.sep
    return unicodedata.normalize("NFC", path)


def get_auto_mem_entrypoint(project_root: str, locations: SettingsLocations | None = None) -> str:
    """`getAutoMemEntrypoint`."""
    return os.path.join(get_auto_mem_path(project_root, locations), AUTO_MEM_ENTRYPOINT_NAME)


def is_auto_mem_path(absolute_path: str, project_root: str, locations: SettingsLocations | None = None) -> bool:
    """`isAutoMemPath`: the normalized path lies in the auto-memory directory."""
    return os.path.normpath(absolute_path).startswith(get_auto_mem_path(project_root, locations))
