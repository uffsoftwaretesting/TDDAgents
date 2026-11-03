"""
The `tdd-project-config` skill: how a project declares its setup and test commands to TDDAgents.

Ported from `reference/claude-code/src/skills/bundled/claudeApi.ts` -> `LANGUAGE_INDICATORS`,
`detectLanguage`, `getFilesForLanguage`, `processContent`, `buildInlineReference`,
`INLINE_READING_GUIDE`, `buildPrompt`, `registerClaudeApiSkill`; the content layout of
`claudeApiContent.ts` (`SKILL_PROMPT`, `SKILL_FILES`, `SKILL_MODEL_VARS`).

The skill teaches the `## Project Configuration` block of a plan (saved as
`.tddagents/project.json`): the commands that build the project's environment and run its whole
suite with a JUnit report, and the globs of its test files, manifests and environment
directories. Its prompt is built in code at each invocation, as upstream's `claude-api`:

- **The language.** The argument names it (`tdd-project-config go`; aliases such as `ts` or
  `dotnet` are accepted). Otherwise `detect_language` reads the project directory's top-level
  entries, as upstream's `readdir(getCwd())`, and the first ecosystem in
  `LANGUAGE_INDICATORS` order with a matching file name or extension wins.
- **The documentation.** `references/<language>.md` and `references/shared.md` are inlined in
  `<doc path="…">` tags after the reading guide. HTML comments are stripped and `{{VAR}}`
  placeholders rendered from `PROJECT_CONFIG_PROMPT_VARS` (upstream's `SKILL_MODEL_VARS`) and
  `TDD_PROMPT_VARS` (the coverage rule of thumb); an
  unknown placeholder stays visible.
- **No language.** The prompt lists the available ecosystems and inlines only `shared.md`.

The Markdown lives in `app/prompts/skills/tdd-project-config/`: `prompt.md` (upstream's
`SKILL.md`; not named `SKILL.md`, so the file-based roster does not load it a second time) and
`references/`. Its sections `## Reading Guide`, `## No Language Detected` and `## Common
Pitfalls` are cut and assembled as `buildPrompt` cuts upstream's.

Divergences from `claudeApi.ts`: the argument may name the language (upstream only appends it as
the user request, which this skill also does); with no language detected the prompt lists the
ecosystems and inlines only the shared reference, where upstream inlines every document (eight
ecosystems' examples would only crowd the plan's context); `allowedTools` is empty, because the
documentation is inlined and an `allowedTools` list makes the Skill tool ask (`skillHasOnlySafeProperties`),
which a headless Analyst would be denied.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from app.skills.bundled_skills import BundledSkillContext, BundledSkillDefinition
from app.tdd.coverage_gate import TDD_PROMPT_VARS
from app.tdd.project_config_vars import (
    PROJECT_CONFIG_FILE,
    PROJECT_CONFIG_HEADING,
    PROJECT_CONFIG_PROMPT_VARS,
)

SKILL_NAME = "tdd-project-config"
SKILL_DIR = Path(__file__).resolve().parents[1] / "prompts" / "skills" / SKILL_NAME
PROMPT_FILE = "prompt.md"
REFERENCES_DIR = "references"
SHARED_REFERENCE = f"{REFERENCES_DIR}/shared.md"

READING_GUIDE_HEADING = "## Reading Guide"
NO_LANGUAGE_HEADING = "## No Language Detected"
PITFALLS_HEADING = "## Common Pitfalls"

#: Each ecosystem's reference file stem, and the name the prompt shows.
LANGUAGES: dict[str, str] = {
    "python": "Python",
    "javascript-typescript": "JavaScript / TypeScript",
    "go": "Go",
    "rust": "Rust",
    "java": "Java (Maven, Gradle)",
    "csharp": "C# (.NET)",
    "ruby": "Ruby",
    "php": "PHP",
}

#: `LANGUAGE_INDICATORS`: an entry starting with `.` is an extension, anything else a file name.
#: The order is the precedence, upstream's first, with JavaScript and Rust added.
LANGUAGE_INDICATORS: dict[str, tuple[str, ...]] = {
    "python": (".py", "requirements.txt", "pyproject.toml", "setup.py", "Pipfile"),
    "javascript-typescript": (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", "tsconfig.json", "package.json"),
    "java": (".java", "pom.xml", "build.gradle", "build.gradle.kts"),
    "go": (".go", "go.mod"),
    "rust": (".rs", "Cargo.toml"),
    "ruby": (".rb", "Gemfile"),
    "csharp": (".cs", ".csproj", ".sln"),
    "php": (".php", "composer.json"),
}

#: The names an argument may give an ecosystem by (lower case).
LANGUAGE_ALIASES: dict[str, str] = {
    **{language: language for language in LANGUAGES},
    "py": "python",
    "javascript": "javascript-typescript",
    "typescript": "javascript-typescript",
    "js": "javascript-typescript",
    "ts": "javascript-typescript",
    "node": "javascript-typescript",
    "nodejs": "javascript-typescript",
    "golang": "go",
    "rs": "rust",
    "c#": "csharp",
    "cs": "csharp",
    "dotnet": "csharp",
    ".net": "csharp",
    "rb": "ruby",
}

_COMMENT = re.compile(r"<!--[\s\S]*?-->\n?")
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def detect_language(cwd: str) -> str | None:
    """`detectLanguage`: the first ecosystem with an indicator among `cwd`'s top-level entries."""
    try:
        entries = os.listdir(cwd)
    except OSError:
        return None
    for language, indicators in LANGUAGE_INDICATORS.items():
        for indicator in indicators:
            if indicator.startswith("."):
                if any(entry.endswith(indicator) for entry in entries):
                    return language
            elif indicator in entries:
                return language
    return None


