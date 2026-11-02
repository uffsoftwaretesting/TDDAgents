"""
OAuth for remote (sse / http) MCP servers: discovery, the browser flow, token storage,
refresh, revocation and step-up.

Ported from `reference/claude-code/src/services/mcp/auth.ts` -> `AUTH_REQUEST_TIMEOUT_MS`,
`MAX_LOCK_RETRIES`, `SENSITIVE_OAUTH_PARAMS` / `redactSensitiveUrlParams`,
`NONSTANDARD_INVALID_GRANT_ALIASES` / `normalizeOAuthErrorBody`, `createAuthFetch`,
`fetchAuthServerMetadata`, `AuthenticationCancelledError`, `getServerKey`,
`hasMcpDiscoveryButNoToken`, `revokeToken`, `revokeServerTokens`,
`clearServerTokensFromLocalStorage`, `performMCPOAuthFlow`, `wrapFetchWithStepUpDetection`,
`ClaudeAuthProvider` (every member but `xaaRefresh`), `readClientSecret`,
`saveMcpClientSecret`, `clearMcpClientConfig`, `getMcpClientConfig`, `getScopeFromMetadata`.
The SDK's `auth()` it drives is `app/services/mcp/sdk_auth.py`.

`McpTransportAuth` is the bridge into the Python SDK's transports. Upstream hands the provider
to the TypeScript transports as `authProvider`: every request carries `Bearer
<provider.tokens().access_token>`, a 401 runs `auth()` (with the challenge's
`resource_metadata` and `scope`) and retries once, anything but `AUTHORIZED` throws
`UnauthorizedError`, and a 403 `insufficient_scope` runs `auth()` again with the new scope
(once per challenge). The Python transports take an `httpx2.Auth` instead (`auth=`), which is
where that behaviour lives here. The bridge never raises from inside the request: the Python
streamable-HTTP transport swallows a request's exception in a background task, which would
leave `initialize` waiting for the connection timeout. It records the failure and hands the
401/403 response back; the transport turns that into an error, and the transport wrapper in
`client.py` re-raises the recorded failure in its place.

TDDAgents substitutions: the client is named `TDDAgents (<server>)`, the success page says
"Return to TDDAgents.", the refresh lock lives in TDDAgents' config home, and the environment
variables are `TDDAGENTS_MCP_OAUTH_CLIENT_METADATA_URL` and `TDDAGENTS_MCP_CLIENT_SECRET`
(upstream's `MCP_OAUTH_CLIENT_METADATA_URL`, `MCP_CLIENT_SECRET`).

Divergences, each with its reason:

* **No CIMD URL of our own.** Upstream's `clientMetadataUrl` falls back to
  `MCP_CLIENT_METADATA_URL` (`https://claude.ai/oauth/claude-code-client-metadata`), a client
  metadata document Anthropic hosts for Claude Code. Presenting it would make an authorization
  server treat TDDAgents as Claude Code, so without the environment override the provider has no
  metadata URL and the SDK falls back to dynamic client registration.
* **XAA (cross-app access, SEP-990) is not ported** (`xaa.ts`, `xaaIdpLogin.ts`,
  `performMCPXaaAuth`, `xaaRefresh`, `settings.xaaIdp` and the `mcp xaa` CLI that configures
  it). `is_xaa_enabled()` is always false, so every XAA branch of `auth.ts` takes its
  disabled path; a server configured with `oauth.xaa` fails `perform_mcp_oauth_flow` with an
  error naming the missing support instead of upstream's "set CLAUDE_CODE_ENABLE_XAA=1".
* The callback page escapes the provider's error text with `html.escape` (upstream: the `xss`
  package), and a callback request that carries neither code nor error is closed unanswered.
* Analytics (`logEvent`: flow start / success / error, refresh outcome) are not ported;
  the flow's failure-reason attribution existed only for them. `logMCPDebug` is `logger.debug`.
* The transport bridge discovers and exchanges tokens through `create_auth_fetch()` with the
  transport's headers; upstream reuses the transport's own fetch (60 s on non-GET requests,
  none on GET).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import getpass
import hashlib
import html
import json
import logging
import os
import random
import re
import secrets
import sys
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import parse_qs, parse_qsl, quote, urlencode, urlsplit, urlunsplit

import httpx2
from mcp.shared._httpx_utils import request_within_origin

from app.services.mcp.oauth_port import build_redirect_uri, find_available_port
from app.services.mcp.sdk_auth import (
    FetchLike,
    InvalidateScope,
    InvalidGrantError,
    OAuthError,
    ServerError,
    TemporarilyUnavailableError,
    TooManyRequestsError,
    UnauthorizedError,
    discover_authorization_server_metadata,
    discover_oauth_server_info,
    extract_www_authenticate_params,
    is_oauth_tokens,
    parse_oauth_error_response,
    parse_oauth_metadata,
    refresh_authorization,
    url_href,
    url_pathname,
)
from app.services.mcp.sdk_auth import auth as sdk_auth
from app.services.mcp.types import McpServerConfig
from app.utils.abort_controller import AbortSignal
from app.utils.browser import open_browser
from app.utils.lockfile import lock as lockfile_lock
from app.utils.secure_storage.index import clear_keychain_cache, get_secure_storage
from app.utils.secure_storage.types import SecureStorageData
from app.utils.tool_result_storage import config_home

logger = logging.getLogger(__name__)

#: `AUTH_REQUEST_TIMEOUT_MS`: each OAuth request (discovery, refresh, ...).
AUTH_REQUEST_TIMEOUT_MS = 30000
#: `MAX_LOCK_RETRIES`.
MAX_LOCK_RETRIES = 5
#: `performMCPOAuthFlow`'s callback timeout (5 minutes).
AUTHENTICATION_TIMEOUT_MS = 5 * 60 * 1000
#: `_doRefresh`'s `MAX_ATTEMPTS`.
MAX_REFRESH_ATTEMPTS = 3
#: `tokens()`: refresh when the token expires within this many seconds.
PROACTIVE_REFRESH_WINDOW_S = 300
#: `saveTokens`: `expires_in || 3600`.
DEFAULT_EXPIRES_IN_S = 3600

CLIENT_METADATA_URL_ENV = "TDDAGENTS_MCP_OAUTH_CLIENT_METADATA_URL"
CLIENT_SECRET_ENV = "TDDAGENTS_MCP_CLIENT_SECRET"
#: `constants/oauth.ts` -> `MCP_CLIENT_METADATA_URL`: none for TDDAgents (module docstring).
MCP_CLIENT_METADATA_URL: str | None = None

#: `SENSITIVE_OAUTH_PARAMS`.
SENSITIVE_OAUTH_PARAMS = ("state", "nonce", "code_challenge", "code_verifier", "code")
#: `NONSTANDARD_INVALID_GRANT_ALIASES` (Slack's spellings of `invalid_grant`).
NONSTANDARD_INVALID_GRANT_ALIASES = frozenset({"invalid_refresh_token", "expired_refresh_token", "token_expired"})

_TIMEOUT_MESSAGE = re.compile(r"timeout|timed out|etimedout|econnreset", re.IGNORECASE)
_STEP_UP_SCOPE = re.compile(r"scope=(?:\"([^\"]+)\"|([^\s,]+))")

#: Patched by the tests; upstream's `sleep`.
_sleep = asyncio.sleep


def _debug(server_name: str, message: str) -> None:
    """`logMCPDebug`."""
    logger.debug("MCP server %s: %s", server_name, message)


def _now_ms() -> float:
    return time.time() * 1000


def is_xaa_enabled() -> bool:
    """`xaaIdpLogin.ts` -> `isXaaEnabled`: XAA is not ported (module docstring)."""
    return False


def redact_sensitive_url_params(url: str) -> str:
    """`redactSensitiveUrlParams`: `[REDACTED]` for state, nonce, PKCE values and the code."""
    try:
        parts = urlsplit(url)
        if not parts.scheme or not parts.netloc:
            return url
        pairs = parse_qsl(parts.query, keep_blank_values=True)
    except ValueError:
        return url
    present = {k for k, _ in pairs}
    redacted = [(k, "[REDACTED]" if k in SENSITIVE_OAUTH_PARAMS and k in present else v) for k, v in pairs]
    return urlunsplit(parts._replace(query=urlencode(redacted)))


def normalize_oauth_error_body(response: httpx2.Response) -> httpx2.Response:
    """
    `normalizeOAuthErrorBody`: a 2xx POST whose body is an OAuth error (and not tokens) becomes a
    400, with Slack's non-standard `invalid_grant` aliases renamed, so the SDK maps it.
    """
    if not response.is_success:
        return response
    try:
        parsed = json.loads(response.text)
    except ValueError:
        return response
    if is_oauth_tokens(parsed):
        return response
    try:
        error = parse_oauth_error_response(parsed)
    except ValueError:
        return response
    if error["error"] in NONSTANDARD_INVALID_GRANT_ALIASES:
        error = {
            "error": "invalid_grant",
            "error_description": error.get("error_description")
            or f"Server returned non-standard error code: {error['error']}",
        }
    headers = {k: v for k, v in response.headers.items() if k.lower() not in ("content-length", "content-encoding")}
    normalized = httpx2.Response(400, headers=headers, content=json.dumps(error).encode())
    with contextlib.suppress(RuntimeError):  # a response built without a request has none to carry over
        normalized.request = response.request
    return normalized


def create_auth_fetch(extra_headers: Mapping[str, str] | None = None) -> FetchLike:
    """`createAuthFetch`: a fresh 30 s timeout per request; POST answers are normalized."""

    async def auth_fetch(
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        content: bytes | None = None,
    ) -> httpx2.Response:
        merged = {**(extra_headers or {}), **(headers or {})}
        async with httpx2.AsyncClient(timeout=AUTH_REQUEST_TIMEOUT_MS / 1000) as client:
            response = await request_within_origin(client, method, url, headers=merged, content=content)
        return normalize_oauth_error_body(response) if method.upper() == "POST" else response

    return auth_fetch


async def fetch_auth_server_metadata(
    server_name: str,
    server_url: str,
    configured_metadata_url: str | None,
    fetch_fn: FetchLike | None = None,
    resource_metadata_url: str | None = None,
) -> dict[str, Any] | None:
    """
    `fetchAuthServerMetadata`: the configured (https-only) metadata URL, else RFC 9728 -> RFC 8414
    discovery, else the legacy path-aware RFC 8414 probe on the server URL itself.
    """
    if configured_metadata_url:
        if not configured_metadata_url.startswith("https://"):
            raise ValueError(f"authServerMetadataUrl must use https:// (got: {configured_metadata_url})")
        auth_fetch = fetch_fn or create_auth_fetch()
        response = await auth_fetch(configured_metadata_url, headers={"Accept": "application/json"})
        if response.is_success:
            return parse_oauth_metadata(response.json())
        raise ValueError(
            f"HTTP {response.status_code} fetching configured auth server metadata from {configured_metadata_url}")
    kwargs: dict[str, Any] = {"resource_metadata_url": resource_metadata_url}
    if fetch_fn is not None:
        kwargs["fetch_fn"] = fetch_fn
    try:
        info = await discover_oauth_server_info(server_url, **kwargs)
        if info.authorization_server_metadata:
            return info.authorization_server_metadata
    except Exception as exc:  # noqa: BLE001 - upstream: any error falls through to the legacy probe
        _debug(server_name, f"RFC 9728 discovery failed, falling back: {exc}")
    if url_pathname(server_url) == "/":
        return None
    if fetch_fn is not None:
        return await discover_authorization_server_metadata(server_url, fetch_fn=fetch_fn)
    return await discover_authorization_server_metadata(server_url)


class AuthenticationCancelledError(Exception):
    """`AuthenticationCancelledError`."""

    def __init__(self) -> None:
        super().__init__("Authentication was cancelled")


def get_server_key(server_name: str, server_config: McpServerConfig) -> str:
    """`getServerKey`: `<name>|<first 16 hex of sha256({type, url, headers})>`."""
    config_json = json.dumps(
        {"type": server_config.type, "url": server_config.url, "headers": dict(server_config.headers or {})},
        separators=(",", ":"),
        ensure_ascii=False,
    )
    digest = hashlib.sha256(config_json.encode("utf-8")).hexdigest()[:16]
    return f"{server_name}|{digest}"


def _mcp_oauth(data: SecureStorageData | None) -> dict[str, Any]:
    slot = (data or {}).get("mcpOAuth")
    return slot if isinstance(slot, dict) else {}


def _entry(data: SecureStorageData | None, server_key: str) -> dict[str, Any] | None:
    entry = _mcp_oauth(data).get(server_key)
    return entry if isinstance(entry, dict) else None


def _merge(*parts: Mapping[str, Any] | None, **fields: Any) -> dict[str, Any]:
    """`{...a, ...b, k: v}`, dropping keys whose value is `undefined` (`None`), as `JSON.stringify` does."""
    out: dict[str, Any] = {}
    for part in parts:
        out.update(part or {})
    out.update(fields)
    return {k: v for k, v in out.items() if v is not None}


def _write_entry(data: SecureStorageData, server_key: str, entry: dict[str, Any]) -> SecureStorageData:
    return {**data, "mcpOAuth": {**_mcp_oauth(data), server_key: entry}}


def has_mcp_discovery_but_no_token(server_name: str, server_config: McpServerConfig) -> bool:
    """
    `hasMcpDiscoveryButNoToken`: the server was probed (an entry exists) but holds neither an
    access nor a refresh token, so a connection is certain to answer 401.
    """
    if is_xaa_enabled() and server_config.oauth is not None and server_config.oauth.xaa:
        return False
    entry = _entry(get_secure_storage().read(), get_server_key(server_name, server_config))
    return entry is not None and not entry.get("accessToken") and not entry.get("refreshToken")


def basic_auth_header(client_id: str, client_secret: str) -> str:
    """`revokeToken`'s `Basic base64(encodeURIComponent(id):encodeURIComponent(secret))`."""
    raw = f"{quote(client_id, safe=_URI_COMPONENT_SAFE)}:{quote(client_secret, safe=_URI_COMPONENT_SAFE)}"
    return f"Basic {base64.b64encode(raw.encode()).decode()}"


