"""
The configuration the sandbox runtime is initialized with.

Ported from `@anthropic-ai/sandbox-runtime` 0.0.44 `src/sandbox/sandbox-config.ts` ->
`SandboxRuntimeConfigSchema` and its parts (`NetworkConfigSchema`, `FilesystemConfigSchema`,
`IgnoreViolationsConfigSchema`, `RipgrepConfigSchema`, `SeccompConfigSchema`, `MitmProxyConfigSchema`,
the `domainPatternSchema` refinement). The zod schema becomes frozen dataclasses (snake_cased fields)
and `parse_sandbox_runtime_config`, which validates the camelCase document as the schema does and
raises `SandboxConfigError` with the schema's messages.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: `domainPatternSchema`'s message.
INVALID_DOMAIN_PATTERN_MESSAGE = (
    'Invalid domain pattern. Must be a valid domain (e.g., "example.com") or wildcard (e.g., "*.example.com"). '
    'Overly broad patterns like "*.com" or "*" are not allowed for security reasons.'
)


class SandboxConfigError(ValueError):
    """The document does not satisfy `SandboxRuntimeConfigSchema` (the issues, one per line)."""


def is_valid_domain_pattern(val: str) -> bool:
    """`domainPatternSchema`'s refinement: a domain or `*.domain`, never a protocol, path, port or bare `*`."""
    if "://" in val or "/" in val or ":" in val:
        return False
    if val == "localhost":
        return True
    if val.startswith("*."):
        domain = val[2:]
        # After the *. there must be a valid domain with at least one more dot (*.com is too broad).
        if "." not in domain or domain.startswith(".") or domain.endswith("."):
            return False
        parts = domain.split(".")
        return len(parts) >= 2 and all(len(p) > 0 for p in parts)
    if "*" in val:
        return False
    return "." in val and not val.startswith(".") and not val.endswith(".")


@dataclass(frozen=True)
class MitmProxyConfig:
    """`MitmProxyConfigSchema`: matching domains are routed through an upstream proxy's Unix socket."""

    socket_path: str
    domains: list[str]


@dataclass(frozen=True)
class NetworkConfig:
    """`NetworkConfigSchema`."""

    allowed_domains: list[str]
    denied_domains: list[str]
    allow_unix_sockets: list[str] | None = None
    allow_all_unix_sockets: bool | None = None
    allow_local_binding: bool | None = None
    http_proxy_port: int | None = None
    socks_proxy_port: int | None = None
    mitm_proxy: MitmProxyConfig | None = None


@dataclass(frozen=True)
class FilesystemConfig:
    """`FilesystemConfigSchema`."""

    deny_read: list[str]
    allow_write: list[str]
    deny_write: list[str]
    allow_read: list[str] | None = None
    allow_git_config: bool | None = None


@dataclass(frozen=True)
class RipgrepConfig:
    """`RipgrepConfigSchema`: the ripgrep command, arguments before ripgrep's own, and an `argv0` override."""

    command: str
    args: list[str] | None = None
    argv0: str | None = None


@dataclass(frozen=True)
class SeccompConfig:
    """`SeccompConfigSchema` (Linux only): explicit paths to the BPF filter and `apply-seccomp`."""

    bpf_path: str | None = None
    apply_path: str | None = None


@dataclass(frozen=True)
class SandboxRuntimeConfig:
    """`SandboxRuntimeConfigSchema`."""

    network: NetworkConfig
    filesystem: FilesystemConfig
    ignore_violations: dict[str, list[str]] | None = None
    enable_weaker_nested_sandbox: bool | None = None
    enable_weaker_network_isolation: bool | None = None
    ripgrep: RipgrepConfig | None = None
    mandatory_deny_search_depth: int | None = None
    allow_pty: bool | None = None
    seccomp: SeccompConfig | None = None


class _Issues:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def add(self, path: str, message: str) -> None:
        self.messages.append(f"{path}: {message}")


def _strings(raw: Any, path: str, issues: _Issues, *, optional: bool = False, min_length: int = 0,
             min_message: str = "") -> list[str] | None:
    if raw is None:
        if not optional:
            issues.add(path, "Required")
        return None
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        issues.add(path, "Expected array of strings")
        return None
    for index, item in enumerate(raw):
        if len(item) < min_length:
            issues.add(f"{path}.{index}", min_message)
    return list(raw)


def _domains(raw: Any, path: str, issues: _Issues, *, optional: bool = False) -> list[str] | None:
    values = _strings(raw, path, issues, optional=optional)
    for index, value in enumerate(values or []):
        if not is_valid_domain_pattern(value):
            issues.add(f"{path}.{index}", INVALID_DOMAIN_PATTERN_MESSAGE)
    return values


def _boolean(raw: Any, path: str, issues: _Issues) -> bool | None:
    if raw is None or isinstance(raw, bool):
        return raw
    issues.add(path, "Expected boolean")
    return None


def _int_in(raw: Any, path: str, issues: _Issues, low: int, high: int) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int):
        issues.add(path, "Expected integer")
        return None
    value: int = raw
    if value < low or value > high:
        issues.add(path, f"Number must be between {low} and {high}")
        return None
    return value


def _object(raw: Any, path: str, issues: _Issues, *, optional: bool = False) -> Mapping[str, Any] | None:
    if raw is None:
        if not optional:
            issues.add(path, "Required")
        return None
    if not isinstance(raw, Mapping):
        issues.add(path, "Expected object")
        return None
    return raw


def _optional_string(raw: Any, path: str, issues: _Issues) -> str | None:
    if raw is not None and not isinstance(raw, str):
        issues.add(path, "Expected string")
        return None
    return raw


