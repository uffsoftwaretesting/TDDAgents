"""
Path rules, the protected files, the default write paths and the proxy environment of the sandbox.

Ported from `@anthropic-ai/sandbox-runtime` 0.0.44 `src/sandbox/sandbox-utils.ts` -> `DANGEROUS_FILES`,
`DANGEROUS_DIRECTORIES`, `getDangerousDirectories`, `normalizeCaseForComparison`, `containsGlobChars`,
`removeTrailingGlobSuffix`, `isSymlinkOutsideBoundary`, `normalizePathForSandbox`,
`getDefaultWritePaths`, `generateProxyEnvVars`, `encodeSandboxedCommand`, `decodeSandboxedCommand`,
`globToRegex`, `expandGlobPattern`.

Divergences:
* Upstream reads `process.cwd()` and `os.homedir()`; here the working directory and the home are
  parameters (`cwd`, `home`): TDDAgents' process never `chdir`s into the project it sandboxes.
* The filesystem is reached through `SandboxFs` (`HostFs` by default), so the bwrap argv builder can be
  tested with a fake one.
* Product renames: `.claude/commands` and `.claude/agents` are `.tddagents/commands` and
  `.tddagents/agents`; `~/.claude/debug` is `~/.tddagents/debug`.
* The sandbox temp directory is a parameter of `getDefaultWritePaths` and `generateProxyEnvVars`
  (upstream: `/tmp/claude`, `CLAUDE_TMPDIR`). Upstream's runtime default and its adapter's
  `/tmp/claude-<uid>` differ, which leaves the sandboxed `TMPDIR` unwritable; TDDAgents gives the
  runtime the adapter's one directory (user decision 2026-11-08: `/tmp` as upstream, writable only at
  the per-user directory).
"""

from __future__ import annotations

import base64
import logging
import os
import posixpath
import re
import tempfile
from typing import Final, Protocol

from app.utils.sandbox.runtime.platform import Platform, get_platform

logger = logging.getLogger("TDDOrchestrator.Sandbox")

#: `DANGEROUS_FILES`: files a write to which can run code or leak data.
DANGEROUS_FILES: Final[tuple[str, ...]] = (
    ".gitconfig",
    ".gitmodules",
    ".bashrc",
    ".bash_profile",
    ".zshrc",
    ".zprofile",
    ".profile",
    ".ripgreprc",
    ".mcp.json",
)

#: `DANGEROUS_DIRECTORIES`.
DANGEROUS_DIRECTORIES: Final[tuple[str, ...]] = (".git", ".vscode", ".idea")

#: Upstream's `.claude/commands` and `.claude/agents`, under TDDAgents' configuration directory.
CONFIG_DIR_NAME = ".tddagents"


class SandboxFs(Protocol):
    """The filesystem queries the runtime makes (Node's `fs.*Sync`)."""

    def exists(self, path: str) -> bool:
        """`fs.existsSync`: follows symlinks, so a broken one does not exist."""
        ...

    def is_dir(self, path: str) -> bool:
        """`fs.statSync(path).isDirectory()`; raises `OSError` when the path does not exist."""
        ...

    def is_file(self, path: str) -> bool:
        """`fs.statSync(path).isFile()`; raises `OSError` when the path does not exist."""
        ...

    def is_symlink(self, path: str) -> bool:
        """`fs.lstatSync(path).isSymbolicLink()`; raises `OSError` when the path does not exist."""
        ...

    def realpath(self, path: str) -> str:
        """`fs.realpathSync`: raises `OSError` when the path does not resolve."""
        ...

    def listdir(self, path: str) -> list[str]:
        """`fs.readdirSync`."""
        ...

    def walk(self, path: str) -> list[str]:
        """`fs.readdirSync(path, {recursive: true})`: every entry's full path below `path`."""
        ...

    def mkdtemp(self, prefix: str) -> str:
        """`fs.mkdtempSync(join(tmpdir(), prefix))`."""
        ...


