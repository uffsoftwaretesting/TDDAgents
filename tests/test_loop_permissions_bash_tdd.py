"""
Barrier 2 through `Bash`: `app/tdd/bash_phase_permissions.py`.

The hole these tests exist for: the phase gate reads a tool's declared path argument, so it
saw `WriteFile(file_path="calculator.py")` and refused it in RED, but never saw
`Bash("echo code > calculator.py")` — the same write, through a tool whose only argument is
a string. One of the three independent barriers was bypassable by rephrasing.
"""

from __future__ import annotations

import pytest

from app.tdd.bash_phase_permissions import check_bash_tdd_phase, redirect_targets
from app.tdd.ledger import PhaseLedger, TddPhase
from tests.plan_helpers import TEST_PROJECT_CONFIG

RED = PhaseLedger()
GREEN = PhaseLedger(phase=TddPhase.GREEN, tests_written=True)
REFACTOR = PhaseLedger(phase=TddPhase.REFACTOR, tests_written=True, green_passed=True)


def allowed(command: str, ledger: PhaseLedger) -> bool:
    return check_bash_tdd_phase(command, ledger, rules=TEST_PROJECT_CONFIG.rules)[0]


# ── extraction ──────────────────────────────────────────────────────────────

def test_targets_are_extracted_from_both_write_operators():
    assert redirect_targets("echo x > a.py")[0] == ("a.py",)
    assert redirect_targets("echo x >> a.py")[0] == ("a.py",)


def test_a_command_with_no_redirection_has_no_targets():
    assert redirect_targets("pytest -q") == ((), False)


def test_discards_to_dev_null_are_not_writes():
    assert redirect_targets("noisy_cmd > /dev/null")[0] == ()
    assert redirect_targets("noisy_cmd 2>&1")[0] == ()


def test_an_expanded_target_is_reported_as_unverifiable():
    assert redirect_targets("echo x > $OUT")[1] is True


def test_several_targets_are_all_extracted():
    targets, _ = redirect_targets("echo a > one.py && echo b > two.py")
    assert set(targets) == {"one.py", "two.py"}


# ── RED ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("command", [
    "echo 'def add(a,b): return a+b' > calculator.py",
    "echo x >> src/app.py",
    "cat template.py > app/models.py",
    "printf 'x' > ./calculator.py",
])
def test_red_denies_writing_production_code_through_a_redirection(command):
    permitted, reason = check_bash_tdd_phase(command, RED, rules=TEST_PROJECT_CONFIG.rules)
    assert permitted is False
    assert "RED denies writing to production file" in reason


@pytest.mark.parametrize("command", [
    "echo 'def test_add(): pass' > tests/test_calc.py",
    "cat > test_calc.py",
    "echo x >> tests/conftest.py",
])
def test_red_allows_writing_tests_through_a_redirection(command):
    assert allowed(command, RED) is True


@pytest.mark.parametrize("command", ["ls -la", "pytest -q", "mkdir -p src", "cat calculator.py"])
def test_red_does_not_object_to_commands_that_write_nothing(command):
    assert allowed(command, RED) is True


UNRESOLVABLE = ["echo x > $TARGET", "printf '#!/bin/sh' > ~/.local/bin/composer", "echo x > `pwd`/a.py",
                "echo x > src/*.py", "echo x > {a,b}.py"]


@pytest.mark.parametrize("ledger", [RED, GREEN])
@pytest.mark.parametrize("command", UNRESOLVABLE)
def test_a_target_the_tier_cannot_resolve_is_left_to_upstreams_ask(command, ledger):
    """
    User decision 2026-11-21: a target built from shell expansion (`hasDangerousExpansion`, `utils/bash/commands.ts`
    l.830, every `~` included) is not the tier's to refuse; upstream's `checkPathConstraints` asks for it
    (`pathValidation.ts` l.1052), so the user decides.
    """
    assert check_bash_tdd_phase(command, ledger, rules=TEST_PROJECT_CONFIG.rules) == (True, "")


def test_a_resolvable_target_beside_an_unresolvable_one_is_still_judged():
    permitted, reason = check_bash_tdd_phase("echo a > $OUT && echo b > calculator.py", RED,
                                             rules=TEST_PROJECT_CONFIG.rules)
    assert (permitted, reason) == (False, RED_PRODUCTION)
    permitted, reason = check_bash_tdd_phase("echo a > ~/x && echo b > tests/test_calc.py", GREEN,
                                             rules=TEST_PROJECT_CONFIG.rules)
    assert permitted is False and "denies modifying test file 'tests/test_calc.py'" in reason


def test_with_no_declaration_an_unresolvable_redirection_is_still_refused():
    from app.tdd.phase_permissions import NO_PROJECT_CONFIG

    assert check_bash_tdd_phase("echo x > $OUT", RED, rules=None) == (
        False, f"TDD phase RED denies this command's redirection: {NO_PROJECT_CONFIG}")