#: The characters `encodeURIComponent` leaves alone besides alphanumerics.
_URI_COMPONENT_SAFE = "-_.!~*'()"


async def _post_form(endpoint: str, params: Mapping[str, str], headers: Mapping[str, str]) -> None:
    """`axios.post`: no timeout (axios' default `0`); a non-2xx answer raises `httpx2.HTTPStatusError`."""
    async with httpx2.AsyncClient(timeout=None) as client:
        response = await client.post(endpoint, content=urlencode(dict(params)).encode(), headers=dict(headers))
    response.raise_for_status()


async def revoke_token(
    *,
    server_name: str,
    endpoint: str,
    token: str,
    token_type_hint: str,
    client_id: str | None = None,
    client_secret: str | None = None,
    access_token: str | None = None,
    auth_method: str = "client_secret_basic",
) -> None:
    """
    `revokeToken` (RFC 7009): client authentication per RFC 6749 §2.3; a 401 retries once with
    the access token as `Bearer` and no client credentials, for non-compliant servers.
    """
    params: dict[str, str] = {"token": token, "token_type_hint": token_type_hint}
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if client_id and client_secret:
        if auth_method == "client_secret_post":
            params["client_id"] = client_id
            params["client_secret"] = client_secret
        else:
            headers["Authorization"] = basic_auth_header(client_id, client_secret)
    elif client_id:
        params["client_id"] = client_id
    else:
        _debug(server_name, f"No client_id available for {token_type_hint} revocation - server may reject")
    try:
        await _post_form(endpoint, params, headers)
        _debug(server_name, f"Successfully revoked {token_type_hint}")
    except httpx2.HTTPStatusError as exc:
        if exc.response.status_code != 401 or not access_token:
            raise
        _debug(server_name, f"Got 401, retrying {token_type_hint} revocation with Bearer auth")
        params.pop("client_id", None)
        params.pop("client_secret", None)
        await _post_form(endpoint, params, {**headers, "Authorization": f"Bearer {access_token}"})
        _debug(server_name, f"Successfully revoked {token_type_hint} with Bearer auth")


