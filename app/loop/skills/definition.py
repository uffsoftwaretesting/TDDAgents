"""
Skill definition data model conforming to §4.6 and Part J1.

Ported from:
- `reference/claude-code/src/skills/loadSkillsDir.ts` -> `parseSkillFrontmatterFields`
- `reference/claude-code/src/types/command.ts` -> `Command`, `PromptCommand`
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class SkillFrontmatterError(ValueError):
    """Raised when a skill definition carries invalid or malformed frontmatter."""


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    """
    Parsed and validated skill definition loaded from a SKILL.md file.

    Follows §4.6:
    - SKILL.md with frontmatter and a body, plus references/ loaded on demand.
    - Only name, description, argument hints, and when-to-use reach the system prompt.
    - The body loads on invocation.
    - References load only if the body points at them or on demand.
    """

    name: str
    description: str
    body: str
    when_to_use: str | None = None
    argument_hint: str | None = None
    argument_names: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    model: str | None = None
    user_invocable: bool = True
    context: str | None = None  # "inline" or "fork"
    agent: str | None = None
    paths: tuple[str, ...] | None = None
    hooks: dict[str, Any] | None = None
    effort: str | None = None
    source: str = "built-in"  # "built-in", "project", "user", "plugin"
    base_dir: str = ""
    skill_file_path: str = ""
    references: dict[str, str] = field(default_factory=dict)
    raw_frontmatter: dict[str, Any] = field(default_factory=dict)
