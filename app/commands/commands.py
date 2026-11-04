"""
The REPL's slash commands (Phase 11) and the command registry.

Ported from `reference/claude-code/src/commands.ts` -> `getCommands`, `builtInCommandNames`,
`findCommand`, `hasCommand`, `getCommand`, `formatDescriptionWithSource`, and
`types/command.ts` -> `getCommandName`. Upstream merges bundled, skill, plugin and built-in
commands into one list and dispatches by type: TDDAgents' built-ins are `local` (they run in the
session and report to the user, `app/screens/repl.py`), except `/init` (`app/commands/init.py`), a
built-in `prompt` command the skill registry holds after every skill (`set_built_in_commands`);
skills and `/init` are `prompt` commands, which a typed `/<name> args` expands into the turn
(`app/utils/process_user_input/process_slash_command.py`). `/help` lists `/init` among the
commands, and only the other prompt commands under "Skills:". Not ported: plugins, workflows, MCP
prompts and skills, the remote/bridge filters, availability requirements (no auth types) and the
memoized command cache (the list is built from the run's skill registry each time).

`/reload-plugins` is ported from `reference/claude-code/src/commands/reload-plugins/index.ts` and
`reload-plugins.ts` -> `call`: the run's `refresh_plugins` (`refreshActivePlugins`), reported as
`Reloaded: N plugins · N skills · …` (`reload_plugins_message`). The `CLAUDE_CODE_REMOTE` user
settings re-download is not ported (no settings sync). The `/plugin` dialog (`commands/plugin/`,
React/Ink) is not ported: the `plugin` CLI subcommand does its work (`app/cli/handlers/plugins.py`).

`/context` is ported from `reference/claude-code/src/commands/context/index.ts` ->
`contextNonInteractive` and `context-noninteractive.ts` -> `collectContextData`, `call`,
`formatContextAsMarkdownTable`: the Markdown report, which upstream's interactive build replaces
with a coloured grid (`context.tsx`, `components/ContextVisualization.tsx`, React/Ink, not
ported). `collect_context_data` is also what the SDK's `get_context_usage` control request
answers with (`cli/print.ts`). Upstream keeps it in `src/commands/context/`; here it lives in this
module. Not ported: the context-collapse status
(`CONTEXT_COLLAPSE`, off in the external build) and the `[ANT-ONLY]` tables.

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
| `/context` | Show current context usage |
| `/reload-plugins` | Activate pending plugin changes in the current session |
| `/mcp` | Manage MCP servers; `/mcp enable [server]`, `/mcp disable [server]`, `/mcp reconnect <server>` |
| `/hooks` | View hook configurations for tool events (`app/commands/hooks/hooks.py`) |
| `/doctor` | Diagnose the installation and settings (`app/commands/doctor/`); off under `DISABLE_DOCTOR_COMMAND` |
| `/init` | Ask the agents to write the project's `TDDAGENTS.md` (a `prompt` command) |
| `/help` | This list |
| `/exit`, `/quit` | Leave (the session can be continued with `--thread-id`) |

As upstream, a slash command never runs mid-turn: one typed while the agents work waits for
the turn to end. `/help`, `/plan` (showing), `/pause`, `/mcp`, `/hooks` and `/exit` are immediate.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.commands.doctor.doctor import DESCRIPTION as DOCTOR_DESCRIPTION
from app.commands.doctor.doctor import is_enabled as is_doctor_enabled
from app.services.compact.compact import get_messages_after_compact_boundary
from app.services.compact.micro_compact import microcompact_messages
from app.tdd.plan import PlanError, TddPlan
from app.tool import Tool
from app.utils.analyze_context import (
    FREE_SPACE_NAME,
    RESERVED_CATEGORY_NAME,
    ContextData,
    TokenCounter,
    analyze_context_usage,
    rough_count_tokens,
)
from app.utils.format import format_tokens
from app.utils.messages import Message
from app.utils.settings.constants import get_source_display_name

COMMANDS = ("plan", "pause", "resume", "requirements", "context", "mcp", "hooks", "doctor", "reload-plugins",
            "help", "exit", "quit")

#: Commands that run even while a turn is in flight (upstream's `immediate`).
IMMEDIATE = frozenset({"help", "pause", "mcp", "hooks", "exit", "quit"})

HELP_TEXT = """Commands:
  /plan                         show the TDD plan
  /plan add <text>              add a sub-requirement
  /plan edit <id> <text>        reword a sub-requirement (a done one is reopened)
  /plan remove <id>             remove a sub-requirement
  /plan move <id> <position>    move a sub-requirement
  /pause                        let the agents stop after the current turn
  /resume                       continue the plan
  /requirements <request>       enter plan mode and plan a new request
  /context                      show current context usage
  /reload-plugins               activate pending plugin changes in the current session
  /mcp                          manage MCP servers ([enable|disable [server-name]])
  /hooks                        view hook configurations for tool events
  /doctor                       diagnose and verify your TDDAgents installation and settings
  /init                         initialize a new TDDAGENTS.md file with codebase documentation
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
    argument_hint: str | None = None
    #: `pluginInfo.pluginManifest.name` of a plugin's command.
    plugin_name: str | None = None

    def user_facing_name(self) -> str:
        """`getCommandName`: the display name when there is one."""
        return self.display_name or self.name


