"""
WebFetch (2026-10-28): a port of `WebFetchTool.ts`, `utils.ts`, `preapproved.ts` and `prompt.ts`,
driven through an httpx `MockTransport` and a fake small model, never the network.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from langchain_core.messages import AIMessage, ToolCall

from app.services.tools.tool_execution import run_tool_use
from app.state.app_state_store import AppState, AppStateStore
from app.tool import tool_context_for
from app.tools.web_fetch_tool import utils as fetch_utils
from app.tools.web_fetch_tool.preapproved import PREAPPROVED_HOSTS, is_preapproved_host
from app.tools.web_fetch_tool.utils import (
    MAX_MARKDOWN_LENGTH,
    FetchedContent,
    RedirectInfo,
    apply_prompt_to_markdown,
    is_binary_content_type,
    is_permitted_redirect,
    make_secondary_model_prompt,
    validate_url,
)
from app.tools.web_fetch_tool.web_fetch_tool import (
    build_suggestions,
    build_web_fetch_tool,
    check_web_fetch_permissions,
    permission_rule_content,
    redirect_message,
)
from app.types.permissions import (
    PermissionBehavior,
    PermissionMode,
    PermissionRule,
    PermissionRuleSource,
    ToolPermissionContext,
)
from app.utils.permissions.yolo_classifier import to_auto_classifier_input
from app.workspace.local import LocalWorkspace

REPO = Path(__file__).resolve().parent.parent
UPSTREAM = REPO / "reference" / "claude-code" / "src" / "tools" / "WebFetchTool"
needs_reference = pytest.mark.skipif(not UPSTREAM.is_dir(),
                                     reason="the claude-code reference snapshot is not checked out")


@pytest.fixture(autouse=True)
def _fresh_cache():
    fetch_utils.clear_web_fetch_cache()
    yield
    fetch_utils.clear_web_fetch_cache()


# ── pure helpers ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url, valid", [
    ("https://docs.python.org/3/", True), ("http://example.com/a", True),
    ("https://localhost/x", False), ("https://user:pw@example.com", False), ("not a url", False),
    ("https://example.com/" + "a" * 2000, False),
])
def test_validate_url(url: str, valid: bool):
    assert validate_url(url) is valid


@pytest.mark.parametrize("original, redirect, permitted", [
    ("https://example.com/a", "https://example.com/b", True),
    ("https://example.com/a", "https://www.example.com/a", True),
    ("https://www.example.com/a", "https://example.com/a", True),
    ("https://example.com/a", "https://evil.com/a", False),
    ("https://example.com/a", "http://example.com/a", False),
    ("https://example.com/a", "https://example.com:8443/a", False),
    ("https://example.com/a", "https://u:p@example.com/a", False),
])
def test_is_permitted_redirect(original: str, redirect: str, permitted: bool):
    assert is_permitted_redirect(original, redirect) is permitted


def test_preapproved_hosts_keep_path_boundaries():
    assert "docs.python.org" in PREAPPROVED_HOSTS and "github.com/anthropics" in PREAPPROVED_HOSTS
    assert is_preapproved_host("docs.python.org", "/3/") is True
    assert is_preapproved_host("github.com", "/anthropics") is True
    assert is_preapproved_host("github.com", "/anthropics/claude-code") is True
    assert is_preapproved_host("github.com", "/anthropics-evil/x") is False
    assert is_preapproved_host("example.com", "/") is False


@pytest.mark.parametrize("content_type, binary", [
    ("", False), ("text/html; charset=utf-8", False), ("application/json", False), ("application/ld+json", False),
    ("application/xml", False), ("application/atom+xml", False), ("application/javascript", False),
    ("application/x-www-form-urlencoded", False), ("application/pdf", True), ("image/png", True),
])
def test_is_binary_content_type(content_type: str, binary: bool):
    assert is_binary_content_type(content_type) is binary


def test_the_secondary_prompt_varies_with_preapproval():
    approved = make_secondary_model_prompt("PAGE", "What?", True)
    assert approved == ("\nWeb page content:\n---\nPAGE\n---\n\nWhat?\n\nProvide a concise response based on the "
                        "content above. Include relevant details, code examples, and documentation excerpts as "
                        "needed.\n")
    other = make_secondary_model_prompt("PAGE", "What?", False)
    assert "strict 125-character maximum for quotes" in other
    assert "Never produce or reproduce exact song lyrics." in other


def test_long_content_is_truncated_before_the_model():
    seen: list[str] = []

    async def model(prompt: str) -> str:
        seen.append(prompt)
        return ""

    answer = asyncio.run(apply_prompt_to_markdown("q", "x" * (MAX_MARKDOWN_LENGTH + 5), False, model))
    assert answer == "No response from model"
    assert "x" * MAX_MARKDOWN_LENGTH + "\n\n[Content truncated due to length...]" in seen[0]
    assert "x" * (MAX_MARKDOWN_LENGTH + 1) not in seen[0]


# ── permissions ──────────────────────────────────────────────────────────────

def ctx_with(*rules: PermissionRule, headless: bool = True) -> Any:
    perm = ToolPermissionContext(
        mode=PermissionMode.DEFAULT, should_avoid_permission_prompts=headless,
        always_allow_rules=tuple(r for r in rules if r.rule_behavior == PermissionBehavior.ALLOW),
        always_deny_rules=tuple(r for r in rules if r.rule_behavior == PermissionBehavior.DENY),
        always_ask_rules=tuple(r for r in rules if r.rule_behavior == PermissionBehavior.ASK))
    return tool_context_for(AppStateStore(AppState(tool_permission_context=perm)))


def rule(behavior: PermissionBehavior, content: str = "domain:example.com") -> PermissionRule:
    return PermissionRule(tool_name="WebFetch", rule_behavior=behavior, rule_content=content,
                          source=PermissionRuleSource.LOCAL_SETTINGS)


def test_rule_content_is_the_domain():
    assert permission_rule_content({"url": "https://a.example.com/x"}) == "domain:a.example.com"
    assert permission_rule_content({"url": "nonsense"}).startswith("input:")


def test_a_preapproved_host_is_allowed_without_a_rule():
    result = check_web_fetch_permissions({"url": "https://docs.python.org/3/"}, ctx_with())
    assert result.behavior == PermissionBehavior.ALLOW
    assert result.decision_reason == {"type": "other", "reason": "Preapproved host"}


@pytest.mark.parametrize("behavior, expected", [
    (PermissionBehavior.DENY, PermissionBehavior.DENY),
    (PermissionBehavior.ASK, PermissionBehavior.ASK),
    (PermissionBehavior.ALLOW, PermissionBehavior.ALLOW),
])
def test_domain_rules_decide(behavior: PermissionBehavior, expected: PermissionBehavior):
    result = check_web_fetch_permissions({"url": "https://example.com/a"}, ctx_with(rule(behavior)))
    assert result.behavior == expected
    if expected == PermissionBehavior.DENY:
        assert result.message == "WebFetch denied access to domain:example.com."


def test_with_no_rule_it_asks_and_suggests_allowing_the_domain():
    other_domain = ctx_with(rule(PermissionBehavior.ALLOW, "domain:other.com"))
    result = check_web_fetch_permissions({"url": "https://example.com/a"}, other_domain)
    assert result.behavior == PermissionBehavior.ASK
    assert result.message == "TDDAgents requested permissions to use WebFetch, but you haven't granted it yet."
    assert result.suggestions == build_suggestions("domain:example.com")
    (update,) = result.suggestions
    assert update.rules == (("WebFetch", "domain:example.com"),) and update.behavior == PermissionBehavior.ALLOW


# ── the call ─────────────────────────────────────────────────────────────────

class Recorder:
    def __init__(self, answer: str = "ANSWER") -> None:
        self.prompts: list[str] = []
        self.answer = answer

    def __call__(self, context: Any) -> Any:
        async def ask(prompt: str) -> str:
            self.prompts.append(prompt)
            return self.answer
        return ask


def handler_for(routes: dict[str, httpx.Response]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        return routes.get(str(request.url), httpx.Response(404, text="missing"))
    return httpx.MockTransport(handle)


def run_call(tmp_path: Path, args: dict[str, Any], transport: httpx.MockTransport, model: Recorder) -> Any:
    tool = build_web_fetch_tool(model_for=model, transport=transport)
    ctx = tool_context_for(AppStateStore(), workspace=LocalWorkspace(str(tmp_path / "project")), run_id="r1")
    return asyncio.run(tool.call(args, ctx))


HTML = httpx.Response(200, html="<html><body><h1>Title</h1><p>Hello <b>world</b></p></body></html>",
                      headers={"content-type": "text/html; charset=utf-8"})


def test_html_is_converted_and_answered_by_the_small_model(tmp_path: Path):
    model = Recorder()
    result = run_call(tmp_path, {"url": "https://example.com/page", "prompt": "Summarize"},
                      handler_for({"https://example.com/page": HTML}), model)
    assert result.content == "ANSWER" and result.is_error is False
    assert result.metadata["code"] == 200 and result.metadata["url"] == "https://example.com/page"
    (prompt,) = model.prompts
    assert "Title\n=====" in prompt and "Hello **world**" in prompt and "\nSummarize\n" in prompt
    assert "125-character maximum" in prompt  # not preapproved


def test_http_is_upgraded_and_the_result_is_cached(tmp_path: Path):
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text="plain body", headers={"content-type": "text/plain"})

    model = Recorder()
    transport = httpx.MockTransport(handle)
    run_call(tmp_path, {"url": "http://example.com/a", "prompt": "q"}, transport, model)
    run_call(tmp_path, {"url": "http://example.com/a", "prompt": "q"}, transport, model)
    assert calls == ["https://example.com/a"]  # upgraded, then served from the cache
    assert "plain body" in model.prompts[1]


def test_preapproved_short_markdown_is_returned_as_is(tmp_path: Path):
    model = Recorder()
    url = "https://docs.python.org/3/x.md"
    result = run_call(tmp_path, {"url": url, "prompt": "q"},
                      handler_for({url: httpx.Response(200, text="# Doc", headers={"content-type": "text/markdown"})}),
                      model)
    assert result.content == "# Doc" and model.prompts == []


def test_same_host_redirects_are_followed_and_others_reported(tmp_path: Path):
    routes = {
        "https://example.com/old": httpx.Response(301, headers={"location": "/new"}),
        "https://example.com/new": HTML,
        "https://example.com/away": httpx.Response(302, headers={"location": "https://evil.com/x"}),
    }
    model = Recorder()
    assert run_call(tmp_path, {"url": "https://example.com/old", "prompt": "q"}, handler_for(routes),
                    model).content == "ANSWER"
    away = run_call(tmp_path, {"url": "https://example.com/away", "prompt": "find it"}, handler_for(routes), model)
    assert away.content == redirect_message(RedirectInfo("https://example.com/away", "https://evil.com/x", 302),
                                            "find it")
    assert away.content.startswith("REDIRECT DETECTED: The URL redirects to a different host.")
    assert "Status: 302 Found" in away.content and '- url: "https://evil.com/x"' in away.content


def test_a_redirect_loop_is_an_error(tmp_path: Path):
    loop = httpx.MockTransport(lambda r: httpx.Response(301, headers={"location": str(r.url)}))
    result = run_call(tmp_path, {"url": "https://example.com/a", "prompt": "q"}, loop, Recorder())
    assert result.is_error is True and "Too many redirects (exceeded 10)" in result.content


def test_binary_content_is_also_saved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "cfg"))
    pdf = httpx.Response(200, content=b"%PDF-1.4 data", headers={"content-type": "application/pdf"})
    result = run_call(tmp_path, {"url": "https://example.com/doc.pdf", "prompt": "q"},
                      handler_for({"https://example.com/doc.pdf": pdf}), Recorder())
    saved = re.search(r"\[Binary content \(application/pdf, 13 bytes\) also saved to (.+\.pdf)\]", result.content)
    assert saved is not None and Path(saved.group(1)).read_bytes() == b"%PDF-1.4 data"


def test_an_http_error_is_the_calls_error(tmp_path: Path):
    result = run_call(tmp_path, {"url": "https://example.com/gone", "prompt": "q"}, handler_for({}), Recorder())
    assert result.is_error is True and result.content.startswith("Error fetching https://example.com/gone:")


def test_an_invalid_url_is_refused_by_the_call(tmp_path: Path):
    result = run_call(tmp_path, {"url": "https://localhost/x", "prompt": "q"}, handler_for({}), Recorder())
    assert result.is_error is True and "Invalid URL" in result.content


def test_the_cache_honours_its_ttl_and_size():
    clock = [0.0]
    cache = fetch_utils._UrlCache(ttl_ms=1000, max_size=10, clock=lambda: clock[0])
    entry = FetchedContent("c", 1, 200, "OK", "text/plain")
    cache.set("a", entry, 6)
    cache.set("b", entry, 6)  # over the size limit: the oldest goes
    assert cache.get("a") is None and cache.get("b") is entry
    clock[0] = 2.0
    assert cache.get("b") is None


# ── the pipeline, the prompt, the classifier ─────────────────────────────────

def test_headless_a_non_preapproved_domain_is_denied(tmp_path: Path):
    tool = build_web_fetch_tool(model_for=Recorder(), transport=handler_for({}))
    perm = ToolPermissionContext(mode=PermissionMode.DEFAULT, should_avoid_permission_prompts=True)
    ctx = tool_context_for(AppStateStore(AppState(tool_permission_context=perm)),
                           workspace=LocalWorkspace(str(tmp_path / "p")))
    ctx.tools = (tool,)
    call = ToolCall(id="c1", name="WebFetch", args={"url": "https://example.com/a", "prompt": "q"})
    outcome = asyncio.run(run_tool_use(call, AIMessage(content="", tool_calls=[call]), ctx, (tool,)))
    assert outcome.message.status == "error"
    assert "Permission to use WebFetch has been denied." in outcome.message.content


def test_invalid_input_is_refused_before_the_call():
    tool = build_web_fetch_tool(model_for=Recorder())
    result = tool.validate_input({"url": "nonsense", "prompt": "q"}, tool_context_for(AppStateStore()))
    assert (result.valid, result.error_code) == (False, 1)
    assert result.message == 'Error: Invalid URL "nonsense". The URL provided could not be parsed.'


def test_the_tool_is_read_only_and_described_by_host():
    tool = build_web_fetch_tool(model_for=Recorder())
    assert tool.is_read_only({}) and tool.is_concurrency_safe({})
    assert tool.description({"url": "https://example.com/a"}) == "TDDAgents wants to fetch content from example.com"
    assert to_auto_classifier_input(tool, {"url": "https://x.org", "prompt": "q"}) == "https://x.org: q"


@needs_reference
def test_the_prompt_is_upstreams():
    source = (UPSTREAM / "prompt.ts").read_text()
    description = source.split("export const DESCRIPTION = `", 1)[1].split("`", 1)[0]
    tool_source = (UPSTREAM / "WebFetchTool.ts").read_text()
    head = re.search(r"return `(IMPORTANT: WebFetch WILL FAIL[^\n]*)\n\$\{DESCRIPTION\}`", tool_source)
    assert head is not None
    assert build_web_fetch_tool(model_for=Recorder()).prompt == (head.group(1) + "\n" + description).strip()
