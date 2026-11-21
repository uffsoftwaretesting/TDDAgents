"""
WebFetch, extensively: every branch of the port against what `reference/claude-code/src/tools/WebFetchTool/`
does, offline (an httpx `MockTransport` and a fake small model, never the network).

Organised by upstream symbol:

* `new URL(url)` -> `parse_url` (hostname, port, IDN, IPv6), used by every function below;
* `WebFetchTool.ts`: `webFetchToolInputToPermissionRuleContent`, `checkPermissions`, `validateInput`,
  `description`, `call` (its output, redirects, the preapproved shortcut, binary content);
* `utils.ts`: `validateURL`, `isPermittedRedirect`, `getWithPermittedRedirects` (relative locations, the hop
  limit, 303, the egress proxy's 403), `getURLMarkdownContent` (upgrade, cache, HTML, decoding, size cap),
  `applyPromptToMarkdown`;
* `UI.tsx`: `renderToolUseMessage`, `getToolUseSummary`, `getActivityDescription`;
* the permission dialog (`WebFetchPermissionRequest.tsx`) and the whole pipeline (`run_tool_use` with
  `canUseTool`, headless, rules from the settings).
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from langchain_core.messages import AIMessage, ToolCall

from app.components.permissions.permission_options import NO, YES, permission_options
from app.components.permissions.web_fetch_permission_request.web_fetch_permission_request import (
    web_fetch_permission_request,
)
from app.hooks.use_can_use_tool import PermissionAnswer, PermissionPrompt, build_interactive_can_use_tool
from app.services.tools.tool_execution import run_tool_use
from app.state.app_state_store import AppState, AppStateStore
from app.tool import tool_context_for
from app.tools.web_fetch_tool import ui
from app.tools.web_fetch_tool import utils as fetch_utils
from app.tools.web_fetch_tool.utils import (
    MAX_HTTP_CONTENT_LENGTH,
    MAX_MARKDOWN_LENGTH,
    MAX_REDIRECTS,
    EgressBlockedError,
    ParsedUrl,
    RedirectInfo,
    apply_prompt_to_markdown,
    is_permitted_redirect,
    is_preapproved_url,
    parse_url,
    validate_url,
)
from app.tools.web_fetch_tool.web_fetch_tool import (
    INPUT_RULE_CONTENT,
    build_web_fetch_tool,
    check_web_fetch_permissions,
    permission_rule_content,
    redirect_message,
)
from app.types.permissions import (
    PermissionBehavior,
    PermissionMode,
    PermissionResult,
    PermissionRule,
    PermissionRuleSource,
    ToolPermissionContext,
)
from app.utils.settings.settings import build_tool_permission_context
from app.workspace.local import LocalWorkspace
from tests.conftest import FakeWorkspace


@pytest.fixture(autouse=True)
def _fresh_cache() -> Any:
    fetch_utils.clear_web_fetch_cache()
    yield
    fetch_utils.clear_web_fetch_cache()


def q(url: str) -> dict[str, Any]:
    """A schema-valid input (`url` and `prompt`)."""
    return {"url": url, "prompt": "q"}


class Model:
    """The small, fast model: records each prompt and answers `answer`."""

    def __init__(self, answer: str = "ANSWER") -> None:
        self.prompts: list[str] = []
        self.answer = answer

    def __call__(self, context: Any) -> Any:
        async def ask(prompt: str) -> str:
            self.prompts.append(prompt)
            return self.answer
        return ask


class Server:
    """An httpx transport over a route table; records every request."""

    def __init__(self, routes: dict[str, httpx.Response] | None = None) -> None:
        self.routes = routes or {}
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return self.routes.get(str(request.url), httpx.Response(404, text="missing"))
        return httpx.MockTransport(handle)

    @property
    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]


def fetch(tmp_path: Path, args: dict[str, Any], server: Server, model: Model | None = None,
          clock: list[float] | None = None) -> Any:
    times = iter(clock if clock is not None else [0.0, 0.0])
    tool = build_web_fetch_tool(model_for=model or Model(), transport=server.transport(), now=lambda: next(times))
    ctx = tool_context_for(AppStateStore(), workspace=LocalWorkspace(str(tmp_path / "project")), run_id="r1")
    return asyncio.run(tool.call(args, ctx))


def markdown(text: str) -> httpx.Response:
    return httpx.Response(200, text=text, headers={"content-type": "text/markdown; charset=utf-8"})


def html(body: str) -> httpx.Response:
    return httpx.Response(200, html=f"<html><body>{body}</body></html>",
                          headers={"content-type": "text/html; charset=utf-8"})


# ── new URL(url) ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("url", "expected"), [
    ("https://Example.COM/A", ParsedUrl("https", "example.com", "", "", "", "/A")),
    ("https://example.com:443/x", ParsedUrl("https", "example.com", "", "", "", "/x")),
    ("http://example.com:80", ParsedUrl("http", "example.com", "", "", "", "/")),
    ("https://example.com:8443/x", ParsedUrl("https", "example.com", "8443", "", "", "/x")),
    ("https://bücher.example/x", ParsedUrl("https", "xn--bcher-kva.example", "", "", "", "/x")),
    ("http://[::1]:8080/", ParsedUrl("http", "[::1]", "8080", "", "", "/")),
    ("https://u:p@example.com/", ParsedUrl("https", "example.com", "", "u", "p", "/")),
    ("  https://example.com/x  ", ParsedUrl("https", "example.com", "", "", "", "/x")),
    ("mailto:someone@example.com", ParsedUrl("mailto", "", "", "", "", "someone@example.com")),
    ("file:///etc/passwd", ParsedUrl("file", "", "", "", "", "/etc/passwd")),
])
def test_parse_url_reads_what_the_url_parser_reads(url: str, expected: ParsedUrl) -> None:
    assert parse_url(url) == expected


@pytest.mark.parametrize("url", ["nonsense", "", "://x", "https://", "https:///path", "http://example.com:99999/",
                                 "http://example.com:abc/", "1http://x.com"])
def test_parse_url_is_none_where_the_url_parser_throws(url: str) -> None:
    assert parse_url(url) is None


# ── webFetchToolInputToPermissionRuleContent ───────────────────────────────────

@pytest.mark.parametrize(("url", "content"), [
    ("https://example.com/a?b=c#d", "domain:example.com"),
    ("https://EXAMPLE.com", "domain:example.com"),
    ("https://example.com:8443/x", "domain:example.com"),
    ("https://user:pw@example.com/x", "domain:example.com"),
    ("https://docs.example.com/x", "domain:docs.example.com"),
    ("https://bücher.example/x", "domain:xn--bcher-kva.example"),
    ("http://[2001:db8::1]/x", "domain:[2001:db8::1]"),
    ("http://192.168.0.1/x", "domain:192.168.0.1"),
])
def test_the_rule_content_is_the_urls_hostname(url: str, content: str) -> None:
    assert permission_rule_content(q(url)) == content


@pytest.mark.parametrize("args", [
    {"url": "nonsense", "prompt": "q"},
    {"url": "https://example.com/"},                                   # no prompt: the strict schema fails
    {"url": "https://example.com/", "prompt": "q", "extra": 1},        # an unknown key: strictObject fails
    {"url": 7, "prompt": "q"},
    {"prompt": "q"},
    {},
])
def test_an_input_that_does_not_parse_falls_back_to_upstreams_literal(args: dict[str, Any]) -> None:
    """`input:${input.toString()}`: a plain object's `toString()` is `[object Object]`."""
    assert permission_rule_content(args) == INPUT_RULE_CONTENT == "input:[object Object]"


