"""
Unit and Integration Test Suite for Part J — Skills.

Tests:
1. J1: SKILL.md loader, frontmatter parsing, on-demand references, and 3-tier discovery.
2. J2: Progressive disclosure budget and metadata token estimation.
3. J3: Path-conditional activation and glob matching.
4. J4: Bundled TDD skill roster and Skill tool invocation.
"""

from __future__ import annotations

from pathlib import Path
import pytest

from app.loop.context import AppState, AppStateStore, tool_context_for
from app.loop.skills.activation import (
    estimate_skill_frontmatter_tokens,
    filter_active_skills,
    filter_skills_by_budget,
    is_path_matching_pattern,
    is_skill_active_for_paths,
    render_skills_prompt_section,
)
from app.loop.skills.definition import SkillDefinition
from app.loop.skills.loader import (
    SkillRegistry,
    discover_skills,
    extract_description_from_markdown,
    load_skill_from_path,
    parse_skill_frontmatter,
    parse_skill_paths,
)
from app.loop.skills.tool import build_skill_tool, substitute_arguments


# ============================================================================
# 1. J1: Frontmatter Parsing & SKILL.md Loader
# ============================================================================

def test_parse_skill_frontmatter_standard() -> None:
    content = """---
name: custom-linter
description: Linting guidelines for python code.
when_to_use: When formatting code or fixing style warnings.
argument_hint: "[file-path]"
arguments:
  - file_path
allowed_tools:
  - ReadFile
  - Edit
paths:
  - "**/*.py"
---

# Linter Instructions

Use flake8 and black to format python files.
"""
    frontmatter, body = parse_skill_frontmatter(content, fallback_name="custom-linter")
    assert frontmatter["name"] == "custom-linter"
    assert frontmatter["description"] == "Linting guidelines for python code."
    assert frontmatter["when_to_use"] == "When formatting code or fixing style warnings."
    assert frontmatter["argument_hint"] == "[file-path]"
    assert frontmatter["allowed_tools"] == ["ReadFile", "Edit"]
    assert "Use flake8 and black" in body


def test_parse_skill_frontmatter_strips_html_comments() -> None:
    content = """---
name: comment-test
description: Test skill with comments.
---

<!-- This comment must be stripped -->
# Skill Body
Content here.
<!-- Another hidden comment -->
"""
    frontmatter, body = parse_skill_frontmatter(content)
    assert frontmatter["name"] == "comment-test"
    assert "<!--" not in body
    assert "Content here." in body


def test_extract_description_from_markdown_fallback() -> None:
    content = """# Header 1
## Subheader

This is the first actual paragraph of the skill documentation.
It explains what this skill does.

Another paragraph.
"""
    desc = extract_description_from_markdown(content, fallback="MySkill")
    assert desc == "This is the first actual paragraph of the skill documentation. It explains what this skill does."

    empty_content = "# Header Only\n"
    fallback_desc = extract_description_from_markdown(empty_content, fallback="MySkill")
    assert fallback_desc == "MySkill instructions"


def test_parse_skill_paths_variations() -> None:
    # Comma-separated string with /** stripped
    p1 = parse_skill_paths("tests/**, src/**/*.py/**")
    assert p1 == ("tests", "src/**/*.py")

    # List of patterns
    p2 = parse_skill_paths(["app/**", "test_*.py"])
    assert p2 == ("app", "test_*.py")

    # All match-all becomes None (unconditional)
    assert parse_skill_paths("**") is None
    assert parse_skill_paths(["**"]) is None

    # Empty or None
    assert parse_skill_paths(None) is None
    assert parse_skill_paths("") is None


