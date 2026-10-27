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

import asyncio
import json
from pathlib import Path

import pytest

from app.schemas.hooks import CommandHook
from app.tdd.ledger import PhaseLedger, TddPhase
from app.tdd.phase_permissions import (
    check_tdd_phase_permission,
    get_phase_deny_rules,
)
from app.tool import ToolResult, build_tool
from app.tools.agent_tool.agent_memory import AgentMemoryStore
from app.tools.agent_tool.agent_tool_utils import resolve_agent_tools
from app.tools.agent_tool.load_agents_dir import AgentDefinition
from app.types.permissions import (
    PermissionBehavior,
    PermissionMode,
    ToolPermissionContext,
)
from app.utils.hooks.exec_http_hook import interpolate_env_vars
from app.utils.hooks.exec_prompt_hook import add_arguments_to_prompt
from app.utils.hooks.hooks import exec_command_hook
from app.utils.permissions.filesystem import (
    check_write_permission_for_tool,
    is_dangerous_file_path_to_auto_edit,
)
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
        assert is_dangerous_file_path_to_auto_edit(target) is True

    # Bypass mode included: the safety check is an 'ask' the gate never bypasses.
    for mode in (PermissionMode.DEFAULT, PermissionMode.ACCEPT_EDITS, PermissionMode.BYPASS_PERMISSIONS):
        ctx = ToolPermissionContext(mode=mode)
        for target in dangerous_targets:
            target_path = str(tmp_path / target)
            res = check_write_permission_for_tool(target_path, {}, ctx, str(tmp_path))
            assert res.behavior == PermissionBehavior.ASK
            assert res.decision_reason == {"type": "safetyCheck", "reason": res.message, "classifierApprovable": True}
            assert "sensitive file" in res.message


# ============================================================================
# 3. Shell Injection & Capability Evasion (Part C4)
# ============================================================================


# ============================================================================
# 4. Hook Safety & Environment Variable Leakage (Part H)
# ============================================================================


def test_prompt_hook_arguments_preserve_valid_json() -> None:
    payload = {
        "tool_name": "Bash",
        "command": "rm -rf /; echo 'owned'",
        "nested": {"param": 'quote"test', "list": [1, 2, 3]},
    }
    interpolated = add_arguments_to_prompt("Arguments evaluated: $ARGUMENTS", json.dumps(payload))
    assert interpolated.startswith("Arguments evaluated: ")
    assert json.loads(interpolated.removeprefix("Arguments evaluated: ")) == payload


def test_http_hook_headers_never_resolve_unallowed_env_vars() -> None:
    env = {
        "SAFE_TOKEN": "token_safe_123",
        "SECRET_OPENAI_KEY": "sk-secret-do-not-leak",
        "SYSTEM_ROOT_PASSWORD": "super_secret_admin",
    }
    allowed = frozenset({"SAFE_TOKEN"})
    assert interpolate_env_vars("Bearer $SAFE_TOKEN", allowed, env) == "Bearer token_safe_123"
    # Unallowed env vars must be blanked out, never resolved
    assert interpolate_env_vars("$SECRET_OPENAI_KEY", allowed, env) == ""
    assert interpolate_env_vars("${SYSTEM_ROOT_PASSWORD}", allowed, env) == ""


def test_command_hook_with_a_missing_binary_fails_without_crashing(tmp_path: Path) -> None:
    hook = CommandHook(command="non_existent_binary_xyz_123456")
    result = asyncio.run(
        exec_command_hook(
            hook, "PreToolUse", "PreToolUse:Bash", "{}", None, cwd=str(tmp_path), project_dir=str(tmp_path)
        )
    )
    # The shell reports the missing command; the hook fails, the caller does not.
    assert result.status == 127
    assert "not found" in result.stderr.lower()


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
    from app.tools.tools import assemble_tool_pool

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
