"""
The TDDAgents entry point.

A session is one conversation (`app/cli/print.py`) that opens in plan mode: the Analyst, Claude
Code's Plan Mode ported, gathers the requirements, writes the plan file (the engineering
specification and the `Implementation Plan` of sub-requirements) and asks for its approval with
`ExitPlanMode`. Once approved, the same conversation develops the sub-requirements test-first.

Two modes, as claude-code has them:

* **Interactive** (default): the use-case menu picks the initial request, and the session is a
  REPL (`app/screens/repl.py`): the user answers the Analyst's questions, approves the plan (or
  keeps planning, or edits it with `/edit`), then talks to the agents while they work, edits the
  plan (`/plan`), re-plans (`/requirements`) or interrupts (Esc).
* **Headless** (`-p/--print`, upstream `claude -p` -> `runHeadless`): the request comes from
  `--spec FILE`, the prompt argument or stdin, and nobody is asked anything. As upstream's `-p`,
  the plan's approval is denied, so a headless session plans and stops unplanned (exit 1). A
  question the agents ask (`AskUserQuestion`) stops the session there too, and
  `--thread-id <id> "<answer>"` continues it with the answer as the next prompt, as upstream's
  `claude -p --resume <id> "<prompt>"`. The exit code is 0 only when there is an approved plan and
  every sub-requirement completed its Red -> Green -> Refactor cycle.

`-p --thread-id <id> --rewind-files <user-message-id>` (upstream's hidden `--rewind-files`) puts
the session's files back as they were when that user message was sent, then exits; it needs
`TDDAGENTS_ENABLE_SDK_FILE_CHECKPOINTING`, as file checkpointing in any headless run does
(`app/utils/file_history.py`).

`--thread-id` continues a session from its transcript, TDD plan and plan file. It runs in the
thread's own project; `--permission-mode`, `--settings` and `--dangerously-skip-permissions` are
upstream's startup flags for it, and `--coordinator` turns on coordinator mode
(`TDDAGENTS_COORDINATOR_MODE`). `--plugin-dir <path>` (repeatable) loads a plugin directory for
this process only (upstream's session plugins, `<name>@inline`). `-d/--debug [filter]`,
`--debug-file <path>` and the hidden `-d2e/--debug-to-stderr` are upstream's debug options
(`app/utils/debug.py`): hook execution and settings reloads are written to
`<config home>/debug/<thread id>.txt`.

`plugin …` (alias `plugins`) is upstream's `claude plugin` subcommand (`main.tsx`'s `pluginCmd`,
handlers in `app/cli/handlers/plugins.py`): `validate`, `list`, `install`, `uninstall`, `enable`,
`disable`, `update`, and `marketplace add|list|remove|update`. It runs without a session; its
project scope is the directory it runs in, as upstream's, or with `--thread-id <id>` that session's
project folder (user decision 2026-11-02: the session projects live outside the repository;
`plugin_cli_locations`).

`doctor` is upstream's `claude doctor` (`main.tsx`'s `doctor` command, `app/cli/handlers/util.py` ->
`doctor_handler`): the `/doctor` screen without a session, for the directory it runs in, or with
`--thread-id <id>` that session's project folder (user decision 2026-11-04). `-v` / `--version`
prints `VERSION`, as upstream's `.version(...)`.
"""

import argparse
import logging
import os
import sys
import textwrap
from collections.abc import Sequence
from pathlib import Path
from typing import Any

# Allows running this file directly without losing absolute `app.*` imports.
if __package__ in (None, ""):
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

from dotenv import load_dotenv

from app.cli.print import DevelopmentOutcome, HeadlessOptions
from app.constants.product import CLI_COMMAND, VERSION
from app.components.trust_dialog.trust_dialog import TrustDialogExit
from app.utils.sandbox.sandbox_adapter import SandboxUnavailableError
from app.query.transitions import Terminal
from app.session.driver import (
    SessionOptions,
    derive_thread_id,
    develop_headless,
    develop_interactive,
    fresh_thread_id,
    rewind_files_headless,
    session_exists,
)
from app.tdd.plan import ItemStatus, TddPlan
from app.tools.ask_user_question_tool.ask_user_question_tool import render_questions
from app.types.permissions import PERMISSION_MODES
from app.utils.claudemd import is_env_truthy

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
# Silences verbose logs from base libraries
for noisy_logger in ("httpx", "httpcore", "anthropic", "e2b"):
    logging.getLogger(noisy_logger).setLevel(logging.ERROR)

logger = logging.getLogger("TDDMain")
logger.setLevel(logging.INFO)