def test_load_skill_from_path_directory(tmp_path: Path) -> None:
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        """---
name: my-skill
description: Custom engineering skill
when_to_use: When doing task X
argument_hint: "[arg1]"
paths:
  - src/**
allowed_tools:
  - ReadFile
context: fork
agent: tester
---

# My Skill Instructions
Execute step 1 with $ARGUMENTS.
""",
        encoding="utf-8",
    )

    # Add a references/ subdirectory with documentation
    ref_dir = skill_dir / "references"
    ref_dir.mkdir()
    (ref_dir / "guide.md").write_text("# Reference Guide\nDetailed guide contents.", encoding="utf-8")

    skill = load_skill_from_path(skill_dir, source="project")
    assert skill.name == "my-skill"
    assert skill.description == "Custom engineering skill"
    assert skill.when_to_use == "When doing task X"
    assert skill.context == "fork"
    assert skill.agent == "tester"
    assert skill.paths == ("src",)
    assert skill.allowed_tools == ("ReadFile",)
    assert skill.source == "project"
    assert "guide.md" in skill.references
    assert "Detailed guide contents." in skill.references["guide.md"]


def test_load_skill_missing_file_raises(tmp_path: Path) -> None:
    empty_dir = tmp_path / "empty-skill"
    empty_dir.mkdir()
    with pytest.raises(FileNotFoundError):
        load_skill_from_path(empty_dir)


def test_load_skill_invalid_yaml_handles_gracefully(tmp_path: Path) -> None:
    skill_file = tmp_path / "bad.md"
    skill_file.write_text(
        """---
name: bad-yaml
description: [unclosed list
---

# Body
Body text here.
""",
        encoding="utf-8",
    )
    skill = load_skill_from_path(skill_file, expected_name="bad-yaml")
    assert skill.name == "bad-yaml"
    assert "Body text here." in skill.body


# ============================================================================
# 2. Multi-tier Discovery & Precedence (J1)
# ============================================================================

def test_discover_skills_multi_tier_precedence(tmp_path: Path) -> None:
    # Tier 1: Built-in
    built_in_dir = tmp_path / "builtin"
    built_in_dir.mkdir()
    s1_dir = built_in_dir / "skill-alpha"
    s1_dir.mkdir()
    (s1_dir / "SKILL.md").write_text(
        "---\nname: skill-alpha\ndescription: Builtin alpha\n---\nBody alpha",
        encoding="utf-8",
    )

    s2_dir = built_in_dir / "skill-beta"
    s2_dir.mkdir()
    (s2_dir / "SKILL.md").write_text(
        "---\nname: skill-beta\ndescription: Builtin beta\n---\nBody beta",
        encoding="utf-8",
    )

    # Tier 2: User
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    s2_user = user_dir / "skill-beta"
    s2_user.mkdir()
    (s2_user / "SKILL.md").write_text(
        "---\nname: skill-beta\ndescription: User beta override\n---\nUser body beta",
        encoding="utf-8",
    )

    # Tier 3: Project
    proj_dir = tmp_path / "project"
    proj_skills = proj_dir / ".tddagents" / "skills"
    proj_skills.mkdir(parents=True)
    s1_proj = proj_skills / "skill-alpha"
    s1_proj.mkdir()
    (s1_proj / "SKILL.md").write_text(
        "---\nname: skill-alpha\ndescription: Project alpha override\n---\nProj body alpha",
        encoding="utf-8",
    )

    registry = discover_skills(
        project_root=proj_dir,
        user_skills_dir=user_dir,
        built_in_dir=built_in_dir,
    )

    # 1. Project overrides Built-in for skill-alpha
    alpha = registry.get("skill-alpha")
    assert alpha is not None
    assert alpha.description == "Project alpha override"
    assert alpha.source == "project"

    # 2. User overrides Built-in for skill-beta
    beta = registry.get("skill-beta")
    assert beta is not None
    assert beta.description == "User beta override"
    assert beta.source == "user"


# ============================================================================
# 3. J2: Progressive Disclosure Budget & Metadata Token Estimation
# ============================================================================

