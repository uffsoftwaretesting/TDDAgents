"""
Skill preloading for agents (Phase 8).

Ported from `reference/claude-code/src/tools/AgentTool/runAgent.ts` -> the `skillsToPreload`
loop and `resolveSkillName`. Each skill an agent's frontmatter names (`skills:`) is loaded into
the worker's first messages as one `isMeta` user message: the skill-format loading metadata,
then the skill's text with no arguments. A name that resolves to nothing is logged and skipped.

Skills come from `getSkillToolCommands` — the ones the model may invoke — so a skill with
`disable-model-invocation` cannot be preloaded either.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from langchain_core.messages import HumanMessage

from app.loop.messages import Message
from app.loop.skills.definition import SkillCommand
from app.loop.skills.loader import SkillRegistry
from app.loop.skills.tool import format_skill_loading_metadata

logger = logging.getLogger(__name__)


def _has_command(name: str, commands: Sequence[SkillCommand]) -> bool:
    return any(name in (c.name, c.user_facing_name()) for c in commands)


def resolve_skill_name(skill_name: str, commands: Sequence[SkillCommand], agent_type: str) -> str | None:
    """`resolveSkillName`: exact, then the agent's plugin prefix, then a `:name` suffix."""
    if _has_command(skill_name, commands):
        return skill_name
    plugin_prefix = agent_type.split(":")[0]
    if plugin_prefix and _has_command(f"{plugin_prefix}:{skill_name}", commands):
        return f"{plugin_prefix}:{skill_name}"
    return next((c.name for c in commands if c.name.endswith(f":{skill_name}")), None)


def skill_preload_messages(
    agent_skills: Sequence[str], registry: SkillRegistry, *, agent_type: str, session_id: str = ""
) -> list[Message]:
    """The preloaded skills' messages, in frontmatter order."""
    commands = registry.get_skill_tool_commands()
    messages: list[Message] = []
    for skill_name in agent_skills:
        resolved = resolve_skill_name(skill_name, commands, agent_type)
        if resolved is None:
            logger.warning("[Agent: %s] Warning: Skill '%s' specified in frontmatter was not found",
                           agent_type, skill_name)
            continue
        skill = next(c for c in commands if resolved in (c.name, c.user_facing_name()))
        logger.debug("[Agent: %s] Preloaded skill '%s'", agent_type, skill_name)
        messages.append(HumanMessage(
            content=[
                {"type": "text", "text": format_skill_loading_metadata(skill_name)},
                {"type": "text", "text": skill.prompt_for_command("", session_id=session_id)},
            ],
            additional_kwargs={"is_meta": True},
        ))
    return messages


__all__ = ["resolve_skill_name", "skill_preload_messages"]
