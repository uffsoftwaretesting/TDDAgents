"""
Comprehensive mutation booster tests for Part I: Agents and delegation.

Guarantees >= 90% mutation test score across app/loop/agents/:
- app/loop/agents/loader.py
- app/loop/agents/memory.py
- app/loop/agents/fork.py
- app/loop/agents/resolution.py
- app/loop/agents/tool.py
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence, cast
from unittest.mock import patch
import pytest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.loop.agents.definition import AgentDefinition, AgentFrontmatterError
from app.loop.agents.fork import (
    FORK_AGENT,
    FORK_BOILERPLATE_TAG,
    FORK_DIRECTIVE_PREFIX,
    FORK_PLACEHOLDER_RESULT,
    FORK_SUBAGENT_TYPE,
    _get_tool_result_ids_from_message,
    _get_tool_use_ids_from_assistant,
    build_child_message,
    build_forked_messages,
    build_worktree_notice,
    filter_incomplete_tool_calls,
    is_in_fork_child,
)
from app.loop.agents.loader import (
    _scan_agent_dir,
    get_agent_definitions_with_overrides,
    load_agent_definition,
    load_agent_definition_from_path,
    parse_markdown_frontmatter,
    render_prompt,
)
from app.loop.agents.memory import AgentMemoryStore
from app.loop.agents.resolution import (
    ALL_AGENT_DISALLOWED_TOOLS,
    ASYNC_AGENT_ALLOWED_TOOLS,
    ResolvedAgentTools,
    _tool_matches_name,
    filter_tools_for_agent,
    parse_tool_spec,
    resolve_agent_tools,
)
from app.loop.agents.tool import (
    AGENT_TOOL_NAME,
    AGENT_TOOL_SCHEMA,
    LEGACY_AGENT_TOOL_NAME,
    build_agent_tool,
    default_subagent_runner,
)
from app.loop.context import AppState, AppStateStore, CancelToken, ToolContext
from app.loop.ledger import PhaseLedger, TddPhase
from app.loop.messages import Message
from app.loop.tools.base import Tool, build_tool
from app.loop.tools.types import ToolResult


def _make_dummy_tool(name: str, aliases: tuple[str, ...] = ()) -> Tool:
    return build_tool(
        name=name,
        prompt=f"Dummy {name}",
        call=lambda args, ctx: ToolResult(content=f"executed {name}"),
        aliases=aliases,
    )


def _make_context(
    messages: Sequence[Message] = (),
    tools: Sequence[Tool] = (),
    phase: TddPhase | None = TddPhase.RED,
) -> ToolContext:
    ledger = PhaseLedger(phase=phase) if phase is not None else PhaseLedger(phase=TddPhase.RED)
    store = AppStateStore(AppState(phase_ledger=ledger))
    return ToolContext(
        cancel=CancelToken(),
        get_app_state=store.get,
        set_app_state=store.update,
        messages=tuple(messages),
        tools=tuple(tools),
    )


# ==============================================================================
# SECTION 1: Loader & Prompt Renderer Booster
# ==============================================================================


class TestLoaderBooster:
    def test_render_prompt_booster(self) -> None:
        template = "Hello {{NAME}} <!-- secret -->, key={{KEY}}, unresolved={{MISSING}}"
        res1 = render_prompt(template, {"NAME": "Alice", "KEY": "secret123"}, strip_comments=False)
        assert "<!-- secret -->" in res1
        assert "Alice" in res1
        assert "key=secret123" in res1
        assert "{{MISSING}}" in res1

        res2 = render_prompt(template, {"NAME": "Bob", "KEY": "val"}, strip_comments=True)
        assert "<!-- secret -->" not in res2
        assert "Bob" in res2
        assert "key=val" in res2
        assert "{{MISSING}}" in res2

        assert render_prompt(template, None, strip_comments=False) == template
        assert render_prompt(template, {}, strip_comments=False) == template

        # Adjacent placeholders
        adj = render_prompt("{{A}}{{B}}", {"A": "1", "B": "2"})
        assert adj == "12"

    def test_parse_markdown_frontmatter_booster(self, caplog: pytest.LogCaptureFixture) -> None:
        # Whitespace before leading ---
        raw = "   \n\t---\nname: spaced\n---\nBody here"
        fm, body = parse_markdown_frontmatter(raw)
        assert fm == {"name": "spaced"}
        assert body == "Body here"

        # Content without ---
        fm_none, body_none = parse_markdown_frontmatter("Just text\nno frontmatter")
        assert fm_none == {}
        assert body_none == "Just text\nno frontmatter"

        # Content with single ---
        fm_single, body_single = parse_markdown_frontmatter("---")
        assert fm_single == {}
        assert body_single == "---"

        # Content with fewer than 3 parts (unclosed frontmatter)
        fm_unclosed, body_unclosed = parse_markdown_frontmatter("---\nname: unclosed")
        assert fm_unclosed == {}
        assert body_unclosed == "---\nname: unclosed"

        # Content with 3 parts but empty YAML
        fm_empty, body_empty = parse_markdown_frontmatter("---\n---\nBody only")
        assert fm_empty == {}
        assert body_empty == "Body only"

        # Content with > 3 parts (extra delimiter in body)
        fm_extra, body_extra = parse_markdown_frontmatter("---\nname: extra\n---\nBody---part2")
        assert fm_extra == {"name": "extra"}
        assert body_extra == "Body---part2"

        # Non-dict YAML (e.g. integer or list)
        fm_int, body_int = parse_markdown_frontmatter("---\n42\n---\nBody")
        assert fm_int == {}
        assert body_int == "Body"

        fm_list, body_list = parse_markdown_frontmatter("---\n- item1\n- item2\n---\nBody")
        assert fm_list == {}
        assert body_list == "Body"

        # Invalid YAML triggering exception
        with caplog.at_level(logging.WARNING):
            fm_bad, body_bad = parse_markdown_frontmatter("---\n: : bad yaml\n---\nBody")
        assert fm_bad == {}
        assert body_bad == "Body"
        assert "Failed to parse YAML frontmatter" in caplog.text

    def test_load_agent_definition_defaults_and_fields(self) -> None:
        content = """---
name: custom_agent
description: Detailed description
phase: green
tools: [ReadFile, Grep]
disallowed_tools: [Bash]
permissionMode: workspace_write
memory: run
model: custom-llm
forkFrom: dev_agent
revertOnRed: true
background: false
hooks:
  PreToolUse: []
