"""
Skills (Phase 8): upstream's skill commands, ported from `skills/loadSkillsDir.ts`,
`skills/bundledSkills.ts`, `tools/SkillTool/` and `tools/AgentTool/runAgent.ts`'s preload.

The light modules are re-exported here. The Skill tool (`skills/tool.py`) and the agent preload
(`skills/preload.py`) are imported directly: the tool pulls in the worker loop, which this
package's light modules must not.
"""

from __future__ import annotations

from app.loop.skills.arguments import (
    parse_argument_names,
    parse_arguments,
    substitute_arguments,
)
from app.loop.skills.definition import SkillCommand
from app.loop.skills.loader import (
    BUNDLED_SKILLS_DIR,
    SkillRegistry,
    get_skill_registry,
    load_skills_from_skills_dir,
    parse_skill_frontmatter_fields,
)

__all__ = [
    "BUNDLED_SKILLS_DIR",
    "SkillCommand",
    "SkillRegistry",
    "get_skill_registry",
    "load_skills_from_skills_dir",
    "parse_argument_names",
    "parse_arguments",
    "parse_skill_frontmatter_fields",
    "substitute_arguments",
]