# ── checkPermissions ───────────────────────────────────────────────────────────

def rule(behavior: PermissionBehavior, content: str | None = "domain:example.com") -> PermissionRule:
    return PermissionRule(tool_name="WebFetch", rule_behavior=behavior, rule_content=content,
                          source=PermissionRuleSource.LOCAL_SETTINGS)


def perm_ctx(*rules: PermissionRule, mode: PermissionMode = PermissionMode.DEFAULT,
             headless: bool = False) -> Any:
    perm = ToolPermissionContext(
        mode=mode, should_avoid_permission_prompts=headless,
        always_allow_rules=tuple(r for r in rules if r.rule_behavior == PermissionBehavior.ALLOW),
        always_deny_rules=tuple(r for r in rules if r.rule_behavior == PermissionBehavior.DENY),
        always_ask_rules=tuple(r for r in rules if r.rule_behavior == PermissionBehavior.ASK))
    return tool_context_for(AppStateStore(AppState(tool_permission_context=perm)))


ASK_MESSAGE = "TDDAgents requested permissions to use WebFetch, but you haven't granted it yet."


def test_a_preapproved_host_is_allowed_before_any_rule_even_a_deny() -> None:
    """`checkPermissions` tests `isPreapprovedHost` first (l.110); a deny rule on that domain never runs."""
    args = q("https://docs.python.org/3/library/")
    result = check_web_fetch_permissions(args, perm_ctx(rule(PermissionBehavior.DENY, "domain:docs.python.org")))
    assert result.behavior == PermissionBehavior.ALLOW
    assert result.updated_input == args
    assert result.decision_reason == {"type": "other", "reason": "Preapproved host"}


