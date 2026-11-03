"""
`/init` (`reference/claude-code/src/commands/init.ts`): the command, its two prompts, and where a
typed `/init` goes (the REPL, `-p`, the SDK's init message).

The two prompts are upstream's text verbatim after the recorded substitutions, checked against
the reference snapshot when it is present.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import app.project_onboarding_state as onboarding
from app.commands.commands import HELP_TEXT, format_description_with_source, get_commands, help_text
from app.commands.init import (
    INIT_PROGRESS_MESSAGE,
    NEW_INIT_DESCRIPTION,
    NEW_INIT_ENV,
    NEW_INIT_PROMPT,
    OLD_INIT_DESCRIPTION,
    OLD_INIT_PROMPT,
    PROMPTS_DIR,
    init_command,
)
from app.services.plugins.plugin_operations import PLUGIN_CLI
from app.skills.load_skills_dir import SkillRegistry
from app.utils.config import get_current_project_config
from tests.test_commands_registry import _skill

REPO = Path(__file__).resolve().parent.parent
INIT_TS = REPO / "reference" / "claude-code" / "src" / "commands" / "init.ts"
needs_reference = pytest.mark.skipif(not INIT_TS.is_file(),
                                     reason="the claude-code reference snapshot is not checked out")


# ── the prompts: upstream's text, verbatim after the recorded substitutions ──

def _literal(source: str, name: str) -> str:
    start = source.index(f"const {name} = `") + len(f"const {name} = `")
    end = source.index("`\n", start)
    while source[end - 1] == "\\":
        end = source.index("`\n", end + 1)
    return source[start:end].replace("\\`", "`")


#: NEW_INIT_PROMPT: what TDDAgents lacks, its equivalent where a route exists, otherwise dropped.
NEW_INIT_ONLY = [
    # Hooks are read when a session starts here, so the `update-config` flow has no in-turn live proof.
    (" → live-proof (for `Pre|PostToolUse` on triggerable matchers) → cleanup → handoff.", " → handoff."),
    # This build's tool names.
    ("`PostToolUse` with matcher `Write|Edit`", "`PostToolUse` with matcher `WriteFile|Edit`"),
    ("`[hooks-only] Constructing a PostToolUse/Write|Edit format hook",
     "`[hooks-only] Constructing a PostToolUse/WriteFile|Edit format hook"),
    # No bundled `/verify` skill.
    ("existing bundled skill (e.g., `/verify`), write", "existing bundled skill, write"),
    # The official marketplace's plugins are `.claude-plugin/` packages, which this build does not load.
    ("- If frontend code was detected (React, Vue, Svelte, etc.): `/plugin install "
     "frontend-design@claude-plugins-official` gives Claude design principles and component patterns so it "
     "produces polished UI; `/plugin install playwright@claude-plugins-official` lets Claude launch a real browser, "
     "screenshot what it built, and fix visual bugs itself.\n", ""),
    ("- To help you create skills and optimize existing skills using evals, Claude Code has an official "
     "skill-creator plugin you can install. Install it with `/plugin install skill-creator@claude-plugins-official`, "
     "then run `/skill-creator <skill-name>` to create new skills or refine any existing skill. "
     "(Always include this one.)\n", ""),
    # No `/plugin` dialog: the `plugin` CLI lists what the marketplaces offer.
    ("- Browse official plugins with `/plugin` — ", "- Browse plugins with `{{PLUGIN_CLI}} list --available` — "),
]
#: Both prompts: the product substitutions, the prefix line without the URL (user decision).
PRODUCT = [
    ("This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.",
     "This file provides guidance to TDDAgents when working with code in this repository."),
    ("CLAUDE.local.md", "TDDAGENTS.local.md"),
    ("CLAUDE.md", "TDDAGENTS.md"),
    ("Claude Code", "TDDAgents"),
    (".claude/", ".tddagents/"),
    ("Claude's", "TDDAgents'"),
]


def _expected(name: str) -> str:
    text = _literal(INIT_TS.read_text(encoding="utf-8"), name)
    for old, new in NEW_INIT_ONLY if name == "NEW_INIT_PROMPT" else []:
        assert old in text, old  # upstream changed: the substitution must be reviewed
        text = text.replace(old, new)
    for old, new in PRODUCT:
        text = text.replace(old, new)
    return re.sub(r"\bClaude\b", "TDDAgents", text)


def _file(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


@needs_reference
def test_old_init_prompt_is_upstreams():
    assert _file("old_init.md") == _expected("OLD_INIT_PROMPT") + "\n"
    assert OLD_INIT_PROMPT == _expected("OLD_INIT_PROMPT")


@needs_reference
def test_new_init_prompt_is_upstreams():
    assert _file("new_init.md") == _expected("NEW_INIT_PROMPT") + "\n"


def test_new_init_prompt_names_the_plugin_cli():
    assert NEW_INIT_PROMPT == _file("new_init.md").removesuffix("\n").replace("{{PLUGIN_CLI}}", PLUGIN_CLI)
    assert f"`{PLUGIN_CLI} list --available`" in NEW_INIT_PROMPT and "{{" not in NEW_INIT_PROMPT


def test_both_prompts_end_with_upstreams_last_line_and_name_tddagents_only():
    assert OLD_INIT_PROMPT.endswith("This file provides guidance to TDDAgents when working with code in this "
                                    "repository.\n```")
    assert NEW_INIT_PROMPT.endswith("(Always include this one.)")
    for text in (OLD_INIT_PROMPT, NEW_INIT_PROMPT):
        assert "Claude" not in text and "CLAUDE" not in text and ".claude/" not in text


# ── the command ──────────────────────────────────────────────────────────────

def test_init_is_a_built_in_prompt_command(tmp_path, monkeypatch):
    monkeypatch.delenv(NEW_INIT_ENV, raising=False)
    command = init_command(tmp_path)
    assert (command.name, command.source, command.loaded_from, command.skill_root) == ("init", "builtin", None, None)
    assert (command.description, command.progress_message, command.content_length) == (
        OLD_INIT_DESCRIPTION, INIT_PROGRESS_MESSAGE, 0)
    assert OLD_INIT_DESCRIPTION == "Initialize a new TDDAGENTS.md file with codebase documentation"
    assert INIT_PROGRESS_MESSAGE == "analyzing your codebase"
    assert command.user_invocable and not command.disable_model_invocation and not command.is_hidden
    assert (command.allowed_tools, command.model, command.context, command.cwd) == ((), None, None, str(tmp_path))
    assert command.prompt_for_command("ignored", session_id="s") == OLD_INIT_PROMPT


@pytest.mark.parametrize("value, new", [("1", True), ("true", True), ("yes", True), ("0", False), ("", False),
                                        (None, False)])
def test_tddagents_new_init_picks_the_new_prompt_and_description(tmp_path, monkeypatch, value, new):
    if value is None:
        monkeypatch.delenv(NEW_INIT_ENV, raising=False)
    else:
        monkeypatch.setenv(NEW_INIT_ENV, value)
    assert NEW_INIT_ENV == "TDDAGENTS_NEW_INIT"
    command = init_command(tmp_path)
    assert command.description == (NEW_INIT_DESCRIPTION if new else OLD_INIT_DESCRIPTION)
    assert command.prompt_for_command("") == (NEW_INIT_PROMPT if new else OLD_INIT_PROMPT)
    assert NEW_INIT_DESCRIPTION == ("Initialize new TDDAGENTS.md file(s) and optional skills/hooks with codebase "
                                    "documentation")


def test_the_prompt_marks_the_onboarding_complete_once_it_is(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n")
    command = init_command(tmp_path)
    command.prompt_for_command("")
    assert onboarding.HAS_COMPLETED_PROJECT_ONBOARDING not in get_current_project_config(tmp_path)
    (tmp_path / "TDDAGENTS.md").write_text("# TDDAGENTS.md\n")
    command.prompt_for_command("")
    assert get_current_project_config(tmp_path)[onboarding.HAS_COMPLETED_PROJECT_ONBOARDING] is True


# ── the registry: after every skill, never in the Skill tool's listing ───────

def test_built_ins_come_after_every_skill_and_a_skill_of_the_same_name_wins(tmp_path):
    registry = SkillRegistry([_skill()])
    registry.set_built_in_commands([init_command(tmp_path)])
    assert [c.name for c in registry.commands()] == ["design", "init"]
    found = registry.find_command("init")
    assert found is not None and found.source == "builtin"
    shadowed = SkillRegistry([_skill("init")])
    shadowed.set_built_in_commands([init_command(tmp_path)])
    found = shadowed.find_command("init")
    assert found is not None and found.source == "projectSettings"


def test_built_ins_are_not_in_the_skill_tool_listing(tmp_path):
    registry = SkillRegistry([_skill()])
    registry.set_built_in_commands([init_command(tmp_path)])
    assert [c.name for c in registry.get_skill_tool_commands()] == ["design"]
    # Even a built-in that would otherwise qualify (`source !== 'builtin'` comes first upstream).
    registry.set_built_in_commands([_skill("described", source="builtin", has_user_specified_description=True)])
    assert [c.name for c in registry.get_skill_tool_commands()] == ["design"]


def test_get_commands_and_help_list_init_among_the_commands(tmp_path, monkeypatch):
    monkeypatch.delenv(NEW_INIT_ENV, raising=False)
    registry = SkillRegistry([_skill()])
    registry.set_built_in_commands([init_command(tmp_path)])
    commands = get_commands(registry)
    init = next(c for c in commands if c.name == "init")
    assert (init.type, init.source) == ("prompt", "builtin")
    assert format_description_with_source(init) == OLD_INIT_DESCRIPTION
    assert [c.name for c in commands][:3] == ["design", "init", "plan"]
    assert "\n  /init                         initialize a new TDDAGENTS.md file with codebase documentation\n" \
        in HELP_TEXT
    text = help_text(commands)
    assert text == HELP_TEXT + "\nSkills:\n  /design   Design tests (project)"


# ── typed: the REPL and `-p` ─────────────────────────────────────────────────

def test_process_slash_command_finds_init(tmp_path, monkeypatch):
    from app.state.app_state_store import AppStateStore
    from app.tool import tool_context_for
    from app.types.permissions import ToolPermissionContext
    from app.utils.process_user_input.process_user_input import process_slash_command

    async def no_attachments(context: Any, messages: Any, *, user_input: str | None = None) -> list[Any]:
        return []

    monkeypatch.setattr("app.utils.attachments.get_attachment_messages", no_attachments)
    monkeypatch.delenv(NEW_INIT_ENV, raising=False)
    registry = SkillRegistry([])
    registry.set_built_in_commands([init_command(tmp_path)])
    ctx = tool_context_for(AppStateStore(), skill_registry=registry,
                           permission_context=ToolPermissionContext(should_avoid_permission_prompts=True))
    result = asyncio.run(process_slash_command("/init", ctx, []))
    assert result is not None and result.should_query
    metadata, content = result.messages
    assert metadata.content == "<command-message>init</command-message>\n<command-name>/init</command-name>"
    assert content.content == OLD_INIT_PROMPT and content.additional_kwargs == {"is_meta": True}
    assert (result.allowed_tools, result.model) == ((), None)


def _sent(model: Any) -> list[HumanMessage]:
    return [m for m in model.seen[0].messages if isinstance(m, HumanMessage)]


def test_dash_p_init_queries_the_model_with_the_init_prompt(python_workspace, tmp_path, monkeypatch):
    from app.cli.print import HeadlessOptions, run_headless
    from tests.test_stream_json import Model, run_options

    monkeypatch.delenv(NEW_INIT_ENV, raising=False)
    model = Model([[AIMessage(content="Wrote TDDAGENTS.md.")]])
    written: list[str] = []
    outcome = asyncio.run(run_headless("init-p", "/init", workspace=python_workspace,
                                       options=HeadlessOptions(output_format="json"), write_stdout=written.append,
                                       **run_options(tmp_path, model)))
    assert len(model.seen) == 1
    sent = _sent(model)
    assert any(m.content == "<command-message>init</command-message>\n<command-name>/init</command-name>"
               for m in sent)
    assert any(m.content == OLD_INIT_PROMPT and m.additional_kwargs.get("is_meta") for m in sent)
    assert json.loads(written[-1])["result"] == "Wrote TDDAGENTS.md."
    assert outcome is not None


def test_the_sdk_init_message_lists_init_as_a_command_not_a_skill(python_workspace, tmp_path):
    from app.cli.print import run_headless
    from tests.test_stream_json import STREAM, Feed, Model, lines_of, run_options, user

    feed = Feed(user("hi", "u1"))
    written: list[str] = []

    def write(text: str) -> None:
        written.append(text)
        if any(m.get("type") == "result" for m in lines_of([text])):
            feed.close()

    model = Model([[AIMessage(content="ok")]])
    asyncio.run(run_headless("init-sdk", None, workspace=python_workspace, options=STREAM,
                             input_blocks=feed.blocks(), write_stdout=write, **run_options(tmp_path, model)))
    system_init = next(m for m in lines_of(written) if m.get("subtype") == "init")
    assert "init" in system_init["slash_commands"]
    assert "init" not in system_init["skills"]


def test_typing_init_in_the_repl_runs_it_as_a_turn(python_workspace, tmp_path, monkeypatch):
    from app.tdd.plan import TddPlan
    from tests.test_repl import CYCLE, FakeIO, Model, session, until

    monkeypatch.delenv(NEW_INIT_ENV, raising=False)
    model = Model([[AIMessage(content=f"reply {i}")] for i in range(4)])

    async def script(repl: Any, io: FakeIO) -> None:
        await until(lambda: not repl.busy and len(model.seen) > 0)
        start = len(model.seen)
        io.lines.put_nowait("/init")
        await until(lambda: len(model.seen) == start + 1 and not repl.busy)
        io.lines.put_nowait("/exit")

    asyncio.run(session(python_workspace, tmp_path, model, TddPlan.from_texts(["A"]).complete_current(CYCLE), script))
    said = [m for m in model.seen[-1].messages if isinstance(m, HumanMessage)]
    assert any(m.content == OLD_INIT_PROMPT and m.additional_kwargs.get("is_meta") for m in said)
    assert not any("Unknown skill" in str(m.content) for m in said)
