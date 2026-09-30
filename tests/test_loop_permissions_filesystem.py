"""
Tests for workspace boundary, path validation, and dangerous file guards (Part C5).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.loop.permissions.filesystem import (
    DANGEROUS_DIRECTORIES,
    DANGEROUS_FILES,
    is_dangerous_path,
    is_path_allowed,
    is_path_in_allowed_working_dirs,
    is_path_in_working_dir,
    normalize_case,
)
from app.loop.permissions.types import (
    PermissionBehavior,
    PermissionMode,
    PermissionRule,
    ToolPermissionContext,
)


class TestDangerousPaths:
    def test_dangerous_files_detected(self):
        assert is_dangerous_path(".bashrc")[0] is True
        assert is_dangerous_path("/home/user/.gitconfig")[0] is True
        assert is_dangerous_path("project/.mcp.json")[0] is True
        assert is_dangerous_path("sub/.claude.json")[0] is True

    def test_dangerous_directories_detected(self):
        assert is_dangerous_path(".git/config")[0] is True
        assert is_dangerous_path("project/.vscode/settings.json")[0] is True
        assert is_dangerous_path(".idea/workspace.xml")[0] is True
        assert is_dangerous_path(".claude/commands/run.sh")[0] is True

    def test_dangerous_paths_case_insensitive(self):
        assert is_dangerous_path(".BASHRC")[0] is True
        assert is_dangerous_path(".Git/HEAD")[0] is True
        assert is_dangerous_path(".cLauDe/config")[0] is True

    def test_regular_project_files_are_not_dangerous(self):
        assert is_dangerous_path("src/main.py")[0] is False
        assert is_dangerous_path("tests/test_app.py")[0] is False
        assert is_dangerous_path("docs/README.md")[0] is False

    @pytest.mark.parametrize("fname", DANGEROUS_FILES)
    def test_all_dangerous_files_constants(self, fname):
        assert is_dangerous_path(fname)[0] is True
        assert is_dangerous_path(f"/path/to/{fname}")[0] is True

    @pytest.mark.parametrize("dname", DANGEROUS_DIRECTORIES)
    def test_all_dangerous_directories_constants(self, dname):
        assert is_dangerous_path(f"{dname}/file.txt")[0] is True
        assert is_dangerous_path(f"/work/{dname}/sub/file.txt")[0] is True

    def test_normalize_case(self):
        assert normalize_case("Foo/BAR") == "foo/bar"


class TestWorkspaceBoundaryContainment:
    def test_path_inside_working_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            inside = work_dir / "src" / "code.py"
            assert is_path_in_working_dir(inside, work_dir) is True

    def test_path_traversal_outside_working_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir) / "workspace"
            work_dir.mkdir()
            outside_traversal = work_dir / ".." / "secret.txt"
            assert is_path_in_working_dir(outside_traversal, work_dir) is False

    def test_additional_working_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir1, tempfile.TemporaryDirectory() as tmpdir2:
            w1 = Path(tmpdir1)
            w2 = Path(tmpdir2)

            file_in_w1 = w1 / "file.txt"
            file_in_w2 = w2 / "file.txt"
            outside = Path("/etc/passwd")

            assert is_path_in_allowed_working_dirs(file_in_w1, w1, (str(w2),)) is True
            assert is_path_in_allowed_working_dirs(file_in_w2, w1, (str(w2),)) is True
            assert is_path_in_allowed_working_dirs(outside, w1, (str(w2),)) is False


class TestIsPathAllowed:
    def test_path_denied_by_deny_rule(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            target = work_dir / "restricted.txt"
            rule = PermissionRule(tool_name="Edit", rule_behavior=PermissionBehavior.DENY, rule_content=str(target))
            ctx = ToolPermissionContext(mode=PermissionMode.BYPASS_PERMISSIONS, always_deny_rules=(rule,))

            res = is_path_allowed(target, ctx, work_dir, operation="write")
            assert res.behavior == PermissionBehavior.DENY
            assert res.message == f"Access to path '{target}' denied by rule."
            assert res.decision_reason == {"type": "rule", "rule": rule}

    def test_path_outside_workspace_denied(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir) / "ws"
            work_dir.mkdir()
            outside = Path(tmpdir) / "outside.txt"

            ctx = ToolPermissionContext(mode=PermissionMode.BYPASS_PERMISSIONS)
            res = is_path_allowed(outside, ctx, work_dir, operation="write")
            assert res.behavior == PermissionBehavior.DENY
            assert res.message == f"Path '{outside}' is outside the allowed workspace boundary."
            assert res.decision_reason == {"type": "other", "reason": "outside_workspace"}

    def test_dangerous_path_write_returns_safety_check_ask(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            sensitive = work_dir / ".git" / "config"

            # Even in bypass permissions mode, safety checks require asking!
            ctx = ToolPermissionContext(mode=PermissionMode.BYPASS_PERMISSIONS)
            res = is_path_allowed(sensitive, ctx, work_dir, operation="write")
            assert res.behavior == PermissionBehavior.ASK
            expected_reason = {
                "type": "safetyCheck",
                "reason": "Modifying files inside sensitive directory '.git' is restricted.",
            }
            assert res.decision_reason == expected_reason
            assert res.message == "Modifying files inside sensitive directory '.git' is restricted."

    def test_read_operation_inside_workspace_allowed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            target = work_dir / "file.txt"
            ctx = ToolPermissionContext(mode=PermissionMode.DEFAULT)

            res = is_path_allowed(target, ctx, work_dir, operation="read")
            assert res.behavior == PermissionBehavior.ALLOW
            assert res.decision_reason == {"type": "mode", "mode": PermissionMode.DEFAULT}

    def test_write_operation_modes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            target = work_dir / "file.txt"

            # In acceptEdits mode -> allowed
            ctx_accept = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS)
            res_accept = is_path_allowed(target, ctx_accept, work_dir, operation="write")
            assert res_accept.behavior == PermissionBehavior.ALLOW
            assert res_accept.decision_reason == {"type": "mode", "mode": PermissionMode.ACCEPT_EDITS}

            # In bypassPermissions mode -> allowed
            ctx_bypass = ToolPermissionContext(mode=PermissionMode.BYPASS_PERMISSIONS)
            res_bypass = is_path_allowed(target, ctx_bypass, work_dir, operation="write")
            assert res_bypass.behavior == PermissionBehavior.ALLOW
            assert res_bypass.decision_reason == {"type": "mode", "mode": PermissionMode.BYPASS_PERMISSIONS}

            # In default mode -> ask
            ctx_default = ToolPermissionContext(mode=PermissionMode.DEFAULT)
            res_default = is_path_allowed(target, ctx_default, work_dir, operation="write")
            assert res_default.behavior == PermissionBehavior.ASK
            assert res_default.decision_reason == {"type": "mode", "mode": PermissionMode.DEFAULT}
            assert res_default.message == f"Writing to '{target}' requires permission in default mode."

            # Default operation argument is write when omitted
            res_omitted = is_path_allowed(target, ctx_default, work_dir)
            assert res_omitted.behavior == PermissionBehavior.ASK
            assert res_omitted.decision_reason == {"type": "mode", "mode": PermissionMode.DEFAULT}
            assert res_omitted.message == f"Writing to '{target}' requires permission in default mode."

            # In plan mode -> ask
            ctx_plan = ToolPermissionContext(mode=PermissionMode.PLAN)
            res_plan = is_path_allowed(target, ctx_plan, work_dir, operation="write")
            assert res_plan.behavior == PermissionBehavior.ASK
            assert res_plan.decision_reason == {"type": "mode", "mode": PermissionMode.PLAN}
            assert res_plan.message == f"Writing to '{target}' requires permission in plan mode."

            # In dontAsk mode -> deny
            ctx_dont_ask = ToolPermissionContext(mode=PermissionMode.DONT_ASK)
            perm_res = is_path_allowed(target, ctx_dont_ask, work_dir, operation="write")
            assert perm_res.behavior == PermissionBehavior.DENY
            assert perm_res.decision_reason == {"type": "mode", "mode": PermissionMode.DONT_ASK}
            assert perm_res.message == f"Writing to '{target}' suppressed in dontAsk mode."

    def test_unrelated_deny_rule_does_not_deny_allowed_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            target = work_dir / "allowed.txt"
            other = work_dir / "other.txt"
            rule = PermissionRule(tool_name="Edit", rule_behavior=PermissionBehavior.DENY, rule_content=str(other))
            ctx = ToolPermissionContext(mode=PermissionMode.BYPASS_PERMISSIONS, always_deny_rules=(rule,))

            res = is_path_allowed(target, ctx, work_dir, operation="write")
            assert res.behavior == PermissionBehavior.ALLOW

    def test_wildcard_deny_rule(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            secret_dir = work_dir / "secrets"
            secret_dir.mkdir()
            secret_file = secret_dir / "key.pem"
            other_file = work_dir / "other.txt"

            rule = PermissionRule(
                tool_name="Edit",
                rule_behavior=PermissionBehavior.DENY,
                rule_content=f"{secret_dir}/*",
            )
            ctx = ToolPermissionContext(mode=PermissionMode.BYPASS_PERMISSIONS, always_deny_rules=(rule,))

            res_secret = is_path_allowed(secret_file, ctx, work_dir, operation="write")
            assert res_secret.behavior == PermissionBehavior.DENY
            assert "denied by rule" in res_secret.message

            res_other = is_path_allowed(other_file, ctx, work_dir, operation="write")
            assert res_other.behavior == PermissionBehavior.ALLOW

    def test_deny_rule_with_none_or_empty_content_ignored_in_path_check(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            target = work_dir / "file.txt"
            rule1 = PermissionRule(tool_name="Edit", rule_behavior=PermissionBehavior.DENY, rule_content=None)
            rule2 = PermissionRule(tool_name="Edit", rule_behavior=PermissionBehavior.DENY, rule_content="")
            ctx = ToolPermissionContext(
                mode=PermissionMode.BYPASS_PERMISSIONS,
                always_deny_rules=(rule1, rule2),
            )

            res = is_path_allowed(target, ctx, work_dir, operation="write")
            assert res.behavior == PermissionBehavior.ALLOW

    def test_read_operation_on_dangerous_path_allowed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            sensitive = work_dir / ".git" / "config"
            ctx = ToolPermissionContext(mode=PermissionMode.DEFAULT)

            res = is_path_allowed(sensitive, ctx, work_dir, operation="read")
            assert res.behavior == PermissionBehavior.ALLOW

    def test_plan_mode_write_requires_permission(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            target = work_dir / "file.txt"
            ctx = ToolPermissionContext(mode=PermissionMode.PLAN)

            res = is_path_allowed(target, ctx, work_dir, operation="write")
            assert res.behavior == PermissionBehavior.ASK
            assert "plan mode" in res.message

    def test_additional_working_directories_in_is_path_allowed(self):
        with tempfile.TemporaryDirectory() as tmpdir1, tempfile.TemporaryDirectory() as tmpdir2:
            w1 = Path(tmpdir1)
            w2 = Path(tmpdir2)
            target = w2 / "file.txt"
            ctx = ToolPermissionContext(
                mode=PermissionMode.ACCEPT_EDITS,
                additional_working_directories=(str(w2),),
            )

            res = is_path_allowed(target, ctx, w1, operation="write")
            assert res.behavior == PermissionBehavior.ALLOW
