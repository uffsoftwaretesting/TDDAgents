"""
Mutation booster test suite for Part J — Skills.

Systematically targets and kills surviving mutants in:
- app.loop.skills.loader (load_skill_from_path, _scan_skills_in_directory,
  discover_skills, parse_skill_frontmatter, load_skill_references, SkillRegistry)
- app.loop.skills.activation (is_path_matching_pattern, filter_skills_by_budget,
  render_skills_prompt_section, estimate_skill_frontmatter_tokens)
- app.loop.skills.tool (build_skill_tool, substitute_arguments)
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch
import pytest

from app.loop.context import AppState, AppStateStore, tool_context_for
from app.loop.context.tokens import estimate_string_tokens
from app.loop.skills.activation import (
    estimate_skill_frontmatter_tokens,
    filter_skills_by_budget,
    is_path_matching_pattern,
    render_skills_prompt_section,
)
from app.loop.skills.definition import SkillDefinition, SkillFrontmatterError
from app.loop.skills.loader import (
    SkillRegistry,
    _scan_skills_in_directory,
    discover_skills,
    extract_description_from_markdown,
    load_skill_from_path,
    load_skill_references,
    parse_skill_frontmatter,
    parse_skill_paths,
)
from app.loop.skills.tool import SKILL_TOOL_PROMPT, build_skill_tool, substitute_arguments


# ============================================================================
# 1. Loader: Frontmatter, Defaults, Aliases & Error Handling
# ============================================================================

def test_load_skill_from_path_default_source(tmp_path: Path) -> None:
    skill_dir = tmp_path / "default-source-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: default-source-skill\ndescription: test\n---\nBody",
        encoding="utf-8",
    )
    skill = load_skill_from_path(skill_dir)
    assert skill.source == "built-in"


def test_load_skill_from_path_expected_name_and_fallbacks(tmp_path: Path) -> None:
    # 1. Directory with explicit expected_name and no frontmatter name
    dir1 = tmp_path / "dir1"
    dir1.mkdir()
    (dir1 / "SKILL.md").write_text("---\ndescription: test\n---\nBody", encoding="utf-8")
    s1 = load_skill_from_path(dir1, expected_name="custom_expected_1")
    assert s1.name == "custom_expected_1"

    # 2. Directory without expected_name and no frontmatter name (falls back to p.name)
    s2 = load_skill_from_path(dir1)
    assert s2.name == "dir1"

    # 3. File named SKILL.md without expected_name and no frontmatter name (falls back to p.parent.name)
    s3 = load_skill_from_path(dir1 / "SKILL.md")
    assert s3.name == "dir1"

    # 4. Standalone file other.md without expected_name and no frontmatter name (falls back to p.stem)
    other_file = tmp_path / "standalone_tool.md"
    other_file.write_text("---\ndescription: standalone\n---\nBody", encoding="utf-8")
    s4 = load_skill_from_path(other_file)
    assert s4.name == "standalone_tool"

    # 5. Standalone file with explicit expected_name
    s5 = load_skill_from_path(other_file, expected_name="explicit_override")
    assert s5.name == "explicit_override"


def test_load_skill_from_path_fallback_description_from_body(tmp_path: Path) -> None:
    skill_file = tmp_path / "no_desc.md"
    skill_file.write_text(
        """---
name: no-desc-skill
---

# Title
## Section

This is the primary summary paragraph explaining what the skill accomplishes.

Subsequent details.
""",
        encoding="utf-8",
    )
    skill = load_skill_from_path(skill_file)
    assert skill.description == "This is the primary summary paragraph explaining what the skill accomplishes."


def test_load_skill_from_path_frontmatter_aliases_and_types(tmp_path: Path) -> None:
    # Test camelCase aliases, string arguments/allowed-tools, and kebab-case keys
    skill_file = tmp_path / "aliases.md"
    skill_file.write_text(
        """---
