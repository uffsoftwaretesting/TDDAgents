"""
The `skill_listing` source (Phase 8).

Ported from `reference/claude-code/src/utils/attachments.ts` -> `getSkillListingAttachments`
(`sentSkillNames`). Each agent is told about each skill once: the first listing carries every
skill the model may invoke, later ones only skills that appeared since (a conditional skill
activated by a file path). Nothing is listed unless the context's pool holds the Skill tool.

The registry comes from the context (`skill_registry`), and its `sent_skill_names` is upstream's
module-scope map made per run. The budget follows the run's model window
(`getContextWindowForModel(mainLoopModel)`), here `SkillRegistry.model`; unset means the
default budget, which is what an unknown model's 200k window gives anyway.

Not ported: MCP skill commands (`getMcpSkillCommands`), the skill-search filter
(`EXPERIMENTAL_SKILL_SEARCH`), `suppressNextSkillListing` (resume wiring is Phase 9), and the
`NODE_ENV === 'test'` short-circuit.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from app.loop.attachments.types import Attachment
from app.loop.context.tokens import get_context_window_for_model
from app.loop.messages import Message
from app.loop.skills.prompt import SKILL_TOOL_NAME, format_commands_within_budget

logger = logging.getLogger(__name__)


def _has_skill_tool(context: Any) -> bool:
    return any(t.name == SKILL_TOOL_NAME for t in context.tools)


def get_skill_listing_attachments(messages: Sequence[Message], context: Any) -> list[Attachment]:
    """`getSkillListingAttachments`."""
    registry = context.skill_registry
    if registry is None or not _has_skill_tool(context):
        return []
    commands = registry.get_skill_tool_commands()
    # Upstream keys by `agentId ?? ''`; the main thread's key here is its `None` agent id.
    sent = registry.sent_skill_names.setdefault(context.agent_id, set())
    new_skills = [cmd for cmd in commands if cmd.name not in sent]
    if not new_skills:
        return []
    is_initial = not sent
    sent.update(cmd.name for cmd in new_skills)
    logger.debug("Sending %d skills via attachment (%s, %d total sent)",
                 len(new_skills), "initial" if is_initial else "dynamic", len(sent))
    window = get_context_window_for_model(registry.model) if registry.model else None
    content = format_commands_within_budget(new_skills, window)
    return [Attachment("skill_listing", {"content": content, "skillCount": len(new_skills), "isInitial": is_initial})]


__all__ = ["get_skill_listing_attachments"]