def get_initial_user_prompt() -> str:
    """
    Displays the use-case menu by reading the .txt files from the specs directory,
    shows the standard disclaimer, and returns the prompt chosen (or typed) by the user.
    """
    warning_message = (
        "WARNING: The Analyst agent may ask for more details about the requirements, "
        "which will require more interaction and prompts from the user. For the purposes "
        "of the scientific research being conducted, we consider attempts where the Analyst "
        "agent did not ask for any additional implementation details and considered the initial "
        "prompt sufficient to elicit the requirements. Therefore, when using any of the prompt "
        "options provided, we recommend discarding any attempts that request further detail. "
        "Only consider attempts where the INITIAL prompt is sufficient for requirements elicitation.\n"
    )
    print(warning_message)

    # Resolves the path to the specs directory
    specs_dir = Path("app/prompts/specs")

    if not specs_dir.exists() or not specs_dir.is_dir():
        print(f"⚠️ Warning: Specs directory not found at '{specs_dir}'.\n")
        spec_files = []
    else:
        # Pulls the .txt files and sorts them alphabetically
        spec_files = sorted([f for f in specs_dir.iterdir() if f.suffix == '.txt'])

    print("USE CASE MENU (type the desired option):\n")
    for i, file_path in enumerate(spec_files, start=1):
        print(f"{i}. {file_path.name}")

    custom_input_option = len(spec_files) + 1
    print(f"{custom_input_option}. Type your own prompt\n")

    while True:
        try:
            choice_str = input("Select an option: ").strip()
            if not choice_str:
                continue

            choice = int(choice_str)

            # If the user chose one of the .txt files
            if 1 <= choice <= len(spec_files):
                selected_file = spec_files[choice - 1]
                with open(selected_file, "r", encoding="utf-8") as f:
                    prompt = f.read().strip()
                print(f"\n✅ [Prompt loaded from '{selected_file.name}']\n")
                return prompt

            # If the user decides to type their own prompt
            elif choice == custom_input_option:
                prompt = input("\n👤 [Your Initial Request]: ").strip()
                return prompt

            else:
                print("❌ Invalid option. Try again.")
        except ValueError:
            print("❌ Invalid input. Please type a number corresponding to the menu.")