name: alias-skill
description: Tests field aliases.
whenToUse: On condition Z
argumentHint: "<target-id>"
arguments: argA, argB, argC
allowed-tools: Bash, ReadFile
user-invocable: false
context: inline
agent: custom-agent
model: custom-model
effort: low
hooks:
  pre: run_pre
---
Body content.
""",
        encoding="utf-8",
    )
    skill = load_skill_from_path(skill_file)
    assert skill.name == "alias-skill"
    assert skill.description == "Tests field aliases."
    assert skill.when_to_use == "On condition Z"
    assert skill.argument_hint == "<target-id>"
    assert skill.argument_names == ("argA", "argB", "argC")
    assert skill.allowed_tools == ("Bash", "ReadFile")
    assert skill.user_invocable is False
    assert skill.context == "inline"
    assert skill.agent == "custom-agent"
    assert skill.model == "custom-model"
    assert skill.effort == "low"
    assert skill.hooks == {"pre": "run_pre"}
    assert skill.raw_frontmatter["name"] == "alias-skill"
    assert skill.base_dir == str(tmp_path)
    assert skill.skill_file_path == str(skill_file.resolve())


def test_load_skill_from_path_omitted_optionals_are_strictly_none_or_defaults(tmp_path: Path) -> None:
    f = tmp_path / "minimal.md"
    f.write_text(
        """---
name: min-skill
description: Minimal skill
---
Body
""",
        encoding="utf-8",
    )
    s = load_skill_from_path(f)
    assert s.when_to_use is None
    assert s.argument_hint is None
    assert s.argument_names == ()
    assert s.allowed_tools == ()
    assert s.user_invocable is True
    assert s.context is None
    assert s.agent is None
    assert s.model is None
    assert s.effort is None
    assert s.hooks is None
    assert s.paths is None


def test_load_skill_from_path_user_invocable_truthy_falsy(tmp_path: Path) -> None:
    variations = [
        ("user_invocable: true", True),
        ("user_invocable: false", False),
        ("user_invocable: 0", False),
        ("user_invocable: '0'", False),
        ("user_invocable: 'no'", False),
        ("user_invocable: 'false'", False),
        ("user_invocable: 1", True),
        ("user_invocable: 'yes'", True),
        ("", True),  # default
    ]
    for i, (yaml_line, expected) in enumerate(variations):
        f = tmp_path / f"invocable_{i}.md"
        f.write_text(f"---\nname: skill_{i}\ndescription: d\n{yaml_line}\n---\nBody", encoding="utf-8")
        s = load_skill_from_path(f)
        assert s.user_invocable is expected, f"Failed for {yaml_line}"


def test_load_skill_from_path_argument_names_tuple_and_allowed_tools_tuple(tmp_path: Path) -> None:
    f = tmp_path / "tuple_test.md"
    f.write_text(
        """---
name: tuple-skill
description: d
argument_names:
  - opt1
  - opt2
allowed_tools:
  - ToolA
  - ToolB
---
Body
""",
        encoding="utf-8",
    )
    s = load_skill_from_path(f)
    assert s.argument_names == ("opt1", "opt2")
    assert s.allowed_tools == ("ToolA", "ToolB")


def test_load_skill_from_path_invalid_context_and_hooks(tmp_path: Path) -> None:
    f = tmp_path / "invalid_context.md"
    f.write_text(
        """---
