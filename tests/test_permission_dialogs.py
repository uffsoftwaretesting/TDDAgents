"""
The permission dialogs (`app/components/permissions/`), mirroring upstream's components.

The first test pins the live-run case (2026-10-31): a compound Bash command used to be shown as
the rebuilt subcommand text (`2 >& 1`, doubled quotes); upstream shows the command verbatim and
no explanation for `subcommandResults`.
"""

from __future__ import annotations

from typing import Any

from app.components.permissions.permission_dialog import (
    DEFAULT_QUESTION,
    PermissionDialog,
)
from app.components.permissions.permission_request import permission_dialog
from app.components.permissions.permission_rule_explanation import (
    permission_rule_explanation,
)
from app.state.app_state_store import AppStateStore
from app.tool import ToolResult, build_tool, tool_context_for
from app.tools.bash_tool.bash_tool import build_bash_tool
from app.tools.file_edit_tool.file_edit_tool import build_edit_tool
from app.tools.file_read_tool.file_read_tool import build_read_file_tool
from app.tools.file_write_tool.file_write_tool import build_write_file_tool
from app.tools.glob_tool.glob_tool import build_glob_tool
from app.tools.web_fetch_tool.web_fetch_tool import build_web_fetch_tool
from app.types.permissions import (
    PermissionBehavior,
    PermissionResult,
    PermissionRule,
    PermissionRuleSource,
)
from tests.conftest import FakeWorkspace

ASK = PermissionBehavior.ASK


def _ask(reason: dict[str, Any] | None = None) -> PermissionResult:
    return PermissionResult(behavior=ASK, message="model-facing text", decision_reason=reason)


def _ctx(files: dict[str, str] | None = None) -> Any:
    ws = FakeWorkspace(files=files or {})
    ws.root = "/ws"  # type: ignore[attr-defined]
    return tool_context_for(AppStateStore(), workspace=ws)


def test_a_compound_bash_command_is_shown_verbatim_without_the_subcommand_text():
    command = "cd /p && .venv/bin/python -m pip install -r requirements.txt 2>&1 | tail -n 5"
    args = {"command": command, "description": "Install project requirements into venv"}
    reason = {"type": "subcommandResults", "reasons": {"cd /p && pip ... 2 >& 1": _ask()}}
    dialog = permission_dialog(build_bash_tool(), args, _ask(reason), _ctx())
    assert dialog == PermissionDialog(title="Bash command", body=command,
                                      description="Install project requirements into venv")
    quoted = """curl -d '{"content":"   "}' localhost"""
    assert permission_dialog(build_bash_tool(), {"command": quoted}, _ask(), _ctx()).body == quoted


def test_a_bash_dialog_without_a_description_says_run_shell_command_and_shows_a_rule():
    rule = PermissionRule(tool_name="Bash", rule_behavior=ASK, rule_content="make:*",
                          source=PermissionRuleSource.PROJECT_SETTINGS)
    dialog = permission_dialog(build_bash_tool(), {"command": "make"}, _ask({"type": "rule", "rule": rule}), _ctx())
    assert dialog.description == "Run shell command"
    assert dialog.explanation == "Permission rule Bash(make:*) requires confirmation for this command."
    assert dialog.lines() == ["Bash command", "make", "Run shell command", dialog.explanation, DEFAULT_QUESTION]


def test_the_explanation_shows_only_upstreams_reason_types():
    assert permission_rule_explanation(None, "tool") is None
    for kind in ("subcommandResults", "mode", "asyncAgent", "phase_rule"):
        assert permission_rule_explanation({"type": kind, "reason": "x"}, "tool") is None
    for kind in ("safetyCheck", "other", "workingDir"):
        assert permission_rule_explanation({"type": kind, "reason": "the reason"}, "tool") == "the reason"
        assert permission_rule_explanation({"type": kind}, "tool") is None
    assert permission_rule_explanation({"type": "hook", "hookName": "H"}, "command") == (
        "Hook H requires confirmation for this command.")
    assert permission_rule_explanation({"type": "hook", "hookName": "H", "reason": "r", "hookSource": "user"},
                                       "tool") == "Hook H requires confirmation for this tool:\nr [user]"
    assert permission_rule_explanation({"type": "classifier", "classifier": "auto-mode", "reason": "r"}, "tool") == (
        "Auto mode classifier requires confirmation for this tool.\nr")
    assert permission_rule_explanation({"type": "classifier", "classifier": "c", "reason": "r"}, "command") == (
        "Classifier c requires confirmation for this command.\nr")
    assert permission_rule_explanation({"type": "rule", "rule": "not a rule"}, "tool") is None


