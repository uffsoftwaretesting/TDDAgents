"""
The sandbox's HTTP proxy: every HTTP and HTTPS request a sandboxed command makes, filtered by host.

Ported from `@anthropic-ai/sandbox-runtime` 0.0.46 `src/sandbox/http-proxy.ts` -> `createHttpProxyServer`:
* `CONNECT host:port` (HTTPS and any tunnel): a malformed target is `400 Bad Request`; a host the filter
  refuses is `403 Forbidden` with `X-Proxy-Error: blocked-by-allowlist` and the text
  "Connection blocked by network allowlist"; an allowed one is tunnelled
  (`200 Connection Established`), or `502 Bad Gateway` when the connection fails. A host matching the
  MITM configuration is tunnelled through the MITM proxy's Unix socket instead (its `CONNECT` answer
  must be a 200, else 502).
* An absolute-URI request (`GET http://host/path`): the same filter (`403` with the same header and
  text), then the request is forwarded to the host (TLS for `https:`) with its `Host` header set to
  the URL's host, and the response streamed back; `502 Bad Gateway` when the upstream fails, `500` when
  the request line is not an absolute URL.

Divergences: asyncio streams stand in for Node's `http` server, so the forwarding is byte-level: each
forwarded request is sent with `Connection: close` and the client connection closes after its response
(Node's server would read the next keep-alive request and filter it again; closing keeps every request
filtered). The filter receives no socket (upstream's third argument, unused by its manager).

**Mutation-exempt seam** (`[tool.mutmut] do_not_mutate`): the sockets. Verified end to end by
`tests/test_sandbox_proxies.py` against local servers (CONNECT, absolute URI, MITM; allow, deny, ask).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import ssl
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

logger = logging.getLogger("TDDOrchestrator.Sandbox")

#: How much of a request head is read before it is refused.
MAX_HEAD_BYTES = 64 * 1024

BLOCKED_TEXT = "Connection blocked by network allowlist"
CONNECT_FORBIDDEN = (b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain\r\n"
                     b"X-Proxy-Error: blocked-by-allowlist\r\n\r\n" + BLOCKED_TEXT.encode())
CONNECT_ESTABLISHED = b"HTTP/1.1 200 Connection Established\r\n\r\n"
BAD_REQUEST = b"HTTP/1.1 400 Bad Request\r\n\r\n"
BAD_GATEWAY = b"HTTP/1.1 502 Bad Gateway\r\n\r\n"
INTERNAL_ERROR = b"HTTP/1.1 500 Internal Server Error\r\n\r\n"

Filter = Callable[[int, str], Awaitable[bool]]
GetMitmSocketPath = Callable[[str], "str | None"]


@dataclass(frozen=True)
class HttpProxyServerOptions:
    """`HttpProxyServerOptions`."""

    filter: Filter
    get_mitm_socket_path: GetMitmSocketPath | None = None


def _response(status: str, body: str, *, blocked: bool = False) -> bytes:
    headers = "Content-Type: text/plain\r\n"
    if blocked:
        headers += "X-Proxy-Error: blocked-by-allowlist\r\n"
    data = body.encode()
    return (f"HTTP/1.1 {status}\r\n{headers}Content-Length: {len(data)}\r\nConnection: close\r\n\r\n").encode() + data


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, OSError, asyncio.CancelledError):
        pass
    finally:
        with contextlib.suppress(Exception):
            if writer.can_write_eof():
                writer.write_eof()


async def _splice(a_reader: asyncio.StreamReader, a_writer: asyncio.StreamWriter,
                  b_reader: asyncio.StreamReader, b_writer: asyncio.StreamWriter) -> None:
    await asyncio.gather(_pipe(a_reader, b_writer), _pipe(b_reader, a_writer))


async def _close(writer: asyncio.StreamWriter) -> None:
    with contextlib.suppress(Exception):
        writer.close()
        await writer.wait_closed()


async def _read_head(reader: asyncio.StreamReader) -> bytes | None:
    try:
        return await reader.readuntil(b"\r\n\r\n")
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
        return None


def _parse_head(head: bytes) -> tuple[str, str, str, list[tuple[str, str]]]:
    lines = head.decode("latin-1").split("\r\n")
    method, target, version = lines[0].split(" ", 2)
    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        if not line:
            continue
        name, _, value = line.partition(":")
        headers.append((name.strip(), value.strip()))
    return method, target, version, headers


async def _handle_connect(options: HttpProxyServerOptions, target: str,
                          reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    hostname, _, port_str = target.partition(":")
    port = int(port_str) if port_str.isdigit() else 0
    if not hostname or not port:
        logger.error("Invalid CONNECT request: %s", target)
        writer.write(BAD_REQUEST)
        return
    if not await options.filter(port, hostname):
        logger.error("Connection blocked to %s:%s", hostname, port)
        writer.write(CONNECT_FORBIDDEN)
        return
    mitm_socket_path = options.get_mitm_socket_path(hostname) if options.get_mitm_socket_path else None
    try:
        if mitm_socket_path:
            logger.debug("Routing CONNECT %s:%s through MITM proxy at %s", hostname, port, mitm_socket_path)
            up_reader, up_writer = await asyncio.open_unix_connection(mitm_socket_path)
            up_writer.write(f"CONNECT {hostname}:{port} HTTP/1.1\r\nHost: {hostname}:{port}\r\n\r\n".encode())
            await up_writer.drain()
            answer = await up_reader.readuntil(b"\r\n\r\n")
            status_line = answer.split(b"\r\n", 1)[0].decode("latin-1")
            if " 200 " not in status_line:
                logger.error("MITM proxy rejected CONNECT: %s", status_line)
                writer.write(BAD_GATEWAY)
                await _close(up_writer)
                return
        else:
            up_reader, up_writer = await asyncio.open_connection(hostname, port)
    except (OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as error:
        logger.error("CONNECT tunnel failed: %s", error)
        writer.write(BAD_GATEWAY)
        return
    writer.write(CONNECT_ESTABLISHED)
    await writer.drain()
    await _splice(reader, writer, up_reader, up_writer)
    await _close(up_writer)


async def _handle_request(options: HttpProxyServerOptions, method: str, target: str, version: str,
                          headers: list[tuple[str, str]], reader: asyncio.StreamReader,
                          writer: asyncio.StreamWriter) -> None:
    url = urlsplit(target)
    if url.scheme not in ("http", "https") or not url.hostname:
        logger.error("Error handling HTTP request: invalid URL %s", target)
        writer.write(_response("500 Internal Server Error", "Internal Server Error"))
        return
    hostname = url.hostname
    port = url.port or (443 if url.scheme == "https" else 80)
    if not await options.filter(port, hostname):
        logger.error("HTTP request blocked to %s:%s", hostname, port)
        writer.write(_response("403 Forbidden", BLOCKED_TEXT, blocked=True))
        return
    mitm_socket_path = options.get_mitm_socket_path(hostname) if options.get_mitm_socket_path else None
    path = (url.path or "/") + (f"?{url.query}" if url.query else "")
    try:
        if mitm_socket_path:
            logger.debug("Routing HTTP %s %s:%s through MITM proxy at %s", method, hostname, port, mitm_socket_path)
            up_reader, up_writer = await asyncio.open_unix_connection(mitm_socket_path)
            path = target
        elif url.scheme == "https":
            up_reader, up_writer = await asyncio.open_connection(hostname, port, ssl=ssl.create_default_context())
        else:
            up_reader, up_writer = await asyncio.open_connection(hostname, port)
    except OSError as error:
        logger.error("Proxy request failed: %s", error)
        writer.write(_response("502 Bad Gateway", "Bad Gateway"))
        return
    kept = [(name, value) for name, value in headers
            if name.lower() not in ("host", "connection", "proxy-connection", "keep-alive")]
    head = f"{method} {path} {version}\r\nHost: {url.netloc.rpartition('@')[2]}\r\n"
    head += "".join(f"{name}: {value}\r\n" for name, value in kept) + "Connection: close\r\n\r\n"
    up_writer.write(head.encode("latin-1"))
    await up_writer.drain()
    # The request body flows up while the response flows back; the response's end ends the exchange.
    upload = asyncio.ensure_future(_pipe(reader, up_writer))
    try:
        await _pipe(up_reader, writer)
    finally:
        upload.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await upload
        await _close(up_writer)


def create_http_proxy_handler(options: HttpProxyServerOptions) -> Callable[
        [asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]:
    """The connection handler `createHttpProxyServer` installs (`asyncio.start_server` serves it)."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await _read_head(reader)
            if head is None:
                return
            try:
                method, target, version, headers = _parse_head(head)
            except ValueError:
                writer.write(BAD_REQUEST)
                return
            if method.upper() == "CONNECT":
                await _handle_connect(options, target, reader, writer)
            else:
                await _handle_request(options, method, target, version, headers, reader, writer)
        except Exception as error:  # noqa: BLE001 - upstream answers any handler failure with a 500
            logger.error("Error handling proxy request: %s", error)
            with contextlib.suppress(Exception):
                writer.write(INTERNAL_ERROR)
        finally:
            with contextlib.suppress(Exception):
                await writer.drain()
            await _close(writer)

    return handle


async def start_http_proxy_server(options: HttpProxyServerOptions, host: str = "127.0.0.1",
                                  port: int = 0) -> tuple[asyncio.Server, int]:
    """`startHttpProxyServer`'s listen: the server on `host` (an ephemeral port by default) and its port."""
    server = await asyncio.start_server(create_http_proxy_handler(options), host, port, limit=MAX_HEAD_BYTES)
    bound = server.sockets[0].getsockname()[1]
    logger.debug("HTTP proxy listening on localhost:%s", bound)
    return server, int(bound)


__all__ = [
    "BLOCKED_TEXT",
    "HttpProxyServerOptions",
    "create_http_proxy_handler",
    "start_http_proxy_server",
]
