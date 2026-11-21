"""
Fetching a URL as markdown and applying a prompt to it.

Ported from `reference/claude-code/src/tools/WebFetchTool/utils.ts`: `validateURL`,
`isPermittedRedirect`, `getWithPermittedRedirects`, `getURLMarkdownContent`, `isPreapprovedUrl`,
`applyPromptToMarkdown`, `EgressBlockedError`, the URL cache and its limits; `prompt.ts` ->
`makeSecondaryModelPrompt`; `utils/mcpOutputStorage.ts` -> `isBinaryContentType`. `parse_url` stands in for
the WHATWG `new URL(url)` every one of them starts from.

httpx stands in for axios (no automatic redirects, a timeout, a byte cap) and markdownify for
turndown. *Divergence:* the domain-blocklist preflight to Anthropic's `domain_info` service is not
ported (user decision 2026-10-28); `skipWebFetchPreflight` therefore has nothing to skip.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from markdownify import markdownify

from app.tools.web_fetch_tool.preapproved import is_preapproved_host
from app.utils.user_agent import get_tddagents_user_agent

#: Truncate to not spend too many tokens.
MAX_MARKDOWN_LENGTH = 100_000
MAX_URL_LENGTH = 2000
#: Resource consumption control on one response.
MAX_HTTP_CONTENT_LENGTH = 10 * 1024 * 1024
FETCH_TIMEOUT_MS = 60_000
#: Same-host redirect hops, so a redirect loop cannot hang the tool.
MAX_REDIRECTS = 10
CACHE_TTL_MS = 15 * 60 * 1000
MAX_CACHE_SIZE_BYTES = 50 * 1024 * 1024
#: `getWebFetchUserAgent` (`Claude-User (<agent>; +<support URL>)`), with this product's name and no URL.
USER_AGENT = f"TDDAgents-User ({get_tddagents_user_agent()})"

#: `(user prompt, model) -> text`: the small, fast model's answer (`queryHaiku`).
SecondaryModel = Callable[[str], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class ParsedUrl:
    """The parts of a WHATWG `URL` WebFetch reads: `protocol` without its colon, `hostname`, `port` ('' for the
    scheme's default), `username`, `password` and `pathname`."""

    scheme: str
    hostname: str
    port: str
    username: str
    password: str
    pathname: str


#: The WHATWG special schemes and their default ports (`URL.port` is '' for them).
_SPECIAL_SCHEMES: dict[str, int | None] = {"http": 80, "https": 443, "ws": 80, "wss": 443, "ftp": 21, "file": None}
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*")


def _whatwg_hostname(host: str) -> str | None:
    """`URL.hostname`: lower case, an IPv6 address in brackets, a non-ASCII name in punycode (IDNA)."""
    if ":" in host:
        return f"[{host}]"
    try:
        return host.encode("idna").decode("ascii").lower() if host else ""
    except UnicodeError:
        return None


def parse_url(url: str) -> ParsedUrl | None:
    """
    `new URL(url)` as WebFetch reads it, `None` where it throws: a scheme is required, a special scheme other
    than `file` needs a host, the port must be a number, and the hostname must encode. Leading and trailing
    spaces are trimmed, as the URL parser does.
    """
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except (ValueError, TypeError, AttributeError):
        return None
    scheme = parts.scheme.lower()
    if not scheme or not _SCHEME.fullmatch(scheme):
        return None
    special = scheme in _SPECIAL_SCHEMES
    raw_host = parts.hostname or ""
    if special and scheme != "file" and not raw_host:
        return None
    hostname = _whatwg_hostname(raw_host)
    if hostname is None:
        return None
    default_port = _SPECIAL_SCHEMES.get(scheme)
    return ParsedUrl(scheme=scheme, hostname=hostname,
                     port="" if port is None or port == default_port else str(port),
                     username=parts.username or "", password=parts.password or "",
                     pathname=parts.path or ("/" if special else ""))


class EgressBlockedError(RuntimeError):
    """`EgressBlockedError`: the egress proxy refused the host (`403` with `X-Proxy-Error: blocked-by-allowlist`)."""

    def __init__(self, domain: str) -> None:
        super().__init__(json.dumps({"error_type": "EGRESS_BLOCKED", "domain": domain,
                                     "message": f"Access to {domain} is blocked by the network egress proxy."},
                                    separators=(",", ":"), ensure_ascii=False))
        self.domain = domain


@dataclass(frozen=True, slots=True)
class RedirectInfo:
    original_url: str
    redirect_url: str
    status_code: int


@dataclass(frozen=True, slots=True)
class FetchedContent:
    content: str
    bytes: int
    code: int
    code_text: str
    content_type: str
    persisted_path: str | None = None
    persisted_size: int | None = None


class _UrlCache:
    """`URL_CACHE`: an LRU keyed by the requested URL, with a TTL and a total size limit."""

    def __init__(self, ttl_ms: int = CACHE_TTL_MS, max_size: int = MAX_CACHE_SIZE_BYTES,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl = ttl_ms / 1000
        self.max_size = max_size
        self.clock = clock
        self._entries: OrderedDict[str, tuple[float, int, FetchedContent]] = OrderedDict()

    def get(self, url: str) -> FetchedContent | None:
        found = self._entries.get(url)
        if found is None:
            return None
        stored_at, _, entry = found
        if self.clock() - stored_at > self.ttl:
            del self._entries[url]
            return None
        self._entries.move_to_end(url)
        return entry

    def set(self, url: str, entry: FetchedContent, size: int) -> None:
        self._entries[url] = (self.clock(), max(1, size), entry)
        self._entries.move_to_end(url)
        while sum(s for _, s, _ in self._entries.values()) > self.max_size and len(self._entries) > 1:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()


URL_CACHE = _UrlCache()


def clear_web_fetch_cache() -> None:
    """`clearWebFetchCache`."""
    URL_CACHE.clear()


def is_preapproved_url(url: str) -> bool:
    """`isPreapprovedUrl`: `isPreapprovedHost(hostname, pathname)`, false when the URL does not parse."""
    parsed = parse_url(url)
    return parsed is not None and is_preapproved_host(parsed.hostname, parsed.pathname)


def validate_url(url: str) -> bool:
    """`validateURL`: parseable, not too long, no credentials, and a dotted (public) hostname."""
    if len(url) > MAX_URL_LENGTH:
        return False
    parsed = parse_url(url)
    if parsed is None:
        return False
    if parsed.username or parsed.password:
        return False
    return len(parsed.hostname.split(".")) >= 2


def is_permitted_redirect(original_url: str, redirect_url: str) -> bool:
    """`isPermittedRedirect`: same protocol, port (a default port is '') and host, `www.` added or removed
    allowed, no credentials in the redirect."""
    original, redirect = parse_url(original_url), parse_url(redirect_url)
    if original is None or redirect is None:
        return False
    if redirect.scheme != original.scheme or redirect.port != original.port:
        return False
    if redirect.username or redirect.password:
        return False
    return original.hostname.removeprefix("www.") == redirect.hostname.removeprefix("www.")


def is_binary_content_type(content_type: str) -> bool:
    """`isBinaryContentType`: anything but text, JSON, XML, JavaScript and form data."""
    if not content_type:
        return False
    media = content_type.split(";")[0].strip().lower()
    if media.startswith("text/"):
        return False
    if media.endswith("+json") or media == "application/json":
        return False
    if media.endswith("+xml") or media == "application/xml":
        return False
    if media.startswith("application/javascript"):
        return False
    return media != "application/x-www-form-urlencoded"


async def get_with_permitted_redirects(client: httpx.AsyncClient, url: str,
                                       redirect_checker: Callable[[str, str], bool] = is_permitted_redirect,
                                       depth: int = 0) -> httpx.Response | RedirectInfo:
    """`getWithPermittedRedirects`: follow permitted redirects only; report any other."""
    if depth > MAX_REDIRECTS:
        raise RuntimeError(f"Too many redirects (exceeded {MAX_REDIRECTS})")
    response = await client.get(url, headers={"Accept": "text/markdown, text/html, */*", "User-Agent": USER_AGENT})
    if response.status_code in (301, 302, 307, 308):
        location = response.headers.get("location")
        if not location:
            raise RuntimeError("Redirect missing Location header")
        redirect_url = urljoin(url, location)
        if redirect_checker(url, redirect_url):
            return await get_with_permitted_redirects(client, redirect_url, redirect_checker, depth + 1)
        return RedirectInfo(url, redirect_url, response.status_code)
    if response.status_code == 403 and response.headers.get("x-proxy-error") == "blocked-by-allowlist":
        parsed = parse_url(url)
        raise EgressBlockedError(parsed.hostname if parsed is not None else url)
    response.raise_for_status()
    if len(response.content) > MAX_HTTP_CONTENT_LENGTH:
        raise RuntimeError(f"Response exceeds the maximum content length of {MAX_HTTP_CONTENT_LENGTH} bytes")
    return response


def _upgrade(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(scheme="https")) if parts.scheme == "http" else url


async def get_url_markdown_content(
    url: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    persist_binary: Callable[[bytes, str, str], tuple[Path, int]] | None = None,
) -> FetchedContent | RedirectInfo:
    """`getURLMarkdownContent`: the URL as markdown, from the cache when fresh."""
    if not validate_url(url):
        raise ValueError("Invalid URL")
    cached = URL_CACHE.get(url)
    if cached is not None:
        return cached
    async with httpx.AsyncClient(transport=transport, timeout=FETCH_TIMEOUT_MS / 1000,
                                 follow_redirects=False) as client:
        response = await get_with_permitted_redirects(client, _upgrade(url))
    if isinstance(response, RedirectInfo):
        return response
    raw = response.content
    content_type = response.headers.get("content-type", "")
    persisted_path: str | None = None
    persisted_size: int | None = None
    if is_binary_content_type(content_type) and persist_binary is not None:
        try:
            path, size = persist_binary(raw, content_type, f"webfetch-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}")
            persisted_path, persisted_size = str(path), size
        except OSError:
            pass
    text = raw.decode("utf-8", errors="replace")
    if "text/html" in content_type:
        markdown = markdownify(text)
        size = len(markdown.encode("utf-8"))
    else:
        markdown, size = text, len(raw)
    entry = FetchedContent(markdown, len(raw), response.status_code, response.reason_phrase, content_type,
                           persisted_path, persisted_size)
    # Stored under the requested URL, not the upgraded or redirected one.
    URL_CACHE.set(url, entry, size)
    return entry


def make_secondary_model_prompt(markdown_content: str, prompt: str, is_preapproved_domain: bool) -> str:
    """`makeSecondaryModelPrompt`."""
    if is_preapproved_domain:
        guidelines = ("Provide a concise response based on the content above. Include relevant details, code "
                      "examples, and documentation excerpts as needed.")
    else:
        guidelines = """Provide a concise response based only on the content above. In your response:
 - Enforce a strict 125-character maximum for quotes from any source document. Open Source Software is ok as long as we respect the license.
 - Use quotation marks for exact language from articles; any language outside of the quotation should never be word-for-word the same.
 - You are not a lawyer and never comment on the legality of your own prompts and responses.
 - Never produce or reproduce exact song lyrics."""  # noqa: E501 - upstream's text, verbatim
    return f"""
Web page content:
---
{markdown_content}
---

{prompt}

{guidelines}
"""


async def apply_prompt_to_markdown(prompt: str, markdown_content: str, is_preapproved_domain: bool,
                                   model: SecondaryModel) -> str:
    """`applyPromptToMarkdown`: truncate, then ask the small, fast model."""
    truncated = (markdown_content[:MAX_MARKDOWN_LENGTH] + "\n\n[Content truncated due to length...]"
                 if len(markdown_content) > MAX_MARKDOWN_LENGTH else markdown_content)
    answer = await model(make_secondary_model_prompt(truncated, prompt, is_preapproved_domain))
    return answer or "No response from model"


__all__ = [
    "MAX_MARKDOWN_LENGTH",
    "URL_CACHE",
    "EgressBlockedError",
    "FetchedContent",
    "ParsedUrl",
    "RedirectInfo",
    "SecondaryModel",
    "apply_prompt_to_markdown",
    "clear_web_fetch_cache",
    "get_url_markdown_content",
    "get_with_permitted_redirects",
    "is_binary_content_type",
    "is_permitted_redirect",
    "is_preapproved_url",
    "make_secondary_model_prompt",
    "parse_url",
    "validate_url",
]
