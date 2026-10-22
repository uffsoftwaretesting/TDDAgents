"""Tests for the HTTP hook executor (port of `execHttpHook.ts`)."""

from __future__ import annotations

import asyncio
import http.server
import threading
from collections.abc import Iterator
from typing import Any, ClassVar

import pytest

from app.loop.hooks.exec_http import (
    DEFAULT_HTTP_HOOK_TIMEOUT_MS,
    HttpHookPolicy,
    HttpRequest,
    HttpResponse,
    default_transport,
    exec_http_hook,
    get_no_proxy,
    get_proxy_url,
    http_hook_policy_from_settings,
    interpolate_env_vars,
    sanitize_header_value,
    should_bypass_proxy,
    url_matches_pattern,
)
from app.loop.hooks.schemas import HttpHook
from app.loop.hooks.ssrf_guard import SsrfBlockedError


class FakeTransport:
    def __init__(self, response: HttpResponse | None = None, error: Exception | None = None, delay: float = 0) -> None:
        self.response = response or HttpResponse(status=200, body="{}")
        self.error = error
        self.delay = delay
        self.requests: list[HttpRequest] = []

    async def __call__(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.response


class Signal:
    def __init__(self, aborted: bool = False) -> None:
        self.aborted = aborted


def run(hook: HttpHook, transport: Any, policy: HttpHookPolicy | None = None, signal: Any = None,
        env: dict[str, str] | None = None, resolver: Any = None) -> Any:
    return asyncio.run(
        exec_http_hook(
            hook, "PreToolUse", '{"x": 1}', signal,
            policy=policy or HttpHookPolicy(), transport=transport,
            env=env if env is not None else {}, resolver=resolver,
        )
    )


# ── policy / helpers ─────────────────────────────────────────────────────────

def test_policy_from_settings() -> None:
    p = http_hook_policy_from_settings(
        {"allowedHttpHookUrls": ["https://a/*", 3], "httpHookAllowedEnvVars": ["TOKEN"]}, {"ignored": True}
    )
    assert p == HttpHookPolicy(allowed_urls=("https://a/*",), allowed_env_vars=("TOKEN",))
    assert http_hook_policy_from_settings({}, None) == HttpHookPolicy(None, None)
    assert http_hook_policy_from_settings({"allowedHttpHookUrls": "x"}, None).allowed_urls is None
    assert http_hook_policy_from_settings({"allowedHttpHookUrls": []}, None).allowed_urls == ()


def test_url_matches_pattern() -> None:
    assert url_matches_pattern("https://hooks.example.com/a/b", "https://hooks.example.com/*")
    assert not url_matches_pattern("https://hooksXexample.com/a", "https://hooks.example.com/*")
    assert url_matches_pattern("https://x.test/a", "https://x.test/a")
    assert not url_matches_pattern("https://x.test/ab", "https://x.test/a")
    assert not url_matches_pattern("xhttps://x.test/a", "https://x.test/a")
    assert url_matches_pattern("http://a?b=(1)", "http://a?b=(1)")
    assert url_matches_pattern("http://a/$[x]{y}|z+^\\", "http://a/$[x]{y}|z+^\\")
    assert not url_matches_pattern("http://a/\nb", "http://a/*")


def test_sanitize_header_value() -> None:
    assert sanitize_header_value("a\r\nX-Evil: 1\x00b") == "aX-Evil: 1b"


def test_interpolate_env_vars() -> None:
    env = {"TOKEN": "t\r\nX: y", "OTHER": "secret", "lower": "nope"}
    allowed = frozenset({"TOKEN"})
    assert interpolate_env_vars("Bearer $TOKEN", allowed, env) == "Bearer tX: y"
    assert interpolate_env_vars("Bearer ${TOKEN}!", allowed, env) == "Bearer tX: y!"
    assert interpolate_env_vars("$OTHER-${OTHER}", allowed, env) == "-"
    assert interpolate_env_vars("$MISSING", frozenset({"MISSING"}), env) == ""
    assert interpolate_env_vars("$lower", frozenset({"lower"}), env) == "$lower"


def test_interpolate_defaults_to_process_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOOK_TEST_VAR", "v")
    assert interpolate_env_vars("$HOOK_TEST_VAR", frozenset({"HOOK_TEST_VAR"})) == "v"


def test_proxy_env() -> None:
    assert get_proxy_url({}) is None
    assert get_proxy_url({"HTTP_PROXY": "h"}) == "h"
    assert get_proxy_url({"HTTP_PROXY": "h", "http_proxy": "l"}) == "l"
    assert get_proxy_url({"http_proxy": "l", "HTTPS_PROXY": "S"}) == "S"
    assert get_proxy_url({"HTTPS_PROXY": "S", "https_proxy": "s"}) == "s"
    assert get_proxy_url({"https_proxy": ""}) is None
    assert get_no_proxy({}) is None
    assert get_no_proxy({"NO_PROXY": "a"}) == "a"
    assert get_no_proxy({"NO_PROXY": "a", "no_proxy": "b"}) == "b"


def test_proxy_env_defaults_to_process_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("https_proxy", "HTTPS_PROXY", "http_proxy", "no_proxy"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HTTP_PROXY", "http://p:1")
    monkeypatch.setenv("NO_PROXY", "x")
    assert get_proxy_url() == "http://p:1"
    assert get_no_proxy() == "x"


@pytest.mark.parametrize(
    ("url", "no_proxy", "expected"),
    [
        ("http://a.com/x", None, False),
        ("http://a.com/x", "", False),
        ("http://a.com/x", "*", True),
        ("http://a.com/x", "a.com", True),
        ("http://A.COM/x", "a.com", True),
        ("http://a.com/x", "b.com, a.com", True),
        ("http://a.com/x", "b.com c.com", False),
        ("http://sub.a.com/x", ".a.com", True),
        ("http://a.com/x", ".a.com", True),
        ("http://nota.com/x", ".a.com", False),
        ("http://a.com/x", "a.com:80", True),
        ("https://a.com/x", "a.com:443", True),
        ("https://a.com/x", "a.com:80", False),
        ("http://a.com:8080/x", "a.com:8080", True),
        ("http://sub.a.com/x", "a.com", False),
        ("http://[::1/", "a.com", False),
    ],
)
def test_should_bypass_proxy(url: str, no_proxy: str | None, expected: bool) -> None:
    assert should_bypass_proxy(url, no_proxy) is expected


# ── exec_http_hook ───────────────────────────────────────────────────────────

def test_allowlist_miss_blocks_before_io() -> None:
    t = FakeTransport()
    res = run(HttpHook(url="https://evil.test/h"), t, HttpHookPolicy(allowed_urls=("https://ok.test/*",)))
    assert res.ok is False and res.status_code is None and res.body == "" and res.aborted is False
    assert res.error == "HTTP hook blocked: https://evil.test/h does not match any pattern in allowedHttpHookUrls"
    assert t.requests == []


def test_empty_allowlist_blocks_all() -> None:
    t = FakeTransport()
    res = run(HttpHook(url="https://ok.test/h"), t, HttpHookPolicy(allowed_urls=()))
    assert res.error is not None and t.requests == []


def test_allowlist_hit_sends_request() -> None:
    t = FakeTransport(HttpResponse(status=200, body='{"ok": true}'))
    res = run(HttpHook(url="https://ok.test/h"), t, HttpHookPolicy(allowed_urls=("https://no/*", "https://ok.test/*")))
    assert res.ok is True and res.status_code == 200 and res.body == '{"ok": true}' and res.error is None
    req = t.requests[0]
    assert req.url == "https://ok.test/h"
    assert req.body == '{"x": 1}'
    assert req.headers == (("Content-Type", "application/json"),)
    assert req.timeout_s == DEFAULT_HTTP_HOOK_TIMEOUT_MS / 1000
    assert req.proxy_url is None
    assert req.lookup is not None


def test_status_codes() -> None:
    for status, ok in ((199, False), (200, True), (204, True), (299, True), (300, False), (404, False), (500, False)):
        res = run(HttpHook(url="https://ok.test/h"), FakeTransport(HttpResponse(status=status, body="b")))
        assert (res.ok, res.status_code, res.body) == (ok, status, "b")


def test_none_body_becomes_empty() -> None:
    empty = HttpResponse(status=200, body=None)  # type: ignore[arg-type]
    res = run(HttpHook(url="https://ok.test/h"), FakeTransport(empty))
    assert res.body == ""


def test_hook_timeout_used() -> None:
    t = FakeTransport()
    run(HttpHook(url="https://ok.test/h", timeout=2.5), t)
    assert t.requests[0].timeout_s == 2.5


def test_headers_interpolated_with_hook_allowlist() -> None:
    t = FakeTransport()
    hook = HttpHook(
        url="https://ok.test/h",
        headers=(("Authorization", "Bearer $TOKEN"), ("X-Other", "${SECRET}")),
        allowed_env_vars=("TOKEN",),
    )
    run(hook, t, env={"TOKEN": "abc", "SECRET": "s"})
    assert t.requests[0].headers == (
        ("Content-Type", "application/json"),
        ("Authorization", "Bearer abc"),
        ("X-Other", ""),
    )


def test_headers_intersect_policy_env_allowlist() -> None:
    t = FakeTransport()
    hook = HttpHook(
        url="https://ok.test/h",
        headers=(("A", "$TOKEN"), ("B", "$OTHER")),
        allowed_env_vars=("TOKEN", "OTHER"),
    )
    run(hook, t, HttpHookPolicy(allowed_env_vars=("OTHER",)), env={"TOKEN": "t", "OTHER": "o"})
    assert t.requests[0].headers[1:] == (("A", ""), ("B", "o"))


def test_no_headers_means_no_interpolation() -> None:
    t = FakeTransport()
    run(HttpHook(url="https://ok.test/h", allowed_env_vars=("TOKEN",)), t, env={"TOKEN": "t"})
    assert t.requests[0].headers == (("Content-Type", "application/json"),)


def test_guarded_lookup_is_wired_with_resolver() -> None:
    t = FakeTransport()
    run(HttpHook(url="https://lan.test/h"), t, resolver=lambda h: [("10.1.1.1", 4)])
    lookup = t.requests[0].lookup
    assert lookup is not None
    with pytest.raises(SsrfBlockedError):
        lookup("lan.test")
    t2 = FakeTransport()
    run(HttpHook(url="https://ok.test/h"), t2, resolver=lambda h: [("8.8.8.8", 4)])
    assert t2.requests[0].lookup is not None and t2.requests[0].lookup("ok.test") == [("8.8.8.8", 4)]


def test_env_proxy_skips_guard() -> None:
    t = FakeTransport()
    run(HttpHook(url="https://ok.test/h"), t, env={"HTTPS_PROXY": "http://proxy:3128"})
    assert t.requests[0].lookup is None
    assert t.requests[0].proxy_url == "http://proxy:3128"


def test_no_proxy_bypass_keeps_guard() -> None:
    t = FakeTransport()
    run(HttpHook(url="https://ok.test/h"), t, env={"HTTPS_PROXY": "http://proxy:3128", "NO_PROXY": "ok.test"})
    assert t.requests[0].lookup is not None
    assert t.requests[0].proxy_url is None


def test_transport_error_reported() -> None:
    res = run(HttpHook(url="https://ok.test/h"), FakeTransport(error=SsrfBlockedError("ok.test", "10.0.0.1")))
    assert res.ok is False and res.aborted is False and res.status_code is None and res.body == ""
    assert res.error is not None and res.error.startswith("HTTP hook blocked: ok.test resolves to 10.0.0.1")


def test_error_after_abort_is_aborted() -> None:
    signal = Signal()

    class Abort:
        async def __call__(self, request: HttpRequest) -> HttpResponse:
            signal.aborted = True
            raise RuntimeError("boom")

    res = run(HttpHook(url="https://ok.test/h"), Abort(), signal=signal)
    assert res.aborted is True and res.error is None and res.ok is False


def test_already_aborted_signal() -> None:
    t = FakeTransport(delay=5)
    res = run(HttpHook(url="https://ok.test/h"), t, signal=Signal(aborted=True))
    assert res == type(res)(ok=False, status_code=None, body="", aborted=True)


def test_cancel_token_abort_mid_request() -> None:
    class Token:
        cancelled = False

    token = Token()

    async def go() -> Any:
        task = asyncio.ensure_future(
            exec_http_hook(HttpHook(url="https://ok.test/h"), "Stop", "{}", token,
                           policy=HttpHookPolicy(), transport=FakeTransport(delay=5), env={})
        )
        await asyncio.sleep(0.1)
        token.cancelled = True
        return await asyncio.wait_for(task, 2)

    res = asyncio.run(go())
    assert res.aborted is True


def test_timeout_is_aborted() -> None:
    res = run(HttpHook(url="https://ok.test/h", timeout=0.1), FakeTransport(delay=5))
    assert res.aborted is True and res.ok is False and res.error is None


# ── default transport against a local loopback server ────────────────────────

class _Handler(http.server.BaseHTTPRequestHandler):
    seen: ClassVar[list[dict[str, Any]]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        _Handler.seen.append({
            "path": self.path,
            "body": self.rfile.read(length).decode(),
            "host": self.headers.get("Host"),
            "auth": self.headers.get("Authorization"),
        })
        if "/redirect" in self.path:
            self.send_response(302)
            self.send_header("Location", "/elsewhere")
            self.end_headers()
            return
        status = 503 if "/fail" in self.path else 200
        payload = b'{"decision": "approve"}'
        self.send_response(status)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture()
def server() -> Iterator[int]:
    _Handler.seen = []
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def test_default_transport_posts_to_loopback(server: int) -> None:
    hook = HttpHook(url=f"http://localhost:{server}/hook?q=1", headers=(("Authorization", "Bearer $T"),),
                    allowed_env_vars=("T",))
    res = asyncio.run(exec_http_hook(hook, "Stop", '{"a": 1}', None, policy=HttpHookPolicy(),
                                     env={"T": "tok"}, resolver=lambda h: [("127.0.0.1", 4)]))
    assert res.ok is True and res.status_code == 200 and res.body == '{"decision": "approve"}'
    seen = _Handler.seen[0]
    assert seen == {"path": "/hook?q=1", "body": '{"a": 1}', "host": f"localhost:{server}", "auth": "Bearer tok"}


def test_default_transport_no_redirect_and_non_2xx(server: int) -> None:
    res = asyncio.run(default_transport(HttpRequest(
        url=f"http://127.0.0.1:{server}/redirect", body="{}", headers=(), timeout_s=5, lookup=None)))
    assert res.status == 302
    assert [s["path"] for s in _Handler.seen] == ["/redirect"]
    res = asyncio.run(default_transport(HttpRequest(
        url=f"http://127.0.0.1:{server}", body="{}", headers=(), timeout_s=5, lookup=None)))
    assert res.status == 200 and _Handler.seen[-1]["path"] == "/"


def test_default_transport_ssrf_blocks_before_connect(server: int) -> None:
    hook = HttpHook(url=f"http://internal.test:{server}/h")
    res = asyncio.run(exec_http_hook(hook, "Stop", "{}", None, policy=HttpHookPolicy(), env={},
                                     resolver=lambda h: [("192.168.0.10", 4)]))
    assert res.ok is False and res.error is not None and "resolves to 192.168.0.10" in res.error
    assert _Handler.seen == []


def test_default_transport_via_env_proxy(server: int) -> None:
    hook = HttpHook(url="http://target.invalid/fail")
    res = asyncio.run(exec_http_hook(hook, "Stop", "{}", None, policy=HttpHookPolicy(),
                                     env={"HTTP_PROXY": f"http://127.0.0.1:{server}"}))
    assert res.ok is False and res.status_code == 503 and res.body == '{"decision": "approve"}'
    assert _Handler.seen[0]["path"] == "http://target.invalid/fail"
    ok = asyncio.run(exec_http_hook(HttpHook(url="http://target.invalid/x"), "Stop", "{}", None,
                                    policy=HttpHookPolicy(), env={"HTTP_PROXY": f"http://127.0.0.1:{server}"}))
    assert ok.ok is True and ok.status_code == 200
