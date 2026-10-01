"""
Tests verifying all authored system, tool, and agent prompt files under app/prompts/.
"""

from pathlib import Path

from app.loop.context.instructions import load_instruction_file
from app.loop.ledger import TddPhase
from app.loop.prompts.loader import load_agent_definition_from_path, parse_markdown_frontmatter

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
    ]
    for filename in expected_files:
        p = system_dir / filename
        assert p.is_file(), f"Missing system prompt file: {p}"
        loaded = load_instruction_file(p)
        assert loaded is not None
        assert len(loaded.content) > 20, f"Prompt {filename} is too short"


def test_tool_prompt_files_exist_and_load() -> None:
    tools_dir = PROMPTS_ROOT / "tools"
    expected_tools = [
        "read_file.md",
        "write_file.md",
        "edit.md",
        "grep.md",
        "glob.md",
        "bash.md",
        "run_tests.md",
    ]
    for filename in expected_tools:
        p = tools_dir / filename
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
    for name, expected_phase in expected_agents.items():
        agent_path = agents_dir / name / "AGENT.md"
        assert agent_path.is_file(), f"Missing agent AGENT.md: {agent_path}"
        agent = load_agent_definition_from_path(agent_path)
        assert agent.name == name
        assert agent.phase == expected_phase
        assert len(agent.description) > 5
        assert len(agent.prompt) > 20
        assert agent.tools is not None


def test_refactorer_catalogue_reference_exists() -> None:
    cat_path = PROMPTS_ROOT / "agents" / "refactorer" / "references" / "refactoring-catalogue.md"
    assert cat_path.is_file()
    loaded = load_instruction_file(cat_path)
    assert loaded is not None
    assert "Extract Function" in loaded.content
