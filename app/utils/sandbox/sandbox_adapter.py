"""
The sandbox adapter: TDDAgents' settings and permission rules turned into the sandbox runtime's config,
and the run's sandbox manager built over it.

Ported from `reference/claude-code/src/utils/sandbox/sandbox-adapter.ts` in full:
`permissionRuleValueFromString` / `permissionRuleExtractPrefix` (its local copies),
`resolvePathPatternForSandbox`, `resolveSandboxFilesystemPath`, `shouldAllowManagedSandboxDomainsOnly`,
`shouldAllowManagedReadPathsOnly`, `convertToSandboxRuntimeConfig`, the bare-repo scrub
(`scrubBareGitRepoFiles`), `detectWorktreeMainRepoPath`, `checkDependencies`, `getSandboxEnabledSetting`,
`isAutoAllowBashIfSandboxedEnabled`, `areUnsandboxedCommandsAllowed`, `isSandboxRequired`,
`isSupportedPlatform`, `isPlatformInEnabledList`, `isSandboxingEnabled`, `getSandboxUnavailableReason`,
`getLinuxGlobPatternWarnings`, `areSandboxSettingsLockedByPolicy`, `setSandboxSettings`,
`getExcludedCommands`, `wrapWithSandbox`, `initialize`, `refreshConfig`, `reset`,
`addToExcludedCommands`, and the `SandboxManager` object (`ISandboxManager`).

Divergences:
* **One adapter per run** (`SandboxAdapter`), not module state, over the run's `SettingsLocations`:
  the session's and the final run's (`app/tdd/final_run.py`) are separate. The settings are read from
  disk at each call, as upstream's `getSettings_DEPRECATED`.
* Product renames: `.claude/settings*.json` and `.claude/skills` are `.tddagents/...`; the temp
  directory is `get_tddagents_temp_dir()` (`/tmp/tddagents-<uid>`), which is also the runtime's (see
  `runtime/sandbox_utils.py`).
* **The bwrap probe** (TDDAgents, user decision 2026-11-08): `checkDependencies` also runs
  `bwrap --ro-bind / / --unshare-net true` once (`bwrap_probe.py`). A bwrap that is installed but cannot
  create a namespace exits 1 for every command, which a RunTests run would read as a failed test run
  (RED evidence); its failure is a dependency error, so a required sandbox refuses to start.
* Synchronous `initialize` (see `runtime/sandbox_manager.py`), given the caller's loop for the ask; the
  settings subscription is the run's `SettingsChangeDetector`.
* `getPlatform`'s `wsl` (`utils/platform.ts`) comes from `app/utils/system_directories.py`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from app.types.permissions import PermissionRuleSource
from app.utils.permissions.filesystem import expand_path, get_tddagents_temp_dir
from app.utils.sandbox.bwrap_probe import probe_bwrap
from app.utils.sandbox.runtime.linux_sandbox_utils import RunRipgrep, SandboxDependencyCheck, Which
from app.utils.sandbox.runtime.network_host import start_host_network
from app.utils.sandbox.runtime.ripgrep import rip_grep
from app.utils.sandbox.runtime.sandbox_config import (
    FilesystemConfig,
    NetworkConfig,
    RipgrepConfig,
    SandboxRuntimeConfig,
)
from app.utils.sandbox.runtime.sandbox_manager import SandboxManager, StartNetwork
from app.utils.sandbox.runtime.sandbox_schemas import (
    FsReadRestrictionConfig,
    FsWriteRestrictionConfig,
    NetworkRestrictionConfig,
    SandboxAskCallback,
)
from app.utils.sandbox.runtime.sandbox_utils import HOST_FS, SandboxFs
from app.utils.sandbox.runtime.sandbox_violation_store import SandboxViolationStore
from app.utils.sandbox.runtime.which import which_sync
from app.utils.settings.settings import (
    MANAGED_DROP_IN_DIRNAME,
    SETTING_SOURCES,
    SETTINGS_DIR,
    SettingsLocations,
    get_settings_for_source,
    load_settings,
    settings_file_path_for_source,
    settings_root_path_for_source,
)

logger = logging.getLogger("TDDOrchestrator.Sandbox")

WEB_FETCH_TOOL_NAME = "WebFetch"
FILE_EDIT_TOOL_NAME = "Edit"
FILE_READ_TOOL_NAME = "Read"
BASH_TOOL_NAME = "Bash"

#: `bareGitRepoFiles`: what makes git treat the working directory as a bare repository.
BARE_GIT_REPO_FILES = ("HEAD", "objects", "refs", "hooks", "config")

#: `getSandboxUnavailableReason`'s hints.
LINUX_DEPENDENCY_HINT = "install missing tools (e.g. apt install bubblewrap socat) or run /sandbox for details"
MACOS_DEPENDENCY_HINT = "run /sandbox or /doctor for details"


class SandboxUnavailableError(RuntimeError):
    """The session cannot open: the sandbox is required and unavailable, or it failed to start."""


def permission_rule_value_from_string(rule_string: str) -> tuple[str, str | None]:
    """`permissionRuleValueFromString`, the adapter's local copy: `Tool(content)` or a bare tool name."""
    match = re.match(r"^([^(]+)\(([^)]+)\)$", rule_string)
    if not match:
        return rule_string, None
    return match.group(1), match.group(2)


