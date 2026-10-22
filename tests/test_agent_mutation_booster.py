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

import atexit
import logging
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
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


#: One run-memory root for this module's contexts, removed when the test process exits.
_MEMORY_ROOT = tempfile.mkdtemp(prefix="agent-mem-")
atexit.register(shutil.rmtree, _MEMORY_ROOT, True)


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
        run_id="loop-test",
        agent_memory_store=AgentMemoryStore(base_dir=_MEMORY_ROOT),
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
disallowedTools: [Bash]
permissionMode: acceptEdits
memory: run
model: custom-llm
background: false
hooks:
  PreToolUse: []
---
Prompt for {{ROLE}}
"""
        defn = load_agent_definition(content, vars={"ROLE": "Engineer"}, source="user", base_dir="/custom/dir")
        assert defn is not None
        assert defn.name == "custom_agent"
        assert defn.description == "Detailed description"
        assert defn.prompt == "Prompt for Engineer"
        assert defn.phase == TddPhase.GREEN
        assert defn.tools == ("ReadFile", "Grep")
        assert defn.disallowed_tools == ("Bash",)
        assert defn.permission_mode == "acceptEdits"
        assert defn.memory == "run"
        assert defn.model == "custom-llm"
        assert defn.background is None
        assert defn.hooks == {"PreToolUse": []}
        assert defn.source == "user"
        assert defn.base_dir == "/custom/dir"
        assert defn.raw_frontmatter["name"] == "custom_agent"

    def test_load_agent_definition_without_a_name_is_not_an_agent(self) -> None:
        assert load_agent_definition("") is None
        assert load_agent_definition("---\nname: ''\ndescription: d\n---") is None
        defn_c = load_agent_definition("---\nname: a\ndescription: d\n---\n<!-- comment -->Prompt body")
        assert defn_c is not None and defn_c.prompt == "Prompt body"
        assert defn_c.source == "built-in" and defn_c.base_dir == ""
        assert defn_c.phase is None and defn_c.tools is None

    def test_load_agent_definition_phase_variations(self) -> None:
        def phase(value: str) -> object:
            defn = load_agent_definition(f"---\nname: a\ndescription: d\nphase: {value}\n---")
            assert defn is not None
            return defn.phase

        assert phase("null") is None
        assert phase("red") == TddPhase.RED
        assert phase("REFACTOR") == TddPhase.REFACTOR
        assert phase("post_green") == TddPhase.REFACTOR
        assert phase("Post_Green") == TddPhase.REFACTOR
        with pytest.raises(AgentFrontmatterError) as exc_info:
            load_agent_definition("---\nname: tester\ndescription: d\nphase: invalid_phase\n---")
        assert str(exc_info.value) == (
            "Invalid phase 'invalid_phase' in agent 'tester'. "
            "Allowed phases: ['RED', 'GREEN', 'REFACTOR', 'post_green']"
        )

    def test_load_agent_definition_invalid_field_warnings(self, caplog: pytest.LogCaptureFixture) -> None:
        content = """---
name: warning_test
description: d
tools: [1, 2]
permissionMode: invalid_pm
memory: invalid_mem
model: 1234
background: "not_a_bool"
hooks: [not, a, dict]
maxTurns: zero
---
Body
"""
        with caplog.at_level(logging.DEBUG):
            defn = load_agent_definition(content)
        assert defn is not None
        assert defn.tools == ()
        assert defn.permission_mode is None
        assert defn.memory is None
        assert defn.model is None
        assert defn.background is None
        assert defn.hooks is None
        assert defn.max_turns is None
        assert "Agent warning_test has invalid permissionMode 'invalid_pm'. Valid options: " in caplog.text
        assert "Agent warning_test has invalid memory value 'invalid_mem'." in caplog.text
        assert "Agent warning_test has invalid background value 'not_a_bool'." in caplog.text
        assert "Agent warning_test has invalid hooks: ['not', 'a', 'dict']" in caplog.text
        assert "Agent warning_test has invalid maxTurns 'zero'. Must be a positive integer." in caplog.text

    def test_load_agent_definition_from_path(self, tmp_path: Path) -> None:
        missing_file = tmp_path / "missing.md"
        with pytest.raises(FileNotFoundError) as exc_info:
            load_agent_definition_from_path(missing_file)
        assert f"Agent definition file not found: {missing_file}" in str(exc_info.value)

        valid_file = tmp_path / "agent.md"
        valid_file.write_text("---\nname: path_agent\ndescription: d\n---\nPrompt", encoding="utf-8")
        defn = load_agent_definition_from_path(valid_file, source="user")
        assert defn is not None
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
        (sub / "AGENT.md").write_text("---\nname: worker\ndescription: d\n---\nWorker prompt", encoding="utf-8")

        # Subdir without AGENT.md is ignored
        sub_empty = test_dir / "empty"
        sub_empty.mkdir()

        # Direct markdown file
        (test_dir / "direct.md").write_text("---\nname: direct\ndescription: d\n---\nDirect prompt", encoding="utf-8")

        # Corrupted markdown file with phase error
        (test_dir / "bad.md").write_text("---\nname: bad\ndescription: d\nphase: bad_phase\n---\nBad", encoding="utf-8")
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
        header = "---\nname: {}\ndescription: d\n---\n"
        (sub / "AGENT.md").write_text(header.format("worker") + "Worker prompt for {{ROLE}}", encoding="utf-8")
        (test_dir / "direct.md").write_text(header.format("direct") + "Direct prompt for {{ROLE}}", encoding="utf-8")
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
        assert "Explore" in real_builtin_defs


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
        with pytest.raises(ValueError, match="^Run-scoped agent memory needs a run id$"):
            store.get_memory_dir("agent1", "run", run_id=None)

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
        with patch.object(Path, "read_text", side_effect=OSError("Disk read error")), caplog.at_level(logging.WARNING):
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
        with patch("shutil.rmtree", side_effect=OSError("Permission denied")), caplog.at_level(logging.WARNING):
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
        assert FORK_AGENT.permission_mode is None

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
