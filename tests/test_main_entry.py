"""Phases 9 and 11: the entry point — interactive and headless (`-p`) sessions."""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any

import pytest

import app.main as entry
from app.cli.print import DevelopmentOutcome
from app.query.transitions import Terminal
from app.session.driver import SessionOptions
from app.tdd.ledger import PhaseLedger, TddPhase
from app.tdd.plan import TddPlan

CYCLE = PhaseLedger(phase=TddPhase.REFACTOR, tests_written=True, green_passed=True, refactor_passed=True)
PLAN = TddPlan.from_texts(["Item A"])
DONE = DevelopmentOutcome(terminal=Terminal.COMPLETED, plan=PLAN.complete_current(CYCLE))
STOPPED = DevelopmentOutcome(terminal=Terminal.STOP_HOOK_PREVENTED, plan=PLAN)
#: A session that ended in plan mode: no approved plan, nothing developed.
UNPLANNED = DevelopmentOutcome(terminal=Terminal.COMPLETED, plan=TddPlan())


@pytest.fixture
def fake_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """The session's project, `session_exists` and the two develop paths, faked over a tmp workspace."""
    monkeypatch.chdir(tmp_path)
    # setenv first so teardown restores the variable `--coordinator` writes to os.environ.
    monkeypatch.setenv("TDDAGENTS_COORDINATOR_MODE", "")
    monkeypatch.delenv("TDDAGENTS_COORDINATOR_MODE")
    holder: dict[str, Any] = {"outcome": DONE, "options": [], "threads": [], "developed": [], "prompts": [],
                              "sessions": set()}

    class Workspace:
        root = tmp_path / "session" / "project"
        run_dir = tmp_path / "session"

    Workspace.root.mkdir(parents=True)
    (Workspace.root / "calc.py").write_text("x = 1\n")

    def fake_workspace(thread_id: str) -> Any:
        holder["threads"].append(thread_id)
        return Workspace()

    def fake_develop(thread_id: str, request: str | None, workspace: Any, options: Any,
                     prompt: str | None = None) -> DevelopmentOutcome:
        holder["developed"].append((thread_id, request))
        holder["options"].append(options)
        holder["prompts"].append(prompt)
        holder["sessions"].add(thread_id)
        outcome: DevelopmentOutcome = holder["outcome"]
        return outcome

    monkeypatch.setattr("app.cli.print.session_workspace", fake_workspace)
    monkeypatch.setattr(entry, "session_exists", lambda thread_id, workspace: thread_id in holder["sessions"])
    monkeypatch.setattr(entry, "develop_headless", fake_develop)
    monkeypatch.setattr(entry, "develop_interactive", fake_develop)
    return holder


def test_parse_args_defaults() -> None:
    args = entry.parse_args([])
    assert args.prompt is None and args.print is False and args.spec is None
    assert args.fresh is False and args.thread_id is None
    assert args.permission_mode is None and args.settings is None
    assert args.dangerously_skip_permissions is False and args.coordinator is False


def test_parse_args_flags() -> None:
    args = entry.parse_args(["-p", "Build X", "--spec", "s.txt", "--fresh", "--thread-id", "t",
                             "--permission-mode", "auto", "--settings", "f.json",
                             "--dangerously-skip-permissions", "--coordinator"])
    assert (args.print, args.prompt, args.spec, args.fresh, args.thread_id) == (True, "Build X", "s.txt", True, "t")
    assert (args.permission_mode, args.settings, args.dangerously_skip_permissions, args.coordinator) == (
        "auto", "f.json", True, True)
    assert entry.parse_args(["--print"]).print is True


def test_parse_args_rejects_an_unknown_mode() -> None:
    with pytest.raises(SystemExit):
        entry.parse_args(["--permission-mode", "workspace_write"])


def test_session_options() -> None:
    args = entry.parse_args(["-p", "--permission-mode", "plan", "--settings", "f", "--dangerously-skip-permissions"])
    assert entry.session_options(args) == SessionOptions(
        headless=True, permission_mode="plan", settings_path="f", dangerously_skip_permissions=True)
    assert entry.session_options(entry.parse_args([])) == SessionOptions()


