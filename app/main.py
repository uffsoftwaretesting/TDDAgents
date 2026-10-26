"""
The TDDAgents entry point (Phase 9).

Two modes, as claude-code has them:

* **Interactive** (default): the use-case menu picks the initial request, and the analyst's
  questions and checklist are answered on the terminal (`/yes` approves a checklist;
  `/exit` or `/quit` leaves, with the session paused at its checkpoint).
* **Headless** (`-p/--print`, upstream `claude -p` -> `runHeadless`): the request comes from
  `--spec FILE`, the prompt argument or stdin, and nobody is asked anything. The analyst's
  checklist is approved; a clarifying question fails the session. The exit code is 0 only
  when every plan item completed a Red-then-Green cycle.

Either way the session is one LangGraph thread (`app/session/`): it checkpoints to
`POSTGRES_URL` by default (`--checkpoint memory` opts out), and `--thread-id` continues a
thread from its last checkpoint. Each plan item is one loop run in the thread's shared local
workspace; `--permission-mode`, `--settings` and `--dangerously-skip-permissions` are
upstream's startup flags for those runs, and `--coordinator` turns on coordinator mode
(`TDDAGENTS_COORDINATOR_MODE`).
"""

import argparse
import logging
import os
import sys
import textwrap
from pathlib import Path
from typing import Any

# Allows running this file directly without losing absolute `app.*` imports.
if __package__ in (None, ""):
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

from dotenv import load_dotenv

from app.loop.permissions.types import PERMISSION_MODES
from app.session.driver import (
    SessionOptions,
    SessionRun,
    build_session_shell,
    derive_thread_id,
    drive_session,
    failed_requirements,
    fresh_thread_id,
)
from app.session.export import export_run_workspace
from app.utils.pass_rate import write_pass_rate_report
from app.utils.resilience_metrics import write_resilience_metrics

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
# Silences verbose logs from base libraries
for noisy_logger in ("httpx", "httpcore", "anthropic", "e2b"):
    logging.getLogger(noisy_logger).setLevel(logging.ERROR)

logger = logging.getLogger("TDDMain")
logger.setLevel(logging.INFO)

#: The answers that leave the interactive session (paused at its checkpoint).
EXIT_COMMANDS = ("/exit", "/quit")


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
    if args.print and not sys.stdin.isatty():
        return sys.stdin.read().strip() or None
    return None


def interactive_answer(payload: dict[str, Any]) -> str | None:
    """Show the analyst's answer and read the user's; `None` when the user leaves."""
    print(f"\n🤖 [Requirements Analyst]:\n{payload.get('prompt', '')}\n")
    if payload.get("has_checklist"):
        print("💡 TIP: The Analyst has generated the Final Checklist.")
        print("   - Type '/yes' to proceed to Engineering.")
        print("   - Or type the changes you'd like to make (e.g. 'Missing validation X').")
    else:
        print("💡 TIP: The Analyst needs more details.")
        print("   - Answer the question to help define the scope.")
    try:
        reply = input("\n👤 [Your Response] (or '/exit'): ").strip()
    except (KeyboardInterrupt, EOFError):
        return None
    if reply.lower() in EXIT_COMMANDS:
        return None
    return reply


def format_dialogue_text(dialogue: str, width: int = 88) -> str:
    """Formats the dialogue for readability in a text file, with line wrapping."""
    if not dialogue or not dialogue.strip():
        return "(empty)"

    formatted_lines: list[str] = []
    speaker_prefixes = ("[User]:", "[Analyst]:")

    for raw_line in dialogue.splitlines():
        line = raw_line.strip()
        if not line:
            formatted_lines.append("")
            continue

        speaker = next((prefix for prefix in speaker_prefixes if line.startswith(prefix)), None)
        if speaker:
            content = line[len(speaker):].strip()
            if not content:
                formatted_lines.append(speaker)
                continue

            indent = " " * (len(speaker) + 1)
            wrapped = textwrap.fill(
                content,
                width=width,
                initial_indent=f"{speaker} ",
                subsequent_indent=indent,
                break_long_words=False,
                break_on_hyphens=False,
            )
            formatted_lines.append(wrapped)
            continue

        wrapped = textwrap.fill(
            line,
            width=width,
            break_long_words=False,
            break_on_hyphens=False,
        )
        formatted_lines.append(wrapped)

    return "\n".join(formatted_lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Runs the TDDAgents pipeline: requirements, plan, one TDD loop per item.",
    )
    parser.add_argument("prompt", nargs="?", default=None, help="The initial request (instead of the menu).")
    parser.add_argument("-p", "--print", action="store_true", help="Headless: ask nothing, exit non-zero on failure.")
    parser.add_argument("--spec", default=None, help="Read the initial request from this file.")
    parser.add_argument("--fresh", action="store_true", help="Forces a new run by generating a random thread_id.")
    parser.add_argument("--thread-id", default=None, help="Explicitly sets the thread_id (continues that session).")
    parser.add_argument(
        "--checkpoint", default=None,
        help="Checkpointer URI, or 'memory'. Defaults to POSTGRES_URL, falling back to memory if it cannot connect.",
    )
    parser.add_argument("--permission-mode", choices=[str(m) for m in PERMISSION_MODES], default=None)
    parser.add_argument("--settings", default=None, help="A settings file (upstream's flag settings).")
    parser.add_argument("--dangerously-skip-permissions", action="store_true")
    parser.add_argument("--coordinator", action="store_true", help="Run each plan item in coordinator mode.")
    return parser


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
        checkpoint=args.checkpoint,
        permission_mode=args.permission_mode,
        settings_path=args.settings,
        dangerously_skip_permissions=bool(args.dangerously_skip_permissions),
    )