def language_from_args(args: str) -> str | None:
    """The ecosystem the argument's first word names, if it names one."""
    words = args.split()
    return LANGUAGE_ALIASES.get(words[0].lower()) if words else None


def skill_files() -> dict[str, str]:
    """`SKILL_FILES`: every reference, by its path relative to the skill directory."""
    return {
        f"{REFERENCES_DIR}/{path.name}": path.read_text(encoding="utf-8")
        for path in sorted((SKILL_DIR / REFERENCES_DIR).glob("*.md"))
    }


def get_files_for_language(language: str, files: dict[str, str]) -> list[str]:
    """`getFilesForLanguage`: the language's reference and the shared one."""
    return [path for path in files if path in (f"{REFERENCES_DIR}/{language}.md", SHARED_REFERENCE)]


def process_content(markdown: str, variables: dict[str, str]) -> str:
    """`processContent`: HTML comments stripped (nested ones too), then `{{VAR}}` rendered."""
    out = markdown
    while True:
        stripped = _COMMENT.sub("", out)
        if stripped == out:
            break
        out = stripped
    return _PLACEHOLDER.sub(lambda match: variables.get(match.group(1), match.group(0)), out)


def build_inline_reference(file_paths: list[str], files: dict[str, str], variables: dict[str, str]) -> str:
    """`buildInlineReference`: each file in a `<doc path="…">` tag, in path order."""
    sections = [
        f'<doc path="{path}">\n{process_content(files[path], variables).strip()}\n</doc>'
        for path in sorted(file_paths)
        if files.get(path)
    ]
    return "\n\n".join(sections)


def ecosystem_listing() -> str:
    """The available ecosystems, one bullet each, with the argument that selects it."""
    return "\n".join(f"- `{language}`: {name}" for language, name in LANGUAGES.items())


def _between(text: str, start: str, end: str | None) -> str:
    begin = text.find(start)
    if begin == -1:
        return ""
    finish = text.find(end, begin) if end else -1
    return (text[begin:finish] if finish != -1 else text[begin:]).strip()


def build_prompt(language: str | None, args: str, prompt: str, files: dict[str, str]) -> str:
    """`buildPrompt`: the base prompt, the reading guide, the documentation, the pitfalls."""
    variables = {**PROJECT_CONFIG_PROMPT_VARS, **TDD_PROMPT_VARS, "LANGUAGE": language or "",
                 "ECOSYSTEMS": ecosystem_listing()}
    clean = process_content(prompt, variables)
    guide_at = clean.find(READING_GUIDE_HEADING)
    parts = [(clean[:guide_at] if guide_at != -1 else clean).rstrip()]
    if language:
        parts.append(_between(clean, READING_GUIDE_HEADING, NO_LANGUAGE_HEADING))
        paths = get_files_for_language(language, files)
    else:
        parts.append(_between(clean, NO_LANGUAGE_HEADING, PITFALLS_HEADING))
        paths = [SHARED_REFERENCE]
    parts.append("---\n\n## Included Documentation\n\n" + build_inline_reference(paths, files, variables))
    pitfalls = _between(clean, PITFALLS_HEADING, None)
    if pitfalls:
        parts.append(pitfalls)
    if args:
        parts.append(f"## User Request\n\n{args}")
    return "\n\n".join(part for part in parts if part)


def get_prompt_for_command(args: str, context: BundledSkillContext) -> str:
    """The skill's `getPromptForCommand`: the argument's language, else the detected one."""
    language = language_from_args(args) or detect_language(context.cwd)
    prompt = (SKILL_DIR / PROMPT_FILE).read_text(encoding="utf-8")
    return build_prompt(language, args.strip(), prompt, skill_files())


def tdd_project_config_skill() -> BundledSkillDefinition:
    """`registerClaudeApiSkill`'s definition, for `tdd-project-config`."""
    return BundledSkillDefinition(
        name=SKILL_NAME,
        description=(
            "Declare how a project installs its dependencies and runs its tests for TDDAgents: the "
            f"`## {PROJECT_CONFIG_HEADING}` block of a plan, saved as `{PROJECT_CONFIG_FILE}`.\n"
            "TRIGGER when: planning a project or a change to how it installs dependencies, builds or runs its "
            f"tests; writing or fixing the `## {PROJECT_CONFIG_HEADING}` block; ExitPlanMode refused the "
            "plan's project configuration.\n"
            "DO NOT TRIGGER when: writing tests or production code, or running the tests."
        ),
        argument_hint="[language]",
        user_invocable=True,
        get_prompt_for_command=get_prompt_for_command,
    )


__all__ = [
    "LANGUAGES",
    "LANGUAGE_ALIASES",
    "LANGUAGE_INDICATORS",
    "SKILL_DIR",
    "SKILL_NAME",
    "build_inline_reference",
    "build_prompt",
    "detect_language",
    "ecosystem_listing",
    "get_files_for_language",
    "get_prompt_for_command",
    "language_from_args",
    "process_content",
    "skill_files",
    "tdd_project_config_skill",
]