@pytest.mark.parametrize(("url", "preapproved"), [
    ("https://docs.python.org/3/", True),
    ("https://DOCS.PYTHON.ORG/3/", True),
    ("https://docs.python.org.evil.com/", False),
    ("https://evil.com/docs.python.org", False),
    ("https://github.com/anthropics/x", True),
    ("https://github.com/anthropics-fake/x", False),
    ("https://github.com/other/x", False),
])
def test_preapproval_is_by_host_and_path_prefix(url: str, preapproved: bool) -> None:
    assert is_preapproved_url(url) is preapproved
    result = check_web_fetch_permissions(q(url), perm_ctx())
    assert (result.behavior == PermissionBehavior.ALLOW) is preapproved


def test_deny_wins_over_ask_and_allow_on_the_same_domain() -> None:
    rules = (rule(PermissionBehavior.ALLOW), rule(PermissionBehavior.ASK), rule(PermissionBehavior.DENY))
    result = check_web_fetch_permissions(q("https://example.com/a"), perm_ctx(*rules))
    assert result.behavior == PermissionBehavior.DENY
    assert result.message == "WebFetch denied access to domain:example.com."
    assert result.decision_reason is not None and result.decision_reason["rule"].rule_behavior == "deny"


def test_ask_wins_over_allow_and_still_suggests_the_domain() -> None:
    rules = (rule(PermissionBehavior.ALLOW), rule(PermissionBehavior.ASK))
    result = check_web_fetch_permissions(q("https://example.com/a"), perm_ctx(*rules))
    assert result.behavior == PermissionBehavior.ASK and result.message == ASK_MESSAGE
    assert result.decision_reason is not None and result.decision_reason["type"] == "rule"
    (update,) = result.suggestions or ()
    assert update.rules == (("WebFetch", "domain:example.com"),)
    assert update.destination == PermissionRuleSource.LOCAL_SETTINGS and update.behavior == PermissionBehavior.ALLOW


def test_an_allow_rule_hands_back_the_whole_input() -> None:
    args = q("https://example.com/a")
    result = check_web_fetch_permissions(args, perm_ctx(rule(PermissionBehavior.ALLOW)))
    assert result.behavior == PermissionBehavior.ALLOW and result.updated_input == args


@pytest.mark.parametrize("url", ["https://docs.example.com/a", "https://example.com.evil.org/", "https://ample.com/"])
def test_a_domain_rule_matches_that_exact_hostname_only(url: str) -> None:
    """`getRuleByContentsForTool(...).get(ruleContent)`: an exact lookup, so no subdomain or suffix match."""
    result = check_web_fetch_permissions(q(url), perm_ctx(rule(PermissionBehavior.ALLOW)))
    assert result.behavior == PermissionBehavior.ASK


def test_a_tool_wide_rule_is_not_a_domain_rule() -> None:
    """`WebFetch` with no content is a whole-tool rule, decided by the pipeline, not by `checkPermissions`."""
    result = check_web_fetch_permissions(q("https://example.com/a"), perm_ctx(rule(PermissionBehavior.ALLOW, None)))
    assert result.behavior == PermissionBehavior.ASK


def test_an_idn_rule_is_written_and_matched_in_punycode() -> None:
    result = check_web_fetch_permissions(q("https://bücher.example/a"),
                                         perm_ctx(rule(PermissionBehavior.ALLOW, "domain:xn--bcher-kva.example")))
    assert result.behavior == PermissionBehavior.ALLOW


def test_without_a_rule_it_asks_with_no_reason_and_the_domain_suggestion() -> None:
    result = check_web_fetch_permissions(q("https://example.com/a"), perm_ctx())
    assert (result.behavior, result.message, result.decision_reason) == (PermissionBehavior.ASK, ASK_MESSAGE, None)
    (update,) = result.suggestions or ()
    assert update.rules == (("WebFetch", "domain:example.com"),)


# ── validateInput and description ──────────────────────────────────────────────

@pytest.mark.parametrize(("url", "valid"), [
    ("https://example.com", True), ("http://example.com/a b", True), ("mailto:x@example.com", True),
    ("file:///etc/passwd", True), ("nonsense", False), ("", False), ("https://", False),
])
def test_validate_input_accepts_what_the_url_parser_accepts(url: str, valid: bool) -> None:
    tool = build_web_fetch_tool(model_for=Model())
    result = tool.validate_input(q(url), tool_context_for(AppStateStore()))
    assert result.valid is valid
    if not valid:
        assert result.message == f'Error: Invalid URL "{url}". The URL provided could not be parsed.'
        assert result.error_code == 1