def read_initial_request(args: argparse.Namespace) -> str | None:
    """
    The initial request from the command line: `--spec FILE`, else the prompt argument, else
    (headless) piped stdin. `None` when none was given.
    """
    if args.spec:
        return Path(args.spec).read_text(encoding="utf-8").strip()
    if args.prompt:
        return str(args.prompt).strip()
    if args.print and args.input_format != "stream-json" and not sys.stdin.isatty():
        return sys.stdin.read().strip() or None
    return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Runs the TDDAgents pipeline: requirements, plan, then test-driven development of the plan.",
    )
    parser.add_argument("prompt", nargs="?", default=None, help="The initial request (instead of the menu).")
    # `main.tsx`: `.version(`${MACRO.VERSION} (Claude Code)`, '-v, --version', 'Output the version number')`.
    parser.add_argument("-v", "--version", action="version", version=f"{VERSION} (TDDAgents)",
                        help="Output the version number")
    parser.add_argument("-p", "--print", action="store_true", help="Headless: ask nothing, exit non-zero on failure.")
    parser.add_argument("--spec", default=None, help="Read the initial request from this file.")
    parser.add_argument("--fresh", action="store_true", help="Forces a new run by generating a random thread_id.")
    parser.add_argument("--thread-id", default=None, help="Explicitly sets the thread_id (continues that session).")
    # `main.tsx`'s session flags: the thread id is the session id (user decision 2026-11-01).
    parser.add_argument("-c", "--continue", dest="continue_session", action="store_true",
                        help="Continue the most recent conversation")
    parser.add_argument("-r", "--resume", nargs="?", const=True, default=None,
                        help="Resume a conversation by session ID, or open interactive picker with optional "
                             "search term")
    parser.add_argument("--fork-session", action="store_true",
                        help="When resuming, create a new session ID instead of reusing the original "
                             "(use with --resume or --continue)")
    parser.add_argument("--session-id", default=None,
                        help="Use a specific session ID for the conversation (must be a valid UUID)")
    parser.add_argument("--permission-mode", choices=[str(m) for m in PERMISSION_MODES], default=None)
    parser.add_argument("--settings", default=None, help="A settings file (upstream's flag settings).")
    parser.add_argument("--dangerously-skip-permissions", action="store_true")
    parser.add_argument("--coordinator", action="store_true", help="Run development in coordinator mode.")
    # `main.tsx`'s debug options (`app/utils/debug.py`).
    parser.add_argument("-d", "--debug", nargs="?", const=True, default=None, metavar="filter",
                        help='Enable debug mode with optional category filtering (e.g., "api,hooks" or "!1p,!file")')
    parser.add_argument("-d2e", "--debug-to-stderr", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--debug-file", default=None, metavar="<path>",
                        help="Write debug logs to a specific file path (implicitly enables debug mode)")
    parser.add_argument("--verbose", action="store_true", help="Override verbose mode setting from config")
    # `main.tsx`'s SDK options (only with --print).
    parser.add_argument("--output-format", choices=OUTPUT_FORMATS, default=None,
                        help='Output format (only works with --print): "text" (default), "json" (single result), '
                             'or "stream-json" (realtime streaming)')
    parser.add_argument("--input-format", default=None,
                        help='Input format (only works with --print): "text" (default), or "stream-json" '
                             '(realtime streaming input)')
    parser.add_argument("--include-partial-messages", action="store_true",
                        help="Include partial message chunks as they arrive (only works with --print and "
                             "--output-format=stream-json)")
    parser.add_argument("--include-hook-events", action="store_true",
                        help="Include all hook lifecycle events in the output stream (only works with "
                             "--output-format=stream-json)")
    parser.add_argument("--replay-user-messages", action="store_true",
                        help="Re-emit user messages from stdin back on stdout for acknowledgment (only works with "
                             "--input-format=stream-json and --output-format=stream-json)")
    parser.add_argument("--permission-prompt-tool", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--max-turns", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--thinking", choices=("enabled", "adaptive", "disabled"), default=None,
                        help=argparse.SUPPRESS)
    parser.add_argument("--max-thinking-tokens", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--agents", type=agents_flag, default=None,
                        help='JSON object defining custom agents (e.g. \'{"reviewer": {"description": '
                             '"Reviews code", "prompt": "You are a code reviewer"}}\')')
    parser.add_argument("--json-schema", type=json_schema_flag, default=None,
                        help='JSON Schema for structured output validation. Example: '
                             '{"type":"object","properties":{"name":{"type":"string"}},"required":["name"]}')
    parser.add_argument("--max-budget-usd", type=max_budget_usd, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--system-prompt", default=None, help="System prompt to use for the session")
    parser.add_argument("--system-prompt-file", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--append-system-prompt", default=None,
                        help="Append a system prompt to the default system prompt")
    parser.add_argument("--append-system-prompt-file", default=None, help=argparse.SUPPRESS)
    # `main.tsx`: "Restore files to state at the specified user message and exit (requires --resume)",
    # hidden from the help (`.hideHelp()`).
    parser.add_argument("--rewind-files", default=None, metavar="<user-message-id>", help=argparse.SUPPRESS)
    # `main.tsx`: one directory per flag, repeatable; collected in order.
    parser.add_argument("--plugin-dir", dest="plugin_dir", action="append", default=[], metavar="<path>",
                        help="Load plugins from a directory for this session only "
                             "(repeatable: --plugin-dir A --plugin-dir B)")
    return parser


def apply_plugin_dirs(plugin_dirs: Sequence[str]) -> None:
    """
    `main.tsx`'s preAction for `--plugin-dir`: the directories become the session's inline
    plugins (`setInlinePlugins`), and the plugin loader's memo is cleared.
    """
    if not plugin_dirs:
        return
    from app.bootstrap.state import set_inline_plugins
    from app.utils.plugins.plugin_loader import clear_plugin_cache

    set_inline_plugins(list(plugin_dirs))
    clear_plugin_cache("preAction: --plugin-dir inline plugins")


def max_budget_usd(value: str) -> float:
    """`--max-budget-usd`'s parser (`main.tsx`): a positive number."""
    try:
        amount = float(value)
    except ValueError:
        amount = float("nan")
    if not amount > 0:
        raise argparse.ArgumentTypeError("--max-budget-usd must be a positive number greater than 0")
    return amount


def json_schema_flag(value: str) -> dict[str, Any]:
    """`--json-schema`'s `jsonParse`: a JSON object."""
    import json

    try:
        parsed = json.loads(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"--json-schema is not valid JSON: {error}") from error
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("--json-schema must be a JSON object")
    return parsed


def agents_flag(value: str) -> dict[str, Any]:
    """`--agents`: the JSON object of agents, parsed (`parseAgentsFromJson`, source `flagSettings`)."""
    import json

    from app.tools.agent_tool.load_agents_dir import parse_agents_from_json

    try:
        parsed = json.loads(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"--agents is not valid JSON: {error}") from error
    return dict(parse_agents_from_json(parsed))


#: `--output-format`'s choices.
OUTPUT_FORMATS = ("text", "json", "stream-json")


def validate_sdk_flags(args: argparse.Namespace) -> str | None:
    """`main.tsx`'s checks on the SDK flags, in its order: the first error, or `None`."""
    output_format = args.output_format or "text"
    input_format = args.input_format
    if input_format and input_format not in ("text", "stream-json"):
        return f'Error: Invalid input format "{input_format}".'
    if input_format == "stream-json" and output_format != "stream-json":
        return "Error: --input-format=stream-json requires output-format=stream-json."
    if args.replay_user_messages and (input_format != "stream-json" or output_format != "stream-json"):
        return "Error: --replay-user-messages requires both --input-format=stream-json and --output-format=stream-json."
    partial = args.include_partial_messages or is_env_truthy(os.environ.get("TDDAGENTS_INCLUDE_PARTIAL_MESSAGES"))
    if partial and (not args.print or output_format != "stream-json"):
        return "Error: --include-partial-messages requires --print and --output-format=stream-json."
    if args.print and output_format == "stream-json" and not args.verbose:
        return "Error: When using --print, --output-format=stream-json requires --verbose"
    return None


def headless_options(args: argparse.Namespace) -> HeadlessOptions:
    """The `-p` run's output and input options."""
    return HeadlessOptions(
        output_format=args.output_format or "text",
        input_format=args.input_format or "text",
        verbose=bool(args.verbose),
        include_partial_messages=bool(args.include_partial_messages)
        or is_env_truthy(os.environ.get("TDDAGENTS_INCLUDE_PARTIAL_MESSAGES")),
        replay_user_messages=bool(args.replay_user_messages),
        include_hook_events=bool(args.include_hook_events),
        permission_prompt_tool=args.permission_prompt_tool,
        max_turns=args.max_turns,
        max_budget_usd=args.max_budget_usd,
        json_schema=args.json_schema,
    )


class SessionFlagError(ValueError):
    """A session flag `main.tsx` refuses, with its message."""


def recent_threads(term: str | None = None) -> list[str]:
    """The sessions, most recently active first, whose id or first request contains `term`."""
    from app.cli.print import thread_transcript
    from app.workspace.local import workspaces_root

    root = workspaces_root()
    found: list[tuple[float, str]] = []
    for thread_dir in (root.iterdir() if root.is_dir() else ()):
        if not (thread_dir / "project").exists():
            continue
        transcript = thread_transcript(thread_dir.name)
        if not transcript.exists():
            continue
        if term and term.lower() not in (thread_dir.name + " " + first_request(thread_dir.name)).lower():
            continue
        found.append((transcript.stat().st_mtime, thread_dir.name))
    return [thread for _, thread in sorted(found, reverse=True)]


def first_request(thread_id: str) -> str:
    """The session's initial request, as its reports recorded it ('' when there is none)."""
    from app.workspace.local import workspaces_root

    path = workspaces_root() / thread_id / "metrics_and_logging" / "initial_user_prompt.txt"
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines()[2:] if line and set(line) != {"="}]
    except OSError:
        return ""
    return " ".join(lines)


