"""
The MCP SDK's client-side OAuth driver, as `auth.ts` and the transports call it.

Ported from the TypeScript MCP SDK (`@modelcontextprotocol/sdk`, read at 1.32.2), which
claude-code imports and which the source snapshot does not contain:

* `client/auth.js` -> `UnauthorizedError`, `auth` / `authInternal`, `selectClientAuthMethod`,
  `applyClientAuthentication`, `parseErrorResponse`, `isHttpsUrl`, `selectResourceURL`,
  `extractWWWAuthenticateParams` (and `extractFieldFromWwwAuth`),
  `discoverOAuthProtectedResourceMetadata`, `discoverMetadataWithFallback`,
  `buildWellKnownPath`, `fetchWithCorsRetry`, `buildDiscoveryUrls`,
  `discoverAuthorizationServerMetadata`, `discoverOAuthServerInfo`, `startAuthorization`,
  `prepareAuthorizationCodeRequest`, `executeTokenRequest`, `refreshAuthorization`,
  `fetchToken`, `registerClient`;
* `server/auth/errors.js` -> `OAuthError`, its subclasses and `OAUTH_ERRORS`;
* `shared/auth.js` -> the zod schemas `auth.ts` relies on (`OAuthMetadataSchema`,
  `OpenIdProviderDiscoveryMetadataSchema`, `OAuthProtectedResourceMetadataSchema`,
  `OAuthTokensSchema`, `OAuthErrorResponseSchema`, `OAuthClientInformationFullSchema`), as
  validating functions over plain dicts (`looseObject` keeps unknown keys, `strip` drops them);
* `shared/auth-utils.js` -> `resourceUrlFromServerUrl`, `checkResourceAllowed`.

Why a port instead of the Python SDK's `mcp.client.auth.OAuthClientProvider`: upstream's
`ClaudeAuthProvider` is an implementation of the TypeScript `OAuthClientProvider` interface,
driven by this `auth()`: it is asked for tokens on every request (and refreshes them itself,
under a cross-process lock), persists the discovery state, invalidates credentials by scope,
answers `REDIRECT` without blocking (the transport then fails with `UnauthorizedError` and the
server becomes `needs-auth`), and `performMCPOAuthFlow` calls `auth()` twice around its own
callback server. The Python provider owns its tokens in memory, has none of those hooks and
blocks inside the request on its callback handler. The Python SDK's types and helpers are not
reused for the same reason: their URL normalisation (pydantic `AnyHttpUrl`) differs from the
WHATWG URLs the upstream strings were produced by.

Not ported: the SEP-2352 issuer stamps (`issuer` on stored values, `discardIfIssuerMismatch`,
`boundElsewhereError`; newer than the claude-code release this repository follows), and the
provider hooks `ClaudeAuthProvider` does not implement (`validateResourceURL`,
`addClientAuthentication`, `prepareTokenRequest`).

HTTP goes through a `FetchLike`: `(url, *, method, headers, content) -> httpx2.Response`, the
seam the callers wrap (timeouts, error-body normalisation, step-up detection) and the tests
replace. A transport-level failure is Node's `TypeError: fetch failed`; a timeout is not.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx2
from mcp.shared._httpx_utils import request_within_origin
from mcp.types import LATEST_PROTOCOL_VERSION

AuthResult = Literal["AUTHORIZED", "REDIRECT"]
InvalidateScope = Literal["all", "client", "tokens", "verifier", "discovery"]

AUTHORIZATION_CODE_RESPONSE_TYPE = "code"
AUTHORIZATION_CODE_CHALLENGE_METHOD = "S256"


# ── errors (server/auth/errors.js) ────────────────────────────────────────────


class UnauthorizedError(Exception):
    """`UnauthorizedError`."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or "Unauthorized")


class OAuthError(Exception):
    """`OAuthError`: an RFC 6749 error with its `error_code`."""

    error_code = "unknown_error"

    def __init__(self, message: str = "", error_uri: str | None = None) -> None:
        super().__init__(message)
        self.error_uri = error_uri


def _oauth_error(name: str, code: str) -> type[OAuthError]:
    return type(name, (OAuthError,), {"error_code": code, "__doc__": f"`{name}` (`{code}`)."})


InvalidRequestError = _oauth_error("InvalidRequestError", "invalid_request")
InvalidClientError = _oauth_error("InvalidClientError", "invalid_client")
InvalidGrantError = _oauth_error("InvalidGrantError", "invalid_grant")
UnauthorizedClientError = _oauth_error("UnauthorizedClientError", "unauthorized_client")
UnsupportedGrantTypeError = _oauth_error("UnsupportedGrantTypeError", "unsupported_grant_type")
InvalidScopeError = _oauth_error("InvalidScopeError", "invalid_scope")
AccessDeniedError = _oauth_error("AccessDeniedError", "access_denied")
ServerError = _oauth_error("ServerError", "server_error")
TemporarilyUnavailableError = _oauth_error("TemporarilyUnavailableError", "temporarily_unavailable")
UnsupportedResponseTypeError = _oauth_error("UnsupportedResponseTypeError", "unsupported_response_type")
UnsupportedTokenTypeError = _oauth_error("UnsupportedTokenTypeError", "unsupported_token_type")
InvalidTokenError = _oauth_error("InvalidTokenError", "invalid_token")
MethodNotAllowedError = _oauth_error("MethodNotAllowedError", "method_not_allowed")
TooManyRequestsError = _oauth_error("TooManyRequestsError", "too_many_requests")
InvalidClientMetadataError = _oauth_error("InvalidClientMetadataError", "invalid_client_metadata")
InsufficientScopeError = _oauth_error("InsufficientScopeError", "insufficient_scope")
InvalidTargetError = _oauth_error("InvalidTargetError", "invalid_target")

