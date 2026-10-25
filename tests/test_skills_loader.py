"""Phase 8: skill loading (`skills/loadSkillsDir.ts`, `skills/bundledSkills.ts`,
`commands.ts` -> `loadAllCommands`, `findCommand`, `getSkillToolCommands`)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.loop.skills.loader import (
    BUNDLED_SKILLS_DIR,
    SkillRegistry,
    get_project_dirs_up_to_home,
    get_skill_registry,
    load_skills_from_skills_dir,
    parse_skill_frontmatter_fields,
    parse_skill_paths,
    parse_slash_command_tools_from_frontmatter,
)


def _skill(root: Path, name: str, text: str) -> Path:
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestFrontmatterFields:
    def test_every_upstream_field(self):
        fm = {
            "name": "Pretty", "description": "  does x  ", "allowed-tools": ["Bash(git:*)", "ReadFile"],
            "argument-hint": "[path]", "arguments": "first second", "when_to_use": "when y",
            "version": "1", "model": "haiku", "disable-model-invocation": "true", "user-invocable": False,
            "context": "fork", "agent": "Explore", "hooks": {"Stop": []},
        }
        fields = parse_skill_frontmatter_fields(fm, "# Heading\nbody", "dir-name")
        assert fields["display_name"] == "Pretty"
        assert (fields["description"], fields["has_user_specified_description"]) == ("does x", True)
        assert fields["allowed_tools"] == ("Bash(git:*)", "ReadFile")
        assert (fields["argument_hint"], fields["argument_names"]) == ("[path]", ("first", "second"))
        assert (fields["when_to_use"], fields["version"]) == ("when y", "1")
        assert fields["model"] == "claude-haiku-4-5-20251001"
        assert (fields["disable_model_invocation"], fields["user_invocable"]) == (True, False)
        assert (fields["context"], fields["agent"], fields["hooks"]) == ("fork", "Explore", {"Stop": []})

    def test_defaults_and_fallbacks(self):
        fields = parse_skill_frontmatter_fields({}, "\n\n## Use it well\nmore", "x")
        assert fields["display_name"] is None
        assert (fields["description"], fields["has_user_specified_description"]) == ("Use it well", False)
        assert fields["allowed_tools"] == ()
        assert (fields["model"], fields["context"], fields["user_invocable"]) == (None, None, True)
        assert fields["disable_model_invocation"] is False

    def test_inherit_model_and_unknown_context(self):
        fields = parse_skill_frontmatter_fields({"model": "inherit", "context": "inline"}, "b", "x")
        assert (fields["model"], fields["context"]) == (None, None)

    def test_description_coercion(self):
        assert parse_skill_frontmatter_fields({"description": 3}, "b", "x")["description"] == "3"
        listy = parse_skill_frontmatter_fields({"description": ["no"]}, "fallback line", "x")
        assert (listy["description"], listy["has_user_specified_description"]) == ("fallback line", False)
        assert parse_skill_frontmatter_fields({"description": "   "}, "", "x")["description"] == "Skill"
        long_line = "a" * 120
        assert parse_skill_frontmatter_fields({}, long_line, "x")["description"] == "a" * 97 + "..."

    def test_argument_names_drop_numbers_and_blanks(self):
        fields = parse_skill_frontmatter_fields({"arguments": ["a", "2", " ", "b"]}, "b", "x")
        assert fields["argument_names"] == ("a", "b")


def test_tool_lists():
    assert parse_slash_command_tools_from_frontmatter(None) == ()
    assert parse_slash_command_tools_from_frontmatter("") == ()
    assert parse_slash_command_tools_from_frontmatter("Bash(git add:*), ReadFile") == ("Bash(git add:*)", "ReadFile")
    assert parse_slash_command_tools_from_frontmatter(["ReadFile", 3, "*"]) == ("*",)
    assert parse_slash_command_tools_from_frontmatter(42) == ()


def test_paths():
    assert parse_skill_paths({}) is None
    assert parse_skill_paths({"paths": "**"}) is None
    assert parse_skill_paths({"paths": ["src/**", "tests/*.py"]}) == ("src", "tests/*.py")
    assert parse_skill_paths({"paths": "a, b/**"}) == ("a", "b")


def test_a_skills_dir_loads_directory_skills_only(tmp_path):
    _skill(tmp_path, "good", "---\ndescription: d\nname: Shown\n---\nBody $ARGUMENTS")
    (tmp_path / "loose.md").write_text("---\ndescription: no\n---\nx")
    (tmp_path / "empty-dir").mkdir()
    _skill(tmp_path, "badyaml", "---\ndescription: [unclosed\n---\nStill a skill")
    loaded = load_skills_from_skills_dir(tmp_path, "userSettings")
    by_name = {s.name: (s, p) for s, p in loaded}
    assert set(by_name) == {"good", "badyaml"}
    good, path = by_name["good"]
    assert path == tmp_path / "good" / "SKILL.md"
    assert (good.display_name, good.description, good.source, good.loaded_from) == (
        "Shown", "d", "userSettings", "skills")
    assert good.skill_root == str(tmp_path / "good")
    assert by_name["badyaml"][0].description == "Still a skill"
    assert load_skills_from_skills_dir(tmp_path / "missing", "userSettings") == []


def test_project_dirs_walk_up_to_the_git_root(tmp_path):
    repo = tmp_path / "home" / "mid" / "repo"
    deep = repo / "pkg" / "sub"
    deep.mkdir(parents=True)
    (repo / ".git").mkdir()
    # `mid` sits between the git root and home: the walk must stop at the root, not reach it.
    for d in (repo, repo / "pkg", tmp_path / "home" / "mid", tmp_path / "home"):
        (d / ".tddagents" / "skills").mkdir(parents=True)
    dirs = get_project_dirs_up_to_home(deep, home=tmp_path / "home")
    assert dirs == [repo / "pkg" / ".tddagents" / "skills", repo / ".tddagents" / "skills"]


def test_project_dirs_stop_at_home_without_git(tmp_path):
    home = tmp_path / "home"
    work = home / "a" / "b"
    work.mkdir(parents=True)
    (home / "a" / ".tddagents" / "skills").mkdir(parents=True)
    (home / ".tddagents" / "skills").mkdir(parents=True)
    assert get_project_dirs_up_to_home(work, home=home) == [home / "a" / ".tddagents" / "skills"]


def _registry(tmp_path, **kw) -> SkillRegistry:
    kw.setdefault("bundled_dir", tmp_path / "bundled")
    kw.setdefault("managed_dir", tmp_path / "etc")
    kw.setdefault("user_home", tmp_path / "home")
    return get_skill_registry(tmp_path / "home" / "proj", **kw)


class TestRegistry:
    def test_sources_load_in_upstream_order_and_first_match_wins(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TDDAGENTS_DISABLE_POLICY_SKILLS", raising=False)
        monkeypatch.delenv("TDDAGENTS_CONFIG_DIR", raising=False)
        (tmp_path / "home" / "proj").mkdir(parents=True)
        _skill(tmp_path / "bundled", "same", "---\ndescription: bundled\n---\nB")
        _skill(tmp_path / "etc" / ".tddagents" / "skills", "same", "---\ndescription: policy\n---\nP")
        _skill(tmp_path / "etc" / ".tddagents" / "skills", "pol", "---\ndescription: p\n---\nP")
        _skill(tmp_path / "home" / ".tddagents" / "skills", "usr", "---\ndescription: u\n---\nU")
        _skill(tmp_path / "home" / "proj" / ".tddagents" / "skills", "prj", "---\ndescription: j\n---\nJ")
        reg = _registry(tmp_path)
        assert [(c.name, c.source) for c in reg.commands()] == [
            ("same", "bundled"), ("pol", "policySettings"), ("same", "policySettings"), ("usr", "userSettings"),
            ("prj", "projectSettings")]
        found = reg.find_command("same")
        assert found is not None and found.source == "bundled"
        assert found.loaded_from == "bundled" and found.has_user_specified_description is True

    def test_policy_skills_can_be_disabled(self, tmp_path, monkeypatch):
        (tmp_path / "home" / "proj").mkdir(parents=True)
        _skill(tmp_path / "etc" / ".tddagents" / "skills", "pol", "---\ndescription: p\n---\nP")
        monkeypatch.setenv("TDDAGENTS_DISABLE_POLICY_SKILLS", "1")
        assert _registry(tmp_path).commands() == []

    def test_the_same_file_twice_is_loaded_once(self, tmp_path):
        proj = tmp_path / "home" / "proj"
        proj.mkdir(parents=True)
        _skill(tmp_path / "home" / ".tddagents" / "skills", "shared", "---\ndescription: d\n---\nS")
        (proj / ".tddagents").mkdir()
        (proj / ".tddagents" / "skills").symlink_to(tmp_path / "home" / ".tddagents" / "skills")
        assert [c.source for c in _registry(tmp_path).commands()] == ["userSettings"]

    def test_find_command_matches_name_or_display_name(self, tmp_path):
        (tmp_path / "home" / "proj").mkdir(parents=True)
        _skill(tmp_path / "bundled", "dir-name", "---\ndescription: d\nname: Display\n---\nX")
        reg = _registry(tmp_path)
        assert reg.find_command("dir-name") is reg.find_command("Display") is not None
        assert reg.find_command("nope") is None

    def test_skill_tool_commands_exclude_model_disabled(self, tmp_path):
        (tmp_path / "home" / "proj").mkdir(parents=True)
        _skill(tmp_path / "bundled", "on", "---\ndescription: d\n---\nX")
        _skill(tmp_path / "bundled", "off", "---\ndescription: d\ndisable-model-invocation: true\n---\nX")
        assert [c.name for c in _registry(tmp_path).get_skill_tool_commands()] == ["on"]

    def test_bundled_skills_never_go_conditional(self, tmp_path):
        (tmp_path / "home" / "proj").mkdir(parents=True)
        _skill(tmp_path / "bundled", "b", "---\ndescription: d\npaths: [src/**]\n---\nX")
        cmd = _registry(tmp_path).find_command("b")
        assert cmd is not None and cmd.paths is None

    def test_conditional_skills_wait_for_a_matching_path(self, tmp_path):
        proj = tmp_path / "home" / "proj"
        proj.mkdir(parents=True)
        _skill(proj / ".tddagents" / "skills", "py", "---\ndescription: d\npaths: [src/**]\n---\nX")
        reg = _registry(tmp_path)
        assert reg.find_command("py") is None
        assert reg.activate_conditional_skills_for_paths([str(proj / "README.md")], str(proj)) == []
        assert reg.activate_conditional_skills_for_paths([str(tmp_path / "out" / "src" / "a.py")], str(proj)) == []
        assert reg.activate_conditional_skills_for_paths(["src/app.py"], str(proj)) == ["py"]
        assert reg.find_command("py") is not None
        assert reg.activate_conditional_skills_for_paths(["src/app.py"], str(proj)) == []

    def test_an_activated_skill_never_shadows_a_base_command(self, tmp_path):
        proj = tmp_path / "home" / "proj"
        proj.mkdir(parents=True)
        _skill(tmp_path / "bundled", "dup", "---\ndescription: bundled\n---\nX")
        _skill(proj / ".tddagents" / "skills", "dup", "---\ndescription: cond\npaths: [src/**]\n---\nX")
        reg = _registry(tmp_path)
        reg.activate_conditional_skills_for_paths(["src/a.py"], str(proj))
        assert [c.description for c in reg.commands()] == ["bundled"]


def test_the_shipped_roster_is_bundled_and_invocable_headless():
    """TDDAgents' three skills: unconditional and with no extra permissions, so the Skill
    tool's safe-properties check allows them without a prompt."""
    reg = get_skill_registry(BUNDLED_SKILLS_DIR, bundled_dir=BUNDLED_SKILLS_DIR, user_home=Path("/nonexistent"),
                             managed_dir=Path("/nonexistent"))
    names = [c.name for c in reg.get_skill_tool_commands()]
    assert names == ["tdd-mutation-defense", "tdd-refactor-clean", "tdd-test-design"]
    for cmd in reg.commands():
        assert (cmd.source, cmd.loaded_from, cmd.allowed_tools, cmd.paths) == ("bundled", "bundled", (), None)
        assert cmd.when_to_use and cmd.argument_hint