@pytest.mark.parametrize(("url", "text"), [
    ("https://Example.com/a", "TDDAgents wants to fetch content from example.com"),
    ("https://bücher.example/", "TDDAgents wants to fetch content from xn--bcher-kva.example"),
    ("nonsense", "TDDAgents wants to fetch content from this URL"),
])
def test_the_description_names_the_host(url: str, text: str) -> None:
    assert build_web_fetch_tool(model_for=Model()).description(q(url)) == text


# ── validateURL ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("url", "valid"), [
    ("https://example.com/", True),
    ("https://sub.example.co.uk/x", True),
    ("http://192.168.1.1/", True),            # four labels: upstream's dotted-hostname filter lets it through
    ("https://localhost/", False),
    ("https://intranet/", False),
    ("https://user@example.com/", False),
    ("https://:pw@example.com/", False),
    ("https://example.com/" + "a" * 1980, True),
    ("https://example.com/" + "a" * 1981, False),  # 2001 characters
    ("file:///etc/passwd", False),
    ("nonsense", False),
])
def test_validate_url(url: str, valid: bool) -> None:
    assert validate_url(url) is valid


# ── isPermittedRedirect ────────────────────────────────────────────────────────

@pytest.mark.parametrize(("original", "redirect", "permitted"), [
    ("https://example.com/a", "https://example.com/b", True),
    ("https://example.com/a", "https://www.example.com/a", True),
    ("https://www.example.com/a", "https://example.com/a", True),
    ("https://example.com/a", "https://EXAMPLE.com/a", True),
    ("https://example.com/a", "https://example.com:443/a", True),        # the default port is ''
    ("https://example.com:8443/a", "https://example.com:8443/b", True),
    ("https://example.com/a", "https://example.com:8443/a", False),
    ("https://example.com/a", "http://example.com/a", False),
    ("https://example.com/a", "https://user:pw@example.com/a", False),
    ("https://example.com/a", "https://evil.com/a", False),
    ("https://example.com/a", "https://sub.example.com/a", False),
    ("https://www.example.com/a", "https://www2.example.com/a", False),
    ("https://example.com/a", "https://wwwexample.com/a", False),
    ("https://example.com/a", "nonsense", False),
])
def test_is_permitted_redirect(original: str, redirect: str, permitted: bool) -> None:
    assert is_permitted_redirect(original, redirect) is permitted


# ── getWithPermittedRedirects / getURLMarkdownContent through call ───────────────

def test_the_request_asks_for_markdown_first_and_names_its_agent(tmp_path: Path) -> None:
    server = Server({"https://example.com/a": markdown("# A")})
    fetch(tmp_path, q("https://example.com/a"), server)
    (request,) = server.requests
    assert request.headers["accept"] == "text/markdown, text/html, */*"
    assert request.headers["user-agent"].startswith("TDDAgents-User (")


def test_a_relative_redirect_resolves_against_the_url_and_is_followed(tmp_path: Path) -> None:
    server = Server({"https://example.com/docs/a": httpx.Response(308, headers={"location": "../b"}),
                     "https://example.com/b": markdown("B")})
    model = Model()
    result = fetch(tmp_path, q("https://example.com/docs/a"), server, model)
    assert server.urls == ["https://example.com/docs/a", "https://example.com/b"]
    assert result.content == "ANSWER" and "B" in model.prompts[0]


@pytest.mark.parametrize(("status", "text"), [(301, "Moved Permanently"), (302, "Found"),
                                              (307, "Temporary Redirect"), (308, "Permanent Redirect")])
def test_a_redirect_to_another_host_is_reported_with_its_status(tmp_path: Path, status: int, text: str) -> None:
    server = Server({"https://example.com/a": httpx.Response(status, headers={"location": "https://other.org/z"})})
    model = Model()
    result = fetch(tmp_path, {"url": "https://example.com/a", "prompt": "summarise"}, server, model,
                   clock=[10.0, 10.25])
    assert result.content == redirect_message(RedirectInfo("https://example.com/a", "https://other.org/z", status),
                                              "summarise")
    assert f"Status: {status} {text}" in result.content
    assert result.content.endswith('- url: "https://other.org/z"\n- prompt: "summarise"')
    assert result.metadata == {"bytes": len(result.content.encode()), "code": status, "codeText": text,
                               "durationMs": 250, "url": "https://example.com/a"}
    assert model.prompts == [] and result.is_error is False


def test_a_www_redirect_is_followed_and_a_scheme_change_is_reported(tmp_path: Path) -> None:
    server = Server({"https://example.com/a": httpx.Response(301, headers={"location": "https://www.example.com/a"}),
                     "https://www.example.com/a": markdown("W"),
                     "https://example.com/b": httpx.Response(301, headers={"location": "http://example.com/b"})})
    assert fetch(tmp_path, q("https://example.com/a"), server).content == "ANSWER"
    reported = fetch(tmp_path, q("https://example.com/b"), server)
    assert reported.content.startswith("REDIRECT DETECTED") and "Redirect URL: http://example.com/b" in reported.content