async def revoke_server_tokens(
    server_name: str, server_config: McpServerConfig, *, preserve_step_up_state: bool = False
) -> None:
    """
    `revokeServerTokens`: best-effort server-side revocation (refresh token first, then access
    token), then always clear the local entry; `preserve_step_up_state` keeps the step-up scope
    and the discovery URLs for the next flow.
    """
    storage = get_secure_storage()
    existing = storage.read()
    if not existing or not isinstance(existing.get("mcpOAuth"), dict):
        return
    server_key = get_server_key(server_name, server_config)
    token_data = _entry(existing, server_key)
    if token_data and (token_data.get("accessToken") or token_data.get("refreshToken")):
        try:
            discovery = token_data.get("discoveryState") or {}
            as_url = discovery.get("authorizationServerUrl") or str(server_config.url)
            oauth = server_config.oauth
            metadata = await fetch_auth_server_metadata(
                server_name, as_url, oauth.auth_server_metadata_url if oauth else None)
            if not metadata:
                _debug(server_name, "No OAuth metadata found")
            elif not metadata.get("revocation_endpoint"):
                _debug(server_name, "Server does not support token revocation")
            else:
                endpoint = str(metadata["revocation_endpoint"])
                methods = metadata.get("revocation_endpoint_auth_methods_supported")
                if methods is None:
                    methods = metadata.get("token_endpoint_auth_methods_supported")
                auth_method = (
                    "client_secret_post"
                    if methods and "client_secret_basic" not in methods and "client_secret_post" in methods
                    else "client_secret_basic"
                )
                _debug(server_name, f"Revoking tokens via {endpoint} ({auth_method})")
                for hint, key in (("refresh_token", "refreshToken"), ("access_token", "accessToken")):
                    value = token_data.get(key)
                    if not value:
                        continue
                    try:
                        await revoke_token(
                            server_name=server_name, endpoint=endpoint, token=value, token_type_hint=hint,
                            client_id=token_data.get("clientId"), client_secret=token_data.get("clientSecret"),
                            access_token=token_data.get("accessToken"), auth_method=auth_method,
                        )
                    except Exception as exc:  # noqa: BLE001 - upstream logs and continues
                        label = "refresh" if hint == "refresh_token" else "access"
                        _debug(server_name, f"Failed to revoke {label} token: {exc}")
        except Exception as exc:  # noqa: BLE001 - revocation is best-effort
            _debug(server_name, f"Failed to revoke tokens: {exc}")
    else:
        _debug(server_name, "No tokens to revoke")

    clear_server_tokens_from_local_storage(server_name, server_config)

    if preserve_step_up_state and token_data and (token_data.get("stepUpScope") or token_data.get("discoveryState")):
        fresh = storage.read() or {}
        previous = _entry(fresh, server_key) or {}
        discovery = token_data.get("discoveryState")
        entry = _merge(
            previous,
            serverName=server_name,
            serverUrl=server_config.url,
            accessToken=previous.get("accessToken", ""),
            expiresAt=previous.get("expiresAt", 0),
            stepUpScope=token_data.get("stepUpScope") or None,
            discoveryState=_merge(
                authorizationServerUrl=discovery.get("authorizationServerUrl"),
                resourceMetadataUrl=discovery.get("resourceMetadataUrl"),
            ) if discovery else None,
        )
        storage.update(_write_entry(fresh, server_key, entry))
        _debug(server_name, "Preserved step-up auth state across revocation")


def clear_server_tokens_from_local_storage(server_name: str, server_config: McpServerConfig) -> None:
    """`clearServerTokensFromLocalStorage`: delete the server's whole entry."""
    storage = get_secure_storage()
    existing = storage.read()
    if not existing or not isinstance(existing.get("mcpOAuth"), dict):
        return
    server_key = get_server_key(server_name, server_config)
    if existing["mcpOAuth"].get(server_key):
        del existing["mcpOAuth"][server_key]
        storage.update(existing)
        _debug(server_name, "Cleared stored tokens")


# ── performMCPOAuthFlow ───────────────────────────────────────────────────────

SUCCESS_PAGE = "<h1>Authentication Successful</h1><p>You can close this window. Return to TDDAgents.</p>"
STATE_MISMATCH_PAGE = (
    "<h1>Authentication Error</h1><p>Invalid state parameter. Please try again.</p>"
    "<p>You can close this window.</p>"
)
STATE_MISMATCH_MESSAGE = "OAuth state mismatch - possible CSRF attack"


def _http_response(status: int, body: str) -> bytes:
    reason = {200: "OK", 400: "Bad Request"}[status]
    payload = body.encode("utf-8")
    return (
        f"HTTP/1.1 {status} {reason}\r\nContent-Type: text/html\r\nContent-Length: {len(payload)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii") + payload


