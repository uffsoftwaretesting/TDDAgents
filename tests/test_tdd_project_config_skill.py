"""
The `tdd-project-config` skill (`skills/bundled/claudeApi.ts`' pattern): the language from the
argument or the project's files, the inlined references, and its invocation as a skill.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import ClassVar

import pytest

from app.skills.bundled.index import init_bundled_skills
from app.skills.bundled_skills import BundledSkillContext
from app.skills.load_skills_dir import get_skill_registry
from app.tdd.project_config_skill import (
    LANGUAGE_INDICATORS,
    LANGUAGES,
    SKILL_DIR,
    build_inline_reference,
    build_prompt,
    detect_language,
    ecosystem_listing,
    get_files_for_language,
    get_prompt_for_command,
    language_from_args,
    process_content,
    skill_files,
    tdd_project_config_skill,
)
from app.tdd.project_config_vars import (
    COVERAGE_REPORT_ENV,
    JUNIT_REPORT_ENV,
    PROJECT_CONFIG_FILE,
    PROJECT_CONFIG_HEADING,
    PROJECT_CONFIG_PROMPT_VARS,
    PROJECT_CONFIG_VERSION,
)
from app.tdd.coverage_gate import COVERAGE_RULE_OF_THUMB, TDD_PROMPT_VARS
from app.tools.skill_tool.skill_tool import skill_has_only_safe_properties
from app.utils.process_user_input.process_slash_command import (
    get_messages_for_prompt_slash_command,
)

LANGUAGE_FILES = sorted(f"references/{name}.md" for name in LANGUAGES)
_JSON_BLOCK = re.compile(r"```json\n(.*?)\n```", re.DOTALL)
_DOC = re.compile(r'<doc path="([^"]+)">')


def _prompt(args: str = "", cwd: str = "/nonexistent") -> str:
    return get_prompt_for_command(args, BundledSkillContext(cwd=cwd))


class TestDetectLanguage:
    @pytest.mark.parametrize("entry, language", [
        ("main.py", "python"), ("requirements.txt", "python"), ("pyproject.toml", "python"),
        ("setup.py", "python"), ("Pipfile", "python"),
        ("index.ts", "javascript-typescript"), ("App.tsx", "javascript-typescript"),
        ("index.js", "javascript-typescript"), ("App.jsx", "javascript-typescript"),
        ("lib.mjs", "javascript-typescript"), ("lib.cjs", "javascript-typescript"),
        ("tsconfig.json", "javascript-typescript"), ("package.json", "javascript-typescript"),
        ("Main.java", "java"), ("pom.xml", "java"), ("build.gradle", "java"), ("build.gradle.kts", "java"),
        ("main.go", "go"), ("go.mod", "go"),
        ("lib.rs", "rust"), ("Cargo.toml", "rust"),
        ("app.rb", "ruby"), ("Gemfile", "ruby"),
        ("Program.cs", "csharp"), ("App.csproj", "csharp"), ("App.sln", "csharp"),
        ("index.php", "php"), ("composer.json", "php"),
    ])
    def test_each_indicator(self, tmp_path, entry, language):
        (tmp_path / entry).write_text("")
        assert detect_language(str(tmp_path)) == language

    def test_the_indicator_order_is_the_precedence(self, tmp_path):
        for name in ("package.json", "go.mod", "requirements.txt"):
            (tmp_path / name).write_text("")
        assert detect_language(str(tmp_path)) == "python"
        assert list(LANGUAGE_INDICATORS)[:2] == ["python", "javascript-typescript"]

    def test_a_file_name_must_match_whole(self, tmp_path):
        (tmp_path / "my-package.json.bak").write_text("")
        (tmp_path / "notes.txt").write_text("")
        assert detect_language(str(tmp_path)) is None

    def test_only_the_top_level_is_read(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.go").write_text("")
        assert detect_language(str(tmp_path)) is None

    def test_an_empty_or_missing_directory(self, tmp_path):
        assert detect_language(str(tmp_path)) is None
        assert detect_language(str(tmp_path / "missing")) is None

    def test_every_ecosystem_has_indicators_and_a_reference(self):
        assert set(LANGUAGE_INDICATORS) == set(LANGUAGES)
        assert sorted(skill_files()) == sorted([*LANGUAGE_FILES, "references/shared.md"])


class TestLanguageFromArgs:
    @pytest.mark.parametrize("args, language", [
        ("python", "python"), ("Py", "python"), ("ts", "javascript-typescript"),
        ("TypeScript with vitest", "javascript-typescript"), ("node", "javascript-typescript"),
        ("javascript-typescript", "javascript-typescript"), ("golang", "go"), ("rust", "rust"),
        ("java", "java"), ("C#", "csharp"), ("dotnet", "csharp"), (".NET", "csharp"),
        ("ruby", "ruby"), ("php", "php"),
    ])
    def test_the_first_word_names_it(self, args, language):
        assert language_from_args(args) == language

    @pytest.mark.parametrize("args", ["", "   ", "cobol", "build me a python app"])
    def test_anything_else_names_none(self, args):
        assert language_from_args(args) is None


class TestProcessContent:
    def test_comments_are_stripped_until_none_is_left(self):
        """`processContent`'s loop: removing one comment may reveal another."""
        assert process_content("a<!-- x -->\nb<!<!-- y -->-- z -->\nc", {}) == "abc"

    def test_known_placeholders_render_and_unknown_stay_visible(self):
        assert process_content("{{A}} {{B}} {A} ${A}", {"A": "1"}) == "1 {{B}} {A} ${A}"

    def test_the_inline_reference_tags_each_file_in_path_order(self):
        files = {"references/b.md": "  B {{X}}\n", "references/a.md": "A", "references/e.md": ""}
        text = build_inline_reference(["references/b.md", "references/a.md", "references/e.md", "nope"], files,
                                      {"X": "x"})
        assert text == '<doc path="references/a.md">\nA\n</doc>\n\n<doc path="references/b.md">\nB x\n</doc>'

    def test_files_for_a_language(self):
        files = {p: "x" for p in [*LANGUAGE_FILES, "references/shared.md"]}
        assert get_files_for_language("go", files) == ["references/go.md", "references/shared.md"]
        assert get_files_for_language("cobol", files) == ["references/shared.md"]