def permission_rule_extract_prefix(permission_rule: str) -> str | None:
    """`permissionRuleExtractPrefix`: `npm run test:*` -> `npm run test`."""
    match = re.match(r"^(.+):\*$", permission_rule)
    return match.group(1) if match else None


def _root_for(source: PermissionRuleSource, locations: SettingsLocations) -> str:
    """`getSettingsRootPathForSource`; the project root where TDDAgents' answer is the working directory."""
    root = settings_root_path_for_source(source, locations)
    return str(root if root is not None else locations.project_root)


def resolve_path_pattern_for_sandbox(pattern: str, source: PermissionRuleSource, locations: SettingsLocations) -> str:
    """
    `resolvePathPatternForSandbox` (permission-rule paths): `//x` is `/x` from the root, `/x` is under the
    settings file's root, anything else is left to the runtime.
    """
    if pattern.startswith("//"):
        return pattern[1:]
    if pattern.startswith("/"):
        return os.path.normpath(os.path.join(_root_for(source, locations), pattern[1:]))
    return pattern


def resolve_sandbox_filesystem_path(pattern: str, source: PermissionRuleSource, locations: SettingsLocations) -> str:
    """
    `resolveSandboxFilesystemPath` (`sandbox.filesystem.*`): standard path semantics, `/x` absolute, `~`
    expanded, relative to the settings file's root; `//x` accepted as `/x` for compatibility.
    """
    if pattern.startswith("//"):
        return pattern[1:]
    return expand_path(pattern, _root_for(source, locations))


def _sandbox_section(settings: Mapping[str, Any] | None) -> Mapping[str, Any]:
    sandbox = (settings or {}).get("sandbox")
    return sandbox if isinstance(sandbox, Mapping) else {}