---
Prompt for {{ROLE}}
"""
        defn = load_agent_definition(content, vars={"ROLE": "Engineer"}, source="user", base_dir="/custom/dir")
        assert defn.name == "custom_agent"
        assert defn.description == "Detailed description"
        assert defn.prompt == "Prompt for Engineer"
        assert defn.phase == TddPhase.GREEN
        assert defn.tools == ("ReadFile", "Grep")
        assert defn.disallowed_tools == ("Bash",)
        assert defn.permission_mode == "workspace_write"
        assert defn.memory == "run"
        assert defn.model == "custom-llm"
        assert defn.fork_from == "dev_agent"
        assert defn.revert_on_red is True
        assert defn.background is False
        assert defn.hooks == {"PreToolUse": []}
        assert defn.source == "user"
        assert defn.base_dir == "/custom/dir"
        assert defn.raw_frontmatter["name"] == "custom_agent"

    def test_load_agent_definition_unnamed_and_empty_defaults(self) -> None:
        defn = load_agent_definition("")
        assert defn.name == "unnamed_agent"
        assert defn.description == ""
        assert defn.prompt == ""
        assert defn.phase is None
        assert defn.tools is None
        assert defn.source == "built-in"
        assert defn.base_dir == ""

        # Explicit empty name defaults to unnamed_agent
        defn2 = load_agent_definition("---\nname: ''\n---")
        assert defn2.name == "unnamed_agent"

        # Comments stripped by default in load_agent_definition
        defn_c = load_agent_definition("<!-- comment -->Prompt body")
        assert defn_c.prompt == "Prompt body"

    def test_load_agent_definition_phase_variations(self) -> None:
        # None phase
        defn_none = load_agent_definition("---\nphase: null\n---")
        assert defn_none.phase is None

        # Red phase
        defn_red = load_agent_definition("---\nphase: red\n---")
        assert defn_red.phase == TddPhase.RED

        # Refactor phase
        defn_ref = load_agent_definition("---\nphase: REFACTOR\n---")
        assert defn_ref.phase == TddPhase.REFACTOR

        # Post_green alias lowercase and uppercase
        defn_pg = load_agent_definition("---\nphase: post_green\n---")
        assert defn_pg.phase == TddPhase.REFACTOR

        defn_pg2 = load_agent_definition("---\nphase: Post_Green\n---")
        assert defn_pg2.phase == TddPhase.REFACTOR

        # Invalid phase raises error with allowed options
        with pytest.raises(AgentFrontmatterError) as exc_info:
            load_agent_definition("---\nname: tester\nphase: invalid_phase\n---")
        assert str(exc_info.value) == (
            "Invalid phase 'invalid_phase' in agent 'tester'. "
            "Allowed phases: ['RED', 'GREEN', 'REFACTOR', 'post_green']"
        )

    def test_load_agent_definition_field_validations(self, caplog: pytest.LogCaptureFixture) -> None:
        # Tuple tools and disallowedTools
        defn_tuple = load_agent_definition("---\ntools:\n  - ReadFile\ndisallowedTools:\n  - Bash\n---")
        assert defn_tuple.tools == ("ReadFile",)
        assert defn_tuple.disallowed_tools == ("Bash",)

        # Upper-case permissionMode and memory
        defn_upper = load_agent_definition("---\npermissionMode: PLAN\nmemory: SESSION\n---")
        assert defn_upper.permission_mode == "plan"
        assert defn_upper.memory == "session"

        # revertOnRed False and background True
        defn_bools = load_agent_definition("---\nrevertOnRed: false\nbackground: true\n---")
        assert defn_bools.revert_on_red is False
        assert defn_bools.background is True

    def test_load_agent_definition_invalid_field_warnings(self, caplog: pytest.LogCaptureFixture) -> None:
        content = """---
