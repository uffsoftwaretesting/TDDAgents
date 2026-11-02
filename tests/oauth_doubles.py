"""A loopback HTTP server standing in for MCP servers and OAuth authorization servers (offline)."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit


@dataclass
class Seen:
    """One request the server received."""

    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    body: bytes

    @property
    def form(self) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(self.body.decode()).items()}

    @property
    def json(self) -> Any:
        return json.loads(self.body)


@dataclass
class Reply:
    status: int = 200
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)


Route = Reply | Callable[[Seen], Reply]


@dataclass
class FakeHttpServer:
    """Routes are `(METHOD, path) -> Reply | callable`; an unknown route answers 404."""

    base: str
    routes: dict[tuple[str, str], Route]
    seen: list[Seen]

    def route(self, method: str, path: str, reply: Route) -> None:
        self.routes[(method, path)] = reply

    def requests(self, method: str, path: str) -> list[Seen]:
        return [s for s in self.seen if s.method == method and s.path == path]


@contextmanager
def fake_http_server() -> Iterator[FakeHttpServer]:
    routes: dict[tuple[str, str], Route] = {}
    seen: list[Seen] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def _handle(self) -> None:
            parts = urlsplit(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            request = Seen(self.command, parts.path, parse_qs(parts.query), dict(self.headers.items()),
                           self.rfile.read(length) if length else b"")
            seen.append(request)
            route = routes.get((self.command, parts.path))
            reply = Reply(404, {"error": "not_found"}) if route is None else (
                route(request) if callable(route) else route)
            payload = reply.body if isinstance(reply.body, bytes) else (
                b"" if reply.body is None else json.dumps(reply.body).encode())
            self.send_response(reply.status)
            headers = {"Content-Type": "application/json", **reply.headers}
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = do_DELETE = _handle

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        yield FakeHttpServer(f"http://127.0.0.1:{server.server_address[1]}", routes, seen)
    finally:
        server.shutdown()
        server.server_close()


def as_metadata(base: str, **extra: Any) -> dict[str, Any]:
    """RFC 8414 metadata for an authorization server at `base`."""
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint": f"{base}/token",
        "registration_endpoint": f"{base}/register",
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": ["S256"],
        **extra,
    }


def install_authorization_server(
    server: FakeHttpServer, *, resource_path: str = "/mcp", scopes: list[str] | None = None, **metadata: Any
) -> None:
    """PRM for `resource_path`, AS metadata, DCR, and a token endpoint issuing `tok-<n>`."""
    base = server.base
    prm: dict[str, Any] = {"resource": f"{base}{resource_path}", "authorization_servers": [base]}
    if scopes is not None:
        prm["scopes_supported"] = scopes
    server.route("GET", f"/.well-known/oauth-protected-resource{resource_path}", Reply(200, prm))
    server.route("GET", "/.well-known/oauth-authorization-server", Reply(200, as_metadata(base, **metadata)))
    server.route("POST", "/register", lambda r: Reply(201, {**r.json, "client_id": "client-1"}))
    counter = {"n": 0}

    def token(request: Seen) -> Reply:
        counter["n"] += 1
        return Reply(200, {"access_token": f"tok-{counter['n']}", "token_type": "Bearer", "expires_in": 3600,
                           "refresh_token": f"ref-{counter['n']}", "scope": request.form.get("scope", "read")})

    server.route("POST", "/token", token)
