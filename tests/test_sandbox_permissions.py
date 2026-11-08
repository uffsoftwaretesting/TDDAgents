"""
The sandbox in the permission checks: Bash's `checkSandboxAutoAllow` (deny and ask rules still win, on the
command and on each subcommand), step 1b's exception for a tool-wide Bash ask rule, the sandboxed-git
gate of `checkReadOnlyConstraints`, and the TDD phase tier deciding before all of it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from app.state.app_state_store import AppStateStore
from app.tool import tool_context_for
from app.tools.bash_tool.bash_permissions import (
    AUTO_ALLOWED_WITH_SANDBOX,
    bash_tool_has_permission,
    bash_tool_has_permission_in_sandbox,
    check_sandbox_auto_allow,
)
from app.tools.bash_tool.bash_tool import build_bash_tool
from app.tools.bash_tool.read_only_validation import (
    SANDBOXED_OUTSIDE_ORIGINAL_CWD,
    check_read_only_constraints,
)
from app.types.permissions import (
    PermissionBehavior,
    PermissionMode,
    PermissionRule,
    PermissionRuleSource,
    ToolPermissionContext,
)
from app.utils.permissions.permissions import can_sandbox_auto_allow, has_permissions_to_use_tool


@dataclass
class Sandbox:
    enabled: bool = True
    auto_allow: bool = True
    excluded: list[str] = field(default_factory=list)

    def is_sandboxing_enabled(self) -> bool:
        return self.enabled

    def is_auto_allow_bash_if_sandboxed_enabled(self) -> bool:
        return self.auto_allow

    def are_unsandboxed_commands_allowed(self) -> bool:
        return True

    def get_excluded_commands(self) -> list[str]:
        return self.excluded


def rule(content: str | None, behavior: PermissionBehavior) -> PermissionRule:
    return PermissionRule(tool_name="Bash", rule_behavior=behavior, rule_content=content,
                          source=PermissionRuleSource.PROJECT_SETTINGS)


def perm(*, deny: tuple[str | None, ...] = (), ask: tuple[str | None, ...] = (),
         mode: PermissionMode = PermissionMode.DEFAULT) -> ToolPermissionContext:
    return ToolPermissionContext(mode=mode,
                                 always_deny_rules=tuple(rule(c, PermissionBehavior.DENY) for c in deny),
                                 always_ask_rules=tuple(rule(c, PermissionBehavior.ASK) for c in ask))


# ── checkSandboxAutoAllow ──────────────────────────────────────────────────────


def test_no_rule_allows_with_the_sandbox() -> None:
    result = check_sandbox_auto_allow("pip install x", perm())
    assert result.behavior == PermissionBehavior.ALLOW and result.updated_input == {"command": "pip install x"}
    assert result.decision_reason == {"type": "other", "reason": AUTO_ALLOWED_WITH_SANDBOX}
    assert AUTO_ALLOWED_WITH_SANDBOX == "Auto-allowed with sandbox (autoAllowBashIfSandboxed enabled)"


def test_a_deny_rule_on_the_command_denies() -> None:
    result = check_sandbox_auto_allow("  rm -rf build ", perm(deny=("rm:*",)))
    assert result.behavior == PermissionBehavior.DENY
    assert result.message == "Permission to use Bash with command rm -rf build has been denied."


def test_a_deny_rule_on_a_subcommand_wins_over_an_ask_on_the_whole() -> None:
    result = check_sandbox_auto_allow("echo hi && rm -rf /", perm(deny=("rm:*",), ask=("echo hi && rm -rf /",)))
    assert result.behavior == PermissionBehavior.DENY
    assert result.decision_reason["rule"].rule_content == "rm:*"  # type: ignore[index]


def test_ask_rules_ask_on_a_subcommand_then_on_the_command() -> None:
    sub = check_sandbox_auto_allow("ls && curl x", perm(ask=("curl:*",)))
    reason: Any = sub.decision_reason
    assert sub.behavior == PermissionBehavior.ASK and reason["rule"].rule_content == "curl:*"
    whole = check_sandbox_auto_allow("curl x", perm(ask=("curl:*",)))
    assert whole.behavior == PermissionBehavior.ASK


def test_the_auto_allow_runs_only_for_a_sandboxed_command(tmp_path: Path) -> None:
    cwd = str(tmp_path)
    command = {"command": "pip install x"}
    assert bash_tool_has_permission_in_sandbox(command, perm(), cwd, sandbox=Sandbox(), original_cwd=cwd).behavior == (
        PermissionBehavior.ALLOW)
    for sandbox in (None, Sandbox(enabled=False), Sandbox(auto_allow=False), Sandbox(excluded=["pip:*"])):
        result = bash_tool_has_permission_in_sandbox(command, perm(), cwd, sandbox=sandbox, original_cwd=cwd)
        assert result.behavior == PermissionBehavior.PASSTHROUGH, sandbox
    override = {"command": "pip install x", "dangerouslyDisableSandbox": True}
    assert bash_tool_has_permission_in_sandbox(override, perm(), cwd, sandbox=Sandbox(), original_cwd=cwd).behavior == (
        PermissionBehavior.PASSTHROUGH)
    # Malformed syntax asks before the auto-allow, as upstream's order.
    assert bash_tool_has_permission_in_sandbox({"command": "echo ${}"}, perm(), cwd, sandbox=Sandbox(),
                                               original_cwd=cwd).behavior == PermissionBehavior.ASK
    # Outside the call, nothing carries the sandbox.
    assert bash_tool_has_permission("pip install x", perm(), cwd).behavior == PermissionBehavior.PASSTHROUGH


def test_a_piped_command_carries_the_override_into_each_segment(tmp_path: Path) -> None:
    cwd = str(tmp_path)
    override = {"command": "pip list | grep x", "dangerouslyDisableSandbox": True}
    result = bash_tool_has_permission_in_sandbox(override, perm(), cwd, sandbox=Sandbox(), original_cwd=cwd)
    assert result.behavior != PermissionBehavior.ALLOW or result.decision_reason != {
        "type": "other", "reason": AUTO_ALLOWED_WITH_SANDBOX}


# ── the sandboxed-git gate ─────────────────────────────────────────────────────


def test_git_outside_the_original_directory_is_not_read_only_while_sandboxed(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    assert check_read_only_constraints("git status", False, str(tmp_path)).behavior == PermissionBehavior.ALLOW
    token = SANDBOXED_OUTSIDE_ORIGINAL_CWD.set(True)
    try:
        gated = check_read_only_constraints("git status", False, str(sub))
        assert gated.behavior == PermissionBehavior.PASSTHROUGH
        assert gated.message == ("Git commands outside the original working directory require permission checks "
                                 "when sandbox is enabled")
        assert check_read_only_constraints("ls", False, str(sub)).behavior == PermissionBehavior.ALLOW
    finally:
        SANDBOXED_OUTSIDE_ORIGINAL_CWD.reset(token)


def test_the_gate_is_set_only_for_a_sandboxed_check_outside_the_original_cwd(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    no_auto = Sandbox(auto_allow=False)
    moved = bash_tool_has_permission_in_sandbox({"command": "git status"}, perm(), str(sub), sandbox=no_auto,
                                                original_cwd=str(tmp_path))
    assert moved.behavior == PermissionBehavior.PASSTHROUGH
    home = bash_tool_has_permission_in_sandbox({"command": "git status"}, perm(), str(tmp_path), sandbox=no_auto,
                                               original_cwd=str(tmp_path))
    assert home.behavior == PermissionBehavior.ALLOW
    unsandboxed = bash_tool_has_permission_in_sandbox({"command": "git status"}, perm(), str(sub),
                                                      sandbox=Sandbox(enabled=False), original_cwd=str(tmp_path))
    assert unsandboxed.behavior == PermissionBehavior.ALLOW
    assert SANDBOXED_OUTSIDE_ORIGINAL_CWD.get() is False


# ── step 1b and the whole gate ─────────────────────────────────────────────────


def ctx_for(permission_context: ToolPermissionContext, sandbox: Any, workspace: Any = None) -> Any:
    ctx = tool_context_for(AppStateStore(), permission_context=permission_context, workspace=workspace)
    ctx.sandbox_manager = sandbox
    return ctx


def test_can_sandbox_auto_allow() -> None:
    bash, other = build_bash_tool(), type("T", (), {"name": "WebFetch"})()
    assert can_sandbox_auto_allow(bash, {"command": "ls"}, ctx_for(perm(), Sandbox())) is True
    assert can_sandbox_auto_allow(other, {"command": "ls"}, ctx_for(perm(), Sandbox())) is False
    assert can_sandbox_auto_allow(bash, {"command": "ls"}, ctx_for(perm(), None)) is False
    assert can_sandbox_auto_allow(bash, {"command": "ls"}, ctx_for(perm(), Sandbox(auto_allow=False))) is False
    assert can_sandbox_auto_allow(bash, {"command": "ls"}, ctx_for(perm(), Sandbox(enabled=False))) is False
    assert can_sandbox_auto_allow(bash, {"command": "docker ps"}, ctx_for(perm(), Sandbox(excluded=["docker:*"]))) is (
        False)


def test_a_tool_wide_ask_rule_gives_way_to_a_sandboxed_command(tmp_path: Path) -> None:
    from app.workspace.local import LocalWorkspace

    ws = LocalWorkspace(tmp_path)
    tool = build_bash_tool()
    ask_all = perm(ask=(None,))
    sandboxed = asyncio.run(has_permissions_to_use_tool(tool, {"command": "pip install x"},
                                                        ctx_for(ask_all, Sandbox(), ws)))
    assert sandboxed.behavior == PermissionBehavior.ALLOW
    assert sandboxed.decision_reason == {"type": "other", "reason": AUTO_ALLOWED_WITH_SANDBOX}
    excluded = asyncio.run(has_permissions_to_use_tool(tool, {"command": "pip install x"},
                                                       ctx_for(ask_all, Sandbox(excluded=["pip:*"]), ws)))
    reason: Any = excluded.decision_reason
    assert excluded.behavior == PermissionBehavior.ASK and reason["type"] == "rule"


def test_the_rule_check_used_for_hooks_respects_step_1b(tmp_path: Path) -> None:
    from app.utils.permissions.permissions import check_rule_based_permissions
    from app.workspace.local import LocalWorkspace

    ws = LocalWorkspace(tmp_path)
    tool = build_bash_tool()
    sandboxed = ctx_for(perm(ask=(None,)), Sandbox(), ws)
    assert asyncio.run(check_rule_based_permissions(tool, {"command": "ls"}, sandboxed)) is None
    asked = asyncio.run(check_rule_based_permissions(tool, {"command": "ls"}, ctx_for(perm(ask=(None,)), None, ws)))
    assert asked is not None and asked.behavior == PermissionBehavior.ASK


def test_the_tdd_tier_still_decides_first(tmp_path: Path) -> None:
    """In RED, a Bash redirect into production code is denied even though the sandbox would allow it."""
    from dataclasses import replace

    from app.tdd.ledger import PhaseLedger, TddPhase
    from app.workspace.local import LocalWorkspace
    from tests.plan_helpers import CURRENT_ITEM_PLAN, TEST_PROJECT_CONFIG

    ws = LocalWorkspace(tmp_path)
    store = AppStateStore()
    store.update(lambda s: replace(s, tdd_plan=CURRENT_ITEM_PLAN, project_config=TEST_PROJECT_CONFIG,
                                   phase_ledger=PhaseLedger(phase=TddPhase.RED)))
    ctx = tool_context_for(store, permission_context=perm(mode=PermissionMode.ACCEPT_EDITS), workspace=ws)
    ctx.sandbox_manager = Sandbox()
    result = asyncio.run(has_permissions_to_use_tool(build_bash_tool(), {"command": "echo x > calc.py"}, ctx))
    assert result.behavior == PermissionBehavior.DENY


@pytest.mark.parametrize("command", ["ls", "pip install x"])
def test_a_headless_run_without_a_sandbox_still_asks_the_same(command: str, tmp_path: Path) -> None:
    from app.workspace.local import LocalWorkspace

    ws = LocalWorkspace(tmp_path)
    result = asyncio.run(has_permissions_to_use_tool(build_bash_tool(), {"command": command},
                                                     ctx_for(perm(ask=(None,)), None, ws)))
    assert result.behavior == PermissionBehavior.ASK