def test_a_redirect_without_location_is_an_error(tmp_path: Path) -> None:
    result = fetch(tmp_path, q("https://example.com/a"), Server({"https://example.com/a": httpx.Response(302)}))
    assert result.is_error is True and "Redirect missing Location header" in result.content


def test_redirects_stop_after_the_hop_limit(tmp_path: Path) -> None:
    routes = {f"https://example.com/{i}": httpx.Response(301, headers={"location": f"/{i + 1}"})
              for i in range(MAX_REDIRECTS + 2)}
    server = Server(routes)
    result = fetch(tmp_path, q("https://example.com/0"), server)
    assert result.is_error is True and f"Too many redirects (exceeded {MAX_REDIRECTS})" in result.content
    assert len(server.requests) == MAX_REDIRECTS + 1


def test_a_303_is_not_a_followed_redirect(tmp_path: Path) -> None:
    """Upstream follows 301, 302, 307 and 308 only; axios rejects any other non-2xx."""
    server = Server({"https://example.com/a": httpx.Response(303, headers={"location": "/b"}),
                     "https://example.com/b": markdown("B")})
    result = fetch(tmp_path, q("https://example.com/a"), server)
    assert result.is_error is True and server.urls == ["https://example.com/a"]


def test_the_egress_proxys_refusal_is_its_own_error(tmp_path: Path) -> None:
    blocked = httpx.Response(403, headers={"x-proxy-error": "blocked-by-allowlist"}, text="blocked")
    result = fetch(tmp_path, q("https://Example.com/a"), Server({"https://example.com/a": blocked}))
    assert result.is_error is True
    payload = json.loads(result.content.split(": ", 1)[1])
    assert payload == {"error_type": "EGRESS_BLOCKED", "domain": "example.com",
                       "message": "Access to example.com is blocked by the network egress proxy."}
    assert str(EgressBlockedError("a.org")) == ('{"error_type":"EGRESS_BLOCKED","domain":"a.org",'
                                                '"message":"Access to a.org is blocked by the network egress proxy."}')


@pytest.mark.parametrize("status", [403, 404, 500])
def test_any_other_error_status_is_the_calls_error(tmp_path: Path, status: int) -> None:
    result = fetch(tmp_path, q("https://example.com/a"),
                   Server({"https://example.com/a": httpx.Response(status, text="no")}))
    assert result.is_error is True and result.content.startswith("Error fetching https://example.com/a:")
    assert "EGRESS_BLOCKED" not in result.content


def test_a_body_over_the_size_cap_is_an_error(tmp_path: Path) -> None:
    big = httpx.Response(200, content=b"a" * (MAX_HTTP_CONTENT_LENGTH + 1), headers={"content-type": "text/plain"})
    result = fetch(tmp_path, q("https://example.com/big"), Server({"https://example.com/big": big}))
    assert result.is_error is True and "maximum content length" in result.content


@pytest.mark.parametrize("url", ["https://localhost/", "https://user:pw@example.com/", "nonsense"])
def test_an_invalid_url_never_reaches_the_network(tmp_path: Path, url: str) -> None:
    server = Server()
    result = fetch(tmp_path, q(url), server)
    assert result.is_error is True and "Invalid URL" in result.content and server.requests == []


def test_http_is_upgraded_and_cached_under_the_requested_url(tmp_path: Path) -> None:
    server = Server({"https://example.com/a": markdown("A")})
    fetch(tmp_path, q("http://example.com/a"), server)
    fetch(tmp_path, q("http://example.com/a"), server)
    assert server.urls == ["https://example.com/a"]
    # The https URL is another cache key: it is fetched once more.
    fetch(tmp_path, q("https://example.com/a"), server)
    assert server.urls == ["https://example.com/a", "https://example.com/a"]


def test_a_redirect_is_never_cached(tmp_path: Path) -> None:
    server = Server({"https://example.com/a": httpx.Response(302, headers={"location": "https://x.org/"})})
    fetch(tmp_path, q("https://example.com/a"), server)
    fetch(tmp_path, q("https://example.com/a"), server)
    assert len(server.requests) == 2


def test_the_cache_expires(tmp_path: Path) -> None:
    clock = [0.0]
    fetch_utils.URL_CACHE.clock = lambda: clock[0]
    try:
        server = Server({"https://example.com/a": markdown("A")})
        fetch(tmp_path, q("https://example.com/a"), server)
        clock[0] = fetch_utils.CACHE_TTL_MS / 1000 + 1
        fetch(tmp_path, q("https://example.com/a"), server)
        assert len(server.requests) == 2
    finally:
        fetch_utils.URL_CACHE.clock = time.monotonic


