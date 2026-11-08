"""
Locating the seccomp filter that blocks Unix-socket creation, and the binary that applies it.

Ported from `@anthropic-ai/sandbox-runtime` 0.0.44 `src/sandbox/generate-seccomp-filter.ts` ->
`getPreGeneratedBpfPath`, `getApplySeccompBinaryPath`, `generateSeccompFilter`, `cleanupSeccompFilter`,
`getVendorArchitecture`. The package's `vendor/seccomp/{x64,arm64}/` (`apply-seccomp`,
`unix-block.bpf`, built from `vendor/seccomp-src/`) is vendored beside this module, as upstream bundles
it beside its code. An explicit path (`sandbox.seccomp.bpfPath` / `applyPath`) wins when it exists.

Not ported: the global-npm fallback (`getGlobalNpmPaths`, `npm root -g`), which serves upstream's
native builds without a bundled `vendor/`; TDDAgents always ships its copy. The lookups are memoized
per explicit path, as upstream's caches.
"""

from __future__ import annotations

import logging
import os
import platform as host_platform
from pathlib import Path

logger = logging.getLogger("TDDOrchestrator.Sandbox")

#: The vendored `vendor/seccomp/` directory.
VENDOR_SECCOMP_DIR = Path(__file__).resolve().parent / "vendor" / "seccomp"

BPF_FILENAME = "unix-block.bpf"
APPLY_SECCOMP_FILENAME = "apply-seccomp"

_bpf_path_cache: dict[str, str | None] = {}
_apply_seccomp_path_cache: dict[str, str | None] = {}


def get_vendor_architecture(machine: str | None = None) -> str | None:
    """
    `getVendorArchitecture`: `x64` or `arm64`; `None` elsewhere, 32-bit x86 included (its `socketcall()`
    multiplexer would bypass a filter that only blocks `socket(AF_UNIX, ...)`).
    """
    arch = (machine if machine is not None else host_platform.machine()).lower()
    if arch in ("x64", "x86_64", "amd64"):
        return "x64"
    if arch in ("arm64", "aarch64"):
        return "arm64"
    if arch in ("ia32", "x86", "i386", "i686"):
        logger.error("[SeccompFilter] 32-bit x86 (ia32) is not currently supported due to missing socketcall() "
                     "syscall blocking.")
        return None
    logger.debug("[SeccompFilter] Unsupported architecture: %s. Only x64 and arm64 are supported.", arch)
    return None


def _find(explicit: str | None, filename: str, vendor_dir: Path, machine: str | None) -> str | None:
    if explicit:
        if os.path.exists(explicit):
            return explicit
        logger.debug("[SeccompFilter] Explicit path provided but file not found: %s", explicit)
    arch = get_vendor_architecture(machine)
    if arch is None:
        return None
    candidate = vendor_dir / arch / filename
    if candidate.exists():
        return str(candidate)
    logger.debug("[SeccompFilter] %s not found in any expected location (%s)", filename, arch)
    return None


def get_pre_generated_bpf_path(explicit_path: str | None = None, *, vendor_dir: Path = VENDOR_SECCOMP_DIR,
                               machine: str | None = None) -> str | None:
    """`getPreGeneratedBpfPath`: the BPF filter for this architecture, or `None`."""
    key = f"{explicit_path or ''}\0{vendor_dir}\0{machine or ''}"
    if key not in _bpf_path_cache:
        _bpf_path_cache[key] = _find(explicit_path, BPF_FILENAME, vendor_dir, machine)
    return _bpf_path_cache[key]


def get_apply_seccomp_binary_path(explicit_path: str | None = None, *, vendor_dir: Path = VENDOR_SECCOMP_DIR,
                                  machine: str | None = None) -> str | None:
    """`getApplySeccompBinaryPath`: the `apply-seccomp` binary for this architecture, or `None`."""
    key = f"{explicit_path or ''}\0{vendor_dir}\0{machine or ''}"
    if key not in _apply_seccomp_path_cache:
        _apply_seccomp_path_cache[key] = _find(explicit_path, APPLY_SECCOMP_FILENAME, vendor_dir, machine)
    return _apply_seccomp_path_cache[key]


def generate_seccomp_filter(explicit_path: str | None = None) -> str | None:
    """`generateSeccompFilter`: the pre-generated filter's path (nothing is generated at run time)."""
    path = get_pre_generated_bpf_path(explicit_path)
    if path is None:
        logger.error("[SeccompFilter] Pre-generated BPF filter not available for this architecture. "
                     "Only x64 and arm64 are supported.")
    return path


def cleanup_seccomp_filter(_filter_path: str) -> None:
    """`cleanupSeccompFilter`: a no-op, pre-generated filters are never deleted."""


def clear_seccomp_path_caches() -> None:
    """Forget the memoized lookups (tests)."""
    _bpf_path_cache.clear()
    _apply_seccomp_path_cache.clear()


__all__ = [
    "APPLY_SECCOMP_FILENAME",
    "BPF_FILENAME",
    "VENDOR_SECCOMP_DIR",
    "cleanup_seccomp_filter",
    "clear_seccomp_path_caches",
    "generate_seccomp_filter",
    "get_apply_seccomp_binary_path",
    "get_pre_generated_bpf_path",
    "get_vendor_architecture",
]
