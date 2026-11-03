"""
Registering the bundled skills built in code.

Ported from `reference/claude-code/src/skills/bundled/index.ts` -> `initBundledSkills`.

Upstream registers its code-built skills once at startup (`main.tsx`, before `getCommands`).
TDDAgents calls this where a run first loads its commands (`open_session_run`, before
`get_skill_registry`), so it registers only the skills not yet in the process registry: a second
session in the same process does not list a skill twice.

TDDAgents' code-built skills, in upstream's registration order: `update-config`
(`app/skills/bundled/update_config.py`, upstream's `registerUpdateConfigSkill`, which `/init`'s
`NEW_INIT_PROMPT` loads for its hooks step) and `tdd-project-config` (`app/tdd/project_config_skill.py`,
registered where upstream registers `claude-api`, whose pattern it follows). Upstream's other bundled
skills are not ported.
"""

from __future__ import annotations

from app.skills.bundled.update_config import update_config_skill
from app.skills.bundled_skills import get_bundled_skills, register_bundled_skill
from app.tdd.project_config_skill import tdd_project_config_skill


def init_bundled_skills() -> None:
    """`initBundledSkills`: every code-built skill, each registered once per process."""
    registered = {definition.name for definition in get_bundled_skills()}
    for definition in (update_config_skill(), tdd_project_config_skill()):
        if definition.name not in registered:
            register_bundled_skill(definition)


__all__ = ["init_bundled_skills"]