def _section(parent: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = parent.get(key)
    return value if isinstance(value, Mapping) else {}


def _strings(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def should_allow_managed_sandbox_domains_only(locations: SettingsLocations) -> bool:
    """`shouldAllowManagedSandboxDomainsOnly`: policy settings' `sandbox.network.allowManagedDomainsOnly`."""
    policy = get_settings_for_source(PermissionRuleSource.POLICY_SETTINGS, locations)
    return _section(_sandbox_section(policy), "network").get("allowManagedDomainsOnly") is True


def should_allow_managed_read_paths_only(locations: SettingsLocations) -> bool:
    """`shouldAllowManagedReadPathsOnly`."""
    policy = get_settings_for_source(PermissionRuleSource.POLICY_SETTINGS, locations)
    return _section(_sandbox_section(policy), "filesystem").get("allowManagedReadPathsOnly") is True


def _web_fetch_domains(rules: Sequence[str]) -> list[str]:
    domains: list[str] = []
    for rule_string in rules:
        tool, content = permission_rule_value_from_string(rule_string)
        if tool == WEB_FETCH_TOOL_NAME and content is not None and content.startswith("domain:"):
            domains.append(content[len("domain:"):])
    return domains


def detect_worktree_main_repo_path(cwd: str) -> str | None:
    """
    `detectWorktreeMainRepoPath`: in a git worktree `.git` is a file (`gitdir: <main>/.git/worktrees/<n>`);
    the main repository's path, or `None` (not a worktree, `.git` a directory, unreadable).
    """
    try:
        content = Path(cwd, ".git").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    match = re.search(r"^gitdir:\s*(.+)$", content, re.MULTILINE)
    if not match:
        return None
    gitdir = os.path.normpath(os.path.join(cwd, match.group(1).strip()))
    marker = f"{os.sep}.git{os.sep}worktrees{os.sep}"
    index = gitdir.rfind(marker)
    return gitdir[:index] if index > 0 else None


class SandboxAdapter:
    """
    `ISandboxManager` for one run: the settings over `locations`, the runtime manager below.
    `cwd_state` is `getCwdState` (the shell's tracked directory); the original cwd is the project root.
    """

    def __init__(
        self, locations: SettingsLocations, *, cwd_state: Callable[[], str] | None = None,
        home: str | None = None, platform: str | None = None, wsl_version: str | None = None,
        which: Which = which_sync, fs: SandboxFs = HOST_FS, run_ripgrep: RunRipgrep = rip_grep,
        start_network: StartNetwork = start_host_network, ripgrep_command: str | None = None,
        probe: Callable[[], str | None] = probe_bwrap, temp_dir: str | None = None,
    ) -> None:
        from app.utils.system_directories import get_platform as get_cli_platform

        self.locations = locations
        self.original_cwd = str(locations.project_root)
        self._cwd_state = cwd_state or (lambda: self.original_cwd)
        self.cli_platform = platform if platform is not None else get_cli_platform()
        self.temp_dir = temp_dir if temp_dir is not None else get_tddagents_temp_dir()
        self._ripgrep_command = ripgrep_command
        self._which = which
        self._probe = probe
        runtime_platform = "linux" if self.cli_platform in ("linux", "wsl") else (
            "macos" if self.cli_platform == "macos" else "unknown")
        self.base = SandboxManager(
            cwd=self.original_cwd, home=home if home is not None else os.path.expanduser("~"),
            tmpdir=self.temp_dir, platform=runtime_platform,  # type: ignore[arg-type]
            wsl_version=wsl_version, which=which, fs=fs, run_ripgrep=run_ripgrep, start_network=start_network)
        self._initialized = False
        self._settings_unsubscribe: Callable[[], None] | None = None
        self._worktree_main_repo_path: str | None = None
        self._worktree_resolved = False
        self._bare_git_repo_scrub_paths: list[str] = []
        self._dependencies: SandboxDependencyCheck | None = None
        #: The probe's error ("" when it passed), `None` before it ran.
        self._probe_failure: str | None = None

    # ── settings ─────────────────────────────────────────────────────────────

    def settings(self) -> dict[str, Any]:
        """`getSettings_DEPRECATED`: the merged settings, read now."""
        return load_settings(self.locations)

    def ripgrep_config(self) -> RipgrepConfig:
        """`ripgrepCommand()`: TDDAgents' vendored ripgrep (`app/utils/ripgrep.py`)."""
        if self._ripgrep_command is not None:
            return RipgrepConfig(command=self._ripgrep_command)
        from app.utils.ripgrep import builtin_ripgrep_path, is_env_defined_falsy

        command = "rg" if is_env_defined_falsy(os.environ.get("USE_BUILTIN_RIPGREP")) else builtin_ripgrep_path()
        return RipgrepConfig(command=command)

    def convert_to_sandbox_runtime_config(self, settings: Mapping[str, Any]) -> SandboxRuntimeConfig:
        """
        `convertToSandboxRuntimeConfig`: the network allowlist (sandbox domains and `WebFetch(domain:…)`
        allow rules, only the policy's under `allowManagedDomainsOnly`) and denylist; the writable paths
        (the project, the temp directory, the worktree's main repository, the additional directories, the
        `Edit` allow rules and `sandbox.filesystem.allowWrite`); the denied writes (every settings file,
        the managed drop-in directory, the skills directory, the existing bare-repo files, the `Edit` deny
        rules, `denyWrite`); the denied and re-allowed reads; ripgrep.
        """
        locations = self.locations
        permissions = _section(settings, "permissions")
        sandbox = _sandbox_section(settings)
        network = _section(sandbox, "network")

        allowed_domains: list[str] = []
        if should_allow_managed_sandbox_domains_only(locations):
            policy = get_settings_for_source(PermissionRuleSource.POLICY_SETTINGS, locations) or {}
            allowed_domains += _strings(_section(_sandbox_section(policy), "network").get("allowedDomains"))
            allowed_domains += _web_fetch_domains(_strings(_section(policy, "permissions").get("allow")))
        else:
            allowed_domains += _strings(network.get("allowedDomains"))
            allowed_domains += _web_fetch_domains(_strings(permissions.get("allow")))
        denied_domains = _web_fetch_domains(_strings(permissions.get("deny")))

        allow_write: list[str] = [".", self.temp_dir]
        deny_write: list[str] = []
        deny_read: list[str] = []
        allow_read: list[str] = []

        # Every settings file, so a sandboxed command cannot rewrite its own sandbox.
        for source in SETTING_SOURCES:
            path = settings_file_path_for_source(source, locations)
            if path is not None:
                deny_write.append(str(path))
        deny_write.append(str(locations.managed_dir / MANAGED_DROP_IN_DIRNAME))

        cwd, original_cwd = self._cwd_state(), self.original_cwd
        if cwd != original_cwd:
            deny_write.append(os.path.join(cwd, SETTINGS_DIR, "settings.json"))
            deny_write.append(os.path.join(cwd, SETTINGS_DIR, "settings.local.json"))
        deny_write.append(os.path.join(original_cwd, SETTINGS_DIR, "skills"))
        if cwd != original_cwd:
            deny_write.append(os.path.join(cwd, SETTINGS_DIR, "skills"))

        # A planted HEAD + objects/ + refs/ makes git treat the directory as a bare repository (and run
        # its config's fsmonitor): an existing file is denied in place, a missing one scrubbed afterwards.
        self._bare_git_repo_scrub_paths.clear()
        for directory in ([original_cwd] if cwd == original_cwd else [original_cwd, cwd]):
            for git_file in BARE_GIT_REPO_FILES:
                bare_path = os.path.join(directory, git_file)
                if os.path.exists(bare_path):
                    deny_write.append(bare_path)
                else:
                    self._bare_git_repo_scrub_paths.append(bare_path)

        if self._worktree_main_repo_path and self._worktree_main_repo_path != cwd:
            allow_write.append(self._worktree_main_repo_path)

        additional = dict.fromkeys([*_strings(permissions.get("additionalDirectories")),
                                    *locations.additional_directories])
        allow_write.extend(additional)

        managed_read_only = should_allow_managed_read_paths_only(locations)
        for source in SETTING_SOURCES:
            source_settings = get_settings_for_source(source, locations)
            source_permissions = _section(source_settings or {}, "permissions")
            for rule_string in _strings(source_permissions.get("allow")):
                tool, content = permission_rule_value_from_string(rule_string)
                if tool == FILE_EDIT_TOOL_NAME and content:
                    allow_write.append(resolve_path_pattern_for_sandbox(content, source, locations))
            for rule_string in _strings(source_permissions.get("deny")):
                tool, content = permission_rule_value_from_string(rule_string)
                if tool == FILE_EDIT_TOOL_NAME and content:
                    deny_write.append(resolve_path_pattern_for_sandbox(content, source, locations))
                if tool == FILE_READ_TOOL_NAME and content:
                    deny_read.append(resolve_path_pattern_for_sandbox(content, source, locations))
            fs_settings = _section(_sandbox_section(source_settings), "filesystem")
            if fs_settings:
                allow_write += [resolve_sandbox_filesystem_path(p, source, locations)
                                for p in _strings(fs_settings.get("allowWrite"))]
                deny_write += [resolve_sandbox_filesystem_path(p, source, locations)
                               for p in _strings(fs_settings.get("denyWrite"))]
                deny_read += [resolve_sandbox_filesystem_path(p, source, locations)
                              for p in _strings(fs_settings.get("denyRead"))]
                if not managed_read_only or source == PermissionRuleSource.POLICY_SETTINGS:
                    allow_read += [resolve_sandbox_filesystem_path(p, source, locations)
                                   for p in _strings(fs_settings.get("allowRead"))]

        ripgrep_setting = _section(sandbox, "ripgrep")
        ripgrep = (RipgrepConfig(command=str(ripgrep_setting["command"]), args=_strings(ripgrep_setting.get("args")),
                                 argv0=None) if isinstance(ripgrep_setting.get("command"), str)
                   else self.ripgrep_config())

        def opt_bool(mapping: Mapping[str, Any], key: str) -> bool | None:
            value = mapping.get(key)
            return value if isinstance(value, bool) else None

        def opt_int(key: str) -> int | None:
            value = network.get(key)
            return value if isinstance(value, int) and not isinstance(value, bool) else None

        ignore = sandbox.get("ignoreViolations")
        return SandboxRuntimeConfig(
            network=NetworkConfig(
                allowed_domains=allowed_domains,
                denied_domains=denied_domains,
                allow_unix_sockets=_strings(network["allowUnixSockets"]) if "allowUnixSockets" in network else None,
                allow_all_unix_sockets=opt_bool(network, "allowAllUnixSockets"),
                allow_local_binding=opt_bool(network, "allowLocalBinding"),
                http_proxy_port=opt_int("httpProxyPort"),
                socks_proxy_port=opt_int("socksProxyPort"),
            ),
            filesystem=FilesystemConfig(deny_read=deny_read, allow_read=allow_read, allow_write=allow_write,
                                        deny_write=deny_write),
            ignore_violations={str(k): _strings(v) for k, v in ignore.items()} if isinstance(ignore, Mapping) else None,
            enable_weaker_nested_sandbox=opt_bool(sandbox, "enableWeakerNestedSandbox"),
            enable_weaker_network_isolation=opt_bool(sandbox, "enableWeakerNetworkIsolation"),
            ripgrep=ripgrep,
        )

    # ── enablement ───────────────────────────────────────────────────────────

    def check_dependencies(self) -> SandboxDependencyCheck:
        """
        `checkDependencies` (memoized): the runtime's check for ripgrep, bwrap, socat and seccomp, then,
        on a supported platform with no error and a sandbox the settings enable, the one-off bwrap probe
        (TDDAgents, see the docstring; memoized on its own, so a disabled sandbox never spawns bwrap).
        """
        if self._dependencies is None:
            self._dependencies = self.base.check_dependencies(self.ripgrep_config())
        deps = self._dependencies
        if deps.errors or not self.base.is_supported_platform() or not self.is_sandbox_enabled_in_settings():
            return deps
        if self._probe_failure is None:
            self._probe_failure = self._probe() or ""
        if not self._probe_failure:
            return deps
        return SandboxDependencyCheck(errors=[self._probe_failure], warnings=list(deps.warnings))

    def is_sandbox_enabled_in_settings(self) -> bool:
        """`getSandboxEnabledSetting`: `sandbox.enabled`, false by default (and on any failure to read)."""
        try:
            return _sandbox_section(self.settings()).get("enabled") is True
        except Exception as error:  # noqa: BLE001 - upstream treats unreadable settings as disabled
            logger.debug("Failed to get settings for sandbox check: %s", error)
            return False

    def is_auto_allow_bash_if_sandboxed_enabled(self) -> bool:
        """`isAutoAllowBashIfSandboxedEnabled`: true unless set false."""
        return _sandbox_section(self.settings()).get("autoAllowBashIfSandboxed") is not False

    def are_unsandboxed_commands_allowed(self) -> bool:
        """`areUnsandboxedCommandsAllowed`: true unless set false."""
        return _sandbox_section(self.settings()).get("allowUnsandboxedCommands") is not False

    def is_sandbox_required(self) -> bool:
        """`isSandboxRequired`: enabled and `failIfUnavailable`."""
        return self.is_sandbox_enabled_in_settings() and _sandbox_section(self.settings()).get(
            "failIfUnavailable") is True

    def is_supported_platform(self) -> bool:
        """`isSupportedPlatform`: the runtime's (Linux and WSL2+; macOS is not ported)."""
        return self.base.is_supported_platform()

    def is_platform_in_enabled_list(self) -> bool:
        """`isPlatformInEnabledList`: the undocumented `sandbox.enabledPlatforms`; unset allows all, empty none."""
        try:
            enabled = _sandbox_section(self.settings()).get("enabledPlatforms")
        except Exception as error:  # noqa: BLE001 - upstream defaults to enabled when it cannot read
            logger.debug("Failed to check enabledPlatforms: %s", error)
            return True
        if not isinstance(enabled, list):
            return True
        return self.cli_platform in enabled

    def is_sandboxing_enabled(self) -> bool:
        """`isSandboxingEnabled`: a supported platform, no dependency error, listed, and enabled in settings."""
        if not self.is_supported_platform():
            return False
        if self.check_dependencies().errors:
            return False
        if not self.is_platform_in_enabled_list():
            return False
        return self.is_sandbox_enabled_in_settings()

    def get_sandbox_unavailable_reason(self) -> str | None:
        """
        `getSandboxUnavailableReason`: why a sandbox the settings enable cannot run (platform, the enabled
        platforms, missing dependencies), or `None`; nothing when the settings never enabled it.
        """
        if not self.is_sandbox_enabled_in_settings():
            return None
        platform = self.cli_platform
        if not self.is_supported_platform():
            if platform == "wsl":
                return "sandbox.enabled is set but WSL1 is not supported (requires WSL2)"
            return f"sandbox.enabled is set but {platform} is not supported (requires macOS, Linux, or WSL2)"
        if not self.is_platform_in_enabled_list():
            return f"sandbox.enabled is set but {platform} is not in sandbox.enabledPlatforms"
        deps = self.check_dependencies()
        if deps.errors:
            hint = MACOS_DEPENDENCY_HINT if platform == "macos" else LINUX_DEPENDENCY_HINT
            return f"sandbox.enabled is set but dependencies are missing: {', '.join(deps.errors)} · {hint}"
        return None

    def get_linux_glob_pattern_warnings(self) -> list[str]:
        """`getLinuxGlobPatternWarnings`: `Edit`/`Read` rules whose globs bwrap cannot apply (Linux, enabled)."""
        if self.cli_platform not in ("linux", "wsl"):
            return []
        try:
            settings = self.settings()
            if _sandbox_section(settings).get("enabled") is not True:
                return []
            permissions = _section(settings, "permissions")
            warnings: list[str] = []
            for rule_string in [*_strings(permissions.get("allow")), *_strings(permissions.get("deny"))]:
                tool, content = permission_rule_value_from_string(rule_string)
                if tool in (FILE_EDIT_TOOL_NAME, FILE_READ_TOOL_NAME) and content and re.search(
                        r"[*?\[\]]", re.sub(r"/\*\*$", "", content)):
                    warnings.append(rule_string)
            return warnings
        except Exception as error:  # noqa: BLE001 - upstream logs and returns no warnings
            logger.debug("Failed to get Linux glob pattern warnings: %s", error)
            return []

    def are_sandbox_settings_locked_by_policy(self) -> bool:
        """`areSandboxSettingsLockedByPolicy`: flag or policy settings set any of the three switches."""
        for source in (PermissionRuleSource.FLAG_SETTINGS, PermissionRuleSource.POLICY_SETTINGS):
            sandbox = _sandbox_section(get_settings_for_source(source, self.locations))
            if any(key in sandbox for key in ("enabled", "autoAllowBashIfSandboxed", "allowUnsandboxedCommands")):
                return True
        return False

    def set_sandbox_settings(self, *, enabled: bool | None = None, auto_allow_bash_if_sandboxed: bool | None = None,
                             allow_unsandboxed_commands: bool | None = None) -> None:
        """`setSandboxSettings`: the given switches written to the local settings."""
        from app.utils.permissions.permission_update import update_settings_for_source

        existing = _sandbox_section(get_settings_for_source(PermissionRuleSource.LOCAL_SETTINGS, self.locations))
        sandbox = dict(existing)
        for key, value in (("enabled", enabled), ("autoAllowBashIfSandboxed", auto_allow_bash_if_sandboxed),
                           ("allowUnsandboxedCommands", allow_unsandboxed_commands)):
            if value is not None:
                sandbox[key] = value
        update_settings_for_source(PermissionRuleSource.LOCAL_SETTINGS, self.locations, {"sandbox": sandbox})

    def get_excluded_commands(self) -> list[str]:
        """`getExcludedCommands`: `sandbox.excludedCommands`."""
        return _strings(_sandbox_section(self.settings()).get("excludedCommands"))

    # ── lifecycle ────────────────────────────────────────────────────────────

    def wrap_with_sandbox(self, command: str, bin_shell: str | None = None) -> str:
        """`wrapWithSandbox`: an enabled sandbox must have been initialized first."""
        if self.is_sandboxing_enabled() and not self._initialized:
            raise RuntimeError("Sandbox failed to initialize. ")
        return self.base.wrap_with_sandbox(command, bin_shell)

    def initialize(self, ask_callback: SandboxAskCallback | None = None, *,
                   ask_loop: asyncio.AbstractEventLoop | None = None, change_detector: Any = None) -> None:
        """
        `initialize`: nothing unless sandboxing is enabled; the worktree resolved once, the config built,
        the runtime started (an ask under `allowManagedDomainsOnly` is refused before it is asked), and
        the config refreshed whenever the run's settings change. A failure leaves it uninitialized and
        raises `SandboxUnavailableError` (upstream logs it; both of its callers then exit 1).
        """
        if self._initialized or not self.is_sandboxing_enabled():
            return
        wrapped: SandboxAskCallback | None = None
        if ask_callback is not None:
            callback = ask_callback

            async def wrapped_ask(pattern: Any) -> bool:
                if should_allow_managed_sandbox_domains_only(self.locations):
                    logger.debug("[sandbox] Blocked network request to %s (allowManagedDomainsOnly)", pattern["host"])
                    return False
                return await callback(pattern)

            wrapped = wrapped_ask
        try:
            if not self._worktree_resolved:
                self._worktree_main_repo_path = detect_worktree_main_repo_path(self._cwd_state())
                self._worktree_resolved = True
            self.base.initialize(self.convert_to_sandbox_runtime_config(self.settings()), wrapped, ask_loop)
        except Exception as error:
            raise SandboxUnavailableError(str(error)) from error
        self._initialized = True
        if change_detector is not None:
            self._settings_unsubscribe = change_detector.subscribe(lambda _source: self.refresh_config())

    def refresh_config(self) -> None:
        """`refreshConfig`: the runtime config rebuilt from the settings now (after a rule was persisted)."""
        if not self.is_sandboxing_enabled():
            return
        self.base.update_config(self.convert_to_sandbox_runtime_config(self.settings()))
        logger.debug("Sandbox configuration updated from settings change")

    def reset(self) -> None:
        """`reset`: the subscription dropped, the caches forgotten, the runtime reset."""
        if self._settings_unsubscribe is not None:
            self._settings_unsubscribe()
            self._settings_unsubscribe = None
        self._worktree_main_repo_path = None
        self._worktree_resolved = False
        self._bare_git_repo_scrub_paths.clear()
        self._dependencies = None
        self._probe_failure = None
        self._initialized = False
        self.base.reset()

    def cleanup_after_command(self) -> None:
        """`cleanupAfterCommand`: the runtime's mount points, then the bare-repo files a command planted."""
        self.base.cleanup_after_command()
        for path in self._bare_git_repo_scrub_paths:
            with contextlib.suppress(OSError):
                if os.path.isdir(path) and not os.path.islink(path):
                    shutil.rmtree(path)
                else:
                    os.unlink(path)
                logger.debug("[Sandbox] scrubbed planted bare-repo file: %s", path)

    # ── forwarded to the runtime ─────────────────────────────────────────────

    def get_fs_read_config(self) -> FsReadRestrictionConfig:
        return self.base.get_fs_read_config()

    def get_fs_write_config(self) -> FsWriteRestrictionConfig:
        return self.base.get_fs_write_config()

    def get_network_restriction_config(self) -> NetworkRestrictionConfig:
        return self.base.get_network_restriction_config()

    def get_ignore_violations(self) -> dict[str, list[str]] | None:
        return self.base.get_ignore_violations()

    def get_allow_unix_sockets(self) -> list[str] | None:
        return self.base.get_allow_unix_sockets()

    def get_allow_local_binding(self) -> bool | None:
        return self.base.get_allow_local_binding()

    def get_enable_weaker_nested_sandbox(self) -> bool | None:
        return self.base.get_enable_weaker_nested_sandbox()

    def get_proxy_port(self) -> int | None:
        return self.base.get_proxy_port()

    def get_socks_proxy_port(self) -> int | None:
        return self.base.get_socks_proxy_port()

    def get_linux_http_socket_path(self) -> str | None:
        return self.base.get_linux_http_socket_path()

    def get_linux_socks_socket_path(self) -> str | None:
        return self.base.get_linux_socks_socket_path()

    def wait_for_network_initialization(self) -> bool:
        return self.base.wait_for_network_initialization()

    def get_sandbox_violation_store(self) -> SandboxViolationStore:
        return self.base.get_sandbox_violation_store()

    def annotate_stderr_with_sandbox_failures(self, command: str, stderr: str) -> str:
        return self.base.annotate_stderr_with_sandbox_failures(command, stderr)


def add_to_excluded_commands(locations: SettingsLocations, command: str,
                             permission_updates: Sequence[Mapping[str, Any]] | None = None) -> str:
    """
    `addToExcludedCommands`: the command (or the prefix of the first Bash rule a suggestion carries)
    appended to the local settings' `sandbox.excludedCommands`, once. Returns the pattern.
    """
    from app.utils.permissions.permission_update import update_settings_for_source

    existing = _sandbox_section(get_settings_for_source(PermissionRuleSource.LOCAL_SETTINGS, locations))
    excluded = _strings(existing.get("excludedCommands"))
    pattern = command
    for update in permission_updates or ():
        rules = update.get("rules") or []
        if update.get("type") == "addRules" and any(rule.get("toolName") == BASH_TOOL_NAME for rule in rules):
            first = next(rule for rule in rules if rule.get("toolName") == BASH_TOOL_NAME)
            content = first.get("ruleContent")
            if content:
                pattern = permission_rule_extract_prefix(content) or content
            break
    if pattern not in excluded:
        update_settings_for_source(PermissionRuleSource.LOCAL_SETTINGS, locations,
                                   {"sandbox": {**existing, "excludedCommands": [*excluded, pattern]}})
    return pattern


__all__ = [
    "BARE_GIT_REPO_FILES",
    "LINUX_DEPENDENCY_HINT",
    "SandboxAdapter",
    "SandboxUnavailableError",
    "add_to_excluded_commands",
    "detect_worktree_main_repo_path",
    "permission_rule_extract_prefix",
    "permission_rule_value_from_string",
    "resolve_path_pattern_for_sandbox",
    "resolve_sandbox_filesystem_path",
    "should_allow_managed_read_paths_only",
    "should_allow_managed_sandbox_domains_only",
]