def test_the_refusal_names_the_file_and_the_alternative():
    _, reason = check_bash_tdd_phase("echo x > calculator.py", RED, rules=TEST_PROJECT_CONFIG.rules)
    assert "'calculator.py'" in reason
    assert "Only test files may be written" in reason


# ── GREEN and REFACTOR ──────────────────────────────────────────────────────

def test_green_denies_rewriting_the_tests():
    permitted, reason = check_bash_tdd_phase("echo 'assert True' > tests/test_calc.py", GREEN,
                                             rules=TEST_PROJECT_CONFIG.rules)
    assert permitted is False
    assert "denies modifying test file" in reason


@pytest.mark.parametrize("command", [
    "echo 'assert True' > tests/test_calc.py", "echo x >> tests/test_calc.py", "echo x > $TARGET",
    "echo 'def add(a,b): return a+b' > calculator.py",
])
def test_refactor_may_write_tests_and_code_alike(command):
    """REFACTOR is mandatory and improves both (Phase 12): nothing is refused there."""
    assert check_bash_tdd_phase(command, REFACTOR, rules=TEST_PROJECT_CONFIG.rules) == (True, "")


@pytest.mark.parametrize("ledger", [GREEN, REFACTOR])
def test_the_implementing_phases_allow_writing_production_code(ledger):
    assert allowed("echo 'def add(a,b): return a+b' > calculator.py", ledger) is True


# ── through the gate, which is where it actually matters ────────────────────

def test_the_permission_gate_routes_bash_through_the_phase_check():
    from app.tdd.phase_permissions import check_tdd_phase_permission
    from app.tool import build_tool

    async def noop(args, ctx):  # pragma: no cover - never called
        raise AssertionError

    bash = build_tool(name="Bash", prompt="p", call=noop)
    permitted, reason = check_tdd_phase_permission(bash, {"command": "echo x > calculator.py"}, RED,
                                                   rules=TEST_PROJECT_CONFIG.rules)
    assert permitted is False and "RED denies writing to production file" in reason

    assert check_tdd_phase_permission(bash, {"command": "pytest -q"}, RED,
                                      rules=TEST_PROJECT_CONFIG.rules) == (True, "")


def test_run_tests_is_never_denied_even_though_it_is_not_bash():
    from app.tdd.phase_permissions import check_tdd_phase_permission
    from app.tool import build_tool

    async def noop(args, ctx):  # pragma: no cover - never called
        raise AssertionError

    run_tests = build_tool(name="RunTests", prompt="p", call=noop)
    assert check_tdd_phase_permission(run_tests, {"test_path": "tests"}, RED,
                                      rules=TEST_PROJECT_CONFIG.rules) == (True, "")


# ── the exact refusals, because the message is the model's only feedback ────

RED_PRODUCTION = (
    "TDD phase RED denies writing to production file 'calculator.py' through a shell "
    "redirection. Only test files may be written before this sub-requirement's tests are "
    "written and run."
)
GREEN_TEST_FILE = (
    "TDD phase GREEN denies modifying test file 'tests/test_calc.py' through a shell "
    "redirection. The tests are what pins the behaviour being implemented; they can be "
    "improved in REFACTOR. If a test is wrong (it contradicts the specification), go back to RED: "
    "spawn the tester agent (Agent with subagent_type 'tester') to fix it. That restarts this "
    "sub-requirement at RED; then implement again."
)


def test_the_red_production_refusal_reads_exactly_this():
    assert check_bash_tdd_phase("echo x > calculator.py", RED,
                                rules=TEST_PROJECT_CONFIG.rules) == (False, RED_PRODUCTION)


def test_the_green_test_file_refusal_reads_exactly_this():
    assert check_bash_tdd_phase("echo x > tests/test_calc.py", GREEN,
                                rules=TEST_PROJECT_CONFIG.rules) == (False, GREEN_TEST_FILE)


def test_a_permitted_command_returns_an_empty_reason():
    assert check_bash_tdd_phase("pytest -q", RED, rules=TEST_PROJECT_CONFIG.rules) == (True, "")
    assert check_bash_tdd_phase("echo x > tests/test_a.py", RED, rules=TEST_PROJECT_CONFIG.rules) == (True, "")


@pytest.mark.parametrize("phase", list(TddPhase))
def test_a_declared_manifest_may_be_redirected_to_in_every_phase(phase: TddPhase):
    import dataclasses

    config = dataclasses.replace(TEST_PROJECT_CONFIG, manifest_files=("requirements.txt",))
    ledger = PhaseLedger(phase=phase, tests_written=phase != TddPhase.RED, green_passed=phase == TddPhase.REFACTOR)
    assert check_bash_tdd_phase("echo pytest >> requirements.txt", ledger, rules=config.rules) == (True, "")