def test_html_becomes_markdown_before_the_model(tmp_path: Path) -> None:
    model = Model()
    page = html("<h1>Title</h1><ul><li>one</li><li>two</li></ul><a href='https://x.org'>link</a>")
    fetch(tmp_path, q("https://example.com/p"), Server({"https://example.com/p": page}), model)
    (prompt,) = model.prompts
    assert "Title\n=====" in prompt and "* one" in prompt and "[link](https://x.org)" in prompt
    assert "<h1>" not in prompt


@pytest.mark.parametrize("content_type", ["text/plain", "application/json", ""])
def test_anything_but_html_is_passed_raw(tmp_path: Path, content_type: str) -> None:
    model = Model()
    body = '<p>not html</p> {"a": 1}'
    response = httpx.Response(200, content=body.encode(), headers={"content-type": content_type} if content_type
                              else {})
    result = fetch(tmp_path, q("https://example.com/r"), Server({"https://example.com/r": response}), model)
    assert body in model.prompts[0]
    assert result.metadata["bytes"] == len(body.encode())


def test_bytes_that_are_not_utf8_are_replaced_not_fatal(tmp_path: Path) -> None:
    model = Model()
    response = httpx.Response(200, content=b"ok \xff\xfe end", headers={"content-type": "text/plain"})
    result = fetch(tmp_path, q("https://example.com/r"), Server({"https://example.com/r": response}), model)
    assert result.is_error is False and "ok �� end" in model.prompts[0]


def test_the_output_reports_bytes_code_text_duration_and_url(tmp_path: Path) -> None:
    body = "é" * 10
    response = httpx.Response(200, text=body, headers={"content-type": "text/plain; charset=utf-8"})
    result = fetch(tmp_path, q("https://example.com/r"), Server({"https://example.com/r": response}),
                   clock=[5.0, 5.5])
    assert result.metadata == {"bytes": 20, "code": 200, "codeText": "OK", "durationMs": 500,
                               "url": "https://example.com/r"}
    assert result.content == "ANSWER"


# ── the preapproved shortcut and applyPromptToMarkdown ─────────────────────────

def test_short_preapproved_markdown_is_returned_without_the_model(tmp_path: Path) -> None:
    model = Model()
    url = "https://docs.python.org/3/x.md"
    result = fetch(tmp_path, q(url), Server({url: markdown("# Doc\n\nexact")}), model)
    assert result.content == "# Doc\n\nexact" and model.prompts == []


@pytest.mark.parametrize(("url", "response", "preapproved_prompt"), [
    # preapproved, but not markdown: the model, with the preapproved guidelines
    ("https://docs.python.org/3/x", html("<p>x</p>"), True),
    # preapproved markdown at the length limit: the model
    ("https://docs.python.org/3/x.md", markdown("m" * MAX_MARKDOWN_LENGTH), True),
    # markdown from any other host: the model, with the quoting guidelines
    ("https://example.com/x.md", markdown("# Doc"), False),
])
def test_everything_else_goes_through_the_model(
        tmp_path: Path, url: str, response: httpx.Response, preapproved_prompt: bool) -> None:
    model = Model()
    result = fetch(tmp_path, q(url), Server({url: response}), model)
    assert result.content == "ANSWER"
    (prompt,) = model.prompts
    assert ("strict 125-character maximum" in prompt) is not preapproved_prompt
    assert ("documentation excerpts as needed" in prompt) is preapproved_prompt


def test_the_users_prompt_sits_between_the_page_and_the_guidelines() -> None:
    seen: list[str] = []

    async def model(prompt: str) -> str:
        seen.append(prompt)
        return "A"

    asyncio.run(apply_prompt_to_markdown("What is X?", "PAGE", False, model))
    (prompt,) = seen
    assert prompt.index("PAGE") < prompt.index("What is X?") < prompt.index("Provide a concise response")


@pytest.mark.parametrize(("length", "truncated"), [(MAX_MARKDOWN_LENGTH, False), (MAX_MARKDOWN_LENGTH + 1, True)])
def test_truncation_starts_one_past_the_limit(length: int, truncated: bool) -> None:
    seen: list[str] = []

    async def model(prompt: str) -> str:
        seen.append(prompt)
        return "A"

    asyncio.run(apply_prompt_to_markdown("q", "x" * length, False, model))
    assert ("[Content truncated due to length...]" in seen[0]) is truncated


def test_an_empty_answer_is_no_response_from_model(tmp_path: Path) -> None:
    result = fetch(tmp_path, q("https://example.com/r"), Server({"https://example.com/r": markdown("x")}), Model(""))
    assert result.content == "No response from model"


