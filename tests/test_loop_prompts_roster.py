"""
Tests verifying all authored system, tool, and agent prompt files under app/prompts/.
"""

from pathlib import Path

from app.tdd.ledger import TddPhase
from app.tools.agent_tool.load_agents_dir import load_agent_definition_from_path
from app.utils.frontmatter_parser import parse_markdown_frontmatter, strip_html_comments

PROMPTS_ROOT = Path(__file__).parent.parent / "app" / "prompts"


def test_system_prompt_files_exist_and_load() -> None:
    system_dir = PROMPTS_ROOT / "system"
    expected_files = [
        "identity.md",
        "system.md",
        "doing-tasks.md",
        "tdd-contract.md",
        "tools.md",
        "actions-with-care.md",
        "tone.md",
        "output-efficiency.md",
    ]
    for filename in expected_files:
        p = system_dir / filename
        assert p.is_file(), f"Missing system prompt file: {p}"
        assert len(strip_html_comments(p.read_text(encoding="utf-8")).strip()) > 20, f"Prompt {filename} is too short"


def test_every_tool_directory_has_its_prompt() -> None:
    """Each tool's `prompt.md` sits beside it, as upstream's `tools/<Tool>/prompt.ts` does."""
    app_root = PROMPTS_ROOT.parent
    tool_dirs = sorted(d for d in (app_root / "tools").iterdir() if d.is_dir() and d.name.endswith("_tool"))
    assert [d.name for d in tool_dirs] == [
        "agent_tool", "ask_user_question_tool", "bash_tool", "enter_plan_mode_tool", "exit_plan_mode_tool",
        "file_edit_tool", "file_read_tool", "file_write_tool",
        "glob_tool", "grep_tool", "list_mcp_resources_tool", "mcp_tool", "read_mcp_resource_tool", "send_message_tool",
        "skill_tool", "task_output_tool", "task_stop_tool", "todo_write_tool", "web_fetch_tool",
    ]
    with_markdown = [d for d in tool_dirs if d.name not in ("agent_tool", "mcp_tool")] + [
        app_root / "tdd" / "run_tests_tool"
    ]
    for directory in with_markdown:
        p = directory / "prompt.md"
        assert p.is_file(), f"Missing tool prompt file: {p}"
        fm, body = parse_markdown_frontmatter(p.read_text(encoding="utf-8"))
        assert "name" in fm
        assert "description" in fm
        assert len(body) > 10


def test_agent_roster_loads_cleanly() -> None:
    agents_dir = PROMPTS_ROOT / "agents"
    expected_agents = {
        "tester": TddPhase.RED,
        "developer": TddPhase.GREEN,
        "refactorer": TddPhase.REFACTOR,
        "verification": None,
        "explore": None,
        "plan": None,
    }
    # Upstream's agent types for the two one-shot built-ins (`ONE_SHOT_BUILTIN_AGENT_TYPES`).
    agent_types = {"explore": "Explore", "plan": "Plan"}
    for directory, expected_phase in expected_agents.items():
        name = agent_types.get(directory, directory)
        agent_path = agents_dir / directory / "AGENT.md"
        assert agent_path.is_file(), f"Missing agent AGENT.md: {agent_path}"
        agent = load_agent_definition_from_path(agent_path)
        assert agent is not None
        assert agent.name == name
        assert agent.phase == expected_phase
        assert len(agent.description) > 5
        assert len(agent.prompt) > 20
        # Upstream's verification agent lists no tools: it gets every tool its denylist leaves.
        assert (agent.tools is None) == (directory == "verification")


def test_refactorer_catalogue_reference_exists() -> None:
    cat_path = PROMPTS_ROOT / "agents" / "refactorer" / "references" / "refactoring-catalogue.md"
    assert cat_path.is_file()
    assert "Extract Function" in cat_path.read_text(encoding="utf-8")