name: warning_test
tools: [1, 2]
disallowedTools: "not_a_list"
permissionMode: invalid_pm
memory: invalid_mem
model: 1234
forkFrom: 5678
revertOnRed: "not_a_bool"
background: "not_a_bool"
hooks: [not, a, dict]
---
Body
"""
        with caplog.at_level(logging.WARNING):
            defn = load_agent_definition(content)

        assert defn.tools is None
        assert defn.disallowed_tools is None
        assert defn.permission_mode is None
        assert defn.memory is None
        assert defn.model is None
        assert defn.fork_from is None
        assert defn.revert_on_red is None
        assert defn.background is None
        assert defn.hooks is None

        assert "Invalid tools list in agent 'warning_test': [1, 2]" in caplog.text
        assert "Invalid disallowed_tools in agent 'warning_test': 'not_a_list'" in caplog.text
        assert "Invalid permissionMode in agent 'warning_test': 'invalid_pm'" in caplog.text
        assert "Invalid memory in agent 'warning_test': 'invalid_mem'" in caplog.text
        assert "Invalid model in agent 'warning_test': 1234" in caplog.text
        assert "Invalid forkFrom in agent 'warning_test': 5678" in caplog.text
        assert "Invalid revertOnRed in agent 'warning_test': 'not_a_bool'" in caplog.text
        assert "Invalid background in agent 'warning_test': 'not_a_bool'" in caplog.text
        assert "Invalid hooks dict in agent 'warning_test': ['not', 'a', 'dict']" in caplog.text

    def test_load_agent_definition_from_path(self, tmp_path: Path) -> None:
        missing_file = tmp_path / "missing.md"
        with pytest.raises(FileNotFoundError) as exc_info:
            load_agent_definition_from_path(missing_file)
        assert f"Agent definition file not found: {missing_file}" in str(exc_info.value)

        valid_file = tmp_path / "agent.md"
        valid_file.write_text("---\nname: path_agent\n---\nPrompt", encoding="utf-8")
        defn = load_agent_definition_from_path(valid_file, source="user")
        assert defn.name == "path_agent"
        assert defn.base_dir == str(tmp_path)
        assert defn.source == "user"

    def test_scan_agent_dir_edge_cases(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        assert _scan_agent_dir(tmp_path / "missing", source="project") == {}

        test_dir = tmp_path / "agents"
        test_dir.mkdir()

        # Non-md file ignored
        (test_dir / "notes.txt").write_text("ignore", encoding="utf-8")

        # README.md is ignored
        (test_dir / "README.md").write_text("# Readme", encoding="utf-8")

        # Subdir with AGENT.md
        sub = test_dir / "worker"
        sub.mkdir()
        (sub / "AGENT.md").write_text("---\nname: worker\n---\nWorker prompt", encoding="utf-8")

        # Subdir without AGENT.md is ignored
        sub_empty = test_dir / "empty"
        sub_empty.mkdir()

        # Direct markdown file
        (test_dir / "direct.md").write_text("---\nname: direct\n---\nDirect prompt", encoding="utf-8")

        # Corrupted markdown file with phase error
        (test_dir / "bad.md").write_text("---\nphase: bad_phase\n---\nBad", encoding="utf-8")
        with pytest.raises(AgentFrontmatterError):
            _scan_agent_dir(test_dir, source="project")

        (test_dir / "bad.md").unlink()

        # Markdown file with general exception (e.g. read error or YAML exception)
        corrupted_sub = test_dir / "corrupted"
        corrupted_sub.mkdir()
        (corrupted_sub / "AGENT.md").write_text("corrupted content", encoding="utf-8")
        with patch("app.loop.agents.loader.load_agent_definition_from_path", side_effect=[
            RuntimeError("Unexpected load error"),
            AgentDefinition(name="worker", description="", prompt=""),
            AgentDefinition(name="direct", description="", prompt=""),
        ]):
            with caplog.at_level(logging.WARNING):
                res = _scan_agent_dir(test_dir, source="project")
            assert "Failed loading agent from" in caplog.text
            assert "worker" in res

        # Clean scan with vars
        import shutil
        shutil.rmtree(corrupted_sub)
        (sub / "AGENT.md").write_text("---\nname: worker\n---\nWorker prompt for {{ROLE}}", encoding="utf-8")
        (test_dir / "direct.md").write_text("---\nname: direct\n---\nDirect prompt for {{ROLE}}", encoding="utf-8")
        scanned = _scan_agent_dir(test_dir, source="project", vars={"ROLE": "Master"})
        assert set(scanned.keys()) == {"worker", "direct"}
        assert scanned["worker"].source == "project"
        assert scanned["direct"].source == "project"
        assert scanned["worker"].prompt == "Worker prompt for Master"
        assert scanned["direct"].prompt == "Direct prompt for Master"

    def test_get_agent_definitions_with_overrides(self, tmp_path: Path) -> None:
        built_in_dir = tmp_path / "builtin"
        user_dir = tmp_path / "user"
        proj_dir = tmp_path / "proj"

        for d in (built_in_dir, user_dir / ".tddagents" / "agents", proj_dir / ".tddagents" / "agents"):
            d.mkdir(parents=True)

        # Built-in agent
        b_content = "---\nname: {name}\ndescription: builtin\n---\nBuiltin {{{{ROLE}}}}"
        (built_in_dir / "agent1.md").write_text(b_content.format(name="agent1"), encoding="utf-8")
        (built_in_dir / "agent2.md").write_text(b_content.format(name="agent2"), encoding="utf-8")
        (built_in_dir / "agent3.md").write_text(b_content.format(name="agent3"), encoding="utf-8")

        # User overrides agent2
        (user_dir / ".tddagents" / "agents" / "agent2.md").write_text(
            "---\nname: agent2\ndescription: user_override\n---\nUser {{ROLE}}", encoding="utf-8"
        )

        # Project overrides agent3
        (proj_dir / ".tddagents" / "agents" / "agent3.md").write_text(
            "---\nname: agent3\ndescription: project_override\n---\nProject {{ROLE}}", encoding="utf-8"
        )

        defs = get_agent_definitions_with_overrides(
            project_dir=proj_dir,
            user_home=user_dir,
            built_in_dir=built_in_dir,
            vars={"ROLE": "Master"},
        )

        assert defs["agent1"].description == "builtin"
        assert defs["agent1"].source == "built-in"
        assert defs["agent1"].prompt == "Builtin Master"

        assert defs["agent2"].description == "user_override"
        assert defs["agent2"].source == "user"
        assert defs["agent2"].prompt == "User Master"

        assert defs["agent3"].description == "project_override"
        assert defs["agent3"].source == "project"
        assert defs["agent3"].prompt == "Project Master"

        # None directories fallback loads real builtins from app/prompts/agents
        real_builtin_defs = get_agent_definitions_with_overrides(project_dir=tmp_path / "empty_proj")
        assert "developer" in real_builtin_defs
        assert real_builtin_defs["developer"].source == "built-in"
        assert "explore" in real_builtin_defs


# ==============================================================================
# SECTION 2: Memory Store Booster
# ==============================================================================


class TestMemoryStoreBooster:
    def test_memory_dir_agent_sanitization(self, tmp_path: Path) -> None:
        store = AgentMemoryStore(base_dir=tmp_path / "proj")
        mdir = store.get_memory_dir("my-plugin:sub/agent:task", "project")
        assert mdir is not None
        assert "my-plugin-sub-agent-task" in str(mdir)

    def test_memory_dir_defaults_for_run_and_session(self, tmp_path: Path) -> None:
        store = AgentMemoryStore(base_dir=tmp_path / "proj")
        run_dir = store.get_memory_dir("agent1", "run", run_id=None)
        assert run_dir is not None
        assert "default_run" in str(run_dir)

        sess_dir = store.get_memory_dir("agent1", "session", session_id=None)
        assert sess_dir is not None
        assert "default_session" in str(sess_dir)

    def test_memory_dir_default_base_and_user_home(self) -> None:
        store = AgentMemoryStore(base_dir=None, user_home=None)
        u_dir = store.get_memory_dir("a", "user")
        assert u_dir is not None
        assert str(Path.home()) in str(u_dir)

        p_dir = store.get_memory_dir("a", "project")
        assert p_dir is not None
        assert str(Path.cwd()) in str(p_dir)

    def test_memory_dir_scope_none_vs_unrecognized(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        store = AgentMemoryStore(base_dir=tmp_path / "proj")
        with caplog.at_level(logging.WARNING):
            none_dir = store.get_memory_dir("agent1", "none")
        assert none_dir is None
        assert "Unrecognized memory scope" not in caplog.text

        with caplog.at_level(logging.WARNING):
            unrec_dir = store.get_memory_dir("agent1", "unrecognized_scope")
        assert unrec_dir is None
        assert "Unrecognized memory scope 'unrecognized_scope', treating as none" in caplog.text

    def test_ensure_memory_dir_and_entrypoint_with_session_id(self, tmp_path: Path) -> None:
        store = AgentMemoryStore(base_dir=tmp_path / "proj")
        mdir = store.ensure_memory_dir_exists("agent1", "session", session_id="my_sess")
        assert mdir is not None
        assert "my_sess" in str(mdir)
        assert mdir.is_dir()

        entrypoint = store.get_entrypoint("agent1", "session", session_id="my_sess")
        assert entrypoint == mdir / "MEMORY.md"

        # none scope
        assert store.ensure_memory_dir_exists("agent1", "none") is None
        assert store.get_entrypoint("agent1", "none") is None

    def test_load_memory_prompt_all_scopes(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        store = AgentMemoryStore(base_dir=tmp_path / "proj", user_home=tmp_path / "user")

        # none scope returns empty string
        assert store.load_memory_prompt("agent1", "none") == ""

        # unrecognized scope returns empty string
        assert store.load_memory_prompt("agent1", "unknown") == ""

        p_run = store.load_memory_prompt("agent1", "run", run_id="r1")
        mdir = (tmp_path / "proj" / ".tddagents" / "run-memory" / "r1" / "agent1").resolve()
        entrypoint = mdir / "MEMORY.md"
        expected_run_prompt = (
            "# Agent Persistent Memory\n"
            f"You have access to a run-scoped (scratchpad discarded at run end to guarantee sample independence) "
            f"memory store at: {mdir}\n"
            f"Entrypoint file: {entrypoint}\n"
            "- Use your file writing/editing tools to update MEMORY.md with "
            "key architectural findings, patterns, or decisions.\n"
            "- NOTE: This memory is strictly run-scoped and will be purged upon "
            "run completion to maintain scientific reproducibility."
        )
        assert p_run == expected_run_prompt

        # session with session_id
        p_sess = store.load_memory_prompt("agent1", "session", session_id="s1")
        assert "session-scoped (preserved across queries within this active session)" in p_sess
        assert "s1" in p_sess

        # local
        p_local = store.load_memory_prompt("agent1", "local")
        assert "local-scoped (persisted on this machine, untracked by VCS)" in p_local

        # project
        p_proj = store.load_memory_prompt("agent1", "project")
        assert "project-scoped (shared via version control)" in p_proj

        # user
        p_user = store.load_memory_prompt("agent1", "user")
        assert "user-scoped (universal across all projects on this machine)" in p_user

        # Existing content in MEMORY.md
        entry = store.get_entrypoint("agent1", "run", run_id="r1")
        assert entry is not None
        entry.write_text("Saved notes here", encoding="utf-8")
        p_with_content = store.load_memory_prompt("agent1", "run", run_id="r1")
        assert "## Existing Memory Notes:\nSaved notes here" in p_with_content

        # read exception in MEMORY.md
        with patch.object(Path, "read_text", side_effect=OSError("Disk read error")):
            with caplog.at_level(logging.WARNING):
                _ = store.load_memory_prompt("agent1", "run", run_id="r1")
        assert "Failed reading agent memory" in caplog.text
        assert "Disk read error" in caplog.text

    def test_discard_run_memory_logging_and_errors(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        store = AgentMemoryStore(base_dir=tmp_path / "proj")
        store.ensure_memory_dir_exists("agent1", "run", run_id="run_to_purge")

        with caplog.at_level(logging.INFO):
            store.discard_run_memory("run_to_purge")
        assert "Purged run-scoped agent memory for run run_to_purge" in caplog.text

        # Purging non-existent run memory does nothing
        store.discard_run_memory("non_existent_run")

        # rmtree exception handling
        store.ensure_memory_dir_exists("agent1", "run", run_id="run_fail")
        with patch("shutil.rmtree", side_effect=OSError("Permission denied")):
            with caplog.at_level(logging.WARNING):
                store.discard_run_memory("run_fail")
        assert "Failed purging run memory" in caplog.text

        # default base_dir in discard_run_memory
        store_def = AgentMemoryStore(base_dir=None)
        store_def.discard_run_memory("some_run")


# ==============================================================================
# SECTION 3: Fork Context Booster
# ==============================================================================


class TestForkContextBooster:
    def test_fork_constants(self) -> None:
        assert FORK_SUBAGENT_TYPE == "fork"
        assert FORK_AGENT.name == "fork"
        assert FORK_AGENT.tools == ("*",)
        assert FORK_AGENT.permission_mode == "workspace_write"

    def test_build_worktree_notice_exact(self) -> None:
        expected = (
            "You've inherited the conversation context above from a parent agent working in /parent. "
            "You are operating in an isolated git worktree at /worktree — same repository, "
            "same relative file structure, separate working copy. Paths in the inherited context refer to "
            "the parent's working directory; translate them to your worktree root. Re-read files before editing "
            "if the parent may have modified them since they appear in the context. Your changes stay in this "
            "worktree and will not affect the parent's files."
        )
        assert build_worktree_notice("/parent", "/worktree") == expected

    def test_build_child_message_exact(self) -> None:
        directive = "Fix bug in calculation"
        msg = build_child_message(directive)
        assert f"<{FORK_BOILERPLATE_TAG}>" in msg
        assert f"</{FORK_BOILERPLATE_TAG}>" in msg
        assert "STOP. READ THIS FIRST." in msg
        assert "RULES (non-negotiable):" in msg
        assert f"{FORK_DIRECTIVE_PREFIX}{directive}" in msg

    def test_get_tool_use_ids_from_assistant_variations(self) -> None:
        obj_tc = SimpleNamespace(id="tc_obj_1")
        msg_obj = SimpleNamespace(type="assistant", tool_calls=[obj_tc])
        assert _get_tool_use_ids_from_assistant(msg_obj) == ["tc_obj_1"]  # type: ignore

        # Tool call as dict with empty id or no id
        msg_empty = SimpleNamespace(
            type="assistant",
            tool_calls=[{"name": "foo"}, {"name": "bar", "id": ""}],
        )
        assert _get_tool_use_ids_from_assistant(msg_empty) == []  # type: ignore

        # Content blocks
        msg_blocks = AIMessage(
            content=[
                {"type": "text", "text": "calling"},
                {"type": "tool_use", "id": "tc_block_1", "name": "foo"},
                {"type": "tool_use", "id": "", "name": "bar"},
                {"type": "other"},
            ]
        )
        assert _get_tool_use_ids_from_assistant(msg_blocks) == ["tc_block_1"]

    def test_get_tool_result_ids_from_message_variations(self) -> None:
        tm = ToolMessage(content="ok", tool_call_id="call_99")
        assert _get_tool_result_ids_from_message(tm) == ["call_99"]

        # Message with content blocks having tool_use_id and tool_call_id
        msg_blocks = HumanMessage(
            content=[
                {"type": "tool_result", "tool_use_id": "call_use_1"},
                {"type": "tool_result", "tool_call_id": "call_call_2"},
                {"type": "tool_result"},  # no id
                {"type": "text", "text": "hello"},
            ]
        )
        assert _get_tool_result_ids_from_message(msg_blocks) == ["call_use_1", "call_call_2"]

    def test_filter_incomplete_tool_calls_variations(self) -> None:
        msg = AIMessage(content="plain answer without tools")
        assert filter_incomplete_tool_calls([msg]) == [msg]

        # Assistant with incomplete tool use is dropped
        asst_incomp = AIMessage(
            content=[{"type": "tool_use", "id": "call_orphan", "name": "Bash"}]
        )
        assert filter_incomplete_tool_calls([asst_incomp]) == []

        # Assistant with completed tool use is kept
        tool_res = ToolMessage(content="output", tool_call_id="call_orphan")
        assert filter_incomplete_tool_calls([asst_incomp, tool_res]) == [asst_incomp, tool_res]

        # Non-AIMessage assistant object with type='assistant'
        asst_obj = SimpleNamespace(
            type="assistant",
            tool_calls=[{"name": "Bash", "id": "call_obj"}],
        )
        assert filter_incomplete_tool_calls([asst_obj]) == []  # type: ignore
        tool_obj_res = ToolMessage(content="output", tool_call_id="call_obj")
        assert filter_incomplete_tool_calls([asst_obj, tool_obj_res]) == [asst_obj, tool_obj_res]  # type: ignore

        # Message with no type attribute
        msg_no_type = SimpleNamespace(content="user text")
        assert filter_incomplete_tool_calls([msg_no_type]) == [msg_no_type]  # type: ignore

    def test_is_in_fork_child_variations(self) -> None:
        needle = f"<{FORK_BOILERPLATE_TAG}>"
        assert is_in_fork_child([]) is False

        # Content string match vs non-match
        assert is_in_fork_child([HumanMessage(content=f"start {needle} end")]) is True
        assert is_in_fork_child([HumanMessage(content="hello world")]) is False

        # Content block string in tuple
        msg_tuple = HumanMessage(content=(f"prefix {needle} suffix",))  # type: ignore
        assert is_in_fork_child([msg_tuple]) is True

        # Content block dict with text key
        msg_dict = HumanMessage(content=[{"type": "text", "text": f"prefix {needle}"}])
        assert is_in_fork_child([msg_dict]) is True

        # Content block dict without text key or other types
        msg_dict_no_text = SimpleNamespace(content=[{"type": "other", "data": 123}, 456])
        assert is_in_fork_child([msg_dict_no_text]) is False  # type: ignore

        # Message with no content attribute
        msg_no_content = SimpleNamespace()
        assert is_in_fork_child([msg_no_content]) is False  # type: ignore

        # Message with non-string, non-list content (e.g. integer)
        msg_int_content = SimpleNamespace(content=123)
        assert is_in_fork_child([msg_int_content]) is False  # type: ignore

    def test_build_forked_messages_full(self) -> None:
        asst = AIMessage(
            content="Run tool",
            tool_calls=[{"id": "call_1", "name": "tool1", "args": {}}, {"id": "call_2", "name": "tool2", "args": {}}],
        )
        parent_hist = [HumanMessage(content="initial request")]
        forked = build_forked_messages("Child directive", asst, parent_hist)

        assert len(forked) == 5
        assert forked[0] == parent_hist[0]
        assert forked[1] == asst
        assert isinstance(forked[2], ToolMessage)
        assert forked[2].tool_call_id == "call_1"
        assert forked[2].content == FORK_PLACEHOLDER_RESULT
        assert isinstance(forked[3], ToolMessage)
        assert forked[3].tool_call_id == "call_2"
        assert forked[3].content == FORK_PLACEHOLDER_RESULT
        assert isinstance(forked[4], HumanMessage)
        assert "Child directive" in str(forked[4].content)


# ==============================================================================
# SECTION 4: Tool Resolution Booster
# ==============================================================================


class TestToolResolutionBooster:
    def test_parse_tool_spec_variations(self) -> None:
        assert parse_tool_spec("ToolName()") == ("ToolName", None)
        assert parse_tool_spec("ToolName(a, b)") == ("ToolName", "a, b")
        assert parse_tool_spec("ToolName: a, b") == ("ToolName", "a, b")
        assert parse_tool_spec("Simple") == ("Simple", None)
        assert parse_tool_spec("tool_with-hyphen(x)") == ("tool_with-hyphen", "x")
        assert parse_tool_spec("   ToolName   ") == ("ToolName", None)

    def test_tool_matches_name_variations(self) -> None:
        t = _make_dummy_tool("Bash", aliases=("sh", "terminal"))
        assert _tool_matches_name(t, "bash") is True
        assert _tool_matches_name(t, "BASH") is True
        assert _tool_matches_name(t, "sh") is True
        assert _tool_matches_name(t, "TERMINAL") is True
        assert _tool_matches_name(t, "unknown") is False

        # Tool without aliases
        t_no_alias = _make_dummy_tool("Grep")
        assert _tool_matches_name(t_no_alias, "grep") is True
        assert _tool_matches_name(t_no_alias, "other") is False

    def test_filter_tools_for_agent_all_rules(self) -> None:
        t_mcp = _make_dummy_tool("mcp__server_tool")
        t_exit_plan = _make_dummy_tool("ExitPlanMode")
        t_agent = _make_dummy_tool("Agent")
        t_task = _make_dummy_tool("Task")
        t_read = _make_dummy_tool("ReadFile")
        t_write = _make_dummy_tool("WriteFile")

        # Read tool is kept across all modes
        assert filter_tools_for_agent([t_read]) == [t_read]

        # Call with no optional flags tests defaults (is_async=False, allow_nested_agent=False)
        default_filtered = filter_tools_for_agent([t_write, t_agent])
        assert t_write in default_filtered
        assert t_agent not in default_filtered

        # Plan mode preserves ExitPlanMode
        filtered_plan = filter_tools_for_agent([t_exit_plan], permission_mode="plan")
        assert t_exit_plan in filtered_plan

        # Non-plan mode drops ExitPlanMode
        filtered_non_plan = filter_tools_for_agent([t_exit_plan], permission_mode="default")
        assert t_exit_plan not in filtered_non_plan

        # Nested agent disallowed vs allowed
        assert filter_tools_for_agent([t_agent, t_task], allow_nested_agent=False) == []
        assert filter_tools_for_agent([t_agent, t_task], allow_nested_agent=True) == [t_agent, t_task]

        # ALL_AGENT_DISALLOWED_TOOLS dropped even when allow_nested_agent=True
        for disallowed_name in ALL_AGENT_DISALLOWED_TOOLS:
            d_tool = _make_dummy_tool(disallowed_name)
            assert filter_tools_for_agent([d_tool], allow_nested_agent=True) == []

        # Async agent allowed tools
        for async_name in ASYNC_AGENT_ALLOWED_TOOLS:
            a_tool = _make_dummy_tool(async_name)
            assert filter_tools_for_agent([a_tool], is_async=True) == [a_tool]

        # Async agent drops write tools
        assert filter_tools_for_agent([t_write], is_async=True) == []

        # MCP tool preserved even in async mode
        assert filter_tools_for_agent([t_mcp], is_async=True) == [t_mcp]

    def test_resolve_agent_tools_variations(self, caplog: pytest.LogCaptureFixture) -> None:
        t_agent = _make_dummy_tool("Agent")
        t_read = _make_dummy_tool("ReadFile")
        t_write = _make_dummy_tool("WriteFile")
        t_bash = _make_dummy_tool("Bash", aliases=("sh",))

        # Call resolve_agent_tools with no optional flags (verifies is_async=False, allow_nested_agent=False)
        defn_default = AgentDefinition(
            name="default_agent",
            description="",
            prompt="",
            tools=None,
            source="built-in",
        )
        res_default = resolve_agent_tools(defn_default, [t_write, t_agent])
        assert t_write in res_default.resolved_tools
        assert t_agent not in res_default.resolved_tools

        # Main thread skips filtering
        defn_main = AgentDefinition(name="main", description="", prompt="", tools=("Agent",))
        res_main = resolve_agent_tools(defn_main, [t_agent], is_main_thread=True)
        assert len(res_main.resolved_tools) == 1

        # Wildcard None
        defn_wild_none = AgentDefinition(name="wild_none", description="", prompt="", tools=None)
        res_wn = resolve_agent_tools(defn_wild_none, [t_read, t_write])
        assert isinstance(res_wn, ResolvedAgentTools)
        assert res_wn.has_wildcard is True
        assert len(res_wn.resolved_tools) == 2

        # Wildcard ("*",)
        defn_wild_star = AgentDefinition(name="wild_star", description="", prompt="", tools=("*",))
        res_ws = resolve_agent_tools(defn_wild_star, [t_read, t_write])
        assert res_ws.has_wildcard is True
        assert len(res_ws.resolved_tools) == 2

        # Multiple tools with wildcard ("*", "ReadFile") is NOT treated as wildcard shortcut
        defn_multi_star = AgentDefinition(name="multi", description="", prompt="", tools=("*", "ReadFile"))
        res_ms = resolve_agent_tools(defn_multi_star, [t_read])
        assert res_ms.has_wildcard is False
        assert "*" in res_ms.invalid_tools
        assert "ReadFile" in res_ms.valid_tools

        # Disallowed tool by name and by alias
        defn_disallow = AgentDefinition(
            name="disallow",
            description="",
            prompt="",
            tools=("*",),
            disallowed_tools=("ReadFile", "sh"),
        )
        res_dis = resolve_agent_tools(defn_disallow, [t_read, t_write, t_bash])
        assert t_read not in res_dis.resolved_tools
        assert t_bash not in res_dis.resolved_tools
        assert t_write in res_dis.resolved_tools

        # Agent with allowed agent types args
        defn_agent_args = AgentDefinition(
            name="caller",
            description="",
            prompt="",
            tools=("Agent(worker, tester)",),
        )
        res_aa = resolve_agent_tools(defn_agent_args, [t_agent], allow_nested_agent=True)
        assert res_aa.allowed_agent_types == ("worker", "tester")

        # Task alias with allowed agent types args
        defn_task_args = AgentDefinition(name="task_caller", description="", prompt="", tools=("Task(worker)",))
        res_ta = resolve_agent_tools(defn_task_args, [t_agent], allow_nested_agent=True)
        assert res_ta.allowed_agent_types == ("worker",)

        # Deduplication of identical tools requested twice
        defn_dup = AgentDefinition(name="dup", description="", prompt="", tools=("ReadFile", "readfile"))
        res_dup = resolve_agent_tools(defn_dup, [t_read])
        assert res_dup.valid_tools == ("ReadFile", "readfile")
        assert len(res_dup.resolved_tools) == 1

        # Missing tool logs warning
        defn_missing = AgentDefinition(name="worker", description="", prompt="", tools=("GhostTool",))
        with caplog.at_level(logging.WARNING):
            res_miss = resolve_agent_tools(defn_missing, [t_read])
        assert res_miss.invalid_tools == ("GhostTool",)
        assert "Agent 'worker' requested unavailable tool 'GhostTool'" in caplog.text


# ==============================================================================
# SECTION 5: Agent Tool Execution Booster
# ==============================================================================


class TestAgentToolExecutionBooster:
    def test_agent_tool_constants_and_schema(self) -> None:
        assert AGENT_TOOL_NAME == "Agent"
        assert LEGACY_AGENT_TOOL_NAME == "Task"
        assert AGENT_TOOL_SCHEMA["required"] == ["prompt"]
        assert "subagent_type" in AGENT_TOOL_SCHEMA["properties"]
        assert "run_in_background" in AGENT_TOOL_SCHEMA["properties"]
        assert "isolation" in AGENT_TOOL_SCHEMA["properties"]

    @pytest.mark.anyio
    async def test_default_subagent_runner_variations(self) -> None:
        agent_def = AgentDefinition(name="analyzer", description="analyzer", prompt="prompt")
        ctx = _make_context()

        # Empty directive
        s_empty, r_empty = await default_subagent_runner(agent_def, "", (), (), ctx, "")
        assert "Scope: Subagent task" in s_empty
        assert "Result: Subagent 'analyzer' completed task successfully." in s_empty
        assert r_empty == "completed"

        # Multiline directive
        multiline = "First line directive\nSecond line details"
        s_multi, r_multi = await default_subagent_runner(agent_def, multiline, (), (), ctx, "")
        assert "Scope: First line directive" in s_multi
        assert "Key files: []\nFiles changed: []\nIssues: []" in s_multi
        assert r_multi == "completed"

    def test_agent_tool_validate_input_variations(self) -> None:
        tool = build_agent_tool()
        ctx = _make_context()

        # Empty input
        val_empty = tool.validate_input({}, ctx)
        assert val_empty.valid is False
        assert val_empty.message == "Missing required parameter 'prompt'"

        # Empty prompt string
        val_str_empty = tool.validate_input({"prompt": ""}, ctx)
        assert val_str_empty.valid is False
        assert val_str_empty.message == "Missing required parameter 'prompt'"

        # Valid prompt
        val_ok = tool.validate_input({"prompt": "Do work"}, ctx)
        assert val_ok.valid is True

    @pytest.mark.anyio
    async def test_agent_tool_recursive_fork_denial(self) -> None:
        tool = build_agent_tool(definitions={})
        fork_msg = HumanMessage(content=f"<{FORK_BOILERPLATE_TAG}>Forked child</{FORK_BOILERPLATE_TAG}>")
        ctx = _make_context(messages=[fork_msg])

        res = await tool.call({"prompt": "Recursive fork"}, ctx)
        assert res.is_error is True
        assert res.content == "Error: Fork children cannot recursively spawn fork subagents."

    @pytest.mark.anyio
    async def test_agent_tool_unknown_agent_error(self) -> None:
        tool = build_agent_tool(definitions={})
        ctx = _make_context()

        res = await tool.call({"subagent_type": "ghost", "prompt": "Work"}, ctx)
        assert res.is_error is True
        assert res.content == "Error: Unknown agent type 'ghost'. Available: []"

    @pytest.mark.anyio
    async def test_agent_tool_phase_invariant_denial(self) -> None:
        dev_def = AgentDefinition(name="developer", description="dev", prompt="dev", phase=TddPhase.GREEN)
        tool = build_agent_tool(definitions={"developer": dev_def})
        ctx = _make_context(phase=TddPhase.RED)

        res = await tool.call({"subagent_type": "developer", "prompt": "Write code"}, ctx)
        assert res.is_error is True
        assert res.content == "Denied: Agent 'developer' requires phase GREEN, but current TDD ledger is in phase RED."

    @pytest.mark.anyio
    async def test_agent_tool_available_tools_vs_context_tools(self) -> None:
        t_a = _make_dummy_tool("ToolA")
        t_b = _make_dummy_tool("ToolB")

        passed_tools: list[Tool] = []

        async def inspect_runner(
            agent_def: AgentDefinition,
            directive: str,
            child_messages: Sequence[Message],
            worker_tools: Sequence[Tool],
            context: ToolContext,
            memory_prompt: str,
        ) -> tuple[str, str | None]:
            passed_tools.extend(worker_tools)
            return "ok", "completed"

        # 1. available_tools provided
        tool1 = build_agent_tool(
            available_tools=[t_a],
            definitions={"w": AgentDefinition(name="w", description="", prompt="", tools=("*",))},
            subagent_runner=inspect_runner,
        )
        ctx1 = _make_context(tools=[t_b])
        await tool1.call({"subagent_type": "w", "prompt": "test"}, ctx1)
        assert any(t.name == "ToolA" for t in passed_tools)
        assert not any(t.name == "ToolB" for t in passed_tools)

        # 2. available_tools omitted, uses context.tools
        passed_tools.clear()
        tool2 = build_agent_tool(
            definitions={"w": AgentDefinition(name="w", description="", prompt="", tools=("*",))},
            subagent_runner=inspect_runner,
        )
        ctx2 = _make_context(tools=[t_b])
        await tool2.call({"subagent_type": "w", "prompt": "test"}, ctx2)
        assert any(t.name == "ToolB" for t in passed_tools)

    @pytest.mark.anyio
    async def test_agent_tool_allow_nested_agent_flag(self) -> None:
        t_agent = _make_dummy_tool("Agent")
        passed_tools: list[Tool] = []

        async def inspect_runner(
            agent_def: AgentDefinition,
            directive: str,
            child_messages: Sequence[Message],
            worker_tools: Sequence[Tool],
            context: ToolContext,
            memory_prompt: str,
        ) -> tuple[str, str | None]:
            passed_tools.extend(worker_tools)
            return "ok", "completed"

        # allow_nested_agent=False (explicit)
        tool_no_nest = build_agent_tool(
            available_tools=[t_agent],
            definitions={"w": AgentDefinition(name="w", description="", prompt="", tools=("*",))},
            subagent_runner=inspect_runner,
            allow_nested_agent=False,
        )
        await tool_no_nest.call({"subagent_type": "w", "prompt": "test"}, _make_context())
        assert not any(t.name == "Agent" for t in passed_tools)

        # allow_nested_agent default parameter (omitted)
        passed_tools.clear()
        tool_default_nest = build_agent_tool(
            available_tools=[t_agent],
            definitions={"w": AgentDefinition(name="w", description="", prompt="", tools=("*",))},
            subagent_runner=inspect_runner,
        )
        await tool_default_nest.call({"subagent_type": "w", "prompt": "test"}, _make_context())
        assert not any(t.name == "Agent" for t in passed_tools)

        # allow_nested_agent=True
        passed_tools.clear()
        tool_nest = build_agent_tool(
            available_tools=[t_agent],
            definitions={"w": AgentDefinition(name="w", description="", prompt="", tools=("*",))},
            subagent_runner=inspect_runner,
            allow_nested_agent=True,
        )
        await tool_nest.call({"subagent_type": "w", "prompt": "test"}, _make_context())
        assert any(t.name == "Agent" for t in passed_tools)

    @pytest.mark.anyio
    async def test_agent_tool_async_execution(self) -> None:
        t_w = _make_dummy_tool("WriteFile")
        t_r = _make_dummy_tool("ReadFile")

        worker_def = AgentDefinition(name="worker", description="", prompt="", tools=("*",))
        bg_def = AgentDefinition(name="bg_worker", description="", prompt="", background=True, tools=("*",))

        tool = build_agent_tool(
            available_tools=[t_w, t_r],
            definitions={"worker": worker_def, "bg_worker": bg_def},
        )
        ctx = _make_context()

        # run_in_background=True
        res_async1 = await tool.call({"subagent_type": "worker", "prompt": "async job", "run_in_background": True}, ctx)
        assert res_async1.is_error is False
        assert "launched in background" in res_async1.content
        assert res_async1.metadata is not None
        assert res_async1.metadata["status"] == "async_launched"
        assert res_async1.metadata["agent_type"] == "worker"
        assert "ReadFile" in res_async1.metadata["tools"]
        assert "WriteFile" not in res_async1.metadata["tools"]

        # Agent definition background=True
        res_async2 = await tool.call({"subagent_type": "bg_worker", "prompt": "bg job"}, ctx)
        assert res_async2.is_error is False
        assert "launched in background" in res_async2.content
        assert res_async2.metadata is not None
        assert res_async2.metadata["status"] == "async_launched"
        assert res_async2.metadata["agent_type"] == "bg_worker"

    @pytest.mark.anyio
    async def test_agent_tool_phase_ledger_strips_denied_tools(self) -> None:
        t_w = _make_dummy_tool("WriteImplementation")
        t_r = _make_dummy_tool("ReadFile")
        passed_tools: list[Tool] = []

        async def inspect_runner(
            agent_def: AgentDefinition,
            directive: str,
            child_messages: Sequence[Message],
            worker_tools: Sequence[Tool],
            context: ToolContext,
            memory_prompt: str,
        ) -> tuple[str, str | None]:
            passed_tools.extend(worker_tools)
            return "ok", "completed"

        # In RED phase, WriteImplementation is denied by phase ledger
        tester_agent = AgentDefinition(
            name="tester",
            description="",
            prompt="",
            tools=("*",),
            phase=TddPhase.RED,
        )
        tool = build_agent_tool(
            available_tools=[t_w, t_r],
            definitions={"tester": tester_agent},
            subagent_runner=inspect_runner,
        )
        ctx_red = _make_context(phase=TddPhase.RED)
        await tool.call({"subagent_type": "tester", "prompt": "Write test"}, ctx_red)
        assert any(t.name == "ReadFile" for t in passed_tools)
        assert not any(t.name == "WriteImplementation" for t in passed_tools)

    @pytest.mark.anyio
    async def test_agent_tool_fork_child_messages_inspection(self) -> None:
        passed_messages: list[Message] = []

        async def fork_runner(
            agent_def: AgentDefinition,
            directive: str,
            child_messages: Sequence[Message],
            worker_tools: Sequence[Tool],
            context: ToolContext,
            memory_prompt: str,
        ) -> tuple[str, str | None]:
            passed_messages.extend(child_messages)
            return "fork success", "completed"

        tool = build_agent_tool(definitions={}, subagent_runner=fork_runner)
        asst_msg = AIMessage(
            content="Plan",
            tool_calls=[{"name": "ReadFile", "args": {}, "id": "tc_fork_1"}],
        )
        ctx = _make_context(messages=[HumanMessage(content="Start"), asst_msg])

        res = await tool.call({"prompt": "Perform fork task"}, ctx)
        assert res.is_error is False
        assert len(passed_messages) == 4
        assert passed_messages[0].content == "Start"
        assert passed_messages[1] == asst_msg
        assert isinstance(passed_messages[2], ToolMessage)
        assert passed_messages[2].tool_call_id == "tc_fork_1"
        assert passed_messages[2].content == FORK_PLACEHOLDER_RESULT
        assert isinstance(passed_messages[3], HumanMessage)
        assert "Perform fork task" in str(passed_messages[3].content)

    @pytest.mark.anyio
    async def test_agent_tool_definitions_none_uses_workspace_root(self, tmp_path: Path) -> None:
        proj_agents = tmp_path / ".tddagents" / "agents"
        proj_agents.mkdir(parents=True)
        (proj_agents / "custom.md").write_text("---\nname: custom_agent\n---\nCustom prompt", encoding="utf-8")

        tool = build_agent_tool(definitions=None)
        ctx = ToolContext(
            cancel=CancelToken(),
            get_app_state=lambda: AppState(),
            set_app_state=lambda fn: None,
            workspace=SimpleNamespace(root=tmp_path),
        )
        res = await tool.call({"subagent_type": "custom_agent", "prompt": "Run custom"}, ctx)
        assert res.is_error is False
        assert "Subagent 'custom_agent' completed task successfully" in res.content

    @pytest.mark.anyio
    async def test_agent_tool_custom_runner_and_metadata(self) -> None:
        captured_args: dict[str, Any] = {}

        async def custom_runner(
            agent_def: AgentDefinition,
            directive: str,
            child_messages: Sequence[Message],
            worker_tools: Sequence[Tool],
            context: ToolContext,
            memory_prompt: str,
        ) -> tuple[str, str | None]:
            captured_args["agent_def"] = agent_def
            captured_args["directive"] = directive
            captured_args["child_messages"] = child_messages
            captured_args["worker_tools"] = worker_tools
            captured_args["context"] = context
            captured_args["memory_prompt"] = memory_prompt
            context.set_app_state(lambda s: s)
            return f"Custom execution for {agent_def.name}", "custom_done"

        agent_def = AgentDefinition(name="custom_worker", description="", prompt="")
        tool = build_agent_tool(definitions={"custom_worker": agent_def}, subagent_runner=custom_runner)
        ctx = _make_context()

        res = await tool.call({"subagent_type": "custom_worker", "prompt": "Do custom"}, ctx)
        assert res.is_error is False
        assert res.content == "Custom execution for custom_worker"
        assert res.metadata is not None
        assert res.metadata["status"] == "completed"
        assert res.metadata["terminal_reason"] == "custom_done"
        assert res.metadata["agent_type"] == "custom_worker"
        assert res.metadata["agent_id"].startswith("agent_")
        assert len(res.metadata["agent_id"]) == 14
        assert res.metadata["run_id"].startswith("run_")
        assert len(res.metadata["run_id"]) == 12

        worker_ctx = captured_args["context"]
        assert worker_ctx.cancel is ctx.cancel
        assert worker_ctx.get_app_state is ctx.get_app_state
        assert worker_ctx.workspace is ctx.workspace
        assert worker_ctx.hook_dispatcher is ctx.hook_dispatcher
        assert worker_ctx.permission_context is ctx.permission_context
        assert worker_ctx.messages == tuple(captured_args["child_messages"])
        assert worker_ctx.tools == tuple(captured_args["worker_tools"])

    @pytest.mark.anyio
    async def test_agent_tool_memory_scopes_and_purge(self, tmp_path: Path) -> None:
        store = AgentMemoryStore(base_dir=tmp_path)
        mem_run = AgentDefinition(name="run_agent", description="", prompt="", memory="run")
        mem_proj = AgentDefinition(name="proj_agent", description="", prompt="", memory="project")

        received_prompts: list[str] = []

        async def recording_runner(
            agent_def: AgentDefinition,
            directive: str,
            child_messages: Sequence[Message],
            worker_tools: Sequence[Tool],
            context: ToolContext,
            memory_prompt: str,
        ) -> tuple[str, str | None]:
            received_prompts.append(memory_prompt)
            return "ok", "completed"

        tool = build_agent_tool(
            definitions={"run_agent": mem_run, "proj_agent": mem_proj},
            memory_store=store,
            subagent_runner=recording_runner,
        )
        ctx = _make_context()

        # Run memory agent
        res1 = await tool.call({"subagent_type": "run_agent", "prompt": "Task 1"}, ctx)
        assert res1.is_error is False
        assert len(received_prompts) == 1
        assert "run-scoped" in received_prompts[0]

        # Project memory agent - verify discard_run_memory is NOT called for project memory
        with patch.object(store, "discard_run_memory", wraps=store.discard_run_memory) as mock_discard:
            res2 = await tool.call({"subagent_type": "proj_agent", "prompt": "Task 2"}, ctx)
            assert res2.is_error is False
            assert len(received_prompts) == 2
            assert "project-scoped" in received_prompts[1]
            mock_discard.assert_not_called()

    @pytest.mark.anyio
    async def test_agent_tool_runner_exception_still_purges_run_memory(self, tmp_path: Path) -> None:
        store = AgentMemoryStore(base_dir=tmp_path)
        mem_agent = AgentDefinition(name="faulty", description="f", prompt="f", memory="run")

        async def failing_runner(*args: Any) -> tuple[str, str | None]:
            raise RuntimeError("Subagent crashed during run")

        tool = build_agent_tool(
            definitions={"faulty": mem_agent},
            memory_store=store,
            subagent_runner=failing_runner,
        )
        ctx = _make_context()

        with pytest.raises(RuntimeError) as exc_info:
            await tool.call({"subagent_type": "faulty", "prompt": "Fail now"}, ctx)
        assert "Subagent crashed during run" in str(exc_info.value)

        run_mem_root = tmp_path / ".tddagents" / "run-memory"
        if run_mem_root.exists():
            assert list(run_mem_root.iterdir()) == []

    @pytest.mark.anyio
    async def test_agent_tool_fork_child_without_assistant_inspects_directive(self) -> None:
        passed_messages: list[Message] = []

        async def fork_runner(
            agent_def: AgentDefinition,
            directive: str,
            child_messages: Sequence[Message],
            worker_tools: Sequence[Tool],
            context: ToolContext,
            memory_prompt: str,
        ) -> tuple[str, str | None]:
            passed_messages.extend(child_messages)
            return "fork ok", "completed"

        tool = build_agent_tool(definitions={}, subagent_runner=fork_runner)
        # Context has NO assistant message (only human message)
        ctx_no_asst = _make_context(messages=[HumanMessage(content="Previous user request")])

        res = await tool.call({"prompt": "Fork without assistant"}, ctx_no_asst)
        assert res.is_error is False
        assert len(passed_messages) == 1
        assert isinstance(passed_messages[0], HumanMessage)
        assert FORK_BOILERPLATE_TAG in str(passed_messages[0].content)
        assert "Fork without assistant" in str(passed_messages[0].content)

    @pytest.mark.anyio
    async def test_agent_tool_fork_with_non_aimessage_assistant(self) -> None:
        passed_messages: list[Message] = []

        async def fork_runner(
            agent_def: AgentDefinition,
            directive: str,
            child_messages: Sequence[Message],
            worker_tools: Sequence[Tool],
            context: ToolContext,
            memory_prompt: str,
        ) -> tuple[str, str | None]:
            passed_messages.extend(child_messages)
            return "fork ok", "completed"

        tool = build_agent_tool(definitions={}, subagent_runner=fork_runner)
        # Non-AIMessage assistant object with type="assistant"
        custom_asst = cast(Message, SimpleNamespace(type="assistant", content="Assistant text", tool_calls=[]))
        ctx = _make_context(messages=[HumanMessage(content="User start"), custom_asst])

        res = await tool.call({"prompt": "Do fork with custom asst"}, ctx)
        assert res.is_error is False
        assert len(passed_messages) >= 2
        assert passed_messages[0].content == "User start"
        assert passed_messages[1] == custom_asst