def pick_thread(term: str | None) -> str | None:
    """The interactive `--resume` picker: a numbered list of the sessions; `None` when cancelled."""
    threads = recent_threads(term)
    if not threads:
        print("No conversations found to resume.")
        return None
    for i, thread in enumerate(threads, start=1):
        request = first_request(thread)
        print(f"{i}. {thread}  {request[:70]}")
    while True:
        try:
            choice = input("Select a conversation to resume (empty to cancel): ").strip()
        except (KeyboardInterrupt, EOFError):
            return None
        if not choice:
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(threads):
            return threads[int(choice) - 1]
        print("❌ Invalid option. Try again.")


def resolve_session_flags(args: argparse.Namespace) -> str | None:
    """
    `main.tsx` / `print.ts`'s `--continue`, `--resume`, `--session-id` and `--fork-session`: the
    thread to run (a fork's new one), or `None` when the flags name none. `SessionFlagError` carries
    upstream's message.
    """
    import uuid as uuid_module

    from app.cli.print import fork_thread, most_recent_thread, thread_transcript

    if args.session_id is not None:
        if (args.continue_session or args.resume) and not args.fork_session:
            raise SessionFlagError("Error: --session-id can only be used with --continue or --resume if "
                                   "--fork-session is also specified.")
        try:
            uuid_module.UUID(str(args.session_id))
        except ValueError:
            raise SessionFlagError("Error: Invalid session ID. Must be a valid UUID.") from None
        if thread_transcript(str(args.session_id)).exists():
            raise SessionFlagError(f"Error: Session ID {args.session_id} is already in use.")
    source: str | None = None
    if args.continue_session:
        source = most_recent_thread()
        if source is None:
            raise SessionFlagError("No conversation found to continue")
    elif isinstance(args.resume, str):
        if thread_transcript(args.resume).exists():
            source = args.resume
        elif args.print:
            raise SessionFlagError(f"No conversation found with session ID: {args.resume}")
        else:
            source = pick_thread(args.resume)
    elif args.resume is True:
        if args.print:
            raise SessionFlagError("Error: --resume requires a session ID when used with --print")
        source = pick_thread(None)
    if source is None:
        return str(args.session_id) if args.session_id is not None else None
    if args.fork_session:
        target = str(args.session_id) if args.session_id is not None else fresh_thread_id()
        fork_thread(source, target)
        return target
    return source


def resolve_thread_id(args: argparse.Namespace, initial_input: str | None) -> str:
    """`--thread-id`, else a fresh id with `--fresh`, else one derived from the request."""
    if args.thread_id:
        return str(args.thread_id)
    if args.fresh or initial_input is None:
        return fresh_thread_id()
    return derive_thread_id(initial_input)


def session_options(args: argparse.Namespace) -> SessionOptions:
    return SessionOptions(
        headless=bool(args.print),
        permission_mode=args.permission_mode,
        settings_path=args.settings,
        dangerously_skip_permissions=bool(args.dangerously_skip_permissions),
        verbose=bool(args.verbose),
        headless_options=headless_options(args) if args.print else None,
        system_prompt=system_prompt_flag(args.system_prompt, args.system_prompt_file),
        append_system_prompt=system_prompt_flag(args.append_system_prompt, args.append_system_prompt_file),
        thinking=args.thinking,
        max_thinking_tokens=args.max_thinking_tokens,
        agents=args.agents,
    )


def system_prompt_flag(text: str | None, file: str | None) -> str | None:
    """`--system-prompt` or its `-file` form, read (`main.tsx`); `None` when neither is given."""
    if file:
        return Path(file).read_text(encoding="utf-8")
    return text


def _write_token_report(artifacts_dir: str) -> None:
    """Logs token usage and persists a report to artifacts_dir/token_usage.txt."""
    from app.services.api.claude import get_global_token_tracker

    summary = get_global_token_tracker().summary()
    totals = summary["totals"]
    logger.info(
        "📊 Token Usage — total: %d | prompt: %d | completion: %d | cached: %d",
        totals["total_tokens"],
        totals["prompt_tokens"],
        totals["completion_tokens"],
        totals["cached_tokens"],
    )
    with open(os.path.join(artifacts_dir, "token_usage.txt"), "w", encoding="utf-8") as f:
        f.write("TOKEN USAGE REPORT\n")
        f.write("=" * 80 + "\n\n")
        f.write("BY MODEL:\n")
        for model, stats in summary["by_model"].items():
            f.write(f"  {model}:\n")
            f.writelines(f"    {k}: {v}\n" for k, v in stats.items())
        f.write("\nTOTALS:\n")
        f.writelines(f"  {k}: {v}\n" for k, v in totals.items())