def _first(query: Mapping[str, list[str]], name: str) -> str | None:
    values = query.get(name)
    return values[0] if values else None


async def perform_mcp_oauth_flow(
    server_name: str,
    server_config: McpServerConfig,
    on_authorization_url: Callable[[str], None],
    abort_signal: AbortSignal | None = None,
    *,
    skip_browser_open: bool = False,
    on_waiting_for_callback: Callable[[Callable[[str], None]], None] | None = None,
) -> None:
    """
    `performMCPOAuthFlow`: open a localhost callback server, run `auth()` to the redirect, wait
    for the code (the browser's callback, a pasted callback URL through
    `on_waiting_for_callback`, the abort signal, or the 5-minute timeout), then run `auth()`
    again to exchange it. Stored credentials are cleared first (keeping a cached step-up scope
    and resource-metadata URL for this flow).
    """
    oauth = server_config.oauth
    if oauth is not None and oauth.xaa:
        raise RuntimeError(
            f"XAA is not supported by TDDAgents. Remove 'oauth.xaa' from server '{server_name}' to use the "
            "standard consent flow.")

    storage = get_secure_storage()
    server_key = get_server_key(server_name, server_config)
    cached_entry = _entry(storage.read(), server_key) or {}
    cached_step_up_scope = cached_entry.get("stepUpScope")
    cached_resource_metadata_url = (cached_entry.get("discoveryState") or {}).get("resourceMetadataUrl")
    clear_server_tokens_from_local_storage(server_name, server_config)

    resource_metadata_url: str | None = None
    if cached_resource_metadata_url:
        parts = urlsplit(str(cached_resource_metadata_url))
        if parts.scheme and parts.netloc:
            resource_metadata_url = str(cached_resource_metadata_url)
        else:
            _debug(server_name, f"Invalid cached resourceMetadataUrl: {cached_resource_metadata_url}")

    server_url = str(server_config.url)
    try:
        configured_port = oauth.callback_port if oauth else None
        port = configured_port or await find_available_port()
        redirect_uri = build_redirect_uri(port)
        _debug(server_name, f"Using redirect port: {port}{' (from config)' if configured_port else ''}")
        provider = ClaudeAuthProvider(
            server_name, server_config, redirect_uri, True, on_authorization_url, skip_browser_open)
        try:
            metadata = await fetch_auth_server_metadata(
                server_name, server_url, oauth.auth_server_metadata_url if oauth else None, None,
                resource_metadata_url)
            if metadata:
                provider.set_metadata(metadata)
                _debug(server_name, f"Fetched OAuth metadata with scope: {get_scope_from_metadata(metadata) or 'NONE'}")
        except Exception as exc:  # noqa: BLE001 - upstream logs and continues without metadata
            _debug(server_name, f"Failed to fetch OAuth metadata: {exc}")

        oauth_state = await provider.state()
        authorization_code = await _await_authorization_code(
            server_name, server_url, port, provider, oauth_state, cached_step_up_scope, resource_metadata_url,
            abort_signal, on_waiting_for_callback,
        )

        _debug(server_name, "Completing auth flow with authorization code")
        result = await sdk_auth(provider, server_url=server_url, authorization_code=authorization_code,
                                resource_metadata_url=resource_metadata_url)
        _debug(server_name, f"Auth result: {result}")
        if result != "AUTHORIZED":
            raise RuntimeError(f"Unexpected auth result: {result}")
        saved = await provider.tokens()
        _debug(server_name, f"Tokens after auth: {'Present' if saved else 'Missing'}")
    except Exception as error:
        _debug(server_name, f"Error during auth completion: {error}")
        if isinstance(error, OAuthError) and error.error_code == "invalid_client" and "Client not found" in str(error):
            existing = storage.read() or {}
            entry = _entry(existing, server_key)
            if entry is not None:
                entry.pop("clientId", None)
                entry.pop("clientSecret", None)
                storage.update(existing)
        raise


async def _await_authorization_code(
    server_name: str,
    server_url: str,
    port: int,
    provider: ClaudeAuthProvider,
    oauth_state: str,
    scope: str | None,
    resource_metadata_url: str | None,
    abort_signal: AbortSignal | None,
    on_waiting_for_callback: Callable[[Callable[[str], None]], None] | None,
) -> str:
    """`performMCPOAuthFlow`'s callback `Promise`: the first code, error, abort or timeout wins."""
    loop = asyncio.get_running_loop()
    outcome: asyncio.Future[str] = loop.create_future()
    server: asyncio.Server | None = None
    timer: asyncio.TimerHandle | None = None
    tasks: set[asyncio.Task[Any]] = set()

    def abort_listener(_reason: str | None) -> None:
        cleanup()
        reject_once(AuthenticationCancelledError())

    def cleanup() -> None:
        nonlocal server, timer
        if server is not None:
            server.close()
            server = None
        if timer is not None:
            timer.cancel()
            timer = None
        if abort_signal is not None:
            abort_signal.remove_listener(abort_listener)
        _debug(server_name, "MCP OAuth server cleaned up")

    def resolve_once(code: str) -> None:
        if not outcome.done():
            outcome.set_result(code)

    def reject_once(error: BaseException) -> None:
        if not outcome.done():
            outcome.set_exception(error)

    if abort_signal is not None:
        if abort_signal.aborted:
            abort_listener(abort_signal.reason)
            return await outcome
        abort_signal.add_listener(abort_listener)

    if on_waiting_for_callback is not None:

        def submit(callback_url: str) -> None:
            try:
                parts = urlsplit(callback_url)
                if not parts.scheme:
                    return
                query = parse_qs(parts.query, keep_blank_values=True)
            except ValueError:
                return
            error = _first(query, "error")
            if error:
                cleanup()
                reject_once(RuntimeError(f"OAuth error: {error} - {_first(query, 'error_description') or ''}"))
                return
            code = _first(query, "code")
            if not code:
                return
            if _first(query, "state") != oauth_state:
                cleanup()
                reject_once(RuntimeError(STATE_MISMATCH_MESSAGE))
                return
            _debug(server_name, "Received auth code via manual callback URL")
            cleanup()
            resolve_once(code)

        on_waiting_for_callback(submit)

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = (await reader.readline()).decode("latin-1")
            while (await reader.readline()) not in (b"\r\n", b"\n", b""):
                pass
            target = request_line.split(" ")[1] if request_line.count(" ") >= 2 else ""
            parts = urlsplit(target)
            if parts.path != "/callback":
                return
            query = parse_qs(parts.query, keep_blank_values=True)
            code, state = _first(query, "code"), _first(query, "state")
            error, description = _first(query, "error"), _first(query, "error_description")
            error_uri = _first(query, "error_uri")
            if not error and state != oauth_state:
                writer.write(_http_response(400, STATE_MISMATCH_PAGE))
                await writer.drain()
                cleanup()
                reject_once(RuntimeError(STATE_MISMATCH_MESSAGE))
                return
            if error:
                safe_description = html.escape(description) if description else ""
                writer.write(_http_response(
                    200, f"<h1>Authentication Error</h1><p>{html.escape(error)}: {safe_description}</p>"
                         "<p>You can close this window.</p>"))
                await writer.drain()
                cleanup()
                message = f"OAuth error: {error}"
                if description:
                    message += f" - {description}"
                if error_uri:
                    message += f" (See: {error_uri})"
                reject_once(RuntimeError(message))
                return
            if code:
                writer.write(_http_response(200, SUCCESS_PAGE))
                await writer.drain()
                cleanup()
                resolve_once(code)
        except (OSError, IndexError, ValueError):
            pass
        finally:
            writer.close()

    try:
        server = await asyncio.start_server(handle, "127.0.0.1", port)
    except OSError as exc:
        cleanup()
        if exc.errno in (98, 48, 10048):  # EADDRINUSE on Linux, macOS, Windows
            find_cmd = (f"netstat -ano | findstr :{port}" if sys.platform == "win32"
                        else f"lsof -ti:{port} -sTCP:LISTEN")
            reject_once(RuntimeError(
                f"OAuth callback port {port} is already in use — another process may be holding it. "
                f"Run `{find_cmd}` to find it."))
        else:
            reject_once(RuntimeError(f"OAuth callback server failed: {exc.strerror or exc}"))
        return await outcome

    async def start_auth() -> None:
        try:
            _debug(server_name, "Starting SDK auth")
            _debug(server_name, f"Server URL: {server_url}")
            result = await sdk_auth(provider, server_url=server_url, scope=scope,
                                    resource_metadata_url=resource_metadata_url)
            _debug(server_name, f"Initial auth result: {result}")
            if result != "REDIRECT":
                _debug(server_name, f"Unexpected auth result, expected REDIRECT: {result}")
        except Exception as exc:  # noqa: BLE001 - upstream: rejects the flow with the wrapped message
            _debug(server_name, f"SDK auth error: {exc}")
            cleanup()
            reject_once(RuntimeError(f"SDK auth failed: {exc}"))

    task = loop.create_task(start_auth())
    tasks.add(task)
    task.add_done_callback(tasks.discard)

    def on_timeout() -> None:
        cleanup()
        reject_once(RuntimeError("Authentication timeout"))

    timer = loop.call_later(AUTHENTICATION_TIMEOUT_MS / 1000, on_timeout)
    try:
        return await outcome
    finally:
        cleanup()
        for pending in list(tasks):
            if not pending.done():
                pending.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


