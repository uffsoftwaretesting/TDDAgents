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

RED = PhaseLedger()
GREEN = PhaseLedger(phase=TddPhase.GREEN, tests_written=True)
REFACTOR = PhaseLedger(phase=TddPhase.REFACTOR, tests_written=True, green_passed=True)


def allowed(command: str, ledger: PhaseLedger) -> bool:
    return check_bash_tdd_phase(command, ledger)[0]


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
    permitted, reason = check_bash_tdd_phase(command, RED)
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


def test_red_refuses_a_write_it_cannot_locate():
    """Fail closed: in a phase that forbids the write, 'I cannot tell' is a refusal."""
    permitted, reason = check_bash_tdd_phase("echo x > $TARGET", RED)
    assert permitted is False
    assert "cannot verify where this command writes" in reason


def test_the_refusal_names_the_file_and_the_alternative():
    _, reason = check_bash_tdd_phase("echo x > calculator.py", RED)
    assert "'calculator.py'" in reason
    assert "Only test files may be written" in reason


# ── GREEN and REFACTOR ──────────────────────────────────────────────────────

def test_green_denies_rewriting_the_tests():
    permitted, reason = check_bash_tdd_phase("echo 'assert True' > tests/test_calc.py", GREEN)
    assert permitted is False
    assert "denies modifying test file" in reason


@pytest.mark.parametrize("command", [
    "echo 'assert True' > tests/test_calc.py", "echo x >> tests/test_calc.py", "echo x > $TARGET",
    "echo 'def add(a,b): return a+b' > calculator.py",
])
def test_refactor_may_write_tests_and_code_alike(command):
    """REFACTOR is mandatory and improves both (Phase 12): nothing is refused there."""
    assert check_bash_tdd_phase(command, REFACTOR) == (True, "")


@pytest.mark.parametrize("ledger", [GREEN, REFACTOR])
def test_the_implementing_phases_allow_writing_production_code(ledger):
    assert allowed("echo 'def add(a,b): return a+b' > calculator.py", ledger) is True


def test_green_also_refuses_an_unverifiable_target():
    permitted, reason = check_bash_tdd_phase("echo x > $TARGET", GREEN)
    assert permitted is False
    assert "cannot verify where this command writes" in reason


# ── through the gate, which is where it actually matters ────────────────────

def test_the_permission_gate_routes_bash_through_the_phase_check():
    from app.tdd.phase_permissions import check_tdd_phase_permission
    from app.tool import build_tool

    async def noop(args, ctx):  # pragma: no cover - never called
        raise AssertionError

    bash = build_tool(name="Bash", prompt="p", call=noop)
    permitted, reason = check_tdd_phase_permission(bash, {"command": "echo x > calculator.py"}, RED)
    assert permitted is False and "RED denies writing to production file" in reason

    assert check_tdd_phase_permission(bash, {"command": "pytest -q"}, RED) == (True, "")


def test_run_tests_is_never_denied_even_though_it_is_not_bash():
    from app.tdd.phase_permissions import check_tdd_phase_permission
    from app.tool import build_tool

    async def noop(args, ctx):  # pragma: no cover - never called
        raise AssertionError

    run_tests = build_tool(name="RunTests", prompt="p", call=noop)
    assert check_tdd_phase_permission(run_tests, {"test_path": "tests"}, RED) == (True, "")


# ── the exact refusals, because the message is the model's only feedback ────

RED_PRODUCTION = (
    "TDD phase RED denies writing to production file 'calculator.py' through a shell "
    "redirection. Only test files may be written before this sub-requirement's tests are "
    "written and run."
)
RED_UNVERIFIABLE = (
    "TDD phase RED cannot verify where this command writes: its redirection target uses "
    "shell expansion or could not be parsed. Write the test file with WriteFile instead."
)
GREEN_TEST_FILE = (
    "TDD phase GREEN denies modifying test file 'tests/test_calc.py' through a shell "
    "redirection. The tests are what pins the behaviour being implemented; they can be "
    "improved in REFACTOR. If a test is wrong (it contradicts the specification), go back to RED: "
    "spawn the tester agent (Agent with subagent_type 'tester') to fix it. That restarts this "
    "sub-requirement at RED; then implement again."
)
GREEN_UNVERIFIABLE = (
    "TDD phase GREEN cannot verify where this command writes: its redirection target uses "
    "shell expansion or could not be parsed."
)


def test_the_red_production_refusal_reads_exactly_this():
    assert check_bash_tdd_phase("echo x > calculator.py", RED) == (False, RED_PRODUCTION)


def test_the_red_unverifiable_refusal_reads_exactly_this():
    assert check_bash_tdd_phase("echo x > $OUT", RED) == (False, RED_UNVERIFIABLE)


def test_the_green_test_file_refusal_reads_exactly_this():
    assert check_bash_tdd_phase("echo x > tests/test_calc.py", GREEN) == (False, GREEN_TEST_FILE)


def test_the_green_unverifiable_refusal_reads_exactly_this():
    assert check_bash_tdd_phase("echo x > $OUT", GREEN) == (False, GREEN_UNVERIFIABLE)


def test_a_permitted_command_returns_an_empty_reason():
    assert check_bash_tdd_phase("pytest -q", RED) == (True, "")
    assert check_bash_tdd_phase("echo x > tests/test_a.py", RED) == (True, "")
