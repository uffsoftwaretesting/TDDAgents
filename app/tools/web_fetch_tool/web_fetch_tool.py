"""
WebFetch: fetch a URL, convert it to markdown, and answer a prompt about it with a small model.

Ported from `reference/claude-code/src/tools/WebFetchTool/WebFetchTool.ts`: `WebFetchTool`
(`checkPermissions`, `validateInput`, `call`, `mapToolResultToToolResultBlockParam`),
`webFetchToolInputToPermissionRuleContent` and `buildSuggestions`.

Permissions are by domain: a preapproved code-documentation host is allowed, otherwise a
`WebFetch(domain:<host>)` deny, ask or allow rule decides, and with none the call asks (denied
headless). The page is processed by `TDDAGENTS_SMALL_FAST_MODEL` (upstream's Haiku), else the
run's model, through `with_retry`, unless it is preapproved markdown short enough to return as is.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.tool import BuiltTool, ToolContext, ToolResult, ValidationResult, build_tool
from app.tools.utils import get_prompt
from app.tools.web_fetch_tool.preapproved import is_preapproved_host
from app.tools.web_fetch_tool.utils import (
    MAX_MARKDOWN_LENGTH,
    FetchedContent,
    RedirectInfo,
    SecondaryModel,
    apply_prompt_to_markdown,
    get_url_markdown_content,
    is_preapproved_url,
)
from app.types.permissions import (
    PermissionBehavior,
    PermissionResult,
    PermissionRule,
    PermissionRuleSource,
)
from app.utils.mcp_validation import persist_binary_content
from app.utils.permissions.filesystem import permission_cwd
from app.utils.permissions.permission_update import PermissionUpdate
from app.utils.permissions.permissions import extract_permission_context
from app.utils.tool_result_storage import format_file_size

WEB_FETCH_TOOL_NAME = "WebFetch"
PROMPT_PATH = Path(__file__).with_name("prompt.md")

_REDIRECT_TEXT = {301: "Moved Permanently", 308: "Permanent Redirect", 307: "Temporary Redirect"}

#: How the tool reaches the small, fast model for a context: `(context) -> (prompt) -> answer`.
ModelFor = Callable[[ToolContext], SecondaryModel]


def permission_rule_content(input_args: dict[str, Any]) -> str:
    """`webFetchToolInputToPermissionRuleContent`: `domain:<host>`, or `input:<…>` when unparseable."""
    try:
        hostname = urlsplit(str(input_args["url"])).hostname
    except (KeyError, ValueError):
        hostname = None
    return f"domain:{hostname}" if hostname else f"input:{input_args}"


def _rule(rules: tuple[PermissionRule, ...], content: str) -> PermissionRule | None:
    """`getRuleByContentsForTool(...).get(content)`: an exact `WebFetch(<content>)` rule."""
    return next((r for r in rules if r.tool_name == WEB_FETCH_TOOL_NAME and r.rule_content == content), None)


def build_suggestions(rule_content: str) -> tuple[PermissionUpdate, ...]:
    """`buildSuggestions`: allow this domain in the local settings."""
    return (PermissionUpdate("addRules", PermissionRuleSource.LOCAL_SETTINGS,
                             rules=((WEB_FETCH_TOOL_NAME, rule_content),), behavior=PermissionBehavior.ALLOW),)


def _asked(rule_content: str, reason: dict[str, Any] | None = None) -> PermissionResult:
    return PermissionResult(
        behavior=PermissionBehavior.ASK,
        message=f"TDDAgents requested permissions to use {WEB_FETCH_TOOL_NAME}, but you haven't granted it yet.",
        decision_reason=reason, suggestions=build_suggestions(rule_content))


def check_web_fetch_permissions(input_args: dict[str, Any], context: ToolContext) -> PermissionResult:
    """`WebFetchTool.checkPermissions`."""
    try:
        parts = urlsplit(str(input_args.get("url") or ""))
        if parts.hostname and is_preapproved_host(parts.hostname, parts.path or "/"):
            return PermissionResult(behavior=PermissionBehavior.ALLOW, updated_input=input_args,
                                    decision_reason={"type": "other", "reason": "Preapproved host"})
    except ValueError:
        pass
    content = permission_rule_content(input_args)
    perm = extract_permission_context(context)
    deny = _rule(perm.always_deny_rules, content)
    if deny is not None:
        return PermissionResult(behavior=PermissionBehavior.DENY,
                                message=f"{WEB_FETCH_TOOL_NAME} denied access to {content}.",
                                decision_reason={"type": "rule", "rule": deny})
    ask = _rule(perm.always_ask_rules, content)
    if ask is not None:
        return _asked(content, {"type": "rule", "rule": ask})
    allow = _rule(perm.always_allow_rules, content)
    if allow is not None:
        return PermissionResult(behavior=PermissionBehavior.ALLOW, updated_input=input_args,
                                decision_reason={"type": "rule", "rule": allow})
    return _asked(content)


def validate_web_fetch(input_args: dict[str, Any], context: ToolContext) -> ValidationResult:
    """`WebFetchTool.validateInput`: the URL must parse."""
    url = str(input_args.get("url") or "")
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return ValidationResult(valid=False, error_code=1,
                                message=f'Error: Invalid URL "{url}". The URL provided could not be parsed.')
    return ValidationResult(valid=True)


def redirect_message(redirect: RedirectInfo, prompt: str) -> str:
    """The text `call` returns for a redirect to another host."""
    status = _REDIRECT_TEXT.get(redirect.status_code, "Found")
    return f"""REDIRECT DETECTED: The URL redirects to a different host.

