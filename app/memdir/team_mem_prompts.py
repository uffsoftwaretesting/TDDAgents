"""
The memory prompt when team memory is on: a private and a shared directory.

Ported from `reference/claude-code/src/memdir/teamMemPrompts.ts` -> `buildCombinedMemoryPrompt`,
verbatim with this build's tool names.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.memdir.memdir import (
    BUILD_UP_LINES,
    DIRS_EXIST_GUIDANCE,
    PERSISTENCE_SECTION,
    how_to_save_lines,
)
from app.memdir.memory_types import (
    MEMORY_DRIFT_CAVEAT,
    TRUSTING_RECALL_SECTION,
    TYPES_SECTION_COMBINED,
    WHAT_NOT_TO_SAVE_SECTION,
)


def build_combined_memory_prompt(auto_dir: str, team_dir: str, extra_guidelines: Sequence[str] = (),
                                 skip_index: bool = False, past_context: Sequence[str] = ()) -> str:
    """`buildCombinedMemoryPrompt`."""
    lines = [
        "# Memory",
        "",
        (f"You have a persistent, file-based memory system with two directories: a private directory at "
         f"`{auto_dir}` and a shared team directory at `{team_dir}`. {DIRS_EXIST_GUIDANCE}"),
        "",
        *BUILD_UP_LINES,
        "",
        "## Memory scope",
        "",
        "There are two scope levels:",
        "",
        (f"- private: memories that are private between you and the current user. They persist across "
         f"conversations with only this specific user and are stored at the root `{auto_dir}`."),
        (f"- team: memories that are shared with and contributed by all of the users who work within this project "
         f"directory. Team memories are synced at the beginning of every session and they are stored at "
         f"`{team_dir}`."),
        "",
        *TYPES_SECTION_COMBINED,
        *WHAT_NOT_TO_SAVE_SECTION,
        ("- You MUST avoid saving sensitive data within shared team memories. For example, never save API keys or "
         "user credentials."),
        "",
        *how_to_save_lines(skip_index, combined=True),
        "",
        "## When to access memories",
        ("- When memories (personal or team) seem relevant, or the user references prior work with them or others "
         "in their organization."),
        "- You MUST access memory when the user explicitly asks you to check, recall, or remember.",
        ("- If the user says to *ignore* or *not use* memory: proceed as if MEMORY.md were empty. Do not apply "
         "remembered facts, cite, compare against, or mention memory content."),
        MEMORY_DRIFT_CAVEAT,
        "",
        *TRUSTING_RECALL_SECTION,
        "",
        *PERSISTENCE_SECTION,
        *extra_guidelines,
        "",
        *past_context,
    ]
    return "\n".join(lines)
