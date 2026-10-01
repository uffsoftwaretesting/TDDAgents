"""
Unit tests for I1: Agent definition parsing and multi-tier override resolution.
"""

from pathlib import Path
import pytest

from app.loop.agents.definition import AgentDefinition, AgentFrontmatterError
from app.loop.agents.loader import (
    get_agent_definitions_with_overrides,
    load_agent_definition,
    load_agent_definition_from_path,
    parse_markdown_frontmatter,
)
from app.loop.ledger import TddPhase


def test_agent_definition_dataclass_fields() -> None:
    defn = AgentDefinition(
        name="custom",
        description="A test agent",
        prompt="Do things",
        phase=TddPhase.RED,
        tools=("ReadFile", "WriteFile"),
        disallowed_tools=("Bash",),
        permission_mode="workspace_write",
        memory="run",
        fork_from="developer",
        revert_on_red=True,
        hooks={"PreToolUse": []},
        model="inherit",
        background=True,
        source="project",
        base_dir="/tmp",
        raw_frontmatter={"name": "custom"},
    )
    assert defn.name == "custom"
    assert defn.description == "A test agent"
    assert defn.phase == TddPhase.RED
    assert defn.tools == ("ReadFile", "WriteFile")
    assert defn.disallowed_tools == ("Bash",)
    assert defn.permission_mode == "workspace_write"
    assert defn.memory == "run"
    assert defn.fork_from == "developer"
    assert defn.revert_on_red is True
    assert defn.model == "inherit"
    assert defn.background is True
    assert defn.source == "project"
    assert defn.base_dir == "/tmp"


def test_rule_1_omission_means_unset() -> None:
    content = """---
name: minimal
description: Only the essentials
---
Execute instructions.
"""
    defn = load_agent_definition(content)
    assert defn.name == "minimal"
    assert defn.description == "Only the essentials"
    assert defn.prompt == "Execute instructions."
    assert defn.phase is None
    assert defn.tools is None
    assert defn.disallowed_tools is None
    assert defn.permission_mode is None
    assert defn.memory is None
    assert defn.fork_from is None
    assert defn.revert_on_red is None
    assert defn.hooks is None
    assert defn.model is None
    assert defn.background is None


def test_rule_2_invalid_values_are_logged_and_ignored() -> None:
    content = """---
name: sloppy
description: Invalid optional values
tools: "not a list"
disallowedTools: 12345
permissionMode: invalid_mode_xyz
memory: invalid_memory_xyz
model: 999
forkFrom: 123
revertOnRed: "yes"
background: "not_a_bool"
hooks: "not a dict"
---
Prompt here.
"""
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


def test_rule_3_phase_fails_loudly() -> None:
    content = """---
name: broken_phase
description: Bad phase
phase: speculative_phase
---
Body
"""
    with pytest.raises(AgentFrontmatterError) as exc_info:
        load_agent_definition(content)
    assert "Invalid phase 'speculative_phase'" in str(exc_info.value)


def test_post_green_alias_normalizes_to_refactor() -> None:
    content = """---
name: post_green_agent
description: Normalizes to REFACTOR
phase: post_green
---
Body
"""
    defn = load_agent_definition(content)
    assert defn.phase == TddPhase.REFACTOR


def test_disallowed_tools_parsed_cleanly() -> None:
    content = """---
name: restricted
description: Has disallowed tools
tools: [ReadFile, Bash]
disallowedTools: [Bash, WriteFile]
---
Body
"""
    defn = load_agent_definition(content)
    assert defn.tools == ("ReadFile", "Bash")
    assert defn.disallowed_tools == ("Bash", "WriteFile")


def test_load_agent_definition_from_path(tmp_path: Path) -> None:
    p = tmp_path / "AGENT.md"
    p.write_text(
        """---
name: on_disk
description: On disk test
tools: [ReadFile]
---
Disk body
""",
        encoding="utf-8",
    )
    defn = load_agent_definition_from_path(p, source="project")
    assert defn.name == "on_disk"
    assert defn.source == "project"
    assert defn.base_dir == str(tmp_path)


def test_load_agent_definition_from_path_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_agent_definition_from_path(tmp_path / "nonexistent.md")


def test_multi_tier_overrides(tmp_path: Path) -> None:
    built_in = tmp_path / "builtin"
    built_in.mkdir()
    (built_in / "agent_a").mkdir()
    (built_in / "agent_a" / "AGENT.md").write_text(
        """---
name: agent_a
description: Built-in A
---
Prompt Builtin A
""",
        encoding="utf-8",
    )
    (built_in / "agent_b").mkdir()
    (built_in / "agent_b" / "AGENT.md").write_text(
        """---
name: agent_b
description: Built-in B
---
Prompt Builtin B
""",
        encoding="utf-8",
    )

    user_home = tmp_path / "user"
    user_agents = user_home / ".tddagents" / "agents"
    user_agents.mkdir(parents=True)
    # User overrides agent_a
    (user_agents / "agent_a.md").write_text(
        """---
name: agent_a
description: User A
---
Prompt User A
""",
        encoding="utf-8",
    )
    # User adds agent_c
    (user_agents / "agent_c.md").write_text(
        """---
name: agent_c
description: User C
---
Prompt User C
""",
        encoding="utf-8",
    )

    project_dir = tmp_path / "project"
    project_agents = project_dir / ".tddagents" / "agents"
    project_agents.mkdir(parents=True)
    # Project overrides agent_a
    (project_agents / "agent_a").mkdir()
    (project_agents / "agent_a" / "AGENT.md").write_text(
        """---
name: agent_a
description: Project A
---
Prompt Project A
""",
        encoding="utf-8",
    )
    # Project adds agent_d
    (project_agents / "agent_d.md").write_text(
        """---
name: agent_d
description: Project D
---
Prompt Project D
""",
        encoding="utf-8",
    )

    definitions = get_agent_definitions_with_overrides(
        project_dir=project_dir,
        user_home=user_home,
        built_in_dir=built_in,
    )

    assert set(definitions.keys()) == {"agent_a", "agent_b", "agent_c", "agent_d"}
    # agent_a should be Project A
    assert definitions["agent_a"].description == "Project A"
    assert definitions["agent_a"].source == "project"
    # agent_b should be Built-in B
    assert definitions["agent_b"].description == "Built-in B"
    assert definitions["agent_b"].source == "built-in"
    # agent_c should be User C
    assert definitions["agent_c"].description == "User C"
    assert definitions["agent_c"].source == "user"
    # agent_d should be Project D
    assert definitions["agent_d"].description == "Project D"
    assert definitions["agent_d"].source == "project"


def test_multi_tier_overrides_with_defaults_nonexistent_paths() -> None:
    # When paths don't exist, it should not fail
    defs = get_agent_definitions_with_overrides(
        project_dir="/nonexistent/proj",
        user_home="/nonexistent/user",
        built_in_dir="/nonexistent/builtin",
    )
    assert defs == {}


def test_parse_markdown_frontmatter_corrupted_yaml() -> None:
    content = """---
: : : bad yaml
---
Body
"""
    fm, body = parse_markdown_frontmatter(content)
    assert fm == {}
    assert body == "Body"
