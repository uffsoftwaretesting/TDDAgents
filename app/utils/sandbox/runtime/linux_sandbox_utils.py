"""
The bubblewrap command line: filesystem binds, mandatory denies, the network namespace and seccomp.

Ported from `@anthropic-ai/sandbox-runtime` 0.0.44 `src/sandbox/linux-sandbox-utils.ts` ->
`LinuxSandboxParams`, `findSymlinkInPath`, `hasFileAncestor`, `findFirstNonExistentComponent`,
`linuxGetMandatoryDenyPaths`, `cleanupBwrapMountPoints`, `getLinuxDependencyStatus`,
`checkLinuxDependencies`, `buildSandboxCommand`, `generateFilesystemArgs`, `wrapCommandWithSandboxLinux`.
`initializeLinuxNetworkBridge` (the host's socat processes) is in `linux_network_bridge.py`, the
process seam.

Divergences:
* Synchronous: upstream's `async` is the ripgrep scan's; here `run_ripgrep` blocks for it (10 s
  at most). There is no `abortSignal`.
* The working directory, the home, the filesystem (`SandboxFs`), `which` and the ripgrep runner are
  parameters, so the argv can be built in a test with nothing real behind it.
* Quoting uses `shlex` where upstream uses `shell-quote`: both produce POSIX shell words with the same
  meaning, not the same characters.
"""

from __future__ import annotations

import atexit
import logging
import posixpath
import shlex
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final

from app.utils.sandbox.runtime.generate_seccomp_filter import (
    cleanup_seccomp_filter,
    generate_seccomp_filter,
    get_apply_seccomp_binary_path,
    get_pre_generated_bpf_path,
)
from app.utils.sandbox.runtime.ripgrep import rip_grep
from app.utils.sandbox.runtime.sandbox_config import RipgrepConfig, SeccompConfig
from app.utils.sandbox.runtime.sandbox_schemas import FsReadRestrictionConfig, FsWriteRestrictionConfig
from app.utils.sandbox.runtime.sandbox_utils import (
    DANGEROUS_FILES,
    HOST_FS,
    SandboxFs,
    generate_proxy_env_vars,
    get_dangerous_directories,
    is_symlink_outside_boundary,
    normalize_case_for_comparison,
    normalize_path_for_sandbox,
)
from app.utils.sandbox.runtime.which import which_sync

logger = logging.getLogger("TDDOrchestrator.Sandbox")

#: `DEFAULT_MANDATORY_DENY_SEARCH_DEPTH`.
DEFAULT_MANDATORY_DENY_SEARCH_DEPTH: Final = 3
#: The socat listeners inside the network namespace (`buildSandboxCommand`).
INTERNAL_HTTP_PROXY_PORT: Final = 3128
INTERNAL_SOCKS_PROXY_PORT: Final = 1080
#: `wrapCommandWithSandboxLinux`'s `apply-seccomp` error.
APPLY_SECCOMP_MISSING: Final = (
    "apply-seccomp binary not found. This should have been caught earlier. "
    "Ensure vendor/seccomp/{x64,arm64}/apply-seccomp binaries are included in the package."
)
#: `/etc/ssh/ssh_config.d` is always hidden (SSH rejects its ownership inside the sandbox).
SSH_CONFIG_D: Final = "/etc/ssh/ssh_config.d"
#: Root entries a deny of `/` never covers: remounted by the caller (`/proc`, `/dev`) or the kernel's.
ROOT_DENY_SKIP: Final = frozenset({"proc", "dev", "sys"})

RunRipgrep = Callable[[list[str], str, RipgrepConfig], list[str]]
Which = Callable[[str], "str | None"]


