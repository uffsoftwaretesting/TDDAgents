"""
Phase 11: interactive permission prompts — upstream's suggestions, the per-tool options and
`canUseTool` (`app/hooks/use_can_use_tool.py`), with the TDD tier still in front.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolCall

from app.components.permissions.permission_options import (
    NO,
    YES,
    PermissionOption,
    bash_tool_use_options,
    fallback_permission_options,
    file_permission_options,
    generate_shell_suggestions_label,
    permission_options,
    should_show_always_allow_options,
)
from app.hooks.use_can_use_tool import (
    PermissionAnswer,
    PermissionPrompt,
    build_interactive_can_use_tool,
    rejection,
)
from app.services.tools.tool_execution import run_tool_use
from app.state.app_state_store import AppState, AppStateStore
from app.tdd.ledger import PhaseLedger, TddPhase
from app.tdd.plan import TddPlan
from app.tdd.tdd_plan_tool.tdd_plan_tool import build_update_plan_tool
from app.tool import ToolResult, build_tool, tool_context_for
from app.tools.bash_tool.bash_permissions import (
    MAX_SUGGESTED_RULES_FOR_COMPOUND,
    check_command_and_suggest_rules,
    compound_suggestions,
)
from app.tools.bash_tool.bash_tool import build_bash_tool
from app.tools.file_write_tool.file_write_tool import build_write_file_tool
from app.types.permissions import (
    PermissionBehavior,
    PermissionMode,
    PermissionResult,
    PermissionRuleSource,
    ToolPermissionContext,
)
from app.utils.messages import REJECT_MESSAGE, REJECT_MESSAGE_WITH_REASON_PREFIX
from app.utils.permissions.filesystem import (
    check_read_permission_for_tool,
    check_write_permission_for_tool,
    generate_suggestions,
    get_directory_for_path,
)
from app.utils.permissions.permission_update import (
    PermissionUpdate,
    create_read_rule_suggestion,
)
from app.utils.permissions.shell_rule_matching import (
    suggestion_for_exact_command,
    suggestion_for_prefix,
)
from app.utils.settings.settings import build_tool_permission_context
from tests.conftest import FakeWorkspace

LOCAL = PermissionRuleSource.LOCAL_SETTINGS
SESSION = PermissionRuleSource.SESSION


def rule_update(*rules: tuple[str, str | None], destination: PermissionRuleSource = LOCAL) -> PermissionUpdate:
    return PermissionUpdate("addRules", destination, rules=rules, behavior=PermissionBehavior.ALLOW)


# ── suggestions ──────────────────────────────────────────────────────────────

def test_shell_suggestions_are_local_allow_rules():
    assert suggestion_for_exact_command("Bash", "npm test") == (rule_update(("Bash", "npm test")),)
    assert suggestion_for_prefix("Bash", "npm run") == (rule_update(("Bash", "npm run:*")),)


def test_an_unknown_command_suggests_its_exact_rule():
    result = check_command_and_suggest_rules("make deploy", ToolPermissionContext(), "/w")
    assert result.behavior == PermissionBehavior.PASSTHROUGH
    assert result.suggestions == (rule_update(("Bash", "make deploy")),)


def test_a_command_the_security_checks_flag_suggests_nothing():
    result = check_command_and_suggest_rules("echo $(whoami)", ToolPermissionContext(), "/w")
    assert result.behavior == PermissionBehavior.ASK
    assert result.suggestions == ()


def test_an_allowed_command_carries_no_suggestion():
    assert check_command_and_suggest_rules("ls", ToolPermissionContext(), "/w").suggestions is None


def test_compound_suggestions_collect_dedupe_cap_and_synthesize():
    passthrough = PermissionResult(behavior=PermissionBehavior.PASSTHROUGH,
                                   suggestions=(rule_update(("Bash", "a")), rule_update(("Bash", "a"))))
    flagged = PermissionResult(behavior=PermissionBehavior.ASK, suggestions=())
    by_rule = PermissionResult(behavior=PermissionBehavior.ASK, decision_reason={"type": "rule"})
    allowed = PermissionResult(behavior=PermissionBehavior.ALLOW, suggestions=(rule_update(("Bash", "x")),))
    results = {"a": passthrough, "rm -rf y": flagged, "asked": by_rule, "ok": allowed}
    assert compound_suggestions(results) == (rule_update(("Bash", "a"), ("Bash", "rm -rf y")),)
    many = {f"c{i}": PermissionResult(behavior=PermissionBehavior.ASK) for i in range(7)}
    (update,) = compound_suggestions(many) or ()
    assert [content for _, content in update.rules] == [f"c{i}" for i in range(MAX_SUGGESTED_RULES_FOR_COMPOUND)]
    assert compound_suggestions({"ok": allowed}) is None


def test_create_read_rule_suggestion():
    assert create_read_rule_suggestion("/data") == rule_update(("Read", "//data/**"), destination=SESSION)
    assert create_read_rule_suggestion("rel") == rule_update(("Read", "rel/**"), destination=SESSION)
    assert create_read_rule_suggestion("/") is None


def test_get_directory_for_path():
    assert get_directory_for_path("/a/b/c.py") == "/a/b"
    assert get_directory_for_path("/a/b/") == "/a"
    assert get_directory_for_path("/c.py") == "/"


def ctx_for(mode: PermissionMode = PermissionMode.DEFAULT, **kw: Any) -> ToolPermissionContext:
    return ToolPermissionContext(mode=mode, **kw)


ACCEPT = PermissionUpdate("setMode", SESSION, mode=PermissionMode.ACCEPT_EDITS)


def test_generate_suggestions_for_writes():
    assert generate_suggestions("/w/a.py", "write", ctx_for(), "/w") == (ACCEPT,)
    assert generate_suggestions("/w/a.py", "create", ctx_for(PermissionMode.PLAN), "/w") == (ACCEPT,)
    assert generate_suggestions("/w/a.py", "write", ctx_for(PermissionMode.AUTO), "/w") == ()
    outside = generate_suggestions("/elsewhere/a.py", "write", ctx_for(), "/w")
    assert outside == (ACCEPT, PermissionUpdate("addDirectories", SESSION, directories=("/elsewhere",)))
    assert generate_suggestions("/elsewhere/a.py", "write", ctx_for(PermissionMode.ACCEPT_EDITS), "/w") == (
        PermissionUpdate("addDirectories", SESSION, directories=("/elsewhere",)),)


def test_generate_suggestions_for_reads():
    assert generate_suggestions("/elsewhere/a.py", "read", ctx_for(), "/w") == (
        rule_update(("Read", "//elsewhere/**"), destination=SESSION),)
    assert generate_suggestions("/w/a.py", "read", ctx_for(), "/w") == (ACCEPT,)
    assert generate_suggestions("/w/a.py", "read", ctx_for(PermissionMode.BYPASS_PERMISSIONS), "/w") == ()
    assert generate_suggestions("/c.py", "read", ctx_for(), "/w") == ()


def test_the_file_asks_carry_their_suggestions():
    write = check_write_permission_for_tool("a.py", {}, ctx_for(), "/w")
    assert write.behavior == PermissionBehavior.ASK and write.suggestions == (ACCEPT,)
    unsafe = check_write_permission_for_tool(".git/config", {}, ctx_for(), "/w")
    assert unsafe.behavior == PermissionBehavior.ASK and unsafe.suggestions == (ACCEPT,)
    read = check_read_permission_for_tool("/outside/x.py", {}, ctx_for(), "/w")
    assert read.behavior == PermissionBehavior.ASK
    assert read.suggestions == (rule_update(("Read", "//outside/**"), destination=SESSION),)


# ── options ──────────────────────────────────────────────────────────────────

def test_fallback_options():
    options = fallback_permission_options("mcp__s__t", "/w", True)
    assert [o.value for o in options] == ["yes", "yes-dont-ask-again", "no"]
    assert options[1].label == "Yes, and don't ask again for mcp__s__t commands in /w"
    assert options[1].updates == (rule_update(("mcp__s__t", None)),)
    assert [o.value for o in fallback_permission_options("t", "/w", False)] == ["yes", "no"]
    assert options[0] == YES and options[-1] == NO and NO.reject and not YES.reject


@pytest.mark.parametrize("rules, directories, label", [
    ([("Bash", "npm test")], [], "Yes, and don't ask again for npm test commands in /w"),
    ([("Bash", "npm run:*")], [], "Yes, and don't ask again for npm run commands in /w"),
    ([("Bash", "a"), ("Bash", "b"), ("Bash", "c")], [], "Yes, and don't ask again for a, b, and c commands in /w"),
    ([("Bash", "x" * 51)], [], "Yes, and don't ask again for similar commands in /w"),
    ([("Bash", "echo hi > out.txt")], [], "Yes, and don't ask again for echo hi commands in /w"),
    ([("Read", "//data/**")], [], "Yes, allow reading from data/ from this project"),
    ([("Read", "//a/**"), ("Read", "//b/**"), ("Read", "//c/**")], [],
     "Yes, allow reading from a/, b/ and 1 more from this project"),
    ([], ["/srv"], "Yes, and always allow access to srv/ from this project"),
    ([], ["/a", "/b"], "Yes, and always allow access to a/ and b/ from this project"),
    ([("Read", "//d/**")], ["/s"], "Yes, and always allow access to s/ and d/ from this project"),
    ([("Bash", "ls")], ["/s"], "Yes, and allow access to s/ and ls commands"),
    ([("Bash", "ls"), ("Bash", "cat")], ["/s"], "Yes, and allow s/ access and ls and cat commands"),
    ([("Edit", "x")], [], None),
])
def test_shell_suggestions_label(rules: list[Any], directories: list[str], label: str | None):
    suggestions: list[Any] = [rule_update(*rules)] if rules else []
    if directories:
        suggestions.append(PermissionUpdate("addDirectories", SESSION, directories=tuple(directories)))
    assert generate_shell_suggestions_label(suggestions, "Bash", "/w") == label


def test_bash_options():
    suggestions = (rule_update(("Bash", "npm test")),)
    options = bash_tool_use_options(suggestions, True, "/w")
    assert [o.value for o in options] == ["yes", "yes-apply-suggestions", "no"]
    assert options[1].updates == suggestions
    assert [o.value for o in bash_tool_use_options(suggestions, False, "/w")] == ["yes", "no"]
    assert [o.value for o in bash_tool_use_options((), True, "/w")] == ["yes", "no"]
    assert [o.value for o in bash_tool_use_options((rule_update(("Edit", "x")),), True, "/w")] == ["yes", "no"]


def test_file_options():
    inside = file_permission_options("a.py", "write", ctx_for(), "/w")
    assert [o.label for o in inside] == ["Yes", "Yes, allow all edits during this session", "No"]
    assert inside[1].updates == (ACCEPT,)
    read_inside = file_permission_options("a.py", "read", ctx_for(), "/w")
    assert read_inside[1].label == "Yes, during this session"
    outside = file_permission_options("../out/a.py", "write", ctx_for(), "/w")
    assert outside[1].label == "Yes, allow all edits in out/ during this session"
    read_outside = file_permission_options("../out/a.py", "read", ctx_for(), "/w")
    assert read_outside[1].label == "Yes, allow reading from out/ during this session"
    assert file_permission_options("", "read", ctx_for(), "/w")[1].label == "Yes, during this session"


def test_permission_options_dispatch_by_tool():
    perm = ctx_for()
    assert permission_options("UpdatePlan", {}, None, perm, "/w") == [YES, NO]
    assert [o.value for o in permission_options("Bash", {}, (rule_update(("Bash", "x")),), perm, "/w")] == [
        "yes", "yes-apply-suggestions", "no"]
    assert permission_options("WriteFile", {"file_path": "a.py"}, None, perm, "/w")[1].value == "yes-session"
    assert permission_options("Grep", {"path": "src"}, None, perm, "/w")[1].label == "Yes, during this session"
    assert permission_options("mcp__x", {}, None, perm, "/w")[1].value == "yes-dont-ask-again"


def test_always_options_are_hidden_under_managed_rules_only(tmp_path: Path):
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "managed-settings.json").write_text(json.dumps({"allowManagedPermissionRulesOnly": True}))
    managed = build_tool_permission_context(tmp_path, tmp_path / "home", managed_dir=etc)
    assert should_show_always_allow_options(managed) is False
    assert [o.value for o in permission_options("mcp__x", {}, None, managed, "/w")] == ["yes", "no"]
    assert should_show_always_allow_options(build_tool_permission_context(tmp_path, tmp_path / "home",
                                                                          managed_dir=tmp_path / "none")) is True
    assert should_show_always_allow_options(ToolPermissionContext()) is True


# ── canUseTool ───────────────────────────────────────────────────────────────

class Asker:
    def __init__(self, *answers: PermissionAnswer) -> None:
        self.answers = list(answers)
        self.prompts: list[PermissionPrompt] = []

    async def __call__(self, prompt: PermissionPrompt) -> PermissionAnswer:
        self.prompts.append(prompt)
        return self.answers.pop(0)


def interactive_context(tmp_path: Path, asker: Asker, *, ledger: PhaseLedger = PhaseLedger(),
                        plan: TddPlan = TddPlan(), mode: str = "default") -> Any:
    (tmp_path / "home").mkdir(exist_ok=True)
    perm = build_tool_permission_context(tmp_path, tmp_path / "home", managed_dir=tmp_path / "etc",
                                         permission_mode_cli=mode, working_dir=tmp_path / "ws",
                                         should_avoid_permission_prompts=False)
    store = AppStateStore(state=AppState(tool_permission_context=perm, phase_ledger=ledger, tdd_plan=plan))
    ws = FakeWorkspace()
    ws.root = tmp_path / "ws"  # type: ignore[attr-defined]
    ctx = tool_context_for(store, workspace=ws)
    ctx.can_use_tool = build_interactive_can_use_tool(asker)
    return ctx


def call(tool: Any, args: dict[str, Any], ctx: Any) -> Any:
    ctx.tools = (tool,)
    return asyncio.run(run_tool_use(ToolCall(name=tool.name, args=args, id="c1"), AIMessage(content=""), ctx, (tool,)))


def test_yes_runs_the_call_once(tmp_path: Path):
    asker = Asker(PermissionAnswer("yes"), PermissionAnswer("no", "not again"))
    ctx = interactive_context(tmp_path, asker)
    tool = build_bash_tool()
    assert call(tool, {"command": "make build"}, ctx).message.status == "success"
    prompt = asker.prompts[0]
    assert (prompt.tool_name, prompt.input) == ("Bash", {"command": "make build"})
    assert [o.value for o in prompt.options] == ["yes", "yes-apply-suggestions", "no"]
    assert prompt.message and prompt.description
    # Only once: the same command asks again.
    assert call(tool, {"command": "make build"}, ctx).message.status == "error"
    assert len(asker.prompts) == 2


def test_dont_ask_again_persists_the_rule_and_the_next_call_runs(tmp_path: Path):
    asker = Asker(PermissionAnswer("yes-apply-suggestions"))
    ctx = interactive_context(tmp_path, asker)
    tool = build_bash_tool()
    assert call(tool, {"command": "make build"}, ctx).message.status == "success"
    assert call(tool, {"command": "make build"}, ctx).message.status == "success"
    assert len(asker.prompts) == 1
    local = json.loads((tmp_path / ".tddagents" / "settings.local.json").read_text())
    assert local["permissions"]["allow"] == ["Bash(make build)"]


def test_during_this_session_switches_to_accept_edits_in_memory_only(tmp_path: Path):
    asker = Asker(PermissionAnswer("yes-session"))
    ctx = interactive_context(tmp_path, asker)
    tool = build_write_file_tool()
    ctx.set_app_state(lambda s: s)
    out = call(tool, {"file_path": "tests/test_a.py", "content": "x"}, ctx)
    assert out.message.status == "success"
    assert ctx.get_app_state().tool_permission_context.mode == PermissionMode.ACCEPT_EDITS
    assert not (tmp_path / ".tddagents" / "settings.local.json").exists()


def test_no_without_feedback_rejects_and_ends_the_turn(tmp_path: Path):
    asker = Asker(PermissionAnswer("no"))
    ctx = interactive_context(tmp_path, asker)
    out = call(build_bash_tool(), {"command": "make build"}, ctx)
    assert out.message.status == "error" and out.message.content == REJECT_MESSAGE
    assert ctx.cancel.cancelled is True


def test_no_with_feedback_tells_the_model_and_keeps_the_turn(tmp_path: Path):
    asker = Asker(PermissionAnswer("no", "  use the Makefile target 'test'  "))
    ctx = interactive_context(tmp_path, asker)
    out = call(build_bash_tool(), {"command": "make build"}, ctx)
    assert out.message.content == f"{REJECT_MESSAGE_WITH_REASON_PREFIX}use the Makefile target 'test'"
    assert ctx.cancel.cancelled is False


def test_an_answer_naming_no_option_is_a_no(tmp_path: Path):
    ctx = interactive_context(tmp_path, Asker(PermissionAnswer("maybe")))
    assert call(build_bash_tool(), {"command": "make build"}, ctx).message.content == REJECT_MESSAGE


def test_the_tdd_tier_denies_before_any_question(tmp_path: Path):
    asker = Asker()
    ctx = interactive_context(tmp_path, asker, mode="bypassPermissions")
    out = call(build_write_file_tool(), {"file_path": "calculator.py", "content": "x"}, ctx)
    assert out.message.status == "error"
    assert asker.prompts == []


def test_a_permission_request_hook_answers_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import app.hooks.use_can_use_tool as handler

    async def hook(*a: Any, **k: Any) -> PermissionResult:
        return PermissionResult(behavior=PermissionBehavior.ALLOW)

    monkeypatch.setattr(handler, "run_permission_request_hooks_for_headless_agent", hook)
    asker = Asker()
    ctx = interactive_context(tmp_path, asker)
    assert call(build_bash_tool(), {"command": "make build"}, ctx).message.status == "success"
    assert asker.prompts == []


def test_a_decision_that_is_not_an_ask_passes_through():
    can_use = build_interactive_can_use_tool(Asker())
    allow = PermissionResult(behavior=PermissionBehavior.ALLOW)
    assert asyncio.run(can_use(SimpleNamespace(name="t"), {}, allow, SimpleNamespace(), "id")) is allow


def test_rejection_messages():
    assert rejection(None) == PermissionResult(behavior=PermissionBehavior.ASK, message=REJECT_MESSAGE)
    assert rejection("x").message == REJECT_MESSAGE_WITH_REASON_PREFIX + "x"


def test_an_approved_plan_change_applies(tmp_path: Path):
    asker = Asker(PermissionAnswer("yes"))
    ctx = interactive_context(tmp_path, asker, plan=TddPlan.from_texts(["A"]))
    out = call(build_update_plan_tool(), {"operations": [{"op": "add", "text": "B"}]}, ctx)
    assert out.message.status == "success"
    assert [o.value for o in asker.prompts[0].options] == ["yes", "no"]
    assert asker.prompts[0].message.startswith("Update the TDD plan?")
    assert [i.text for i in ctx.get_app_state().tdd_plan.items] == ["A", "B"]


def test_a_rejected_plan_change_leaves_the_plan(tmp_path: Path):
    asker = Asker(PermissionAnswer("no", "keep it as it is"))
    ctx = interactive_context(tmp_path, asker, plan=TddPlan.from_texts(["A"]))
    out = call(build_update_plan_tool(), {"operations": [{"op": "add", "text": "B"}]}, ctx)
    assert out.message.status == "error"
    assert [i.text for i in ctx.get_app_state().tdd_plan.items] == ["A"]


def test_without_can_use_tool_an_ask_is_refused(tmp_path: Path):
    ctx = interactive_context(tmp_path, Asker())
    ctx.can_use_tool = None
    out = call(build_tool(name="Ask", prompt="p", call=lambda a, c: ToolResult(content="ran"),
                          check_permissions=lambda a, c: PermissionResult(behavior=PermissionBehavior.ASK,
                                                                          message="needs a person")), {}, ctx)
    assert out.message.status == "error" and out.message.content == "needs a person"


def test_permission_option_defaults():
    assert PermissionOption("v", "l") == PermissionOption("v", "l", (), False)


def test_ledger_phases_are_unaffected_by_an_answer(tmp_path: Path):
    asker = Asker(PermissionAnswer("yes-apply-suggestions"))
    ctx = interactive_context(tmp_path, asker, ledger=PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True))
    call(build_bash_tool(), {"command": "make build"}, ctx)
    assert ctx.get_app_state().phase_ledger == PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True)
