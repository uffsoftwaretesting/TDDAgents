"""
WebSearch and WebFetch — the researcher's window onto anything outside this machine.

Both are **disabled unless configured**: `WebSearch` needs `TAVILY_API_KEY`, and each
reports itself unavailable through `is_enabled()` rather than failing at call time. A tool
that is not enabled never reaches `bind_tools`, so a model is never shown a capability it
cannot actually use — which is a different and better failure mode than an error the model
has to learn to stop retrying.

Tavily is used through a plain `httpx` POST rather than an SDK: the request is one JSON
body and the response is already reduced to clean text, so a dependency would buy nothing.
`WebFetch` does need real parsing — raw HTML is close to useless to a model — so it uses
beautifulsoup4 and markdownify.

Neither tool touches a Workspace, and neither can write anything.
"""

from __future__ import annotations

import logging

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify
from pydantic import BaseModel, Field

from app.config.config import Config
from app.tools.base import (
    AnyTool,
    Capability,
    ToolContext,
    ToolResult,
    build_tool,
    err,
    ok,
)

logger = logging.getLogger("TDDOrchestrator.Tools")

TAVILY_ENDPOINT = "https://api.tavily.com/search"
WEB_TIMEOUT = 30.0
MAX_FETCH_BYTES = 5_000_000

#: Stripped before conversion — none of it is content, and all of it is expensive.
NON_CONTENT_TAGS = ("script", "style", "noscript", "nav", "header", "footer", "iframe",
                    "svg", "aside", "form")

#: Many documentation themes build their sidebar out of plain `<div>`s, so tag names alone
#: miss it. A live fetch of the pytest docs came back over half navigation before this.
NON_CONTENT_ROLES = ("navigation", "banner", "contentinfo", "search", "complementary")


class WebSearchArgs(BaseModel):
    query: str = Field(description="What to search for.")
    max_results: int = Field(default=5, description="How many results to return (1-10).")


def _web_search(args: WebSearchArgs, ctx: ToolContext) -> ToolResult:
    payload = {
        "api_key": Config.TAVILY_API_KEY,
        "query": args.query,
        "max_results": max(1, min(args.max_results, 10)),
        "search_depth": "basic",
        "include_answer": True,
    }

    try:
        response = httpx.post(TAVILY_ENDPOINT, json=payload, timeout=WEB_TIMEOUT)
        response.raise_for_status()
        document = response.json()
    except httpx.HTTPStatusError as exc:
        return err(f"Search failed: the provider returned {exc.response.status_code}.")
    except httpx.HTTPError as exc:
        return err(f"Search failed: {exc}")
    except ValueError:
        return err("Search failed: the provider returned malformed JSON.")

    return ok(_render_search(args.query, document))


def _render_search(query: str, document: object) -> str:
    if not isinstance(document, dict):
        return f"No results for '{query}'."

    parts: list[str] = []
    answer = document.get("answer")
    if isinstance(answer, str) and answer.strip():
        parts.append(f"Summary: {answer.strip()}")

    results = document.get("results")
    entries = results if isinstance(results, list) else []
    # Numbered by what is actually rendered, not by position in the raw list: skipping a
    # malformed entry must not leave a hole in the numbering the model then puzzles over.
    rendered = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        rendered += 1
        title = str(entry.get("title", "(untitled)"))
        url = str(entry.get("url", ""))
        snippet = str(entry.get("content", "")).strip()
        parts.append(f"{rendered}. {title}\n   {url}\n   {snippet}")

    if not parts:
        return f"No results for '{query}'."
    return "\n\n".join(parts)


WebSearch = build_tool(
    name="WebSearch",
    args_schema=WebSearchArgs,
    prompt=(
        "Searches the web and returns ranked results as title, URL and an extracted "
        "snippet, usually with a short synthesized summary first. Use it to find "
        "documentation, API references, or current practice you do not already know. "
        "Follow up with WebFetch when a result looks worth reading in full."
    ),
    call=_web_search,
    description=lambda args: f"Search: {args.query[:60]}",
    is_enabled=lambda: bool(Config.TAVILY_API_KEY),
    is_read_only=lambda args: True,
    is_concurrency_safe=lambda args: True,
    required_capability=lambda args: Capability.READ,
)


class WebFetchArgs(BaseModel):
    url: str = Field(description="The absolute http(s) URL to fetch.")


def _web_fetch(args: WebFetchArgs, ctx: ToolContext) -> ToolResult:
    if not args.url.lower().startswith(("http://", "https://")):
        return err(f"Only http and https URLs are supported; got {args.url}.")

    try:
        response = httpx.get(
            args.url,
            timeout=WEB_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "TDDAgents/1.0"},
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return err(f"Fetch failed: {exc.response.status_code} for {args.url}.")
    except httpx.HTTPError as exc:
        return err(f"Fetch failed: {exc}")

    if len(response.content) > MAX_FETCH_BYTES:
        return err(f"{args.url} is larger than {MAX_FETCH_BYTES} bytes; refusing to fetch it.")

    content_type = response.headers.get("content-type", "")
    if "html" not in content_type.lower():
        # Plain text, JSON, or source: it is already readable, so hand it over untouched.
        return ok(response.text)

    return ok(html_to_markdown(response.text))


def html_to_markdown(html: str) -> str:
    """
    Reduces an HTML page to Markdown a model can actually read.

    Navigation, scripts and styling are removed first: they are the bulk of a modern page
    and none of it is content, so converting them would spend the context window on chrome.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(list(NON_CONTENT_TAGS)):
        tag.decompose()
    for tag in soup.find_all(None, attrs={"role": lambda value: value in NON_CONTENT_ROLES}):
        tag.decompose()

    body = soup.body or soup
    markdown = markdownify(str(body), heading_style="ATX")

    # markdownify leaves long runs of blank lines where the stripped tags used to be.
    lines = [line.rstrip() for line in markdown.splitlines()]
    collapsed: list[str] = []
    for line in lines:
        if not line and collapsed and not collapsed[-1]:
            continue
        collapsed.append(line)
    return "\n".join(collapsed).strip()


WebFetch = build_tool(
    name="WebFetch",
    args_schema=WebFetchArgs,
    prompt=(
        "Fetches a URL and returns its content as Markdown, with navigation, scripts and "
        "styling stripped. Non-HTML responses (plain text, JSON) are returned as-is.\n\n"
        "Read-only: it cannot submit forms or send data anywhere."
    ),
    call=_web_fetch,
    description=lambda args: f"Fetch {args.url[:60]}",
    is_read_only=lambda args: True,
    is_concurrency_safe=lambda args: True,
    required_capability=lambda args: Capability.READ,
)


WEB_TOOLS: list[AnyTool] = [WebSearch, WebFetch]
