"""
Skill activation, path matching, and progressive disclosure budget conforming to §4.6 and Parts J2 & J3.

Ported from:
- `reference/claude-code/src/skills/loadSkillsDir.ts` -> `estimateSkillFrontmatterTokens`, `parseSkillPaths`
- `docs/transition_elaboration_plan.md` -> §4.6, Part J2 (progressive disclosure budget),
  Part J3 (path-conditional activation)
"""

from __future__ import annotations

import fnmatch
import logging
from pathlib import PurePosixPath
from typing import Sequence

from app.loop.context.tokens import estimate_string_tokens
from app.loop.skills.definition import SkillDefinition

logger = logging.getLogger(__name__)


def estimate_skill_frontmatter_tokens(skill: SkillDefinition) -> int:
    """
    Estimates token count for a skill based on frontmatter only (name, description, when_to_use, argument_hint)
    since full content is only loaded on invocation (Part J2).

    Ported from `estimateSkillFrontmatterTokens` in `loadSkillsDir.ts`.
    """
    tokens = [skill.name, skill.description]
    if skill.when_to_use:
        tokens.append(skill.when_to_use)
    if skill.argument_hint:
        tokens.append(skill.argument_hint)
    return estimate_string_tokens(" ".join(tokens))


def is_path_matching_pattern(file_path: str, pattern: str) -> bool:
    """
    Check if a file path matches a glob pattern.

    Supports:
    - Wildcards: '*.py', 'test_*.py'
    - Recursive globs: 'tests/**', 'src/**/*.ts'
    - Exact paths or directory prefixes: 'tests/', 'app/core'
    """
    norm_path = file_path.replace("\\", "/").lstrip("/")
    norm_pat = pattern.replace("\\", "/").lstrip("/")

    if norm_pat.endswith("/**"):
        prefix = norm_pat[:-3]
        return norm_path == prefix or norm_path.startswith(f"{prefix}/")

    if norm_pat.endswith("/"):
        prefix = norm_pat[:-1]
        return norm_path == prefix or norm_path.startswith(f"{prefix}/")

    if "**" in norm_pat:
        # Match using PurePosixPath.match or glob-to-regex
        parts_pat = norm_pat.split("/")
        parts_path = norm_path.split("/")
        # If starts with 'tests/**'
        if parts_pat[0] != "**" and parts_path and parts_path[0] == parts_pat[0]:
            sub_pat = "/".join(parts_pat[1:])
            sub_path = "/".join(parts_path[1:])
            return is_path_matching_pattern(sub_path, sub_pat)
        if norm_pat.startswith("**/"):
            rest = norm_pat[3:]
            if "/" not in rest:
                return fnmatch.fnmatch(norm_path.split("/")[-1], rest)
            return PurePosixPath(norm_path).match(norm_pat)
        return PurePosixPath(norm_path).match(norm_pat)

    if "/" not in norm_pat:
        return fnmatch.fnmatch(norm_path.split("/")[-1], norm_pat)

    return PurePosixPath(norm_path).match(norm_pat)


def is_skill_active_for_paths(
    skill: SkillDefinition,
    active_paths: Sequence[str] | set[str] | None = None,
) -> bool:
    """
    Determine if a skill is active given the current active or target paths (Part J3).

    - If skill has no paths constraint (paths is None), it is always active (unconditional).
    - If active_paths is None or empty, conditional skills are inactive.
    - If any active path matches any pattern in skill.paths, the skill is active.
    """
    if skill.paths is None:
        return True

    if not active_paths:
        return False

    for path in active_paths:
        for pat in skill.paths:
            if is_path_matching_pattern(path, pat):
                return True

    return False


def filter_active_skills(
    skills: Sequence[SkillDefinition],
    active_paths: Sequence[str] | set[str] | None = None,
) -> tuple[SkillDefinition, ...]:
    """
    Filter skills keeping only those active for the given paths (Part J3).
    """
    return tuple(s for s in skills if is_skill_active_for_paths(s, active_paths))


def filter_skills_by_budget(
    skills: Sequence[SkillDefinition],
    token_budget: int | None = None,
) -> tuple[SkillDefinition, ...]:
    """
    Limit skills exposed in prompt to fit within a token budget (Part J2).

    If token_budget is None or <= 0, all skills are returned.
    """
    if token_budget is None or token_budget <= 0:
        return tuple(skills)

    budgeted: list[SkillDefinition] = []
    consumed = 0

    for skill in skills:
        cost = estimate_skill_frontmatter_tokens(skill)
        if consumed + cost > token_budget:
            logger.debug(
                "Skill '%s' (tokens=%d) omitted: exceeds progressive disclosure budget (%d/%d)",
                skill.name,
                cost,
                consumed + cost,
                token_budget,
            )
            continue
        budgeted.append(skill)
        consumed += cost

    return tuple(budgeted)


def render_skills_prompt_section(
    skills: Sequence[SkillDefinition],
    token_budget: int | None = None,
) -> str:
    """
    Render the available skills section for the system prompt.

    Enforces progressive disclosure: ONLY frontmatter metadata (name, description,
    when-to-use, argument-hint) is included. The body is strictly loaded on invocation.
    """
    selected = filter_skills_by_budget(skills, token_budget)
    if not selected:
        return ""

    lines = [
        "<available_skills>",
        "The following specialized skills provide domain procedures and can be executed via the `Skill` tool:",
        "",
    ]

    for skill in selected:
        line = f"- **{skill.name}**: {skill.description}"
        details: list[str] = []
        if skill.when_to_use:
            details.append(f"When: {skill.when_to_use}")
        if skill.argument_hint:
            details.append(f"Args: {skill.argument_hint}")
        if details:
            line += f" ({'; '.join(details)})"
        lines.append(line)

    lines.append("</available_skills>")
    return "\n".join(lines)
