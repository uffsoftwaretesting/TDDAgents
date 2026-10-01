"""
Security and Adversarial Test Suite across Parts D, E, F, G, and H.

Covers:
1. Path traversal attacks and workspace containment (Part G).
2. Symlink escape defenses and dangerous path protections (Part G, Part C5).
3. Command injection, shell redirection, and capability evasion (Part C4).
4. Hook payload safety, header injection defense, and env var leakage (Part H).
5. TDD phase ledger bypass defenses and generic writer path filtering (Part D).
6. Agent pool privilege isolation and run-scoped memory containment (Part I).
"""

from __future__ import annotations

from pathlib import Path
import pytest

from app.hooks.backends import interpolate_arguments, interpolate_headers
from app.hooks.schemas import CommandHook
from app.loop.agents.definition import AgentDefinition
from app.loop.agents.memory import AgentMemoryStore
from app.loop.agents.resolution import resolve_agent_tools
from app.loop.ledger import PhaseLedger, TddPhase
from app.loop.permissions.capability import (
    is_bash_command_read_only,
    split_shell_commands,
)
from app.loop.permissions.filesystem import is_dangerous_path, is_path_allowed
from app.loop.permissions.tdd import (
    check_tdd_phase_permission,
    get_phase_deny_rules,
)
from app.loop.permissions.types import PermissionBehavior, ToolPermissionContext
from app.loop.tools.base import ToolResult, build_tool
from app.workspace.base import (
    WorkspacePathError,
    normalize_path,
)
from app.workspace.local import LocalWorkspace


def _dummy_call(input_dict: dict[str, object], context: object) -> ToolResult:
    return ToolResult(content="ok")


# ============================================================================
# 1. Path Traversal & Normalization Attacks (Part G)
# ============================================================================

def test_normalize_path_traversal_attempts_raise_workspace_path_error() -> None:
    malicious_paths = [
        "../etc/passwd",
        "../../shadow",
        "foo/../../bar",
        "a/b/c/../../../../root",
        "..",
        "../",
        "/../etc",
        "/../../../../var/log",
    ]
    for p in malicious_paths:
        with pytest.raises(WorkspacePathError) as exc_info:
            normalize_path(p)
        assert "escapes the workspace root" in str(exc_info.value) or "must not be empty" in str(exc_info.value)


def test_normalize_path_empty_or_none_raises_workspace_path_error() -> None:
    with pytest.raises(WorkspacePathError, match="Path must not be None"):
        normalize_path(None)  # type: ignore[arg-type]

    with pytest.raises(WorkspacePathError, match="Path must not be empty"):
        normalize_path("")

    with pytest.raises(WorkspacePathError, match="Path must not be empty"):
        normalize_path("   ")


def test_local_workspace_traversal_attacks_blocked(tmp_path: Path) -> None:
    ws = LocalWorkspace(root=tmp_path)

    # Attempting to read outside workspace
    with pytest.raises(WorkspacePathError):
        ws.read_file("../escape.txt")

    with pytest.raises(WorkspacePathError):
        ws.read_file("../../etc/passwd")

    # Attempting to write outside workspace
    with pytest.raises(WorkspacePathError):
        ws.write_file("../escape.txt", "hacked")

    # Attempting to delete outside workspace
    with pytest.raises(WorkspacePathError):
        ws.delete_file("../escape.txt")

    # Attempting to check existence outside workspace returns False safely
    assert ws.exists("../escape.txt") is False

    # Attempting to move outside workspace
    with pytest.raises(WorkspacePathError):
        ws.move("local.txt", "../outside.txt")


# ============================================================================
# 2. Symlink Containment & Sensitive Files Defense (Part G & C5)
# ============================================================================