def _write_text(path: str, title: str, body: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(title + "\n")
        f.write("=" * 80 + "\n")
        f.write(body + "\n")
        f.write("=" * 80 + "\n")


def planning_dialogue(transcript_path: str | None) -> str:
    """
    The planning phase as the user saw it, read from the transcript: the user's messages, the
    Analyst's text, the questions it asked with their answers, up to the approved `ExitPlanMode`.
    """
    import json

    if not transcript_path or not os.path.exists(transcript_path):
        return "(empty)"
    lines: list[str] = []
    with open(transcript_path, encoding="utf-8") as transcript:
        for raw in transcript:
            message = (json.loads(raw).get("message") or {})
            data = message.get("data") or {}
            content = data.get("content")
            text = content if isinstance(content, str) else "\n".join(
                str(block.get("text", "")) for block in content or []
                if isinstance(block, dict) and block.get("type") == "text"
            )
            kind = message.get("type")
            if kind == "human" and not (data.get("additional_kwargs") or {}).get("is_meta") and text.strip():
                lines.append(f"[User]: {text.strip()}")
            elif kind == "ai" and text.strip():
                lines.append(f"[Analyst]: {text.strip()}")
            elif kind == "tool" and data.get("name") == "AskUserQuestion" and text.strip():
                lines.append(f"[User answers]: {text.strip()}")
            elif kind == "tool" and data.get("name") == "ExitPlanMode" and text.startswith("User has approved"):
                lines.append("[User]: approved the plan.")
                break
    return "\n\n".join(lines) or "(empty)"


def write_session_artifacts(
    session_directory: str, request: str | None, outcome: DevelopmentOutcome | None
) -> str:
    """The initial request, the approved plan and the planning dialogue; returns the artifacts dir."""
    artifacts_dir = os.path.join(session_directory, "metrics_and_logging")
    os.makedirs(artifacts_dir, exist_ok=True)
    if request:
        wrapped = "\n".join(textwrap.fill(line, width=80) if line.strip() else "" for line in request.splitlines())
        _write_text(os.path.join(artifacts_dir, "initial_user_prompt.txt"), "INITIAL PROMPT SENT BY THE USER",
                    wrapped or "(empty)")
    if outcome is not None:
        if outcome.planned and outcome.plan_file and os.path.exists(outcome.plan_file):
            with open(outcome.plan_file, encoding="utf-8") as plan_file:
                plan_text = plan_file.read()
            with open(os.path.join(artifacts_dir, "approved_plan.md"), "w", encoding="utf-8") as approved:
                approved.write(plan_text)
        _write_text(os.path.join(artifacts_dir, "planning_dialogue.txt"), "PLANNING DIALOGUE (USER AND ANALYST)",
                    planning_dialogue(outcome.transcript_path))
    return artifacts_dir


def write_subreq_results(artifacts_dir: str, plan: TddPlan | None, terminal: Terminal | None) -> None:
    """
    Each sub-requirement's outcome (Phase 11): done (its Red -> Green -> Refactor observed), the one the
    session stopped on with the terminal reason, or not run.
    """
    if plan is None or not plan.items:
        _write_text(os.path.join(artifacts_dir, "subreq_results.txt"), "SUB-REQUIREMENT RESULTS", "(no results)")
        return
    stopped_on = plan.current if terminal is not None and terminal != Terminal.COMPLETED else None
    lines = [
        f"Total sub-requirements: {len(plan.items)}",
        f"Done: {plan.done_count}",
        f"Not done: {len(plan.items) - plan.done_count}",
    ]
    if terminal is not None:
        lines.append(f"Session ended: {terminal}")
    lines.append("-" * 80)
    for item in plan.items:
        if item.status == ItemStatus.DONE:
            lines.append(f"{item.id}. [DONE   ] {item.text}")
        elif item is stopped_on:
            lines.append(f"{item.id}. [FAILED ] {item.text}")
            lines.append(f"   Terminal: {terminal}")
        else:
            lines.append(f"{item.id}. [NOT_RUN] {item.text}")
    _write_text(os.path.join(artifacts_dir, "subreq_results.txt"), "SUB-REQUIREMENT RESULTS", "\n".join(lines))


def session_dir(workspace: Any, thread_id: str) -> str:
    """
    The session's directory, beside the project (Phase 13): `<workspaces root>/<thread>/`. The
    reports live there, outside the project's git tree and never in TDDAgents' own folder.
    """
    from app.workspace.local import workspaces_root

    run_dir = getattr(workspace, "run_dir", None)
    return str(run_dir) if run_dir is not None else str(workspaces_root() / thread_id)


def write_final_test_run(artifacts_dir: str, workspace: Any) -> str:
    """
    Run the project's declared setup and test commands once more, in a clean copy of the project built
    from its manifests alone (`app/tdd/final_run.py`, 2026-11-03), and record it in `final_test_run.txt`.
    This is the result anyone checking the project out gets. Returns the one-line summary printed in
    the report.
    """
    from app.tdd.final_run import run_final

    run_dir = getattr(workspace, "run_dir", None) or os.path.dirname(os.path.abspath(artifacts_dir))
    final = run_final(workspace, run_dir)
    lines = [f"Project: {getattr(workspace, 'root', '?')}", f"Result: {final.summary}"]
    if final.lines:
        lines += ["-" * 80, *final.lines]
    _write_text(os.path.join(artifacts_dir, "final_test_run.txt"), "FINAL TEST RUN", "\n".join(lines))
    return final.summary


def waiting_text(thread_id: str, question: Sequence[dict[str, Any]], headless: bool) -> str:
    """What the user is told when the agents stopped on a question nobody could answer."""
    flag = "-p " if headless else ""
    return "\n".join([
        "\n❓ The agents are waiting for your answer:", render_questions(question),
        f"\n   Answer with: python -m app.main {flag}--thread-id {thread_id} \"<your answer>\"",
    ])


def report(thread_id: str, workspace: Any, request: str | None, outcome: DevelopmentOutcome) -> None:
    """Write the reports beside the project, run its suite once more, then print the outcome."""
    target = session_dir(workspace, thread_id)
    os.makedirs(target, exist_ok=True)
    artifacts_dir = write_session_artifacts(target, request, outcome)
    write_subreq_results(artifacts_dir, outcome.plan, outcome.terminal)
    _write_token_report(artifacts_dir)
    print(f"\n📁 Project: {getattr(workspace, 'root', '?')}")
    print(f"📝 Reports: {artifacts_dir}/")
    if outcome.planned:
        print(f"🧪 Final test run: {write_final_test_run(artifacts_dir, workspace)}")

    plan = outcome.plan
    if not outcome.planned:
        print("\n📋  NO APPROVED PLAN: the session ended in plan mode.")
        if outcome.plan_file and os.path.exists(outcome.plan_file):
            print(f"   The plan so far: {outcome.plan_file}")
    elif outcome.completed:
        print("\n✅  PIPELINE COMPLETED SUCCESSFULLY!")
    else:
        print(f"\n⚠️  DEVELOPMENT STOPPED: {outcome.terminal}")
        if plan.current is not None:
            print(f"   • {plan.current.id}. {plan.current.text}: not done")
    print(f"\n🔑 Thread ID for this run: {thread_id}")
    print("   (To inspect the state or resume later, use --thread-id)\n")


def rewind_files(args: argparse.Namespace, initial_input: str | None, write_stdout: Any = None) -> int:
    """
    `print.ts`'s `--rewind-files` (print mode only, as upstream): restore the session's files to
    their state at a user message, then exit. It needs the session (`--thread-id`, upstream's
    `--resume`) and is a standalone operation, so a prompt is refused.
    """
    if not args.thread_id:
        sys.stderr.write("Error: --rewind-files requires --thread-id\n")
        return 1
    if initial_input:
        sys.stderr.write("Error: --rewind-files is a standalone operation and cannot be used with a prompt\n")
        return 1
    from app.cli.print import session_workspace

    thread_id = str(args.thread_id)
    return rewind_files_headless(thread_id, str(args.rewind_files), session_workspace(thread_id),
                                 session_options(args), write_out=write_stdout)


#: `main.tsx`'s `plugin` command and its alias.
PLUGIN_COMMANDS = ("plugin", "plugins")


def build_plugin_parser() -> argparse.ArgumentParser:
    """`main.tsx`'s `pluginCmd` and `marketplaceCmd` (commander), as argparse subcommands."""
    from app.services.plugins.plugin_operations import (
        VALID_INSTALLABLE_SCOPES,
        VALID_UPDATE_SCOPES,
    )
    from app.utils.plugins.plugin_directories import PLUGIN_PACKAGE_DIR

    parser = argparse.ArgumentParser(prog="python -m app.main plugin", description="Manage TDDAgents plugins")
    parser.add_argument("--plugin-dir", dest="plugin_dir", action="append", default=[], metavar="<path>",
                        help="Load plugins from a directory for this session only")
    parser.add_argument("--thread-id", dest="thread_id", default=None, metavar="<id>",
                        help="Use that session's project folder for the project and local scopes "
                             "(default: the current directory)")
    commands = parser.add_subparsers(dest="command", required=True)

    def command(name: str, help_text: str, aliases: Sequence[str] = ()) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help_text, aliases=list(aliases), description=help_text)
        sub.add_argument("--cowork", action="store_true", help=argparse.SUPPRESS)
        return sub

    validate = command("validate", "Validate a plugin or marketplace manifest")
    validate.add_argument("path")
    listing = command("list", "List installed plugins")
    listing.add_argument("--json", action="store_true", help="Output as JSON")
    listing.add_argument("--available", action="store_true",
                         help="Include available plugins from marketplaces (requires --json)")
    marketplace = commands.add_parser("marketplace", help="Manage TDDAgents marketplaces")
    marketplace_commands = marketplace.add_subparsers(dest="marketplace_command", required=True)

    def marketplace_command(name: str, help_text: str, aliases: Sequence[str] = ()) -> argparse.ArgumentParser:
        sub = marketplace_commands.add_parser(name, help=help_text, aliases=list(aliases), description=help_text)
        sub.add_argument("--cowork", action="store_true", help=argparse.SUPPRESS)
        return sub

    add = marketplace_command("add", "Add a marketplace from a URL, path, or GitHub repo")
    add.add_argument("source")
    add.add_argument("--sparse", nargs="+", metavar="<paths...>",
                     help="Limit checkout to specific directories via git sparse-checkout (for monorepos). "
                          f"Example: --sparse {PLUGIN_PACKAGE_DIR} plugins")
    add.add_argument("--scope", help="Where to declare the marketplace: user (default), project, or local")
    marketplace_list = marketplace_command("list", "List all configured marketplaces")
    marketplace_list.add_argument("--json", action="store_true", help="Output as JSON")
    marketplace_command("remove", "Remove a configured marketplace", aliases=("rm",)).add_argument("name")
    marketplace_command("update", "Update marketplace(s) from their source - updates all if no name specified"
                        ).add_argument("name", nargs="?")
    install = command("install", "Install a plugin from available marketplaces (use plugin@marketplace for specific "
                      "marketplace)", aliases=("i",))
    install.add_argument("plugin")
    install.add_argument("-s", "--scope", default="user", help="Installation scope: user, project, or local")
    uninstall = command("uninstall", "Uninstall an installed plugin", aliases=("remove", "rm"))
    uninstall.add_argument("plugin")
    uninstall.add_argument("-s", "--scope", default="user", help="Uninstall from scope: user, project, or local")
    uninstall.add_argument("--keep-data", action="store_true",
                           help="Preserve the plugin's persistent data directory (~/.tddagents/plugins/data/{id}/)")
    scopes = ", ".join(VALID_INSTALLABLE_SCOPES)
    enable = command("enable", "Enable a disabled plugin")
    enable.add_argument("plugin")
    enable.add_argument("-s", "--scope", help=f"Installation scope: {scopes} (default: auto-detect)")
    disable = command("disable", "Disable an enabled plugin")
    disable.add_argument("plugin", nargs="?")
    disable.add_argument("-a", "--all", dest="all_plugins", action="store_true", help="Disable all enabled plugins")
    disable.add_argument("-s", "--scope", help=f"Installation scope: {scopes} (default: auto-detect)")
    update = command("update", "Update a plugin to the latest version (restart required to apply)")
    update.add_argument("plugin")
    update.add_argument("-s", "--scope", help=f"Installation scope: {', '.join(VALID_UPDATE_SCOPES)} (default: user)")
    return parser