#: `OAUTH_ERRORS`.
OAUTH_ERRORS: dict[str, type[OAuthError]] = {
    cls.error_code: cls
    for cls in (
        InvalidRequestError, InvalidClientError, InvalidGrantError, UnauthorizedClientError,
        UnsupportedGrantTypeError, InvalidScopeError, AccessDeniedError, ServerError,
        TemporarilyUnavailableError, UnsupportedResponseTypeError, UnsupportedTokenTypeError,
        InvalidTokenError, MethodNotAllowedError, TooManyRequestsError, InvalidClientMetadataError,
        InsufficientScopeError, InvalidTargetError,
    )
}


class SchemaError(ValueError):
    """A zod `parse` failure."""


# ── URLs (WHATWG `URL` where the upstream strings depend on it) ───────────────


def url_pathname(url: str) -> str:
    """`new URL(url).pathname`: `/` for a bare origin."""
    return urlsplit(url).path or "/"


def url_origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def url_href(url: str) -> str:
    """`new URL(url).href` for an http(s) URL: a bare origin gains its `/`."""
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=parts.path or "/"))


def is_url(value: Any) -> bool:
    """`z.string().url()` / `URL.canParse` for absolute URLs."""
    if not isinstance(value, str):
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return bool(parts.scheme) and (bool(parts.netloc) or parts.scheme not in ("http", "https"))


def is_safe_url(value: Any) -> bool:
    """`SafeUrlSchema`: a URL that is not `javascript:`, `data:` or `vbscript:`."""
    return is_url(value) and urlsplit(value).scheme.lower() not in ("javascript", "data", "vbscript")


def resource_url_from_server_url(url: str) -> str:
    """`resourceUrlFromServerUrl`: the URL without its fragment."""
    return url_href(urlunsplit(urlsplit(url)._replace(fragment="")))


def check_resource_allowed(requested_resource: str, configured_resource: str) -> bool:
    """`checkResourceAllowed`: same origin, and the requested path under the configured one."""
    if url_origin(requested_resource).lower() != url_origin(configured_resource).lower():
        return False
    requested = url_pathname(requested_resource)
    configured = url_pathname(configured_resource)
    if len(requested) < len(configured):
        return False
    requested = requested if requested.endswith("/") else requested + "/"
    configured = configured if configured.endswith("/") else configured + "/"
    return requested.startswith(configured)


def is_https_url(value: str | None) -> bool:
    """`isHttpsUrl` (SEP-991): https with a non-root path."""
    if not value:
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return parts.scheme == "https" and bool(parts.netloc) and (parts.path or "/") != "/"


def _set_query_param(url: str, name: str, value: str, *, append: bool = False) -> str:
    """`url.searchParams.set` / `append`."""
    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    if not append:
        pairs = [(k, v) for k, v in pairs if k != name]
    pairs.append((name, value))
    return urlunsplit(parts._replace(query=urlencode(pairs)))


# ── schemas (shared/auth.js) ──────────────────────────────────────────────────


def _require(data: Mapping[str, Any], key: str, check: Any, *, optional: bool = False) -> None:
    if key not in data or data[key] is None:
        if optional:
            return
        raise SchemaError(f"{key}: Required")
    if not check(data[key]):
        raise SchemaError(f"{key}: Invalid")


def _str(value: Any) -> bool:
    return isinstance(value, str)


def _str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _bool(value: Any) -> bool:
    return isinstance(value, bool)