def test_estimate_skill_frontmatter_tokens() -> None:
    skill = SkillDefinition(
        name="test-skill",
        description="A short description for testing token estimation.",
        when_to_use="When running unit tests.",
        argument_hint="[target]",
        body="# Long Body That Should Not Be Counted\n" * 100,
    )
    tokens = estimate_skill_frontmatter_tokens(skill)
    # The frontmatter text has ~20 words, estimated tokens should be small (< 50)
    assert 10 <= tokens < 50


def test_filter_skills_by_budget() -> None:
    s1 = SkillDefinition(name="s1", description="desc 1", body="body")
    s2 = SkillDefinition(name="s2", description="desc 2", body="body")
    s3 = SkillDefinition(name="s3", description="desc 3", body="body")

    all_skills = [s1, s2, s3]

    # No budget returns all
    assert filter_skills_by_budget(all_skills, token_budget=None) == (s1, s2, s3)
    assert filter_skills_by_budget(all_skills, token_budget=0) == (s1, s2, s3)

    # A tiny budget of 4 tokens allows only 1 skill (each skill frontmatter is 3 tokens)
    budgeted = filter_skills_by_budget(all_skills, token_budget=4)
    assert len(budgeted) == 1
    assert budgeted[0].name == "s1"


def test_render_skills_prompt_section_progressive_disclosure() -> None:
    s1 = SkillDefinition(
        name="tdd-red",
        description="Write red tests first.",
        when_to_use="In RED phase",
        argument_hint="[test-path]",
        body="SECRET INSTRUCTIONS IN BODY SHOULD NEVER APPEAR IN PROMPT!",
    )
    s2 = SkillDefinition(
        name="tdd-green",
        description="Implement minimal code.",
        when_to_use="In GREEN phase",
        body="OTHER SECRET INSTRUCTIONS!",
    )

    rendered = render_skills_prompt_section([s1, s2])
    assert "<available_skills>" in rendered
    assert "</available_skills>" in rendered
    assert "- **tdd-red**: Write red tests first." in rendered
    assert "When: In RED phase" in rendered
    assert "Args: [test-path]" in rendered
    assert "- **tdd-green**: Implement minimal code." in rendered

    # Progressive disclosure verification: body is completely withheld from prompt!
    assert "SECRET INSTRUCTIONS" not in rendered
    assert "OTHER SECRET INSTRUCTIONS" not in rendered


# ============================================================================
# 4. J3: Path-Conditional Activation
# ============================================================================

def test_is_path_matching_pattern() -> None:
    # Wildcard in filename
    assert is_path_matching_pattern("tests/test_calculator.py", "*.py") is True
    assert is_path_matching_pattern("tests/test_calculator.py", "test_*.py") is True
    assert is_path_matching_pattern("tests/test_calculator.py", "*.ts") is False

    # Recursive directory globs
    assert is_path_matching_pattern("tests/unit/test_sub.py", "tests/**") is True
    assert is_path_matching_pattern("tests/test_calculator.py", "tests/**") is True
    assert is_path_matching_pattern("app/main.py", "tests/**") is False

    # Specific directory patterns
    assert is_path_matching_pattern("src/components/Button.tsx", "src/**/*.tsx") is True
    assert is_path_matching_pattern("src/Button.tsx", "src/**/*.tsx") is True
    assert is_path_matching_pattern("tests/Button.test.tsx", "src/**/*.tsx") is False


def test_is_skill_active_for_paths() -> None:
    skill_unconditional = SkillDefinition(
        name="always-active",
        description="",
        body="",
        paths=None,
    )
    # Unconditional skill is active regardless of active_paths
    assert is_skill_active_for_paths(skill_unconditional, ["any/path.py"]) is True
    assert is_skill_active_for_paths(skill_unconditional, []) is True
    assert is_skill_active_for_paths(skill_unconditional, None) is True

    skill_conditional = SkillDefinition(
        name="test-only",
        description="",
        body="",
        paths=("tests/**", "test_*.py"),
    )
    # Conditional skill matches active test file
    assert is_skill_active_for_paths(skill_conditional, ["tests/test_add.py"]) is True
    assert is_skill_active_for_paths(skill_conditional, ["app/calculator.py", "test_foo.py"]) is True

    # Does not match when editing only production code
    assert is_skill_active_for_paths(skill_conditional, ["app/calculator.py"]) is False
    assert is_skill_active_for_paths(skill_conditional, []) is False
    assert is_skill_active_for_paths(skill_conditional, None) is False