@pytest.mark.parametrize("name", ["tdd-test-design", "tdd-refactor-clean", "tdd-mutation-defense"])
def test_each_shipped_skill_references_its_material_relative_to_its_base_dir(name):
    reg = get_skill_registry(BUNDLED_SKILLS_DIR, bundled_dir=BUNDLED_SKILLS_DIR, user_home=Path("/nonexistent"),
                             managed_dir=Path("/nonexistent"))
    cmd = reg.find_command(name)
    assert cmd is not None
    text = cmd.prompt_for_command("")
    assert text.startswith(f"Base directory for this skill: {BUNDLED_SKILLS_DIR / name}\n\n")
    assert "references/" in text


# ── mutation-gate pins (Phase 8) ────────────────────────────────────────────

def test_frontmatter_keeps_the_bodys_own_leading_text():
    """`parseFrontmatter` drops only the newlines after the closing `---`."""
    from app.loop.skills.loader import parse_frontmatter

    assert parse_frontmatter("---\na: 1\n---\n\n  Xray\n") == ({"a": 1}, "  Xray\n")
    assert parse_frontmatter("---\na: [\n---\nbody") == ({}, "body")
    assert parse_frontmatter("---\na: 1\n---\nXray") == ({"a": 1}, "Xray")


def test_a_description_line_is_capped_past_100_characters():
    from app.loop.skills.loader import extract_description_from_markdown

    assert extract_description_from_markdown("x" * 100) == "x" * 100
    assert extract_description_from_markdown("x" * 101) == "x" * 97 + "..."


