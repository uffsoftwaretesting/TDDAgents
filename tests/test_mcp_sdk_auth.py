"""`app/services/mcp/sdk_auth.py`: the TypeScript SDK's `auth()` and its helpers, against a loopback AS."""

from __future__ import annotations

import asyncio
import base64
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from mcp.types import LATEST_PROTOCOL_VERSION

from app.services.mcp import sdk_auth as sa
from tests.oauth_doubles import (
    Reply,
    as_metadata,
    fake_http_server,
    install_authorization_server,
)


class MemoryProvider:
    """An `OAuthClientProvider` keeping everything in memory."""

    def __init__(self, redirect: str | None = "http://localhost:1/callback", **client: Any) -> None:
        self._redirect = redirect
        self.client: dict[str, Any] | None = client or None
        self.saved_tokens: dict[str, Any] | None = None
        self.discovery: dict[str, Any] | None = None
        self.verifier: str | None = None
        self.redirected_to: str | None = None
        self.invalidated: list[str] = []
        self.metadata_url: str | None = None

    @property
    def redirect_url(self) -> str | None:
        return self._redirect

    @property
    def client_metadata(self) -> dict[str, Any]:
        return {"client_name": "t", "redirect_uris": [self._redirect or "http://localhost:1/callback"],
                "scope": "fallback"}

    @property
    def client_metadata_url(self) -> str | None:
        return self.metadata_url

    async def state(self) -> str:
        return "st"

    async def client_information(self) -> dict[str, Any] | None:
        return self.client

    async def save_client_information(self, info: dict[str, Any]) -> None:
        self.client = info

    async def tokens(self) -> dict[str, Any] | None:
        return self.saved_tokens

    async def save_tokens(self, tokens: dict[str, Any]) -> None:
        self.saved_tokens = tokens

    async def redirect_to_authorization(self, url: str) -> None:
        self.redirected_to = url

    async def save_code_verifier(self, verifier: str) -> None:
        self.verifier = verifier

    async def code_verifier(self) -> str:
        assert self.verifier
        return self.verifier

    async def invalidate_credentials(self, scope: Any) -> None:
        self.invalidated.append(scope)
        if scope == "all":
            self.client = None
        if scope in ("all", "tokens"):
            self.saved_tokens = None

    async def save_discovery_state(self, state: dict[str, Any]) -> None:
        self.discovery = state

    async def discovery_state(self) -> dict[str, Any] | None:
        return self.discovery


def query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def test_a_first_auth_discovers_registers_and_redirects():
    with fake_http_server() as srv:
        install_authorization_server(srv, scopes=["read", "write"])
        provider = MemoryProvider()
        result = asyncio.run(sa.auth(provider, server_url=f"{srv.base}/mcp"))
        assert result == "REDIRECT"
        assert provider.discovery == {
            "authorizationServerUrl": srv.base, "resourceMetadataUrl": None,
            "resourceMetadata": {"resource": f"{srv.base}/mcp", "authorization_servers": [srv.base],
                                 "scopes_supported": ["read", "write"]},
            "authorizationServerMetadata": as_metadata(srv.base),
        }
        registration = srv.requests("POST", "/register")[0].json
        assert registration["scope"] == "read write" and registration["client_name"] == "t"
        assert provider.client and provider.client["client_id"] == "client-1"
        url = provider.redirected_to
        assert url and url.startswith(f"{srv.base}/authorize?")
        params = query(url)
        assert params["client_id"] == "client-1" and params["state"] == "st"
        assert params["redirect_uri"] == "http://localhost:1/callback"
        assert params["scope"] == "read write" and params["resource"] == f"{srv.base}/mcp"
        assert params["code_challenge_method"] == "S256" and params["response_type"] == "code"
        verifier = provider.verifier or ""
        assert len(verifier) == 43
        expected = base64.urlsafe_b64encode(__import__("hashlib").sha256(verifier.encode()).digest()).decode()
        assert params["code_challenge"] == expected.rstrip("=")
        protocol = srv.requests("GET", "/.well-known/oauth-protected-resource/mcp")[0].headers
        assert protocol["MCP-Protocol-Version"] == LATEST_PROTOCOL_VERSION