def test_read_initial_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = tmp_path / "spec.txt"
    spec.write_text("  from file \n", encoding="utf-8")
    assert entry.read_initial_request(entry.parse_args(["--spec", str(spec), "ignored"])) == "from file"
    assert entry.read_initial_request(entry.parse_args([" typed "])) == "typed"

    class Pipe(io.StringIO):
        def isatty(self) -> bool:
            return False

    monkeypatch.setattr("sys.stdin", Pipe(" piped \n"))
    assert entry.read_initial_request(entry.parse_args(["-p"])) == "piped"
    monkeypatch.setattr("sys.stdin", Pipe("   "))
    assert entry.read_initial_request(entry.parse_args(["-p"])) is None
    monkeypatch.setattr("sys.stdin", Pipe("not read interactively"))
    assert entry.read_initial_request(entry.parse_args([])) is None

    class Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("sys.stdin", Tty("x"))
    assert entry.read_initial_request(entry.parse_args(["-p"])) is None


def test_resolve_thread_id() -> None:
    from app.session.driver import derive_thread_id

    assert entry.resolve_thread_id(entry.parse_args(["--thread-id", "t-1"]), "x") == "t-1"
    assert entry.resolve_thread_id(entry.parse_args([]), "Build X") == derive_thread_id("Build X")
    fresh = entry.resolve_thread_id(entry.parse_args(["--fresh"]), "Build X")
    assert fresh.startswith("tdd-") and fresh != derive_thread_id("Build X")
    assert entry.resolve_thread_id(entry.parse_args([]), None).startswith("tdd-")


def test_headless_run_succeeds(fake_session: dict[str, Any], tmp_path: Path,
                               capsys: pytest.CaptureFixture[str]) -> None:
    code = entry.main(["-p", "Build X", "--permission-mode", "acceptEdits"])
    assert code == 0
    (options,) = fake_session["options"]
    assert options.headless is True and options.permission_mode == "acceptEdits"
    (thread_id,) = fake_session["threads"]
    # Phase 13: the reports go beside the project, nothing into TDDAgents' folder (the cwd here).
    out_dir = tmp_path / "session"
    assert not list(tmp_path.glob("workspace_output_*"))
    assert (out_dir / "metrics_and_logging" / "execution_logs.txt").exists()
    assert (out_dir / "metrics_and_logging" / "token_usage.txt").exists()
    assert (out_dir / "metrics_and_logging" / "final_test_run.txt").exists()
    out = capsys.readouterr().out
    assert "PIPELINE COMPLETED SUCCESSFULLY" in out and thread_id in out and "(HEADLESS)" in out
    assert fake_session["developed"] == [(thread_id, "Build X")]
    assert "1. [DONE   ] Item A" in (out_dir / "metrics_and_logging" / "subreq_results.txt").read_text()


