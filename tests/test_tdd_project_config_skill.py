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
    NOSANDBOX_DIR,
    process_content,
    sandbox_enabled,
    skill_files,
    tdd_project_config_skill,
)
from app.tdd.plan import PROJECT_CONFIG_HEADING
from app.tdd.project_config import (
    COVERAGE_REPORT_ENV,
    JUNIT_REPORT_ENV,
    PROJECT_CONFIG_FILE,
    PROJECT_CONFIG_VERSION,
    parse_project_config,
)
from app.tdd.coverage_gate import COVERAGE_RULE_OF_THUMB
from app.tdd.prompt_vars import PROJECT_CONFIG_PROMPT_VARS, TDD_PROMPT_VARS
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
        """`buildPrompt` with no language (`claudeApi.ts` l.155-165): every file is inlined, in one call."""
        assert build_prompt(None, "", self.PROMPT, self.FILES) == (
            f"# T\nintro {PROJECT_CONFIG_HEADING}\n\n## No Language Detected\npick one:\n{ecosystem_listing()}\n\n"
            "---\n\n## Included Documentation\n\n"
            '<doc path="references/go.md">\nGO\n</doc>\n\n<doc path="references/rust.md">\nRUST\n</doc>\n\n'
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

    def test_with_nothing_detected_it_includes_every_ecosystem_in_one_call(self):
        """Upstream's `claude-api` (`claudeApi.ts` l.155-165): with no language, every reference is inlined and the
        model picks the matching one; it is never told to invoke the skill again (user decision 2026-11-21)."""
        text = _prompt("make it fast")
        assert _DOC.findall(text) == sorted([*LANGUAGE_FILES, "references/shared.md"])
        assert "## No Language Detected" in text and "## Reading Guide" not in text
        assert "invoke this skill again" not in text and "again" not in text.split("## No Language Detected")[1][:400]
        assert "Use the language the user's request names" in text
        for language in LANGUAGES:
            assert f"- `{language}`: {LANGUAGES[language]}" in text
        assert text.endswith("## User Request\n\nmake it fast")

    def test_an_empty_project_without_the_sandbox_gets_the_no_sandbox_texts(self, tmp_path):
        """Planning starts in an empty folder: the one call carries the default (no sandbox) references."""
        from app.tdd.project_config_skill import skill_files

        files = skill_files(sandboxed=False)
        text = _prompt("", str(tmp_path))
        assert len(_DOC.findall(text)) == len(files) == len(LANGUAGES) + 1
        for path in ("references/python.md", "references/shared.md", "references/go.md"):
            body = files[path].split("-->", 1)[-1].strip()[:120]
            assert body and body.split("{{")[0][:60] in text, path

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
            # Every example is a configuration `parse_project_config` accepts as it stands.
            assert parse_project_config(config)[1] == (), config
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
            "PROJECT_CONFIG_VERSION", "PROJECT_CONFIG_SKILL"}
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