def test_an_authorization_code_is_exchanged_for_tokens():
    with fake_http_server() as srv:
        install_authorization_server(srv)
        provider = MemoryProvider(client_id="cid")
        provider.verifier = "v" * 43
        result = asyncio.run(sa.auth(provider, server_url=f"{srv.base}/mcp", authorization_code="the-code"))
        assert result == "AUTHORIZED"
        assert provider.saved_tokens and provider.saved_tokens["access_token"] == "tok-1"
        form = srv.requests("POST", "/token")[0].form
        assert form == {"grant_type": "authorization_code", "code": "the-code", "code_verifier": "v" * 43,
                        "redirect_uri": "http://localhost:1/callback", "client_id": "cid",
                        "resource": f"{srv.base}/mcp"}


def test_code_exchange_without_client_information_is_refused():
    with fake_http_server() as srv:
        install_authorization_server(srv)
        with pytest.raises(ValueError, match="Existing OAuth client information is required"):
            asyncio.run(sa.auth(MemoryProvider(), server_url=f"{srv.base}/mcp", authorization_code="c"))


def test_a_refresh_token_is_used_before_redirecting():
    with fake_http_server() as srv:
        install_authorization_server(srv)
        provider = MemoryProvider(client_id="cid", client_secret="sec")
        provider.saved_tokens = {"access_token": "old", "refresh_token": "r0", "token_type": "Bearer"}
        assert asyncio.run(sa.auth(provider, server_url=f"{srv.base}/mcp")) == "AUTHORIZED"
        request = srv.requests("POST", "/token")[0]
        assert request.form["grant_type"] == "refresh_token" and request.form["refresh_token"] == "r0"
        assert request.headers["Authorization"] == "Basic " + base64.b64encode(b"cid:sec").decode()
        assert "client_id" not in request.form
        assert provider.redirected_to is None


def test_a_server_error_on_refresh_falls_through_to_the_redirect():
    with fake_http_server() as srv:
        install_authorization_server(srv)
        srv.route("POST", "/token", Reply(500, {"error": "server_error"}))
        provider = MemoryProvider(client_id="cid")
        provider.saved_tokens = {"access_token": "old", "refresh_token": "r0", "token_type": "Bearer"}
        assert asyncio.run(sa.auth(provider, server_url=f"{srv.base}/mcp")) == "REDIRECT"


def test_invalid_grant_invalidates_the_tokens_and_retries_once():
    with fake_http_server() as srv:
        install_authorization_server(srv)
        srv.route("POST", "/token", Reply(400, {"error": "invalid_grant", "error_description": "gone"}))
        provider = MemoryProvider(client_id="cid")
        provider.saved_tokens = {"access_token": "old", "refresh_token": "r0", "token_type": "Bearer"}
        assert asyncio.run(sa.auth(provider, server_url=f"{srv.base}/mcp")) == "REDIRECT"
        assert provider.invalidated == ["tokens"]


def test_invalid_client_invalidates_everything_and_re_registers():
    with fake_http_server() as srv:
        install_authorization_server(srv)
        srv.route("POST", "/token", Reply(401, {"error": "invalid_client"}))
        provider = MemoryProvider(client_id="stale")
        provider.saved_tokens = {"access_token": "old", "refresh_token": "r0", "token_type": "Bearer"}
        assert asyncio.run(sa.auth(provider, server_url=f"{srv.base}/mcp")) == "REDIRECT"
        assert provider.invalidated == ["all"]
        assert provider.client and provider.client["client_id"] == "client-1"


def test_a_cached_discovery_state_skips_the_protected_resource_probe_for_the_as():
    with fake_http_server() as srv:
        install_authorization_server(srv)
        provider = MemoryProvider(client_id="cid")
        provider.discovery = {"authorizationServerUrl": srv.base}
        assert asyncio.run(sa.auth(provider, server_url=f"{srv.base}/mcp")) == "REDIRECT"
        assert srv.requests("GET", "/.well-known/oauth-authorization-server")
        assert provider.discovery["authorizationServerMetadata"] == as_metadata(srv.base)