class TestBuildPrompt:
    PROMPT = (
        "# T\nintro {{PROJECT_CONFIG_HEADING}}\n\n## Reading Guide\nfor {{LANGUAGE}}\n\n"
        "## No Language Detected\npick one:\n{{ECOSYSTEMS}}\n\n## Common Pitfalls\n- p\n"
    )
    FILES: ClassVar[dict[str, str]] = {
        "references/go.md": "GO", "references/rust.md": "RUST", "references/shared.md": "SHARED"}

    def test_with_a_language(self):
        assert build_prompt("go", "go fast", self.PROMPT, self.FILES) == (
            f"# T\nintro {PROJECT_CONFIG_HEADING}\n\n## Reading Guide\nfor go\n\n"
            "---\n\n## Included Documentation\n\n"
            '<doc path="references/go.md">\nGO\n</doc>\n\n<doc path="references/shared.md">\nSHARED\n</doc>\n\n'
            "## Common Pitfalls\n- p\n\n## User Request\n\ngo fast"
        )

    def test_without_a_language(self):
        assert build_prompt(None, "", self.PROMPT, self.FILES) == (
            f"# T\nintro {PROJECT_CONFIG_HEADING}\n\n## No Language Detected\npick one:\n{ecosystem_listing()}\n\n"
            "---\n\n## Included Documentation\n\n"
            '<doc path="references/shared.md">\nSHARED\n</doc>\n\n## Common Pitfalls\n- p'
        )

    def test_a_prompt_without_the_sections(self):
        assert build_prompt("go", "", "# Only\n", self.FILES) == (
            "# Only\n\n---\n\n## Included Documentation\n\n"
            '<doc path="references/go.md">\nGO\n</doc>\n\n<doc path="references/shared.md">\nSHARED\n</doc>'
        )

    def test_the_ecosystem_listing(self):
        listing = ecosystem_listing().splitlines()
        assert listing[0] == "- `python`: Python" and len(listing) == len(LANGUAGES)


