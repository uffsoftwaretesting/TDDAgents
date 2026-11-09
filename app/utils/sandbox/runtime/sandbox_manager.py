"""
The sandbox manager: the session's network filter and proxies, and the command wrapper.

Ported from `@anthropic-ai/sandbox-runtime` 0.0.46 `src/sandbox/sandbox-manager.ts` -> `SandboxManager`
(`initialize`, `isSupportedPlatform`, `isSandboxingEnabled`, `checkDependencies`, `getFsReadConfig`,
`getFsWriteConfig`, `getNetworkRestrictionConfig`, the getters, `waitForNetworkInitialization`,
`wrapWithSandbox`, `getConfig`, `updateConfig`, `cleanupAfterCommand`, `reset`,
`getSandboxViolationStore`, `annotateStderrWithSandboxFailures`, `getLinuxGlobPatternWarnings`) and its
private helpers `matchesDomainPattern`, `filterNetworkRequest`, `getMitmSocketPath`.

Divergences:
* **One instance per run**, not module state: the session's manager and the final run's are separate.
  The working directory, the home and the sandbox temp directory are constructor arguments (upstream
  reads `process.cwd()`, `os.homedir()` and `/tmp/claude`).
* **Linux only.** macOS's seatbelt profile and its log monitor are not ported (user decision 2026-11-08),
  so `is_supported_platform` is Linux (and WSL2+) alone, and `wrap_with_sandbox` refuses other platforms
  with upstream's error.
* **Synchronous** `initialize`, `wrap_with_sandbox` and `reset`: the network infrastructure runs on its
  own thread (`network_host.py`), so the caller waits for it to start instead of awaiting a promise; an
  ask callback is called on `ask_loop` (the caller's) from the proxies' loop.
* `customConfig` and `abortSignal` of `wrapWithSandbox` are not ported: no caller passes them.
"""

from __future__ import annotations

import asyncio
import atexit
import copy
import logging
import os
from collections.abc import Callable

from app.utils.sandbox.runtime.linux_sandbox_utils import (
    DEFAULT_MANDATORY_DENY_SEARCH_DEPTH,
    LinuxSandboxParams,
    RunRipgrep,
    SandboxDependencyCheck,
    Which,
    check_linux_dependencies,
    cleanup_bwrap_mount_points,
    wrap_command_with_sandbox_linux,
)
from app.utils.sandbox.runtime.network_host import HostNetworkContext, call_on_loop, start_host_network
from app.utils.sandbox.runtime.platform import Platform, get_platform, get_wsl_version
from app.utils.sandbox.runtime.ripgrep import rip_grep
from app.utils.sandbox.runtime.sandbox_config import RipgrepConfig, SandboxRuntimeConfig, SeccompConfig
from app.utils.sandbox.runtime.sandbox_schemas import (
    FsReadRestrictionConfig,
    FsWriteRestrictionConfig,
    NetworkHostPattern,
    NetworkRestrictionConfig,
    SandboxAskCallback,
)
from app.utils.sandbox.runtime.sandbox_utils import (
    HOST_FS,
    SandboxFs,
    contains_glob_chars,
    expand_glob_pattern,
    get_default_write_paths,
    remove_trailing_glob_suffix,
)
from app.utils.sandbox.runtime.sandbox_violation_store import SandboxViolationStore
from app.utils.sandbox.runtime.which import which_sync

logger = logging.getLogger("TDDOrchestrator.Sandbox")

StartNetwork = Callable[..., HostNetworkContext]


class SandboxDependencyError(RuntimeError):
    """`Sandbox dependencies not available: <errors>` (`initialize`)."""


def matches_domain_pattern(hostname: str, pattern: str) -> bool:
    """`matchesDomainPattern`: `*.example.com` matches any subdomain (not the base); otherwise exact, any case."""
    if pattern.startswith("*."):
        return hostname.lower().endswith("." + pattern[2:].lower())
    return hostname.lower() == pattern.lower()