def _write_token_report(artifacts_dir: str) -> None:
    """Logs token usage and persists a report to artifacts_dir/token_usage.txt."""
    from app.loop.model import get_global_token_tracker

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


def write_session_artifacts(workspace_dir: str, values: dict[str, Any]) -> str:
    """Write the session's dialogue, specification, plan and reports; return the artifacts dir."""
    artifacts_dir = os.path.join(workspace_dir, "metrics_and_logging")
    os.makedirs(artifacts_dir, exist_ok=True)

    user_prompts = list(values.get("user_prompts") or [])
    formatted_requirements = format_dialogue_text(str(values.get("conversation_history") or ""))
    numbered = "\n".join(
        textwrap.fill(
            prompt, width=88, initial_indent=f"{idx}. ", subsequent_indent=" " * len(f"{idx}. "),
            break_long_words=False, break_on_hyphens=False,
        )
        for idx, prompt in enumerate(user_prompts, start=1)
    )
    _write_text(
        os.path.join(artifacts_dir, "user_analyst_dialogue.txt"),
        "PROMPTS SENT BY THE USER VIA TERMINAL",
        numbered + "\n" + "=" * 80 + "\n\nFULL DIALOGUE BETWEEN USER AND ANALYST\n" + "=" * 80 + "\n"
        + formatted_requirements,
    )
    initial_prompt = user_prompts[0] if user_prompts else ""
    wrapped_initial = "\n".join(
        textwrap.fill(line, width=80) if line.strip() else "" for line in (initial_prompt.splitlines() or [""])
    )
    _write_text(
        os.path.join(artifacts_dir, "initial_user_prompt.txt"), "INITIAL PROMPT SENT BY THE USER",
        wrapped_initial or "(empty)",
    )

    plan = list(values.get("plan") or [])
    if plan:
        _write_text(
            os.path.join(artifacts_dir, "planner.txt"), "TDD SUB-REQUIREMENTS PLAN",
            "\n".join(f"{i + 1}. {item}" for i, item in enumerate(plan)),
        )
    _write_text(
        os.path.join(artifacts_dir, "engineer_specifications.txt"), "ENGINEER'S TECHNICAL SPECIFICATIONS",
        str(values.get("specification") or ""),
    )
    _write_text(
        os.path.join(artifacts_dir, "confirmed_user_requirements.txt"), "REQUIREMENTS SUBMITTED BY THE USER",
        formatted_requirements,
    )

    success = int(values.get("success_count") or 0)
    failure = int(values.get("failure_count") or 0)
    failed = failed_requirements(values)
    write_resilience_metrics(artifacts_dir, failure, 0, 0, 0)
    write_pass_rate_report(artifacts_dir, plan, list(values.get("subreq_results") or []), success, failure, failed)
    return artifacts_dir


def report(run: SessionRun, thread_id: str, workspace: Any) -> None:
    """Export the workspace and the artifacts, then print the outcome."""
    workspace_dir = os.path.abspath(f"workspace_output_{thread_id}")
    os.makedirs(workspace_dir, exist_ok=True)
    artifacts_dir = write_session_artifacts(workspace_dir, run.values)
    _write_token_report(artifacts_dir)
    exported = export_run_workspace(workspace.root, workspace_dir)
    print(f"\n💾 {len(exported)} file(s) exported from {workspace.root} to {workspace_dir}/")

    if run.paused_at is not None:
        print("\n⏸️  Session paused at the analyst's question.")
    elif run.completed:
        print("\n✅  PIPELINE COMPLETED SUCCESSFULLY!")
    else:
        print(f"\n⚠️  PIPELINE FINISHED WITH STATUS: '{run.values.get('status', 'unknown')}'")
    for item in failed_requirements(run.values):
        print(f"   • {item['requirement']}: {item['reason']}")
    print(f"\n🔑 Thread ID for this run: {thread_id}")
    print("   (To inspect the state or resume later, use --thread-id)\n")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)
    if args.coordinator:
        os.environ["TDDAGENTS_COORDINATOR_MODE"] = "1"

    initial_input = read_initial_request(args)
    if initial_input is None and not args.thread_id and not args.print:
        try:
            initial_input = get_initial_user_prompt()
        except (KeyboardInterrupt, EOFError):
            print("\n👋 Operation cancelled by the user.")
            return 0
    if not args.thread_id and not initial_input:
        print("❌ The initial request cannot be empty.", file=sys.stderr)
        return 1

    thread_id = resolve_thread_id(args, initial_input)
    logger.info("🔑 thread_id: %s", thread_id)
    shell, workspace = build_session_shell(thread_id, session_options(args))
    if not shell.has_session(thread_id) and not initial_input:
        print(f"❌ No session '{thread_id}' to continue, and no initial request to start one.", file=sys.stderr)
        return 1

    log_dir = os.path.join(os.path.abspath(f"workspace_output_{thread_id}"), "metrics_and_logging")
    os.makedirs(log_dir, exist_ok=True)
    file_handler = logging.FileHandler(os.path.join(log_dir, "execution_logs.txt"), mode="a", encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logging.getLogger().addHandler(file_handler)

    try:
        print("\n" + "=" * 80)
        print("🚀  TDDAGENTS SESSION " + ("(HEADLESS)" if args.print else "(INTERACTIVE)"))
        print("=" * 80)
        try:
            run = drive_session(shell, thread_id, initial_input, answer=None if args.print else interactive_answer)
        except KeyboardInterrupt:
            print(f"\n🛑 Interrupted. Continue later with --thread-id {thread_id}.")
            return 130
        report(run, thread_id, workspace)
        return 0 if run.completed else 1
    finally:
        logging.getLogger().removeHandler(file_handler)
        file_handler.close()


if __name__ == "__main__":
    sys.exit(main())