def test_cimd_client_id_when_the_server_supports_it():
    with fake_http_server() as srv:
        install_authorization_server(srv, client_id_metadata_document_supported=True)
        provider = MemoryProvider()
        provider.metadata_url = "https://example.com/client.json"
        assert asyncio.run(sa.auth(provider, server_url=f"{srv.base}/mcp")) == "REDIRECT"
        assert provider.client == {"client_id": "https://example.com/client.json"}
        assert not srv.requests("POST", "/register")
        provider2 = MemoryProvider()
        provider2.metadata_url = "http://example.com/client.json"
        with pytest.raises(sa.InvalidClientMetadataError, match="clientMetadataUrl must be a valid HTTPS URL"):
            asyncio.run(sa.auth(provider2, server_url=f"{srv.base}/mcp"))


def test_a_resource_mismatch_is_refused():
    with fake_http_server() as srv:
        install_authorization_server(srv, resource_path="/mcp")
        srv.route("GET", "/.well-known/oauth-protected-resource/other",
                  Reply(200, {"resource": "https://elsewhere.example/mcp", "authorization_servers": [srv.base]}))
        with pytest.raises(ValueError, match="does not match expected"):
            asyncio.run(sa.auth(MemoryProvider(client_id="c"), server_url=f"{srv.base}/other"))


def test_legacy_servers_fall_back_to_the_origin_and_default_endpoints():
    with fake_http_server() as srv:
        provider = MemoryProvider(client_id="c")
        assert asyncio.run(sa.auth(provider, server_url=f"{srv.base}/mcp")) == "REDIRECT"
        assert provider.discovery and provider.discovery["authorizationServerUrl"] == f"{srv.base}/"
        assert provider.redirected_to and provider.redirected_to.startswith(f"{srv.base}/authorize?")
        assert "resource" not in query(provider.redirected_to)
        assert query(provider.redirected_to)["scope"] == "fallback"


def test_discovery_order_and_fallbacks():
    assert sa.build_discovery_urls("https://as.example") == [
        ("https://as.example/.well-known/oauth-authorization-server", "oauth"),
        ("https://as.example/.well-known/openid-configuration", "oidc"),
    ]
    assert sa.build_discovery_urls("https://as.example/tenant/") == [
        ("https://as.example/.well-known/oauth-authorization-server/tenant", "oauth"),
        ("https://as.example/.well-known/openid-configuration/tenant", "oidc"),
        ("https://as.example/tenant/.well-known/openid-configuration", "oidc"),
    ]
    assert sa.build_well_known_path("x", "/a/") == "/.well-known/x/a"
    assert sa.build_well_known_path("x", "/a", prepend_pathname=True) == "/a/.well-known/x"


def test_openid_metadata_and_server_errors():
    oidc = {**as_metadata("https://i"), "jwks_uri": "https://i/jwks", "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"], "extra": 1}
    with fake_http_server() as srv:
        srv.route("GET", "/t/.well-known/openid-configuration", Reply(200, oidc))
        found = asyncio.run(sa.discover_authorization_server_metadata(f"{srv.base}/t"))
        assert found and found["jwks_uri"] == "https://i/jwks" and "extra" not in found
        srv.route("GET", "/.well-known/oauth-authorization-server", Reply(503, {}))
        with pytest.raises(ValueError, match="HTTP 503 trying to load OAuth metadata from"):
            asyncio.run(sa.discover_authorization_server_metadata(srv.base))
        assert asyncio.run(sa.discover_authorization_server_metadata(f"{srv.base}/none")) is None


def test_protected_resource_metadata_errors():
    with fake_http_server() as srv:
        with pytest.raises(ValueError, match="does not implement OAuth 2.0 Protected Resource Metadata"):
            asyncio.run(sa.discover_oauth_protected_resource_metadata(f"{srv.base}/mcp"))
        srv.route("GET", "/prm", Reply(500, {}))
        with pytest.raises(ValueError, match="HTTP 500 trying to load"):
            asyncio.run(sa.discover_oauth_protected_resource_metadata(
                f"{srv.base}/mcp", resource_metadata_url=f"{srv.base}/prm"))