@dataclass(frozen=True)
class SandboxDependencyCheck:
    """`SandboxDependencyCheck`: errors mean the sandbox cannot run, warnings mean less protection."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LinuxDependencyStatus:
    """`LinuxDependencyStatus`."""

    has_bwrap: bool
    has_socat: bool
    has_seccomp_bpf: bool
    has_seccomp_apply: bool


@dataclass(frozen=True)
class LinuxSandboxParams:
    """`LinuxSandboxParams`, plus the environment upstream reads from the process (see the docstring)."""

    command: str
    needs_network_restriction: bool
    cwd: str
    home: str
    tmpdir: str
    http_socket_path: str | None = None
    socks_socket_path: str | None = None
    http_proxy_port: int | None = None
    socks_proxy_port: int | None = None
    read_config: FsReadRestrictionConfig | None = None
    write_config: FsWriteRestrictionConfig | None = None
    enable_weaker_nested_sandbox: bool | None = None
    allow_all_unix_sockets: bool | None = None
    bin_shell: str | None = None
    ripgrep_config: RipgrepConfig = field(default_factory=lambda: RipgrepConfig(command="rg"))
    mandatory_deny_search_depth: int = DEFAULT_MANDATORY_DENY_SEARCH_DEPTH
    allow_git_config: bool = False
    seccomp_config: SeccompConfig | None = None
    fs: SandboxFs = HOST_FS
    which: Which = which_sync
    run_ripgrep: RunRipgrep = rip_grep


def _components(target_path: str) -> list[str]:
    """`/a/b/c` -> `/a`, `/a/b`, `/a/b/c` (empty parts skipped)."""
    current = ""
    paths: list[str] = []
    for part in target_path.split("/"):
        if not part:
            continue
        current = f"{current}/{part}"
        paths.append(current)
    return paths


def _within(path: str, parent: str) -> bool:
    return path == parent or path.startswith(parent + "/")


def find_symlink_in_path(target_path: str, allowed_write_paths: list[str], fs: SandboxFs = HOST_FS) -> str | None:
    """
    `findSymlinkInPath`: the first component of `target_path` that is a symlink inside an allowed write
    path (it could be replaced by a real directory), or `None`.
    """
    for next_path in _components(target_path):
        try:
            if fs.is_symlink(next_path):
                if any(_within(next_path, allowed) for allowed in allowed_write_paths):
                    return next_path
        except OSError:
            break
    return None


def has_file_ancestor(target_path: str, fs: SandboxFs = HOST_FS) -> bool:
    """`hasFileAncestor`: an existing component is a file, so nothing below it can ever be created."""
    for next_path in _components(target_path):
        try:
            if fs.is_file(next_path):
                return True
        except OSError:
            break
    return False


def find_first_non_existent_component(target_path: str, fs: SandboxFs = HOST_FS) -> str:
    """`findFirstNonExistentComponent`."""
    for next_path in _components(target_path):
        if not fs.exists(next_path):
            return next_path
    return target_path


def linux_get_mandatory_deny_paths(
    *, cwd: str, ripgrep_config: RipgrepConfig, max_depth: int = DEFAULT_MANDATORY_DENY_SEARCH_DEPTH,
    allow_git_config: bool = False, fs: SandboxFs = HOST_FS, run_ripgrep: RunRipgrep = rip_grep,
) -> list[str]:
    """
    `linuxGetMandatoryDenyPaths`: the dangerous files and directories in `cwd`, `.git/hooks` and (unless
    allowed) `.git/config` when `.git` is a directory, and the same names found by one ripgrep scan up to
    `max_depth` below `cwd` (outside `node_modules`). Deduplicated, in order.
    """
    dangerous_directories = get_dangerous_directories()
    deny_paths = [posixpath.join(cwd, f) for f in DANGEROUS_FILES]
    deny_paths.extend(posixpath.join(cwd, d) for d in dangerous_directories)
    dot_git = posixpath.join(cwd, ".git")
    try:
        dot_git_is_directory = fs.is_dir(dot_git)
    except OSError:
        dot_git_is_directory = False
    if dot_git_is_directory:
        deny_paths.append(posixpath.join(cwd, ".git/hooks"))
        if not allow_git_config:
            deny_paths.append(posixpath.join(cwd, ".git/config"))

    iglob_args: list[str] = []
    for file_name in DANGEROUS_FILES:
        iglob_args += ["--iglob", file_name]
    for dir_name in dangerous_directories:
        iglob_args += ["--iglob", f"**/{dir_name}/**"]
    iglob_args += ["--iglob", "**/.git/hooks/**"]
    if not allow_git_config:
        iglob_args += ["--iglob", "**/.git/config"]

    matches: list[str] = []
    try:
        matches = run_ripgrep(
            ["--files", "--hidden", "--max-depth", str(max_depth), *iglob_args, "-g", "!**/node_modules/**"],
            cwd, ripgrep_config)
    except Exception as error:  # noqa: BLE001 - upstream logs a failed scan and keeps the static denies
        logger.debug("[Sandbox] ripgrep scan failed: %s", error)

    for match in matches:
        absolute = posixpath.normpath(posixpath.join(cwd, match))
        found_dir = False
        for dir_name in [*dangerous_directories, ".git"]:
            normalized_dir_name = normalize_case_for_comparison(dir_name)
            segments = absolute.split("/")
            dir_index = next((i for i, s in enumerate(segments)
                              if normalize_case_for_comparison(s) == normalized_dir_name), -1)
            if dir_index != -1:
                if dir_name == ".git":
                    git_dir = "/".join(segments[:dir_index + 1])
                    if ".git/hooks" in match:
                        deny_paths.append(posixpath.join(git_dir, "hooks"))
                    elif ".git/config" in match:
                        deny_paths.append(posixpath.join(git_dir, "config"))
                else:
                    deny_paths.append("/".join(segments[:dir_index + 1]))
                found_dir = True
                break
        if not found_dir:
            deny_paths.append(absolute)
    return list(dict.fromkeys(deny_paths))


#: `bwrapMountPoints`: the empty files and directories bwrap created on the host as mount points for
#: non-existent deny paths; `cleanup_bwrap_mount_points` removes them after each command.
_bwrap_mount_points: dict[str, None] = {}
_exit_handler_registered = False


def _register_exit_cleanup_handler() -> None:
    """`registerExitCleanupHandler`: the mount points are removed at exit too, as a safety net."""
    global _exit_handler_registered
    if not _exit_handler_registered:
        atexit.register(cleanup_bwrap_mount_points)
        _exit_handler_registered = True


def bwrap_mount_points() -> list[str]:
    """The mount points tracked for the next cleanup, in the order they were added."""
    return list(_bwrap_mount_points)


def cleanup_bwrap_mount_points() -> None:
    """
    `cleanupBwrapMountPoints`: remove each tracked mount point that is still what bwrap left (an empty
    file, or an empty directory); anything with content is left alone.
    """
    import os

    for mount_point in list(_bwrap_mount_points):
        try:
            if os.path.isfile(mount_point) and os.path.getsize(mount_point) == 0:
                os.unlink(mount_point)
                logger.debug("[Sandbox Linux] Cleaned up bwrap mount point (file): %s", mount_point)
            elif os.path.isdir(mount_point) and not os.listdir(mount_point):
                os.rmdir(mount_point)
                logger.debug("[Sandbox Linux] Cleaned up bwrap mount point (dir): %s", mount_point)
        except OSError:
            pass
    _bwrap_mount_points.clear()


def get_linux_dependency_status(seccomp_config: SeccompConfig | None = None,
                                which: Which = which_sync) -> LinuxDependencyStatus:
    """`getLinuxDependencyStatus`."""
    return LinuxDependencyStatus(
        has_bwrap=which("bwrap") is not None,
        has_socat=which("socat") is not None,
        has_seccomp_bpf=get_pre_generated_bpf_path(seccomp_config.bpf_path if seccomp_config else None) is not None,
        has_seccomp_apply=get_apply_seccomp_binary_path(
            seccomp_config.apply_path if seccomp_config else None) is not None,
    )


def check_linux_dependencies(seccomp_config: SeccompConfig | None = None,
                             which: Which = which_sync) -> SandboxDependencyCheck:
    """`checkLinuxDependencies`: bwrap and socat are required; seccomp missing is only a warning."""
    errors: list[str] = []
    warnings: list[str] = []
    if which("bwrap") is None:
        errors.append("bubblewrap (bwrap) not installed")
    if which("socat") is None:
        errors.append("socat not installed")
    status = get_linux_dependency_status(seccomp_config, which)
    if not status.has_seccomp_bpf or not status.has_seccomp_apply:
        warnings.append("seccomp not available - unix socket access not restricted")
    return SandboxDependencyCheck(errors=errors, warnings=warnings)


def build_sandbox_command(
    http_socket_path: str, socks_socket_path: str, user_command: str, seccomp_filter_path: str | None,
    shell: str | None = None, apply_seccomp_path: str | None = None,
) -> str:
    """
    `buildSandboxCommand`: inside the namespace, two socat listeners (3128 -> the HTTP bridge socket,
    1080 -> the SOCKS one) killed on exit, then the user's command, under `apply-seccomp` when a filter
    is given (socat needs Unix sockets, so the filter applies only to the command).
    """
    shell_path = shell or "bash"
    socat_commands = [
        f"socat TCP-LISTEN:{INTERNAL_HTTP_PROXY_PORT},fork,reuseaddr UNIX-CONNECT:{http_socket_path} >/dev/null 2>&1 &",
        f"socat TCP-LISTEN:{INTERNAL_SOCKS_PROXY_PORT},fork,reuseaddr UNIX-CONNECT:{socks_socket_path} "
        ">/dev/null 2>&1 &",
        'trap "kill %1 %2 2>/dev/null; exit" EXIT',
    ]
    if seccomp_filter_path:
        apply_seccomp_binary = get_apply_seccomp_binary_path(apply_seccomp_path)
        if not apply_seccomp_binary:
            raise RuntimeError(APPLY_SECCOMP_MISSING)
        apply_seccomp_cmd = shlex.join([apply_seccomp_binary, seccomp_filter_path, shell_path, "-c", user_command])
        inner_script = "\n".join([*socat_commands, apply_seccomp_cmd])
    else:
        inner_script = "\n".join([*socat_commands, f"eval {shlex.quote(user_command)}"])
    return f"{shell_path} -c {shlex.quote(inner_script)}"


def generate_filesystem_args(
    read_config: FsReadRestrictionConfig | None,
    write_config: FsWriteRestrictionConfig | None,
    *,
    cwd: str,
    home: str,
    ripgrep_config: RipgrepConfig,
    mandatory_deny_search_depth: int = DEFAULT_MANDATORY_DENY_SEARCH_DEPTH,
    allow_git_config: bool = False,
    fs: SandboxFs = HOST_FS,
    run_ripgrep: RunRipgrep = rip_grep,
) -> list[str]:
    """
    `generateFilesystemArgs`: a read-only root with the allowed write paths bound writable; a tmpfs over
    each denied read directory (`/dev/null` over a denied file), re-binding the write paths and the
    re-allowed read paths it covered; then the write denies (the configured ones and the mandatory ones)
    as read-only binds, last, so no tmpfs wipes them. A non-existent deny path inside a writable area is
    blocked with `/dev/null` (or an empty directory for an intermediate component), tracked for cleanup.
    """
    def normalize(path: str) -> str:
        return normalize_path_for_sandbox(path, cwd=cwd, home=home, fs=fs)

    args: list[str] = []
    allowed_write_paths: list[str] = []
    deny_write_args: list[str] = []

    if write_config is not None:
        args += ["--ro-bind", "/", "/"]
        for path_pattern in write_config.allow_only:
            normalized = normalize(path_pattern)
            logger.debug("[Sandbox Linux] Processing write path: %s -> %s", path_pattern, normalized)
            if normalized.startswith("/dev/"):
                continue
            if not fs.exists(normalized):
                logger.debug("[Sandbox Linux] Skipping non-existent write path: %s", normalized)
                continue
            try:
                resolved = fs.realpath(normalized)
                for_comparison = normalized.rstrip("/")
                if resolved != for_comparison and is_symlink_outside_boundary(normalized, resolved):
                    logger.debug("[Sandbox Linux] Skipping symlink write path pointing outside expected location: "
                                 "%s -> %s", path_pattern, resolved)
                    continue
            except OSError:
                logger.debug("[Sandbox Linux] Skipping write path that could not be resolved: %s", normalized)
                continue
            args += ["--bind", normalized, normalized]
            allowed_write_paths.append(normalized)

        deny_paths = [*write_config.deny_within_allow, *linux_get_mandatory_deny_paths(
            cwd=cwd, ripgrep_config=ripgrep_config, max_depth=mandatory_deny_search_depth,
            allow_git_config=allow_git_config, fs=fs, run_ripgrep=run_ripgrep)]
        seen_deny_write: set[str] = set()
        for path_pattern in deny_paths:
            normalized = normalize(path_pattern)
            if normalized.startswith("/dev/"):
                continue
            # Entries such as `~/.foo` and `/home/u/.foo` converge here: a second /dev/null bind fails.
            if normalized in seen_deny_write:
                continue
            seen_deny_write.add(normalized)
            symlink_in_path = find_symlink_in_path(normalized, allowed_write_paths, fs)
            if symlink_in_path:
                deny_write_args += ["--ro-bind", "/dev/null", symlink_in_path]
                logger.debug("[Sandbox Linux] Mounted /dev/null at symlink %s to prevent symlink replacement "
                             "attack", symlink_in_path)
                continue
            if not fs.exists(normalized):
                if has_file_ancestor(normalized, fs):
                    logger.debug("[Sandbox Linux] Skipping deny path with file ancestor (cannot create paths "
                                 "under a file): %s", normalized)
                    continue
                ancestor = posixpath.dirname(normalized)
                while ancestor != "/" and not fs.exists(ancestor):
                    ancestor = posixpath.dirname(ancestor)
                within_allowed = any(
                    ancestor.startswith(allowed + "/") or ancestor == allowed or normalized.startswith(allowed + "/")
                    for allowed in allowed_write_paths)
                if within_allowed:
                    first_non_existent = find_first_non_existent_component(normalized, fs)
                    if first_non_existent != normalized:
                        empty_dir = fs.mkdtemp("tddagents-empty-")
                        deny_write_args += ["--ro-bind", empty_dir, first_non_existent]
                    else:
                        deny_write_args += ["--ro-bind", "/dev/null", first_non_existent]
                    _bwrap_mount_points[first_non_existent] = None
                    _register_exit_cleanup_handler()
                else:
                    logger.debug("[Sandbox Linux] Skipping non-existent deny path not within allowed paths: %s",
                                 normalized)
                continue
            if any(_within(normalized, allowed) for allowed in allowed_write_paths):
                deny_write_args += ["--ro-bind", normalized, normalized]
            else:
                logger.debug("[Sandbox Linux] Skipping deny path not within allowed paths: %s", normalized)
    else:
        args += ["--bind", "/", "/"]

    read_deny_paths: list[str] = []
    read_allow_paths = [normalize(p) for p in (read_config.allow_within_deny if read_config else [])]
    # A tmpfs over / would wipe every earlier mount: a denied root becomes its children.
    for path in read_config.deny_only if read_config else []:
        if normalize(path) == "/":
            read_deny_paths.extend("/" + child for child in fs.listdir("/") if child not in ROOT_DENY_SKIP)
        else:
            read_deny_paths.append(path)
    if fs.exists(SSH_CONFIG_D):
        read_deny_paths.append(SSH_CONFIG_D)

    for path_pattern in read_deny_paths:
        normalized = normalize(path_pattern)
        if not fs.exists(normalized):
            logger.debug("[Sandbox Linux] Skipping non-existent read deny path: %s", normalized)
            continue
        deny_sep = "/" if normalized == "/" else normalized + "/"
        if fs.is_dir(normalized):
            args += ["--tmpfs", normalized]
            for write_path in allowed_write_paths:
                if write_path.startswith(deny_sep) or write_path == normalized:
                    args += ["--bind", write_path, write_path]
            for allow_path in read_allow_paths:
                if allow_path.startswith(deny_sep) or allow_path == normalized:
                    if not fs.exists(allow_path):
                        logger.debug("[Sandbox Linux] Skipping non-existent read allow path: %s", allow_path)
                        continue
                    # Already re-bound writable above, by a write path the tmpfs had wiped.
                    if any((w.startswith(deny_sep) or w == normalized) and _within(allow_path, w)
                           for w in allowed_write_paths):
                        continue
                    args += ["--ro-bind", allow_path, allow_path]
        else:
            if any(_within(normalized, allow_path) for allow_path in read_allow_paths):
                logger.debug("[Sandbox Linux] Skipping read deny for re-allowed path: %s", normalized)
                continue
            args += ["--ro-bind", "/dev/null", normalized]

    # The write denies go last, on top of anything the read denies just re-bound.
    args += deny_write_args
    return args


def wrap_command_with_sandbox_linux(params: LinuxSandboxParams) -> str:
    """
    `wrapCommandWithSandboxLinux`: `bwrap --new-session --die-with-parent`, `--unshare-net` with the
    bridge sockets bound and the proxy environment when the network is restricted, the filesystem args,
    `--dev /dev`, `--unshare-pid` (`--proc /proc` unless the weaker nested sandbox is on), then the shell
    running the command (through `apply-seccomp` when the filter is available). The command comes back
    unchanged when there is nothing to restrict.
    """
    read_config, write_config = params.read_config, params.write_config
    has_read_restrictions = read_config is not None and len(read_config.deny_only) > 0
    has_write_restrictions = write_config is not None
    if not params.needs_network_restriction and not has_read_restrictions and not has_write_restrictions:
        return params.command

    bwrap_args = ["--new-session", "--die-with-parent"]
    seccomp_filter_path: str | None = None
    seccomp = params.seccomp_config
    try:
        if not params.allow_all_unix_sockets:
            seccomp_filter_path = generate_seccomp_filter(seccomp.bpf_path if seccomp else None)
            apply_seccomp_binary = get_apply_seccomp_binary_path(seccomp.apply_path if seccomp else None)
            if not seccomp_filter_path or not apply_seccomp_binary:
                logger.warning("[Sandbox Linux] Seccomp binaries not available - unix socket blocking disabled.")
                seccomp_filter_path = None
        else:
            logger.debug("[Sandbox Linux] Skipping seccomp filter - allowAllUnixSockets is enabled")

        if params.needs_network_restriction:
            bwrap_args.append("--unshare-net")
            if params.http_socket_path and params.socks_socket_path:
                if not params.fs.exists(params.http_socket_path):
                    raise RuntimeError(
                        f"Linux HTTP bridge socket does not exist: {params.http_socket_path}. "
                        "The bridge process may have died. Try reinitializing the sandbox.")
                if not params.fs.exists(params.socks_socket_path):
                    raise RuntimeError(
                        f"Linux SOCKS bridge socket does not exist: {params.socks_socket_path}. "
                        "The bridge process may have died. Try reinitializing the sandbox.")
                bwrap_args += ["--bind", params.http_socket_path, params.http_socket_path]
                bwrap_args += ["--bind", params.socks_socket_path, params.socks_socket_path]
                for env in generate_proxy_env_vars(INTERNAL_HTTP_PROXY_PORT, INTERNAL_SOCKS_PROXY_PORT,
                                                   tmpdir=params.tmpdir, platform="linux"):
                    key, _, value = env.partition("=")
                    bwrap_args += ["--setenv", key, value]
                if params.http_proxy_port is not None:
                    bwrap_args += ["--setenv", "TDDAGENTS_HOST_HTTP_PROXY_PORT", str(params.http_proxy_port)]
                if params.socks_proxy_port is not None:
                    bwrap_args += ["--setenv", "TDDAGENTS_HOST_SOCKS_PROXY_PORT", str(params.socks_proxy_port)]

        bwrap_args += generate_filesystem_args(
            read_config, write_config, cwd=params.cwd, home=params.home, ripgrep_config=params.ripgrep_config,
            mandatory_deny_search_depth=params.mandatory_deny_search_depth,
            allow_git_config=params.allow_git_config, fs=params.fs, run_ripgrep=params.run_ripgrep)
        bwrap_args += ["--dev", "/dev"]
        bwrap_args.append("--unshare-pid")
        if not params.enable_weaker_nested_sandbox:
            bwrap_args += ["--proc", "/proc"]

        shell_name = params.bin_shell or "bash"
        shell = params.which(shell_name)
        if not shell:
            raise RuntimeError(f"Shell '{shell_name}' not found in PATH")
        bwrap_args += ["--", shell, "-c"]

        if params.needs_network_restriction and params.http_socket_path and params.socks_socket_path:
            bwrap_args.append(build_sandbox_command(
                params.http_socket_path, params.socks_socket_path, params.command, seccomp_filter_path, shell,
                seccomp.apply_path if seccomp else None))
        elif seccomp_filter_path:
            apply_seccomp_binary = get_apply_seccomp_binary_path(seccomp.apply_path if seccomp else None)
            if not apply_seccomp_binary:
                raise RuntimeError(APPLY_SECCOMP_MISSING)
            bwrap_args.append(shlex.join([apply_seccomp_binary, seccomp_filter_path, shell, "-c", params.command]))
        else:
            bwrap_args.append(params.command)

        restrictions = []
        if params.needs_network_restriction:
            restrictions.append("network")
        if has_read_restrictions or has_write_restrictions:
            restrictions.append("filesystem")
        if seccomp_filter_path:
            restrictions.append("seccomp(unix-block)")
        logger.debug("[Sandbox Linux] Wrapped command with bwrap (%s restrictions)", ", ".join(restrictions))
        return shlex.join(["bwrap", *bwrap_args])
    except Exception:
        if seccomp_filter_path and "/vendor/seccomp/" not in seccomp_filter_path:
            cleanup_seccomp_filter(seccomp_filter_path)
        raise


__all__ = [
    "APPLY_SECCOMP_MISSING",
    "DEFAULT_MANDATORY_DENY_SEARCH_DEPTH",
    "INTERNAL_HTTP_PROXY_PORT",
    "INTERNAL_SOCKS_PROXY_PORT",
    "ROOT_DENY_SKIP",
    "SSH_CONFIG_D",
    "LinuxDependencyStatus",
    "LinuxSandboxParams",
    "SandboxDependencyCheck",
    "build_sandbox_command",
    "bwrap_mount_points",
    "check_linux_dependencies",
    "cleanup_bwrap_mount_points",
    "find_first_non_existent_component",
    "find_symlink_in_path",
    "generate_filesystem_args",
    "get_linux_dependency_status",
    "has_file_ancestor",
    "linux_get_mandatory_deny_paths",
    "wrap_command_with_sandbox_linux",
]