# ── binary content ─────────────────────────────────────────────────────────────

def test_binary_content_is_saved_and_still_summarised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "cfg"))
    model = Model("SUMMARY")
    pdf = httpx.Response(200, content=b"%PDF-1.4 /Title (Hi)", headers={"content-type": "application/pdf"})
    result = fetch(tmp_path, q("https://example.com/d.pdf"), Server({"https://example.com/d.pdf": pdf}), model)
    assert result.content.startswith("SUMMARY\n\n[Binary content (application/pdf, 20 bytes) also saved to ")
    assert "/Title (Hi)" in model.prompts[0]


def test_a_failed_save_leaves_the_summary_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.tools.web_fetch_tool.web_fetch_tool as module

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise OSError("disk full")

    monkeypatch.setattr(module, "persist_binary_content", refuse)
    png = httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})
    result = fetch(tmp_path, q("https://example.com/i.png"), Server({"https://example.com/i.png": png}))
    assert result.content == "ANSWER" and result.is_error is False


# ── UI.tsx ─────────────────────────────────────────────────────────────────────

def test_the_ui_strings() -> None:
    args = {"url": "https://example.com/" + "p" * 200, "prompt": "find the API"}
    assert ui.user_facing_name(args) == "Fetch"
    assert ui.render_tool_use_message(args, False) == args["url"]
    assert ui.render_tool_use_message(args, True) == f'url: "{args["url"]}", prompt: "find the API"'
    assert ui.render_tool_use_message({"url": "https://a.org"}, True) == 'url: "https://a.org"'
    assert ui.render_tool_use_message({}, False) is None
    summary = ui.get_tool_use_summary(args)
    assert summary is not None and summary.startswith("https://example.com/") and summary.endswith("…")
    assert len(summary) < len(args["url"])
    assert ui.get_tool_use_summary({"url": ""}) is None and ui.get_tool_use_summary(None) is None
    assert ui.get_activity_description(q("https://a.org")) == "Fetching https://a.org"
    assert ui.get_activity_description({}) == "Fetching web page"


# ── WebFetchPermissionRequest ──────────────────────────────────────────────────

def test_the_dialog_reads_as_upstreams() -> None:
    tool = build_web_fetch_tool(model_for=Model())
    args = q("https://example.com/a")
    dialog = web_fetch_permission_request(tool, args, PermissionResult(behavior=PermissionBehavior.ASK))
    assert dialog.title == "Fetch"
    assert dialog.body == "https://example.com/a"
    assert dialog.description == "TDDAgents wants to fetch content from example.com"
    assert dialog.question == "Do you want to allow TDDAgents to fetch this content?"


@pytest.mark.parametrize(("url", "host", "content"), [
    ("https://Docs.Example.com:8443/x", "docs.example.com", "domain:docs.example.com"),
    ("https://bücher.example/", "xn--bcher-kva.example", "domain:xn--bcher-kva.example"),
    ("http://[::1]:9/", "[::1]", "domain:[::1]"),
])
def test_the_remembered_choice_names_the_hostname_and_adds_its_rule(url: str, host: str, content: str) -> None:
    options = permission_options("WebFetch", q(url), None, ToolPermissionContext(), "/w")
    assert options[0] == YES and options[-1] == NO
    assert options[1].label == f"Yes, and don't ask again for {host}"
    (update,) = options[1].updates
    assert update.rules == (("WebFetch", content),)
    assert update.destination == PermissionRuleSource.LOCAL_SETTINGS and update.behavior == PermissionBehavior.ALLOW


# ── the whole pipeline ─────────────────────────────────────────────────────────

class Asker:
    def __init__(self, *answers: PermissionAnswer) -> None:
        self.answers = list(answers)
        self.prompts: list[PermissionPrompt] = []

    async def __call__(self, prompt: PermissionPrompt) -> PermissionAnswer:
        self.prompts.append(prompt)
        return self.answers.pop(0)


def session(tmp_path: Path, asker: Asker | None, *, mode: str = "default") -> Any:
    (tmp_path / "home").mkdir(exist_ok=True)
    perm = build_tool_permission_context(tmp_path, tmp_path / "home", managed_dir=tmp_path / "etc",
                                         permission_mode_cli=mode, working_dir=tmp_path / "ws",
                                         should_avoid_permission_prompts=asker is None)
    ws = FakeWorkspace()
    ws.root = tmp_path / "ws"  # type: ignore[attr-defined]
    ctx = tool_context_for(AppStateStore(state=AppState(tool_permission_context=perm)), workspace=ws)
    if asker is not None:
        ctx.can_use_tool = build_interactive_can_use_tool(asker)
    return ctx


