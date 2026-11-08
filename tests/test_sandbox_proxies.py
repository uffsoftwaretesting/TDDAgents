"""
The sandbox's HTTP and SOCKS5 proxies end to end, against local servers: CONNECT tunnels, absolute-URI
requests, SOCKS5 connects, the MITM route, and the filter's allow / deny / ask (the ask answered on another
event loop, as the REPL's or the SDK host's).
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import struct
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from app.utils.sandbox.runtime.network_host import HostNetworkContext, call_on_loop, start_host_network

HostSeen = list[tuple[int, str]]


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server's naming
        body = f"path={self.path} host={self.headers.get('Host')} conn={self.headers.get('Connection')}".encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - http.server's naming
        data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.send_response(201)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args: Any) -> None:
        pass


@pytest.fixture
def upstream() -> Iterator[int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


@pytest.fixture
def echo() -> Iterator[int]:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    stop = threading.Event()

    def serve() -> None:
        listener.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except OSError:
                continue
            with conn:
                data = conn.recv(1024)
                conn.sendall(b"echo:" + data)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield listener.getsockname()[1]
    stop.set()
    thread.join()
    listener.close()


def _closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@contextlib.contextmanager
def proxies(allowed: Callable[[int, str], bool], mitm: str | None = None) -> Iterator[
        tuple[HostNetworkContext, HostSeen]]:
    seen: HostSeen = []

    async def filter_request(port: int, host: str) -> bool:
        seen.append((port, host))
        return allowed(port, host)

    context = start_host_network(filter_request, get_mitm_socket_path=(lambda h: mitm if h == "mitm.test" else None),
                                 linux=False)
    try:
        yield context, seen
    finally:
        context.close()


def _recv_all(conn: socket.socket) -> bytes:
    chunks = []
    while chunk := conn.recv(65536):
        chunks.append(chunk)
    return b"".join(chunks)


def _first_answer(port: int, data: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as conn:
        conn.sendall(data)
        return conn.recv(1024)


def _send(port: int, data: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as conn:
        conn.sendall(data)
        return _recv_all(conn)


# ── HTTP proxy ──────────────────────────────────────────────────────────────────


def test_connect_to_an_allowed_host_is_tunnelled(echo: int) -> None:
    with proxies(lambda _p, _h: True) as (context, seen):
        with socket.create_connection(("127.0.0.1", context.http_proxy_port), timeout=5) as conn:
            conn.sendall(f"CONNECT 127.0.0.1:{echo} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
            assert conn.recv(1024) == b"HTTP/1.1 200 Connection Established\r\n\r\n"
            conn.sendall(b"ping")
            assert conn.recv(1024) == b"echo:ping"
    assert seen == [(echo, "127.0.0.1")]


def test_connect_to_a_refused_host_is_forbidden_by_the_allowlist() -> None:
    with proxies(lambda _p, _h: False) as (context, seen):
        answer = _send(context.http_proxy_port, b"CONNECT evil.test:443 HTTP/1.1\r\n\r\n")
    assert answer == (b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain\r\nX-Proxy-Error: blocked-by-allowlist"
                      b"\r\n\r\nConnection blocked by network allowlist")
    assert seen == [(443, "evil.test")]


@pytest.mark.parametrize("target", ["noport", ":443", "host:0", "host:abc"])
def test_a_malformed_connect_target_is_a_bad_request(target: str) -> None:
    with proxies(lambda _p, _h: True) as (context, seen):
        assert _send(context.http_proxy_port, f"CONNECT {target} HTTP/1.1\r\n\r\n".encode()) == (
            b"HTTP/1.1 400 Bad Request\r\n\r\n")
    assert seen == []


def test_connect_to_a_closed_port_is_a_bad_gateway() -> None:
    port = _closed_port()
    with proxies(lambda _p, _h: True) as (context, _seen):
        assert _send(context.http_proxy_port, f"CONNECT 127.0.0.1:{port} HTTP/1.1\r\n\r\n".encode()) == (
            b"HTTP/1.1 502 Bad Gateway\r\n\r\n")


def test_an_absolute_uri_request_is_forwarded_with_its_host_and_closed(upstream: int) -> None:
    with proxies(lambda _p, _h: True) as (context, seen):
        answer = _send(context.http_proxy_port,
                       f"GET http://127.0.0.1:{upstream}/a/b?q=1 HTTP/1.1\r\nHost: wrong\r\n"
                       f"Proxy-Connection: keep-alive\r\n\r\n".encode())
    assert answer.startswith(b"HTTP/1.0 200") or answer.startswith(b"HTTP/1.1 200")
    assert answer.endswith(f"path=/a/b?q=1 host=127.0.0.1:{upstream} conn=close".encode())
    assert seen == [(upstream, "127.0.0.1")]


def test_a_request_body_reaches_the_upstream(upstream: int) -> None:
    with proxies(lambda _p, _h: True) as (context, _seen):
        answer = _send(context.http_proxy_port,
                       f"POST http://127.0.0.1:{upstream}/ HTTP/1.1\r\nContent-Length: 5\r\n\r\nhello".encode())
    assert b" 201 " in answer.split(b"\r\n")[0] and answer.endswith(b"hello")


def test_an_absolute_uri_request_to_a_refused_host_is_forbidden() -> None:
    with proxies(lambda _p, _h: False) as (context, seen):
        answer = _send(context.http_proxy_port, b"GET http://evil.test/ HTTP/1.1\r\n\r\n")
    head, _, body = answer.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 403 Forbidden") and b"X-Proxy-Error: blocked-by-allowlist" in head
    assert body == b"Connection blocked by network allowlist"
    assert seen == [(80, "evil.test")]


def test_https_defaults_to_443_and_a_relative_target_is_an_internal_error() -> None:
    with proxies(lambda _p, _h: False) as (context, seen):
        _send(context.http_proxy_port, b"GET https://secure.test/x HTTP/1.1\r\n\r\n")
        relative = _send(context.http_proxy_port, b"GET /relative HTTP/1.1\r\n\r\n")
        garbage = _send(context.http_proxy_port, b"NONSENSE\r\n\r\n")
    assert seen == [(443, "secure.test")]
    assert relative.startswith(b"HTTP/1.1 500 Internal Server Error")
    assert garbage == b"HTTP/1.1 400 Bad Request\r\n\r\n"


def test_an_unreachable_upstream_is_a_bad_gateway() -> None:
    port = _closed_port()
    with proxies(lambda _p, _h: True) as (context, _seen):
        answer = _send(context.http_proxy_port, f"GET http://127.0.0.1:{port}/ HTTP/1.1\r\n\r\n".encode())
    assert answer.startswith(b"HTTP/1.1 502 Bad Gateway")


def test_a_mitm_host_is_tunnelled_through_the_mitm_socket(tmp_path: Path) -> None:
    socket_path = str(tmp_path / "mitm.sock")
    requests: list[bytes] = []

    async def mitm_main(ready: threading.Event, stop: asyncio.Event) -> None:
        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            requests.append(await reader.readuntil(b"\r\n\r\n"))
            writer.write(b"HTTP/1.1 200 OK\r\n\r\nearly")
            data = await reader.read(100)
            writer.write(b"mitm:" + data)
            await writer.drain()
            writer.close()

        server = await asyncio.start_unix_server(handle, socket_path)
        ready.set()
        await stop.wait()
        server.close()

    loop = asyncio.new_event_loop()
    ready, stop = threading.Event(), asyncio.Event()
    thread = threading.Thread(target=lambda: loop.run_until_complete(mitm_main(ready, stop)), daemon=True)
    thread.start()
    ready.wait(5)
    try:
        with proxies(lambda _p, _h: True, mitm=socket_path) as (context, _seen):
            with socket.create_connection(("127.0.0.1", context.http_proxy_port), timeout=5) as conn:
                conn.sendall(b"CONNECT mitm.test:443 HTTP/1.1\r\n\r\n")
                assert _recv_until(conn, b"early") == b"HTTP/1.1 200 Connection Established\r\n\r\nearly"
                conn.sendall(b"hi")
                assert conn.recv(100) == b"mitm:hi"
    finally:
        loop.call_soon_threadsafe(stop.set)
        thread.join(5)
    assert requests == [b"CONNECT mitm.test:443 HTTP/1.1\r\nHost: mitm.test:443\r\n\r\n"]


def test_a_mitm_proxy_that_refuses_is_a_bad_gateway(tmp_path: Path) -> None:
    socket_path = str(tmp_path / "refuse.sock")
    server = socket.socket(socket.AF_UNIX)
    server.bind(socket_path)
    server.listen()

    def refuse() -> None:
        conn, _ = server.accept()
        conn.recv(1024)
        conn.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n")
        conn.close()

    thread = threading.Thread(target=refuse, daemon=True)
    thread.start()
    with proxies(lambda _p, _h: True, mitm=socket_path) as (context, _seen):
        assert _send(context.http_proxy_port, b"CONNECT mitm.test:443 HTTP/1.1\r\n\r\n") == (
            b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
    thread.join(5)
    server.close()


def _recv_until(conn: socket.socket, marker: bytes) -> bytes:
    data = b""
    while marker not in data:
        chunk = conn.recv(1024)
        if not chunk:
            break
        data += chunk
    return data


# ── SOCKS5 proxy ────────────────────────────────────────────────────────────────


def _socks_connect(port: int, address: bytes, dest_port: int, *, methods: bytes = b"\x00",
                   command: int = 1) -> tuple[bytes, socket.socket]:
    conn = socket.create_connection(("127.0.0.1", port), timeout=5)
    conn.sendall(b"\x05" + bytes([len(methods)]) + methods)
    greeting = conn.recv(2)
    if greeting != b"\x05\x00":
        return greeting, conn
    conn.sendall(b"\x05" + bytes([command]) + b"\x00" + address + struct.pack(">H", dest_port))
    return conn.recv(10), conn


def test_socks_connects_an_allowed_domain_and_splices(echo: int) -> None:
    with proxies(lambda _p, _h: True) as (context, seen):
        reply, conn = _socks_connect(context.socks_proxy_port, b"\x03\x09localhost", echo)
        with conn:
            assert reply == b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00"
            conn.sendall(b"git")
            assert conn.recv(100) == b"echo:git"
    assert seen == [(echo, "localhost")]


def test_socks_refuses_a_denied_destination_with_status_two(echo: int) -> None:
    with proxies(lambda _p, _h: False) as (context, seen):
        reply, conn = _socks_connect(context.socks_proxy_port, b"\x01\x7f\x00\x00\x01", echo)
        conn.close()
    assert reply == b"\x05\x02\x00\x01\x00\x00\x00\x00\x00\x00"
    assert seen == [(echo, "127.0.0.1")]


def test_socks_reports_a_refused_connection_and_unsupported_requests() -> None:
    closed = _closed_port()
    with proxies(lambda _p, _h: True) as (context, seen):
        refused, conn = _socks_connect(context.socks_proxy_port, b"\x01\x7f\x00\x00\x01", closed)
        conn.close()
        no_method, conn = _socks_connect(context.socks_proxy_port, b"", 1, methods=b"\x02")
        conn.close()
        bind, conn = _socks_connect(context.socks_proxy_port, b"\x01\x7f\x00\x00\x01", 1, command=2)
        conn.close()
        ipv6, conn = _socks_connect(context.socks_proxy_port, b"\x04" + bytes(15) + b"\x01", closed)
        conn.close()
    assert refused[:2] == b"\x05\x05"
    assert no_method == b"\x05\xff"
    assert bind == b"\x05\x07"
    assert ipv6[:2] in (b"\x05\x05", b"\x05\x04", b"\x05\x01")
    assert (closed, "0000:0000:0000:0000:0000:0000:0000:0001") in seen


def test_a_failing_socks_filter_refuses() -> None:
    def boom(_port: int, _host: str) -> bool:
        raise RuntimeError("filter failed")

    with proxies(boom) as (context, _seen):
        reply, conn = _socks_connect(context.socks_proxy_port, b"\x03\x04x.io", 22)
        conn.close()
    assert reply[:2] == b"\x05\x02"


# ── the ask, across loops ───────────────────────────────────────────────────────


def test_an_ask_is_answered_on_the_callers_loop(echo: int) -> None:
    asked: list[tuple[Any, bool]] = []

    async def scenario() -> tuple[bytes, bytes]:
        caller = asyncio.get_running_loop()

        async def ask(pattern: Any) -> bool:
            # Runs on the caller's loop, not on the proxies' thread.
            asked.append((dict(pattern), asyncio.get_running_loop() is caller))
            return bool(pattern["port"] == echo)

        ask_here = call_on_loop(ask, caller)

        async def filter_request(port: int, host: str) -> bool:
            return await ask_here({"host": host, "port": port})

        context = start_host_network(filter_request, linux=False)
        try:
            allowed = await asyncio.to_thread(
                _first_answer, context.http_proxy_port, f"CONNECT 127.0.0.1:{echo} HTTP/1.1\r\n\r\n".encode())
            denied = await asyncio.to_thread(_send, context.http_proxy_port, b"CONNECT other.test:1 HTTP/1.1\r\n\r\n")
            return allowed, denied
        finally:
            context.close()

    allowed, denied = asyncio.run(scenario())
    assert allowed.startswith(b"HTTP/1.1 200 Connection Established")
    assert denied.startswith(b"HTTP/1.1 403 Forbidden")
    assert asked == [({"host": "127.0.0.1", "port": echo}, True), ({"host": "other.test", "port": 1}, True)]


def test_call_on_loop_without_a_loop_is_the_callback_itself() -> None:
    async def ask(_pattern: Any) -> bool:
        return True

    assert call_on_loop(ask, None) is ask


def test_external_proxy_ports_start_no_servers() -> None:
    async def never(_port: int, _host: str) -> bool:
        return False

    context = start_host_network(never, http_proxy_port=8123, socks_proxy_port=8124, linux=False)
    assert (context.http_proxy_port, context.socks_proxy_port, context.proxy_loop, context.servers) == (
        8123, 8124, None, [])
    context.close()
