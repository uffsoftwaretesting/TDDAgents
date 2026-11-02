"""`app/services/mcp/auth.py`: the provider, the browser flow, refresh, revocation, the transport bridge."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx2
import pytest

from app.services.mcp import auth as ma
from app.services.mcp.sdk_auth import InvalidClientError, UnauthorizedError
from app.services.mcp.types import McpOAuthConfig, McpServerConfig
from app.utils.abort_controller import AbortController
from app.utils.secure_storage.index import get_secure_storage
from app.utils.tool_result_storage import config_home
from tests.oauth_doubles import (
    Reply,
    as_metadata,
    fake_http_server,
    install_authorization_server,
)


def http(url: str, **oauth: Any) -> McpServerConfig:
    return McpServerConfig(type="http", scope="user", url=url, oauth=McpOAuthConfig(**oauth) if oauth else None)


def stored() -> dict[str, Any]:
    return get_secure_storage().read() or {}


def entry(name: str, config: McpServerConfig) -> dict[str, Any] | None:
    found = stored().get("mcpOAuth", {}).get(ma.get_server_key(name, config))
    return found if isinstance(found, dict) else None


def fetched(response: Awaitable[httpx2.Response]) -> httpx2.Response:
    """Run one `FetchLike` call to completion."""

    async def scenario() -> httpx2.Response:
        return await response

    return asyncio.run(scenario())


def put(name: str, config: McpServerConfig, **fields: Any) -> None:
    data = stored()
    data.setdefault("mcpOAuth", {})[ma.get_server_key(name, config)] = fields
    get_secure_storage().update(data)


def query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


# ── keys, redaction, error bodies ──


def test_the_server_key_hashes_type_url_and_headers():
    config = McpServerConfig(type="http", scope="user", url="https://x", headers={"A": "1"})
    digest = hashlib.sha256(b'{"type":"http","url":"https://x","headers":{"A":"1"}}').hexdigest()[:16]
    assert ma.get_server_key("srv", config) == f"srv|{digest}"
    plain = hashlib.sha256(b'{"type":"http","url":"https://x","headers":{}}').hexdigest()[:16]
    assert ma.get_server_key("srv", http("https://x")) == f"srv|{plain}"


def test_sensitive_params_are_redacted():
    url = "https://as/authorize?client_id=c&state=s&code_challenge=x&scope=a+b"
    redacted = ma.redact_sensitive_url_params(url)
    assert query(redacted) == {"client_id": "c", "state": "[REDACTED]", "code_challenge": "[REDACTED]",
                               "scope": "a b"}
    assert ma.redact_sensitive_url_params("not a url") == "not a url"


def test_a_200_oauth_error_body_becomes_a_400_and_slack_aliases_become_invalid_grant():
    def body(status: int, payload: Any) -> httpx2.Response:
        content = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return httpx2.Response(status, content=content, headers={"X": "y"})

    out = ma.normalize_oauth_error_body(body(200, {"ok": False, "error": "invalid_refresh_token"}))
    assert out.status_code == 400 and out.headers["X"] == "y"
    assert out.json() == {"error": "invalid_grant",
                          "error_description": "Server returned non-standard error code: invalid_refresh_token"}
    kept = ma.normalize_oauth_error_body(body(200, {"error": "invalid_scope", "error_description": "d"}))
    assert kept.status_code == 400 and kept.json() == {"error": "invalid_scope", "error_description": "d"}
    tokens = body(200, {"access_token": "a", "token_type": "Bearer"})
    assert ma.normalize_oauth_error_body(tokens) is tokens
    for unchanged in (body(200, b"not json"), body(200, {"other": 1}), body(500, {"error": "x"})):
        assert ma.normalize_oauth_error_body(unchanged) is unchanged


def test_the_auth_fetch_sends_extra_headers_and_normalizes_posts_only():
    with fake_http_server() as srv:
        srv.route("POST", "/token", Reply(200, {"error": "token_expired"}))
        srv.route("GET", "/token", Reply(200, {"error": "token_expired"}))
        fetch = ma.create_auth_fetch({"User-Agent": "ua", "X-Extra": "1"})
        post = fetched(fetch(f"{srv.base}/token", method="POST", headers={"X-Extra": "2"}, content=b"a=b"))
        get = fetched(fetch(f"{srv.base}/token"))
        assert post.status_code == 400 and post.json()["error"] == "invalid_grant"
        assert get.status_code == 200
        assert srv.seen[0].headers["X-Extra"] == "2" and srv.seen[0].headers["User-Agent"] == "ua"


# ── metadata ──


def test_configured_metadata_urls_must_be_https():
    with pytest.raises(ValueError, match=r"authServerMetadataUrl must use https:// \(got: http://x\)"):
        asyncio.run(ma.fetch_auth_server_metadata("s", "https://m", "http://x"))


def test_a_configured_metadata_url_is_fetched_directly():
    seen: list[Any] = []

    async def fetch(url: str, **kwargs: Any) -> httpx2.Response:
        seen.append((url, kwargs))
        return httpx2.Response(200, json=as_metadata("https://as"))

    found = asyncio.run(ma.fetch_auth_server_metadata("s", "https://m", "https://as/meta", fetch))
    assert found == as_metadata("https://as")
    assert seen == [("https://as/meta", {"headers": {"Accept": "application/json"}})]

    async def failing(url: str, **kwargs: Any) -> httpx2.Response:
        return httpx2.Response(404)

    with pytest.raises(ValueError, match="HTTP 404 fetching configured auth server metadata from https://as/meta"):
        asyncio.run(ma.fetch_auth_server_metadata("s", "https://m", "https://as/meta", failing))


def test_metadata_discovery_and_the_legacy_path_aware_fallback():
    with fake_http_server() as srv:
        install_authorization_server(srv)
        assert asyncio.run(ma.fetch_auth_server_metadata("s", f"{srv.base}/mcp", None)) == as_metadata(srv.base)
    with fake_http_server() as srv:
        assert asyncio.run(ma.fetch_auth_server_metadata("s", srv.base, None)) is None
        srv.route("GET", "/.well-known/oauth-authorization-server/legacy", Reply(200, as_metadata(srv.base)))
        assert asyncio.run(ma.fetch_auth_server_metadata("s", f"{srv.base}/legacy", None)) == as_metadata(srv.base)


def test_scope_from_metadata():
    assert ma.get_scope_from_metadata(None) is None
    assert ma.get_scope_from_metadata({"scope": "a", "default_scope": "b"}) == "a"
    assert ma.get_scope_from_metadata({"default_scope": "b", "scopes_supported": ["c"]}) == "b"
    assert ma.get_scope_from_metadata({"scopes_supported": ["c", "d"]}) == "c d"
    assert ma.get_scope_from_metadata({"issuer": "i"}) is None


# ── the provider ──


def test_client_metadata_is_a_public_tddagents_client(monkeypatch):
    monkeypatch.delenv(ma.CLIENT_METADATA_URL_ENV, raising=False)
    provider = ma.ClaudeAuthProvider("srv", http("https://m"), "http://localhost:9/callback")
    assert provider.client_metadata == {
        "client_name": "TDDAgents (srv)", "redirect_uris": ["http://localhost:9/callback"],
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    provider.set_metadata({"scopes_supported": ["a", "b"]})
    assert provider.client_metadata["scope"] == "a b"
    assert provider.client_metadata_url is None
    monkeypatch.setenv(ma.CLIENT_METADATA_URL_ENV, "https://me/client.json")
    assert provider.client_metadata_url == "https://me/client.json"
    assert ma.ClaudeAuthProvider("s", http("https://m")).redirect_url == "http://localhost:3118/callback"


def test_state_is_stable_per_provider():
    provider = ma.ClaudeAuthProvider("s", http("https://m"))
    first = asyncio.run(provider.state())
    assert first == asyncio.run(provider.state()) and len(first) >= 43
    assert asyncio.run(ma.ClaudeAuthProvider("s", http("https://m")).state()) != first


def test_client_information_prefers_the_stored_registration_then_the_configured_client():
    config = http("https://m", client_id="cfg")
    provider = ma.ClaudeAuthProvider("s", config)
    assert asyncio.run(provider.client_information()) == {"client_id": "cfg"}
    ma.save_mcp_client_secret("s", config, "shh")
    assert ma.get_mcp_client_config("s", config) == {"clientSecret": "shh"}
    assert asyncio.run(provider.client_information()) == {"client_id": "cfg", "client_secret": "shh"}
    asyncio.run(provider.save_client_information({"client_id": "dcr", "client_secret": "x"}))
    assert asyncio.run(provider.client_information()) == {"client_id": "dcr", "client_secret": "x"}
    assert entry("s", config) == {"serverName": "s", "serverUrl": "https://m", "clientId": "dcr",
                                  "clientSecret": "x", "accessToken": "", "expiresAt": 0}
    ma.clear_mcp_client_config("s", config)
    assert ma.get_mcp_client_config("s", config) is None
    assert asyncio.run(ma.ClaudeAuthProvider("s", http("https://other")).client_information()) is None


def test_save_tokens_and_read_them_back():
    config = http("https://m")
    provider = ma.ClaudeAuthProvider("s", config)
    assert asyncio.run(provider.tokens()) is None
    before = time.time() * 1000
    asyncio.run(provider.save_tokens({"access_token": "a", "refresh_token": "r", "expires_in": 600, "scope": "x"}))
    saved = entry("s", config) or {}
    assert saved["accessToken"] == "a" and saved["refreshToken"] == "r" and saved["scope"] == "x"
    assert before + 600_000 <= saved["expiresAt"] <= time.time() * 1000 + 600_000
    tokens = asyncio.run(provider.tokens()) or {}
    assert tokens["access_token"] == "a" and tokens["refresh_token"] == "r" and tokens["token_type"] == "Bearer"
    assert 590 < tokens["expires_in"] <= 600
    asyncio.run(provider.save_tokens({"access_token": "b"}))
    saved = entry("s", config) or {}
    assert "refreshToken" not in saved and "scope" not in saved
    assert saved["expiresAt"] >= time.time() * 1000 + 3500_000


def test_an_expired_token_without_refresh_token_is_no_token():
    config = http("https://m")
    put("s", config, accessToken="a", expiresAt=time.time() * 1000 - 1)
    assert asyncio.run(ma.ClaudeAuthProvider("s", config).tokens()) is None


def test_a_pending_step_up_withholds_the_refresh_token_until_tokens_are_saved():
    config = http("https://m")
    put("s", config, accessToken="a", refreshToken="r", scope="read", expiresAt=time.time() * 1000 + 3600_000)
    provider = ma.ClaudeAuthProvider("s", config)
    provider.mark_step_up_pending("read")
    assert (asyncio.run(provider.tokens()) or {}).get("refresh_token") == "r"
    provider.mark_step_up_pending("read write")
    assert "refresh_token" not in (asyncio.run(provider.tokens()) or {})
    asyncio.run(provider.save_tokens({"access_token": "b", "refresh_token": "r2", "scope": "read"}))
    assert (asyncio.run(provider.tokens()) or {}).get("refresh_token") == "r2"


def test_an_expiring_token_is_refreshed_once_for_concurrent_callers():
    with fake_http_server() as srv:
        install_authorization_server(srv)
        config = http(f"{srv.base}/mcp")
        put("s", config, accessToken="old", refreshToken="r0", clientId="cid",
            expiresAt=time.time() * 1000 + 10_000, discoveryState={"authorizationServerUrl": srv.base})
        provider = ma.ClaudeAuthProvider("s", config)

        async def both() -> list[Any]:
            return list(await asyncio.gather(provider.tokens(), provider.tokens()))

        first, second = asyncio.run(both())
        assert first["access_token"] == second["access_token"] == "tok-1"
        tokens = srv.requests("POST", "/token")
        assert len(tokens) == 1
        assert tokens[0].form == {"grant_type": "refresh_token", "refresh_token": "r0", "client_id": "cid",
                                  "resource": f"{srv.base}/mcp"}
        assert (entry("s", config) or {})["accessToken"] == "tok-1"
        assert not list((config_home()).glob("*.lock.lock"))


def test_a_refresh_that_fails_returns_the_current_tokens(monkeypatch):
    config = http("https://m.invalid/mcp")
    put("s", config, accessToken="old", refreshToken="r0", expiresAt=time.time() * 1000 + 10_000)
    provider = ma.ClaudeAuthProvider("s", config)

    async def none(_self: Any, refresh_token: str) -> None:
        return None

    monkeypatch.setattr(ma.ClaudeAuthProvider, "refresh_authorization", none)
    assert (asyncio.run(provider.tokens()) or {})["access_token"] == "old"

    async def boom(_self: Any, refresh_token: str) -> None:
        raise RuntimeError("down")

    monkeypatch.setattr(ma.ClaudeAuthProvider, "refresh_authorization", boom)
    assert (asyncio.run(provider.tokens()) or {})["access_token"] == "old"


def test_invalid_grant_on_refresh_invalidates_the_tokens():
    with fake_http_server() as srv:
        install_authorization_server(srv)
        srv.route("POST", "/token", Reply(400, {"error": "invalid_grant"}))
        config = http(f"{srv.base}/mcp")
        put("s", config, accessToken="old", refreshToken="r0", clientId="cid", expiresAt=0,
            discoveryState={"authorizationServerUrl": srv.base})
        provider = ma.ClaudeAuthProvider("s", config)
        assert asyncio.run(provider.refresh_authorization("r0")) is None
        saved = entry("s", config) or {}
        assert saved["accessToken"] == "" and saved["expiresAt"] == 0 and "refreshToken" not in saved


def test_tokens_another_process_refreshed_are_reused():
    config = http("https://m.invalid/mcp")
    put("s", config, accessToken="fresh", refreshToken="r1", expiresAt=time.time() * 1000 + 3600_000)
    tokens = asyncio.run(ma.ClaudeAuthProvider("s", config).refresh_authorization("stale")) or {}
    assert tokens["access_token"] == "fresh" and tokens["refresh_token"] == "r1"


def test_transient_refresh_failures_are_retried_with_backoff(monkeypatch):
    delays: list[float] = []

    async def no_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(ma, "_sleep", no_sleep)
    with fake_http_server() as srv:
        install_authorization_server(srv)
        srv.route("POST", "/token", Reply(503, {"error": "temporarily_unavailable"}))
        config = http(f"{srv.base}/mcp")
        put("s", config, accessToken="old", refreshToken="r0", clientId="cid", expiresAt=0,
            discoveryState={"authorizationServerUrl": srv.base})
        assert asyncio.run(ma.ClaudeAuthProvider("s", config).refresh_authorization("r0")) is None
        assert delays == [1.0, 2.0] and len(srv.requests("POST", "/token")) == 3
        srv.route("POST", "/token", Reply(400, {"error": "invalid_request"}))
        delays.clear()
        assert asyncio.run(ma.ClaudeAuthProvider("s", config).refresh_authorization("r0")) is None
        assert delays == []


def test_refresh_without_client_information_or_metadata_gives_up():
    with fake_http_server() as srv:
        install_authorization_server(srv)
        config = http(f"{srv.base}/mcp")
        put("s", config, accessToken="old", refreshToken="r0", expiresAt=0)
        assert asyncio.run(ma.ClaudeAuthProvider("s", config).refresh_authorization("r0")) is None
        assert not srv.requests("POST", "/token")
    with fake_http_server() as srv:
        config = http(srv.base)
        put("s", config, accessToken="old", refreshToken="r0", clientId="c", expiresAt=0)
        assert asyncio.run(ma.ClaudeAuthProvider("s", config).refresh_authorization("r0")) is None


def test_a_held_refresh_lock_is_waited_for_then_ignored(monkeypatch):
    delays: list[float] = []

    async def no_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(ma, "_sleep", no_sleep)
    config = http("https://m.invalid/mcp")
    put("s", config, accessToken="fresh", expiresAt=time.time() * 1000 + 3600_000)
    provider = ma.ClaudeAuthProvider("s", config)
    import re

    sanitized = re.sub(r"[^a-zA-Z0-9]", "_", ma.get_server_key("s", config))
    lock_dir = config_home() / f"mcp-refresh-{sanitized}.lock.lock"
    lock_dir.mkdir(parents=True)
    tokens = asyncio.run(provider.refresh_authorization("r")) or {}
    assert tokens["access_token"] == "fresh"
    assert len(delays) == ma.MAX_LOCK_RETRIES and all(1 <= d <= 2 for d in delays)
    assert lock_dir.exists()


def test_invalidate_credentials_by_scope():
    config = http("https://m")
    provider = ma.ClaudeAuthProvider("s", config)
    asyncio.run(provider.invalidate_credentials("all"))
    full = {"accessToken": "a", "refreshToken": "r", "expiresAt": 5, "clientId": "c", "clientSecret": "x",
            "discoveryState": {"authorizationServerUrl": "u"}, "stepUpScope": "s"}
    put("s", config, **full)
    asyncio.run(provider.invalidate_credentials("client"))
    assert "clientId" not in (entry("s", config) or {}) and "clientSecret" not in (entry("s", config) or {})
    asyncio.run(provider.invalidate_credentials("tokens"))
    assert {k: (entry("s", config) or {}).get(k) for k in ("accessToken", "refreshToken", "expiresAt")} == {
        "accessToken": "", "refreshToken": None, "expiresAt": 0}
    asyncio.run(provider.invalidate_credentials("discovery"))
    assert "discoveryState" not in (entry("s", config) or {}) and "stepUpScope" not in (entry("s", config) or {})
    asyncio.run(provider.save_code_verifier("v"))
    asyncio.run(provider.invalidate_credentials("verifier"))
    with pytest.raises(ValueError, match="No code verifier saved"):
        asyncio.run(provider.code_verifier())
    asyncio.run(provider.invalidate_credentials("all"))
    assert entry("s", config) is None


def test_discovery_state_keeps_only_the_urls():
    config = http("https://m")
    provider = ma.ClaudeAuthProvider("s", config)
    assert asyncio.run(provider.discovery_state()) is None
    asyncio.run(provider.save_discovery_state({
        "authorizationServerUrl": "https://as", "resourceMetadataUrl": "https://m/prm",
        "authorizationServerMetadata": {"big": "blob"}, "resourceMetadata": {"big": "blob"}}))
    assert (entry("s", config) or {})["discoveryState"] == {"authorizationServerUrl": "https://as",
                                                            "resourceMetadataUrl": "https://m/prm"}
    assert asyncio.run(provider.discovery_state()) == {"authorizationServerUrl": "https://as",
                                                       "resourceMetadataUrl": "https://m/prm"}


def test_discovery_state_from_a_configured_metadata_url(monkeypatch):
    config = http("https://m", auth_server_metadata_url="https://as/meta")

    async def fake(name: str, url: str, configured: str | None, *rest: Any) -> dict[str, Any]:
        assert configured == "https://as/meta"
        return as_metadata("https://as")

    monkeypatch.setattr(ma, "fetch_auth_server_metadata", fake)
    state = asyncio.run(ma.ClaudeAuthProvider("s", config).discovery_state())
    assert state == {"authorizationServerUrl": "https://as", "authorizationServerMetadata": as_metadata("https://as")}

    async def failing(*args: Any) -> None:
        raise ValueError("no")

    monkeypatch.setattr(ma, "fetch_auth_server_metadata", failing)
    assert asyncio.run(ma.ClaudeAuthProvider("s", config).discovery_state()) is None


def test_the_transport_provider_persists_the_step_up_scope_and_does_not_redirect():
    config = http("https://m")
    put("s", config, accessToken="", expiresAt=0)
    urls: list[str] = []
    provider = ma.ClaudeAuthProvider("s", config, on_authorization_url=urls.append)
    asyncio.run(provider.redirect_to_authorization("https://as/authorize?scope=a+b&state=x"))
    assert (entry("s", config) or {})["stepUpScope"] == "a b"
    assert urls == [] and provider.authorization_url == "https://as/authorize?scope=a+b&state=x"
    other = ma.ClaudeAuthProvider("s2", http("https://n"))
    other.set_metadata({"scopes_supported": ["m"]})
    asyncio.run(other.redirect_to_authorization("https://as/authorize"))
    assert entry("s2", http("https://n")) is None


def test_the_flow_provider_announces_the_url_and_opens_the_browser(monkeypatch):
    opened: list[str] = []

    async def fake_open(url: str) -> bool:
        opened.append(url)
        return False

    monkeypatch.setattr(ma, "open_browser", fake_open)
    config = http("https://m")
    put("s", config, accessToken="", expiresAt=0)
    urls: list[str] = []
    provider = ma.ClaudeAuthProvider("s", config, "http://localhost:1/callback", True, urls.append)
    asyncio.run(provider.redirect_to_authorization("https://as/authorize?scope=a"))
    assert urls == opened == ["https://as/authorize?scope=a"]
    assert "stepUpScope" not in (entry("s", config) or {})
    quiet = ma.ClaudeAuthProvider("s", config, "http://localhost:1/callback", True, urls.append, True)
    asyncio.run(quiet.redirect_to_authorization("https://as/authorize"))
    assert opened == ["https://as/authorize?scope=a"]
    with pytest.raises(ValueError, match="must use http:// or https:// scheme"):
        asyncio.run(provider.redirect_to_authorization("ftp://as/authorize"))


def test_discovery_without_a_token_means_needs_auth():
    config = http("https://m")
    assert ma.has_mcp_discovery_but_no_token("s", config) is False
    put("s", config, accessToken="", expiresAt=0, discoveryState={"authorizationServerUrl": "u"})
    assert ma.has_mcp_discovery_but_no_token("s", config) is True
    put("s", config, accessToken="", refreshToken="r", expiresAt=0)
    assert ma.has_mcp_discovery_but_no_token("s", config) is False
    assert ma.is_xaa_enabled() is False


# ── revocation ──


def test_revoke_server_tokens_revokes_refresh_then_access_and_clears_locally():
    with fake_http_server() as srv:
        install_authorization_server(srv, revocation_endpoint=f"{srv.base}/revoke")
        srv.route("POST", "/revoke", Reply(200, {}))
        config = http(f"{srv.base}/mcp")
        put("s", config, accessToken="a", refreshToken="r", clientId="cid", expiresAt=0)
        asyncio.run(ma.revoke_server_tokens("s", config))
        forms = [s.form for s in srv.requests("POST", "/revoke")]
        assert forms == [{"token": "r", "token_type_hint": "refresh_token", "client_id": "cid"},
                         {"token": "a", "token_type_hint": "access_token", "client_id": "cid"}]
        assert entry("s", config) is None


def test_revocation_uses_client_secret_methods_and_the_bearer_fallback():
    with fake_http_server() as srv:
        install_authorization_server(srv, revocation_endpoint=f"{srv.base}/revoke",
                                     revocation_endpoint_auth_methods_supported=["client_secret_post"])
        srv.route("POST", "/revoke", lambda r: Reply(401 if "Authorization" not in r.headers else 200, {}))
        config = http(f"{srv.base}/mcp")
        put("s", config, accessToken="a", clientId="cid", clientSecret="sec", expiresAt=0)
        asyncio.run(ma.revoke_server_tokens("s", config))
        first, retry = srv.requests("POST", "/revoke")
        assert first.form == {"token": "a", "token_type_hint": "access_token", "client_id": "cid",
                              "client_secret": "sec"}
        assert retry.form == {"token": "a", "token_type_hint": "access_token"}
        assert retry.headers["Authorization"] == "Bearer a"


def test_basic_auth_encodes_like_encode_uri_component():
    import base64

    assert ma.basic_auth_header("a b", "s:/!") == "Basic " + base64.b64encode(b"a%20b:s%3A%2F!").decode()


def test_revocation_is_best_effort_and_can_preserve_step_up_state():
    with fake_http_server() as srv:
        install_authorization_server(srv)
        config = http(f"{srv.base}/mcp")
        put("s", config, accessToken="a", expiresAt=0, stepUpScope="admin",
            discoveryState={"authorizationServerUrl": srv.base, "resourceMetadataUrl": "u",
                            "authorizationServerMetadata": {"blob": 1}})
        asyncio.run(ma.revoke_server_tokens("s", config, preserve_step_up_state=True))
        assert entry("s", config) == {
            "serverName": "s", "serverUrl": f"{srv.base}/mcp", "accessToken": "", "expiresAt": 0,
            "stepUpScope": "admin", "discoveryState": {"authorizationServerUrl": srv.base, "resourceMetadataUrl": "u"},
        }
        asyncio.run(ma.revoke_server_tokens("s", config))
        assert entry("s", config) is None
    asyncio.run(ma.revoke_server_tokens("gone", http("https://x")))


# ── the browser flow ──


async def _hit(url: str) -> httpx2.Response:
    async with httpx2.AsyncClient() as client:
        return await client.get(url)


def run_flow(config: McpServerConfig, respond: Any, **kwargs: Any) -> Any:
    """Run `perform_mcp_oauth_flow`; `respond(auth_url)` acts as the browser."""

    async def scenario() -> Any:
        urls: asyncio.Queue[str] = asyncio.Queue()
        flow = asyncio.create_task(ma.perform_mcp_oauth_flow(
            "s", config, urls.put_nowait, kwargs.pop("abort", None), skip_browser_open=True, **kwargs))
        getter = asyncio.create_task(urls.get())
        done, _ = await asyncio.wait({flow, getter}, return_when=asyncio.FIRST_COMPLETED)
        if getter in done:
            browser = await respond(getter.result())
        else:
            getter.cancel()
            browser = None
        try:
            await flow
            return browser, None
        except Exception as exc:  # noqa: BLE001 - handed to the assertions
            return browser, exc

    return asyncio.run(scenario())


def callback_for(auth_url: str, **params: str) -> str:
    q = query(auth_url)
    return f"{q['redirect_uri']}?{urlencode(params)}"


def test_the_browser_flow_exchanges_the_callback_code_for_tokens(monkeypatch):
    monkeypatch.delenv("TDDAGENTS_MCP_OAUTH_CALLBACK_PORT", raising=False)
    with fake_http_server() as srv:
        install_authorization_server(srv, scopes=["read"])
        config = http(f"{srv.base}/mcp")
        put("s", config, accessToken="stale", expiresAt=0)

        async def browser(auth_url: str) -> Any:
            q = query(auth_url)
            assert q["redirect_uri"].startswith("http://localhost:") and q["scope"] == "read"
            return await _hit(callback_for(auth_url, code="the-code", state=q["state"]))

        page, error = run_flow(config, browser)
        assert error is None
        assert page.status_code == 200 and "Return to TDDAgents." in page.text
        saved = entry("s", config) or {}
        assert saved["accessToken"] == "tok-1" and saved["clientId"] == "client-1"
        exchange = srv.requests("POST", "/token")[0].form
        assert exchange["code"] == "the-code" and exchange["grant_type"] == "authorization_code"


def test_a_pasted_callback_url_completes_the_flow():
    with fake_http_server() as srv:
        install_authorization_server(srv)
        config = http(f"{srv.base}/mcp")
        submitters: list[Any] = []

        async def paste(auth_url: str) -> None:
            q = query(auth_url)
            submit = submitters[0]
            submit("not a url")
            submit(callback_for(auth_url, state=q["state"]))
            submit(callback_for(auth_url, code="pasted", state=q["state"]))

        _, error = run_flow(config, paste, on_waiting_for_callback=submitters.append)
        assert error is None
        assert srv.requests("POST", "/token")[0].form["code"] == "pasted"


def test_a_wrong_state_is_rejected_as_csrf():
    with fake_http_server() as srv:
        install_authorization_server(srv)

        async def browser(auth_url: str) -> Any:
            return await _hit(callback_for(auth_url, code="c", state="forged"))

        page, error = run_flow(http(f"{srv.base}/mcp"), browser)
        assert page.status_code == 400 and "Invalid state parameter" in page.text
        assert str(error) == ma.STATE_MISMATCH_MESSAGE


def test_a_pasted_url_with_a_wrong_state_is_rejected():
    with fake_http_server() as srv:
        install_authorization_server(srv)
        submitters: list[Any] = []

        async def paste(auth_url: str) -> None:
            submitters[0](callback_for(auth_url, code="c", state="forged"))

        _, error = run_flow(http(f"{srv.base}/mcp"), paste, on_waiting_for_callback=submitters.append)
        assert str(error) == ma.STATE_MISMATCH_MESSAGE


def test_a_provider_error_is_reported_and_escaped():
    with fake_http_server() as srv:
        install_authorization_server(srv)

        async def browser(auth_url: str) -> Any:
            return await _hit(callback_for(auth_url, error="access_denied", error_description="<b>no</b>",
                                           error_uri="https://help"))

        page, error = run_flow(http(f"{srv.base}/mcp"), browser)
        assert "access_denied: &lt;b&gt;no&lt;/b&gt;" in page.text
        assert str(error) == "OAuth error: access_denied - <b>no</b> (See: https://help)"


def test_a_pasted_provider_error_is_reported():
    with fake_http_server() as srv:
        install_authorization_server(srv)
        submitters: list[Any] = []

        async def paste(auth_url: str) -> None:
            submitters[0](callback_for(auth_url, error="denied"))

        _, error = run_flow(http(f"{srv.base}/mcp"), paste, on_waiting_for_callback=submitters.append)
        assert str(error) == "OAuth error: denied - "


def test_abort_and_timeout_end_the_flow(monkeypatch):
    with fake_http_server() as srv:
        install_authorization_server(srv)
        controller = AbortController()

        async def cancel(auth_url: str) -> None:
            controller.abort()

        _, error = run_flow(http(f"{srv.base}/mcp"), cancel, abort=controller.signal)
        assert isinstance(error, ma.AuthenticationCancelledError)
        _, error = run_flow(http(f"{srv.base}/mcp"), cancel, abort=controller.signal)
        assert isinstance(error, ma.AuthenticationCancelledError)
        monkeypatch.setattr(ma, "AUTHENTICATION_TIMEOUT_MS", 50)

        async def ignore(auth_url: str) -> None:
            return None

        _, error = run_flow(http(f"{srv.base}/mcp"), ignore)
        assert str(error) == "Authentication timeout"


def test_an_sdk_failure_before_the_redirect_fails_the_flow():
    with fake_http_server() as srv:
        install_authorization_server(srv, registration_endpoint=None)
        _, error = run_flow(http(f"{srv.base}/mcp"), None)
        assert str(error) == ("SDK auth failed: Incompatible auth server: does not support dynamic client "
                              "registration")


def test_a_busy_callback_port_is_reported(monkeypatch):
    import socket

    with fake_http_server() as srv, socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen()
        port = held.getsockname()[1]
        install_authorization_server(srv)
        _, error = run_flow(http(f"{srv.base}/mcp", callback_port=port), None)
        assert str(error).startswith(f"OAuth callback port {port} is already in use")


def test_a_cached_step_up_scope_is_requested(monkeypatch):
    with fake_http_server() as srv:
        install_authorization_server(srv, scopes=["read"])
        config = http(f"{srv.base}/mcp")
        put("s", config, accessToken="", expiresAt=0, stepUpScope="admin",
            discoveryState={"authorizationServerUrl": srv.base})
        seen: list[str] = []

        async def browser(auth_url: str) -> None:
            seen.append(query(auth_url)["scope"])
            raise asyncio.CancelledError

        controller = AbortController()

        async def look(auth_url: str) -> None:
            seen.append(query(auth_url)["scope"])
            controller.abort()

        run_flow(config, look, abort=controller.signal)
        assert seen == ["admin"]


def test_xaa_servers_are_refused():
    with pytest.raises(RuntimeError, match="XAA is not supported by TDDAgents"):
        asyncio.run(ma.perform_mcp_oauth_flow("s", http("https://m", xaa=True), lambda u: None))


def test_an_unknown_registered_client_is_forgotten_by_auths_retry():
    # auth() answers invalid_client by invalidating everything and retrying; the retried code
    # exchange then has no client to present, as upstream's.
    with fake_http_server() as srv:
        install_authorization_server(srv)
        srv.route("POST", "/token", Reply(401, {"error": "invalid_client", "error_description": "Client not found"}))
        config = http(f"{srv.base}/mcp")

        async def browser(auth_url: str) -> Any:
            return await _hit(callback_for(auth_url, code="c", state=query(auth_url)["state"]))

        _, error = run_flow(config, browser)
        assert str(error) == "Existing OAuth client information is required when exchanging an authorization code"
        assert "clientId" not in (entry("s", config) or {})
        assert len(srv.requests("POST", "/token")) == 1


def test_client_not_found_for_a_configured_client_reaches_the_caller():
    with fake_http_server() as srv:
        install_authorization_server(srv)
        srv.route("POST", "/token", Reply(401, {"error": "invalid_client", "error_description": "Client not found"}))
        config = http(f"{srv.base}/mcp", client_id="configured")

        async def browser(auth_url: str) -> Any:
            assert query(auth_url)["client_id"] == "configured"
            return await _hit(callback_for(auth_url, code="c", state=query(auth_url)["state"]))

        _, error = run_flow(config, browser)
        assert isinstance(error, InvalidClientError) and str(error) == "Client not found"
        assert len(srv.requests("POST", "/token")) == 2


def test_client_not_found_drops_a_stored_client_registration(monkeypatch):
    config = http("https://m/mcp")
    seen: list[Any] = []

    async def fake(provider: Any, **kwargs: Any) -> Any:
        seen.append(kwargs.get("authorization_code"))
        if kwargs.get("authorization_code") is None:
            await provider.save_client_information({"client_id": "dcr", "client_secret": "sec"})
            params = {"state": await provider.state(), "redirect_uri": provider.redirect_url}
            await provider.redirect_to_authorization(f"https://as/authorize?{urlencode(params)}")
            return "REDIRECT"
        raise InvalidClientError("Client not found")

    monkeypatch.setattr(ma, "sdk_auth", fake)
    monkeypatch.setattr(ma, "fetch_auth_server_metadata", _no_metadata)

    async def browser(auth_url: str) -> Any:
        return await _hit(callback_for(auth_url, code="c", state=query(auth_url)["state"]))

    _, error = run_flow(config, browser)
    assert isinstance(error, InvalidClientError) and seen == [None, "c"]
    kept = entry("s", config) or {}
    assert "clientId" not in kept and "clientSecret" not in kept and kept["serverName"] == "s"


async def _no_metadata(*args: Any, **kwargs: Any) -> None:
    return None


# ── step-up detection and the transport bridge ──


def test_step_up_detection_marks_the_challenged_scope():
    provider = ma.ClaudeAuthProvider("s", http("https://m"))
    calls: list[str] = []

    async def base(url: str, **kwargs: Any) -> httpx2.Response:
        calls.append(url)
        return httpx2.Response(403, headers={"WWW-Authenticate": 'Bearer error="insufficient_scope", scope=admin'})

    wrapped = ma.wrap_fetch_with_step_up_detection(base, provider)
    fetched(wrapped("https://m/x"))
    assert calls == ["https://m/x"] and provider._pending_step_up_scope == "admin"
    other = ma.ClaudeAuthProvider("s", http("https://m"))
    ma.mark_step_up_from_response(httpx2.Response(403, headers={"WWW-Authenticate": "Bearer error=other"}), other)
    ma.mark_step_up_from_response(httpx2.Response(401, headers={"WWW-Authenticate": "insufficient_scope scope=x"}),
                                  other)
    assert other._pending_step_up_scope is None


class Script:
    """A mock MCP endpoint: answers in order, recording each request's Authorization."""

    def __init__(self, *responses: httpx2.Response) -> None:
        self.responses = list(responses)
        self.auth: list[str | None] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.auth.append(request.headers.get("Authorization"))
        return self.responses.pop(0)


