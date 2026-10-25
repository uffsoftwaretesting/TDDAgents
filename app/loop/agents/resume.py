"""
Resuming a worker in the background (Phase 8).

Ported from `reference/claude-code/src/tools/AgentTool/resumeAgent.ts` ->
`resumeAgentBackground`: reload a worker's sidechain transcript (`getAgentTranscript`), clean
it with the resume filters (`filterUnresolvedToolUses`, `filterOrphanedThinkingOnlyMessages`,
`filterWhitespaceOnlyAssistantMessages`), route on its metadata sidecar (`readAgentMetadata`),
and run it again as a background task with the new prompt appended. `SendMessage` is the
caller.

**TDD.** A resumed worker always runs async (upstream's `isAsync: true`), so a phase agent is
refused before anything runs — the same rule as the Agent tool's background path: an async
worker's state changes are discarded, so its `RunTests` could not move the ledger.

Divergences: no `general-purpose` agent exists here, so a missing or unknown agent type raises
instead of falling back to it; fork resume, worktrees, content-replacement reconstruction and
the agent context for analytics are not ported.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from langchain_core.messages import HumanMessage

from app.loop.agents.definition import AgentDefinition
from app.loop.agents.run_agent import run_agent
from app.loop.agents.tool import agent_system_prompt, refuse_background_phase_agent
from app.loop.config import RunConfig
from app.loop.context import ToolContext
from app.loop.context.assembly import enhance_system_prompt_with_env_details
from app.loop.deps import LoopDeps
from app.loop.model_aliases import get_agent_model
from app.loop.storage.loader import get_agent_transcript
from app.loop.storage.recovery import (
    filter_orphaned_thinking_only_messages,
    filter_unresolved_tool_uses,
    filter_whitespace_only_assistant_messages,
)
from app.loop.tasks.disk_output import get_task_output_path, init_task_output_as_symlink
from app.loop.tasks.local_agent_task import (
    register_async_agent,
    run_async_agent_lifecycle,
    start_background_lifecycle,
)
from app.loop.tools.base import Tool


@dataclass(frozen=True, slots=True)
class ResumeAgentResult:
    """`ResumeAgentResult`."""

    agent_id: str
    description: str
    output_file: str


def _no_transcript(agent_id: str) -> ValueError:
    return ValueError(f"No transcript found for agent ID: {agent_id}")


async def resume_agent_background(
    *,
    agent_id: str,
    prompt: str,
    context: ToolContext,
    definitions: Mapping[str, AgentDefinition],
    worker_tools: Sequence[Tool],
    config: RunConfig,
    deps: LoopDeps,
    now: Callable[[], float] = time.time,
) -> ResumeAgentResult:
    """`resumeAgentBackground`: start the worker again and return at once."""
    start = now()
    store = context.transcript_store
    if store is None:
        raise _no_transcript(agent_id)
    transcript = get_agent_transcript(store.agent_transcript_path(agent_id), agent_id)
    if not transcript:
        raise _no_transcript(agent_id)
    resumed = filter_whitespace_only_assistant_messages(
        filter_orphaned_thinking_only_messages(filter_unresolved_tool_uses(transcript))
    )

    meta = store.read_agent_metadata(agent_id) or {}
    agent_type = meta.get("agentType")
    if not agent_type:
        raise ValueError(f"No agent type recorded for agent ID: {agent_id}")
    selected = definitions.get(str(agent_type))
    if selected is None:
        raise ValueError(f"Agent type '{agent_type}' not found. Available agents: {', '.join(definitions)}")
    refuse_background_phase_agent(selected, True)
    description = str(meta.get("description") or "(resumed)")

    model = get_agent_model(selected.model, config.model, None)
    additional = context.get_app_state().tool_permission_context.additional_working_directories
    system_prompt = enhance_system_prompt_with_env_details(
        [agent_system_prompt(selected, context)], model, additional, context.workspace
    )
    output_path = get_task_output_path(context, agent_id)
    init_task_output_as_symlink(output_path, store.agent_transcript_path(agent_id))
    task = register_async_agent(
        agent_id=agent_id, description=description, prompt=prompt, agent_type=selected.name,
        set_app_state=context.set_app_state, output_file=str(output_path), now=start,
        tool_use_id=context.tool_use_id,
    )
    stream = run_agent(
        agent_definition=selected,
        prompt_messages=[*resumed, HumanMessage(content=prompt)],
        tool_context=context,
        config=config,
        deps=deps,
        available_tools=worker_tools,
        system_prompt=system_prompt,
        is_async=True,
        agent_id=agent_id,
        cancel=task.cancel,
        description=meta.get("description"),
    )
    start_background_lifecycle(run_async_agent_lifecycle(
        task_id=agent_id, cancel=task.cancel, stream=stream, description=description,
        agent_type=selected.name, start_time=start, root_set_app_state=context.set_app_state,
        queue=context.command_queue, output_file=str(output_path), now=now,
        tool_use_id=context.tool_use_id,
    ))
    return ResumeAgentResult(agent_id=agent_id, description=description, output_file=str(output_path))


__all__ = ["ResumeAgentResult", "resume_agent_background"]
