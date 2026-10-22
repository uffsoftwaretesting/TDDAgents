"""
Running one HTTP hook: POST the hook input JSON to the configured URL.

Ported from `reference/claude-code/src/utils/hooks/execHttpHook.ts` -> `execHttpHook`,
`getHttpHookPolicy`, `urlMatchesPattern`, `sanitizeHeaderValue`, `interpolateEnvVars`,
`DEFAULT_HTTP_HOOK_TIMEOUT_MS`, and `src/utils/proxy.ts` -> `getProxyUrl`, `getNoProxy`,
`shouldBypassProxy`.

* **URL allowlist** (`allowedHttpHookUrls`, before any I/O): unset means no restriction, an
  empty list blocks every HTTP hook, otherwise the URL must match a `*`-wildcard pattern.
* **Headers**: `Content-Type: application/json`, plus the hook's headers with `$VAR` /
  `${VAR}` interpolated from the environment — only for names in the hook's
  `allowedEnvVars`, intersected with the policy's `httpHookAllowedEnvVars` when that is set.
  Any other reference becomes an empty string, and CR/LF/NUL are stripped (header
  injection).
* **SSRF guard** (`ssrf_guard.py`) on the resolved address, pinned for the connection —
  skipped when an env-var proxy (`HTTPS_PROXY`/`HTTP_PROXY`, honouring `NO_PROXY`) carries
  the request, because the proxy resolves the target.
* No redirects are followed and every status is a response; only 2xx is `ok`.
* Timeout: the hook's `timeout` seconds, else ten minutes. A timeout or an abort is
  `aborted`; any other failure is `error` with the exception's message.

Both settings keys are read from the **merged** settings (arrays concatenate across sources),
as upstream's `getInitialSettings()`; `policy` is accepted for the dispatcher's uniform call
shape and not consulted, since `allowManagedHooksOnly` already limits which hooks run.

Not ported: the sandbox network proxy (`getSandboxProxyConfig` — this build has no
sandbox-runtime proxy), mTLS / custom CA configuration of the proxy agent.
"""

from __future__ import annotations

import asyncio
import http.client
import os
import re
import socket
import ssl
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from app.loop.hooks.process import ABORT_POLL_SECONDS, is_aborted
from app.loop.hooks.schemas import HttpHook
from app.loop.hooks.ssrf_guard import Resolver, ssrf_guarded_lookup

#: `DEFAULT_HTTP_HOOK_TIMEOUT_MS`: ten minutes, matching `TOOL_HOOK_EXECUTION_TIMEOUT_MS`.
DEFAULT_HTTP_HOOK_TIMEOUT_MS = 10 * 60 * 1000

_ENV_REF = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}|\$([A-Z_][A-Z0-9_]*)")
_HEADER_UNSAFE = re.compile("[\r\n\x00]")
_PATTERN_SPECIALS = re.compile(r"[.+?^${}()|\[\]\\]")


@dataclass(frozen=True, slots=True)
class HttpHookResult:
    """`execHttpHook`'s result."""

    ok: bool
    status_code: int | None
    body: str
    error: str | None = None
    aborted: bool = False


@dataclass(frozen=True, slots=True)
class HttpHookPolicy:
    """`getHttpHookPolicy`: `None` means the setting is absent (no restriction)."""

    allowed_urls: tuple[str, ...] | None = None
    allowed_env_vars: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class HttpRequest:
    """What a transport is asked to send. `lookup` is the SSRF-guarded resolver, or `None`."""

    url: str
    body: str
    headers: tuple[tuple[str, str], ...]
    timeout_s: float
    lookup: Callable[[str], list[tuple[str, int]]] | None
    proxy_url: str | None = None


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    body: str


Transport = Callable[[HttpRequest], Awaitable[HttpResponse]]


def _string_list(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, list):
        return None
    return tuple(v for v in value if isinstance(v, str))


def http_hook_policy_from_settings(merged: Mapping[str, Any], policy: Mapping[str, Any] | None) -> HttpHookPolicy:
    """`getHttpHookPolicy`, from the merged settings."""
    return HttpHookPolicy(
        allowed_urls=_string_list(merged.get("allowedHttpHookUrls")),
        allowed_env_vars=_string_list(merged.get("httpHookAllowedEnvVars")),
    )


def url_matches_pattern(url: str, pattern: str) -> bool:
    """`urlMatchesPattern`: `*` matches any characters, everything else is literal."""
    escaped = _PATTERN_SPECIALS.sub(lambda m: "\\" + m.group(0), pattern)
    return re.fullmatch(escaped.replace("*", ".*"), url) is not None


def sanitize_header_value(value: str) -> str:
    """`sanitizeHeaderValue`: strip CR, LF and NUL."""
    return _HEADER_UNSAFE.sub("", value)


def interpolate_env_vars(value: str, allowed_env_vars: frozenset[str], env: Mapping[str, str] | None = None) -> str:
    """`interpolateEnvVars`: allowed `$VAR`/`${VAR}` from the environment, others empty."""
    source = os.environ if env is None else env

    def replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        if name not in allowed_env_vars:
            return ""
        return source.get(name, "")

    return sanitize_header_value(_ENV_REF.sub(replace, value))


def get_proxy_url(env: Mapping[str, str] | None = None) -> str | None:
    """`getProxyUrl`: `https_proxy`, `HTTPS_PROXY`, `http_proxy`, `HTTP_PROXY`, first set."""
    source = os.environ if env is None else env
    for key in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY"):
        if source.get(key):
            return source[key]
    return None


