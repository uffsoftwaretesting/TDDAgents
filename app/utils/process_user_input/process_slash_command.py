"""
A typed `/<skill> args`: the messages that load the skill into the turn.

Ported from `reference/claude-code/src/utils/processUserInput/processSlashCommand.tsx` ->
`looksLikeCommand`, `formatSlashCommandLoadingMetadata`, `formatSkillLoadingMetadata`,
`formatCommandLoadingMetadata`, the unknown-command branch of `processSlashCommand` ("Unknown
skill: …") and `getMessagesForPromptSlashCommand`: a user message carrying the command's loading
metadata, then the skill's text as a meta message; the skill's `allowed-tools` and `model` apply
to the turn (upstream's `command_permissions` attachment). In coordinator mode the main thread
gets the skill described for its workers instead (`coordinator_skill_summary`). Not ported: skill
hooks registered from a typed command and the invoked-skill bookkeeping.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage

from app.tools.skill_tool.skill_tool import (
    coordinator_skill_summary,
    format_skill_loading_metadata,
)
from app.utils.settings.settings import parse_tool_list_from_cli

COMMAND_MESSAGE_TAG = "command-message"
COMMAND_NAME_TAG = "command-name"


def looks_like_command(name: str) -> bool:
    """`looksLikeCommand`: only letters, digits, `:`, `-` and `_` (so `/usr/bin` is a path, not a command)."""
    return re.search(r"[^a-zA-Z0-9:\-_]", name) is None


def parse_slash_input(line: str) -> tuple[str, str] | None:
    """`parseSlashCommand`: (`name`, `args`) of a `/name args` line, `None` for anything else."""
    stripped = line.strip()
    if not stripped.startswith("/") or len(stripped) == 1:
        return None
    name, _, args = stripped[1:].partition(" ")
    return name, args.strip()


def format_slash_command_loading_metadata(name: str, args: str = "") -> str:
    """`formatSlashCommandLoadingMetadata`."""
    lines = [f"<{COMMAND_MESSAGE_TAG}>{name}</{COMMAND_MESSAGE_TAG}>",
             f"<{COMMAND_NAME_TAG}>/{name}</{COMMAND_NAME_TAG}>"]
    if args:
        lines.append(f"<command-args>{args}</command-args>")
    return "\n".join(lines)


def format_command_loading_metadata(command: Any, args: str = "") -> str:
    """`formatCommandLoadingMetadata`: `/name` for a user-invocable skill, the skill format otherwise."""
    if command.user_invocable or command.loaded_from not in ("skills", "plugin", "mcp"):
        return format_slash_command_loading_metadata(command.name, args)
    return format_skill_loading_metadata(command.name)


def unknown_skill_message(name: str, args: str) -> str:
    """The unknown-command reply: `Unknown skill: <name>`, and the args it got."""
    text = f"Unknown skill: {name}"
    return f"{text}\nArgs from unknown skill: {args}" if args else text


@dataclass(frozen=True, slots=True)
class PromptCommandTurn:
    """`SlashCommandResult` for a prompt command: the messages, and what applies to the turn."""

    messages: tuple[HumanMessage, ...]
    allowed_tools: tuple[str, ...] = field(default_factory=tuple)
    model: str | None = None


def get_messages_for_prompt_slash_command(command: Any, args: str, *, session_id: str = "",
                                          coordinator: bool = False) -> PromptCommandTurn:
    """`getMessagesForPromptSlashCommand`."""
    metadata = HumanMessage(content=format_command_loading_metadata(command, args))
    if coordinator:
        summary = HumanMessage(content=coordinator_skill_summary(command), additional_kwargs={"is_meta": True})
        return PromptCommandTurn((metadata, summary), model=command.model)
    content = HumanMessage(content=command.prompt_for_command(args, session_id=session_id),
                           additional_kwargs={"is_meta": True})
    allowed = tuple(parse_tool_list_from_cli(command.allowed_tools))
    return PromptCommandTurn((metadata, content), allowed, command.model)
