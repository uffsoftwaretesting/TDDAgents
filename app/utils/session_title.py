"""
A short title for a session, asked of the small fast model.

Ported from `reference/claude-code/src/utils/sessionTitle.ts` -> `MAX_CONVERSATION_TEXT`,
`extractConversationText`, `SESSION_TITLE_PROMPT` (verbatim), `generateSessionTitle`. The SDK's
`generate_session_title` control request answers with it, and with `persist` the title is saved in
the transcript (`saveAiGeneratedTitle`, `app/utils/session_storage.py`).

Divergence: upstream asks with `outputFormat: json_schema` (structured output); TDDAgents'
`queryHaiku` has no such option, so the answer's JSON is parsed leniently from the text (the first
`{...}` in it). Any failure is `None`, as upstream's.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

logger = logging.getLogger(__name__)

#: `MAX_CONVERSATION_TEXT`: the tail of the conversation a title is made from.
MAX_CONVERSATION_TEXT = 1000

SESSION_TITLE_PROMPT = """Generate a concise, sentence-case title (3-7 words) that captures the main topic or goal of this coding session. The title should be clear enough that the user recognizes the session in a list. Use sentence case: capitalize only the first word and proper nouns.

Return JSON with a single "title" field.

Good examples:
{"title": "Fix login button on mobile"}
{"title": "Add OAuth authentication"}
{"title": "Debug failing CI tests"}
{"title": "Refactor API client error handling"}

Bad (too vague): {"title": "Code changes"}
Bad (too long): {"title": "Investigate and fix the issue where the login button does not respond on mobile devices"}
Bad (wrong case): {"title": "Fix Login Button On Mobile"}"""  # noqa: E501

#: `queryHaiku`: (system prompt, user prompt) -> the answer's text.
QueryHaiku = Callable[[str, str], Awaitable[str]]


def extract_conversation_text(messages: Sequence[Any]) -> str:
    """`extractConversationText`: the user's and the assistant's text (no meta), its last 1000 characters."""
    parts: list[str] = []
    for message in messages:
        if getattr(message, "type", None) not in ("human", "ai"):
            continue
        if (getattr(message, "additional_kwargs", None) or {}).get("is_meta"):
            continue
        content = message.content
        if isinstance(content, str):
            parts.append(content)
        else:
            parts.extend(str(block.get("text", "")) for block in content
                         if isinstance(block, dict) and block.get("type") == "text" and "text" in block)
    text = "\n".join(parts)
    return text[-MAX_CONVERSATION_TEXT:] if len(text) > MAX_CONVERSATION_TEXT else text


def parse_title(text: str) -> str | None:
    """`titleSchema().safeParse(safeParseJSON(text))`: the trimmed `title`, or `None`."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        data = json.loads(match.group(0) if match else text)
    except ValueError:
        return None
    title = data.get("title") if isinstance(data, dict) else None
    return title.strip() or None if isinstance(title, str) else None


async def generate_session_title(description: str, query_haiku: QueryHaiku) -> str | None:
    """`generateSessionTitle`: the title for `description`; `None` when empty or on any failure."""
    trimmed = description.strip()
    if not trimmed:
        return None
    try:
        return parse_title(await query_haiku(SESSION_TITLE_PROMPT, trimmed))
    except Exception as error:  # noqa: BLE001 - upstream logs and returns null
        logger.error("generateSessionTitle failed: %s", error)
        return None


__all__ = [
    "MAX_CONVERSATION_TEXT",
    "SESSION_TITLE_PROMPT",
    "extract_conversation_text",
    "generate_session_title",
    "parse_title",
]