def _network(raw: Mapping[str, Any], issues: _Issues) -> NetworkConfig:
    mitm: MitmProxyConfig | None = None
    mitm_raw = _object(raw.get("mitmProxy"), "network.mitmProxy", issues, optional=True)
    if mitm_raw is not None:
        socket_path = mitm_raw.get("socketPath")
        if not isinstance(socket_path, str) or not socket_path:
            issues.add("network.mitmProxy.socketPath", "String must contain at least 1 character(s)")
        domains = _domains(mitm_raw.get("domains"), "network.mitmProxy.domains", issues)
        if domains is not None and not domains:
            issues.add("network.mitmProxy.domains", "Array must contain at least 1 element(s)")
        if isinstance(socket_path, str) and socket_path and domains:
            mitm = MitmProxyConfig(socket_path=socket_path, domains=domains)
    return NetworkConfig(
        allowed_domains=_domains(raw.get("allowedDomains"), "network.allowedDomains", issues) or [],
        denied_domains=_domains(raw.get("deniedDomains"), "network.deniedDomains", issues) or [],
        allow_unix_sockets=_strings(raw.get("allowUnixSockets"), "network.allowUnixSockets", issues, optional=True),
        allow_all_unix_sockets=_boolean(raw.get("allowAllUnixSockets"), "network.allowAllUnixSockets", issues),
        allow_local_binding=_boolean(raw.get("allowLocalBinding"), "network.allowLocalBinding", issues),
        http_proxy_port=_int_in(raw.get("httpProxyPort"), "network.httpProxyPort", issues, 1, 65535),
        socks_proxy_port=_int_in(raw.get("socksProxyPort"), "network.socksProxyPort", issues, 1, 65535),
        mitm_proxy=mitm,
    )


def _filesystem(raw: Mapping[str, Any], issues: _Issues) -> FilesystemConfig:
    empty = "Path cannot be empty"
    return FilesystemConfig(
        deny_read=_strings(raw.get("denyRead"), "filesystem.denyRead", issues, min_length=1,
                           min_message=empty) or [],
        allow_read=_strings(raw.get("allowRead"), "filesystem.allowRead", issues, optional=True, min_length=1,
                            min_message=empty),
        allow_write=_strings(raw.get("allowWrite"), "filesystem.allowWrite", issues, min_length=1,
                             min_message=empty) or [],
        deny_write=_strings(raw.get("denyWrite"), "filesystem.denyWrite", issues, min_length=1,
                            min_message=empty) or [],
        allow_git_config=_boolean(raw.get("allowGitConfig"), "filesystem.allowGitConfig", issues),
    )


def parse_sandbox_runtime_config(raw: Any) -> SandboxRuntimeConfig:
    """`SandboxRuntimeConfigSchema.parse`: the camelCase document as a `SandboxRuntimeConfig`, or raise."""
    issues = _Issues()
    document = _object(raw, "(root)", issues)
    if document is None:
        raise SandboxConfigError("\n".join(issues.messages))
    network_raw = _object(document.get("network"), "network", issues)
    filesystem_raw = _object(document.get("filesystem"), "filesystem", issues)
    network = _network(network_raw or {}, issues)
    filesystem = _filesystem(filesystem_raw or {}, issues)
    ignore: dict[str, list[str]] | None = None
    ignore_raw = _object(document.get("ignoreViolations"), "ignoreViolations", issues, optional=True)
    if ignore_raw is not None:
        ignore = {}
        for key, value in ignore_raw.items():
            paths = _strings(value, f"ignoreViolations.{key}", issues)
            if paths is not None:
                ignore[str(key)] = paths
    ripgrep: RipgrepConfig | None = None
    ripgrep_raw = _object(document.get("ripgrep"), "ripgrep", issues, optional=True)
    if ripgrep_raw is not None:
        command = ripgrep_raw.get("command")
        if not isinstance(command, str):
            issues.add("ripgrep.command", "Required" if command is None else "Expected string")
        else:
            ripgrep = RipgrepConfig(
                command=command,
                args=_strings(ripgrep_raw.get("args"), "ripgrep.args", issues, optional=True),
                argv0=_optional_string(ripgrep_raw.get("argv0"), "ripgrep.argv0", issues),
            )
    seccomp: SeccompConfig | None = None
    seccomp_raw = _object(document.get("seccomp"), "seccomp", issues, optional=True)
    if seccomp_raw is not None:
        seccomp = SeccompConfig(
            bpf_path=_optional_string(seccomp_raw.get("bpfPath"), "seccomp.bpfPath", issues),
            apply_path=_optional_string(seccomp_raw.get("applyPath"), "seccomp.applyPath", issues),
        )
    config = SandboxRuntimeConfig(
        network=network,
        filesystem=filesystem,
        ignore_violations=ignore,
        enable_weaker_nested_sandbox=_boolean(document.get("enableWeakerNestedSandbox"),
                                              "enableWeakerNestedSandbox", issues),
        enable_weaker_network_isolation=_boolean(document.get("enableWeakerNetworkIsolation"),
                                                 "enableWeakerNetworkIsolation", issues),
        ripgrep=ripgrep,
        mandatory_deny_search_depth=_int_in(document.get("mandatoryDenySearchDepth"),
                                            "mandatoryDenySearchDepth", issues, 1, 10),
        allow_pty=_boolean(document.get("allowPty"), "allowPty", issues),
        seccomp=seccomp,
    )
    if issues.messages:
        raise SandboxConfigError("\n".join(issues.messages))
    return config


__all__ = [
    "INVALID_DOMAIN_PATTERN_MESSAGE",
    "FilesystemConfig",
    "MitmProxyConfig",
    "NetworkConfig",
    "RipgrepConfig",
    "SandboxConfigError",
    "SandboxRuntimeConfig",
    "SeccompConfig",
    "is_valid_domain_pattern",
    "parse_sandbox_runtime_config",
]