class HostFs:
    """`SandboxFs` over the real filesystem."""

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def is_dir(self, path: str) -> bool:
        os.stat(path)
        return os.path.isdir(path)

    def is_file(self, path: str) -> bool:
        os.stat(path)
        return os.path.isfile(path)

    def is_symlink(self, path: str) -> bool:
        os.lstat(path)
        return os.path.islink(path)

    def realpath(self, path: str) -> str:
        return os.path.realpath(path, strict=True)

    def listdir(self, path: str) -> list[str]:
        return os.listdir(path)

    def walk(self, path: str) -> list[str]:
        entries: list[str] = []
        for current, dirnames, filenames in os.walk(path):
            entries.extend(os.path.join(current, name) for name in [*dirnames, *filenames])
        return entries

    def mkdtemp(self, prefix: str) -> str:
        return tempfile.mkdtemp(prefix=prefix)


HOST_FS: Final = HostFs()


def get_dangerous_directories() -> list[str]:
    """
    `getDangerousDirectories`: `DANGEROUS_DIRECTORIES` without `.git` (writable for git itself; its
    hooks and config are denied one by one), plus the configuration's commands and agents.
    """
    return [*(d for d in DANGEROUS_DIRECTORIES if d != ".git"),
            f"{CONFIG_DIR_NAME}/commands", f"{CONFIG_DIR_NAME}/agents"]


def normalize_case_for_comparison(path_str: str) -> str:
    """`normalizeCaseForComparison`: always lowercase, whatever the platform."""
    return path_str.lower()


def contains_glob_chars(path_pattern: str) -> bool:
    """`containsGlobChars`."""
    return any(char in path_pattern for char in "*?[]")


def remove_trailing_glob_suffix(path_pattern: str) -> str:
    """`removeTrailingGlobSuffix`: `x/**` is `x`; an empty result is `/`."""
    stripped = re.sub(r"/\*\*$", "", path_pattern)
    return stripped or "/"


def _path_normalize(path: str) -> str:
    """Node's `path.normalize` for an absolute POSIX path (a trailing slash is kept)."""
    normalized = posixpath.normpath(path)
    if normalized.startswith("//"):
        normalized = "/" + normalized.lstrip("/")
    if path.endswith("/") and normalized != "/":
        normalized += "/"
    return normalized


def is_symlink_outside_boundary(original_path: str, resolved_path: str) -> bool:
    """
    `isSymlinkOutsideBoundary`: whether resolving `original_path` to `resolved_path` broadens the scope
    (an ancestor, the root, a single-component directory, or any path outside the original's tree).
    macOS's `/tmp` and `/var` -> `/private/...` canonicalizations are allowed.
    """
    normalized_original = _path_normalize(original_path)
    normalized_resolved = _path_normalize(resolved_path)
    if normalized_resolved == normalized_original:
        return False
    if normalized_original.startswith("/tmp/") and normalized_resolved == "/private" + normalized_original:
        return False
    if normalized_original.startswith("/var/") and normalized_resolved == "/private" + normalized_original:
        return False
    if normalized_resolved == "/":
        return True
    resolved_parts = [part for part in normalized_resolved.split("/") if part]
    if len(resolved_parts) <= 1:
        return True
    if normalized_original.startswith(normalized_resolved + "/"):
        return True
    canonical_original = normalized_original
    if normalized_original.startswith("/tmp/") or normalized_original.startswith("/var/"):
        canonical_original = "/private" + normalized_original
    if canonical_original != normalized_original and canonical_original.startswith(normalized_resolved + "/"):
        return True
    resolved_starts_with_original = normalized_resolved.startswith(normalized_original + "/")
    resolved_starts_with_canonical = (canonical_original != normalized_original
                                      and normalized_resolved.startswith(canonical_original + "/"))
    resolved_is_canonical = canonical_original != normalized_original and normalized_resolved == canonical_original
    return not (resolved_is_canonical or resolved_starts_with_original or resolved_starts_with_canonical)


