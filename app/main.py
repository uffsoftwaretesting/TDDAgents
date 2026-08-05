import hashlib
import logging
import uuid
import argparse
import os
import sys
import textwrap
from pathlib import Path

# Allows running this file directly without losing absolute `app.*` imports.
if __package__ in (None, ""):
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

from app.utils.resilience_metrics import write_resilience_metrics
from app.utils.pass_rate import write_pass_rate_report

from dotenv import load_dotenv

from app.graph.subgraphs.requirements_orchestrator_subgraph import RequirementsOrchestrator
from app.graph.orchestrator import TDDOrchestrator

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
# Silences verbose logs from base libraries
for noisy_logger in ("httpx", "httpcore", "anthropic", "e2b"):
    logging.getLogger(noisy_logger).setLevel(logging.ERROR)

logger = logging.getLogger("TDDMain")
logger.setLevel(logging.INFO)


def make_thread_id(specification: str) -> str:
    """Derives a stable thread_id purely from the specification text."""
    payload = f"sandbox::{specification}"
    return "tdd-" + hashlib.sha1(payload.encode()).hexdigest()[:16]


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


def run_requirements_gathering() -> tuple[str, str, list[str]]:
    """Interactive requirements-gathering phase."""
    print("\n" + "=" * 80)
    print("🤖  PHASE 1: REQUIREMENTS GATHERING (PRODUCT MANAGER)")
    print("=" * 80)
    print("Describe the application or feature you want to build.")
    print("The Analyst will ask questions if something is unclear.\n")

    try:
        # Replaced the manual input with the function backed by the dynamic menu
        initial_input = get_initial_user_prompt()
    except (KeyboardInterrupt, EOFError):
        print("\n👋 Operation cancelled by the user.")
        sys.exit(0)

    if not initial_input:
        print("❌ The initial request cannot be empty.")
        sys.exit(1)

    orchestrator = RequirementsOrchestrator()
    final_state = orchestrator.run(initial_input)

    spec = final_state.get("final_specification", "")
    reqs = final_state.get("conversation_history", "")
    user_prompts = final_state.get("user_prompts", [initial_input])

    if not spec:
        print("\n❌ Critical failure: The Engineer failed to generate the specification.")
        sys.exit(1)

    return spec, reqs, user_prompts


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Runs the Enterprise TDD agent pipeline (E2B Sandbox Edition).",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Forces a new run by generating a random thread_id.",
    )
    parser.add_argument(
        "--thread-id",
        default=None,
        help="Explicitly sets the thread_id.",
    )
    return parser.parse_args()


def _write_token_report(artifacts_dir: str, orchestrator: TDDOrchestrator) -> None:
    """Logs token usage and persists a report to artifacts_dir/token_usage.txt."""
    summary = orchestrator.token_tracker.summary()
    totals = summary["totals"]

    os.makedirs(artifacts_dir, exist_ok=True)

    logger.info(
        "📊 Token Usage — total: %d | prompt: %d | completion: %d | cached: %d",
        totals["total_tokens"],
        totals["prompt_tokens"],
        totals["completion_tokens"],
        totals["cached_tokens"],
    )

    token_report_path = os.path.join(artifacts_dir, "token_usage.txt")
    with open(token_report_path, "w", encoding="utf-8") as f:
        f.write("TOKEN USAGE REPORT\n")
        f.write("=" * 80 + "\n\n")
        f.write("BY MODEL:\n")
        for model, stats in summary["by_model"].items():
            f.write(f"  {model}:\n")
            for k, v in stats.items():
                f.write(f"    {k}: {v}\n")
        f.write("\nTOTALS:\n")
        for k, v in totals.items():
            f.write(f"  {k}: {v}\n")