name: bad-ctx
description: d
context: invalid_mode
hooks: not_a_dict
---
Body
""",
        encoding="utf-8",
    )
    s = load_skill_from_path(f)
    assert s.context is None
    assert s.hooks is None


def test_load_skill_from_path_io_error_raises_skill_frontmatter_error(tmp_path: Path) -> None:
    f = tmp_path / "unreadable.md"
    f.write_text("---\nname: test\n---\nBody", encoding="utf-8")
    with patch.object(Path, "read_text", side_effect=OSError("Disk failure")):
        with pytest.raises(SkillFrontmatterError, match="Cannot read skill file"):
            load_skill_from_path(f)


def test_parse_skill_frontmatter_non_dict_and_yaml_syntax_error(caplog: pytest.LogCaptureFixture) -> None:
    # 1. Frontmatter is valid YAML but parses to a non-dict (e.g., list or scalar)
    content_list = "---\n- item 1\n- item 2\n---\nBody content"
    fm, body = parse_skill_frontmatter(content_list, fallback_name="test-list")
    assert fm == {}
    assert body == "Body content"

    # 2. Frontmatter is invalid YAML syntax (raises exception, logged as warning)
    content_bad = "---\nkey: : bad: yaml:\n---\nBody after bad yaml"
    with caplog.at_level(logging.WARNING):
        fm2, body2 = parse_skill_frontmatter(content_bad, fallback_name="broken-skill")
        assert fm2 == {}
        assert body2 == "Body after bad yaml"
        assert any(
            r.getMessage() == "Failed to parse YAML frontmatter for skill 'broken-skill': while parsing a block mapping"
            or "Failed to parse YAML frontmatter for skill 'broken-skill'" in r.getMessage()
            for r in caplog.records
        )

    # 3. Default fallback_name when omitted is ""
    with caplog.at_level(logging.WARNING):
        caplog.clear()
        parse_skill_frontmatter(content_bad)
        assert any("Failed to parse YAML frontmatter for skill '':" in r.getMessage() for r in caplog.records)


def test_extract_description_from_markdown_default_fallback() -> None:
    # Default fallback when omitted is "Skill" -> "Skill instructions"
    content = "# Title Only\n"
    assert extract_description_from_markdown(content) == "Skill instructions"


def test_parse_skill_paths_types_and_defaults() -> None:
    # List containing integers or mixed types
    assert parse_skill_paths([123, "test_*.py"]) == ("123", "test_*.py")
    # Empty list
    assert parse_skill_paths([]) is None


def test_load_skill_references_missing_dir_and_exception(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    # 1. Non-existent directory returns {}
    non_existent = tmp_path / "does_not_exist"
    assert load_skill_references(non_existent) == {}

    # 2. Existing directory with unreadable file logs warning
    ref_dir = tmp_path / "references"
    ref_dir.mkdir()
    good_ref = ref_dir / "good.md"
    good_ref.write_text("# Good Reference\n<!-- comment -->Content.", encoding="utf-8")

    bad_ref = ref_dir / "bad.md"
    bad_ref.write_text("# Bad Reference", encoding="utf-8")

    orig_read_text = Path.read_text

    def mock_read_text(self: Path, encoding: str | None = None, errors: str | None = None) -> str:
        if encoding != "utf-8":
            raise ValueError("Must use utf-8 encoding")
        if self.name == "bad.md":
            raise PermissionError("Access denied")
        return orig_read_text(self, encoding=encoding, errors=errors)

    with caplog.at_level(logging.WARNING):
        with patch.object(Path, "read_text", mock_read_text):
            refs = load_skill_references(ref_dir)
            assert "good.md" in refs
            assert "Content." in refs["good.md"]
            assert "<!--" not in refs["good.md"]
            assert "bad.md" not in refs
            matching = [
                r for r in caplog.records
                if isinstance(r.args, tuple) and r.args and r.args[0] == bad_ref
            ]
            assert len(matching) == 1
            assert "Could not read skill reference file '%s': %s" in matching[0].msg


# ============================================================================
# 2. Loader: Directory Scanning, Multi-Tier Precedence & Registry
# ============================================================================

def test_scan_skills_in_directory_ignores_non_skill_files(tmp_path: Path) -> None:
    empty_sub = tmp_path / "not_a_skill"
    empty_sub.mkdir()
    (empty_sub / "other.txt").write_text("Hello", encoding="utf-8")

    (tmp_path / "script.py").write_text("print('hi')", encoding="utf-8")
    (tmp_path / "SKILL.md").write_text("---\nname: root\n---\nBody", encoding="utf-8")

    (tmp_path / "valid_standalone.md").write_text(
        "---\nname: standalone-skill\ndescription: d\n---\nBody",
        encoding="utf-8",
    )

    skills = _scan_skills_in_directory(tmp_path, source="project")
    assert len(skills) == 1
    assert skills[0].name == "standalone-skill"
    assert skills[0].source == "project"

    assert _scan_skills_in_directory(tmp_path / "nonexistent", source="project") == []


def test_scan_skills_in_directory_catches_load_errors_separately(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # 1. Broken directory skill
    dir_with_broken = tmp_path / "broken_dir"
    dir_with_broken.mkdir()
    bad_sub = dir_with_broken / "sub_skill"
    bad_sub.mkdir()
    bad_skill_md = bad_sub / "SKILL.md"
    bad_skill_md.write_text("valid text", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        caplog.clear()
        with patch("app.loop.skills.loader.load_skill_from_path", side_effect=ValueError("Sub fail")):
            assert _scan_skills_in_directory(dir_with_broken, source="user") == []
            match_dir = [
                r for r in caplog.records
                if isinstance(r.args, tuple) and r.args and r.args[0] == bad_skill_md
            ]
            assert len(match_dir) == 1
            assert "Error loading skill from '%s': %s" in match_dir[0].msg

    # 2. Broken standalone markdown file
    dir_with_file = tmp_path / "broken_file_dir"
    dir_with_file.mkdir()
    bad_standalone = dir_with_file / "standalone.md"
    bad_standalone.write_text("valid text", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        caplog.clear()
        with patch("app.loop.skills.loader.load_skill_from_path", side_effect=ValueError("File fail")):
            assert _scan_skills_in_directory(dir_with_file, source="user") == []
            match_file = [
                r for r in caplog.records
                if isinstance(r.args, tuple) and r.args and r.args[0] == bad_standalone
            ]
            assert len(match_file) == 1
            assert "Error loading skill file '%s': %s" in match_file[0].msg


def test_discover_skills_all_directory_precedences(tmp_path: Path) -> None:
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()

    # User ~/.tddagents/skills
    user_tdd_skills = fake_home / ".tddagents" / "skills" / "user-tdd"
    user_tdd_skills.mkdir(parents=True)
    (user_tdd_skills / "SKILL.md").write_text(
        "---\nname: user-tdd\ndescription: user tdd\n---\nBody",
        encoding="utf-8",
    )

    # User ~/.claude/skills
    user_claude_skills = fake_home / ".claude" / "skills" / "user-claude"
    user_claude_skills.mkdir(parents=True)
    (user_claude_skills / "SKILL.md").write_text(
        "---\nname: user-claude\ndescription: user claude\n---\nBody",
        encoding="utf-8",
    )

    # Project .tddagents/skills
    proj_dir = tmp_path / "project_root"
    proj_tdd_skills = proj_dir / ".tddagents" / "skills" / "user-tdd"
    proj_tdd_skills.mkdir(parents=True)
    (proj_tdd_skills / "SKILL.md").write_text(
        "---\nname: user-tdd\ndescription: proj override tdd\n---\nBody",
        encoding="utf-8",
    )

    # Project .claude/skills
    proj_claude_skills = proj_dir / ".claude" / "skills" / "user-claude"
    proj_claude_skills.mkdir(parents=True)
    (proj_claude_skills / "SKILL.md").write_text(
        "---\nname: user-claude\ndescription: proj override claude\n---\nBody",
        encoding="utf-8",
    )

    # Builtin dir
    custom_builtin = tmp_path / "builtin"
    custom_builtin.mkdir()
    builtin_skill = custom_builtin / "builtin-skill"
    builtin_skill.mkdir()
    (builtin_skill / "SKILL.md").write_text(
        "---\nname: builtin-skill\ndescription: builtin\n---\nBody",
        encoding="utf-8",
    )

    with patch("pathlib.Path.home", return_value=fake_home):
        registry = discover_skills(project_root=proj_dir, built_in_dir=custom_builtin)
        assert len(registry) == 3

        s_tdd = registry.get("user-tdd")
        assert s_tdd is not None
        assert s_tdd.source == "project"
        assert s_tdd.description == "proj override tdd"

        s_claude = registry.get("user-claude")
        assert s_claude is not None
        assert s_claude.source == "project"
        assert s_claude.description == "proj override claude"

        s_builtin = registry.get("builtin-skill")
        assert s_builtin is not None
        assert s_builtin.source == "built-in"


def test_discover_skills_default_builtin_path() -> None:
    # Calling discover_skills() without built_in_dir verifies default_built_in path
    registry = discover_skills()
    assert "tdd-test-design" in registry


def test_skill_registry_methods() -> None:
    s1 = SkillDefinition(name="s1", description="desc1", body="body1")
    s2 = SkillDefinition(name="s2", description="desc2", body="body2")
    s1_dup = SkillDefinition(name="s1", description="desc1_dup", body="body1_dup")

    reg = SkillRegistry({"s1": s1})
    assert len(reg) == 1
    assert "s1" in reg
    assert "s2" not in reg
    assert reg.names() == ("s1",)
    assert reg.list_skills() == (s1,)

    reg.register(s1_dup, overwrite=False)
    assert reg.get("s1") == s1

    reg.register(s1_dup, overwrite=True)
    assert reg.get("s1") == s1_dup

    reg.register(s2)
    assert len(reg) == 2
    assert "s2" in reg


# ============================================================================
# 3. Activation: Token Estimation, Budget Filtering & Prompt Rendering
# ============================================================================

def test_estimate_skill_frontmatter_tokens_exact() -> None:
    skill_full = SkillDefinition(
        name="test-skill",
        description="A clear description.",
        body="Body not counted",
        when_to_use="When in RED state.",
        argument_hint="[file]",
    )
    expected_full = estimate_string_tokens("test-skill A clear description. When in RED state. [file]")
    assert estimate_skill_frontmatter_tokens(skill_full) == expected_full

    skill_minimal = SkillDefinition(
        name="min-skill",
        description="Just desc.",
        body="Body",
        when_to_use=None,
        argument_hint=None,
    )
    expected_min = estimate_string_tokens("min-skill Just desc.")
    assert estimate_skill_frontmatter_tokens(skill_minimal) == expected_min


def test_filter_skills_by_budget_boundary_and_skipping(caplog: pytest.LogCaptureFixture) -> None:
    s1 = SkillDefinition(name="s1", description="alpha", body="b")
    s2 = SkillDefinition(name="s2", description="beta large description with many words for high cost", body="b")
    s3 = SkillDefinition(name="s3", description="gamma", body="b")

    cost1 = estimate_skill_frontmatter_tokens(s1)
    cost2 = estimate_skill_frontmatter_tokens(s2)
    cost3 = estimate_skill_frontmatter_tokens(s3)

    skills = (s1, s2, s3)

    assert filter_skills_by_budget(skills, -1) == skills
    assert filter_skills_by_budget(skills, 0) == skills

    exact_budget = cost1 + cost3
    with caplog.at_level(logging.DEBUG):
        budgeted = filter_skills_by_budget(skills, exact_budget)
        assert budgeted == (s1, s3)
        omitted_rec = [r for r in caplog.records if f"Skill 's2' (tokens={cost2}) omitted" in r.message]
        assert len(omitted_rec) == 1
        assert omitted_rec[0].args == ("s2", cost2, cost1 + cost2, exact_budget)
        assert omitted_rec[0].msg == "Skill '%s' (tokens=%d) omitted: exceeds progressive disclosure budget (%d/%d)"


def test_render_skills_prompt_section_exact_strings() -> None:
    assert render_skills_prompt_section([]) == ""

    s1 = SkillDefinition(name="alpha", description="First skill", body="b")
    assert render_skills_prompt_section([s1], token_budget=1) == ""

    s_none = SkillDefinition(name="plain", description="Plain desc", body="b")
    rendered_none = render_skills_prompt_section([s_none])
    expected_none = (
        "<available_skills>\n"
        "The following specialized skills provide domain procedures and can be executed via the `Skill` tool:\n\n"
        "- **plain**: Plain desc\n"
        "</available_skills>"
    )
    assert rendered_none == expected_none

    s_when = SkillDefinition(name="w_skill", description="Desc", body="b", when_to_use="When X")
    rendered_when = render_skills_prompt_section([s_when])
    assert "- **w_skill**: Desc (When: When X)" in rendered_when

    s_args = SkillDefinition(name="a_skill", description="Desc", body="b", argument_hint="<arg>")
    rendered_args = render_skills_prompt_section([s_args])
    assert "- **a_skill**: Desc (Args: <arg>)" in rendered_args

    s_both = SkillDefinition(
        name="both_skill",
        description="Desc",
        body="b",
        when_to_use="When X",
        argument_hint="<arg>",
    )
    rendered_both = render_skills_prompt_section([s_both])
    assert "- **both_skill**: Desc (When: When X; Args: <arg>)" in rendered_both


# ============================================================================
# 4. Activation: Glob & Path Matching Branches
# ============================================================================

def test_is_path_matching_pattern_exhaustive_branches() -> None:
    # 1. Windows path separators normalized
    assert is_path_matching_pattern("tests\\unit\\test_sub.py", "tests/**") is True
    assert is_path_matching_pattern("tests\\test_one.py", "test_*.py") is True

    # 2. norm_pat.endswith("/**")
    assert is_path_matching_pattern("tests", "tests/**") is True
    assert is_path_matching_pattern("tests/sub/sub2", "tests/**") is True
    assert is_path_matching_pattern("other/tests", "tests/**") is False

    # 3. norm_pat.endswith("/")
    assert is_path_matching_pattern("tests", "tests/") is True
    assert is_path_matching_pattern("tests/sub", "tests/") is True
    assert is_path_matching_pattern("app/tests", "tests/") is False

    # 4. Leading "**/" without internal "**"
    assert is_path_matching_pattern("app/deep/test_file.py", "**/test_*.py") is True
    assert is_path_matching_pattern("test_file.py", "**/test_*.py") is True
    assert is_path_matching_pattern("app/deep/other.py", "**/test_*.py") is False

    # 5. Leading "**/" WITH internal "**"
    assert is_path_matching_pattern("app/deep/components/sub/Button.tsx", "**/components/**/*.tsx") is True
    assert is_path_matching_pattern("app/deep/components/Button.tsx", "**/components/*.tsx") is True
    assert is_path_matching_pattern("app/deep/other/Button.tsx", "**/components/**/*.tsx") is False

    # 6. Middle "**" recursive matching
    assert is_path_matching_pattern("app/core/helpers/util.py", "app/**/util.py") is True
    assert is_path_matching_pattern("app/util.py", "app/**/util.py") is True
    assert is_path_matching_pattern("tests/core/helpers/util.py", "app/**/util.py") is False

    # 7. No "/" in pattern (matches filename/basename)
    assert is_path_matching_pattern("src/components/Modal.tsx", "*.tsx") is True
    assert is_path_matching_pattern("src/components/Modal.tsx", "*.jsx") is False

    # 8. Single star with directory prefix (does not cross slash)
    assert is_path_matching_pattern("tests/test_foo.py", "tests/*.py") is True
    assert is_path_matching_pattern("tests/unit/test_foo.py", "tests/*.py") is False


# ============================================================================
# 5. Tool: Build Skill Tool, Schema, Permissions & Invocation
# ============================================================================

@pytest.mark.anyio
async def test_build_skill_tool_metadata_and_permissions() -> None:
    registry = SkillRegistry()
    tool = build_skill_tool(registry)

    # Tool identity
    assert tool.name == "Skill"
    assert tool.prompt == SKILL_TOOL_PROMPT

    # Permissions
    assert tool.is_read_only({}) is True
    assert tool.is_concurrency_safe({}) is True

    # Exact Schema
    assert tool.input_schema == {
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": "The unique name of the skill to execute.",
            },
            "args": {
                "type": "string",
                "description": "Optional arguments or parameters for the skill.",
            },
        },
        "required": ["skill_name"],
    }


@pytest.mark.anyio
async def test_build_skill_tool_empty_name_and_skill_alias() -> None:
    registry = SkillRegistry()
    s = SkillDefinition(name="alpha", description="Alpha skill", body="Alpha instructions")
    registry.register(s)
    tool = build_skill_tool(registry)

    store = AppStateStore(AppState())
    ctx = tool_context_for(store)

    # 1. Missing parameter
    res1 = await tool.call({}, ctx)
    assert res1.is_error is True
    assert res1.content == "Error: 'skill_name' parameter is required."

    # 2. Whitespace-only name
    res2 = await tool.call({"skill_name": "   "}, ctx)
    assert res2.is_error is True
    assert res2.content == "Error: 'skill_name' parameter is required."

    # 3. Invocation using 'skill' key alias and verify exact content string
    res3 = await tool.call({"skill": "alpha"}, ctx)
    assert res3.is_error is False
    assert res3.content == '<command-message name="alpha">\nAlpha instructions\n</command-message>'


@pytest.mark.anyio
async def test_build_skill_tool_missing_skill_available_list() -> None:
    # 1. Empty registry lists 'none'
    empty_reg = SkillRegistry()
    tool1 = build_skill_tool(empty_reg)
    store = AppStateStore(AppState())
    ctx = tool_context_for(store)

    res1 = await tool1.call({"skill_name": "missing"}, ctx)
    assert res1.is_error is True
    assert res1.content == "Skill 'missing' not found. Available skills: none."

    # 2. Registry with multiple skills formats list
    populated_reg = SkillRegistry({
        "skill-a": SkillDefinition(name="skill-a", description="a", body=""),
        "skill-b": SkillDefinition(name="skill-b", description="b", body=""),
    })
    tool2 = build_skill_tool(populated_reg)
    res2 = await tool2.call({"skill_name": "missing"}, ctx)
    assert res2.is_error is True
    assert res2.content == "Skill 'missing' not found. Available skills: 'skill-a', 'skill-b'."


@pytest.mark.anyio
async def test_build_skill_tool_base_dir_and_references() -> None:
    store = AppStateStore(AppState())
    ctx = tool_context_for(store)

    # 1. Skill with empty base_dir has no Base directory message
    s_no_dir = SkillDefinition(name="no-dir", description="", body="Body without dir", base_dir="")
    reg1 = SkillRegistry({"no-dir": s_no_dir})
    tool1 = build_skill_tool(reg1)
    res1 = await tool1.call({"skill_name": "no-dir"}, ctx)
    assert res1.content == '<command-message name="no-dir">\nBody without dir\n</command-message>'

    # 2. Skill with multiple references
    s_with_refs = SkillDefinition(
        name="with-refs",
        description="",
        body="Body with refs",
        base_dir="/path/to/skill",
        references={
            "ref_b.md": "Reference B content",
            "ref_a.md": "Reference A content",
        },
    )
    reg2 = SkillRegistry({"with-refs": s_with_refs})
    tool2 = build_skill_tool(reg2)
    res2 = await tool2.call({"skill_name": "with-refs"}, ctx)
    expected_content = (
        '<command-message name="with-refs">\n'
        'Base directory for this skill: /path/to/skill\n\n'
        'Body with refs\n\n'
        '---\n'
        '### Reference Documentation:\n\n'
        '#### ref_a.md\nReference A content\n\n'
        '#### ref_b.md\nReference B content\n'
        '</command-message>'
    )
    assert res2.content == expected_content
    assert res2.is_error is False


def test_substitute_arguments_placeholders() -> None:
    assert substitute_arguments("Run $ARGUMENTS now", "tests/foo.py") == "Run tests/foo.py now"
    assert substitute_arguments("Run {{args}} now", "tests/foo.py") == "Run tests/foo.py now"
    assert substitute_arguments("Run {{ARGUMENTS}} now", "tests/foo.py") == "Run tests/foo.py now"
    assert substitute_arguments("No placeholders", "tests/foo.py") == "No placeholders"