def test_headless_failure_exits_one(fake_session: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    fake_session["outcome"] = STOPPED
    assert entry.main(["-p", "Build X"]) == 1
    out = capsys.readouterr().out
    assert "DEVELOPMENT STOPPED: stop_hook_prevented" in out
    assert "   • 1. Item A: not done" in out


def test_a_session_without_an_approved_plan_exits_one(fake_session: dict[str, Any],
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    """Headless, as upstream's `-p`: the plan's approval is denied, so the session ends in plan mode."""
    fake_session["outcome"] = UNPLANNED
    assert entry.main(["-p", "Build X"]) == 1
    out = capsys.readouterr().out
    assert "\n📋  NO APPROVED PLAN: the session ended in plan mode.\n" in out and "Final test run" not in out


def test_headless_without_a_request_exits_one(fake_session: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    class Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("sys.stdin", Tty(""))
    assert entry.main(["-p"]) == 1
    assert "cannot be empty" in capsys.readouterr().err
    assert fake_session["options"] == []


def test_thread_id_without_a_session_or_request_exits_one(fake_session: dict[str, Any],
                                                          monkeypatch: pytest.MonkeyPatch,
                                                          capsys: pytest.CaptureFixture[str]) -> None:
    class Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("sys.stdin", Tty(""))
    assert entry.main(["-p", "--thread-id", "tdd-missing"]) == 1
    assert "No session 'tdd-missing' to continue" in capsys.readouterr().err


def test_interactive_run_uses_the_menu_and_answers(fake_session: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(entry, "get_initial_user_prompt", lambda: "Build Y")
    assert entry.main([]) == 0
    assert fake_session["options"][0].headless is False
    from app.session.driver import derive_thread_id

    assert fake_session["threads"] == [derive_thread_id("Build Y")]
    assert "(INTERACTIVE)" in capsys.readouterr().out


def test_menu_cancel_exits_zero(fake_session: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    def cancel() -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(entry, "get_initial_user_prompt", cancel)
    assert entry.main([]) == 0
    assert fake_session["options"] == []


def test_keyboard_interrupt_during_the_drive(
    fake_session: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def interrupted(*args: Any, **kwargs: Any) -> DevelopmentOutcome:
        raise KeyboardInterrupt

    monkeypatch.setattr(entry, "develop_headless", interrupted)
    assert entry.main(["-p", "Build X", "--thread-id", "tdd-k"]) == 130
    assert "--thread-id tdd-k" in capsys.readouterr().out


def test_coordinator_flag_sets_the_switch(fake_session: dict[str, Any]) -> None:
    entry.main(["-p", "Build X", "--coordinator"])
    assert os.environ["TDDAGENTS_COORDINATOR_MODE"] == "1"


def test_without_coordinator_flag_the_switch_is_untouched(fake_session: dict[str, Any]) -> None:
    entry.main(["-p", "Build X"])
    assert "TDDAGENTS_COORDINATOR_MODE" not in os.environ


def test_report_status_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                             capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(tmp_path)

    class Workspace:
        root = tmp_path / "missing"

    entry.report("t", Workspace(), None, DONE)
    out = capsys.readouterr().out
    # Without a session directory the reports go to `<workspaces root>/<thread>/`.
    from app.workspace.local import workspaces_root

    assert f"📝 Reports: {workspaces_root() / 't' / 'metrics_and_logging'}/" in out


def test_the_log_file_handler_is_removed(fake_session: dict[str, Any]) -> None:
    import logging

    before = list(logging.getLogger().handlers)
    entry.main(["-p", "Build X"])
    assert logging.getLogger().handlers == before


# ── exact output: the entry point's user-facing text (Phase 9 mutation run) ──

WARNING_TEXT = (
    "WARNING: The Analyst agent may ask for more details about the requirements, "
    "which will require more interaction and prompts from the user. For the purposes "
    "of the scientific research being conducted, we consider attempts where the Analyst "
    "agent did not ask for any additional implementation details and considered the initial "
    "prompt sufficient to elicit the requirements. Therefore, when using any of the prompt "
    "options provided, we recommend discarding any attempts that request further detail. "
    "Only consider attempts where the INITIAL prompt is sufficient for requirements elicitation.\n"
)


def _scripted_input(monkeypatch: pytest.MonkeyPatch, replies: list[str]) -> list[str]:
    prompts: list[str] = []

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        return replies.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)
    return prompts


def test_menu_lists_txt_specs_and_loads_the_choice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    specs = tmp_path / "app" / "prompts" / "specs"
    specs.mkdir(parents=True)
    (specs / "b.txt").write_text("  spec B  \n", encoding="utf-8")
    (specs / "a.txt").write_text("spec A", encoding="utf-8")
    (specs / "notes.md").write_text("ignored", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    prompts = _scripted_input(monkeypatch, ["", "  ", "x", "0", "4", "2"])
    assert entry.get_initial_user_prompt() == "spec B"
    assert prompts == ["Select an option: "] * 6
    assert capsys.readouterr().out == (
        WARNING_TEXT + "\n"
        "USE CASE MENU (type the desired option):\n\n"
        "1. a.txt\n2. b.txt\n3. Type your own prompt\n\n"
        "❌ Invalid input. Please type a number corresponding to the menu.\n"
        "❌ Invalid option. Try again.\n"
        "❌ Invalid option. Try again.\n"
        "\n✅ [Prompt loaded from 'b.txt']\n\n"
    )


def test_menu_custom_prompt_and_missing_specs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(tmp_path)
    prompts = _scripted_input(monkeypatch, ["1", "  my own request  "])
    assert entry.get_initial_user_prompt() == "my own request"
    assert prompts == ["Select an option: ", "\n👤 [Your Initial Request]: "]
    out = capsys.readouterr().out
    assert out == (
        WARNING_TEXT + "\n"
        "⚠️ Warning: Specs directory not found at 'app/prompts/specs'.\n\n"
        "USE CASE MENU (type the desired option):\n\n"
        "1. Type your own prompt\n\n"
    )


def test_menu_specs_path_is_a_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                   capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "app" / "prompts").mkdir(parents=True)
    (tmp_path / "app" / "prompts" / "specs").write_text("not a dir")
    monkeypatch.chdir(tmp_path)
    _scripted_input(monkeypatch, ["1", "r"])
    assert entry.get_initial_user_prompt() == "r"
    assert "Specs directory not found" in capsys.readouterr().out


def test_parser_help_and_metadata() -> None:
    parser = entry.build_parser()
    assert parser.description == (
        "Runs the TDDAgents pipeline: requirements, plan, then test-driven development of the plan.")
    helps = {action.dest: action.help for action in parser._actions}
    assert helps == {
        "help": "show this help message and exit",
        "prompt": "The initial request (instead of the menu).",
        "print": "Headless: ask nothing, exit non-zero on failure.",
        "spec": "Read the initial request from this file.",
        "fresh": "Forces a new run by generating a random thread_id.",
        "thread_id": "Explicitly sets the thread_id (continues that session).",
        "permission_mode": None,
        "settings": "A settings file (upstream's flag settings).",
        "dangerously_skip_permissions": None,
        "coordinator": "Run development in coordinator mode.",
    }
    flags = {action.dest: tuple(action.option_strings) for action in parser._actions}
    assert flags["print"] == ("-p", "--print")
    assert flags["dangerously_skip_permissions"] == ("--dangerously-skip-permissions",)
    assert next(a for a in parser._actions if a.dest == "permission_mode").choices == [
        "default", "acceptEdits", "plan", "bypassPermissions", "dontAsk", "auto"]


def test_subreq_results_exact(tmp_path: Path) -> None:
    plan = TddPlan.from_texts(["A", "B", "C"]).complete_current(CYCLE)
    entry.write_subreq_results(str(tmp_path), plan, Terminal.MODEL_ERROR)
    bar = "=" * 80
    assert (tmp_path / "subreq_results.txt").read_text(encoding="utf-8") == (
        f"SUB-REQUIREMENT RESULTS\n{bar}\n"
        "Total sub-requirements: 3\nDone: 1\nNot done: 2\nSession ended: model_error\n" + "-" * 80 + "\n"
        "1. [DONE   ] A\n2. [FAILED ] B\n   Terminal: model_error\n"
        f"3. [NOT_RUN] C\n{bar}\n"
    )


def test_subreq_results_of_a_completed_session_mark_nothing_failed(tmp_path: Path) -> None:
    entry.write_subreq_results(str(tmp_path), DONE.plan, Terminal.COMPLETED)
    text = (tmp_path / "subreq_results.txt").read_text(encoding="utf-8")
    assert "Session ended: completed\n" in text and "1. [DONE   ] Item A\n" in text and "FAILED" not in text


def test_subreq_results_without_a_terminal_lists_the_plan(tmp_path: Path) -> None:
    entry.write_subreq_results(str(tmp_path), TddPlan.from_texts(["A", "B"]), None)
    text = (tmp_path / "subreq_results.txt").read_text(encoding="utf-8")
    assert "Session ended" not in text
    assert "1. [NOT_RUN] A\n2. [NOT_RUN] B\n" in text


def test_subreq_results_with_nothing(tmp_path: Path) -> None:
    bar = "=" * 80
    for plan in (None, TddPlan()):
        entry.write_subreq_results(str(tmp_path), plan, Terminal.COMPLETED)
        assert (tmp_path / "subreq_results.txt").read_text(encoding="utf-8") == (
            f"SUB-REQUIREMENT RESULTS\n{bar}\n(no results)\n{bar}\n"
        )


def test_token_report_exact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    class Tracker:
        def summary(self) -> dict[str, Any]:
            return {"by_model": {"m": {"total_tokens": 3}},
                    "totals": {"total_tokens": 3, "prompt_tokens": 2, "completion_tokens": 1, "cached_tokens": 0}}

    monkeypatch.setattr("app.services.api.claude.get_global_token_tracker", lambda: Tracker())
    with caplog.at_level(logging.INFO, logger="TDDMain"):
        entry._write_token_report(str(tmp_path))
    assert (tmp_path / "token_usage.txt").read_text(encoding="utf-8") == (
        "TOKEN USAGE REPORT\n" + "=" * 80 + "\n\nBY MODEL:\n  m:\n    total_tokens: 3\n\nTOTALS:\n"
        "  total_tokens: 3\n  prompt_tokens: 2\n  completion_tokens: 1\n  cached_tokens: 0\n"
    )
    assert "📊 Token Usage — total: 3 | prompt: 2 | completion: 1 | cached: 0" in caplog.text


def test_report_exact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(tmp_path)
    ws = tmp_path / "session" / "project"
    ws.mkdir(parents=True)

    class Workspace:
        root = ws
        run_dir = tmp_path / "session"

    out_dir = tmp_path / "session" / "metrics_and_logging"
    entry.report("t", Workspace(), "Build X", STOPPED)
    assert capsys.readouterr().out == (
        f"\n📁 Project: {ws}\n"
        f"📝 Reports: {out_dir}/\n"
        "🧪 Final test run: could not run the tests: no workspace is available to run the tests in\n"
        "\n⚠️  DEVELOPMENT STOPPED: stop_hook_prevented\n"
        "   • 1. Item A: not done\n"
        "\n🔑 Thread ID for this run: t\n"
        "   (To inspect the state or resume later, use --thread-id)\n\n"
    )
    assert "2. " not in (out_dir / "subreq_results.txt").read_text()
    assert "could not run the tests" in (out_dir / "final_test_run.txt").read_text()
    plan_file = tmp_path / "plans" / "a-b-c.md"
    plan_file.parent.mkdir()
    plan_file.write_text("# Plan so far\n")
    entry.report("t", Workspace(), "Build X", DevelopmentOutcome(terminal=Terminal.COMPLETED, plan=TddPlan(),
                                                                 plan_file=str(plan_file)))
    out = capsys.readouterr().out
    assert f"\n📋  NO APPROVED PLAN: the session ended in plan mode.\n   The plan so far: {plan_file}\n" in out
    assert "Final test run" not in out and not (out_dir / "approved_plan.md").exists()
    entry.report("t", Workspace(), None, DevelopmentOutcome(terminal=Terminal.MODEL_ERROR, plan=DONE.plan))
    out = capsys.readouterr().out
    assert "DEVELOPMENT STOPPED: model_error" in out and "not done" not in out
    assert (out_dir / "token_usage.txt").exists()
    assert not list(tmp_path.glob("workspace_output_*"))


def test_the_final_test_run_is_the_projects_own_suite(python_workspace, tmp_path: Path) -> None:
    """Phase 13: exactly RunTests' command in the project; a skipped test means not passing."""
    python_workspace.write_file("calc.py", "def add(a, b):\n    return a + b\n")
    python_workspace.write_file("tests/test_calc.py", (
        "import pytest\nfrom calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n\n"
        "def test_lint():\n    pytest.importorskip('not_installed_anywhere')\n"))
    summary = entry.write_final_test_run(str(tmp_path), python_workspace)
    assert summary == "NOT passing (exit 0: all tests passed, 1 skipped, coverage 100.0% (rule of thumb: 90%))"
    text = (tmp_path / "final_test_run.txt").read_text()
    assert f"Project: {python_workspace.root}" in text and "SKIPPED" in text
    python_workspace.write_file("tests/test_calc.py",
                                "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    assert entry.write_final_test_run(str(tmp_path), python_workspace) == (
        "passed (exit 0: all tests passed, 0 skipped, coverage 100.0% (rule of thumb: 90%))")
    # A test file pytest does not collect never ran: not passing either.
    python_workspace.write_file("pytest.ini", "[pytest]\npython_files = tests/test_*.py\n")
    python_workspace.write_file("tests/tests_extra.py", "def test_extra():\n    assert False\n")
    assert entry.write_final_test_run(str(tmp_path), python_workspace) == (
        "NOT passing (exit 0: all tests passed, 0 skipped, coverage 100.0% (rule of thumb: 90%)); "
        "not collected: tests/tests_extra.py")


def test_main_output_and_log_file(fake_session: dict[str, Any], tmp_path: Path,
                                  capsys: pytest.CaptureFixture[str]) -> None:
    assert entry.main(["-p", "Build X", "--fresh"]) == 0
    (thread_id,) = fake_session["threads"]
    out = capsys.readouterr().out
    assert out.startswith("\n" + "=" * 80 + "\n🚀  TDDAGENTS SESSION (HEADLESS)\n" + "=" * 80 + "\n")
    log_file = tmp_path / "session" / "metrics_and_logging" / "execution_logs.txt"
    assert " [INFO] TDDMain: 📊 Token Usage — total: " in log_file.read_text()
    # Appended, not truncated: a continued session keeps its earlier log.
    entry.main(["-p", "Build X", "--thread-id", thread_id])
    assert log_file.read_text().count("📊 Token Usage") == 2


def test_main_logs_the_thread_id(fake_session: dict[str, Any], caplog: pytest.LogCaptureFixture) -> None:
    import logging

    with caplog.at_level(logging.INFO, logger="TDDMain"):
        entry.main(["-p", "Build X", "--thread-id", "tdd-x"])
    assert "🔑 thread_id: tdd-x" in caplog.text


def test_main_continues_a_thread_without_a_request(fake_session: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    class Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("sys.stdin", Tty(""))
    monkeypatch.setattr(entry, "get_initial_user_prompt", lambda: pytest.fail("the menu must not be shown"))
    assert entry.main(["-p", "Build X", "--thread-id", "tdd-c"]) == 0
    capsys.readouterr()
    # The thread exists, so no request and no menu are needed: its conversation is resumed.
    assert entry.main(["--thread-id", "tdd-c"]) == 0
    assert "PIPELINE COMPLETED SUCCESSFULLY" in capsys.readouterr().out
    assert fake_session["developed"] == [("tdd-c", "Build X"), ("tdd-c", None)]
    # On an existing session the prompt is the user's next message (`--resume <id> "<prompt>"`).
    entry.main(["-p", "--thread-id", "tdd-c", "my answer"])
    assert fake_session["developed"][-1] == ("tdd-c", None) and fake_session["prompts"][-1] == "my answer"


def test_report_success_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                             capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(tmp_path)

    class Workspace:
        root = tmp_path / "none"

    entry.report("t", Workspace(), None, DONE)
    assert "\n\n✅  PIPELINE COMPLETED SUCCESSFULLY!\n\n🔑 Thread ID for this run: t\n" in capsys.readouterr().out
    entry.report("t", Workspace(), None, DevelopmentOutcome(terminal=Terminal.COMPLETED, plan=STOPPED.plan))
    assert "SUCCESSFULLY" not in capsys.readouterr().out


def test_menu_first_option(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    specs = tmp_path / "app" / "prompts" / "specs"
    specs.mkdir(parents=True)
    (specs / "only.txt").write_text("the one", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    _scripted_input(monkeypatch, ["1"])
    assert entry.get_initial_user_prompt() == "the one"


def test_main_messages_are_exact(fake_session: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
                                 capsys: pytest.CaptureFixture[str]) -> None:
    def cancel() -> str:
        raise EOFError

    monkeypatch.setattr(entry, "get_initial_user_prompt", cancel)
    entry.main([])
    assert capsys.readouterr().out == "\n👋 Operation cancelled by the user.\n"
    monkeypatch.setattr(entry, "get_initial_user_prompt", lambda: "")
    assert entry.main([]) == 1
    assert capsys.readouterr().err == "❌ The initial request cannot be empty.\n"
    entry.main(["Build Q"])
    assert "🚀  TDDAGENTS SESSION (INTERACTIVE)\n" in capsys.readouterr().out


def test_the_final_run_never_reports_files_as_uncollected_when_pytest_stopped(python_workspace, tmp_path):
    import app.main as entry

    python_workspace.write_file("tests/test_ok.py", "def test_ok():\n    assert True\n")
    python_workspace.write_file("tests/test_web.py", "import not_installed_anywhere\n\ndef test_w():\n    pass\n")
    summary = entry.write_final_test_run(str(tmp_path), python_workspace)
    assert summary.startswith("NOT passing (exit 2: test execution was interrupted") and "not collected" not in summary


# ── the session's reports ────────────────────────────────────────────────────


def _transcript(path: Path, *entries: dict[str, Any]) -> str:
    import json

    path.write_text("".join(json.dumps({"message": entry}) + "\n" for entry in entries), encoding="utf-8")
    return str(path)


def _msg(kind: str, content: Any, **data: Any) -> dict[str, Any]:
    return {"type": kind, "data": {"content": content, **data}}


def test_planning_dialogue_reads_the_planning_phase_from_the_transcript(tmp_path: Path) -> None:
    path = _transcript(
        tmp_path / "t.jsonl",
        _msg("human", "Build a CPF API"),
        _msg("human", "<system-reminder>plan mode</system-reminder>", additional_kwargs={"is_meta": True}),
        _msg("ai", [{"type": "thinking", "thinking": ""}, {"type": "text", "text": "Which framework?"}]),
        _msg("ai", "", tool_calls=[]),
        _msg("tool", "User has answered your questions: FastAPI", name="AskUserQuestion"),
        _msg("tool", "Exit plan mode? denied", name="ExitPlanMode"),
        _msg("tool", "User has approved your plan. You can now start coding.", name="ExitPlanMode"),
        _msg("ai", "Starting RED for item 1."),
    )
    assert entry.planning_dialogue(path) == (
        "[User]: Build a CPF API\n\n[Analyst]: Which framework?\n\n"
        "[User answers]: User has answered your questions: FastAPI\n\n[User]: approved the plan.")
    assert entry.planning_dialogue(None) == "(empty)"
    assert entry.planning_dialogue(str(tmp_path / "missing.jsonl")) == "(empty)"
    assert entry.planning_dialogue(_transcript(tmp_path / "e.jsonl", _msg("ai", ""))) == "(empty)"


def test_session_artifacts_hold_the_request_the_approved_plan_and_the_dialogue(tmp_path: Path) -> None:
    plan_file = tmp_path / "plan.md"
    plan_file.write_text("# Approved\n\n## Implementation Plan\n1. A\n", encoding="utf-8")
    transcript = _transcript(tmp_path / "t.jsonl", _msg("human", "Build X"))
    outcome = DevelopmentOutcome(terminal=Terminal.COMPLETED, plan=PLAN, plan_file=str(plan_file),
                                 transcript_path=transcript)
    artifacts = Path(entry.write_session_artifacts(str(tmp_path), "Build X " + "y" * 90, outcome))
    bar = "=" * 80
    assert artifacts == tmp_path / "metrics_and_logging"
    assert (artifacts / "initial_user_prompt.txt").read_text(encoding="utf-8") == (
        f"INITIAL PROMPT SENT BY THE USER\n{bar}\nBuild X {'y' * 72}\n{'y' * 18}\n{bar}\n")
    assert (artifacts / "approved_plan.md").read_text(encoding="utf-8") == plan_file.read_text(encoding="utf-8")
    assert (artifacts / "planning_dialogue.txt").read_text(encoding="utf-8") == (
        f"PLANNING DIALOGUE (USER AND ANALYST)\n{bar}\n[User]: Build X\n{bar}\n")
    for old in ("user_analyst_dialogue.txt", "engineer_specifications.txt", "planner.txt",
                "confirmed_user_requirements.txt"):
        assert not (artifacts / old).exists()


def test_artifacts_of_a_resumed_or_unplanned_session(tmp_path: Path) -> None:
    artifacts = Path(entry.write_session_artifacts(str(tmp_path), None, None))
    assert list(artifacts.iterdir()) == []
    unplanned = DevelopmentOutcome(terminal=None, plan=TddPlan(), plan_file=str(tmp_path / "p.md"))
    (tmp_path / "p.md").write_text("draft")
    entry.write_session_artifacts(str(tmp_path), "a\n\n  \nb", unplanned)
    assert not (artifacts / "approved_plan.md").exists()
    assert (artifacts / "planning_dialogue.txt").exists()
    text = (artifacts / "initial_user_prompt.txt").read_text(encoding="utf-8")
    assert text.split("\n")[2:6] == ["a", "", "", "b"]
