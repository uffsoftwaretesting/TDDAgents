"""
Coordinator mode (Phase 7).

Ported from `reference/claude-code/src/coordinator/coordinatorMode.ts` -> `isCoordinatorMode`,
`getCoordinatorUserContext`, `getCoordinatorSystemPrompt`; `utils/toolPool.ts` ->
`applyCoordinatorToolFilter`; `constants/tools.ts` -> `COORDINATOR_MODE_ALLOWED_TOOLS`.

Upstream's coordinator mode is a switch, read from `CLAUDE_CODE_COORDINATOR_MODE` and off by
default; here it is `TDDAGENTS_COORDINATOR_MODE`, frozen at entry as `Gates.coordinator_mode`.
It is policy, not a scheduler: the main thread's pool shrinks to the orchestration tools, its
system prompt becomes the coordinator's, and the user context names what workers can use.

The system prompt is TDDAgents-authored (`app/prompts/system/coordinator.md`): upstream's
sections, adapted to synchronous workers and the TDD roster, with the phase ledger — not the
coordinator — deciding when a phase may start. The `tdd_phase_incomplete` stop hook guards the
coordinator, because its context carries no `agent_id`.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.loop.agents.resolution import ASYNC_AGENT_ALLOWED_TOOLS
from app.loop.config import RunConfig
from app.loop.prompts.loader import render_prompt
from app.loop.tools.base import Tool

COORDINATOR_MODE_ENV = "TDDAGENTS_COORDINATOR_MODE"

AGENT_TOOL_NAME = "Agent"
TASK_STOP_TOOL_NAME = "TaskStop"
SEND_MESSAGE_TOOL_NAME = "SendMessage"
SYNTHETIC_OUTPUT_TOOL_NAME = "StructuredOutput"

#: `COORDINATOR_MODE_ALLOWED_TOOLS`.
COORDINATOR_MODE_ALLOWED_TOOLS = frozenset({
    AGENT_TOOL_NAME, TASK_STOP_TOOL_NAME, SEND_MESSAGE_TOOL_NAME, SYNTHETIC_OUTPUT_TOOL_NAME,
})

#: `INTERNAL_WORKER_TOOLS` (the team tools do not exist here).
INTERNAL_WORKER_TOOLS = frozenset({SEND_MESSAGE_TOOL_NAME, SYNTHETIC_OUTPUT_TOOL_NAME})

_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "system" / "coordinator.md"

#: The names the coordinator prompt refers to, resolved here (prompt convention).
_PROMPT_VARS = {
    "AGENT_TOOL_NAME": AGENT_TOOL_NAME,
    "RUN_TESTS_TOOL_NAME": "RunTests",
    "TESTER": "tester",
    "DEVELOPER": "developer",
    "REFACTORER": "refactorer",
    "VERIFIER": "verification",
    "EXPLORE": "Explore",
    "PLAN": "Plan",
}


def is_coordinator_mode(config: RunConfig) -> bool:
    """`isCoordinatorMode`, as the run's frozen gate."""
    return config.gates.coordinator_mode


def apply_coordinator_tool_filter(tools: Sequence[Tool]) -> list[Tool]:
    """`applyCoordinatorToolFilter` (no PR-activity subscription tools exist here)."""
    return [t for t in tools if t.name in COORDINATOR_MODE_ALLOWED_TOOLS]


def get_coordinator_user_context() -> dict[str, str]:
    """`getCoordinatorUserContext` (no MCP servers and no scratchpad here)."""
    worker_tools = ", ".join(sorted(ASYNC_AGENT_ALLOWED_TOOLS - INTERNAL_WORKER_TOOLS))
    return {"workerToolsContext": f"Workers spawned via the {AGENT_TOOL_NAME} tool have access to these tools: "
                                  f"{worker_tools}"}


def get_coordinator_system_prompt(vars: dict[str, Any] | None = None) -> str:
    """`getCoordinatorSystemPrompt`: the TDDAgents coordinator prompt, rendered."""
    return render_prompt(_PROMPT_PATH.read_text(encoding="utf-8"), {**_PROMPT_VARS, **(vars or {})}).strip()


__all__ = [
    "COORDINATOR_MODE_ALLOWED_TOOLS",
    "COORDINATOR_MODE_ENV",
    "INTERNAL_WORKER_TOOLS",
    "apply_coordinator_tool_filter",
    "get_coordinator_system_prompt",
    "get_coordinator_user_context",
    "is_coordinator_mode",
]
