"""
Tests for Part E4: Markdown prompt loader (app/loop/prompts/loader.py).
"""

from pathlib import Path
import pytest

from app.loop.ledger import TddPhase
from app.loop.prompts.loader import (
    AgentFrontmatterError,
    load_agent_definition,
    load_agent_definition_from_path,
    parse_markdown_frontmatter,
    render_prompt,
)


def test_render_prompt_basic() -> None:
    template = "Hello {{NAME}}, welcome to {{PROJECT}}!"
    rendered = render_prompt(template, {"NAME": "Alice", "PROJECT": "TDDAgents"})
    assert rendered == "Hello Alice, welcome to TDDAgents!"


def test_render_prompt_none_or_empty_vars() -> None:
    template = "Hello {{NAME}}!"
    assert render_prompt(template, None) == "Hello {{NAME}}!"
    assert render_prompt(template, {}) == "Hello {{NAME}}!"


def test_render_prompt_strip_comments_false() -> None:
    template = "Hello <!-- note --> world"
    assert render_prompt(template, {}, strip_comments=False) == "Hello <!-- note --> world"
    assert render_prompt(template, {}, strip_comments=True) == "Hello  world"
    # Default is strip_comments=True
    assert render_prompt(template) == "Hello  world"


def test_acceptance_test_unresolved_placeholder_survives_visibly() -> None:
    """
    CRITICAL ACCEPTANCE TEST (§4.1):
    An unresolved placeholder must survive visibly into the rendered text as {{VAR}},
    never render as empty.
    """
    template = "Fix the requirement: {{sub_requsite}} in file {{TARGET_FILE}}."
    rendered = render_prompt(template, {"TARGET_FILE": "main.py"})
    assert "{{sub_requsite}}" in rendered
    assert "Fix the requirement: {{sub_requsite}} in file main.py." == rendered


def test_parse_markdown_frontmatter_valid() -> None:
    content = """---
name: refactorer
description: Improves structure without changing behaviour.
phase: refactor
tools: [ReadFile, WriteFile, RunTests]
permissionMode: workspace_write
memory: run
model: inherit
---
# Refactorer Instructions
Improve the codebase after green.
"""
    fm, body = parse_markdown_frontmatter(content)
    assert fm["name"] == "refactorer"
    assert fm["description"] == "Improves structure without changing behaviour."
    assert fm["phase"] == "refactor"
    assert fm["tools"] == ["ReadFile", "WriteFile", "RunTests"]
    assert fm["permissionMode"] == "workspace_write"
    assert fm["memory"] == "run"
    assert fm["model"] == "inherit"
    assert "Improve the codebase after green." in body


def test_parse_markdown_frontmatter_leading_whitespace_and_multiple_dashes() -> None:
    content_ws = "  \n---\nname: spaced\n---\nBody"
    fm, body = parse_markdown_frontmatter(content_ws)
    assert fm["name"] == "spaced"
    assert body == "Body"

    content_multiple_dashes = "---\nname: foo\n---\nPart 1\n---\nPart 2\n---\nPart 3"
    fm2, body2 = parse_markdown_frontmatter(content_multiple_dashes)
    assert fm2["name"] == "foo"
    assert body2 == "Part 1\n---\nPart 2\n---\nPart 3"


def test_parse_markdown_frontmatter_no_frontmatter() -> None:
    content = "Just plain markdown body without dashes."
    fm, body = parse_markdown_frontmatter(content)
    assert fm == {}
    assert body == "Just plain markdown body without dashes."


def test_parse_markdown_frontmatter_invalid_yaml() -> None:
    content = "---\n: broken yaml :\n---\nBody content"
    fm, body = parse_markdown_frontmatter(content)
    assert fm == {}
    assert body == "Body content"


def test_load_agent_definition_with_vars() -> None:

    content = """---
name: specialized
description: Uses {{SPECIALTY}}
---
Spec: {{DETAILS}}
"""
    agent = load_agent_definition(content, vars={"SPECIALTY": "Testing", "DETAILS": "Write tests"})
    assert agent.name == "specialized"
    assert "Spec: Write tests" in agent.prompt


def test_load_agent_definition_defaults_for_missing_name_and_desc() -> None:
    content = """---
tools: [ReadFile]
---
Body
"""
    agent = load_agent_definition(content)
    assert agent.name == "unnamed_agent"
    assert agent.description == ""


def test_load_agent_definition_phase_post_green_maps_to_refactor() -> None:
    content = """---
name: refactorer
phase: post_green
---
Body
"""
    agent = load_agent_definition(content)
    assert agent.phase == TddPhase.REFACTOR


def test_load_agent_definition_invalid_phase_fails_loudly() -> None:
    content = """---
name: rogue
phase: invalid_phase_value
---
Body
"""
    with pytest.raises(AgentFrontmatterError) as exc_info:
        load_agent_definition(content)
    assert str(exc_info.value) == (
        "Invalid phase 'invalid_phase_value' in agent 'rogue'. "
        "Allowed phases: ['RED', 'GREEN', 'REFACTOR', 'post_green']"
    )


