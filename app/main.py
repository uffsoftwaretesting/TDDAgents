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

`--thread-id` continues a session from its transcript, TDD plan and plan file. It runs in the
thread's own project; `--permission-mode`, `--settings` and `--dangerously-skip-permissions` are
upstream's startup flags for it, and `--coordinator` turns on coordinator mode
(`TDDAGENTS_COORDINATOR_MODE`).
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

from app.cli.print import DevelopmentOutcome
from app.query.transitions import Terminal
from app.session.driver import (
    SessionOptions,
    derive_thread_id,
    develop_headless,
    develop_interactive,
    fresh_thread_id,
    session_exists,
)
from app.tdd.plan import ItemStatus, TddPlan
from app.tools.ask_user_question_tool.ask_user_question_tool import render_questions
from app.types.permissions import PERMISSION_MODES

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
    if args.print and not sys.stdin.isatty():
        return sys.stdin.read().strip() or None
    return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Runs the TDDAgents pipeline: requirements, plan, then test-driven development of the plan.",
    )
    parser.add_argument("prompt", nargs="?", default=None, help="The initial request (instead of the menu).")
    parser.add_argument("-p", "--print", action="store_true", help="Headless: ask nothing, exit non-zero on failure.")
    parser.add_argument("--spec", default=None, help="Read the initial request from this file.")
    parser.add_argument("--fresh", action="store_true", help="Forces a new run by generating a random thread_id.")
    parser.add_argument("--thread-id", default=None, help="Explicitly sets the thread_id (continues that session).")
    parser.add_argument("--permission-mode", choices=[str(m) for m in PERMISSION_MODES], default=None)
    parser.add_argument("--settings", default=None, help="A settings file (upstream's flag settings).")
    parser.add_argument("--dangerously-skip-permissions", action="store_true")
    parser.add_argument("--coordinator", action="store_true", help="Run development in coordinator mode.")
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
        permission_mode=args.permission_mode,
        settings_path=args.settings,
        dangerously_skip_permissions=bool(args.dangerously_skip_permissions),
    )


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
    Run the project's whole suite once more, exactly as RunTests does, in the project's own venv
    (Phase 13), and record it in `final_test_run.txt`. This is the result anyone re-running the
    project gets. Returns the one-line summary printed in the report.
    """
    from app.tdd.collection_gate import uncollected_test_files
    from app.tdd.run_tests_tool.run_tests_tool import (
        RunTestsUnavailable,
        exit_semantic,
        files_that_ran,
        run_suite,
        skipped_count,
    )

    try:
        result = run_suite(workspace, ".")
    except RunTestsUnavailable as exc:
        summary = f"could not run the tests: {exc}"
        _write_text(os.path.join(artifacts_dir, "final_test_run.txt"), "FINAL TEST RUN", summary)
        return summary
    output = f"{result.stdout}\n{result.stderr}".strip()
    skipped = result.skipped if result.skipped is not None else skipped_count(output)
    uncollected = uncollected_test_files(workspace, ".", files_that_ran(result))
    verdict = "passed" if result.exit_code == 0 and skipped == 0 and not uncollected else "NOT passing"
    meaning = exit_semantic(result.exit_code).meaning
    summary = f"{verdict} (exit {result.exit_code}: {meaning}, {skipped} skipped, {result.coverage.describe()})"
    if uncollected:
        summary += f"; not collected: {', '.join(uncollected)}"
    lines = [f"Project: {getattr(workspace, 'root', '?')}", f"Result: {summary}", "-" * 80, output]
    _write_text(os.path.join(artifacts_dir, "final_test_run.txt"), "FINAL TEST RUN", "\n".join(lines))
    return summary


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
    from app.cli.print import session_workspace

    workspace = session_workspace(thread_id)
    resuming = session_exists(thread_id, workspace)
    if not resuming and not initial_input:
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
            develop = develop_headless if args.print else develop_interactive
            outcome = develop(thread_id, request, workspace, session_options(args), prompt=resumed_prompt)
        except KeyboardInterrupt:
            print(f"\n🛑 Interrupted. Continue later with --thread-id {thread_id}.")
            return 130
        report(thread_id, workspace, request, outcome)
        if outcome.question:
            print(waiting_text(thread_id, outcome.question, args.print))
        return 0 if outcome.completed else 1
    finally:
        logging.getLogger().removeHandler(file_handler)
        file_handler.close()


if __name__ == "__main__":
    sys.exit(main())