def plugin_cli_locations(thread_id: str | None = None) -> Any:
    """
    The settings the `plugin` subcommand reads and writes: upstream's process settings, whose
    project is the directory the command runs in (`getOriginalCwd()`), or, with `--thread-id`,
    that session's project folder (`<workspaces root>/<thread id>/project`, a fork's link resolved).
    An unknown thread raises `LookupError`.
    """
    from app.utils.settings.settings import MANAGED_DIR, SettingsLocations
    from app.workspace.local import PROJECT_DIRNAME, workspaces_root

    project = Path.cwd()
    if thread_id is not None:
        project = (workspaces_root() / thread_id / PROJECT_DIRNAME).resolve()
        if not project.is_dir():
            raise LookupError(f"No session found with thread id: {thread_id}")
    return SettingsLocations(project_root=project, managed_dir=Path(MANAGED_DIR))


def run_plugin_command(argv: Sequence[str]) -> int:
    """`claude plugin …`: parse, then run the handler (each returns the exit code)."""
    import asyncio

    from app.cli.handlers import plugins as handlers

    args = build_plugin_parser().parse_args(list(argv))
    apply_plugin_dirs(args.plugin_dir)
    try:
        locations = plugin_cli_locations(args.thread_id)
    except LookupError as error:
        print(error, file=sys.stderr)
        return 1
    command = args.command
    if command == "validate":
        work: Any = handlers.plugin_validate_handler(args.path, cowork=args.cowork)
    elif command == "list":
        work = handlers.plugin_list_handler(locations=locations, as_json=args.json, available=args.available,
                                            cowork=args.cowork)
    elif command == "marketplace":
        sub = args.marketplace_command
        if sub == "add":
            work = handlers.marketplace_add_handler(args.source, locations=locations, sparse=args.sparse,
                                                    scope=args.scope, cowork=args.cowork)
        elif sub == "list":
            work = handlers.marketplace_list_handler(as_json=args.json, cowork=args.cowork)
        elif sub in ("remove", "rm"):
            work = handlers.marketplace_remove_handler(args.name, locations=locations, cowork=args.cowork)
        else:
            work = handlers.marketplace_update_handler(args.name, cowork=args.cowork)
    elif command in ("install", "i"):
        work = handlers.plugin_install_handler(args.plugin, locations=locations, scope=args.scope, cowork=args.cowork)
    elif command in ("uninstall", "remove", "rm"):
        work = handlers.plugin_uninstall_handler(args.plugin, locations=locations, scope=args.scope,
                                                 keep_data=args.keep_data, cowork=args.cowork)
    elif command == "enable":
        work = handlers.plugin_enable_handler(args.plugin, locations=locations, scope=args.scope, cowork=args.cowork)
    elif command == "disable":
        work = handlers.plugin_disable_handler(args.plugin, locations=locations, scope=args.scope,
                                               all_plugins=args.all_plugins, cowork=args.cowork)
    else:
        work = handlers.plugin_update_handler(args.plugin, locations=locations, scope=args.scope, cowork=args.cowork)
    return int(asyncio.run(work))