def bridge_run(bridge: ma.McpTransportAuth, script: Script) -> httpx2.Response:
    async def scenario() -> httpx2.Response:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(script), auth=bridge) as client:
            return await client.post("https://m/mcp", json={"x": 1})

    return asyncio.run(scenario())


def fake_auth(monkeypatch: Any, results: list[Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def fake(provider: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        result = results.pop(0)
        if isinstance(result, Exception):
            raise result
        if result == "AUTHORIZED":
            await provider.save_tokens({"access_token": "fresh"})
        return result

    monkeypatch.setattr(ma, "sdk_auth", fake)
    return calls


def test_the_bridge_sends_the_stored_bearer_token():
    config = http("https://m/mcp")
    put("s", config, accessToken="tok", expiresAt=time.time() * 1000 + 3600_000)
    script = Script(httpx2.Response(200))
    bridge = ma.McpTransportAuth(ma.ClaudeAuthProvider("s", config), "https://m/mcp")
    assert bridge_run(bridge, script).status_code == 200
    assert script.auth == ["Bearer tok"] and bridge.failure is None


def test_a_401_that_needs_the_browser_is_recorded_as_unauthorized(monkeypatch):
    calls = fake_auth(monkeypatch, ["REDIRECT"])
    script = Script(httpx2.Response(401, headers={
        "WWW-Authenticate": 'Bearer resource_metadata="https://m/prm", scope="read"'}))
    bridge = ma.McpTransportAuth(ma.ClaudeAuthProvider("s", http("https://m/mcp")), "https://m/mcp")
    assert bridge_run(bridge, script).status_code == 401
    assert isinstance(bridge.failure, UnauthorizedError) and script.auth == [None]
    assert calls[0]["resource_metadata_url"] == "https://m/prm" and calls[0]["scope"] == "read"
    assert calls[0]["server_url"] == "https://m/mcp"


def test_a_401_that_auth_resolves_is_retried_with_the_new_token(monkeypatch):
    fake_auth(monkeypatch, ["AUTHORIZED"])
    script = Script(httpx2.Response(401), httpx2.Response(200))
    bridge = ma.McpTransportAuth(ma.ClaudeAuthProvider("s", http("https://m/mcp")), "https://m/mcp")
    assert bridge_run(bridge, script).status_code == 200
    assert script.auth == [None, "Bearer fresh"] and bridge.failure is None
    assert bridge._has_completed_auth_flow is False


def test_a_401_after_a_completed_flow_is_an_error(monkeypatch):
    fake_auth(monkeypatch, ["AUTHORIZED"])
    script = Script(httpx2.Response(401), httpx2.Response(401))
    bridge = ma.McpTransportAuth(ma.ClaudeAuthProvider("s", http("https://m/mcp")), "https://m/mcp")
    assert bridge_run(bridge, script).status_code == 401
    assert str(bridge.failure) == "Server returned 401 after successful authentication"


def test_an_auth_error_is_recorded(monkeypatch):
    fake_auth(monkeypatch, [ValueError("discovery broke")])
    bridge = ma.McpTransportAuth(ma.ClaudeAuthProvider("s", http("https://m/mcp")), "https://m/mcp")
    bridge_run(bridge, Script(httpx2.Response(401)))
    assert str(bridge.failure) == "discovery broke"


def test_insufficient_scope_steps_up_once_per_challenge(monkeypatch):
    calls = fake_auth(monkeypatch, ["AUTHORIZED", "AUTHORIZED"])
    challenge = {"WWW-Authenticate": 'Bearer error="insufficient_scope", scope="admin"'}
    script = Script(httpx2.Response(403, headers=challenge), httpx2.Response(403, headers=challenge))
    provider = ma.ClaudeAuthProvider("s", http("https://m/mcp"))
    bridge = ma.McpTransportAuth(provider, "https://m/mcp")
    assert bridge_run(bridge, script).status_code == 403
    assert calls[0]["scope"] == "admin" and len(calls) == 1
    assert str(bridge.failure) == "Server returned 403 after trying upscoping"
    plain = ma.McpTransportAuth(provider, "https://m/mcp")
    assert bridge_run(plain, Script(httpx2.Response(403))).status_code == 403 and plain.failure is None


def test_a_step_up_the_browser_must_grant_is_unauthorized(monkeypatch):
    fake_auth(monkeypatch, ["REDIRECT"])
    challenge = {"WWW-Authenticate": 'Bearer error="insufficient_scope", scope="admin"'}
    bridge = ma.McpTransportAuth(ma.ClaudeAuthProvider("s", http("https://m/mcp")), "https://m/mcp")
    bridge_run(bridge, Script(httpx2.Response(403, headers=challenge)))
    assert isinstance(bridge.failure, UnauthorizedError)


# ── client secrets ──


def test_read_client_secret_from_the_environment_or_fail_without_a_tty(monkeypatch):
    monkeypatch.setenv(ma.CLIENT_SECRET_ENV, "envsecret")
    assert asyncio.run(ma.read_client_secret()) == "envsecret"
    monkeypatch.delenv(ma.CLIENT_SECRET_ENV)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(RuntimeError, match="No TTY available to prompt for client secret. Set "
                                           "TDDAGENTS_MCP_CLIENT_SECRET env var instead."):
        asyncio.run(ma.read_client_secret())
