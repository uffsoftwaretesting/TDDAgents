"""
`getAttachments` / `getAttachmentMessages`: the per-turn context the loop persists.

Ported from `reference/claude-code/src/utils/attachments.ts` -> `getAttachments`, `maybe`,
`getAttachmentMessages`.

Each source runs through `maybe`: a source that fails is logged and contributes nothing, so
one broken source never costs the turn its other attachments. The order is upstream's —
user-input sources (only when there is input), then the all-thread sources, then the
main-thread ones — with TDDAgents' `tdd_state` last among the all-thread sources.
`TDDAGENTS_DISABLE_ATTACHMENTS` / `TDDAGENTS_SIMPLE` (upstream's `CLAUDE_CODE_*`) leave only
the queued commands.

Sources not ported, each because this build has no subsystem for it: `at_mentioned_files`,
`mcp_resources`, `agent_mentions`, `skill_discovery`, `dynamic_skill`, `skill_listing`
(Phase 8), `deferred_tools_delta` (no deferred tools), `ultrathink_effort` (no reasoning-effort
control), `companion_intro`, `plan_mode`/`plan_mode_exit` (no plan mode in a headless run),
`auto_mode_exit` (no mode transition sets its flag), `teammate_mailbox`, `team_context`,
`agent_pending_messages`, `critical_system_reminder` (no context field carries one),
`compaction_reminder` and `context_efficiency` (feature-gated off), `ide_selection`,
`ide_opened_file`, `output_style`, `diagnostics`, `lsp_diagnostics`, `unified_tasks`,
`budget_usd` (no budget or cost tracker until Phase 6), `output_token_usage`
(`TOKEN_BUDGET`, off), `verify_plan_reminder` (ant-only). The upstream 1 s computation
timeout is not ported: every source here is local and bounded.
"""

from __future__ import annotations

import inspect
import logging
import os
from collections.abc import Callable, Sequence
from typing import Any

from app.loop.attachments.changed_files import get_changed_files
from app.loop.attachments.dates import get_date_change_attachments
from app.loop.attachments.deltas import (
    get_agent_listing_delta_attachment,
    get_mcp_instructions_delta_attachment,
)
from app.loop.attachments.nested_memory import get_nested_memory_source
from app.loop.attachments.queued import (
    get_async_hook_response_attachments,
    get_queued_command_attachments,
)
from app.loop.attachments.reminders import (
    get_auto_mode_attachments,
    get_todo_reminder_attachments,
)
from app.loop.attachments.tdd_state import get_tdd_state_attachments
from app.loop.attachments.types import Attachment, create_attachment_messages
from app.loop.attachments.usage import get_token_usage_attachment
from app.loop.messages import Message

logger = logging.getLogger("TDDOrchestrator.Attachments")

#: A source: `(messages, context)` -> its attachments, or an awaitable of them.
Source = Callable[[Sequence[Message], Any], Any]

_TRUTHY = ("1", "true", "yes", "on")


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


async def maybe(label: str, source: Source, messages: Sequence[Message], context: Any) -> list[Attachment]:
    """`maybe`: a source's attachments, or none if it fails."""
    try:
        result = source(messages, context)
        if inspect.isawaitable(result):
            result = await result
        return list(result)
    except Exception:
        logger.exception("Attachment error in %s", label)
        return []


async def get_attachments(
    context: Any, messages: Sequence[Message], *, user_input: str | None = None
) -> list[Attachment]:
    """`getAttachments`. `user_input` is the prompt being submitted; between turns it is `None`."""
    if _env_truthy("TDDAGENTS_DISABLE_ATTACHMENTS") or _env_truthy("TDDAGENTS_SIMPLE"):
        return await maybe("queued_commands", get_queued_command_attachments, messages, context)
    is_main_thread = not getattr(context, "agent_id", None)
    all_thread: list[tuple[str, Source]] = [
        ("queued_commands", get_queued_command_attachments),
        ("date_change", get_date_change_attachments),
        ("agent_listing_delta", get_agent_listing_delta_attachment),
        ("mcp_instructions_delta", get_mcp_instructions_delta_attachment),
        ("changed_files", get_changed_files),
        ("nested_memory", get_nested_memory_source),
        ("auto_mode", get_auto_mode_attachments),
        ("todo_reminders", get_todo_reminder_attachments),
        ("tdd_state", get_tdd_state_attachments),
    ]
    main_thread: list[tuple[str, Source]] = (
        [
            ("async_hook_responses", get_async_hook_response_attachments),
            ("token_usage", get_token_usage_attachment),
        ]
        if is_main_thread
        else []
    )
    out: list[Attachment] = []
    for label, source in [*all_thread, *main_thread]:
        out.extend(await maybe(label, source, messages, context))
    return out


async def get_attachment_messages(
    context: Any, messages: Sequence[Message], *, user_input: str | None = None
) -> list[Message]:
    """`getAttachmentMessages`: every attachment as its meta messages, in order."""
    out: list[Message] = []
    for attachment in await get_attachments(context, messages, user_input=user_input):
        out.extend(create_attachment_messages(attachment))
    return out


__all__ = ["get_attachment_messages", "get_attachments", "maybe"]
