"""
Picking the memories worth surfacing for a prompt.

Ported from `reference/claude-code/src/memdir/findRelevantMemories.ts` -> `RelevantMemory`,
`SELECT_MEMORIES_SYSTEM_PROMPT`, `findRelevantMemories`, `selectRelevantMemories`. The directory's
headers are listed (`scan_memory_files`), already surfaced ones are left out, and a side query
picks up to five by name and description. The selector is injected (`Select`): the default is the
small fast model through this repo's model seam, whose reply is parsed as upstream's
`{"selected_memories": [...]}` JSON. A failure or an unparsable reply selects nothing.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from app.memdir.memory_scan import format_memory_manifest, scan_memory_files

logger = logging.getLogger(__name__)

#: `SELECT_MEMORIES_SYSTEM_PROMPT`, with the product name.
SELECT_MEMORIES_SYSTEM_PROMPT = (
    "You are selecting memories that will be useful to TDDAgents as it processes a user's query. You will be "
    "given the user's query and a list of available memory files with their filenames and descriptions.\n\n"
    "Return a list of filenames for the memories that will clearly be useful to TDDAgents as it processes the "
    "user's query (up to 5). Only include memories that you are certain will be helpful based on their name and "
    "description.\n"
    "- If you are unsure if a memory will be useful in processing the user's query, then do not include it in your "
    "list. Be selective and discerning.\n"
    "- If there are no memories in the list that would clearly be useful, feel free to return an empty list.\n"
    "- If a list of recently-used tools is provided, do not select memories that are usage reference or API "
    "documentation for those tools (TDDAgents is already exercising them). DO still select memories containing "
    "warnings, gotchas, or known issues about those tools — active use is exactly when those matter.\n"
)
#: The JSON shape upstream asks for with `output_format`, stated in the request instead.
OUTPUT_INSTRUCTION = 'Reply with JSON only: {"selected_memories": ["<filename>", ...]}'

#: (system prompt, user message) -> the model's text reply.
Select = Callable[[str, str], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class RelevantMemory:
    """`RelevantMemory`."""

    path: str
    mtime_ms: float


def _parse_selection(text: str) -> list[str]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        return []
    selected = json.loads(match.group(0)).get("selected_memories")
    return [str(name) for name in selected] if isinstance(selected, list) else []


async def find_relevant_memories(
    query: str, memory_dir: str, select: Select, recent_tools: Sequence[str] = (),
    already_surfaced: frozenset[str] = frozenset(),
) -> list[RelevantMemory]:
    """`findRelevantMemories`: up to five memories the selector picked, in its order."""
    memories = [m for m in scan_memory_files(memory_dir) if m.file_path not in already_surfaced]
    if not memories:
        return []
    tools = f"\n\nRecently used tools: {', '.join(recent_tools)}" if recent_tools else ""
    user = f"Query: {query}\n\nAvailable memories:\n{format_memory_manifest(memories)}{tools}\n\n{OUTPUT_INSTRUCTION}"
    try:
        names = _parse_selection(await select(SELECT_MEMORIES_SYSTEM_PROMPT, user))
    except Exception as exc:  # noqa: BLE001 - upstream's catch: a failed selection selects nothing
        logger.warning("[memdir] selectRelevantMemories failed: %s", exc)
        return []
    by_name = {m.filename: m for m in memories}
    return [RelevantMemory(by_name[n].file_path, by_name[n].mtime_ms) for n in names if n in by_name]
