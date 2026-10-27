"""
The REPL's slash commands (Phase 11).

Upstream's `commands.ts` merges bundled, skill, plugin and built-in commands into one list and
dispatches by type (`prompt`, `local`, `local-jsx`). TDDAgents' REPL has only `local` commands
— they run in the session and report to the user, never sending a prompt themselves — so this
module is the parser and the help text; `app/screens/repl.py` runs them.

| Command | What it does |
|---|---|
| `/plan` | Show the TDD plan |
| `/plan add <text>` | Add a sub-requirement at the end |
| `/plan edit <id> <text>` | Reword one (a done one reopens) |
| `/plan remove <id>` | Remove one |
| `/plan move <id> <position>` | Reorder |
| `/pause` | Let the agents stop after the current turn, without finishing the plan |
| `/resume` | Undo `/pause` and continue the plan |
| `/requirements <request>` | Re-run the analyst, the engineer and the planner with a new request |
| `/help` | This list |
| `/exit`, `/quit` | Leave (the session can be continued with `--thread-id`) |

As upstream, a slash command never runs mid-turn: one typed while the agents work waits for
the turn to end. `/help`, `/plan` (showing), `/pause` and `/exit` are immediate.
"""

from __future__ import annotations

from dataclasses import dataclass

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
  /requirements <request>       re-run the analyst, engineer and planner
  /help                         this list
  /exit, /quit                  leave (continue later with --thread-id)
Anything else is a message to the agents; Esc interrupts them."""


class CommandError(ValueError):
    """A slash command that cannot run as typed; the message says how to type it."""


@dataclass(frozen=True, slots=True)
class SlashCommand:
    name: str
    args: str = ""

    @property
    def immediate(self) -> bool:
        """Runs mid-turn: the immediate commands, and `/plan` with nothing to change."""
        return self.name in IMMEDIATE or (self.name == "plan" and not self.args)


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