Original URL: {redirect.original_url}
Redirect URL: {redirect.redirect_url}
Status: {redirect.status_code} {status}

To complete your request, I need to fetch content from the redirected URL. Please use WebFetch again with these parameters:
- url: "{redirect.redirect_url}"
- prompt: "{prompt}\""""  # noqa: E501 - upstream's text, verbatim


def default_model_for(context: ToolContext) -> SecondaryModel:
    """
    The small, fast model on this repo's model seam (`queryHaiku`): the prompt hooks' caller with
    `get_small_fast_model`, through `with_retry`. **Exempt like the seam it wraps.**
    """
    from langchain_core.messages import HumanMessage

    from app.config.config import Config
    from app.services.api.with_retry import RetryOptions, with_retry
    from app.utils.hooks.exec_prompt_hook import (
        build_prompt_model_caller,
        get_small_fast_model,
    )
    from app.utils.model.model import provider_for_model

    model = get_small_fast_model(str(getattr(context, "main_loop_model", None) or Config.MODEL))
    caller = build_prompt_model_caller(provider_for_model(model, default=Config.CHAT_MODEL))

    async def ask(prompt: str) -> str:  # pragma: no cover - live provider
        async def attempt(_attempt: int, _retry: Any) -> str:
            return await caller([HumanMessage(content=prompt)], "", model, {})

        result: str = await with_retry(attempt, RetryOptions(model=model))
        return result

    return ask


def build_web_fetch_tool(*, model_for: ModelFor = default_model_for,
                         transport: httpx.AsyncBaseTransport | None = None,
                         now: Callable[[], float] = time.monotonic) -> BuiltTool:
    async def call(input_args: dict[str, Any], context: ToolContext) -> ToolResult:
        url, prompt = str(input_args.get("url") or ""), str(input_args.get("prompt") or "")
        start = now()

        def persist(data: bytes, content_type: str, persist_id: str) -> tuple[Path, int]:
            return persist_binary_content(data, content_type, persist_id, run_id=context.run_id or "session",
                                          cwd=permission_cwd(context))

        try:
            response = await get_url_markdown_content(url, transport=transport, persist_binary=persist)
        except Exception as exc:  # noqa: BLE001 - upstream's call throws: the call's error result
            return ToolResult(content=f"Error fetching {url}: {exc}", is_error=True)
        if isinstance(response, RedirectInfo):
            message = redirect_message(response, prompt)
            return ToolResult(content=message, metadata={"code": response.status_code, "url": url,
                                                         "bytes": len(message.encode("utf-8"))})
        fetched: FetchedContent = response
        preapproved = is_preapproved_url(url)
        if preapproved and "text/markdown" in fetched.content_type and len(fetched.content) < MAX_MARKDOWN_LENGTH:
            result = fetched.content
        else:
            result = await apply_prompt_to_markdown(prompt, fetched.content, preapproved, model_for(context))
        if fetched.persisted_path:
            result += (f"\n\n[Binary content ({fetched.content_type}, "
                       f"{format_file_size(fetched.persisted_size or fetched.bytes)}) also saved to "
                       f"{fetched.persisted_path}]")
        return ToolResult(content=result, metadata={
            "bytes": fetched.bytes, "code": fetched.code, "codeText": fetched.code_text,
            "durationMs": int((now() - start) * 1000), "url": url})

    def describe(input_args: dict[str, Any]) -> str:
        hostname = urlsplit(str(input_args.get("url") or "")).hostname
        return f"TDDAgents wants to fetch content from {hostname}" if hostname else \
            "TDDAgents wants to fetch content from this URL"

    return build_tool(
        name=WEB_FETCH_TOOL_NAME,
        prompt=get_prompt(PROMPT_PATH, "Fetches a URL."),
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "format": "uri", "description": "The URL to fetch content from"},
                "prompt": {"type": "string", "description": "The prompt to run on the fetched content"},
            },
            "required": ["url", "prompt"],
            "additionalProperties": False,
        },
        description=describe,
        is_read_only=lambda args: True,
        is_concurrency_safe=lambda args: True,
        validate_input=validate_web_fetch,
        check_permissions=check_web_fetch_permissions,
        call=call,
        # `maxResultSizeChars`: 100K chars, the persistence threshold.
        max_result_size_chars=100_000,
    )


__all__ = [
    "WEB_FETCH_TOOL_NAME",
    "build_suggestions",
    "build_web_fetch_tool",
    "check_web_fetch_permissions",
    "permission_rule_content",
    "redirect_message",
    "validate_web_fetch",
]
