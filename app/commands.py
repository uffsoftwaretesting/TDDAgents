"""
The REPL's slash commands (Phase 11) and the command registry.

Ported from `reference/claude-code/src/commands.ts` -> `getCommands`, `builtInCommandNames`,
`findCommand`, `hasCommand`, `getCommand`, `formatDescriptionWithSource`, and
`types/command.ts` -> `getCommandName`. Upstream merges bundled, skill, plugin and built-in
commands into one list and dispatches by type: TDDAgents' built-ins are `local` (they run in the
session and report to the user, `app/screens/repl.py`); its skills are `prompt` commands, which
a typed `/<skill> args` expands into the turn
(`app/utils/process_user_input/process_slash_command.py`). Not ported: plugins, workflows, MCP
prompts and skills, the remote/bridge filters, availability requirements (no auth types) and the
memoized command cache (the list is built from the run's skill registry each time).

| Command | What it does |
|---|---|
| `/plan` | Show the TDD plan |
| `/plan add <text>` | Add a sub-requirement at the end |
| `/plan edit <id> <text>` | Reword one (a done one reopens) |
| `/plan remove <id>` | Remove one |
| `/plan move <id> <position>` | Reorder |
| `/pause` | Let the agents stop after the current turn, without finishing the plan |
| `/resume` | Undo `/pause` and continue the plan |
| `/requirements <request>` | Enter plan mode and plan the new request (upstream's `/plan <description>`) |
| `/help` | This list |
| `/exit`, `/quit` | Leave (the session can be continued with `--thread-id`) |

As upstream, a slash command never runs mid-turn: one typed while the agents work waits for
the turn to end. `/help`, `/plan` (showing), `/pause` and `/exit` are immediate.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.tdd.plan import PlanError, TddPlan

COMMANDS = ("plan", "pause", "resume", "requirements", "help", "exit", "quit")

#: Commands that run even while a turn is in flight (upstream's `immediate`).
IMMEDIATE = frozenset({"help", "pause", "exit", "quit"})

HELP_TEXT = """Commands:
  /plan                         show the TDD plan
  /plan add <text>              add a sub-requirement
  /plan edit <id> <text>        reword a sub-requirement (a done one is reopened)
  /plan remove <id>             remove a sub-requirement
  /plan move <id> <position>    move a sub-requirement
  /pause                        let the agents stop after the current turn
  /resume                       continue the plan
  /requirements <request>       enter plan mode and plan a new request
  /help                         this list
  /exit, /quit                  leave (continue later with --thread-id)
Anything else is a message to the agents; Esc interrupts them."""


@dataclass(frozen=True, slots=True)
class Command:
    """`Command`, the fields this REPL uses."""

    name: str
    description: str
    type: str  # "local" or "prompt"
    source: str = "builtin"
    aliases: tuple[str, ...] = ()
    is_hidden: bool = False
    display_name: str | None = None

    def user_facing_name(self) -> str:
        """`getCommandName`: the display name when there is one."""
        return self.display_name or self.name


#: The built-in commands' one-line descriptions.
BUILT_IN_DESCRIPTIONS: dict[str, str] = {
    "plan": "Show or change the TDD plan",
    "pause": "Let the agents stop after the current turn",
    "resume": "Continue the plan",
    "requirements": "Enter plan mode and plan a new request",
    "help": "Show the commands",
    "exit": "Leave (continue later with --thread-id)",
}
#: `/quit` is `/exit`'s alias.
BUILT_IN_ALIASES: dict[str, tuple[str, ...]] = {"exit": ("quit",)}

#: `getSettingSourceName`, plus `bundled`.
SOURCE_NAMES: dict[str, str] = {
    "userSettings": "user", "projectSettings": "project", "localSettings": "project, gitignored",
    "flagSettings": "cli flag", "policySettings": "managed",
}


def built_in_commands() -> list[Command]:
    """The REPL's `local` commands."""
    return [Command(name, description, "local", aliases=BUILT_IN_ALIASES.get(name, ()))
            for name, description in BUILT_IN_DESCRIPTIONS.items()]


def built_in_command_names() -> frozenset[str]:
    """`builtInCommandNames`: every built-in name and alias."""
    return frozenset(n for c in built_in_commands() for n in (c.name, *c.aliases))


