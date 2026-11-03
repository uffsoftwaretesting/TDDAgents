"""
Registering the bundled skills built in code.

Ported from `reference/claude-code/src/skills/bundled/index.ts` -> `initBundledSkills`.

Upstream registers its code-built skills once at startup (`main.tsx`, before `getCommands`).
TDDAgents calls this where a run first loads its commands (`open_session_run`, before
`get_skill_registry`), so it registers only the skills not yet in the process registry: a second
session in the same process does not list a skill twice.

TDDAgents' only code-built skill is `tdd-project-config` (`app/tdd/project_config_skill.py`, the
`claude-api` pattern). None of upstream's own bundled skills are ported.
"""

from __future__ import annotations

from app.skills.bundled_skills import get_bundled_skills, register_bundled_skill
from app.tdd.project_config_skill import tdd_project_config_skill


def init_bundled_skills() -> None:
    """`initBundledSkills`: every code-built skill, each registered once per process."""
    registered = {definition.name for definition in get_bundled_skills()}
    for definition in (tdd_project_config_skill(),):
        if definition.name not in registered:
            register_bundled_skill(definition)


__all__ = ["init_bundled_skills"]