def main() -> None:
    load_dotenv()
    args = parse_args()

    # ── Phase 1: Requirements Gathering ────────────────────────────────────
    specification, requirements, user_prompts = run_requirements_gathering()

    # ── thread_id resolution ────────────────────────────────────────────────
    if args.thread_id:
        thread_id = args.thread_id
        logger.info(f"🔑 Using explicit thread_id: {thread_id}")
    elif args.fresh:
        thread_id = "tdd-" + uuid.uuid4().hex[:16]
        logger.info(f"🆕 New run — generated thread_id: {thread_id}")
    else:
        thread_id = make_thread_id(specification)
        logger.info(f"🔑 thread_id derived from the specification: {thread_id}")

    # ── Workspace and Logs Setup ──────────────────────────────────────
    workspace_dir_name = f"workspace_output_{thread_id}"
    workspace_dir = os.path.abspath(workspace_dir_name)
    os.makedirs(workspace_dir, exist_ok=True)

    artifacts_dir = os.path.join(workspace_dir, "metrics_and_logging")
    os.makedirs(artifacts_dir, exist_ok=True)

    log_file_path = os.path.join(artifacts_dir, "execution_logs.txt")
    file_handler = logging.FileHandler(log_file_path, mode="w", encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logging.getLogger().addHandler(file_handler)

    formatted_requirements = format_dialogue_text(requirements)

    prompts_path = os.path.join(artifacts_dir, "user_analyst_dialogue.txt")
    with open(prompts_path, "w", encoding="utf-8") as f:
        f.write("PROMPTS SENT BY THE USER VIA TERMINAL\n")
        f.write("=" * 80 + "\n")
        for idx, prompt in enumerate(user_prompts, start=1):
            wrapped_prompt = textwrap.fill(
                prompt,
                width=88,
                initial_indent=f"{idx}. ",
                subsequent_indent=" " * (len(f"{idx}. ")),
                break_long_words=False,
                break_on_hyphens=False,
            )
            f.write(wrapped_prompt + "\n")
        f.write("=" * 80 + "\n\n")
        f.write("FULL DIALOGUE BETWEEN USER AND ANALYST\n")
        f.write("=" * 80 + "\n")
        f.write(formatted_requirements + "\n")
        f.write("=" * 80 + "\n")

    initial_prompt_path = os.path.join(artifacts_dir, "initial_user_prompt.txt")
    initial_prompt = user_prompts[0] if user_prompts else ""
    wrapped_initial_prompt = "\n".join(
        textwrap.fill(line, width=80) if line.strip() else ""
        for line in (initial_prompt.splitlines() or [initial_prompt])
    )
    with open(initial_prompt_path, "w", encoding="utf-8") as f:
        f.write("INITIAL PROMPT SENT BY THE USER\n")
        f.write("=" * 80 + "\n")
        f.write((wrapped_initial_prompt or "(empty)") + "\n")
        f.write("=" * 80 + "\n")

    # ── Phase 2: TDD Execution ──────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("🚀  PHASE 2: MULTI-AGENT TDD ORCHESTRATION (E2B SANDBOX)")
    print("=" * 80)
    print(f"📂 Output Directory: ./{workspace_dir_name}/")
    print("📜 Preview of the Technical Specification that will guide the agents:")
    print("-" * 80)
    print(f"{specification[:500]}...\n\n[SEE THE FILE engineer_specifications.txt...]\n")
    print("-" * 80 + "\n")

    orchestrator = TDDOrchestrator(task_key=thread_id)

    try:
        final_state = orchestrator.run(specification=specification, requirements=requirements)
    except KeyboardInterrupt:
        print("\n🛑 TDD execution safely interrupted by the user.")
        sys.exit(0)

    # ── Token Usage Report ────────────────────────────────────────────────────
    _write_token_report(artifacts_dir, orchestrator)

    # ── Result and Extraction ──────────────────────────────────────────────
    final_status = final_state.get("status", "unknown")
    failed = final_state.get("failed_requirements", [])
    file_system = final_state.get("file_system", {})
    plan = final_state.get("plan", [])

    if final_status in ("plan_complete", "completed_with_review", "completed_successfully"):
        print("\n✅  PIPELINE COMPLETED SUCCESSFULLY!")
    else:
        print(f"\n⚠️  PIPELINE FINISHED WITH STATUS: '{final_status}'")

    print(f"\n💾 Extracting artifacts to the local machine ({workspace_dir}/)...")

    # 1. Extracts the source code
    if file_system:
        for filepath, content in file_system.items():
            clean_path = filepath.lstrip("/")
            if clean_path.startswith("home/user/"):
                clean_path = clean_path.replace("home/user/", "", 1)

            full_path = os.path.join(workspace_dir, clean_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)

            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)

    # 2. Extracts planner.txt
    if plan:
        plan_path = os.path.join(artifacts_dir, "planner.txt")
        with open(plan_path, "w", encoding="utf-8") as f:
            f.write("TDD SUB-REQUIREMENTS PLAN\n")
            f.write("=" * 80 + "\n")
            for i, item in enumerate(plan):
                f.write(f"{i+1}. {item}\n")
            f.write("=" * 80 + "\n")

    # 3. Extracts the Engineer's specifications
    spec_path = os.path.join(artifacts_dir, "engineer_specifications.txt")
    with open(spec_path, "w", encoding="utf-8") as f:
        f.write("ENGINEER'S TECHNICAL SPECIFICATIONS\n")
        f.write("=" * 80 + "\n")
        f.write(specification)

    # 4. Extracts the validated requirements history
    reqs_path = os.path.join(artifacts_dir, "confirmed_user_requirements.txt")
    with open(reqs_path, "w", encoding="utf-8") as f:
        f.write("REQUIREMENTS SUBMITTED BY THE USER\n")
        f.write("=" * 80 + "\n")
        f.write(formatted_requirements + "\n")
        f.write("=" * 80 + "\n")

    # 5. Generate the resilience metrics report
    total_failures = final_state.get("total_detected_failures", 0)
    corrected_failures = final_state.get("autonomously_corrected_failures", 0)
    test_faults = final_state.get("test_faults", 0)
    implementation_faults = final_state.get("implementation_faults", 0)
    write_resilience_metrics(artifacts_dir, total_failures, corrected_failures, test_faults, implementation_faults)

    # 6. Generate the per-sub-requirement pass/fail report
    subreq_success = final_state.get("subreq_success_count", 0)
    subreq_failure = final_state.get("subreq_failure_count", 0)
    subreq_results = final_state.get("subreq_results", [])
    write_pass_rate_report(artifacts_dir, plan, subreq_results, subreq_success, subreq_failure, failed)

    print("📁 All artifacts and logs were saved successfully!")

    if failed:
        print(f"\n⚠️  {len(failed)} requirement(s) could not be satisfied:")
        for req in failed:
            print(f"   • {req.get('requirement', req)}")

    print(f"\n🔑 Thread ID for this run: {thread_id}")
    print("   (To inspect the state or resume later, use --thread-id)\n")


if __name__ == "__main__":
    main()