def get_commands(skill_registry: Any) -> list[Command]:
    """`getCommands`: the skills (as `prompt` commands), then the built-ins."""
    skills = [] if skill_registry is None else [
        Command(s.name, s.description, "prompt", source=s.source, is_hidden=s.is_hidden, display_name=s.display_name)
        for s in skill_registry.commands()
    ]
    return [*skills, *built_in_commands()]


def find_command(name: str, commands: Sequence[Command]) -> Command | None:
    """`findCommand`: by name, display name or alias."""
    return next((c for c in commands if c.name == name or c.user_facing_name() == name or name in c.aliases), None)


def has_command(name: str, commands: Sequence[Command]) -> bool:
    """`hasCommand`."""
    return find_command(name, commands) is not None


def get_command(name: str, commands: Sequence[Command]) -> Command:
    """`getCommand`: the command, or `LookupError` listing what exists."""
    command = find_command(name, commands)
    if command is None:
        available = sorted(
            f"{c.user_facing_name()} (aliases: {', '.join(c.aliases)})" if c.aliases else c.user_facing_name()
            for c in commands)
        raise LookupError(f"Command {name} not found. Available commands: {', '.join(available)}")
    return command


def format_description_with_source(command: Command) -> str:
    """`formatDescriptionWithSource`: a prompt command's description with where it comes from."""
    if command.type != "prompt" or command.source in ("builtin", "mcp"):
        return command.description
    if command.source == "bundled":
        return f"{command.description} (bundled)"
    return f"{command.description} ({SOURCE_NAMES.get(command.source, command.source)})"


def help_text(commands: Sequence[Command]) -> str:
    """`/help`: the built-ins, then the skills the user can type."""
    skills = [c for c in commands if c.type == "prompt" and not c.is_hidden]
    if not skills:
        return HELP_TEXT
    width = max(len(c.user_facing_name()) for c in skills) + 3
    lines = [f"  /{c.user_facing_name():<{width}}{format_description_with_source(c)}" for c in skills]
    return HELP_TEXT + "\nSkills:\n" + "\n".join(lines)


class CommandError(ValueError):
    """A slash command that cannot run as typed; the message says how to type it."""


@dataclass(frozen=True, slots=True)
class SlashCommand:
    name: str
    args: str = ""
    #: `local` (a built-in) or `prompt` (a skill the user typed).
    kind: str = "local"

    @property
    def immediate(self) -> bool:
        """Runs mid-turn: the immediate commands, and `/plan` with nothing to change."""
        return self.kind == "local" and (self.name in IMMEDIATE or (self.name == "plan" and not self.args))


def parse_slash_command(line: str) -> SlashCommand | None:
    """`/name args` as a command; `None` for anything that is not one (a message to the agents)."""
    stripped = line.strip()
    if not stripped.startswith("/"):
        return None
    name, _, args = stripped[1:].partition(" ")
    if name not in COMMANDS:
        return None
    return SlashCommand(name=name, args=args.strip())


def _int(text: str, usage: str) -> int:
    try:
        return int(text)
    except ValueError:
        raise CommandError(usage) from None


def apply_plan_command(plan: TddPlan, args: str) -> TddPlan:
    """The plan after `/plan <args>`; `CommandError` with the usage when it cannot apply."""
    verb, _, rest = args.partition(" ")
    rest = rest.strip()
    try:
        if verb == "add":
            if not rest:
                raise CommandError("Usage: /plan add <text>")
            return plan.add(rest)
        if verb == "edit":
            item_id, _, text = rest.partition(" ")
            if not item_id or not text.strip():
                raise CommandError("Usage: /plan edit <id> <text>")
            return plan.edit(item_id, text)
        if verb == "remove":
            if not rest or " " in rest:
                raise CommandError("Usage: /plan remove <id>")
            return plan.remove(rest)
        if verb == "move":
            item_id, _, position = rest.partition(" ")
            usage = "Usage: /plan move <id> <position>"
            if not item_id or not position.strip():
                raise CommandError(usage)
            return plan.move(item_id, _int(position.strip(), usage))
    except PlanError as exc:
        raise CommandError(str(exc)) from None
    raise CommandError("Usage: /plan [add <text> | edit <id> <text> | remove <id> | move <id> <position>]")