def get_no_proxy(env: Mapping[str, str] | None = None) -> str | None:
    """`getNoProxy`: `no_proxy`, then `NO_PROXY`."""
    source = os.environ if env is None else env
    return source.get("no_proxy") or source.get("NO_PROXY") or None


def should_bypass_proxy(url: str, no_proxy: str | None) -> bool:
    """`shouldBypassProxy`."""
    if not no_proxy:
        return False
    if no_proxy == "*":
        return True
    try:
        parts = urlsplit(url)
        hostname = (parts.hostname or "").lower()
        port = str(parts.port) if parts.port is not None else ("443" if parts.scheme == "https" else "80")
    except ValueError:
        return False
    host_with_port = f"{hostname}:{port}"
    for pattern in (p for p in re.split(r"[,\s]+", no_proxy) if p):
        pattern = pattern.lower().strip()
        if ":" in pattern:
            if host_with_port == pattern:
                return True
        elif pattern.startswith("."):
            if hostname == pattern[1:] or hostname.endswith(pattern):
                return True
        elif hostname == pattern:
            return True
    return False


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """Connects to an already-validated address; `Host` still names the URL's host."""

    def __init__(self, host: str, port: int | None, address: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._address = address

    def connect(self) -> None:
        self.sock = socket.create_connection((self._address, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int | None, address: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self) -> None:
        raw = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)  # type: ignore[attr-defined]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _send_blocking(request: HttpRequest) -> HttpResponse:
    headers = dict(request.headers)
    data = request.body.encode("utf-8")
    if request.proxy_url is not None:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": request.proxy_url, "https": request.proxy_url}), _NoRedirect()
        )
        req = urllib.request.Request(request.url, data=data, headers=headers, method="POST")
        try:
            with opener.open(req, timeout=request.timeout_s) as resp:
                return HttpResponse(status=resp.status, body=resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            return HttpResponse(status=exc.code, body=exc.read().decode("utf-8", errors="replace"))
    parts = urlsplit(request.url)
    host = parts.hostname or ""
    address = host
    if request.lookup is not None:
        address = request.lookup(host)[0][0]
    cls = _PinnedHTTPSConnection if parts.scheme == "https" else _PinnedHTTPConnection
    conn = cls(host, parts.port, address, request.timeout_s)
    try:
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        conn.request("POST", path, body=data, headers=headers)
        resp = conn.getresponse()
        return HttpResponse(status=resp.status, body=resp.read().decode("utf-8", errors="replace"))
    finally:
        conn.close()


async def default_transport(request: HttpRequest) -> HttpResponse:
    """The real request, run off the event loop."""
    return await asyncio.to_thread(_send_blocking, request)


async def exec_http_hook(
    hook: HttpHook,
    hook_event: str,
    json_input: str,
    signal: Any,
    *,
    policy: HttpHookPolicy,
    transport: Transport | None = None,
    resolver: Resolver | None = None,
    env: Mapping[str, str] | None = None,
) -> HttpHookResult:
    """`execHttpHook`. Never raises."""
    if policy.allowed_urls is not None and not any(url_matches_pattern(hook.url, p) for p in policy.allowed_urls):
        return HttpHookResult(
            ok=False,
            status_code=None,
            body="",
            error=f"HTTP hook blocked: {hook.url} does not match any pattern in allowedHttpHookUrls",
        )

    timeout_ms = hook.timeout * 1000 if hook.timeout else DEFAULT_HTTP_HOOK_TIMEOUT_MS
    headers: list[tuple[str, str]] = [("Content-Type", "application/json")]
    if hook.headers:
        hook_vars = hook.allowed_env_vars
        if policy.allowed_env_vars is not None:
            hook_vars = tuple(v for v in hook_vars if v in policy.allowed_env_vars)
        allowed = frozenset(hook_vars)
        for name, value in hook.headers:
            headers.append((name, interpolate_env_vars(value, allowed, env)))

    proxy_url = get_proxy_url(env)
    env_proxy_active = proxy_url is not None and not should_bypass_proxy(hook.url, get_no_proxy(env))

    def guarded(hostname: str) -> list[tuple[str, int]]:
        return ssrf_guarded_lookup(hostname, resolver)

    request = HttpRequest(
        url=hook.url,
        body=json_input,
        headers=tuple(headers),
        timeout_s=timeout_ms / 1000,
        lookup=None if env_proxy_active else guarded,
        proxy_url=proxy_url if env_proxy_active else None,
    )
    send = transport or default_transport
    task = asyncio.ensure_future(send(request))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_ms / 1000
    while not task.done():
        if is_aborted(signal) or loop.time() >= deadline:
            task.cancel()
            return HttpHookResult(ok=False, status_code=None, body="", aborted=True)
        await asyncio.wait({task}, timeout=min(ABORT_POLL_SECONDS, max(deadline - loop.time(), 0)))
    try:
        response = task.result()
    except Exception as exc:  # noqa: BLE001 - upstream reports any failure as `error`
        if is_aborted(signal):
            return HttpHookResult(ok=False, status_code=None, body="", aborted=True)
        return HttpHookResult(ok=False, status_code=None, body="", error=str(exc))
    body = response.body or ""
    return HttpHookResult(ok=200 <= response.status < 300, status_code=response.status, body=body)


__all__ = [
    "DEFAULT_HTTP_HOOK_TIMEOUT_MS",
    "HttpHookPolicy",
    "HttpHookResult",
    "HttpRequest",
    "HttpResponse",
    "Transport",
    "default_transport",
    "exec_http_hook",
    "get_no_proxy",
    "get_proxy_url",
    "http_hook_policy_from_settings",
    "interpolate_env_vars",
    "sanitize_header_value",
    "should_bypass_proxy",
    "url_matches_pattern",
]