def test_user_invocable_is_read_when_present():
    assert parse_skill_frontmatter_fields({"user-invocable": True}, "", "s")["user_invocable"] is True
    assert parse_skill_frontmatter_fields({"user-invocable": "false"}, "", "s")["user_invocable"] is False


def test_a_file_among_the_skill_dirs_is_skipped_not_the_end(tmp_path):
    (tmp_path / "a-file.md").write_text("not a skill")
    _skill(tmp_path, "b-skill", "---\ndescription: b\n---\nB")
    assert [s.name for s, _ in load_skills_from_skills_dir(tmp_path, "userSettings")] == ["b-skill"]


def test_an_absolute_path_matches_relative_to_cwd_and_outsiders_are_skipped(tmp_path):
    def registry() -> SkillRegistry:
        from app.loop.skills.definition import SkillCommand

        late = SkillCommand(name="late", description="d", markdown_content="x", source="projectSettings",
                            loaded_from="skills", skill_root=None, paths=("src/**",))
        return SkillRegistry([], [late])

    reg = registry()
    assert reg.activate_conditional_skills_for_paths([str(tmp_path / "src" / "a.py")], str(tmp_path)) == ["late"]
    reg = registry()
    outside = str(tmp_path.parent / "elsewhere.py")
    assert reg.activate_conditional_skills_for_paths([outside, "src/a.py"], str(tmp_path)) == ["late"]


def test_the_user_scope_defaults_to_the_config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "cfg"))
    _skill(tmp_path / "cfg" / "skills", "mine", "---\ndescription: m\n---\nM")
    (tmp_path / "proj").mkdir()
    reg = get_skill_registry(tmp_path / "proj", None, tmp_path / "etc", bundled_dir=tmp_path / "bundled")
    assert reg.find_command("mine") is not None


def test_policy_skills_off_accepts_any_truthy_spelling(tmp_path, monkeypatch):
    _skill(tmp_path / "etc" / ".tddagents" / "skills", "pol", "---\ndescription: p\n---\nP")
    monkeypatch.setenv("TDDAGENTS_DISABLE_POLICY_SKILLS", "True")
    assert _registry(tmp_path).find_command("pol") is None


def test_the_project_walk_stops_at_the_given_home(tmp_path):
    """Above `home`, a `.tddagents/skills` is never a project scope."""
    _skill(tmp_path / ".tddagents" / "skills", "above", "---\ndescription: a\n---\nA")
    (tmp_path / "home" / "proj").mkdir(parents=True)
    assert _registry(tmp_path).find_command("above") is None