def normalize_path_for_sandbox(path_pattern: str, *, cwd: str, home: str, fs: SandboxFs = HOST_FS) -> str:
    """
    `normalizePathForSandbox`: `~` expanded against `home`, a relative path made absolute against `cwd`,
    symlinks resolved unless the resolution crosses a boundary; a glob keeps its wildcards, only its
    static directory prefix resolved.
    """
    normalized = path_pattern
    if path_pattern == "~":
        normalized = home
    elif path_pattern.startswith("~/"):
        normalized = home + path_pattern[1:]
    elif not posixpath.isabs(path_pattern):
        normalized = posixpath.normpath(posixpath.join(cwd, path_pattern))

    if contains_glob_chars(normalized):
        static_prefix = re.split(r"[*?\[\]]", normalized)[0]
        if static_prefix and static_prefix != "/":
            base_dir = static_prefix[:-1] if static_prefix.endswith("/") else posixpath.dirname(static_prefix)
            try:
                resolved_base = fs.realpath(base_dir)
                if not is_symlink_outside_boundary(base_dir, resolved_base):
                    return resolved_base + normalized[len(base_dir):]
            except OSError:
                pass
        return normalized

    try:
        resolved = fs.realpath(normalized)
        if not is_symlink_outside_boundary(normalized, resolved):
            normalized = resolved
    except OSError:
        pass
    return normalized


def get_default_write_paths(*, home: str, tmpdir: str) -> list[str]:
    """
    `getDefaultWritePaths`: the device files, the sandbox temp directory (`tmpdir`, and macOS's
    `/private` form of it), npm's log directory and TDDAgents' debug directory.
    """
    return [
        "/dev/stdout",
        "/dev/stderr",
        "/dev/null",
        "/dev/tty",
        "/dev/dtracehelper",
        "/dev/autofs_nowait",
        tmpdir,
        "/private" + tmpdir,
        posixpath.join(home, ".npm/_logs"),
        posixpath.join(home, f"{CONFIG_DIR_NAME}/debug"),
    ]


#: `generateProxyEnvVars`' `NO_PROXY`: localhost and the private networks.
NO_PROXY_ADDRESSES: Final = ",".join([
    "localhost",
    "127.0.0.1",
    "::1",
    "*.local",
    ".local",
    "169.254.0.0/16",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
])


def generate_proxy_env_vars(
    http_proxy_port: int | None = None, socks_proxy_port: int | None = None, *, tmpdir: str,
    platform: Platform | None = None,
) -> list[str]:
    """`generateProxyEnvVars`: `KEY=value` entries pointing every common client at the proxies."""
    env_vars = ["SANDBOX_RUNTIME=1", f"TMPDIR={tmpdir}"]
    if not http_proxy_port and not socks_proxy_port:
        return env_vars
    env_vars.append(f"NO_PROXY={NO_PROXY_ADDRESSES}")
    env_vars.append(f"no_proxy={NO_PROXY_ADDRESSES}")
    if http_proxy_port:
        env_vars.append(f"HTTP_PROXY=http://localhost:{http_proxy_port}")
        env_vars.append(f"HTTPS_PROXY=http://localhost:{http_proxy_port}")
        env_vars.append(f"http_proxy=http://localhost:{http_proxy_port}")
        env_vars.append(f"https_proxy=http://localhost:{http_proxy_port}")
    if socks_proxy_port:
        env_vars.append(f"ALL_PROXY=socks5h://localhost:{socks_proxy_port}")
        env_vars.append(f"all_proxy=socks5h://localhost:{socks_proxy_port}")
        current = platform if platform is not None else get_platform()
        if current == "macos":
            env_vars.append(f"GIT_SSH_COMMAND=ssh -o ProxyCommand='nc -X 5 -x localhost:{socks_proxy_port} %h %p'")
        elif current == "linux" and http_proxy_port:
            env_vars.append(
                f"GIT_SSH_COMMAND=ssh -o ProxyCommand='socat - PROXY:localhost:%h:%p,proxyport={http_proxy_port}'")
        env_vars.append(f"FTP_PROXY=socks5h://localhost:{socks_proxy_port}")
        env_vars.append(f"ftp_proxy=socks5h://localhost:{socks_proxy_port}")
        env_vars.append(f"RSYNC_PROXY=localhost:{socks_proxy_port}")
        docker_port = http_proxy_port or socks_proxy_port
        env_vars.append(f"DOCKER_HTTP_PROXY=http://localhost:{docker_port}")
        env_vars.append(f"DOCKER_HTTPS_PROXY=http://localhost:{docker_port}")
        if http_proxy_port:
            env_vars.append("CLOUDSDK_PROXY_TYPE=https")
            env_vars.append("CLOUDSDK_PROXY_ADDRESS=localhost")
            env_vars.append(f"CLOUDSDK_PROXY_PORT={http_proxy_port}")
        env_vars.append(f"GRPC_PROXY=socks5h://localhost:{socks_proxy_port}")
        env_vars.append(f"grpc_proxy=socks5h://localhost:{socks_proxy_port}")
    return env_vars