def test_filter_active_skills() -> None:
    s_uncond = SkillDefinition(name="general", description="", body="", paths=None)
    s_test = SkillDefinition(name="tester", description="", body="", paths=("tests/**",))
    s_impl = SkillDefinition(name="developer", description="", body="", paths=("app/**",))

    skills = (s_uncond, s_test, s_impl)

    # Active paths in tests: general + tester
    active_test = filter_active_skills(skills, ["tests/test_loop.py"])
    names_test = [s.name for s in active_test]
    assert names_test == ["general", "tester"]

    # Active paths in app: general + developer
    active_app = filter_active_skills(skills, ["app/core.py"])
    names_app = [s.name for s in active_app]
    assert names_app == ["general", "developer"]


# ============================================================================
# 5. J4: Bundled TDD Skill Roster & Skill Tool Execution
# ============================================================================

def test_discover_bundled_tdd_skills() -> None:
    registry = discover_skills()
    skill_names = registry.names()

    # The 3 bundled TDD skills must be discovered
    assert "tdd-test-design" in skill_names
    assert "tdd-refactor-clean" in skill_names
    assert "tdd-mutation-defense" in skill_names

    test_design = registry.get("tdd-test-design")
    assert test_design is not None
    assert "clean, isolated unit tests" in test_design.description
    assert test_design.paths is not None
    assert "tests" in test_design.paths[0]
    assert "isolation.md" in test_design.references

    refactor = registry.get("tdd-refactor-clean")
    assert refactor is not None
    assert "clean_code.md" in refactor.references

    mutation = registry.get("tdd-mutation-defense")
    assert mutation is not None
    assert "mutation_operators.md" in mutation.references


@pytest.mark.anyio
async def test_skill_tool_invocation_success() -> None:
    registry = discover_skills()
    skill_tool = build_skill_tool(registry)

    store = AppStateStore(AppState())
    ctx = tool_context_for(store)

    # Invoke tdd-test-design with target test path argument
    result = await skill_tool.call(
        {"skill_name": "tdd-test-design", "args": "tests/test_math.py"},
        ctx,
    )

    assert result.is_error is False
    assert '<command-message name="tdd-test-design">' in result.content
    assert "tests/test_math.py" in result.content
    assert "TDD Test Design Skill" in result.content
    # Reference documentation included
    assert "Test Isolation and Mocking Principles" in result.content


@pytest.mark.anyio
async def test_skill_tool_invocation_missing_skill_error() -> None:
    registry = discover_skills()
    skill_tool = build_skill_tool(registry)

    store = AppStateStore(AppState())
    ctx = tool_context_for(store)

    result = await skill_tool.call(
        {"skill_name": "nonexistent-skill"},
        ctx,
    )

    assert result.is_error is True
    assert "Skill 'nonexistent-skill' not found" in result.content
    assert "tdd-test-design" in result.content


@pytest.mark.anyio
async def test_skill_tool_invocation_empty_name_error() -> None:
    registry = SkillRegistry()
    skill_tool = build_skill_tool(registry)

    store = AppStateStore(AppState())
    ctx = tool_context_for(store)

    result = await skill_tool.call({}, ctx)
    assert result.is_error is True
    assert "skill_name' parameter is required" in result.content


def test_substitute_arguments() -> None:
    template = "Target is $ARGUMENTS and repeat {{args}}."
    substituted = substitute_arguments(template, "foo/bar.py")
    assert substituted == "Target is foo/bar.py and repeat foo/bar.py."