class SandboxManager:
    """`SandboxManager` for one run (see the module docstring)."""

    def __init__(
        self, *, cwd: str, home: str, tmpdir: str, platform: Platform | None = None,
        wsl_version: str | None = None, which: Which = which_sync, fs: SandboxFs = HOST_FS,
        run_ripgrep: RunRipgrep = rip_grep, start_network: StartNetwork = start_host_network,
    ) -> None:
        self.cwd, self.home, self.tmpdir = cwd, home, tmpdir
        self.platform: Platform = platform if platform is not None else get_platform()
        self._wsl_version = wsl_version if platform is not None else get_wsl_version()
        self._which, self._fs, self._run_ripgrep, self._start_network = which, fs, run_ripgrep, start_network
        self._config: SandboxRuntimeConfig | None = None
        self._context: HostNetworkContext | None = None
        self._ask: SandboxAskCallback | None = None
        self._cleanup_registered = False
        self._violation_store = SandboxViolationStore()

    # ── network filtering ────────────────────────────────────────────────────

    async def filter_network_request(self, port: int, host: str, ask: SandboxAskCallback | None = None) -> bool:
        """
        `filterNetworkRequest`: denied domains first, then allowed ones; no match asks (`ask`) or denies.
        A failing ask denies.
        """
        config = self._config
        if config is None:
            logger.debug("No config available, denying network request")
            return False
        if any(matches_domain_pattern(host, denied) for denied in config.network.denied_domains):
            logger.debug("Denied by config rule: %s:%s", host, port)
            return False
        if any(matches_domain_pattern(host, allowed) for allowed in config.network.allowed_domains):
            logger.debug("Allowed by config rule: %s:%s", host, port)
            return True
        if ask is None:
            logger.debug("No matching config rule, denying: %s:%s", host, port)
            return False
        logger.debug("No matching config rule, asking user: %s:%s", host, port)
        try:
            allowed = bool(await ask(NetworkHostPattern(host=host, port=port)))
        except Exception as error:  # noqa: BLE001 - upstream denies when the permission callback fails
            logger.error("Error in permission callback: %s", error)
            return False
        logger.debug("User %s: %s:%s", "allowed" if allowed else "denied", host, port)
        return allowed

    def get_mitm_socket_path(self, host: str) -> str | None:
        """`getMitmSocketPath`: the MITM proxy's socket when `host` matches one of its domains."""
        mitm = self._config.network.mitm_proxy if self._config is not None else None
        if mitm is None:
            return None
        if any(matches_domain_pattern(host, pattern) for pattern in mitm.domains):
            return mitm.socket_path
        return None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def initialize(self, runtime_config: SandboxRuntimeConfig, ask_callback: SandboxAskCallback | None = None,
                   ask_loop: asyncio.AbstractEventLoop | None = None) -> None:
        """
        `initialize`: the config stored, the dependencies checked (an error raises), the cleanup registered,
        then the proxies (or the external ports) and, on Linux, the bridge. A second call does nothing.
        """
        if self._context is not None:
            return
        self._config = runtime_config
        deps = self.check_dependencies()
        if deps.errors:
            raise SandboxDependencyError(f"Sandbox dependencies not available: {', '.join(deps.errors)}")
        if not self._cleanup_registered:
            atexit.register(self.reset)
            self._cleanup_registered = True
        self._ask = call_on_loop(ask_callback, ask_loop) if ask_callback is not None else None
        ask = self._ask

        async def filter_request(port: int, host: str) -> bool:
            return await self.filter_network_request(port, host, ask)

        network = runtime_config.network
        self._context = self._start_network(
            filter_request, get_mitm_socket_path=self.get_mitm_socket_path,
            http_proxy_port=network.http_proxy_port, socks_proxy_port=network.socks_proxy_port,
            linux=self.platform == "linux")

    def is_supported_platform(self) -> bool:
        """`isSupportedPlatform`: Linux, but not WSL1 (bubblewrap needs a real kernel). macOS is not ported."""
        return self.platform == "linux" and self._wsl_version != "1"

    def is_sandboxing_enabled(self) -> bool:
        """`isSandboxingEnabled`: a config has been set (by `initialize`)."""
        return self._config is not None

    def check_dependencies(self, ripgrep_config: RipgrepConfig | None = None) -> SandboxDependencyCheck:
        """`checkDependencies`: the platform, ripgrep, then (Linux) bwrap, socat and the seccomp files."""
        if not self.is_supported_platform():
            return SandboxDependencyCheck(errors=["Unsupported platform"], warnings=[])
        errors: list[str] = []
        warnings: list[str] = []
        rg = ripgrep_config or (self._config.ripgrep if self._config else None) or RipgrepConfig(command="rg")
        if self._which(rg.command) is None:
            errors.append(f"ripgrep ({rg.command}) not found")
        linux = check_linux_dependencies(self._config.seccomp if self._config else None, self._which)
        errors.extend(linux.errors)
        warnings.extend(linux.warnings)
        return SandboxDependencyCheck(errors=errors, warnings=warnings)

    def wait_for_network_initialization(self) -> bool:
        """`waitForNetworkInitialization`: whether the network infrastructure is up (it starts synchronously)."""
        if self._config is None:
            return False
        return self._context is not None

    def cleanup_after_command(self) -> None:
        """
        `cleanupAfterCommand`: one wrapped command is done; the mount points bwrap left on the host are
        removed once no other wrapped command is still running.
        """
        cleanup_bwrap_mount_points()

    def reset(self) -> None:
        """
        `reset`: the mount points (forced past the active-sandbox count: the session is over), then the
        bridge, the proxies and their loop; the manager is uninitialized.
        """
        cleanup_bwrap_mount_points(force=True)
        context, self._context = self._context, None
        if context is not None:
            context.close()

    # ── configuration ────────────────────────────────────────────────────────

    def _expand(self, paths: list[str]) -> list[str]:
        """Each path without its trailing `/**`; a glob expanded to the paths it matches (bwrap takes none)."""
        expanded: list[str] = []
        for path in paths:
            stripped = remove_trailing_glob_suffix(path)
            if self.platform == "linux" and contains_glob_chars(stripped):
                matches = expand_glob_pattern(path, cwd=self.cwd, home=self.home, fs=self._fs)
                logger.debug('[Sandbox] Expanded glob pattern "%s" to %s paths on Linux', path, len(matches))
                expanded.extend(matches)
            else:
                expanded.append(stripped)
        return expanded

    def _strip_write_globs(self, paths: list[str]) -> list[str]:
        """Each path without its trailing `/**`; on Linux a remaining glob is skipped."""
        kept: list[str] = []
        for path in paths:
            stripped = remove_trailing_glob_suffix(path)
            if self.platform == "linux" and contains_glob_chars(stripped):
                logger.debug("[Sandbox] Skipping glob write pattern on Linux: %s", stripped)
                continue
            kept.append(stripped)
        return kept

    def default_write_paths(self) -> list[str]:
        """`getDefaultWritePaths` for this manager's home and temp directory."""
        return get_default_write_paths(home=self.home, tmpdir=self.tmpdir)

    def get_fs_read_config(self) -> FsReadRestrictionConfig:
        """`getFsReadConfig`."""
        if self._config is None:
            return FsReadRestrictionConfig(deny_only=[], allow_within_deny=[])
        return FsReadRestrictionConfig(deny_only=self._expand(self._config.filesystem.deny_read),
                                       allow_within_deny=self._expand(self._config.filesystem.allow_read or []))

    def get_fs_write_config(self) -> FsWriteRestrictionConfig:
        """`getFsWriteConfig`: the default write paths, then the configured ones."""
        if self._config is None:
            return FsWriteRestrictionConfig(allow_only=self.default_write_paths(), deny_within_allow=[])
        return FsWriteRestrictionConfig(
            allow_only=[*self.default_write_paths(), *self._strip_write_globs(self._config.filesystem.allow_write)],
            deny_within_allow=self._strip_write_globs(self._config.filesystem.deny_write))

    def get_network_restriction_config(self) -> NetworkRestrictionConfig:
        """`getNetworkRestrictionConfig`: only the non-empty lists."""
        if self._config is None:
            return NetworkRestrictionConfig()
        allowed, denied = self._config.network.allowed_domains, self._config.network.denied_domains
        return NetworkRestrictionConfig(allowed_hosts=list(allowed) or None, denied_hosts=list(denied) or None)

    def get_allow_unix_sockets(self) -> list[str] | None:
        return self._config.network.allow_unix_sockets if self._config else None

    def get_allow_all_unix_sockets(self) -> bool | None:
        return self._config.network.allow_all_unix_sockets if self._config else None

    def get_allow_local_binding(self) -> bool | None:
        return self._config.network.allow_local_binding if self._config else None

    def get_ignore_violations(self) -> dict[str, list[str]] | None:
        return self._config.ignore_violations if self._config else None

    def get_enable_weaker_nested_sandbox(self) -> bool | None:
        return self._config.enable_weaker_nested_sandbox if self._config else None

    def get_ripgrep_config(self) -> RipgrepConfig:
        return (self._config.ripgrep if self._config else None) or RipgrepConfig(command="rg")

    def get_mandatory_deny_search_depth(self) -> int:
        depth = self._config.mandatory_deny_search_depth if self._config else None
        return depth if depth is not None else DEFAULT_MANDATORY_DENY_SEARCH_DEPTH

    def get_allow_git_config(self) -> bool:
        return bool(self._config.filesystem.allow_git_config) if self._config else False

    def get_seccomp_config(self) -> SeccompConfig | None:
        return self._config.seccomp if self._config else None

    def get_proxy_port(self) -> int | None:
        return self._context.http_proxy_port if self._context else None

    def get_socks_proxy_port(self) -> int | None:
        return self._context.socks_proxy_port if self._context else None

    def get_linux_http_socket_path(self) -> str | None:
        bridge = self._context.linux_bridge if self._context else None
        return bridge.http_socket_path if bridge else None

    def get_linux_socks_socket_path(self) -> str | None:
        bridge = self._context.linux_bridge if self._context else None
        return bridge.socks_socket_path if bridge else None

    def get_config(self) -> SandboxRuntimeConfig | None:
        """`getConfig`."""
        return self._config

    def update_config(self, new_config: SandboxRuntimeConfig) -> None:
        """`updateConfig`: a deep copy, so later changes to `new_config` do not leak in."""
        self._config = copy.deepcopy(new_config)
        logger.debug("Sandbox configuration updated")

    # ── wrapping ─────────────────────────────────────────────────────────────

    def wrap_with_sandbox(self, command: str, bin_shell: str | None = None) -> str:
        """
        `wrapWithSandbox`: the write config (the defaults plus the configured paths, globs stripped), the
        read config (globs expanded), and the network restricted and proxied whenever a config is set (an
        empty allowlist blocks everything, and the proxy lets `update_config` open hosts later).
        """
        config = self._config
        write_config = FsWriteRestrictionConfig(
            allow_only=[*self.default_write_paths(),
                        *self._strip_write_globs(config.filesystem.allow_write if config else [])],
            deny_within_allow=self._strip_write_globs(config.filesystem.deny_write if config else []))
        read_config = FsReadRestrictionConfig(
            deny_only=self._expand(config.filesystem.deny_read if config else []),
            allow_within_deny=self._expand((config.filesystem.allow_read or []) if config else []))
        needs_network = config is not None
        if needs_network:
            self.wait_for_network_initialization()
        if self.platform != "linux":
            raise RuntimeError(f"Sandbox configuration is not supported on platform: {self.platform}")
        return wrap_command_with_sandbox_linux(LinuxSandboxParams(
            command=command,
            needs_network_restriction=needs_network,
            cwd=self.cwd, home=self.home, tmpdir=self.tmpdir,
            http_socket_path=self.get_linux_http_socket_path() if needs_network else None,
            socks_socket_path=self.get_linux_socks_socket_path() if needs_network else None,
            http_proxy_port=self.get_proxy_port() if needs_network else None,
            socks_proxy_port=self.get_socks_proxy_port() if needs_network else None,
            read_config=read_config,
            write_config=write_config,
            enable_weaker_nested_sandbox=self.get_enable_weaker_nested_sandbox(),
            allow_all_unix_sockets=self.get_allow_all_unix_sockets(),
            bin_shell=bin_shell,
            ripgrep_config=self.get_ripgrep_config(),
            mandatory_deny_search_depth=self.get_mandatory_deny_search_depth(),
            allow_git_config=self.get_allow_git_config(),
            seccomp_config=self.get_seccomp_config(),
            fs=self._fs, which=self._which, run_ripgrep=self._run_ripgrep,
        ))

    # ── violations ───────────────────────────────────────────────────────────

    def get_sandbox_violation_store(self) -> SandboxViolationStore:
        return self._violation_store

    def annotate_stderr_with_sandbox_failures(self, command: str, stderr: str) -> str:
        """`annotateStderrWithSandboxFailures`: the command's violations appended in `<sandbox_violations>`."""
        if self._config is None:
            return stderr
        violations = self._violation_store.get_violations_for_command(command)
        if not violations:
            return stderr
        lines = "".join(v.line + os.linesep for v in violations)
        return f"{stderr}{os.linesep}<sandbox_violations>{os.linesep}{lines}</sandbox_violations>"

    def get_linux_glob_pattern_warnings(self) -> list[str]:
        """`getLinuxGlobPatternWarnings`: write paths that keep a glob after their trailing `/**` is stripped."""
        if self.platform != "linux" or self._config is None:
            return []
        paths = [*self._config.filesystem.allow_write, *self._config.filesystem.deny_write]
        return [path for path in paths if contains_glob_chars(remove_trailing_glob_suffix(path))]


__all__ = [
    "SandboxDependencyError",
    "SandboxManager",
    "matches_domain_pattern",
]
