"""
The auto-memory prompt: how to save typed memories, and the MEMORY.md index.

Ported from `reference/claude-code/src/memdir/memdir.ts` -> `ENTRYPOINT_NAME`,
`MAX_ENTRYPOINT_LINES`, `MAX_ENTRYPOINT_BYTES`, `EntrypointTruncation`, `truncateEntrypointContent`,
`DIR_EXISTS_GUIDANCE`, `DIRS_EXIST_GUIDANCE`, `ensureMemoryDirExists`, `buildMemoryLines`,
`buildMemoryPrompt`, `buildSearchingPastContextSection`, `loadMemoryPrompt`. Texts verbatim, with
this build's tool names (`WriteFile`, `Grep`).

Upstream's remote flags become env opt-ins, off by default as the flags are:
`TDDAGENTS_MEMORY_RELEVANCE` (`tengu_moth_copse`: the relevance search, which also drops the
index steps from the prompt) and `TDDAGENTS_MEMORY_SEARCH_PAST_CONTEXT` (`tengu_coral_fern`).
Not ported: the KAIROS daily-log prompt, the Cowork extra guidelines and the telemetry.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.memdir.memory_types import (
    MEMORY_FRONTMATTER_EXAMPLE,
    TRUSTING_RECALL_SECTION,
    TYPES_SECTION_INDIVIDUAL,
    WHAT_NOT_TO_SAVE_SECTION,
    WHEN_TO_ACCESS_SECTION,
)
from app.memdir.paths import get_auto_mem_path, is_auto_memory_enabled
from app.tools.bash_tool.bash_security import is_env_truthy
from app.utils.settings.settings import SettingsLocations
from app.utils.tool_result_storage import format_file_size

logger = logging.getLogger(__name__)

ENTRYPOINT_NAME = "MEMORY.md"
MAX_ENTRYPOINT_LINES = 200
#: ~125 characters a line at 200 lines.
MAX_ENTRYPOINT_BYTES = 25_000
AUTO_MEM_DISPLAY_NAME = "auto memory"
RELEVANCE_VAR = "TDDAGENTS_MEMORY_RELEVANCE"
SEARCH_PAST_CONTEXT_VAR = "TDDAGENTS_MEMORY_SEARCH_PAST_CONTEXT"
GREP_TOOL_NAME = "Grep"

DIR_EXISTS_GUIDANCE = (
    "This directory already exists — write to it directly with the WriteFile tool (do not run mkdir or check for "
    "its existence)."
)
DIRS_EXIST_GUIDANCE = (
    "Both directories already exist — write to them directly with the WriteFile tool (do not run mkdir or check for "
    "their existence)."
)


@dataclass(frozen=True, slots=True)
class EntrypointTruncation:
    """`EntrypointTruncation`."""

    content: str
    line_count: int
    byte_count: int
    was_line_truncated: bool
    was_byte_truncated: bool


def truncate_entrypoint_content(raw: str) -> EntrypointTruncation:
    """`truncateEntrypointContent`: the line cap, then the byte cap at a line break, then a warning naming why."""
    trimmed = raw.strip()
    lines = trimmed.split("\n")
    line_count, byte_count = len(lines), len(trimmed)
    line_cut, byte_cut = line_count > MAX_ENTRYPOINT_LINES, byte_count > MAX_ENTRYPOINT_BYTES
    if not line_cut and not byte_cut:
        return EntrypointTruncation(trimmed, line_count, byte_count, line_cut, byte_cut)
    truncated = "\n".join(lines[:MAX_ENTRYPOINT_LINES]) if line_cut else trimmed
    if len(truncated) > MAX_ENTRYPOINT_BYTES:
        cut = truncated.rfind("\n", 0, MAX_ENTRYPOINT_BYTES + 1)
        truncated = truncated[:cut if cut > 0 else MAX_ENTRYPOINT_BYTES]
    if byte_cut and not line_cut:
        reason = (f"{format_file_size(byte_count)} (limit: {format_file_size(MAX_ENTRYPOINT_BYTES)}) "
                  "— index entries are too long")
    elif line_cut and not byte_cut:
        reason = f"{line_count} lines (limit: {MAX_ENTRYPOINT_LINES})"
    else:
        reason = f"{line_count} lines and {format_file_size(byte_count)}"
    content = (truncated + f"\n\n> WARNING: {ENTRYPOINT_NAME} is {reason}. Only part of it was loaded. "
               "Keep index entries to one line under ~200 chars; move detail into topic files.")
    return EntrypointTruncation(content, line_count, byte_count, line_cut, byte_cut)


def ensure_memory_dir_exists(memory_dir: str) -> None:
    """`ensureMemoryDirExists`: create it with its parents; a failure is logged, the prompt goes on."""
    try:
        os.makedirs(memory_dir, exist_ok=True)
    except OSError as exc:
        logger.debug("ensureMemoryDirExists failed for %s: %s", memory_dir, exc)


def is_relevance_search_enabled(env: Mapping[str, str] | None = None) -> bool:
    """`tengu_moth_copse`, as the env opt-in `TDDAGENTS_MEMORY_RELEVANCE`."""
    return is_env_truthy((os.environ if env is None else env).get(RELEVANCE_VAR))


def how_to_save_lines(skip_index: bool, combined: bool = False) -> list[str]:
    """`## How to save memories`, single-directory or combined, with or without the index step."""
    where = (" in the chosen directory (private or team, per the type's scope guidance)" if combined else "")
    tail = [
        "- Keep the name, description, and type fields in memory files up-to-date with the content",
        "- Organize memory semantically by topic, not chronologically",
        "- Update or remove memories that turn out to be wrong or outdated",
        ("- Do not write duplicate memories. First check if there is an existing memory you can update before writing "
         "a new one."),
    ]
    if skip_index:
        first = ("Write each memory to its own file" + where + " using this frontmatter format:" if combined
                 else "Write each memory to its own file (e.g., `user_role.md`, `feedback_testing.md`) using this "
                      "frontmatter format:")
        return ["## How to save memories", "", first, "", *MEMORY_FRONTMATTER_EXAMPLE, "", *tail]
    if combined:
        step1 = f"**Step 1** — write the memory to its own file{where} using this frontmatter format:"
        step2 = (f"**Step 2** — add a pointer to that file in the same directory's `{ENTRYPOINT_NAME}`. Each "
                 f"directory (private and team) has its own `{ENTRYPOINT_NAME}` index — each entry should be one line, "
                 "under ~150 characters: `- [Title](file.md) — one-line hook`. They have no frontmatter. Never write "
                 f"memory content directly into a `{ENTRYPOINT_NAME}`.")
        loaded = (f"- Both `{ENTRYPOINT_NAME}` indexes are loaded into your conversation context — lines after "
                  f"{MAX_ENTRYPOINT_LINES} will be truncated, so keep them concise")
    else:
        step1 = ("**Step 1** — write the memory to its own file (e.g., `user_role.md`, `feedback_testing.md`) using "
                 "this frontmatter format:")
        step2 = (f"**Step 2** — add a pointer to that file in `{ENTRYPOINT_NAME}`. `{ENTRYPOINT_NAME}` is an index, "
                 "not a memory — each entry should be one line, under ~150 characters: `- [Title](file.md) — one-line "
                 f"hook`. It has no frontmatter. Never write memory content directly into `{ENTRYPOINT_NAME}`.")
        loaded = (f"- `{ENTRYPOINT_NAME}` is always loaded into your conversation context — lines after "
                  f"{MAX_ENTRYPOINT_LINES} will be truncated, so keep the index concise")
    return ["## How to save memories", "", "Saving a memory is a two-step process:", "", step1, "",
            *MEMORY_FRONTMATTER_EXAMPLE, "", step2, "", loaded, *tail]


#: `## Memory and other forms of persistence`, shared by both prompts.
PERSISTENCE_SECTION: tuple[str, ...] = (
    "## Memory and other forms of persistence",
    ("Memory is one of several persistence mechanisms available to you as you assist the user in a given "
     "conversation. The distinction is often that memory can be recalled in future conversations and should not be "
     "used for persisting information that is only useful within the scope of the current conversation."),
    ("- When to use or update a plan instead of memory: If you are about to start a non-trivial implementation task "
     "and would like to reach alignment with the user on your approach you should use a Plan rather than saving this "
     "information to memory. Similarly, if you already have a plan within the conversation and you have changed your "
     "approach persist that change by updating the plan rather than saving a memory."),
    ("- When to use or update tasks instead of memory: When you need to break your work in current conversation into "
     "discrete steps or keep track of your progress use tasks instead of saving to memory. Tasks are great for "
     "persisting information about the work that needs to be done in the current conversation, but memory should be "
     "reserved for information that will be useful in future conversations."),
)

#: The opening paragraphs both prompts share after the directory line.
BUILD_UP_LINES: tuple[str, ...] = (
    ("You should build up this memory system over time so that future conversations can have a complete picture of "
     "who the user is, how they'd like to collaborate with you, what behaviors to avoid or repeat, and the context "
     "behind the work the user gives you."),
    "",
    ("If the user explicitly asks you to remember something, save it immediately as whichever type fits best. If "
     "they ask you to forget something, find and remove the relevant entry."),
)


def build_searching_past_context_section(auto_mem_dir: str, project_dir: str,
                                         env: Mapping[str, str] | None = None) -> list[str]:
    """`buildSearchingPastContextSection`: how to grep memory and transcripts, when opted in."""
    if not is_env_truthy((os.environ if env is None else env).get(SEARCH_PAST_CONTEXT_VAR)):
        return []
    return [
        "## Searching past context",
        "",
        "When looking for past context:",
        "1. Search topic files in your memory directory:",
        "```",
        f'{GREP_TOOL_NAME} with pattern="<search term>" path="{auto_mem_dir}" glob="*.md"',
        "```",
        "2. Session transcript logs (last resort — large files, slow):",
        "```",
        f'{GREP_TOOL_NAME} with pattern="<search term>" path="{project_dir}/" glob="*.jsonl"',
        "```",
        "Use narrow search terms (error messages, file paths, function names) rather than broad keywords.",
        "",
    ]


def build_memory_lines(display_name: str, memory_dir: str, extra_guidelines: Sequence[str] = (),
                       skip_index: bool = False, past_context: Sequence[str] = ()) -> list[str]:
    """`buildMemoryLines`: the typed-memory instructions for one directory (no MEMORY.md content)."""
    return [
        f"# {display_name}",
        "",
        f"You have a persistent, file-based memory system at `{memory_dir}`. {DIR_EXISTS_GUIDANCE}",
        "",
        *BUILD_UP_LINES,
        "",
        *TYPES_SECTION_INDIVIDUAL,
        *WHAT_NOT_TO_SAVE_SECTION,
        "",
        *how_to_save_lines(skip_index),
        "",
        *WHEN_TO_ACCESS_SECTION,
        "",
        *TRUSTING_RECALL_SECTION,
        "",
        *PERSISTENCE_SECTION,
        "",
        *extra_guidelines,
        "",
        *past_context,
    ]


def build_memory_prompt(display_name: str, memory_dir: str, extra_guidelines: Sequence[str] = ()) -> str:
    """`buildMemoryPrompt`: the instructions plus the directory's MEMORY.md (agent memory)."""
    try:
        with open(os.path.join(memory_dir, ENTRYPOINT_NAME), encoding="utf-8") as handle:
            entrypoint = handle.read()
    except OSError:
        entrypoint = ""
    lines = build_memory_lines(display_name, memory_dir, extra_guidelines)
    if entrypoint.strip():
        lines += [f"## {ENTRYPOINT_NAME}", "", truncate_entrypoint_content(entrypoint).content]
    else:
        lines += [f"## {ENTRYPOINT_NAME}", "",
                  f"Your {ENTRYPOINT_NAME} is currently empty. When you save new memories, they will appear here."]
    return "\n".join(lines)


def load_memory_prompt(project_root: str, project_dir: str, locations: SettingsLocations | None = None,
                       env: Mapping[str, str] | None = None, settings: Mapping[str, Any] | None = None) -> str | None:
    """
    `loadMemoryPrompt`: the combined prompt when team memory is on, the auto-memory instructions
    when auto memory is on, else `None`. The directories are created so the model can write at once.
    """
    if not is_auto_memory_enabled(env, settings):
        return None
    skip_index = is_relevance_search_enabled(env)
    auto_dir = get_auto_mem_path(project_root, locations)
    past_context = build_searching_past_context_section(auto_dir, project_dir, env)
    from app.memdir.team_mem_paths import get_team_mem_path, is_team_memory_enabled

    if is_team_memory_enabled(env, settings):
        from app.memdir.team_mem_prompts import build_combined_memory_prompt

        team_dir = get_team_mem_path(project_root, locations)
        ensure_memory_dir_exists(team_dir)
        return build_combined_memory_prompt(auto_dir, team_dir, skip_index=skip_index, past_context=past_context)
    ensure_memory_dir_exists(auto_dir)
    return "\n".join(build_memory_lines(AUTO_MEM_DISPLAY_NAME, auto_dir, skip_index=skip_index,
                                        past_context=past_context))
