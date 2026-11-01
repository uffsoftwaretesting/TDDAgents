"""
The memory taxonomy and the prompt sections built from it.

Ported from `reference/claude-code/src/memdir/memoryTypes.ts`: `MEMORY_TYPES`, `parseMemoryType`,
`TYPES_SECTION_COMBINED`, `TYPES_SECTION_INDIVIDUAL`, `WHAT_NOT_TO_SAVE_SECTION`,
`MEMORY_DRIFT_CAVEAT`, `WHEN_TO_ACCESS_SECTION`, `TRUSTING_RECALL_SECTION`,
`MEMORY_FRONTMATTER_EXAMPLE`. The texts are upstream's, verbatim, with the product substitution
`CLAUDE.md` -> `TDDAGENTS.md` (pinned against the reference by `tests/test_memdir.py`). Each
section is a Markdown file under `prompts/`, one list item per line (the file ends with one
newline, which is not an item).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

PROMPTS_DIR = Path(__file__).with_name("prompts")

#: `MEMORY_TYPES`.
MEMORY_TYPES: tuple[str, ...] = ("user", "feedback", "project", "reference")

MemoryType = Literal["user", "feedback", "project", "reference"]


def parse_memory_type(raw: object) -> str | None:
    """`parseMemoryType`: a known type, else `None` (legacy or unknown values degrade gracefully)."""
    return raw if isinstance(raw, str) and raw in MEMORY_TYPES else None


def _section(name: str) -> tuple[str, ...]:
    """A section's lines, as upstream's `readonly string[]`."""
    text = (PROMPTS_DIR / name).read_text(encoding="utf-8")
    return tuple(text.removesuffix("\n").split("\n"))


#: `TYPES_SECTION_COMBINED`: the types with `<scope>` guidance (private + team directories).
TYPES_SECTION_COMBINED = _section("types_section_combined.md")
#: `TYPES_SECTION_INDIVIDUAL`: the types for a single directory.
TYPES_SECTION_INDIVIDUAL = _section("types_section_individual.md")
#: `WHAT_NOT_TO_SAVE_SECTION`.
WHAT_NOT_TO_SAVE_SECTION = _section("what_not_to_save_section.md")
#: `MEMORY_DRIFT_CAVEAT`.
MEMORY_DRIFT_CAVEAT = _section("memory_drift_caveat.md")[0]
#: `WHEN_TO_ACCESS_SECTION` (its last item is `MEMORY_DRIFT_CAVEAT`).
WHEN_TO_ACCESS_SECTION = (*_section("when_to_access_section.md"), MEMORY_DRIFT_CAVEAT)
#: `TRUSTING_RECALL_SECTION` (`## Before recommending from memory`).
TRUSTING_RECALL_SECTION = _section("trusting_recall_section.md")

#: `MEMORY_FRONTMATTER_EXAMPLE`.
MEMORY_FRONTMATTER_EXAMPLE: tuple[str, ...] = (
    "```markdown",
    "---",
    "name: {{memory name}}",
    "description: {{one-line description — used to decide relevance in future conversations, so be specific}}",
    f"type: {{{{{', '.join(MEMORY_TYPES)}}}}}",
    "---",
    "",
    ("{{memory content — for feedback/project types, structure as: rule/fact, then **Why:** and **How to apply:** "
     "lines}}"),
    "```",
)