def _object(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise SchemaError("Expected object")
    return data


def parse_oauth_metadata(data: Any) -> dict[str, Any]:
    """`OAuthMetadataSchema.parse` (a loose object)."""
    obj = _object(data)
    _require(obj, "issuer", _str)
    _require(obj, "authorization_endpoint", is_safe_url)
    _require(obj, "token_endpoint", is_safe_url)
    _require(obj, "response_types_supported", _str_list)
    for key in ("registration_endpoint", "service_documentation", "revocation_endpoint"):
        _require(obj, key, is_safe_url, optional=True)
    for key in ("scopes_supported", "response_modes_supported", "grant_types_supported",
                "token_endpoint_auth_methods_supported", "revocation_endpoint_auth_methods_supported",
                "code_challenge_methods_supported"):
        _require(obj, key, _str_list, optional=True)
    _require(obj, "client_id_metadata_document_supported", _bool, optional=True)
    return dict(obj)


def parse_openid_provider_discovery_metadata(data: Any) -> dict[str, Any]:
    """`OpenIdProviderDiscoveryMetadataSchema.parse` (unknown keys stripped)."""
    obj = _object(data)
    _require(obj, "issuer", _str)
    _require(obj, "authorization_endpoint", is_safe_url)
    _require(obj, "token_endpoint", is_safe_url)
    _require(obj, "jwks_uri", is_safe_url)
    _require(obj, "response_types_supported", _str_list)
    _require(obj, "subject_types_supported", _str_list)
    _require(obj, "id_token_signing_alg_values_supported", _str_list)
    for key in ("userinfo_endpoint", "registration_endpoint", "op_policy_uri", "op_tos_uri"):
        _require(obj, key, is_safe_url, optional=True)
    for key in ("scopes_supported", "grant_types_supported", "token_endpoint_auth_methods_supported",
                "code_challenge_methods_supported"):
        _require(obj, key, _str_list, optional=True)
    _require(obj, "client_id_metadata_document_supported", _bool, optional=True)
    return {k: v for k, v in obj.items() if k in _OPENID_KEYS}


_OPENID_KEYS = frozenset({
    "issuer", "authorization_endpoint", "token_endpoint", "userinfo_endpoint", "jwks_uri",
    "registration_endpoint", "scopes_supported", "response_types_supported", "response_modes_supported",
    "grant_types_supported", "acr_values_supported", "subject_types_supported",
    "id_token_signing_alg_values_supported", "id_token_encryption_alg_values_supported",
    "id_token_encryption_enc_values_supported", "userinfo_signing_alg_values_supported",
    "userinfo_encryption_alg_values_supported", "userinfo_encryption_enc_values_supported",
    "request_object_signing_alg_values_supported", "request_object_encryption_alg_values_supported",
    "request_object_encryption_enc_values_supported", "token_endpoint_auth_methods_supported",
    "token_endpoint_auth_signing_alg_values_supported", "display_values_supported", "claim_types_supported",
    "claims_supported", "service_documentation", "claims_locales_supported", "ui_locales_supported",
    "claims_parameter_supported", "request_parameter_supported", "request_uri_parameter_supported",
    "require_request_uri_registration", "op_policy_uri", "op_tos_uri", "client_id_metadata_document_supported",
    "code_challenge_methods_supported",
})


def parse_protected_resource_metadata(data: Any) -> dict[str, Any]:
    """`OAuthProtectedResourceMetadataSchema.parse` (a loose object)."""
    obj = _object(data)
    _require(obj, "resource", is_url)
    _require(obj, "authorization_servers",
             lambda v: isinstance(v, list) and all(is_safe_url(s) for s in v), optional=True)
    _require(obj, "scopes_supported", _str_list, optional=True)
    return dict(obj)


def _coerce_number(value: Any) -> float:
    """`z.coerce.number()`."""
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).strip() or 0)
    except ValueError:
        raise SchemaError("expires_in: Invalid") from None


_TOKEN_KEYS = ("access_token", "id_token", "token_type", "expires_in", "scope", "refresh_token")


def parse_oauth_tokens(data: Any) -> dict[str, Any]:
    """`OAuthTokensSchema.parse` (unknown keys stripped; `expires_in` coerced)."""
    obj = _object(data)
    _require(obj, "access_token", _str)
    _require(obj, "token_type", _str)
    for key in ("id_token", "scope", "refresh_token"):
        _require(obj, key, _str, optional=True)
    out = {k: obj[k] for k in _TOKEN_KEYS if k in obj and obj[k] is not None}
    if "expires_in" in out:
        out["expires_in"] = _coerce_number(out["expires_in"])
    return out


def is_oauth_tokens(data: Any) -> bool:
    """`OAuthTokensSchema.safeParse(...).success`."""
    try:
        parse_oauth_tokens(data)
    except SchemaError:
        return False
    return True


def parse_oauth_error_response(data: Any) -> dict[str, Any]:
    """`OAuthErrorResponseSchema.parse`."""
    obj = _object(data)
    _require(obj, "error", _str)
    _require(obj, "error_description", _str, optional=True)
    _require(obj, "error_uri", _str, optional=True)
    return {k: obj[k] for k in ("error", "error_description", "error_uri") if obj.get(k) is not None}


_CLIENT_INFORMATION_FULL_KEYS = (
    "redirect_uris", "token_endpoint_auth_method", "grant_types", "response_types", "client_name", "client_uri",
    "logo_uri", "scope", "contacts", "tos_uri", "policy_uri", "jwks_uri", "jwks", "software_id",
    "software_version", "software_statement", "client_id", "client_secret", "client_id_issued_at",
    "client_secret_expires_at",
)


def parse_client_information_full(data: Any) -> dict[str, Any]:
    """`OAuthClientInformationFullSchema.parse` (unknown keys stripped)."""
    obj = _object(data)
    _require(obj, "client_id", _str)
    _require(obj, "redirect_uris", lambda v: isinstance(v, list) and all(is_safe_url(s) for s in v))
    _require(obj, "client_secret", _str, optional=True)
    for key in ("client_id_issued_at", "client_secret_expires_at"):
        _require(obj, key, lambda v: isinstance(v, (int, float)) and not isinstance(v, bool), optional=True)
    return {k: obj[k] for k in _CLIENT_INFORMATION_FULL_KEYS if k in obj}