def test_load_agent_definition_invalid_field_types_ignored(caplog: pytest.LogCaptureFixture) -> None:

    content = """---
name: test_agent
tools: not_a_list
permissionMode: 999
memory: [1, 2]
model: 456
forkFrom: 789
revertOnRed: not_a_bool
hooks: not_a_dict
---
Body
"""
    agent = load_agent_definition(content)
    assert agent.tools is None
    assert agent.permission_mode is None
    assert agent.memory is None
    assert agent.model is None
    assert agent.fork_from is None
    assert agent.revert_on_red is None
    assert agent.hooks is None

    # Assert exact warning logs were emitted (exact equality kills mutmut string mutants)
    assert caplog.messages == [
        "Invalid tools list in agent 'test_agent': 'not_a_list'",
        "Invalid permissionMode in agent 'test_agent': 999",
        "Invalid memory in agent 'test_agent': [1, 2]",
        "Invalid model in agent 'test_agent': 456",
        "Invalid forkFrom in agent 'test_agent': 789",
        "Invalid revertOnRed in agent 'test_agent': 'not_a_bool'",
        "Invalid hooks dict in agent 'test_agent': 'not_a_dict'",
    ]


def test_load_agent_definition_invalid_tools_with_non_strings(caplog: pytest.LogCaptureFixture) -> None:
    content = """---
name: test_tools
tools: [ReadFile, 12345]
---
Body
"""
    agent = load_agent_definition(content)
    assert agent.tools is None
    assert caplog.messages == [
        "Invalid tools list in agent 'test_tools': ['ReadFile', 12345]",
    ]


def test_load_agent_definition_valid_fields() -> None:

    content = """---
name: complete_agent
description: Detailed agent
phase: GREEN
tools: [ReadFile, Edit]
permissionMode: FULL
memory: SESSION
model: claude-3-7-sonnet
forkFrom: developer
revertOnRed: true
hooks:
  PreToolUse: []
---
Body content
"""
    agent = load_agent_definition(content)
    assert agent.name == "complete_agent"
    assert agent.description == "Detailed agent"
    assert agent.phase == TddPhase.GREEN
    assert agent.tools == ("ReadFile", "Edit")
    assert agent.permission_mode == "full"
    assert agent.memory == "session"
    assert agent.model == "claude-3-7-sonnet"
    assert agent.fork_from == "developer"
    assert agent.revert_on_red is True
    assert agent.hooks == {"PreToolUse": []}


def test_load_agent_definition_from_path(tmp_path: Path) -> None:
    agent_dir = tmp_path / "developer"
    agent_dir.mkdir()
    agent_file = agent_dir / "AGENT.md"
    agent_file.write_text("""---
name: developer
description: Makes tests pass
phase: GREEN
---
<!-- author comments -->
Write code to turn {{TARGET}} green.
""", encoding="utf-8")

    agent = load_agent_definition_from_path(agent_file, vars={"TARGET": "tests"})
    assert agent.name == "developer"
    assert agent.phase == TddPhase.GREEN
    assert "<!--" not in agent.prompt
    assert "Write code to turn tests green." in agent.prompt


def test_load_agent_definition_from_path_missing(tmp_path: Path) -> None:
    missing_file = tmp_path / "NONEXISTENT.md"
    with pytest.raises(FileNotFoundError) as exc_info:
        load_agent_definition_from_path(missing_file)
    assert str(exc_info.value) == f"Agent definition file not found: {missing_file}"


def test_parse_markdown_frontmatter_edge_cases(caplog: pytest.LogCaptureFixture) -> None:
    # Malformed YAML
    fm1, b1 = parse_markdown_frontmatter("---\n: malformed: yaml: :\n---\nBody 1")
    assert fm1 == {}
    assert b1 == "Body 1"
    assert "Failed to parse YAML frontmatter:" in caplog.text

    # YAML is a list, not a dict
    fm2, b2 = parse_markdown_frontmatter("---\n- item 1\n- item 2\n---\nBody 2")
    assert fm2 == {}
    assert b2 == "Body 2"

    # No frontmatter
    fm3, b3 = parse_markdown_frontmatter("Just markdown text without frontmatter")
    assert fm3 == {}
    assert b3 == "Just markdown text without frontmatter"


def test_load_agent_definition_invalid_enums_ignored(caplog: pytest.LogCaptureFixture) -> None:
    content = """---
name: enum_tester
permissionMode: invalid_perm_mode
memory: invalid_memory_type
---
Body
"""
    agent = load_agent_definition(content)
    assert agent.name == "enum_tester"
    assert agent.permission_mode is None
    assert agent.memory is None
    assert caplog.messages == [
        "Invalid permissionMode in agent 'enum_tester': 'invalid_perm_mode'",
        "Invalid memory in agent 'enum_tester': 'invalid_memory_type'",
    ]