def encode_sandboxed_command(command: str) -> str:
    """`encodeSandboxedCommand`: the first 100 characters, base64-encoded."""
    return base64.b64encode(command[:100].encode("utf-8")).decode("ascii")


def decode_sandboxed_command(encoded_command: str) -> str:
    """`decodeSandboxedCommand`."""
    return base64.b64decode(encoded_command).decode("utf-8")


def glob_to_regex(glob_pattern: str) -> str:
    """
    `globToRegex`: gitignore-style `*` (no `/`), `**` (anything), `**/` (zero or more directories), `?`
    and `[...]`, anchored at both ends.
    """
    pattern = re.sub(r"[.^$+{}()|\\]", lambda m: "\\" + m.group(0), glob_pattern)
    pattern = re.sub(r"\[([^\]]*?)$", r"\\[\1", pattern)
    pattern = pattern.replace("**/", "__GLOBSTAR_SLASH__")
    pattern = pattern.replace("**", "__GLOBSTAR__")
    pattern = pattern.replace("*", "[^/]*")
    pattern = pattern.replace("?", "[^/]")
    pattern = pattern.replace("__GLOBSTAR_SLASH__", "(.*/)?")
    pattern = pattern.replace("__GLOBSTAR__", ".*")
    return "^" + pattern + "$"


def expand_glob_pattern(glob_path: str, *, cwd: str, home: str, fs: SandboxFs = HOST_FS) -> list[str]:
    """
    `expandGlobPattern`: the existing paths a glob matches, listed below its static directory prefix
    (bubblewrap takes no globs). A pattern whose prefix is the root is too broad and expands to nothing.
    """
    normalized = normalize_path_for_sandbox(glob_path, cwd=cwd, home=home, fs=fs)
    static_prefix = re.split(r"[*?\[\]]", normalized)[0]
    if not static_prefix or static_prefix == "/":
        logger.debug("[Sandbox] Glob pattern too broad, skipping: %s", glob_path)
        return []
    base_dir = static_prefix[:-1] if static_prefix.endswith("/") else posixpath.dirname(static_prefix)
    if not fs.exists(base_dir):
        logger.debug("[Sandbox] Base directory for glob does not exist: %s", base_dir)
        return []
    regex = re.compile(glob_to_regex(normalized))
    try:
        return [entry for entry in fs.walk(base_dir) if regex.search(entry)]
    except OSError as error:
        logger.debug("[Sandbox] Error expanding glob pattern %s: %s", glob_path, error)
        return []


__all__ = [
    "CONFIG_DIR_NAME",
    "DANGEROUS_DIRECTORIES",
    "DANGEROUS_FILES",
    "HOST_FS",
    "NO_PROXY_ADDRESSES",
    "HostFs",
    "SandboxFs",
    "contains_glob_chars",
    "decode_sandboxed_command",
    "encode_sandboxed_command",
    "expand_glob_pattern",
    "generate_proxy_env_vars",
    "get_dangerous_directories",
    "get_default_write_paths",
    "glob_to_regex",
    "is_symlink_outside_boundary",
    "normalize_case_for_comparison",
    "normalize_path_for_sandbox",
    "remove_trailing_glob_suffix",
]