# ── the provider interface and the fetch seam ─────────────────────────────────


class FetchLike(Protocol):
    """`FetchLike`: one HTTP exchange, its body already read."""

    def __call__(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        content: bytes | None = None,
    ) -> Awaitable[httpx2.Response]: ...


async def default_fetch(
    url: str,
    *,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    content: bytes | None = None,
) -> httpx2.Response:
    """`fetchWithinOrigin(fetch)`: a redirect is followed only within the request's origin."""
    async with httpx2.AsyncClient() as client:
        return await request_within_origin(client, method, url, headers=dict(headers or {}), content=content)


class OAuthClientProvider(Protocol):
    """`OAuthClientProvider`: the members `auth()` uses and `ClaudeAuthProvider` implements."""

    @property
    def redirect_url(self) -> str | None: ...

    @property
    def client_metadata(self) -> dict[str, Any]: ...

    @property
    def client_metadata_url(self) -> str | None: ...

    async def state(self) -> str: ...

    async def client_information(self) -> dict[str, Any] | None: ...

    async def save_client_information(self, client_information: dict[str, Any]) -> None: ...

    async def tokens(self) -> dict[str, Any] | None: ...

    async def save_tokens(self, tokens: dict[str, Any]) -> None: ...

    async def redirect_to_authorization(self, authorization_url: str) -> None: ...

    async def save_code_verifier(self, code_verifier: str) -> None: ...

    async def code_verifier(self) -> str: ...

    async def invalidate_credentials(self, scope: InvalidateScope) -> None: ...

    async def save_discovery_state(self, state: dict[str, Any]) -> None: ...

    async def discovery_state(self) -> dict[str, Any] | None: ...


# ── errors from responses ─────────────────────────────────────────────────────


async def parse_error_response(response: httpx2.Response | str) -> OAuthError:
    """`parseErrorResponse`: the matching `OAuthError` subclass, else a `ServerError`."""
    status = response.status_code if isinstance(response, httpx2.Response) else None
    body = response.text if isinstance(response, httpx2.Response) else response
    try:
        result = parse_oauth_error_response(json.loads(body))
    except (ValueError, SchemaError) as exc:
        prefix = f"HTTP {status}: " if status else ""
        return ServerError(f"{prefix}Invalid OAuth error response: {exc}. Raw body: {body}")
    error_class = OAUTH_ERRORS.get(result["error"], ServerError)
    return error_class(result.get("error_description") or "", result.get("error_uri"))


def extract_field_from_www_auth(response: httpx2.Response, field_name: str) -> str | None:
    """`extractFieldFromWwwAuth`: `field="value"` or `field=value`."""
    header = response.headers.get("WWW-Authenticate")
    if not header:
        return None
    match = re.search(rf"{field_name}=(?:\"([^\"]+)\"|([^\s,]+))", header)
    if match:
        return match.group(1) or match.group(2)
    return None


@dataclass(frozen=True)
class WWWAuthenticateParams:
    """`extractWWWAuthenticateParams`'s result."""

    resource_metadata_url: str | None = None
    scope: str | None = None
    error: str | None = None


def extract_www_authenticate_params(response: httpx2.Response) -> WWWAuthenticateParams:
    """`extractWWWAuthenticateParams`: only for a `Bearer <params>` challenge."""
    header = response.headers.get("WWW-Authenticate")
    if not header:
        return WWWAuthenticateParams()
    words = header.split(" ")
    if words[0].lower() != "bearer" or len(words) < 2 or not words[1]:
        return WWWAuthenticateParams()
    raw_url = extract_field_from_www_auth(response, "resource_metadata")
    return WWWAuthenticateParams(
        resource_metadata_url=raw_url if raw_url and is_url(raw_url) else None,
        scope=extract_field_from_www_auth(response, "scope") or None,
        error=extract_field_from_www_auth(response, "error") or None,
    )


# ── discovery ─────────────────────────────────────────────────────────────────


async def fetch_with_cors_retry(
    url: str, headers: Mapping[str, str] | None, fetch_fn: FetchLike
) -> httpx2.Response | None:
    """`fetchWithCorsRetry`: a network failure retries once without headers, then answers `None`."""
    try:
        return await fetch_fn(url, headers=headers)
    except httpx2.TimeoutException:
        raise
    except httpx2.TransportError:
        if headers:
            return await fetch_with_cors_retry(url, None, fetch_fn)
        return None


def build_well_known_path(prefix: str, pathname: str = "", *, prepend_pathname: bool = False) -> str:
    """`buildWellKnownPath`."""
    pathname = pathname.removesuffix("/")
    return f"{pathname}/.well-known/{prefix}" if prepend_pathname else f"/.well-known/{prefix}{pathname}"


def _should_attempt_fallback(response: httpx2.Response | None, pathname: str) -> bool:
    """`shouldAttemptFallback`."""
    return response is None or (not response.is_success and response.status_code < 500 and pathname != "/")


