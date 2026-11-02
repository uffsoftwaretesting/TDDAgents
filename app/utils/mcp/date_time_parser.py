"""
Turning a natural-language date ("tomorrow at 3pm") into ISO 8601 with the small fast model.

Ported from `reference/claude-code/src/utils/mcp/dateTimeParser.ts` ->
`parseNaturalLanguageDateTime`, `looksLikeISO8601`, `DateTimeParseResult`. The prompts are
upstream's, verbatim. `queryHaiku` is injected (`query_haiku`: system prompt, user prompt ->
the answer's text; `app/services/api/claude.py` -> `query_haiku_for`), and so is the clock
(`now`, the local time with its timezone), so the parse is testable offline. The abort signal is
the caller's task: cancelling it cancels the query.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

#: `queryHaiku`: (system prompt, user prompt) -> the answer's text.
QueryHaiku = Callable[[str, str], Awaitable[str]]

UNABLE_TO_PARSE = "Unable to parse date/time from input"
PARSE_FAILED = "Unable to parse date/time. Please enter in ISO 8601 format manually."

SYSTEM_PROMPT = "\n\n".join([
    "You are a date/time parser that converts natural language into ISO 8601 format.",
    "You MUST respond with ONLY the ISO 8601 formatted string, with no explanation or additional text.",
    "If the input is ambiguous, prefer future dates over past dates.",
    "For times without dates, use today's date.",
    "For dates without times, do not include a time component.",
    'If the input is incomplete or you cannot confidently parse it into a valid date, respond with exactly '
    '"INVALID" (nothing else).',
    'Examples of INVALID input: partial dates like "2025-01-", lone numbers like "13", gibberish.',
    'Examples of valid natural language: "tomorrow", "next Monday", "jan 1st 2025", "in 2 hours", "yesterday".',
])

_ISO_8601 = re.compile(r"^\d{4}-\d{2}-\d{2}(T|$)")
_STARTS_WITH_YEAR = re.compile(r"^\d{4}")


@dataclass(frozen=True, slots=True)
class DateTimeParseResult:
    """`DateTimeParseResult`: `value` on success, `error` otherwise."""

    success: bool
    value: str = ""
    error: str = ""


def _timezone(now: datetime) -> str:
    offset = now.utcoffset()
    minutes = int(offset.total_seconds() // 60) if offset is not None else 0
    sign = "+" if minutes >= 0 else "-"
    hours, rest = divmod(abs(minutes), 60)
    return f"{sign}{hours:02d}:{rest:02d}"


def _iso_utc(now: datetime) -> str:
    """`Date.prototype.toISOString`."""
    utc = now.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def build_user_prompt(text: str, fmt: str, now: datetime) -> str:
    """The user prompt: the current time, the timezone, the weekday, the input and the format wanted."""
    tz = _timezone(now)
    format_description = (
        "YYYY-MM-DD (date only, no time)" if fmt == "date"
        else f"YYYY-MM-DDTHH:MM:SS{tz} (full date-time with timezone)")
    return (
        "Current context:\n"
        f"- Current date and time: {_iso_utc(now)} (UTC)\n"
        f"- Local timezone: {tz}\n"
        f"- Day of week: {now.strftime('%A')}\n\n"
        f'User input: "{text}"\n\n'
        f"Output format: {format_description}\n\n"
        "Parse the user's input into ISO 8601 format. Return ONLY the formatted string, or \"INVALID\" if the input "
        "is incomplete or unparseable."
    )


async def parse_natural_language_date_time(
    text: str, fmt: str, query_haiku: QueryHaiku, now: datetime | None = None
) -> DateTimeParseResult:
    """`parseNaturalLanguageDateTime`: the model's ISO string, or why there is none."""
    moment = now if now is not None else datetime.now().astimezone()
    try:
        parsed = (await query_haiku(SYSTEM_PROMPT, build_user_prompt(text, fmt, moment))).strip()
    except Exception as error:  # noqa: BLE001 - upstream logs it and hides the details from the user
        logger.error("Natural-language date parse failed: %s", error)
        return DateTimeParseResult(False, error=PARSE_FAILED)
    if not parsed or parsed == "INVALID" or not _STARTS_WITH_YEAR.match(parsed):
        return DateTimeParseResult(False, error=UNABLE_TO_PARSE)
    return DateTimeParseResult(True, value=parsed)


def looks_like_iso_8601(text: str) -> bool:
    """`looksLikeISO8601`: `YYYY-MM-DD`, alone or followed by `T`."""
    return _ISO_8601.match(text.strip()) is not None


__all__ = [
    "PARSE_FAILED",
    "SYSTEM_PROMPT",
    "UNABLE_TO_PARSE",
    "DateTimeParseResult",
    "QueryHaiku",
    "build_user_prompt",
    "looks_like_iso_8601",
    "parse_natural_language_date_time",
]
