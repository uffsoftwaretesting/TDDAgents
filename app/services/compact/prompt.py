"""
The compaction request and the summary message that replaces the history.

Ported from `reference/claude-code/src/services/compact/prompt.ts` -> `getCompactPrompt`,
`formatCompactSummary`, `getCompactUserSummaryMessage`, and the summarizer system prompt of
`src/services/compact/compact.ts` -> `streamCompactSummary`.

The prose lives in `app/prompts/compact/*.md` (verbatim upstream text; only the tool names
are a placeholder, rendered from this build's tool names). The one addition is
`tdd_compact_sections.md`, the TDD sections the summary must carry — appended after
upstream's base prompt and before any additional instructions. The summary is still never
trusted for TDD state: the engine re-attaches it from the ledger after the boundary.

Not ported: the partial-compaction prompts (`/compact` around a selected message is an
interactive feature), and the proactive/KAIROS continuation (feature-gated).
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

from app.utils.frontmatter_parser import parse_markdown_frontmatter, render_prompt

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts" / "compact"

#: The tools the no-tools preamble forbids, by this build's names (upstream lists Read,
#: Bash, Grep, Glob, Edit, Write).
COMPACT_FORBIDDEN_TOOLS: tuple[str, ...] = ("ReadFile", "Bash", "Grep", "Glob", "Edit", "WriteFile")


@cache
def _read(name: str) -> str:
    _, body = parse_markdown_frontmatter((PROMPTS_DIR / name).read_text(encoding="utf-8"))
    return body


#: `streamCompactSummary`'s system prompt.
COMPACT_SUMMARIZER_SYSTEM_PROMPT = _read("summarizer_system.md")


def _no_tools_preamble() -> str:
    return render_prompt(_read("no_tools_preamble.md"), {"TOOL_LIST": ", ".join(COMPACT_FORBIDDEN_TOOLS)}) + "\n\n"


def get_compact_prompt(custom_instructions: str | None = None) -> str:
    """`getCompactPrompt`, plus the TDD sections after the base prompt."""
    prompt = _no_tools_preamble() + _read("base_compact_prompt.md") + "\n\n" + _read("tdd_compact_sections.md")
    if custom_instructions and custom_instructions.strip() != "":
        prompt += f"\n\nAdditional Instructions:\n{custom_instructions}"
    prompt += "\n\n" + _read("no_tools_trailer.md")
    return prompt


_ANALYSIS = re.compile(r"<analysis>[\s\S]*?</analysis>")
_SUMMARY = re.compile(r"<summary>([\s\S]*?)</summary>")
_BLANK_RUNS = re.compile(r"\n\n+")


def format_compact_summary(summary: str) -> str:
    """`formatCompactSummary`: drop the analysis scratchpad, unwrap the summary."""
    formatted = _ANALYSIS.sub("", summary, count=1)
    match = _SUMMARY.search(formatted)
    if match:
        content = match.group(1) or ""
        formatted = _SUMMARY.sub(lambda _: f"Summary:\n{content.strip()}", formatted, count=1)
    formatted = _BLANK_RUNS.sub("\n\n", formatted)
    return formatted.strip()


def get_compact_user_summary_message(
    summary: str,
    suppress_follow_up_questions: bool = False,
    transcript_path: str | None = None,
    recent_messages_preserved: bool = False,
) -> str:
    """`getCompactUserSummaryMessage`: what replaces the summarized history."""
    base = f"{_read('summary_intro.md')}\n\n{format_compact_summary(summary)}"
    if transcript_path:
        base += "\n\n" + render_prompt(_read("summary_transcript_note.md"), {"TRANSCRIPT_PATH": transcript_path})
    if recent_messages_preserved:
        base += "\n\n" + _read("summary_recent_preserved.md")
    if suppress_follow_up_questions:
        return f"{base}\n{_read('summary_continuation.md')}"
    return base


__all__ = [
    "COMPACT_FORBIDDEN_TOOLS",
    "COMPACT_SUMMARIZER_SYSTEM_PROMPT",
    "format_compact_summary",
    "get_compact_prompt",
    "get_compact_user_summary_message",
]
