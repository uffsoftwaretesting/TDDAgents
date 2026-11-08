"""
The sandbox's SOCKS5 proxy: every non-HTTP connection a sandboxed command makes (git over SSH, raw TCP).

Ported from `@anthropic-ai/sandbox-runtime` 0.0.44 `src/sandbox/socks-proxy.ts` ->
`createSocksProxyServer`, and the server it builds on (`@pondwader/socks5-server`: the no-auth
greeting, the request parsing, `setRulesetValidator`, the default connection handler and its status
codes). A greeting without "no authentication" gets `05 FF`; a command other than CONNECT gets
`05 07`; a destination the filter refuses gets `05 02` (connection not allowed by ruleset); a
connection that fails gets the status for its cause (refused, host or network unreachable, general
failure); an established one gets `05 00` and is spliced both ways.

**Mutation-exempt seam** (`[tool.mutmut] do_not_mutate`): the sockets. Verified end to end by
`tests/test_sandbox_proxies.py` against a local server (allow, deny, ask).
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger("TDDOrchestrator.Sandbox")

Filter = Callable[[int, str], Awaitable[bool]]

SOCKS_VERSION = 5
NO_AUTH = 0
NO_ACCEPTABLE_METHODS = 0xFF
CMD_CONNECT = 1
KNOWN_COMMANDS = (1, 2, 3)

REQUEST_GRANTED = 0
GENERAL_FAILURE = 1
CONNECTION_NOT_ALLOWED = 2
NETWORK_UNREACHABLE = 3
HOST_UNREACHABLE = 4
CONNECTION_REFUSED = 5
COMMAND_NOT_SUPPORTED = 7

_HOST_UNREACHABLE_ERRNOS = {errno.EINVAL, errno.ENOENT, errno.ETIMEDOUT, errno.EADDRNOTAVAIL, errno.EHOSTUNREACH}


def _reply(status: int) -> bytes:
    return bytes([SOCKS_VERSION, status, 0, 1, 0, 0, 0, 0, 0, 0])


def _status_for(error: OSError) -> int:
    """The default connection handler's mapping of a connect error to a SOCKS status."""
    import socket

    if isinstance(error, socket.gaierror) or error.errno in _HOST_UNREACHABLE_ERRNOS:
        return HOST_UNREACHABLE
    if error.errno == errno.ENETUNREACH:
        return NETWORK_UNREACHABLE
    if error.errno == errno.ECONNREFUSED:
        return CONNECTION_REFUSED
    return GENERAL_FAILURE


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await reader.read(65536):
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, OSError, asyncio.CancelledError):
        pass
    finally:
        with contextlib.suppress(Exception):
            if writer.can_write_eof():
                writer.write_eof()


async def _read_address(reader: asyncio.StreamReader, address_type: int) -> str | None:
    if address_type == 1:
        return ".".join(str(b) for b in await reader.readexactly(4))
    if address_type == 3:
        length = (await reader.readexactly(1))[0]
        return (await reader.readexactly(length)).decode("utf-8", errors="replace")
    if address_type == 4:
        raw = await reader.readexactly(16)
        return ":".join(f"{raw[i]:02x}{raw[i + 1]:02x}" for i in range(0, 16, 2))
    return None


def create_socks_handler(filter_request: Filter) -> Callable[
        [asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]:
    """The connection handler of `createSocksProxyServer` (its ruleset validator calls `filter_request`)."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            version, methods_count = await reader.readexactly(2)
            if version != SOCKS_VERSION or methods_count == 0 or methods_count > 128:
                return
            methods = await reader.readexactly(methods_count)
            if NO_AUTH not in methods:
                writer.write(bytes([SOCKS_VERSION, NO_ACCEPTABLE_METHODS]))
                return
            writer.write(bytes([SOCKS_VERSION, NO_AUTH]))
            await writer.drain()
            _version, command, _reserved, address_type = await reader.readexactly(4)
            if command not in KNOWN_COMMANDS:
                return
            host = await _read_address(reader, address_type)
            if host is None:
                return
            port = int.from_bytes(await reader.readexactly(2), "big")
            if command != CMD_CONNECT:
                writer.write(bytes([SOCKS_VERSION, COMMAND_NOT_SUPPORTED]))
                return
            logger.debug("Connection request to %s:%s", host, port)
            try:
                allowed = await filter_request(port, host)
            except Exception as error:  # noqa: BLE001 - a failing validation refuses, as upstream's catch
                logger.error("Error validating connection: %s", error)
                allowed = False
            if not allowed:
                logger.error("Connection blocked to %s:%s", host, port)
                writer.write(_reply(CONNECTION_NOT_ALLOWED))
                return
            try:
                up_reader, up_writer = await asyncio.open_connection(host, port)
            except OSError as error:
                writer.write(_reply(_status_for(error)))
                return
            writer.write(_reply(REQUEST_GRANTED))
            await writer.drain()
            await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))
            with contextlib.suppress(Exception):
                up_writer.close()
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            with contextlib.suppress(Exception):
                await writer.drain()
                writer.close()

    return handle


async def start_socks_proxy_server(filter_request: Filter, host: str = "127.0.0.1",
                                   port: int = 0) -> tuple[asyncio.Server, int]:
    """`startSocksProxyServer`'s listen: the server on `host` (an ephemeral port by default) and its port."""
    server = await asyncio.start_server(create_socks_handler(filter_request), host, port)
    bound = int(server.sockets[0].getsockname()[1])
    logger.debug("SOCKS proxy listening on %s:%s", host, bound)
    return server, bound


__all__ = [
    "COMMAND_NOT_SUPPORTED",
    "CONNECTION_NOT_ALLOWED",
    "CONNECTION_REFUSED",
    "GENERAL_FAILURE",
    "HOST_UNREACHABLE",
    "NETWORK_UNREACHABLE",
    "REQUEST_GRANTED",
    "create_socks_handler",
    "start_socks_proxy_server",
]