def run(tool: Any, args: dict[str, Any], ctx: Any) -> Any:
    ctx.tools = (tool,)
    call = ToolCall(name=tool.name, args=args, id="w1")
    return asyncio.run(run_tool_use(call, AIMessage(content="", tool_calls=[call]), ctx, (tool,)))


ANYWHERE = Server({url: markdown("page") for url in (
    "https://example.com/a", "https://example.com/b", "https://other.org/a", "https://docs.python.org/3/")})


def test_yes_fetches_once_and_asks_again_next_time(tmp_path: Path) -> None:
    asker = Asker(PermissionAnswer("yes"), PermissionAnswer("no", "not now"))
    ctx = session(tmp_path, asker)
    tool = build_web_fetch_tool(model_for=Model(), transport=ANYWHERE.transport())
    assert run(tool, q("https://example.com/a"), ctx).message.status == "success"
    assert run(tool, q("https://example.com/b"), ctx).message.status == "error"
    assert len(asker.prompts) == 2
    assert not (tmp_path / ".tddagents" / "settings.local.json").exists()


def test_dont_ask_again_persists_the_domain_rule(tmp_path: Path) -> None:
    asker = Asker(PermissionAnswer("yes-dont-ask-again-domain"), PermissionAnswer("no"))
    ctx = session(tmp_path, asker)
    tool = build_web_fetch_tool(model_for=Model(), transport=ANYWHERE.transport())
    assert run(tool, q("https://example.com/a"), ctx).message.status == "success"
    assert run(tool, q("https://example.com/b"), ctx).message.status == "success"
    assert run(tool, q("https://other.org/a"), ctx).message.status == "error"
    assert len(asker.prompts) == 2 and asker.prompts[1].input == q("https://other.org/a")
    settings = json.loads((tmp_path / ".tddagents" / "settings.local.json").read_text())
    assert settings["permissions"]["allow"] == ["WebFetch(domain:example.com)"]


def test_no_with_feedback_reaches_the_model_and_fetches_nothing(tmp_path: Path) -> None:
    server = Server({"https://example.com/a": markdown("page")})
    ctx = session(tmp_path, Asker(PermissionAnswer("no", "use the local docs instead")))
    tool = build_web_fetch_tool(model_for=Model(), transport=server.transport())
    outcome = run(tool, q("https://example.com/a"), ctx)
    assert outcome.message.status == "error" and "use the local docs instead" in outcome.message.content
    assert server.requests == []


def test_a_preapproved_host_never_asks(tmp_path: Path) -> None:
    asker = Asker()
    tool = build_web_fetch_tool(model_for=Model(), transport=ANYWHERE.transport())
    assert run(tool, q("https://docs.python.org/3/"), session(tmp_path, asker)).message.status == "success"
    assert asker.prompts == []


def test_headless_asks_nobody_and_denies(tmp_path: Path) -> None:
    server = Server({"https://example.com/a": markdown("page")})
    tool = build_web_fetch_tool(model_for=Model(), transport=server.transport())
    outcome = run(tool, q("https://example.com/a"), session(tmp_path, None))
    assert outcome.message.status == "error" and server.requests == []


def test_a_domain_rule_in_the_settings_decides_without_asking(tmp_path: Path) -> None:
    settings = tmp_path / ".tddagents"
    settings.mkdir()
    (settings / "settings.json").write_text(json.dumps({"permissions": {
        "allow": ["WebFetch(domain:example.com)"], "deny": ["WebFetch(domain:other.org)"]}}))
    asker = Asker()
    ctx = session(tmp_path, asker)
    tool = build_web_fetch_tool(model_for=Model(), transport=ANYWHERE.transport())
    assert run(tool, q("https://example.com/a"), ctx).message.status == "success"
    denied = run(tool, q("https://other.org/a"), ctx)
    assert denied.message.status == "error" and "WebFetch denied access to domain:other.org." in denied.message.content
    assert asker.prompts == []


def test_bypass_mode_fetches_without_asking(tmp_path: Path) -> None:
    asker = Asker()
    tool = build_web_fetch_tool(model_for=Model(), transport=ANYWHERE.transport())
    assert run(tool, q("https://other.org/a"), session(tmp_path, asker, mode="bypassPermissions")).message.status \
        == "success"
    assert asker.prompts == []


def test_an_invalid_url_is_refused_before_any_question(tmp_path: Path) -> None:
    asker = Asker()
    tool = build_web_fetch_tool(model_for=Model(), transport=ANYWHERE.transport())
    outcome = run(tool, q("nonsense"), session(tmp_path, asker))
    assert outcome.message.status == "error"
    assert 'Error: Invalid URL "nonsense". The URL provided could not be parsed.' in outcome.message.content
    assert asker.prompts == []