async def discover_metadata_with_fallback(
    server_url: str,
    well_known_type: str,
    fetch_fn: FetchLike,
    *,
    protocol_version: str | None = None,
    metadata_url: str | None = None,
    metadata_server_url: str | None = None,
) -> httpx2.Response | None:
    """`discoverMetadataWithFallback`: path-aware first, then the root well-known URL."""
    issuer = urlsplit(server_url)
    pathname = issuer.path or "/"
    headers = {"MCP-Protocol-Version": protocol_version or LATEST_PROTOCOL_VERSION}
    if metadata_url:
        url = metadata_url
    else:
        base = metadata_server_url or server_url
        url = urljoin(base, build_well_known_path(well_known_type, pathname))
        url = urlunsplit(urlsplit(url)._replace(query=issuer.query))
    response = await fetch_with_cors_retry(url, headers, fetch_fn)
    if not metadata_url and _should_attempt_fallback(response, pathname):
        response = await fetch_with_cors_retry(urljoin(server_url, f"/.well-known/{well_known_type}"), headers,
                                               fetch_fn)
    return response


def _json(response: httpx2.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise SchemaError(f"Invalid JSON: {exc}") from exc


async def discover_oauth_protected_resource_metadata(
    server_url: str, *, resource_metadata_url: str | None = None, fetch_fn: FetchLike = default_fetch
) -> dict[str, Any]:
    """`discoverOAuthProtectedResourceMetadata` (RFC 9728)."""
    response = await discover_metadata_with_fallback(
        server_url, "oauth-protected-resource", fetch_fn, metadata_url=resource_metadata_url
    )
    if response is None or response.status_code == 404:
        raise ValueError("Resource server does not implement OAuth 2.0 Protected Resource Metadata.")
    if not response.is_success:
        raise ValueError(f"HTTP {response.status_code} trying to load well-known OAuth protected resource metadata.")
    return parse_protected_resource_metadata(_json(response))


def build_discovery_urls(authorization_server_url: str) -> list[tuple[str, Literal["oauth", "oidc"]]]:
    """`buildDiscoveryUrls`: RFC 8414 first, then OpenID Connect Discovery."""
    origin = url_origin(authorization_server_url)
    pathname = url_pathname(authorization_server_url)
    if pathname == "/":
        return [
            (f"{origin}/.well-known/oauth-authorization-server", "oauth"),
            (f"{origin}/.well-known/openid-configuration", "oidc"),
        ]
    pathname = pathname.removesuffix("/")
    return [
        (f"{origin}/.well-known/oauth-authorization-server{pathname}", "oauth"),
        (f"{origin}/.well-known/openid-configuration{pathname}", "oidc"),
        (f"{origin}{pathname}/.well-known/openid-configuration", "oidc"),
    ]


async def discover_authorization_server_metadata(
    authorization_server_url: str, *, fetch_fn: FetchLike = default_fetch, protocol_version: str | None = None
) -> dict[str, Any] | None:
    """`discoverAuthorizationServerMetadata`: the first 2xx answer; a 4xx tries the next URL."""
    headers = {"MCP-Protocol-Version": protocol_version or LATEST_PROTOCOL_VERSION, "Accept": "application/json"}
    for endpoint_url, kind in build_discovery_urls(authorization_server_url):
        response = await fetch_with_cors_retry(endpoint_url, headers, fetch_fn)
        if response is None:
            continue
        if not response.is_success:
            if response.status_code < 500:
                continue
            label = "OAuth" if kind == "oauth" else "OpenID provider"
            raise ValueError(f"HTTP {response.status_code} trying to load {label} metadata from {endpoint_url}")
        if kind == "oauth":
            return parse_oauth_metadata(_json(response))
        return parse_openid_provider_discovery_metadata(_json(response))
    return None


@dataclass(frozen=True)
class OAuthServerInfo:
    """`discoverOAuthServerInfo`'s result."""

    authorization_server_url: str
    authorization_server_metadata: dict[str, Any] | None
    resource_metadata: dict[str, Any] | None


async def discover_oauth_server_info(
    server_url: str, *, resource_metadata_url: str | None = None, fetch_fn: FetchLike = default_fetch
) -> OAuthServerInfo:
    """`discoverOAuthServerInfo`: RFC 9728 to find the AS, falling back to the server's origin."""
    resource_metadata: dict[str, Any] | None = None
    authorization_server_url: str | None = None
    try:
        resource_metadata = await discover_oauth_protected_resource_metadata(
            server_url, resource_metadata_url=resource_metadata_url, fetch_fn=fetch_fn
        )
        servers = resource_metadata.get("authorization_servers")
        if servers:
            authorization_server_url = servers[0]
    except (ValueError, httpx2.HTTPError):
        pass
    if not authorization_server_url:
        authorization_server_url = urljoin(server_url, "/")
    metadata = await discover_authorization_server_metadata(authorization_server_url, fetch_fn=fetch_fn)
    return OAuthServerInfo(authorization_server_url, metadata, resource_metadata)


# ── authorization, token and registration requests ────────────────────────────


def pkce_challenge() -> tuple[str, str]:
    """`pkce-challenge`: a 43-char verifier from the unreserved set, and its S256 challenge."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    verifier = "".join(secrets.choice(alphabet) for _ in range(43))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


@dataclass(frozen=True)
class AuthorizationStart:
    """`startAuthorization`'s `{ authorizationUrl, codeVerifier }`."""

    authorization_url: str
    code_verifier: str


async def start_authorization(
    authorization_server_url: str,
    *,
    metadata: Mapping[str, Any] | None,
    client_information: Mapping[str, Any],
    redirect_url: str,
    scope: str | None = None,
    state: str | None = None,
    resource: str | None = None,
) -> AuthorizationStart:
    """`startAuthorization`: the PKCE challenge and the authorization URL."""
    if metadata:
        authorization_url = str(metadata["authorization_endpoint"])
        if AUTHORIZATION_CODE_RESPONSE_TYPE not in metadata.get("response_types_supported", []):
            raise ValueError(
                f"Incompatible auth server: does not support response type {AUTHORIZATION_CODE_RESPONSE_TYPE}")
        methods = metadata.get("code_challenge_methods_supported")
        if methods and AUTHORIZATION_CODE_CHALLENGE_METHOD not in methods:
            raise ValueError(
                "Incompatible auth server: does not support code challenge method "
                f"{AUTHORIZATION_CODE_CHALLENGE_METHOD}")
    else:
        authorization_url = urljoin(authorization_server_url, "/authorize")
    code_verifier, code_challenge = pkce_challenge()
    for name, value in (
        ("response_type", AUTHORIZATION_CODE_RESPONSE_TYPE),
        ("client_id", str(client_information["client_id"])),
        ("code_challenge", code_challenge),
        ("code_challenge_method", AUTHORIZATION_CODE_CHALLENGE_METHOD),
        ("redirect_uri", redirect_url),
    ):
        authorization_url = _set_query_param(authorization_url, name, value)
    if state:
        authorization_url = _set_query_param(authorization_url, "state", state)
    if scope:
        authorization_url = _set_query_param(authorization_url, "scope", scope)
    if scope and "offline_access" in scope:
        authorization_url = _set_query_param(authorization_url, "prompt", "consent", append=True)
    if resource:
        authorization_url = _set_query_param(authorization_url, "resource", resource)
    return AuthorizationStart(authorization_url, code_verifier)


def select_client_auth_method(client_information: Mapping[str, Any], supported_methods: list[str]) -> str:
    """`selectClientAuthMethod`."""
    has_secret = client_information.get("client_secret") is not None
    registered = client_information.get("token_endpoint_auth_method")
    if registered in ("client_secret_basic", "client_secret_post", "none") and (
        not supported_methods or registered in supported_methods
    ):
        return str(registered)
    if not supported_methods:
        return "client_secret_basic" if has_secret else "none"
    if has_secret and "client_secret_basic" in supported_methods:
        return "client_secret_basic"
    if has_secret and "client_secret_post" in supported_methods:
        return "client_secret_post"
    if "none" in supported_methods:
        return "none"
    return "client_secret_post" if has_secret else "none"


def apply_client_authentication(
    method: str, client_information: Mapping[str, Any], headers: dict[str, str], params: dict[str, str]
) -> None:
    """`applyClientAuthentication` (`applyBasicAuth` / `applyPostAuth` / `applyPublicAuth`)."""
    client_id = str(client_information["client_id"])
    client_secret = client_information.get("client_secret")
    if method == "client_secret_basic":
        if not client_secret:
            raise ValueError("client_secret_basic authentication requires a client_secret")
        credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        headers["Authorization"] = f"Basic {credentials}"
    elif method == "client_secret_post":
        params["client_id"] = client_id
        if client_secret:
            params["client_secret"] = str(client_secret)
    elif method == "none":
        params["client_id"] = client_id
    else:
        raise ValueError(f"Unsupported client authentication method: {method}")


async def execute_token_request(
    authorization_server_url: str,
    *,
    metadata: Mapping[str, Any] | None,
    token_request_params: dict[str, str],
    client_information: Mapping[str, Any] | None,
    resource: str | None,
    fetch_fn: FetchLike,
) -> dict[str, Any]:
    """`executeTokenRequest`: POST the grant; a non-2xx answer raises the parsed `OAuthError`."""
    token_url = str(metadata["token_endpoint"]) if metadata and metadata.get("token_endpoint") else urljoin(
        authorization_server_url, "/token")
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    if resource:
        token_request_params["resource"] = resource
    if client_information:
        supported = list((metadata or {}).get("token_endpoint_auth_methods_supported") or [])
        method = select_client_auth_method(client_information, supported)
        apply_client_authentication(method, client_information, headers, token_request_params)
    response = await fetch_fn(token_url, method="POST", headers=headers,
                              content=urlencode(token_request_params).encode())
    if not response.is_success:
        raise await parse_error_response(response)
    return parse_oauth_tokens(_json(response))


def prepare_authorization_code_request(
    authorization_code: str, code_verifier: str, redirect_uri: str
) -> dict[str, str]:
    """`prepareAuthorizationCodeRequest`."""
    return {
        "grant_type": "authorization_code",
        "code": authorization_code,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
    }


async def refresh_authorization(
    authorization_server_url: str,
    *,
    metadata: Mapping[str, Any] | None,
    client_information: Mapping[str, Any],
    refresh_token: str,
    resource: str | None = None,
    fetch_fn: FetchLike = default_fetch,
) -> dict[str, Any]:
    """`refreshAuthorization`: the original refresh token is kept when none is returned."""
    tokens = await execute_token_request(
        authorization_server_url,
        metadata=metadata,
        token_request_params={"grant_type": "refresh_token", "refresh_token": refresh_token},
        client_information=client_information,
        resource=resource,
        fetch_fn=fetch_fn,
    )
    return {"refresh_token": refresh_token, **tokens}


async def fetch_token(
    provider: OAuthClientProvider,
    authorization_server_url: str,
    *,
    metadata: Mapping[str, Any] | None,
    resource: str | None,
    authorization_code: str | None,
    fetch_fn: FetchLike,
) -> dict[str, Any]:
    """`fetchToken` for the authorization-code grant (the provider has no `prepareTokenRequest`)."""
    client_information = await provider.client_information()
    if not authorization_code:
        raise ValueError("Either provider.prepareTokenRequest() or authorizationCode is required")
    redirect_url = provider.redirect_url
    if not redirect_url:
        raise ValueError("redirectUrl is required for authorization_code flow")
    code_verifier = await provider.code_verifier()
    return await execute_token_request(
        authorization_server_url,
        metadata=metadata,
        token_request_params=prepare_authorization_code_request(authorization_code, code_verifier, redirect_url),
        client_information=client_information,
        resource=resource,
        fetch_fn=fetch_fn,
    )


async def register_client(
    authorization_server_url: str,
    *,
    metadata: Mapping[str, Any] | None,
    client_metadata: Mapping[str, Any],
    scope: str | None = None,
    fetch_fn: FetchLike = default_fetch,
) -> dict[str, Any]:
    """`registerClient` (RFC 7591); `scope` overrides the metadata's (SEP-835)."""
    if metadata:
        if not metadata.get("registration_endpoint"):
            raise ValueError("Incompatible auth server: does not support dynamic client registration")
        registration_url = str(metadata["registration_endpoint"])
    else:
        registration_url = urljoin(authorization_server_url, "/register")
    body = {**client_metadata, **({"scope": scope} if scope is not None else {})}
    response = await fetch_fn(registration_url, method="POST", headers={"Content-Type": "application/json"},
                              content=json.dumps(body).encode())
    if not response.is_success:
        raise await parse_error_response(response)
    return parse_client_information_full(_json(response))


async def select_resource_url(server_url: str, resource_metadata: Mapping[str, Any] | None) -> str | None:
    """`selectResourceURL` (no `validateResourceURL` hook): the PRM `resource`, checked."""
    default_resource = resource_url_from_server_url(server_url)
    if not resource_metadata:
        return None
    configured = str(resource_metadata["resource"])
    if not check_resource_allowed(default_resource, configured):
        raise ValueError(f"Protected resource {configured} does not match expected {default_resource} (or origin)")
    return configured


# ── the driver ────────────────────────────────────────────────────────────────


async def auth(
    provider: OAuthClientProvider,
    *,
    server_url: str,
    authorization_code: str | None = None,
    scope: str | None = None,
    resource_metadata_url: str | None = None,
    fetch_fn: FetchLike = default_fetch,
) -> AuthResult:
    """`auth`: one retry after invalidating the client (`invalid_client`) or the tokens (`invalid_grant`)."""
    kwargs: dict[str, Any] = {
        "server_url": server_url, "authorization_code": authorization_code, "scope": scope,
        "resource_metadata_url": resource_metadata_url, "fetch_fn": fetch_fn,
    }
    try:
        return await _auth_internal(provider, **kwargs)
    except (InvalidClientError, UnauthorizedClientError):
        await provider.invalidate_credentials("all")
        return await _auth_internal(provider, **kwargs)
    except InvalidGrantError:
        await provider.invalidate_credentials("tokens")
        return await _auth_internal(provider, **kwargs)


async def _auth_internal(
    provider: OAuthClientProvider,
    *,
    server_url: str,
    authorization_code: str | None,
    scope: str | None,
    resource_metadata_url: str | None,
    fetch_fn: FetchLike,
) -> AuthResult:
    """`authInternal`."""
    cached = await provider.discovery_state()
    effective_resource_metadata_url = resource_metadata_url
    if not effective_resource_metadata_url and cached and cached.get("resourceMetadataUrl"):
        effective_resource_metadata_url = str(cached["resourceMetadataUrl"])

    resource_metadata: dict[str, Any] | None
    metadata: dict[str, Any] | None
    if cached and cached.get("authorizationServerUrl"):
        authorization_server_url = str(cached["authorizationServerUrl"])
        resource_metadata = cached.get("resourceMetadata")
        metadata = cached.get("authorizationServerMetadata") or await discover_authorization_server_metadata(
            authorization_server_url, fetch_fn=fetch_fn)
        if not resource_metadata:
            try:
                resource_metadata = await discover_oauth_protected_resource_metadata(
                    server_url, resource_metadata_url=effective_resource_metadata_url, fetch_fn=fetch_fn)
            except (ValueError, httpx2.HTTPError):
                pass
        if metadata is not cached.get("authorizationServerMetadata") or resource_metadata is not cached.get(
                "resourceMetadata"):
            await provider.save_discovery_state({
                "authorizationServerUrl": authorization_server_url,
                "resourceMetadataUrl": effective_resource_metadata_url,
                "resourceMetadata": resource_metadata,
                "authorizationServerMetadata": metadata,
            })
    else:
        info = await discover_oauth_server_info(
            server_url, resource_metadata_url=effective_resource_metadata_url, fetch_fn=fetch_fn)
        authorization_server_url = info.authorization_server_url
        metadata = info.authorization_server_metadata
        resource_metadata = info.resource_metadata
        await provider.save_discovery_state({
            "authorizationServerUrl": authorization_server_url,
            "resourceMetadataUrl": effective_resource_metadata_url,
            "resourceMetadata": resource_metadata,
            "authorizationServerMetadata": metadata,
        })

    resource = await select_resource_url(server_url, resource_metadata)
    resolved_scope = scope or " ".join((resource_metadata or {}).get("scopes_supported") or []) or \
        provider.client_metadata.get("scope")

    client_information = await provider.client_information()
    if not client_information:
        if authorization_code is not None:
            raise ValueError("Existing OAuth client information is required when exchanging an authorization code")
        supports_url_based_client_id = bool(metadata and metadata.get("client_id_metadata_document_supported") is True)
        client_metadata_url = provider.client_metadata_url
        if client_metadata_url and not is_https_url(client_metadata_url):
            raise InvalidClientMetadataError(
                f"clientMetadataUrl must be a valid HTTPS URL with a non-root pathname, got: {client_metadata_url}")
        if supports_url_based_client_id and client_metadata_url:
            client_information = {"client_id": client_metadata_url}
            await provider.save_client_information(client_information)
        else:
            client_information = await register_client(
                authorization_server_url, metadata=metadata, client_metadata=provider.client_metadata,
                scope=resolved_scope, fetch_fn=fetch_fn)
            await provider.save_client_information(client_information)

    if authorization_code is not None or not provider.redirect_url:
        tokens = await fetch_token(provider, authorization_server_url, metadata=metadata, resource=resource,
                                   authorization_code=authorization_code, fetch_fn=fetch_fn)
        await provider.save_tokens(tokens)
        return "AUTHORIZED"

    current = await provider.tokens()
    if current and current.get("refresh_token"):
        try:
            new_tokens = await refresh_authorization(
                authorization_server_url, metadata=metadata, client_information=client_information,
                refresh_token=str(current["refresh_token"]), resource=resource, fetch_fn=fetch_fn)
            await provider.save_tokens(new_tokens)
            return "AUTHORIZED"
        except Exception as exc:
            # upstream: a ServerError or a non-OAuth error falls through to a new authorization
            if isinstance(exc, OAuthError) and not isinstance(exc, ServerError):
                raise

    state = await provider.state()
    start = await start_authorization(
        authorization_server_url, metadata=metadata, client_information=client_information, state=state,
        redirect_url=str(provider.redirect_url), scope=resolved_scope, resource=resource)
    await provider.save_code_verifier(start.code_verifier)
    await provider.redirect_to_authorization(start.authorization_url)
    return "REDIRECT"


__all__ = [
    "AUTHORIZATION_CODE_CHALLENGE_METHOD",
    "AUTHORIZATION_CODE_RESPONSE_TYPE",
    "OAUTH_ERRORS",
    "AccessDeniedError",
    "AuthResult",
    "AuthorizationStart",
    "FetchLike",
    "InsufficientScopeError",
    "InvalidClientError",
    "InvalidClientMetadataError",
    "InvalidGrantError",
    "InvalidRequestError",
    "InvalidScopeError",
    "InvalidTargetError",
    "InvalidTokenError",
    "InvalidateScope",
    "MethodNotAllowedError",
    "OAuthClientProvider",
    "OAuthError",
    "OAuthServerInfo",
    "SchemaError",
    "ServerError",
    "TemporarilyUnavailableError",
    "TooManyRequestsError",
    "UnauthorizedClientError",
    "UnauthorizedError",
    "UnsupportedGrantTypeError",
    "UnsupportedResponseTypeError",
    "UnsupportedTokenTypeError",
    "WWWAuthenticateParams",
    "apply_client_authentication",
    "auth",
    "build_discovery_urls",
    "build_well_known_path",
    "check_resource_allowed",
    "default_fetch",
    "discover_authorization_server_metadata",
    "discover_metadata_with_fallback",
    "discover_oauth_protected_resource_metadata",
    "discover_oauth_server_info",
    "execute_token_request",
    "extract_field_from_www_auth",
    "extract_www_authenticate_params",
    "fetch_token",
    "fetch_with_cors_retry",
    "is_https_url",
    "is_oauth_tokens",
    "is_safe_url",
    "is_url",
    "parse_client_information_full",
    "parse_error_response",
    "parse_oauth_error_response",
    "parse_oauth_metadata",
    "parse_oauth_tokens",
    "parse_openid_provider_discovery_metadata",
    "parse_protected_resource_metadata",
    "pkce_challenge",
    "prepare_authorization_code_request",
    "refresh_authorization",
    "register_client",
    "resource_url_from_server_url",
    "select_client_auth_method",
    "select_resource_url",
    "start_authorization",
    "url_href",
    "url_origin",
    "url_pathname",
]