# ── step-up and the provider ──────────────────────────────────────────────────


def mark_step_up_from_response(response: httpx2.Response, provider: ClaudeAuthProvider) -> None:
    """`wrapFetchWithStepUpDetection`'s check: a 403 `insufficient_scope` marks step-up pending."""
    if response.status_code != 403:
        return
    www_auth = response.headers.get("WWW-Authenticate")
    if www_auth and "insufficient_scope" in www_auth:
        match = _STEP_UP_SCOPE.search(www_auth)
        scope = (match.group(1) or match.group(2)) if match else None
        if scope:
            provider.mark_step_up_pending(scope)


def wrap_fetch_with_step_up_detection(base_fetch: FetchLike, provider: ClaudeAuthProvider) -> FetchLike:
    """
    `wrapFetchWithStepUpDetection`: mark step-up pending on a 403 `insufficient_scope` before
    `auth()` sees it, so `tokens()` withholds the refresh token (RFC 6749 §6 forbids raising the
    scope by refresh) and the SDK goes to the authorization flow.
    """

    async def wrapped(
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        content: bytes | None = None,
    ) -> httpx2.Response:
        response = await base_fetch(url, method=method, headers=headers, content=content)
        mark_step_up_from_response(response, provider)
        return response

    return wrapped


def get_scope_from_metadata(metadata: Mapping[str, Any] | None) -> str | None:
    """`getScopeFromMetadata`: `scope`, then `default_scope`, then `scopes_supported` joined."""
    if not metadata:
        return None
    if isinstance(metadata.get("scope"), str):
        return str(metadata["scope"])
    if isinstance(metadata.get("default_scope"), str):
        return str(metadata["default_scope"])
    supported = metadata.get("scopes_supported")
    if isinstance(supported, list):
        return " ".join(str(s) for s in supported)
    return None


def _tokens_from_entry(entry: Mapping[str, Any], expires_in: float, *, refresh: bool = True) -> dict[str, Any]:
    return _merge(
        access_token=entry.get("accessToken"),
        refresh_token=entry.get("refreshToken") if refresh else None,
        expires_in=expires_in,
        scope=entry.get("scope"),
        token_type="Bearer",
    )