#: The built-in commands' one-line descriptions.
BUILT_IN_DESCRIPTIONS: dict[str, str] = {
    "plan": "Show or change the TDD plan",
    "pause": "Let the agents stop after the current turn",
    "resume": "Continue the plan",
    "requirements": "Enter plan mode and plan a new request",
    #: `contextNonInteractive.description`.
    "context": "Show current context usage",
    #: `reloadPlugins.description`.
    "reload-plugins": "Activate pending plugin changes in the current session",
    #: `commands/mcp/index.ts`.
    "mcp": "Manage MCP servers",
    #: `commands/hooks/index.ts`.
    "hooks": "View hook configurations for tool events",
    #: `commands/doctor/index.ts`.
    "doctor": DOCTOR_DESCRIPTION,
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


def is_command_enabled(name: str) -> bool:
    """A built-in's `isEnabled`: `/doctor` can be turned off (`DISABLE_DOCTOR_COMMAND`), the rest cannot."""
    return name != "doctor" or is_doctor_enabled()


def built_in_commands() -> list[Command]:
    """The REPL's enabled `local` commands."""
    return [Command(name, description, "local", aliases=BUILT_IN_ALIASES.get(name, ()))
            for name, description in BUILT_IN_DESCRIPTIONS.items() if is_command_enabled(name)]


def base_help_text() -> str:
    """`HELP_TEXT` without the lines of the commands that are turned off."""
    if is_command_enabled("doctor"):
        return HELP_TEXT
    return "\n".join(line for line in HELP_TEXT.split("\n") if not line.startswith("  /doctor "))


def built_in_command_names() -> frozenset[str]:
    """`builtInCommandNames`: every built-in name and alias, enabled or not."""
    return frozenset(n for name in BUILT_IN_DESCRIPTIONS for n in (name, *BUILT_IN_ALIASES.get(name, ())))


def get_commands(skill_registry: Any) -> list[Command]:
    """`getCommands`: the skills, plugin commands and `/init` (as `prompt` commands), then the built-ins."""
    skills = [] if skill_registry is None else [
        Command(s.name, s.description, "prompt", source=s.source, is_hidden=s.is_hidden, display_name=s.display_name,
                argument_hint=s.argument_hint, plugin_name=getattr(s, "plugin_name", None))
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
    if command.type != "prompt":
        return command.description
    if command.source == "plugin":
        return f"({command.plugin_name}) {command.description}" if command.plugin_name else \
            f"{command.description} (plugin)"
    if command.source in ("builtin", "mcp"):
        return command.description
    if command.source == "bundled":
        return f"{command.description} (bundled)"
    return f"{command.description} ({SOURCE_NAMES.get(command.source, command.source)})"


def help_text(commands: Sequence[Command]) -> str:
    """`/help`: the built-ins (`/init` among them), then the skills the user can type."""
    skills = [c for c in commands if c.type == "prompt" and not c.is_hidden and c.source != "builtin"]
    if not skills:
        return base_help_text()
    width = max(len(c.user_facing_name()) for c in skills) + 3
    lines = [f"  /{c.user_facing_name():<{width}}{format_description_with_source(c)}" for c in skills]
    return base_help_text() + "\nSkills:\n" + "\n".join(lines)


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
    if name not in COMMANDS or not is_command_enabled(name):
        return None
    return SlashCommand(name=name, args=args.strip())


def reload_plugins_message(result: Any) -> str:
    """
    `/reload-plugins`' report (`reload-plugins.ts` -> `call`): the counts, and the errors when any.
    The MCP and LSP counts are the plugins' own servers (`gh-31321`).
    """
    from app.utils.string_utils import plural

    def n(count: int, noun: str) -> str:
        return f"{count} {plural(count, noun)}"

    parts = [
        n(result.enabled_count, "plugin"), n(result.command_count, "skill"), n(result.agent_count, "agent"),
        n(result.hook_count, "hook"), n(result.mcp_count, "plugin MCP server"),
        n(result.lsp_count, "plugin LSP server"),
    ]
    message = "Reloaded: " + " · ".join(parts)
    if result.error_count > 0:
        from app.services.plugins.plugin_operations import PLUGIN_CLI

        # Upstream: "Run /doctor for details." (no /doctor here; user decision 2026-11-02).
        message += f"\n{n(result.error_count, 'error')} during load. Run `{PLUGIN_CLI} list` for details."
    return message


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


# ── /context ──────────────────────────────────────────────────────────────────

async def collect_context_data(
    messages: Sequence[Message],
    *,
    main_loop_model: str,
    tools: Sequence[Tool],
    agent_definitions: Mapping[str, Any] | Iterable[Any],
    tool_context: Any,
    custom_system_prompt: str | None = None,
    append_system_prompt: str | None = None,
    count_tokens: TokenCounter = rough_count_tokens,
) -> ContextData:
    """
    `collectContextData`: the shared path of `/context` and the SDK's `get_context_usage`. It
    applies the request's transforms first (from the last compact boundary on, then
    microcompact), so the count is what the model sees; the messages before microcompact give
    the API usage. `tool_context` is the run's main-thread `ToolContext` (`analyze_context_usage`).
    """
    api_view = get_messages_after_compact_boundary(messages)
    compacted = microcompact_messages(api_view, is_main_thread=True).messages
    return await analyze_context_usage(
        compacted, main_loop_model, tools, agent_definitions, tool_context,
        custom_system_prompt=custom_system_prompt, append_system_prompt=append_system_prompt,
        original_messages=api_view, count_tokens=count_tokens)


#: `formatContextAsMarkdownTable`'s agent-source switch (`Policy` here, not `Managed`).
AGENT_SOURCE_DISPLAY: dict[str, str] = {
    "projectSettings": "Project",
    "userSettings": "User",
    "localSettings": "Local",
    "flagSettings": "Flag",
    "policySettings": "Policy",
    "plugin": "Plugin",
    "built-in": "Built-in",
}


def _percent(tokens: int, total: int) -> str:
    """`((tokens / total) * 100).toFixed(1)`: a tie rounds up, as `toFixed` does."""
    return str(Decimal(tokens / total * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def format_context_as_markdown_table(data: ContextData) -> str:
    """`formatContextAsMarkdownTable`: the report `/context` prints outside the interactive grid."""
    raw_max = data["rawMaxTokens"]
    categories = data["categories"]
    output = "## Context Usage\n\n"
    output += f"**Model:** {data['model']}  \n"
    output += f"**Tokens:** {format_tokens(data['totalTokens'])} / {format_tokens(raw_max)} ({data['percentage']}%)\n"
    output += "\n"

    visible = [c for c in categories
               if c["tokens"] > 0 and c["name"] not in (FREE_SPACE_NAME, RESERVED_CATEGORY_NAME)]
    if visible:
        output += "### Estimated usage by category\n\n"
        output += "| Category | Tokens | Percentage |\n"
        output += "|----------|--------|------------|\n"
        for cat in visible:
            output += f"| {cat['name']} | {format_tokens(cat['tokens'])} | {_percent(cat['tokens'], raw_max)}% |\n"
        for name in (FREE_SPACE_NAME, RESERVED_CATEGORY_NAME):
            found = next((c for c in categories if c["name"] == name), None)
            if found is not None and found["tokens"] > 0:
                output += f"| {name} | {format_tokens(found['tokens'])} | {_percent(found['tokens'], raw_max)}% |\n"
        output += "\n"

    if data["mcpTools"]:
        output += "### MCP Tools\n\n"
        output += "| Tool | Server | Tokens |\n"
        output += "|------|--------|--------|\n"
        for tool in data["mcpTools"]:
            output += f"| {tool['name']} | {tool['serverName']} | {format_tokens(tool['tokens'])} |\n"
        output += "\n"

    if data["agents"]:
        output += "### Custom Agents\n\n"
        output += "| Agent Type | Source | Tokens |\n"
        output += "|------------|--------|--------|\n"
        for agent in data["agents"]:
            source = AGENT_SOURCE_DISPLAY.get(agent["source"], str(agent["source"]))
            output += f"| {agent['agentType']} | {source} | {format_tokens(agent['tokens'])} |\n"
        output += "\n"

    if data["memoryFiles"]:
        output += "### Memory Files\n\n"
        output += "| Type | Path | Tokens |\n"
        output += "|------|------|--------|\n"
        for memory_file in data["memoryFiles"]:
            output += f"| {memory_file['type']} | {memory_file['path']} | {format_tokens(memory_file['tokens'])} |\n"
        output += "\n"

    skills = data.get("skills")
    if skills is not None and skills["tokens"] > 0 and skills["skillFrontmatter"]:
        output += "### Skills\n\n"
        output += "| Skill | Source | Tokens |\n"
        output += "|-------|--------|--------|\n"
        for skill in skills["skillFrontmatter"]:
            output += (f"| {skill['name']} | {get_source_display_name(skill['source'])} | "
                       f"{format_tokens(skill['tokens'])} |\n")
        output += "\n"

    return output