def test_with_no_declaration_a_redirection_is_refused_and_a_plain_command_is_not():
    from app.tdd.phase_permissions import NO_PROJECT_CONFIG

    ledger = PhaseLedger(phase=TddPhase.REFACTOR, tests_written=True, green_passed=True)
    assert check_bash_tdd_phase("echo x > tests/test_a.py", ledger, rules=None) == (
        False, f"TDD phase REFACTOR denies this command's redirection: {NO_PROJECT_CONFIG}")
    assert check_bash_tdd_phase("ls", ledger, rules=None) == (True, "")


def test_targets_are_matched_from_the_project_root():
    red = PhaseLedger()
    assert check_bash_tdd_phase("echo x > /proj/tests/test_a.py", red, rules=TEST_PROJECT_CONFIG.rules,
                                root="/proj") == (True, "")
    # Outside the project the phase has nothing to say: upstream's path validation asks (2026-11-21).
    assert check_bash_tdd_phase("echo x > /other/tests/test_a.py", red, rules=TEST_PROJECT_CONFIG.rules,
                                root="/proj") == (True, "")
    assert check_bash_tdd_phase("printf x > /home/u/.local/bin/composer", red, rules=TEST_PROJECT_CONFIG.rules,
                                root="/proj") == (True, "")


# ── through the whole permission pipeline: upstream's ask, the user decides ────

SHELL_EXPANSION = "Shell expansion syntax in paths requires manual approval"


@pytest.mark.parametrize("mode", ["acceptEdits", "default"])
@pytest.mark.parametrize(("command", "reason"), [
    # `checkPathConstraints` (pathValidation.ts l.1052)
    ("printf x > ~/.local/bin/tool", SHELL_EXPANSION),
    # `bashSecurity.ts` asks first for a variable in a redirection, upstream's order
    ("echo x > $HOME/a.py", "Command contains variables in dangerous contexts (redirections or pipes)"),
])
def test_in_red_an_unresolvable_target_asks_with_upstreams_reason(command, reason, mode):
    import asyncio

    from app.state.app_state_store import AppState, AppStateStore
    from app.tool import tool_context_for
    from app.tools.bash_tool.bash_tool import build_bash_tool
    from app.types.permissions import PermissionMode, ToolPermissionContext
    from app.utils.permissions.permissions import has_permissions_to_use_tool
    from tests.plan_helpers import CURRENT_ITEM_PLAN

    store = AppStateStore(AppState(tdd_plan=CURRENT_ITEM_PLAN, project_config=TEST_PROJECT_CONFIG,
                                   phase_ledger=PhaseLedger()))
    ctx = tool_context_for(store, permission_context=ToolPermissionContext(mode=PermissionMode(mode)))
    res = asyncio.run(has_permissions_to_use_tool(build_bash_tool(), {"command": command}, ctx))
    assert res.behavior == "ask"
    assert res.message == reason
    assert (res.decision_reason or {}).get("type") != "phase_rule"
    # A production target the tier can resolve is still the tier's refusal, before any ask.
    denied = asyncio.run(has_permissions_to_use_tool(build_bash_tool(), {"command": "echo x > calculator.py"}, ctx))
    assert denied.behavior == "deny" and (denied.decision_reason or {}).get("type") == "phase_rule"


@pytest.mark.parametrize(("tool_name", "args"), [
    ("Bash", {"command": "printf x > {home}/.local/bin/composer"}),
    ("WriteFile", {"file_path": "{home}/.local/bin/composer", "content": "x"}),
])
def test_in_red_a_write_outside_the_project_reaches_upstreams_ask(tmp_path, tool_name, args):
    """
    The live run's case by absolute path: no phase judges a file outside the project (2026-11-21), so the call
    reaches upstream's own outside-the-working-directory ask (`validateOutputRedirections` for Bash,
    `checkWritePermissionForTool` for the file tool), never the tier's refusal.
    """
    import asyncio

    from app.state.app_state_store import AppState, AppStateStore
    from app.tool import tool_context_for
    from app.tools.bash_tool.bash_tool import build_bash_tool
    from app.tools.file_write_tool.file_write_tool import build_write_file_tool
    from app.types.permissions import PermissionMode, ToolPermissionContext
    from app.utils.permissions.permissions import has_permissions_to_use_tool
    from app.workspace.local import LocalWorkspace
    from tests.plan_helpers import CURRENT_ITEM_PLAN

    project, home = tmp_path / "project", tmp_path / "home"
    project.mkdir()
    store = AppStateStore(AppState(tdd_plan=CURRENT_ITEM_PLAN, project_config=TEST_PROJECT_CONFIG,
                                   phase_ledger=PhaseLedger()))
    ctx = tool_context_for(store, workspace=LocalWorkspace(str(project)),
                           permission_context=ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS))
    tool = build_bash_tool() if tool_name == "Bash" else build_write_file_tool()
    call = {key: value.format(home=home) for key, value in args.items()}
    res = asyncio.run(has_permissions_to_use_tool(tool, call, ctx))
    assert res.behavior == "ask"
    assert (res.decision_reason or {}).get("type") != "phase_rule"
    # The same write inside the project is still the phase's refusal.
    inside = {key: value.format(home=project).replace("/.local/bin/composer", "/src/app.py")
              for key, value in args.items()}
    denied = asyncio.run(has_permissions_to_use_tool(tool, inside, ctx))
    assert denied.behavior == "deny" and (denied.decision_reason or {}).get("type") == "phase_rule"