def test_local_workspace_symlink_escape_blocked(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()

    secret_target = outside_dir / "secret.env"
    secret_target.write_text("SUPER_SECRET=12345", encoding="utf-8")

    # Create a symlink inside workspace pointing outside
    escape_link = workspace_dir / "symlink_to_secret.env"
    try:
        escape_link.symlink_to(secret_target)
    except OSError:
        pytest.skip("Symlink creation not supported in this environment")

    ws = LocalWorkspace(root=workspace_dir)

    # Reading through symlink pointing outside workspace root must raise WorkspacePathError
    with pytest.raises(WorkspacePathError):
        ws.read_file("symlink_to_secret.env")

    # Writing through symlink pointing outside workspace root must raise WorkspacePathError
    with pytest.raises(WorkspacePathError):
        ws.write_file("symlink_to_secret.env", "TAMPERED")


def test_dangerous_files_and_directories_blocked(tmp_path: Path) -> None:
    dangerous_targets = [
        ".git/config",
        ".git/HEAD",
        ".git/hooks/pre-commit",
        ".bashrc",
        ".bash_profile",
        ".zshrc",
        ".profile",
        ".claude.json",
        ".vscode/settings.json",
    ]
    for target in dangerous_targets:
        is_dang, reason = is_dangerous_path(target)
        assert is_dang is True
        assert "restricted" in reason

    ctx = ToolPermissionContext()
    for target in dangerous_targets:
        target_path = tmp_path / target
        res = is_path_allowed(target_path, ctx, working_dir=tmp_path, operation="write")
        assert res.behavior in (PermissionBehavior.DENY, PermissionBehavior.ASK)
        assert "restricted" in res.message.lower() or "denied" in res.message.lower()


# ============================================================================
# 3. Shell Injection & Capability Evasion (Part C4)
# ============================================================================

def test_bash_redirection_and_injection_attacks_not_read_only() -> None:
    attacks = [
        "cat file.txt > /tmp/pwned",
        "ls -la >> /tmp/pwned",
        "grep foo file.txt | tee /tmp/output",
        "ls; rm -rf /tmp/test",
        "echo $(rm -rf /)",
        "echo `rm -rf /`",
        "cat <(cat /etc/passwd)",
        "sed -i 's/foo/bar/g' test.py",
        "sed --in-place 's/foo/bar/g' test.py",
        "python -c 'import os; os.system(\"rm -rf *\")'",
        "pip install malicious-pkg",
        "npm install",
        "git commit -m 'bypass'",
        "git push origin main",
        "git checkout -b new-branch",
        "touch newfile.py",
        "mv file1 file2",
        "cp file1 file2",
    ]
    for cmd in attacks:
        assert is_bash_command_read_only(cmd) is False, f"Expected unsafe command to fail read-only check: {cmd}"


def test_bash_legitimate_read_only_commands_pass() -> None:
    safe_commands = [
        "ls -la",
        "cat app/main.py",
        "head -n 20 setup.cfg",
        "tail -f log.txt",
        "grep -rn 'def ' app/",
        "rg 'class ' .",
        "git status",
        "git diff",
        "git log -n 5",
        "git branch",
        "python --version",
        "python3 -V",
        "pwd",
        "which pytest",
        "diff -u file1 file2",
    ]
    for cmd in safe_commands:
        assert is_bash_command_read_only(cmd) is True, f"Expected safe command to pass read-only check: {cmd}"


def test_split_shell_commands_handles_quotes_and_escapes() -> None:
    chained = 'echo "hello; world" && ls -l | grep "foo && bar"'
    parts = split_shell_commands(chained)
    assert len(parts) >= 2


# ============================================================================
# 4. Hook Safety & Environment Variable Leakage (Part H)
# ============================================================================

def test_interpolate_arguments_preserves_valid_json() -> None:
    template = "Arguments evaluated: $ARGUMENTS"
    payload = {
        "tool_name": "Bash",
        "command": "rm -rf /; echo 'owned'",
        "nested": {"param": 'quote"test', "list": [1, 2, 3]},
    }
    interpolated = interpolate_arguments(template, payload)
    assert '"rm -rf /; echo \'owned\'"' in interpolated
    assert '"param": "quote\\"test"' in interpolated


def test_interpolate_headers_prevents_unauthorized_env_leakage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAFE_TOKEN", "token_safe_123")
    monkeypatch.setenv("SECRET_OPENAI_KEY", "sk-secret-do-not-leak")
    monkeypatch.setenv("SYSTEM_ROOT_PASSWORD", "super_secret_admin")

    raw_headers = {
        "Authorization": "Bearer $SAFE_TOKEN",
        "X-Secret": "$SECRET_OPENAI_KEY",
        "X-Admin": "${SYSTEM_ROOT_PASSWORD}",
    }

    # Only SAFE_TOKEN is allowed in allowed_env_vars
    resolved = interpolate_headers(raw_headers, allowed_env_vars=("SAFE_TOKEN",))

    assert resolved["Authorization"] == "Bearer token_safe_123"
    # Unallowed env vars must be blanked out, never resolved
    assert resolved["X-Secret"] == ""
    assert resolved["X-Admin"] == ""


def test_command_hook_backend_fail_closed_on_syntax_and_missing_binary() -> None:
    from app.hooks.backends import CommandHookBackend

    backend = CommandHookBackend()
    hook = CommandHook(command="non_existent_binary_xyz_123456", shell="bash")
    outcome = backend.execute(hook, {}, "PreToolUse")
    # Subprocess execution failure should be caught, not crashed
    assert outcome.exit_code != 0
    assert "not found" in outcome.stderr.lower() or outcome.exit_code == 127


# ============================================================================
# 5. TDD Phase Ledger Invariants & Tool Resolution Isolation (Part D & I)
# ============================================================================

def test_tdd_phase_rule_strictly_denies_implementation_writers_in_red() -> None:
    # In RED phase:
    ledger_red = PhaseLedger(phase=TddPhase.RED, red_confirmed=False)

    tool_write_impl = build_tool(name="WriteImplementation", prompt="", call=_dummy_call)
    tool_edit_impl = build_tool(name="EditCode", prompt="", call=_dummy_call)
    tool_write_test = build_tool(name="WriteTest", prompt="", call=_dummy_call)
    tool_run_tests = build_tool(name="RunTests", prompt="", call=_dummy_call)

    # Assert implementation writers are blocked in RED
    ok1, reason1 = check_tdd_phase_permission(tool_write_impl, {}, ledger_red)
    assert ok1 is False
    assert "denies implementation-writing" in reason1

    ok2, reason2 = check_tdd_phase_permission(tool_edit_impl, {}, ledger_red)
    assert ok2 is False

    # Test writers and RunTests must be permitted in RED
    ok_test, _ = check_tdd_phase_permission(tool_write_test, {}, ledger_red)
    assert ok_test is True

    ok_run, _ = check_tdd_phase_permission(tool_run_tests, {}, ledger_red)
    assert ok_run is True

    deny_rules = get_phase_deny_rules(ledger_red)
    assert any(r.tool_name == "WriteImplementation" for r in deny_rules)


def test_tdd_phase_rule_generic_writer_target_path_classification() -> None:
    ledger_red = PhaseLedger(phase=TddPhase.RED, red_confirmed=False)
    tool_generic_write = build_tool(name="WriteFile", prompt="", call=_dummy_call)

    # Writing to a test file in RED is allowed
    ok_test, _ = check_tdd_phase_permission(tool_generic_write, {"path": "tests/test_calculator.py"}, ledger_red)
    assert ok_test is True

    # Writing to a production code file in RED is DENIED
    ok_impl, reason_impl = check_tdd_phase_permission(tool_generic_write, {"path": "app/calculator.py"}, ledger_red)
    assert ok_impl is False
    assert "denies writing to production file" in reason_impl


def test_agent_tool_resolution_strips_disallowed_and_enforces_phase_in_subagent() -> None:
    from app.loop.tools.pool import assemble_tool_pool

    t_impl = build_tool(name="WriteImplementation", prompt="", call=_dummy_call)
    t_read = build_tool(name="ReadFile", prompt="", call=_dummy_call)
    t_agent = build_tool(name="Agent", prompt="", call=_dummy_call)

    # Subagent declared in RED phase requesting all tools ("*")
    agent_red = AgentDefinition(
        name="tester_sub",
        description="",
        prompt="",
        phase=TddPhase.RED,
        tools=("*",),
    )

    resolved = resolve_agent_tools(
        agent_red,
        available_tools=[t_impl, t_read, t_agent],
        is_main_thread=False,
    )

    worker_pool = assemble_tool_pool(
        resolved.resolved_tools,
        phase_ledger=PhaseLedger(phase=TddPhase.RED),
    )

    pool_names = [t.name for t in worker_pool]
    # In RED phase, WriteImplementation MUST be excluded from worker pool
    assert "WriteImplementation" not in pool_names
    # Agent tool is forbidden for subagents by default
    assert "Agent" not in pool_names
    # ReadFile must remain available
    assert "ReadFile" in pool_names


# ============================================================================
# 6. Run-Scoped Memory Isolation (Part I & E)
# ============================================================================

def test_agent_memory_store_strict_run_isolation(tmp_path: Path) -> None:
    store = AgentMemoryStore(base_dir=tmp_path)

    run_1 = "run_11111111"
    run_2 = "run_22222222"

    entry_1 = store.get_entrypoint("developer", "run", run_id=run_1)
    entry_2 = store.get_entrypoint("developer", "run", run_id=run_2)

    assert entry_1 is not None and entry_2 is not None
    entry_1.parent.mkdir(parents=True, exist_ok=True)
    entry_2.parent.mkdir(parents=True, exist_ok=True)

    entry_1.write_text("State from run 1", encoding="utf-8")
    entry_2.write_text("State from run 2", encoding="utf-8")

    # Run 1 cannot see Run 2's memory
    prompt_1 = store.load_memory_prompt("developer", "run", run_id=run_1)
    assert "State from run 1" in prompt_1
    assert "State from run 2" not in prompt_1

    # Discarding Run 1 memory cleans only Run 1
    store.discard_run_memory(run_1)

    prompt_1_after = store.load_memory_prompt("developer", "run", run_id=run_1)
    assert "State from run 1" not in prompt_1_after

    # Run 2 memory remains intact
    prompt_2 = store.load_memory_prompt("developer", "run", run_id=run_2)
    assert "State from run 2" in prompt_2