#: `main.tsx`'s `doctor` command.
DOCTOR_COMMAND = "doctor"


def run_doctor_command(argv: Sequence[str]) -> int:
    """
    `claude doctor` (`doctorHandler`), for the directory it runs in, or, with `--thread-id`, that
    session's project folder (user decision 2026-11-04: TDDAgents runs from its own repository, so the
    current directory is never a session's project; the `plugin` subcommand has the same option).
    """
    import asyncio

    from app.cli.handlers.util import DOCTOR_DESCRIPTION, doctor_handler

    parser = argparse.ArgumentParser(prog=f"{CLI_COMMAND} {DOCTOR_COMMAND}", description=DOCTOR_DESCRIPTION)
    parser.add_argument("--thread-id", dest="thread_id", default=None, metavar="<id>",
                        help="Diagnose that session's project folder (default: the current directory)")
    args = parser.parse_args(list(argv))
    try:
        locations = plugin_cli_locations(args.thread_id)
    except LookupError as error:
        print(error, file=sys.stderr)
        return 1
    return asyncio.run(doctor_handler(locations, Path(locations.project_root)))


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in PLUGIN_COMMANDS:
        return run_plugin_command(arguments[1:])
    if arguments and arguments[0] == DOCTOR_COMMAND:
        return run_doctor_command(arguments[1:])
    args = parse_args(argv)
    error = validate_sdk_flags(args)
    if error is not None:
        print(error, file=sys.stderr)
        return 1
    debug_handler = apply_debug_flags(args)
    try:
        return run_entry(args)
    finally:
        if debug_handler is not None:
            logging.getLogger().removeHandler(debug_handler)