class TestShippedPrompt:
    def test_the_argument_picks_the_language_over_the_project(self, tmp_path):
        (tmp_path / "go.mod").write_text("")
        text = _prompt("rust", str(tmp_path))
        assert _DOC.findall(text) == ["references/rust.md", "references/shared.md"]
        assert text.endswith("## User Request\n\nrust")

    def test_the_project_picks_it_without_an_argument(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("")
        text = _prompt("", str(tmp_path))
        assert _DOC.findall(text) == ["references/python.md", "references/shared.md"]
        assert "The documentation for the `python` ecosystem" in text
        assert "## User Request" not in text and "## No Language Detected" not in text

    def test_with_nothing_detected_it_lists_the_ecosystems(self):
        text = _prompt("make it fast")
        assert _DOC.findall(text) == ["references/shared.md"]
        assert "## No Language Detected" in text and "## Reading Guide" not in text
        for language in LANGUAGES:
            assert f"- `{language}`: {LANGUAGES[language]}" in text
        assert text.endswith("## User Request\n\nmake it fast")

    @pytest.mark.parametrize("language", list(LANGUAGES))
    def test_every_reference_renders_and_its_examples_parse(self, language):
        text = _prompt(language)
        assert "{{" not in text and "<!--" not in text
        assert f"${JUNIT_REPORT_ENV}" in text and PROJECT_CONFIG_FILE in text
        doc = text[text.index(f'<doc path="references/{language}.md">'):]
        configs = [json.loads(block) for block in _JSON_BLOCK.findall(doc)]
        configs = [c for c in configs if "test" in c]
        assert configs, f"{language} has no example configuration"
        for config in configs:
            assert config["version"] == PROJECT_CONFIG_VERSION
            assert set(config) <= {"version", "setup", "test", "junitReport", "coverageReport", "testFiles",
                                   "testSupportFiles", "manifestFiles", "exclude"}
            assert config["testFiles"]
            for support in config.get("testSupportFiles", []):
                assert support in config["testFiles"] or any(
                    t.endswith("/") and support.startswith(t) for t in config["testFiles"]), support
            assert config.get("coverageReport", {"format": "lcov"})["format"] in ("cobertura", "lcov")
            uses_env = JUNIT_REPORT_ENV in config["test"]
            assert uses_env or "junitReport" in config or JUNIT_REPORT_ENV in doc

    def test_no_value_tddagents_resolves_is_written_in_the_markdown(self):
        for path in [SKILL_DIR / "prompt.md", *(SKILL_DIR / "references").glob("*.md")]:
            raw = Path(path).read_text(encoding="utf-8")
            for value in (JUNIT_REPORT_ENV, COVERAGE_REPORT_ENV, PROJECT_CONFIG_FILE):
                assert value not in raw, f"{path.name} hard-codes {value}"
        assert set(PROJECT_CONFIG_PROMPT_VARS) == {
            "JUNIT_REPORT_ENV", "COVERAGE_REPORT_ENV", "PROJECT_CONFIG_HEADING", "PROJECT_CONFIG_FILE",
            "PROJECT_CONFIG_VERSION"}
        rule = f"{COVERAGE_RULE_OF_THUMB:.0f}%"
        for path in (SKILL_DIR / "prompt.md", SKILL_DIR / "references" / "shared.md"):
            raw = Path(path).read_text(encoding="utf-8")
            assert rule not in raw and "{{COVERAGE_RULE_OF_THUMB}}%" in raw, path.name


class TestCoverageAndReports:
    """What every ecosystem is told about coverage and the single JUnit report."""

    @pytest.mark.parametrize("language", [*LANGUAGES, None])
    def test_coverage_is_worth_declaring_with_the_rule_of_thumb(self, language):
        text = _prompt(language or "")
        shared = text[text.index('<doc path="references/shared.md">'):]
        rule = f"{TDD_PROMPT_VARS['COVERAGE_RULE_OF_THUMB']}% is a good rule of thumb, not a gate"
        assert f"{COVERAGE_RULE_OF_THUMB:.0f}" == TDD_PROMPT_VARS["COVERAGE_RULE_OF_THUMB"]
        assert "## Coverage\n\nDeclare a coverage report. On every run, RunTests reports the line and branch " \
               "coverage the declared report records, in Cobertura XML or LCOV" in shared
        assert rule in shared and "Coverage never decides whether a run passes." in shared
        assert "shows the untested code and helps raise quality" in text and rule in text

    def test_the_coverage_path_is_optional_and_defaults_to_the_variable(self):
        shared = (SKILL_DIR / "references" / "shared.md").read_text(encoding="utf-8")
        assert ('`{"format": "cobertura"}` or `{"format": "lcov"}`, with an optional `path`' in shared
                and "`path` defaults to `${{COVERAGE_REPORT_ENV}}`" in shared)

    @pytest.mark.parametrize("language", list(LANGUAGES))
    def test_each_example_writes_its_coverage_where_tddagents_reads_it(self, language):
        doc = _prompt(language)
        doc = doc[doc.index(f'<doc path="references/{language}.md">'):]
        for config in (json.loads(b) for b in _JSON_BLOCK.findall(doc)):
            if "coverageReport" in config and "path" not in config["coverageReport"]:
                assert COVERAGE_REPORT_ENV in config["test"] or COVERAGE_REPORT_ENV in doc, language

    def test_a_split_junit_report_is_joined_into_one(self):
        shared = (SKILL_DIR / "references" / "shared.md").read_text(encoding="utf-8")
        assert "It reads **one** JUnit report file" in shared
        assert "(Maven Surefire, Gradle, Minitest's JUnit reporter)" in shared
        java = (SKILL_DIR / "references" / "java.md").read_text(encoding="utf-8")
        ruby = (SKILL_DIR / "references" / "ruby.md").read_text(encoding="utf-8")
        for text in (java, ruby):
            assert "TDDAgents reads a single JUnit file" in text
        assert "a Minitest test command must join those files into one report" in ruby
        assert "TDDAgents does not read JaCoCo's XML report: it reads Cobertura or LCOV only." in java
        prompt = (SKILL_DIR / "prompt.md").read_text(encoding="utf-8")
        assert "TDDAgents reads a single report file, so the test command must join them into one." in prompt


class TestDefinition:
    def test_upstreams_trigger_form_and_flags(self):
        definition = tdd_project_config_skill()
        assert definition.name == "tdd-project-config" and definition.argument_hint == "[language]"
        lines = definition.description.splitlines()
        assert len(lines) == 3
        assert f"`## {PROJECT_CONFIG_HEADING}`" in lines[0] and PROJECT_CONFIG_FILE in lines[0]
        assert lines[1].startswith("TRIGGER when: planning")
        assert lines[2] == "DO NOT TRIGGER when: writing tests or production code, or running the tests."
        assert definition.user_invocable and not definition.disable_model_invocation
        assert definition.allowed_tools == () and definition.files is None

    def test_the_skill_tool_allows_it_without_asking(self, tmp_path):
        init_bundled_skills()
        reg = get_skill_registry(tmp_path, tmp_path / "home", tmp_path / "etc", bundled_dir=tmp_path / "none")
        command = reg.find_command("tdd-project-config")
        assert command is not None and skill_has_only_safe_properties(command)
        assert [c.name for c in reg.get_skill_tool_commands()] == ["update-config", "tdd-project-config"]


def test_the_skill_tool_launches_it(tmp_path):
    from tests.test_skills_tool import _call, _ctx, _tool

    (tmp_path / "Cargo.toml").write_text("")
    init_bundled_skills()
    reg = get_skill_registry(tmp_path, tmp_path / "home", tmp_path / "etc", bundled_dir=tmp_path / "none")
    command = reg.find_command("tdd-project-config")
    assert command is not None
    tool = _tool(command)
    result = _call(tool, {"skill": "tdd-project-config"}, _ctx())
    assert result.content == "Launching skill: tdd-project-config"
    (message,) = result.new_messages
    assert _DOC.findall(str(message.content)) == ["references/rust.md", "references/shared.md"]
    assert result.context_modifier is None


def test_a_typed_slash_command_takes_the_language(tmp_path):
    init_bundled_skills()
    reg = get_skill_registry(tmp_path, tmp_path / "home", tmp_path / "etc", bundled_dir=tmp_path / "none")
    command = reg.find_command("tdd-project-config")
    turn = get_messages_for_prompt_slash_command(command, "php", session_id="s")
    assert "<command-name>/tdd-project-config</command-name>" in str(turn.messages[0].content)
    assert _DOC.findall(str(turn.messages[1].content)) == ["references/php.md", "references/shared.md"]
    assert turn.allowed_tools == ()


def test_the_skill_is_offered_ahead_of_the_file_roster():
    from app.skills.load_skills_dir import BUNDLED_SKILLS_DIR

    init_bundled_skills()
    reg = get_skill_registry(BUNDLED_SKILLS_DIR, bundled_dir=BUNDLED_SKILLS_DIR, user_home=Path("/nonexistent"),
                             managed_dir=Path("/nonexistent"))
    assert [c.name for c in reg.get_skill_tool_commands()] == [
        "update-config", "tdd-project-config", "tdd-mutation-defense", "tdd-refactor-clean", "tdd-test-design"]