def test_network_failures_retry_without_headers_then_answer_none():
    calls: list[Any] = []

    async def failing(url: str, **kwargs: Any) -> httpx2.Response:
        calls.append(kwargs.get("headers"))
        raise httpx2.ConnectError("refused")

    assert asyncio.run(sa.fetch_with_cors_retry("http://x", {"A": "1"}, failing)) is None
    assert calls == [{"A": "1"}, None]

    async def timing_out(url: str, **kwargs: Any) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow")

    with pytest.raises(httpx2.ReadTimeout):
        asyncio.run(sa.fetch_with_cors_retry("http://x", {"A": "1"}, timing_out))


def test_parse_error_response_maps_codes():
    async def parse(body: str, status: int = 400) -> sa.OAuthError:
        return await sa.parse_error_response(httpx2.Response(status, content=body.encode()))

    err = asyncio.run(parse('{"error":"invalid_grant","error_description":"d","error_uri":"u"}'))
    assert isinstance(err, sa.InvalidGrantError) and str(err) == "d" and err.error_uri == "u"
    assert err.error_code == "invalid_grant"
    assert isinstance(asyncio.run(parse('{"error":"made_up"}')), sa.ServerError)
    bad = asyncio.run(parse("<html>", 502))
    assert isinstance(bad, sa.ServerError) and str(bad).startswith("HTTP 502: Invalid OAuth error response:")
    assert str(bad).endswith("Raw body: <html>")
    assert set(sa.OAUTH_ERRORS) >= {"invalid_client", "temporarily_unavailable", "too_many_requests"}


def test_www_authenticate_params():
    def resp(header: str | None) -> httpx2.Response:
        return httpx2.Response(401, headers={"WWW-Authenticate": header} if header else {})

    params = sa.extract_www_authenticate_params(resp(
        'Bearer resource_metadata="https://x/prm", scope="a b", error=insufficient_scope'))
    assert params == sa.WWWAuthenticateParams("https://x/prm", "a b", "insufficient_scope")
    assert sa.extract_www_authenticate_params(resp("Basic realm=x")) == sa.WWWAuthenticateParams()
    assert sa.extract_www_authenticate_params(resp(None)) == sa.WWWAuthenticateParams()
    assert sa.extract_www_authenticate_params(resp("Bearer")) == sa.WWWAuthenticateParams()
    assert sa.extract_www_authenticate_params(resp('Bearer resource_metadata="nope"')).resource_metadata_url is None


@pytest.mark.parametrize("info, supported, method", [
    ({"client_id": "a", "client_secret": "s", "token_endpoint_auth_method": "client_secret_post"}, [],
     "client_secret_post"),
    ({"client_id": "a", "client_secret": "s", "token_endpoint_auth_method": "client_secret_post"},
     ["client_secret_basic"], "client_secret_basic"),
    ({"client_id": "a", "client_secret": "s"}, [], "client_secret_basic"),
    ({"client_id": "a"}, [], "none"),
    ({"client_id": "a", "client_secret": "s"}, ["client_secret_post"], "client_secret_post"),
    ({"client_id": "a"}, ["none", "client_secret_basic"], "none"),
    ({"client_id": "a", "client_secret": "s"}, ["private_key_jwt"], "client_secret_post"),
    ({"client_id": "a"}, ["private_key_jwt"], "none"),
])
def test_select_client_auth_method(info, supported, method):
    assert sa.select_client_auth_method(info, supported) == method


def test_apply_client_authentication():
    headers: dict[str, str] = {}
    params: dict[str, str] = {}
    sa.apply_client_authentication("client_secret_post", {"client_id": "a", "client_secret": "s"}, headers, params)
    assert params == {"client_id": "a", "client_secret": "s"} and headers == {}
    with pytest.raises(ValueError, match="requires a client_secret"):
        sa.apply_client_authentication("client_secret_basic", {"client_id": "a"}, {}, {})
    with pytest.raises(ValueError, match="Unsupported client authentication method: x"):
        sa.apply_client_authentication("x", {"client_id": "a"}, {}, {})


