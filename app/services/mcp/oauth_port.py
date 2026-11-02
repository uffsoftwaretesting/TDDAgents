"""
The OAuth redirect URI and the local port its callback server listens on.

Ported from `reference/claude-code/src/services/mcp/oauthPort.ts` -> `REDIRECT_PORT_RANGE`,
`REDIRECT_PORT_FALLBACK`, `buildRedirectUri`, `getMcpOAuthCallbackPort`, `findAvailablePort`.

The redirect URI is `http://localhost:<port>/callback` (RFC 8252 §7.3: a loopback redirect
matches any port). The port is `TDDAGENTS_MCP_OAUTH_CALLBACK_PORT` when it is a positive
integer (upstream's `MCP_OAUTH_CALLBACK_PORT`, renamed as the other `MCP_*` variables are),
else a random free port in the dynamic range (39152-49151 on Windows, 49152-65535 elsewhere),
at most `min(range, 100)` tries, then the fallback 3118; when that is taken too, it raises.
"""

from __future__ import annotations

import os
import random
import re
import socket
import sys

#: `REDIRECT_PORT_RANGE` (Windows reserves 49152-65535).
REDIRECT_PORT_RANGE = (39152, 49151) if sys.platform == "win32" else (49152, 65535)
#: `REDIRECT_PORT_FALLBACK`.
REDIRECT_PORT_FALLBACK = 3118
CALLBACK_PORT_ENV = "TDDAGENTS_MCP_OAUTH_CALLBACK_PORT"
#: `findAvailablePort`: "Don't try forever".
MAX_PORT_ATTEMPTS = 100

_LEADING_INT = re.compile(r"^\s*([+-]?\d+)")


def build_redirect_uri(port: int = REDIRECT_PORT_FALLBACK) -> str:
    """`buildRedirectUri`."""
    return f"http://localhost:{port}/callback"


def get_mcp_oauth_callback_port() -> int | None:
    """`getMcpOAuthCallbackPort`: `parseInt(env, 10)` when positive."""
    match = _LEADING_INT.match(os.environ.get(CALLBACK_PORT_ENV, ""))
    port = int(match.group(1)) if match else 0
    return port if port > 0 else None


def _port_is_free(port: int) -> bool:
    """`testServer.listen(port)`: Node binds the unspecified address."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("", port))
        except OSError:
            return False
    return True


async def find_available_port() -> int:
    """`findAvailablePort`."""
    configured = get_mcp_oauth_callback_port()
    if configured:
        return configured
    low, high = REDIRECT_PORT_RANGE
    span = high - low + 1
    for _ in range(min(span, MAX_PORT_ATTEMPTS)):
        port = low + random.randrange(span)
        if _port_is_free(port):
            return port
    if _port_is_free(REDIRECT_PORT_FALLBACK):
        return REDIRECT_PORT_FALLBACK
    raise OSError("No available ports for OAuth redirect")


__all__ = [
    "CALLBACK_PORT_ENV",
    "MAX_PORT_ATTEMPTS",
    "REDIRECT_PORT_FALLBACK",
    "REDIRECT_PORT_RANGE",
    "build_redirect_uri",
    "find_available_port",
    "get_mcp_oauth_callback_port",
]
