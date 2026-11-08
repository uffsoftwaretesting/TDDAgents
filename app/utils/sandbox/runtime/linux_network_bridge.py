"""
The host side of the network bridge into bubblewrap's empty network namespace.

Ported from `@anthropic-ai/sandbox-runtime` 0.0.44 `src/sandbox/linux-sandbox-utils.ts` ->
`LinuxNetworkBridgeContext`, `initializeLinuxNetworkBridge`, and the bridge half of
`sandbox-manager.ts` -> `reset` (SIGTERM, a 5 s wait, then SIGKILL; the sockets removed). Two socat
processes listen on Unix sockets in the temp directory (`tddagents-http-<id>.sock`,
`tddagents-socks-<id>.sock`; upstream `claude-*`) and forward to the host's HTTP and SOCKS proxies; the
sandbox binds those sockets in and runs its own socat listeners on 3128 and 1080 (`buildSandboxCommand`).
Both sockets must appear within five attempts (sleeping 0, 100, 200, 300 ms), else both processes are
stopped and the start fails.

**Mutation-exempt seam** (`[tool.mutmut] do_not_mutate`): the socat processes. Surface: `subprocess.Popen`
(`stdin`/`stdout`/`stderr` to `DEVNULL`), `terminate`, `wait(timeout=)`, `kill`. Verified end to end by
`tests/test_sandbox_manager.py` with a fake `socat` on `PATH` that creates the socket path it is given.
"""

from __future__ import annotations

import contextlib
import logging
import os
import secrets
import subprocess
import tempfile
import time
from dataclasses import dataclass

logger = logging.getLogger("TDDOrchestrator.Sandbox")

#: `initializeLinuxNetworkBridge`'s `maxAttempts`.
BRIDGE_MAX_ATTEMPTS = 5
#: `reset`'s wait for a bridge process before SIGKILL, in seconds.
BRIDGE_EXIT_TIMEOUT_SECONDS = 5


class BridgeError(RuntimeError):
    """The bridge could not start (upstream's thrown `Error`s, same messages)."""


@dataclass
class LinuxNetworkBridgeContext:
    """`LinuxNetworkBridgeContext`."""

    http_socket_path: str
    socks_socket_path: str
    http_bridge_process: subprocess.Popen[bytes]
    socks_bridge_process: subprocess.Popen[bytes]
    http_proxy_port: int
    socks_proxy_port: int


def _socat_args(socket_path: str, port: int) -> list[str]:
    return [f"UNIX-LISTEN:{socket_path},fork,reuseaddr",
            f"TCP:localhost:{port},keepalive,keepidle=10,keepintvl=5,keepcnt=3"]


def _spawn(args: list[str]) -> subprocess.Popen[bytes]:
    logger.debug("Starting bridge: socat %s", " ".join(args))
    return subprocess.Popen(["socat", *args], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def _terminate(process: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(ProcessLookupError, OSError):
        process.terminate()


def initialize_linux_network_bridge(http_proxy_port: int, socks_proxy_port: int,
                                    tmpdir: str | None = None) -> LinuxNetworkBridgeContext:
    """`initializeLinuxNetworkBridge`: both socat bridges started and their sockets present."""
    socket_id = secrets.token_hex(8)
    base = tmpdir if tmpdir is not None else tempfile.gettempdir()
    http_socket_path = os.path.join(base, f"tddagents-http-{socket_id}.sock")
    socks_socket_path = os.path.join(base, f"tddagents-socks-{socket_id}.sock")
    try:
        http_process = _spawn(_socat_args(http_socket_path, http_proxy_port))
    except OSError as error:
        raise BridgeError("Failed to start HTTP bridge process") from error
    try:
        socks_process = _spawn(_socat_args(socks_socket_path, socks_proxy_port))
    except OSError as error:
        _terminate(http_process)
        raise BridgeError("Failed to start SOCKS bridge process") from error
    for attempt in range(BRIDGE_MAX_ATTEMPTS):
        if http_process.poll() is not None or socks_process.poll() is not None:
            _terminate(http_process)
            _terminate(socks_process)
            raise BridgeError("Linux bridge process died unexpectedly")
        if os.path.exists(http_socket_path) and os.path.exists(socks_socket_path):
            logger.debug("Linux bridges ready after %s attempts", attempt + 1)
            break
        if attempt == BRIDGE_MAX_ATTEMPTS - 1:
            _terminate(http_process)
            _terminate(socks_process)
            raise BridgeError(f"Failed to create bridge sockets after {BRIDGE_MAX_ATTEMPTS} attempts")
        time.sleep(attempt * 0.1)
    return LinuxNetworkBridgeContext(http_socket_path, socks_socket_path, http_process, socks_process,
                                     http_proxy_port, socks_proxy_port)


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    _terminate(process)
    try:
        process.wait(timeout=BRIDGE_EXIT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        logger.warning("Bridge did not exit, forcing SIGKILL")
        with contextlib.suppress(ProcessLookupError, OSError):
            process.kill()
            process.wait(timeout=BRIDGE_EXIT_TIMEOUT_SECONDS)


def shutdown_linux_network_bridge(bridge: LinuxNetworkBridgeContext) -> None:
    """`reset`'s bridge half: both processes stopped, both sockets removed."""
    _stop(bridge.http_bridge_process)
    _stop(bridge.socks_bridge_process)
    for path in (bridge.http_socket_path, bridge.socks_socket_path):
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)


__all__ = [
    "BRIDGE_EXIT_TIMEOUT_SECONDS",
    "BRIDGE_MAX_ATTEMPTS",
    "BridgeError",
    "LinuxNetworkBridgeContext",
    "initialize_linux_network_bridge",
    "shutdown_linux_network_bridge",
]