def test_start_authorization_checks_the_server_and_adds_consent_for_offline_access():
    meta = as_metadata("https://as")
    start = asyncio.run(sa.start_authorization(
        "https://as", metadata=meta, client_information={"client_id": "c"}, redirect_url="http://l/cb",
        scope="openid offline_access", state="s", resource="https://r"))
    params = query(start.authorization_url)
    assert params["prompt"] == "consent" and params["scope"] == "openid offline_access"
    with pytest.raises(ValueError, match="does not support response type code"):
        asyncio.run(sa.start_authorization("https://as", metadata={**meta, "response_types_supported": ["token"]},
                                           client_information={"client_id": "c"}, redirect_url="x"))
    with pytest.raises(ValueError, match="does not support code challenge method S256"):
        asyncio.run(sa.start_authorization(
            "https://as", metadata={**meta, "code_challenge_methods_supported": ["plain"]},
            client_information={"client_id": "c"}, redirect_url="x"))


def test_registration_requires_an_endpoint_when_metadata_exists():
    with pytest.raises(ValueError, match="does not support dynamic client registration"):
        asyncio.run(sa.register_client("https://as", metadata={**as_metadata("https://as"),
                                                               "registration_endpoint": None},
                                       client_metadata={}))


def test_refresh_keeps_the_original_refresh_token_when_none_is_returned():
    with fake_http_server() as srv:
        srv.route("POST", "/token", Reply(200, {"access_token": "n", "token_type": "Bearer", "expires_in": "60"}))
        tokens = asyncio.run(sa.refresh_authorization(srv.base, metadata=None, client_information={"client_id": "c"},
                                                      refresh_token="keep"))
        assert tokens == {"refresh_token": "keep", "access_token": "n", "token_type": "Bearer", "expires_in": 60.0}


@pytest.mark.parametrize("requested, configured, allowed", [
    ("https://a.com/mcp/x", "https://a.com/mcp", True),
    ("https://a.com/mcp", "https://a.com/mcp/", False),
    ("https://a.com/mcp123", "https://a.com/mcp", False),
    ("https://b.com/mcp", "https://a.com/mcp", False),
    ("https://a.com", "https://a.com/", True),
])
def test_check_resource_allowed(requested, configured, allowed):
    assert sa.check_resource_allowed(requested, configured) is allowed


def test_schemas():
    with pytest.raises(sa.SchemaError, match="token_endpoint: Required"):
        sa.parse_oauth_metadata({"issuer": "i", "authorization_endpoint": "https://a",
                                 "response_types_supported": ["code"]})
    with pytest.raises(sa.SchemaError, match="authorization_endpoint: Invalid"):
        sa.parse_oauth_metadata({**as_metadata("https://i"), "authorization_endpoint": "javascript:alert(1)"})
    assert sa.parse_oauth_metadata({**as_metadata("https://i"), "x": 1})["x"] == 1
    assert sa.parse_oauth_tokens({"access_token": "a", "token_type": "Bearer", "junk": 1}) == {
        "access_token": "a", "token_type": "Bearer"}
    assert not sa.is_oauth_tokens({"error": "invalid_grant"})
    with pytest.raises(sa.SchemaError):
        sa.parse_client_information_full({"client_id": "c"})
    assert sa.parse_client_information_full({"client_id": "c", "redirect_uris": ["http://l/cb"], "x": 1}) == {
        "client_id": "c", "redirect_uris": ["http://l/cb"]}
    assert sa.is_https_url("https://a.com/x") and not sa.is_https_url("https://a.com/")
    assert not sa.is_https_url(None) and not sa.is_https_url("http://a.com/x")
    assert sa.url_href("https://a.com") == "https://a.com/"
    assert sa.resource_url_from_server_url("https://a.com/x#frag") == "https://a.com/x"
    assert str(sa.UnauthorizedError()) == "Unauthorized"