class TestTheJoinRecipesProduceOneValidReport:
    """
    The live Java run (2026-11-10): Surefire's per-class reports end without a newline, so the old recipe
    (`cat … | sed '/<?xml/d'`) deleted a class's closing tag with the next file's declaration, and every
    run with two or more test classes read as no report. The documented commands, run against a fake build
    tool that writes two such files, must produce one report with both suites.
    """

    @staticmethod
    def _suite(name: str, cases: int) -> str:
        body = "".join(f'<testcase classname="{name}" name="t{i}"/>' for i in range(cases))
        # As Surefire writes them: a declaration line, then the suite, and no trailing newline.
        return f'<?xml version="1.0" encoding="UTF-8"?>\n<testsuite name="{name}" tests="{cases}">{body}\n</testsuite>'

    @pytest.mark.parametrize(("tool", "reports_dir"), [
        ("mvn", "target/surefire-reports"), ("./gradlew", "build/test-results/test")])
    def test_two_classes_join_into_one_valid_report(self, tmp_path, tool, reports_dir):
        import os
        import stat
        import subprocess

        from app.tdd.junit_report import parse_junit_report

        doc = _prompt("java")
        doc = doc[doc.index('<doc path="references/java.md">'):]
        command = next(c["test"] for c in (json.loads(b) for b in _JSON_BLOCK.findall(doc))
                       if "test" in c and tool in c["test"])
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake = tmp_path / tool.removeprefix("./") if tool.startswith("./") else bin_dir / tool
        staged = tmp_path / "staged"
        staged.mkdir()
        for name, n in (("a.ATest", 2), ("b.BTest", 3)):
            (staged / f"TEST-{name}.xml").write_text(self._suite(name, n), encoding="utf-8")
        # The build writes its reports after the recipe's `rm -rf` of the reports directory.
        fake.write_text(f"#!/bin/sh\nmkdir -p {reports_dir}\ncp {staged}/*.xml {reports_dir}/\n", encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        report = tmp_path / "junit.xml"
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", JUNIT_REPORT_ENV: str(report)}
        done = subprocess.run(["/bin/sh", "-c", command], cwd=tmp_path, env=env, capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        assert not (tmp_path / reports_dir / "TEST-a.ATest.xml").read_text().endswith("\n")
        parsed = parse_junit_report(report.read_text(encoding="utf-8"))
        assert parsed is not None and parsed.tests == 5


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


def test_one_skill_call_in_an_empty_project_carries_every_ecosystem(tmp_path):
    """The live run's case: an empty project and no argument. One launch holds every reference."""
    from tests.test_skills_tool import _call, _ctx, _tool

    init_bundled_skills()
    reg = get_skill_registry(tmp_path, tmp_path / "home", tmp_path / "etc", bundled_dir=tmp_path / "none")
    command = reg.find_command("tdd-project-config")
    assert command is not None
    result = _call(_tool(command), {"skill": "tdd-project-config"}, _ctx())
    (message,) = result.new_messages
    assert _DOC.findall(str(message.content)) == sorted([*LANGUAGE_FILES, "references/shared.md"])


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


# ── the two modes: the sandbox's references and the default's ────────────────────────

#: The references that carry text about the sandbox, and so have a no-sandbox equivalent.
VARIANTS = ["javascript-typescript", "python", "shared"]
REFERENCES = SKILL_DIR / "references"


def _project(tmp_path: Path, settings: dict[str, object] | None, name: str = "settings.json") -> str:
    project = tmp_path / "project"
    (project / ".tddagents").mkdir(parents=True)
    (project / "pyproject.toml").write_text("")
    if settings is not None:
        (project / ".tddagents" / name).write_text(json.dumps(settings))
    return str(project)


class TestReferencesByMode:
    def test_the_variants_are_exactly_the_references_with_sandbox_text(self) -> None:
        assert sorted(p.stem for p in (REFERENCES / NOSANDBOX_DIR).glob("*.md")) == VARIANTS
        with_text = sorted(p.stem for p in REFERENCES.glob("*.md") if "sandbox" in p.read_text().lower())
        assert with_text == VARIANTS

    def test_the_sandbox_references_are_the_base_files_untouched(self) -> None:
        files = skill_files(sandboxed=True)
        assert files == skill_files()
        assert sorted(files) == sorted([*LANGUAGE_FILES, "references/shared.md"])
        for path, text in files.items():
            assert text == (SKILL_DIR / path).read_text(encoding="utf-8")
        assert "runs in a sandbox" in files["references/shared.md"]

    def test_without_the_sandbox_the_variants_replace_the_same_named_references(self) -> None:
        files = skill_files(sandboxed=False)
        assert sorted(files) == sorted(skill_files(sandboxed=True))
        for name in VARIANTS:
            assert files[f"references/{name}.md"] == (REFERENCES / NOSANDBOX_DIR / f"{name}.md").read_text()
            assert files[f"references/{name}.md"] != skill_files()[f"references/{name}.md"]

    def test_the_other_references_are_the_same_in_both_modes(self) -> None:
        on, off = skill_files(sandboxed=True), skill_files(sandboxed=False)
        for name in LANGUAGES:
            if name not in VARIANTS:
                assert on[f"references/{name}.md"] == off[f"references/{name}.md"]

    def test_no_sandbox_variant_is_listed_as_a_reference_of_its_own(self) -> None:
        assert not any(NOSANDBOX_DIR in path for path in skill_files(sandboxed=False))

    def test_the_default_text_does_not_tell_the_model_it_is_sandboxed(self) -> None:
        shared = skill_files(sandboxed=False)["references/shared.md"]
        assert "runs in a sandbox" not in shared
        assert "private home directory" not in shared
        assert "## The environment" in shared
        assert "## The sandbox" not in shared
        for name in VARIANTS:
            text = skill_files(sandboxed=False)[f"references/{name}.md"]
            assert "the sandbox makes" not in text
            assert "In the sandbox only" not in text

    def test_the_default_text_lets_the_model_install_the_runtime(self) -> None:
        shared = skill_files(sandboxed=False)["references/shared.md"]
        for word in ("install it", "pyenv", "nvm", "sdkman", "rustup", "no administrator password"):
            assert word in shared

    def test_the_default_text_says_an_installation_asks_the_user_first(self) -> None:
        shared = skill_files(sandboxed=False)["references/shared.md"]
        assert "asks the user for permission first" in shared
        assert "If the user declines, do not look for another way around it" in shared
        assert "asks nothing" in shared
        assert "only when the permission flow lets the command through" not in shared
        assert "asks the user for permission" not in skill_files(sandboxed=True)["references/shared.md"]

    def test_the_default_text_keeps_what_is_not_about_the_sandbox(self) -> None:
        on, off = skill_files(sandboxed=True), skill_files(sandboxed=False)
        assert "## The final run" in off["references/shared.md"]
        assert "declare every dependency" in off["references/shared.md"]
        assert "`exclude`" in off["references/shared.md"]
        # Every section but the sandbox's is the same text in both modes.

        def sections(text: str) -> dict[str, str]:
            return {part.split("\n", 1)[0]: part for part in text.split("\n## ")}
        base, nosandbox = sections(on["references/shared.md"]), sections(off["references/shared.md"])
        assert {k: v for k, v in base.items() if "sandbox" not in k} == \
            {k: v for k, v in nosandbox.items() if k != "The environment"}

    def test_the_sandbox_text_keeps_its_rules(self) -> None:
        shared = skill_files(sandboxed=True)["references/shared.md"]
        assert "## The sandbox" in shared
        assert "private home directory" in shared
        assert "`exclude`" in shared
        assert "In the sandbox only the system's Python is visible" in skill_files()["references/python.md"]
        assert "which the sandbox makes new for every command" in \
            skill_files()["references/javascript-typescript.md"]

    def test_the_variants_are_valid_for_the_coverage_and_json_rules(self) -> None:
        for name in VARIANTS:
            text = (REFERENCES / NOSANDBOX_DIR / f"{name}.md").read_text()
            assert "{{COVERAGE_RULE_OF_THUMB}}" in text or name != "shared"
            for block in _JSON_BLOCK.findall(process_content(text, {**PROJECT_CONFIG_PROMPT_VARS, **TDD_PROMPT_VARS})):
                json.loads(block)


class TestBuildPromptByMode:
    def test_the_doc_paths_are_the_same_in_both_modes(self) -> None:
        for sandboxed in (True, False):
            prompt = build_prompt("python", "", "# P\n", skill_files(sandboxed=sandboxed))
            assert _DOC.findall(prompt) == ["references/python.md", "references/shared.md"]

    def test_a_language_with_no_variant_inlines_its_own_reference_in_both_modes(self) -> None:
        for sandboxed in (True, False):
            prompt = build_prompt("go", "", "# P\n", skill_files(sandboxed=sandboxed))
            assert _DOC.findall(prompt) == ["references/go.md", "references/shared.md"]
            assert "go test" in prompt

    def test_the_prompt_says_the_sandbox_only_in_the_sandbox_mode(self) -> None:
        on = build_prompt("python", "", "# P\n", skill_files(sandboxed=True))
        off = build_prompt("python", "", "# P\n", skill_files(sandboxed=False))
        assert "runs in a sandbox" in on
        assert "In the sandbox only" in on
        assert "runs in a sandbox" not in off
        assert "In the sandbox only" not in off
        assert "pyenv" in off

    def test_with_no_language_every_reference_of_the_mode_is_inlined(self) -> None:
        on = build_prompt(None, "", "# P\n", skill_files(sandboxed=True))
        off = build_prompt(None, "", "# P\n", skill_files(sandboxed=False))
        assert _DOC.findall(on) == _DOC.findall(off) == sorted([*LANGUAGE_FILES, "references/shared.md"])
        assert "## The sandbox" in on and "## The sandbox" not in off
        assert "## The environment" in off
        # Each mode's own text for the references the no-sandbox variants replace.
        for name in ("python", "javascript-typescript"):
            assert skill_files(sandboxed=False)[f"references/{name}.md"].split("-->")[-1].strip()[:80] in off


class TestTheModeComesFromTheSettings:
    def test_the_default_is_without_the_sandbox(self, tmp_path: Path) -> None:
        cwd = _project(tmp_path, None)
        assert sandbox_enabled(cwd) is False
        prompt = _prompt(cwd=cwd)
        assert "runs in a sandbox" not in prompt
        assert "## The environment" in prompt

    def test_a_project_with_the_sandbox_on_gets_the_sandbox_text(self, tmp_path: Path) -> None:
        cwd = _project(tmp_path, {"sandbox": {"enabled": True}})
        assert sandbox_enabled(cwd) is True
        prompt = _prompt(cwd=cwd)
        assert "runs in a sandbox" in prompt
        assert "In the sandbox only the system's Python is visible" in prompt

    def test_a_project_with_the_sandbox_off_gets_the_default_text(self, tmp_path: Path) -> None:
        cwd = _project(tmp_path, {"sandbox": {"enabled": False}})
        assert sandbox_enabled(cwd) is False
        assert "runs in a sandbox" not in _prompt(cwd=cwd)

    @pytest.mark.parametrize("value", ["yes", 1, None, {}])
    def test_only_true_turns_the_sandbox_on(self, tmp_path: Path, value: object) -> None:
        assert sandbox_enabled(_project(tmp_path, {"sandbox": {"enabled": value}})) is False

    def test_the_local_settings_count(self, tmp_path: Path) -> None:
        assert sandbox_enabled(_project(tmp_path, {"sandbox": {"enabled": True}}, "settings.local.json")) is True

    def test_the_sandbox_flag_turns_it_on(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.bootstrap.state import set_flag_settings_path
        from app.main import load_settings_from_flag
        from app.workspace.project import load_sandbox_profile

        monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
        cwd = _project(tmp_path, None)
        set_flag_settings_path(load_settings_from_flag(None, load_sandbox_profile()))
        assert sandbox_enabled(cwd) is True
        assert "runs in a sandbox" in _prompt(cwd=cwd)

    def test_the_no_sandbox_flag_turns_it_off_over_a_sandboxed_project(
            self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.bootstrap.state import set_flag_settings_path
        from app.main import load_settings_from_flag

        monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
        cwd = _project(tmp_path, {"sandbox": {"enabled": True}})
        set_flag_settings_path(load_settings_from_flag(None, {"sandbox": {"enabled": False}}))
        assert sandbox_enabled(cwd) is False
        assert "runs in a sandbox" not in _prompt(cwd=cwd)

    def test_the_settings_file_flag_counts(self, tmp_path: Path) -> None:
        from app.bootstrap.state import set_flag_settings_path

        flag = tmp_path / "flag.json"
        flag.write_text(json.dumps({"sandbox": {"enabled": True}}))
        cwd = _project(tmp_path, None)
        set_flag_settings_path(str(flag))
        assert sandbox_enabled(cwd) is True

    def test_unreadable_settings_mean_no_sandbox(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        import app.utils.settings.settings as settings_module

        def broken(_locations: object) -> dict[str, object]:
            raise OSError("unreadable")

        monkeypatch.setattr(settings_module, "load_settings", broken)
        assert sandbox_enabled(_project(tmp_path, {"sandbox": {"enabled": True}})) is False

    def test_a_missing_project_means_no_sandbox(self) -> None:
        assert sandbox_enabled("/nonexistent/project") is False