class ClaudeAuthProvider:
    """`ClaudeAuthProvider`: the SDK's `OAuthClientProvider` over the secure store."""

    def __init__(
        self,
        server_name: str,
        server_config: McpServerConfig,
        redirect_uri: str | None = None,
        handle_redirection: bool = False,
        on_authorization_url: Callable[[str], None] | None = None,
        skip_browser_open: bool = False,
    ) -> None:
        self.server_name = server_name
        self.server_config = server_config
        self._redirect_uri = redirect_uri or build_redirect_uri()
        self.handle_redirection = handle_redirection
        self._on_authorization_url = on_authorization_url
        self.skip_browser_open = skip_browser_open
        self._code_verifier: str | None = None
        self._authorization_url: str | None = None
        self._state: str | None = None
        self._scopes: str | None = None
        self._metadata: dict[str, Any] | None = None
        self._refresh_in_progress: asyncio.Future[dict[str, Any] | None] | None = None
        self._pending_step_up_scope: str | None = None

    @property
    def _key(self) -> str:
        return get_server_key(self.server_name, self.server_config)

    @property
    def _url(self) -> str:
        return str(self.server_config.url)

    @property
    def redirect_url(self) -> str:
        return self._redirect_uri

    @property
    def authorization_url(self) -> str | None:
        return self._authorization_url

    @property
    def client_metadata(self) -> dict[str, Any]:
        """`clientMetadata`: a public client; the metadata's scope when there is one."""
        metadata: dict[str, Any] = {
            "client_name": f"TDDAgents ({self.server_name})",
            "redirect_uris": [self._redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }
        scope = get_scope_from_metadata(self._metadata)
        if scope:
            metadata["scope"] = scope
            _debug(self.server_name, f"Using scope from metadata: {scope}")
        return metadata

    @property
    def client_metadata_url(self) -> str | None:
        """`clientMetadataUrl` (CIMD, SEP-991): the environment override only (module docstring)."""
        override = os.environ.get(CLIENT_METADATA_URL_ENV)
        if override:
            _debug(self.server_name, f"Using CIMD URL from env: {override}")
            return override
        return MCP_CLIENT_METADATA_URL

    def set_metadata(self, metadata: dict[str, Any] | None) -> None:
        self._metadata = metadata

    def mark_step_up_pending(self, scope: str) -> None:
        """`markStepUpPending`."""
        self._pending_step_up_scope = scope
        _debug(self.server_name, f"Marked step-up pending: {scope}")

    async def state(self) -> str:
        """`state`: 32 random bytes, base64url, generated once per provider."""
        if not self._state:
            self._state = secrets.token_urlsafe(32)
            _debug(self.server_name, "Generated new OAuth state")
        return self._state

    async def client_information(self) -> dict[str, Any] | None:
        """`clientInformation`: the stored registration, else the configured client id."""
        data = get_secure_storage().read()
        stored = _entry(data, self._key)
        if stored and stored.get("clientId"):
            _debug(self.server_name, "Found client info")
            return _merge(client_id=stored["clientId"], client_secret=stored.get("clientSecret"))
        config_client_id = self.server_config.oauth.client_id if self.server_config.oauth else None
        if config_client_id:
            client_config = ((data or {}).get("mcpOAuthClientConfig") or {}).get(self._key) or {}
            _debug(self.server_name, "Using pre-configured client ID")
            return _merge(client_id=config_client_id, client_secret=client_config.get("clientSecret"))
        _debug(self.server_name, "No client info found")
        return None

    async def save_client_information(self, client_information: dict[str, Any]) -> None:
        storage = get_secure_storage()
        existing = storage.read() or {}
        previous = _entry(existing, self._key) or {}
        storage.update(_write_entry(existing, self._key, _merge(
            previous,
            serverName=self.server_name,
            serverUrl=self._url,
            clientId=client_information.get("client_id"),
            clientSecret=client_information.get("client_secret"),
            accessToken=previous.get("accessToken") or "",
            expiresAt=previous.get("expiresAt") or 0,
        )))

    async def tokens(self) -> dict[str, Any] | None:
        """
        `tokens`: the stored tokens, refreshed first (once, shared) when they expire within five
        minutes and a refresh token exists; the refresh token is withheld while a step-up is
        pending that the current scope does not cover.
        """
        data = await get_secure_storage().read_async()
        token_data = _entry(data, self._key)
        if not token_data:
            _debug(self.server_name, "No token data found")
            return None
        expires_in = (float(token_data.get("expiresAt") or 0) - _now_ms()) / 1000
        current_scopes = str(token_data.get("scope") or "").split(" ") if token_data.get("scope") else []
        needs_step_up = self._pending_step_up_scope is not None and any(
            s not in current_scopes for s in self._pending_step_up_scope.split(" "))
        if needs_step_up:
            _debug(self.server_name, f"Step-up pending ({self._pending_step_up_scope}), omitting refresh_token")
        if expires_in <= 0 and not token_data.get("refreshToken"):
            _debug(self.server_name, "Token expired without refresh token")
            return None
        if expires_in <= PROACTIVE_REFRESH_WINDOW_S and token_data.get("refreshToken") and not needs_step_up:
            if self._refresh_in_progress is None:
                _debug(self.server_name, f"Token expires in {int(expires_in // 1)}s, attempting proactive refresh")
                future = asyncio.ensure_future(self.refresh_authorization(str(token_data["refreshToken"])))
                self._refresh_in_progress = future

                def clear(_: Any, future: Any = future) -> None:
                    if self._refresh_in_progress is future:
                        self._refresh_in_progress = None

                future.add_done_callback(clear)
            else:
                _debug(self.server_name, "Token refresh already in progress, reusing existing promise")
            try:
                refreshed = await asyncio.shield(self._refresh_in_progress)
                if refreshed:
                    _debug(self.server_name, "Token refreshed successfully")
                    return refreshed
                _debug(self.server_name, "Token refresh failed, returning current tokens")
            except Exception as exc:  # noqa: BLE001 - upstream returns the current tokens
                _debug(self.server_name, f"Token refresh error: {exc}")
        tokens = _tokens_from_entry(token_data, expires_in, refresh=not needs_step_up)
        _debug(self.server_name, "Returning tokens")
        return tokens

    async def save_tokens(self, tokens: dict[str, Any]) -> None:
        """`saveTokens`: clears a pending step-up; `expiresAt` is now + `expires_in` (or 3600 s)."""
        self._pending_step_up_scope = None
        storage = get_secure_storage()
        existing = storage.read() or {}
        previous = _entry(existing, self._key) or {}
        _debug(self.server_name, "Saving tokens")
        storage.update(_write_entry(existing, self._key, _merge(
            previous,
            serverName=self.server_name,
            serverUrl=self._url,
            accessToken=tokens.get("access_token"),
            refreshToken=tokens.get("refresh_token"),
            expiresAt=_now_ms() + float(tokens.get("expires_in") or DEFAULT_EXPIRES_IN_S) * 1000,
            scope=tokens.get("scope"),
        )))

    async def redirect_to_authorization(self, authorization_url: str) -> None:
        """
        `redirectToAuthorization`: remember the URL and its scope; the transport's provider
        (`handle_redirection` false) persists that scope as the step-up scope and stops there,
        the flow's provider announces the URL and opens the browser.
        """
        self._authorization_url = authorization_url
        scopes = dict(parse_qsl(urlsplit(authorization_url).query)).get("scope")
        _debug(self.server_name, f"Authorization URL: {redact_sensitive_url_params(authorization_url)}")
        if scopes:
            self._scopes = scopes
        else:
            metadata_scope = get_scope_from_metadata(self._metadata)
            if metadata_scope:
                self._scopes = metadata_scope
        if self._scopes and not self.handle_redirection:
            storage = get_secure_storage()
            existing = storage.read() or {}
            entry = _entry(existing, self._key)
            if entry is not None:
                entry["stepUpScope"] = self._scopes
                storage.update(existing)
                _debug(self.server_name, f"Persisted step-up scope: {self._scopes}")
        if not self.handle_redirection:
            _debug(self.server_name, "Redirection handling is disabled, skipping redirect")
            return
        if not authorization_url.startswith(("http://", "https://")):
            raise ValueError("Invalid authorization URL: must use http:// or https:// scheme")
        if self._on_authorization_url is not None:
            self._on_authorization_url(authorization_url)
        if not self.skip_browser_open:
            if not await open_browser(authorization_url):
                _debug(self.server_name, "Browser didn't open automatically. URL is shown in UI.")
        else:
            _debug(self.server_name, "Skipping browser open (skipBrowserOpen=true).")

    async def save_code_verifier(self, code_verifier: str) -> None:
        self._code_verifier = code_verifier

    async def code_verifier(self) -> str:
        if not self._code_verifier:
            raise ValueError("No code verifier saved")
        return self._code_verifier

    async def invalidate_credentials(self, scope: InvalidateScope) -> None:
        """`invalidateCredentials`."""
        storage = get_secure_storage()
        existing = storage.read()
        if not existing or not isinstance(existing.get("mcpOAuth"), dict):
            return
        token_data = _entry(existing, self._key)
        if not token_data:
            return
        if scope == "all":
            del existing["mcpOAuth"][self._key]
        elif scope == "client":
            token_data.pop("clientId", None)
            token_data.pop("clientSecret", None)
        elif scope == "tokens":
            token_data["accessToken"] = ""
            token_data.pop("refreshToken", None)
            token_data["expiresAt"] = 0
        elif scope == "verifier":
            self._code_verifier = None
            return
        elif scope == "discovery":
            token_data.pop("discoveryState", None)
            token_data.pop("stepUpScope", None)
        storage.update(existing)
        _debug(self.server_name, f"Invalidated credentials (scope: {scope})")

    async def save_discovery_state(self, state: dict[str, Any]) -> None:
        """`saveDiscoveryState`: only the two URLs, never the metadata documents."""
        storage = get_secure_storage()
        existing = storage.read() or {}
        previous = _entry(existing, self._key) or {}
        _debug(self.server_name, f"Saving discovery state (authServer: {state.get('authorizationServerUrl')})")
        storage.update(_write_entry(existing, self._key, _merge(
            previous,
            serverName=self.server_name,
            serverUrl=self._url,
            accessToken=previous.get("accessToken") or "",
            expiresAt=previous.get("expiresAt") or 0,
            discoveryState=_merge(
                authorizationServerUrl=state.get("authorizationServerUrl"),
                resourceMetadataUrl=state.get("resourceMetadataUrl"),
            ),
        )))

    async def discovery_state(self) -> dict[str, Any] | None:
        """`discoveryState`: the persisted URLs, else the configured metadata URL's document."""
        cached = (_entry(get_secure_storage().read(), self._key) or {}).get("discoveryState")
        if isinstance(cached, dict) and cached.get("authorizationServerUrl"):
            return _merge(
                authorizationServerUrl=cached["authorizationServerUrl"],
                resourceMetadataUrl=cached.get("resourceMetadataUrl"),
                resourceMetadata=cached.get("resourceMetadata"),
                authorizationServerMetadata=cached.get("authorizationServerMetadata"),
            )
        metadata_url = self.server_config.oauth.auth_server_metadata_url if self.server_config.oauth else None
        if metadata_url:
            _debug(self.server_name, f"Fetching metadata from configured URL: {metadata_url}")
            try:
                metadata = await fetch_auth_server_metadata(self.server_name, self._url, metadata_url)
                if metadata:
                    return {"authorizationServerUrl": metadata["issuer"], "authorizationServerMetadata": metadata}
            except Exception as exc:  # noqa: BLE001 - upstream logs and answers undefined
                _debug(self.server_name, f"Failed to fetch from configured metadata URL: {exc}")
        return None

    async def refresh_authorization(self, refresh_token: str) -> dict[str, Any] | None:
        """
        `refreshAuthorization`: under the per-server cross-process lock (5 tries, 1-2 s apart),
        reuse tokens another process already refreshed, else refresh with the freshest stored
        refresh token.
        """
        claude_dir = config_home()
        claude_dir.mkdir(parents=True, exist_ok=True)
        sanitized = re.sub(r"[^a-zA-Z0-9]", "_", self._key)
        lockfile_path = claude_dir / f"mcp-refresh-{sanitized}.lock"
        release: Callable[[], Awaitable[None]] | None = None
        for retry in range(MAX_LOCK_RETRIES):
            try:
                release = await lockfile_lock(
                    lockfile_path, realpath=False,
                    on_compromised=lambda _e: _debug(self.server_name, "Refresh lock was compromised"))
                break
            except OSError as exc:
                code = getattr(exc, "code", None)
                if code == "ELOCKED":
                    _debug(self.server_name, f"Refresh lock held by another process, waiting "
                                             f"(attempt {retry + 1}/{MAX_LOCK_RETRIES})")
                    await _sleep((1000 + random.random() * 1000) / 1000)
                    continue
                _debug(self.server_name, f"Failed to acquire refresh lock: {code}, proceeding without lock")
                break
        if release is None:
            _debug(self.server_name, f"Could not acquire refresh lock after {MAX_LOCK_RETRIES} retries, "
                                     "proceeding without lock")
        try:
            clear_keychain_cache()
            token_data = _entry(get_secure_storage().read(), self._key)
            if token_data:
                expires_in = (float(token_data.get("expiresAt") or 0) - _now_ms()) / 1000
                if expires_in > PROACTIVE_REFRESH_WINDOW_S:
                    _debug(self.server_name, "Another process already refreshed tokens")
                    return _tokens_from_entry(token_data, expires_in)
                if token_data.get("refreshToken"):
                    refresh_token = str(token_data["refreshToken"])
            return await self._do_refresh(refresh_token)
        finally:
            if release is not None:
                with contextlib.suppress(OSError):
                    await release()

    async def _do_refresh(self, refresh_token: str) -> dict[str, Any] | None:
        """`_doRefresh`: up to three attempts, retrying timeouts and transient server errors (1 s, 2 s)."""
        for attempt in range(1, MAX_REFRESH_ATTEMPTS + 1):
            try:
                auth_fetch = create_auth_fetch()
                metadata = self._metadata
                if not metadata:
                    cached = await self.discovery_state()
                    if cached and cached.get("authorizationServerMetadata"):
                        metadata = cached["authorizationServerMetadata"]
                    elif cached and cached.get("authorizationServerUrl"):
                        metadata = await discover_authorization_server_metadata(
                            str(cached["authorizationServerUrl"]), fetch_fn=auth_fetch)
                if not metadata:
                    oauth = self.server_config.oauth
                    metadata = await fetch_auth_server_metadata(
                        self.server_name, self._url, oauth.auth_server_metadata_url if oauth else None, auth_fetch)
                if not metadata:
                    _debug(self.server_name, "Failed to discover OAuth metadata")
                    return None
                self._metadata = metadata
                client_info = await self.client_information()
                if not client_info:
                    _debug(self.server_name, "No client information available")
                    return None
                new_tokens = await refresh_authorization(
                    self._url, metadata=metadata, client_information=client_info, refresh_token=refresh_token,
                    resource=url_href(self._url), fetch_fn=auth_fetch)
                if new_tokens:
                    _debug(self.server_name, "Token refresh successful")
                    await self.save_tokens(new_tokens)
                    return new_tokens
                return None
            except InvalidGrantError as exc:
                _debug(self.server_name, f"Token refresh failed with invalid_grant: {exc}")
                clear_keychain_cache()
                token_data = _entry(get_secure_storage().read(), self._key)
                if token_data:
                    expires_in = (float(token_data.get("expiresAt") or 0) - _now_ms()) / 1000
                    if expires_in > PROACTIVE_REFRESH_WINDOW_S:
                        _debug(self.server_name, "Another process refreshed tokens, using those")
                        return _tokens_from_entry(token_data, expires_in)
                await self.invalidate_credentials("tokens")
                return None
            except Exception as exc:  # noqa: BLE001 - upstream: retry transient failures, else give up
                is_timeout = isinstance(exc, httpx2.TimeoutException) or bool(_TIMEOUT_MESSAGE.search(str(exc)))
                is_transient = isinstance(exc, (ServerError, TemporarilyUnavailableError, TooManyRequestsError))
                if not (is_timeout or is_transient) or attempt >= MAX_REFRESH_ATTEMPTS:
                    _debug(self.server_name, f"Token refresh failed: {exc}")
                    return None
                delay_ms = 1000 * 2 ** (attempt - 1)
                _debug(self.server_name, f"Token refresh failed, retrying in {delay_ms}ms "
                                         f"(attempt {attempt}/{MAX_REFRESH_ATTEMPTS})")
                await _sleep(delay_ms / 1000)
        return None


# ── the transport bridge ──────────────────────────────────────────────────────


class McpTransportAuth(httpx2.Auth):
    """
    The TypeScript transports' `authProvider` handling, as an `httpx2.Auth`: `Bearer` from
    `tokens()` on every request (a configured `Authorization` header wins, as upstream's
    `requestInit` headers do); a 401 runs `auth()` and retries once (a second 401 after a
    completed flow is an error); a 403 `insufficient_scope` marks step-up pending, runs `auth()`
    with the challenged scope and retries (once per distinct challenge). A failure is kept in
    `failure` and the response handed back unchanged.
    """

    def __init__(self, provider: ClaudeAuthProvider, server_url: str, fetch_fn: FetchLike | None = None) -> None:
        self.provider = provider
        self.server_url = server_url
        self.fetch_fn = fetch_fn or create_auth_fetch()
        self.failure: Exception | None = None
        self._resource_metadata_url: str | None = None
        self._scope: str | None = None
        self._has_completed_auth_flow = False
        self._last_upscoping_header: str | None = None

    async def _authorize(self, request: httpx2.Request, configured_authorization: bool) -> None:
        """`_commonHeaders`: the provider's `Bearer`, unless the server's configured headers carry one."""
        tokens = await self.provider.tokens()
        if tokens and not configured_authorization:
            request.headers["Authorization"] = f"Bearer {tokens.get('access_token')}"

    async def _run_auth(self) -> bool:
        try:
            result = await sdk_auth(self.provider, server_url=self.server_url, scope=self._scope,
                                    resource_metadata_url=self._resource_metadata_url, fetch_fn=self.fetch_fn)
        except Exception as exc:  # noqa: BLE001 - the transport's onerror path: recorded, re-raised by client.py
            self.failure = exc
            return False
        if result != "AUTHORIZED":
            self.failure = UnauthorizedError()
            return False
        return True

    async def async_auth_flow(self, request: httpx2.Request) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        # Upstream spreads `requestInit.headers` after the provider's header, so a configured
        # Authorization header (already on the request here) wins over the stored token.
        configured_authorization = "Authorization" in request.headers
        await self._authorize(request, configured_authorization)
        response = yield request
        while True:
            mark_step_up_from_response(response, self.provider)
            if response.status_code == 401:
                if self._has_completed_auth_flow:
                    self.failure = RuntimeError("Server returned 401 after successful authentication")
                    return
                params = extract_www_authenticate_params(response)
                self._resource_metadata_url = params.resource_metadata_url
                self._scope = params.scope
                if not await self._run_auth():
                    return
                self._has_completed_auth_flow = True
            elif response.status_code == 403:
                params = extract_www_authenticate_params(response)
                if params.error != "insufficient_scope":
                    return
                header = response.headers.get("WWW-Authenticate")
                if self._last_upscoping_header == header:
                    self.failure = RuntimeError("Server returned 403 after trying upscoping")
                    return
                if params.scope:
                    self._scope = params.scope
                if params.resource_metadata_url:
                    self._resource_metadata_url = params.resource_metadata_url
                self._last_upscoping_header = header
                if not await self._run_auth():
                    return
            else:
                if response.is_success:
                    self._has_completed_auth_flow = False
                    self._last_upscoping_header = None
                return
            await self._authorize(request, configured_authorization)
            response = yield request


# ── client secrets ────────────────────────────────────────────────────────────


async def read_client_secret() -> str:
    """`readClientSecret`: `TDDAGENTS_MCP_CLIENT_SECRET`, else a hidden prompt on a TTY."""
    env_secret = os.environ.get(CLIENT_SECRET_ENV)
    if env_secret:
        return env_secret
    if not sys.stdin.isatty():
        raise RuntimeError(
            f"No TTY available to prompt for client secret. Set {CLIENT_SECRET_ENV} env var instead.")
    try:
        return await asyncio.to_thread(getpass.getpass, "Enter OAuth client secret: ", sys.stderr)
    except KeyboardInterrupt:
        raise RuntimeError("Cancelled") from None


def save_mcp_client_secret(server_name: str, server_config: McpServerConfig, client_secret: str) -> None:
    """`saveMcpClientSecret`."""
    storage = get_secure_storage()
    existing = storage.read() or {}
    slot = existing.get("mcpOAuthClientConfig") if isinstance(existing.get("mcpOAuthClientConfig"), dict) else {}
    storage.update({**existing, "mcpOAuthClientConfig": {
        **(slot or {}), get_server_key(server_name, server_config): {"clientSecret": client_secret}}})


def clear_mcp_client_config(server_name: str, server_config: McpServerConfig) -> None:
    """`clearMcpClientConfig`."""
    storage = get_secure_storage()
    existing = storage.read()
    slot = (existing or {}).get("mcpOAuthClientConfig")
    if not existing or not isinstance(slot, dict):
        return
    key = get_server_key(server_name, server_config)
    if slot.get(key):
        del slot[key]
        storage.update(existing)


def get_mcp_client_config(server_name: str, server_config: McpServerConfig) -> dict[str, Any] | None:
    """`getMcpClientConfig`."""
    slot = (get_secure_storage().read() or {}).get("mcpOAuthClientConfig")
    entry = slot.get(get_server_key(server_name, server_config)) if isinstance(slot, dict) else None
    return entry if isinstance(entry, dict) else None


__all__ = [
    "AUTHENTICATION_TIMEOUT_MS",
    "AUTH_REQUEST_TIMEOUT_MS",
    "CLIENT_METADATA_URL_ENV",
    "CLIENT_SECRET_ENV",
    "MAX_LOCK_RETRIES",
    "MAX_REFRESH_ATTEMPTS",
    "MCP_CLIENT_METADATA_URL",
    "NONSTANDARD_INVALID_GRANT_ALIASES",
    "SENSITIVE_OAUTH_PARAMS",
    "AuthenticationCancelledError",
    "ClaudeAuthProvider",
    "McpTransportAuth",
    "basic_auth_header",
    "clear_mcp_client_config",
    "clear_server_tokens_from_local_storage",
    "create_auth_fetch",
    "fetch_auth_server_metadata",
    "get_mcp_client_config",
    "get_scope_from_metadata",
    "get_server_key",
    "has_mcp_discovery_but_no_token",
    "is_xaa_enabled",
    "mark_step_up_from_response",
    "normalize_oauth_error_body",
    "perform_mcp_oauth_flow",
    "read_client_secret",
    "redact_sensitive_url_params",
    "revoke_server_tokens",
    "revoke_token",
    "save_mcp_client_secret",
]