def test_an_edit_shows_the_patch_against_the_file_and_names_it():
    ctx = _ctx({"src/calc.py": "a = 1\nb = 2\n"})
    args = {"file_path": "src/calc.py", "old_string": "b = 2", "new_string": "b = 3"}
    dialog = permission_dialog(build_edit_tool(), args, _ask({"type": "other", "reason": "x"}), ctx)
    assert (dialog.title, dialog.subtitle) == ("Edit file", "src/calc.py")
    assert dialog.question == "Do you want to make this edit to calc.py?"
    assert dialog.body == "@@ -1,3 +1,3 @@\n a = 1\n-b = 2\n+b = 3\n "
    assert dialog.explanation == ""


def test_a_write_creates_or_overwrites():
    tool = build_write_file_tool()
    new = permission_dialog(tool, {"file_path": "/ws/.gitignore", "content": ".env\n"}, _ask(), _ctx())
    assert (new.title, new.subtitle, new.body) == ("Create file", ".gitignore", ".env\n")
    assert new.question == "Do you want to create .gitignore?"
    ctx = _ctx({"README.md": "old\n"})
    over = permission_dialog(tool, {"file_path": "README.md", "content": "new\n"}, _ask(), ctx)
    assert (over.title, over.question) == ("Overwrite file", "Do you want to overwrite README.md?")
    assert over.body == "@@ -1,2 +1,2 @@\n-old\n+new\n "


def test_read_and_search_tools_share_the_filesystem_dialog():
    read = permission_dialog(build_read_file_tool(), {"file_path": "/etc/hosts", "offset": 2, "limit": 3}, _ask(),
                             _ctx())
    assert read == PermissionDialog(title="Read file", body="Read(/etc/hosts · lines 2-4)")
    glob = permission_dialog(build_glob_tool(), {"pattern": "*.py", "path": "/elsewhere"}, _ask(), _ctx())
    assert glob.body == 'Search(pattern: "*.py", path: "/elsewhere")'
    # No path: upstream falls back to the generic dialog.
    no_path = permission_dialog(build_glob_tool(), {"pattern": "*.py"}, _ask(), _ctx())
    assert no_path.title == "Tool use" and no_path.body == 'Search(pattern: "*.py")'


def test_web_fetch_shows_the_url_and_its_own_question():
    args = {"url": "https://example.com/x", "prompt": "summarize"}
    dialog = permission_dialog(build_web_fetch_tool(), args, _ask(), _ctx())
    assert (dialog.title, dialog.body) == ("Fetch", "https://example.com/x")
    assert dialog.question == "Do you want to allow TDDAgents to fetch this content?"


def test_any_other_tool_gets_the_fallback_with_its_input():
    tool = build_tool(name="mcp__srv__run", prompt="p", call=lambda a, c: ToolResult(content=""),
                      description=lambda a: "one\ntwo\nthree\nfour", is_mcp=True,
                      user_facing_name=lambda a: "srv - run (MCP)")
    dialog = permission_dialog(tool, {"cmd": "ls", "n": 2, "deep": {"a": [1, "b"]}}, _ask({"type": "other",
                                                                                          "reason": "why"}), _ctx())
    assert dialog == PermissionDialog(title="Tool use", body='srv - run (MCP)(cmd: "ls", n: 2, deep: {"a":[1,"b"]})',
                                      description="one\ntwo\nthree…", explanation="why")
