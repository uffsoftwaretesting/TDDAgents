"""
Attachments and delta pattern for dynamic tool schemas and agents.

Ported from:
- `reference/claude-code/src/services/context/attachments.ts` -> `agent_listing_delta`
- `reference/claude-code/src/services/context/mcpInstructionsDelta.ts` -> `mcp_instructions_delta`

Decouples dynamic agent rosters and MCP instructions from static tool definitions
and system prompt sections, delivering changes as message attachments to prevent
prefix cache invalidation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from langchain_core.messages import HumanMessage

from app.loop.ledger import PhaseLedger
from app.loop.messages import Message


@dataclass(frozen=True, slots=True)
class AgentListingDelta:
    """Represents changes in the pool of available agents."""

    added_types: tuple[str, ...]
    added_lines: tuple[str, ...]
    removed_types: tuple[str, ...]
    is_initial: bool


@dataclass(frozen=True, slots=True)
class McpInstructionsDelta:
    """Represents changes in connected MCP server instructions."""

    added_names: tuple[str, ...]
    added_blocks: tuple[str, ...]
    removed_names: tuple[str, ...]


class DeltaManager:
    """Manages the announcement and reconstruction of dynamic context deltas."""

    def extract_announced_agents(self, messages: Sequence[Message]) -> set[str] | None:
        """
        Reconstruct the set of currently announced agents from historical messages.

        Returns None if no agent listing delta has ever been announced in messages.
        """
        announced: set[str] = set()
        seen_any = False

        for msg in messages:
            kwargs = getattr(msg, "additional_kwargs", None)
            if not isinstance(kwargs, dict):
                continue
            delta_info = kwargs.get("agent_listing_delta")
            if not isinstance(delta_info, dict):
                continue

            seen_any = True
            is_initial = delta_info.get("is_initial", False)
            if is_initial:
                announced = set(delta_info.get("added_types", []))
            else:
                for added in delta_info.get("added_types", []):
                    announced.add(added)
                for removed in delta_info.get("removed_types", []):
                    announced.discard(removed)

        return announced if seen_any else None

    def compute_agent_delta(
        self,
        current_agents: Mapping[str, str],
        messages: Sequence[Message],
    ) -> AgentListingDelta | None:
        """
        Compute diff between current agents and historical announcements.

        Returns None if no change.
        """
        announced = self.extract_announced_agents(messages)

        if announced is None:
            # Initial announcement
            added_types = tuple(sorted(current_agents.keys()))
            added_lines = tuple(
                f"- {name}: {current_agents[name]}" for name in added_types
            )
            return AgentListingDelta(
                added_types=added_types,
                added_lines=added_lines,
                removed_types=(),
                is_initial=True,
            )

        current_set = set(current_agents.keys())
        added = sorted(current_set - announced)
        removed = sorted(announced - current_set)

        if not added and not removed:
            return None

        added_lines = tuple(f"- {name}: {current_agents[name]}" for name in added)
        return AgentListingDelta(
            added_types=tuple(added),
            added_lines=added_lines,
            removed_types=tuple(removed),
            is_initial=False,
        )

    def format_agent_delta_message(self, delta: AgentListingDelta) -> Message:
        """Format an AgentListingDelta into a conversation message with attachment metadata."""
        lines: list[str] = ["<system-attachment type=\"agent_listing_delta\">"]
        if delta.is_initial:
            lines.append("Available agents:")
            lines.extend(delta.added_lines)
        else:
            if delta.added_types:
                lines.append(f"Newly available agents: {', '.join(delta.added_types)}")
                lines.extend(delta.added_lines)
            if delta.removed_types:
                lines.append(f"Removed agents: {', '.join(delta.removed_types)}")
        lines.append("</system-attachment>")

        return HumanMessage(
            content="\n".join(lines),
            additional_kwargs={
                "agent_listing_delta": {
                    "is_initial": delta.is_initial,
                    "added_types": list(delta.added_types),
                    "removed_types": list(delta.removed_types),
                }
            },
        )

    def extract_announced_mcp_servers(self, messages: Sequence[Message]) -> set[str] | None:
        """Reconstruct the set of currently announced MCP servers from historical messages."""
        announced: set[str] = set()
        seen_any = False

        for msg in messages:
            kwargs = getattr(msg, "additional_kwargs", None)
            if not isinstance(kwargs, dict):
                continue
            delta_info = kwargs.get("mcp_instructions_delta")
            if not isinstance(delta_info, dict):
                continue

            seen_any = True
            for added in delta_info.get("added_names", []):
                announced.add(added)
            for removed in delta_info.get("removed_names", []):
                announced.discard(removed)

        return announced if seen_any else None

    def compute_mcp_delta(
        self,
        current_servers: Mapping[str, str],
        messages: Sequence[Message],
    ) -> McpInstructionsDelta | None:
        """Compute diff between current MCP servers and historical announcements."""
        announced = self.extract_announced_mcp_servers(messages)

        if announced is None:
            added_names = tuple(sorted(current_servers.keys()))
            added_blocks = tuple(
                f"## {name}\n{current_servers[name]}" for name in added_names
            )
            return McpInstructionsDelta(
                added_names=added_names,
                added_blocks=added_blocks,
                removed_names=(),
            )

        current_set = set(current_servers.keys())
        added = sorted(current_set - announced)
        removed = sorted(announced - current_set)

        if not added and not removed:
            return None

        added_blocks = tuple(f"## {name}\n{current_servers[name]}" for name in added)
        return McpInstructionsDelta(
            added_names=tuple(added),
            added_blocks=added_blocks,
            removed_names=tuple(removed),
        )

    def format_mcp_delta_message(self, delta: McpInstructionsDelta) -> Message:
        """Format an McpInstructionsDelta into a conversation message with attachment metadata."""
        lines: list[str] = ["<system-attachment type=\"mcp_instructions_delta\">"]
        if delta.added_names:
            lines.append(f"Connected MCP servers: {', '.join(delta.added_names)}")
            lines.extend(delta.added_blocks)
        if delta.removed_names:
            lines.append(f"Disconnected MCP servers: {', '.join(delta.removed_names)}")
        lines.append("</system-attachment>")

        return HumanMessage(
            content="\n".join(lines),
            additional_kwargs={
                "mcp_instructions_delta": {
                    "added_names": list(delta.added_names),
                    "removed_names": list(delta.removed_names),
                }
            },
        )


# ── TDD run state ────────────────────────────────────────────────────────────

TDD_STATE_KEY = "tdd_state"


def wrap_in_system_reminder(content: str) -> str:
    """`src/utils/messages.ts` -> `wrapInSystemReminder`."""
    return f"<system-reminder>\n{content}\n</system-reminder>"


def tdd_state_payload(ledger: PhaseLedger, todo: str | None) -> dict[str, object]:
    return {
        "phase": str(ledger.phase),
        "red_confirmed": ledger.red_confirmed,
        "green_passed": ledger.green_passed,
        "todo": todo,
    }


def last_announced_tdd_state(messages: Sequence[Message]) -> dict[str, object] | None:
    for msg in reversed(messages):
        kwargs = getattr(msg, "additional_kwargs", None)
        if isinstance(kwargs, dict) and isinstance(kwargs.get(TDD_STATE_KEY), dict):
            state: dict[str, object] = kwargs[TDD_STATE_KEY]
            return state
    return None


def compute_tdd_state_attachment(
    messages: Sequence[Message], ledger: PhaseLedger, todo: str | None = None
) -> Message | None:
    """
    The run's TDD state as an attachment, announced only when it changed.

    The ledger is written by `RunTests` alone (Part D2), so this is an observation, not the
    model's claim. Delivered as a meta user message wrapped in `<system-reminder>`, the shape
    upstream uses for its todo reminder, so the system prompt prefix stays cacheable.
    """
    payload = tdd_state_payload(ledger, todo)
    if last_announced_tdd_state(messages) == payload:
        return None
    lines = [
        "TDD phase ledger (written only by RunTests from observed test runs):",
        f"Current Phase: {ledger.phase}",
        f"Red Confirmed: {ledger.red_confirmed}",
        f"Green Passed: {ledger.green_passed}",
    ]
    if todo is not None and todo.strip():
        lines += ["", "Contents of TODO.md:", todo.strip()]
    return HumanMessage(
        content=wrap_in_system_reminder("\n".join(lines)),
        additional_kwargs={"is_meta": True, TDD_STATE_KEY: payload},
    )