# ── a `cd` in the command: relative targets are upstream's to ask about (2026-11-21) ──────────────────────

@pytest.mark.parametrize(("command", "ledger"), [
    ("mkdir -p /tmp/spike && cd /tmp/spike && cat > main.go", RED),   # the Go live run's command
    ("cd /tmp/spike && echo x >> calculator.py", RED),
    ("pushd build && echo x > app.py", RED),
    ("cd tests && echo 'assert 1' > test_calc.py", GREEN),
    ("cd src; popd; echo x > calculator.py", RED),
])
def test_after_a_cd_a_relative_target_is_left_to_upstreams_ask(command, ledger):
    """
    `compoundCommandHasCd` (`bashPermissions.ts` l.2181-2200): after a `cd`, `pushd` or `popd` the final working
    directory cannot be determined, so `validateOutputRedirections` (`pathValidation.ts` l.930-945) asks for every
    redirection. The tier cannot place a relative target either: it is left to that ask, never refused as if it
    were in the project.
    """
    assert check_bash_tdd_phase(command, ledger, rules=TEST_PROJECT_CONFIG.rules, root="/proj") == (True, "")


def test_a_cd_to_the_shells_own_directory_is_no_cd():
    """`filterCdCwdSubcommands` (`bashPermissions.ts` l.1362-1377): the `cd <cwd>` models prepend is dropped."""
    assert check_bash_tdd_phase("cd /proj && echo x > calculator.py", RED, rules=TEST_PROJECT_CONFIG.rules,
                                root="/proj") == (False, RED_PRODUCTION)
    assert check_bash_tdd_phase("cd /proj/tests && echo x > calculator.py", RED, "tests",
                                rules=TEST_PROJECT_CONFIG.rules, root="/proj")[0] is True  # tests/calculator.py


def test_after_a_cd_an_absolute_target_is_still_judged():
    """A `cd` does not move an absolute path: a production file of the project is still refused in RED."""
    permitted, reason = check_bash_tdd_phase("cd /tmp && echo x > /proj/calculator.py", RED,
                                             rules=TEST_PROJECT_CONFIG.rules, root="/proj")
    assert permitted is False and "RED denies writing to production file '/proj/calculator.py'" in reason
    permitted, reason = check_bash_tdd_phase("cd tests && echo x > /proj/tests/test_a.py", GREEN,
                                             rules=TEST_PROJECT_CONFIG.rules, root="/proj")
    assert permitted is False and "denies modifying test file" in reason


def test_after_a_cd_with_no_declaration_a_redirect_is_still_refused():
    from app.tdd.phase_permissions import NO_PROJECT_CONFIG

    assert check_bash_tdd_phase("cd /tmp && echo x > a.py", RED, rules=None) == (
        False, f"TDD phase RED denies this command's redirection: {NO_PROJECT_CONFIG}")


def test_in_red_the_live_runs_cd_command_asks_with_upstreams_message(tmp_path):
    import asyncio

    from app.state.app_state_store import AppState, AppStateStore
    from app.tool import tool_context_for
    from app.tools.bash_tool.bash_tool import build_bash_tool
    from app.types.permissions import PermissionMode, ToolPermissionContext
    from app.utils.permissions.permissions import has_permissions_to_use_tool
    from app.workspace.local import LocalWorkspace
    from tests.plan_helpers import CURRENT_ITEM_PLAN

    project = tmp_path / "project"
    project.mkdir()
    store = AppStateStore(AppState(tdd_plan=CURRENT_ITEM_PLAN, project_config=TEST_PROJECT_CONFIG,
                                   phase_ledger=PhaseLedger()))
    ctx = tool_context_for(store, workspace=LocalWorkspace(str(project)),
                           permission_context=ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS))
    res = asyncio.run(has_permissions_to_use_tool(
        build_bash_tool(), {"command": "mkdir -p /tmp/spike && cd /tmp/spike && cat > main.go"}, ctx))
    assert res.behavior == "ask"
    assert (res.decision_reason or {}).get("type") != "phase_rule"
