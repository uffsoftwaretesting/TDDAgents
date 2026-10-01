"""
Skills package (Part J of transition elaboration plan).

Exports:
- SkillDefinition, SkillFrontmatterError
- SkillRegistry, load_skill_from_path, discover_skills, parse_skill_frontmatter
- estimate_skill_frontmatter_tokens, is_skill_active_for_paths, filter_active_skills,
  filter_skills_by_budget, render_skills_prompt_section
- build_skill_tool, SKILL_TOOL_PROMPT
"""

from __future__ import annotations

from app.loop.skills.activation import (
    estimate_skill_frontmatter_tokens,
    filter_active_skills,
    filter_skills_by_budget,
    is_path_matching_pattern,
    is_skill_active_for_paths,
    render_skills_prompt_section,
)
from app.loop.skills.definition import (
    SkillDefinition,
    SkillFrontmatterError,
)
from app.loop.skills.loader import (
    SkillRegistry,
    discover_skills,
    extract_description_from_markdown,
    load_skill_from_path,
    load_skill_references,
    parse_skill_frontmatter,
    parse_skill_paths,
)
from app.loop.skills.tool import (
    SKILL_TOOL_PROMPT,
    build_skill_tool,
    substitute_arguments,
)

__all__ = [
    # Definition
    "SkillDefinition",
    "SkillFrontmatterError",
    # Loader & Registry
    "SkillRegistry",
    "discover_skills",
    "extract_description_from_markdown",
    "load_skill_from_path",
    "load_skill_references",
    "parse_skill_frontmatter",
    "parse_skill_paths",
    # Activation & Progressive Disclosure
    "estimate_skill_frontmatter_tokens",
    "filter_active_skills",
    "filter_skills_by_budget",
    "is_path_matching_pattern",
    "is_skill_active_for_paths",
    "render_skills_prompt_section",
    # Tool
    "SKILL_TOOL_PROMPT",
    "build_skill_tool",
    "substitute_arguments",
]