def apply_debug_flags(args: argparse.Namespace) -> Any:
    """
    `--debug [filter]`, `--debug-file` and `--debug-to-stderr` (`app/utils/debug.py`); in debug mode
    TDDAgents' own log records go to the debug log too. Returns the handler to remove afterwards.
    """
    from app.utils.debug import configure_debug, install_debug_log_handler, set_has_formatted_output

    configure_debug(args.debug, debug_file=args.debug_file, to_stderr=args.debug_to_stderr)
    # `main.tsx`: in --print mode, JSON on stdout makes a multiline debug message JSON too.
    set_has_formatted_output(bool(args.print and args.output_format in ("json", "stream-json")))
    return install_debug_log_handler()


def run_entry(args: argparse.Namespace) -> int:
    """`main` after the flags are applied: the REPL, or `-p` with stdout kept for the result."""
    if args.coordinator:
        os.environ["TDDAGENTS_COORDINATOR_MODE"] = "1"
    apply_plugin_dirs(args.plugin_dir)
    if not args.print:
        return run_main(args)
    # `-p`: stdout carries only what `runHeadless` writes; everything else goes to stderr.
    from contextlib import redirect_stdout

    from app.utils.stream_json_stdout_guard import (
        install_stream_json_stdout_guard,
        uninstall_stream_json_stdout_guard,
    )

    stdout: Any = install_stream_json_stdout_guard() if args.output_format == "stream-json" else sys.stdout

    def write_stdout(text: str) -> None:
        stdout.write(text)
        stdout.flush()

    try:
        with redirect_stdout(sys.stderr):
            return run_main(args, write_stdout=write_stdout)
    finally:
        if args.output_format == "stream-json":
            uninstall_stream_json_stdout_guard()


def run_main(args: argparse.Namespace, write_stdout: Any = None) -> int:
    """The session `main` opens, its reports, and the exit code."""
    stream_input = args.print and args.input_format == "stream-json"
    try:
        session_thread = resolve_session_flags(args)
    except SessionFlagError as error:
        print(str(error), file=sys.stderr)
        return 1
    if (args.continue_session or args.resume) and session_thread is None:
        return 0
    if session_thread is not None:
        args.thread_id = session_thread
    initial_input = read_initial_request(args)
    if args.rewind_files and args.print:
        return rewind_files(args, initial_input, write_stdout)
    if initial_input is None and not args.thread_id and not args.print:
        try:
            initial_input = get_initial_user_prompt()
        except (KeyboardInterrupt, EOFError):
            print("\n👋 Operation cancelled by the user.")
            return 0
    if not args.thread_id and not initial_input and not stream_input:
        if args.print:
            print("Error: Input must be provided either through stdin or as a prompt argument when using --print",
                  file=sys.stderr)
            return 1
        print("❌ The initial request cannot be empty.", file=sys.stderr)
        return 1

    thread_id = resolve_thread_id(args, initial_input)
    logger.info("🔑 thread_id: %s", thread_id)
    from app.utils.debug import set_debug_session_id

    # The debug log is named after the session (`getDebugLogPath`'s `getSessionId()`).
    set_debug_session_id(thread_id)
    from app.cli.print import session_workspace

    workspace = session_workspace(thread_id)
    resuming = session_exists(thread_id, workspace)
    if not resuming and not initial_input and not stream_input:
        print(f"❌ No session '{thread_id}' to continue, and no initial request to start one.", file=sys.stderr)
        return 1

    log_dir = os.path.join(session_dir(workspace, thread_id), "metrics_and_logging")
    os.makedirs(log_dir, exist_ok=True)
    file_handler = logging.FileHandler(os.path.join(log_dir, "execution_logs.txt"), mode="a", encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logging.getLogger().addHandler(file_handler)

    try:
        print("\n" + "=" * 80)
        print("🚀  TDDAGENTS SESSION " + ("(HEADLESS)" if args.print else "(INTERACTIVE)"))
        print("=" * 80)
        # On a session that already exists, the prompt is the user's next message (upstream's
        # `--resume <id> "<prompt>"`): the answer to the question the agents stopped on.
        request = None if resuming else initial_input
        resumed_prompt = initial_input if resuming else None
        try:
            if args.print:
                outcome = develop_headless(thread_id, request, workspace, session_options(args),
                                           prompt=resumed_prompt, write_stdout=write_stdout)
            else:
                outcome = develop_interactive(thread_id, request, workspace, session_options(args),
                                              prompt=resumed_prompt)
        except KeyboardInterrupt:
            print(f"\n🛑 Interrupted. Continue later with --thread-id {thread_id}.")
            return 130
        except TrustDialogExit as refused:
            # The trust dialog's "No, exit" (1) or Esc (0): `gracefulShutdownSync(code)`, no session.
            return refused.exit_code
        except SandboxUnavailableError as unavailable:
            # `print.ts` / `REPL.tsx`: the sandbox is required and cannot run, or failed to start (exit 1).
            sys.stderr.write(str(unavailable))
            return 1
        report(thread_id, workspace, request, outcome)
        if outcome.question:
            print(waiting_text(thread_id, outcome.question, args.print))
        if args.print:
            # `gracefulShutdownSync(lastMessage?.type === 'result' && lastMessage?.is_error ? 1 : 0)`; no
            # result at all is upstream's thrown 'No messages returned'.
            return 1 if outcome.result is None or outcome.result.get("is_error") else 0
        return 0 if outcome.completed else 1
    finally:
        logging.getLogger().removeHandler(file_handler)
        file_handler.close()


if __name__ == "__main__":
    sys.exit(main())
