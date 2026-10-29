"""
A skill command (Phase 8).

Ported from `reference/claude-code/src/skills/loadSkillsDir.ts` -> `createSkillCommand` (the
`PromptCommand` a `SKILL.md` becomes) and `skills/bundledSkills.ts` -> `registerBundledSkill`.
TDDAgents' bundled skills live as files under `app/prompts/skills/` rather than in code. As
upstream's `registerBundledSkill` with `files`, a bundled command's `skill_root` is its per-process
extraction directory (`app/skills/bundled_skills.py`), its reference files are written there on
first invocation, and `prependBaseDir` names it, so the model reads `references/` without a prompt.

`getPromptForCommand` is `prompt_for_command`: the base-directory line, argument substitution,
then the skill-dir and session placeholders, here `${TDDAGENTS_SKILL_DIR}` and
`${TDDAGENTS_SESSION_ID}` (upstream's `CLAUDE_*`, renamed as every env name in this build).
Not ported: the inline shell execution of `` !`cmd` `` blocks (`executeShellCommandsInPrompt`).

Skill discovery (Phase 8).

Ported from `reference/claude-code/src/skills/loadSkillsDir.ts` -> `parseSkillFrontmatterFields`,
`parseSkillPaths`, `loadSkillsFromSkillsDir`, `getSkillDirCommands`,
`activateConditionalSkillsForPaths`; `utils/markdownConfigLoader.ts` ->
`getProjectDirsUpToHome`, `parseSlashCommandToolsFromFrontmatter`,
`extractDescriptionFromMarkdown`; `utils/frontmatterParser.ts` -> `coerceDescriptionToString`,
`parseBooleanFrontmatter`; `commands.ts` -> `loadAllCommands`, `getCommands`, `findCommand`,
`getSkillToolCommands`.

Sources, in upstream's `loadAllCommands` order — and `findCommand` takes the first match, so this
order is the precedence:

1. **bundled**: TDDAgents' roster in `app/prompts/skills/` (upstream's `getBundledSkills`).
   Never conditional, description always user-specified, as `registerBundledSkill` makes them.
2. **policy**: `<managed dir>/.tddagents/skills` (`TDDAGENTS_DISABLE_POLICY_SKILLS` turns it off).
3. **user**: `<config home>/skills` (`~/.tddagents/skills`).
4. **project**: every `.tddagents/skills` from the project directory up to its git root (or the
   home directory, which is loaded as user, not project).

The same file reached twice (a symlinked directory) is loaded once (`getFileIdentity`). A skill
whose frontmatter has `paths` is *conditional*: withheld from the commands until a file tool
touches a matching path (`activate_conditional_skills_for_paths`, called by `ReadFile`,
`WriteFile` and `Edit` upstream).

Upstream keeps this state in module globals memoized by cwd; here one `SkillRegistry` per run
holds it, including the per-agent `sent_skill_names` of the skill-listing attachment.

Not ported: legacy `/commands/` directories, plugin and MCP skills, `--add-dir` skill dirs,
dynamic skill-dir discovery (`discoverSkillDirsForPaths`), bare mode, effort and `shell`.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pathspec
import yaml

from app.skills.bundled_skills import get_bundled_skill_extract_dir
from app.utils.argument_substitution import parse_argument_names, substitute_arguments
from app.utils.model.model import parse_user_specified_model
from app.utils.settings.settings import MANAGED_DIR, parse_tool_list_from_cli
from app.utils.tool_result_storage import config_home

SKILL_DIR_PLACEHOLDER = "${TDDAGENTS_SKILL_DIR}"
SESSION_ID_PLACEHOLDER = "${TDDAGENTS_SESSION_ID}"


@dataclass(frozen=True)
class SkillCommand:
    """`PromptCommand` for a skill (bundled or from a skills directory)."""

    name: str
    description: str
    markdown_content: str
    #: `bundled`, `policySettings`, `userSettings` or `projectSettings`.
    source: str
    #: `bundled` or `skills`.
    loaded_from: str
    skill_root: str | None
    display_name: str | None = None
    has_user_specified_description: bool = False
    allowed_tools: tuple[str, ...] = ()
    argument_hint: str | None = None
    argument_names: tuple[str, ...] = ()
    when_to_use: str | None = None
    version: str | None = None
    model: str | None = None
    disable_model_invocation: bool = False
    user_invocable: bool = True
    #: `fork` runs the skill as a worker; `None` is inline.
    context: str | None = None
    agent: str | None = None
    paths: tuple[str, ...] | None = None
    hooks: dict[str, Any] | None = None
    progress_message: str = "running"
    #: A bundled skill's own directory under `app/prompts/skills/`, whose reference files are
    #: extracted to `skill_root` (the per-process `get_bundled_skill_extract_dir`) on first use.
    bundled_source_dir: str | None = None

    @property
    def is_hidden(self) -> bool:
        return not self.user_invocable

    @property
    def content_length(self) -> int:
        return len(self.markdown_content)

    def user_facing_name(self) -> str:
        return self.display_name or self.name

    def prompt_for_command(self, args: str, session_id: str = "") -> str:
        """
        `getPromptForCommand`: the skill's text as the model receives it. A bundled skill's files are
        extracted on its first invocation (`registerBundledSkill`); if that fails it goes on without
        its base directory.
        """
        root = self.skill_root
        if self.bundled_source_dir is not None:
            from app.skills.bundled_skills import extract_bundled_skill_files

            root = extract_bundled_skill_files(self.name, self.bundled_source_dir)
        content = (
            f"Base directory for this skill: {root}\n\n{self.markdown_content}"
            if root
            else self.markdown_content
        )
        content = substitute_arguments(content, args, True, self.argument_names)
        if root:
            content = content.replace(SKILL_DIR_PLACEHOLDER, root)
        return content.replace(SESSION_ID_PLACEHOLDER, session_id)


logger = logging.getLogger(__name__)

#: TDDAgents' bundled skills (upstream's `getBundledSkills`).
BUNDLED_SKILLS_DIR = Path(__file__).resolve().parents[1] / "prompts" / "skills"

SKILL_FILE = "SKILL.md"
PROJECT_SKILLS_SUBDIR = Path(".tddagents") / "skills"
DISABLE_POLICY_SKILLS_ENV = "TDDAGENTS_DISABLE_POLICY_SKILLS"

_TRUTHY = ("1", "true", "yes", "on")
_HEADING = re.compile(r"^#+\s+(.+)$")


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """`parseFrontmatter`: a leading `---` YAML block; invalid YAML is logged and ignored."""
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    body = parts[2].lstrip("\n")
    try:
        data = yaml.safe_load(parts[1])
    except yaml.YAMLError as exc:
        logger.warning("Invalid skill frontmatter: %s", exc)
        return {}, body
    return (data if isinstance(data, dict) else {}), body


def coerce_description_to_string(value: Any, component_name: str) -> str | None:
    """`coerceDescriptionToString`."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (int, float, bool)):
        return str(value)
    logger.warning("Description invalid for %s - omitting", component_name)
    return None


def extract_description_from_markdown(content: str, default_description: str = "Custom item") -> str:
    """`extractDescriptionFromMarkdown`: the first non-blank line, a heading's text if a heading."""
    for line in content.split("\n"):
        trimmed = line.strip()
        if trimmed:
            heading = _HEADING.match(trimmed)
            text = heading.group(1) if heading else trimmed
            return text[:97] + "..." if len(text) > 100 else text
    return default_description


def parse_boolean_frontmatter(value: Any) -> bool:
    """`parseBooleanFrontmatter`."""
    return value is True or value == "true"


def parse_slash_command_tools_from_frontmatter(value: Any) -> tuple[str, ...]:
    """`parseSlashCommandToolsFromFrontmatter` over `parseToolListString`."""
    if not value:
        return ()
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = [item for item in value if isinstance(item, str)]
    else:
        return ()
    parsed = parse_tool_list_from_cli(items)
    return ("*",) if "*" in parsed else tuple(parsed)


def parse_skill_paths(frontmatter: Mapping[str, Any]) -> tuple[str, ...] | None:
    """`parseSkillPaths`: a trailing `/**` is dropped; all-`**` means no paths."""
    if not frontmatter.get("paths"):
        return None
    # Imported here: `app.tool` reaches the agents loader, which imports this module.
    from app.utils.claudemd import split_path_in_frontmatter

    patterns = [p.removesuffix("/**") for p in split_path_in_frontmatter(frontmatter["paths"])]
    patterns = [p for p in patterns if p]
    if not patterns or all(p == "**" for p in patterns):
        return None
    return tuple(patterns)


def parse_skill_frontmatter_fields(
    frontmatter: Mapping[str, Any], markdown_content: str, resolved_name: str, fallback_label: str = "Skill"
) -> dict[str, Any]:
    """`parseSkillFrontmatterFields`: every field shared by file skills (snake_case keys)."""
    validated = coerce_description_to_string(frontmatter.get("description"), resolved_name)
    model = frontmatter.get("model")
    return {
        "display_name": str(frontmatter["name"]) if frontmatter.get("name") is not None else None,
        "description": validated or extract_description_from_markdown(markdown_content, fallback_label),
        "has_user_specified_description": validated is not None,
        "allowed_tools": parse_slash_command_tools_from_frontmatter(frontmatter.get("allowed-tools")),
        "argument_hint": str(frontmatter["argument-hint"]) if frontmatter.get("argument-hint") is not None
        else None,
        "argument_names": tuple(parse_argument_names(frontmatter.get("arguments"))),
        "when_to_use": frontmatter.get("when_to_use"),
        "version": frontmatter.get("version"),
        "model": parse_user_specified_model(str(model)) if model and model != "inherit" else None,
        "disable_model_invocation": parse_boolean_frontmatter(frontmatter.get("disable-model-invocation")),
        "user_invocable": True if frontmatter.get("user-invocable") is None
        else parse_boolean_frontmatter(frontmatter["user-invocable"]),
        "hooks": frontmatter["hooks"] if isinstance(frontmatter.get("hooks"), dict) else None,
        "context": "fork" if frontmatter.get("context") == "fork" else None,
        "agent": frontmatter.get("agent"),
    }


def load_skills_from_skills_dir(
    base_path: Path, source: str, loaded_from: str = "skills"
) -> list[tuple[SkillCommand, Path]]:
    """`loadSkillsFromSkillsDir`: `<name>/SKILL.md` directories only; the name is the directory's."""
    try:
        entries = sorted(base_path.iterdir())
    except OSError:
        return []
    results: list[tuple[SkillCommand, Path]] = []
    for entry in entries:
        if not entry.is_dir():
            continue
        skill_file = entry / SKILL_FILE
        try:
            content = skill_file.read_text(encoding="utf-8")
        except OSError:
            continue
        frontmatter, markdown = parse_frontmatter(content)
        fields = parse_skill_frontmatter_fields(frontmatter, markdown, entry.name)
        bundled = loaded_from == "bundled"
        if bundled:
            fields["has_user_specified_description"] = True
        results.append((
            SkillCommand(
                name=entry.name, markdown_content=markdown, source=source, loaded_from=loaded_from,
                skill_root=get_bundled_skill_extract_dir(entry.name) if bundled else str(entry),
                bundled_source_dir=str(entry) if bundled else None,
                paths=None if bundled else parse_skill_paths(frontmatter), **fields,
            ),
            skill_file,
        ))
    return results


def get_project_dirs_up_to_home(cwd: Path, home: Path | None = None) -> list[Path]:
    """`getProjectDirsUpToHome`: each existing `.tddagents/skills` from `cwd` to the git root."""
    stop_at = (home if home is not None else Path.home()).resolve()
    current = cwd.resolve()
    dirs: list[Path] = []
    while current != stop_at:
        candidate = current / PROJECT_SKILLS_SUBDIR
        if candidate.is_dir():
            dirs.append(candidate)
        if (current / ".git").exists() or current.parent == current:
            break
        current = current.parent
    return dirs


def _file_identity(path: Path) -> str | None:
    """`getFileIdentity`: the canonical path, or `None` when it cannot be resolved."""
    try:
        return str(path.resolve(strict=True))
    except OSError:
        return None


class SkillRegistry:
    """One run's skills: the base commands, the conditional ones, and the listing's memory."""

    def __init__(self, commands: Iterable[SkillCommand], conditional: Iterable[SkillCommand] = (),
                 *, model: str | None = None) -> None:
        self._base = list(commands)
        self._conditional = {c.name: c for c in conditional}
        self._dynamic: dict[str, SkillCommand] = {}
        self.model = model
        #: `attachments.ts` -> `sentSkillNames`, keyed by agent id (`""` is the main thread).
        self.sent_skill_names: dict[str | None, set[str]] = {}

    def commands(self) -> list[SkillCommand]:
        """`getCommands`: the base commands, then activated skills no base command shadows."""
        names = {c.name for c in self._base}
        return [*self._base, *(c for c in self._dynamic.values() if c.name not in names)]

    def find_command(self, name: str) -> SkillCommand | None:
        """`findCommand`: the first command whose name or user-facing name matches."""
        return next((c for c in self.commands() if name in (c.name, c.user_facing_name())), None)

    def get_skill_tool_commands(self) -> list[SkillCommand]:
        """`getSkillToolCommands`: what the model may invoke (bundled and skills-dir only here)."""
        return [c for c in self.commands() if not c.disable_model_invocation]

    def activate_conditional_skills_for_paths(self, file_paths: Iterable[str], cwd: str) -> list[str]:
        """`activateConditionalSkillsForPaths`: gitignore-style matching, relative to `cwd`."""
        activated: list[str] = []
        for name, skill in list(self._conditional.items()):
            spec = pathspec.GitIgnoreSpec.from_lines(skill.paths or ())
            for file_path in file_paths:
                relative = os.path.relpath(file_path, cwd) if os.path.isabs(file_path) else file_path
                if not relative or relative.startswith("..") or os.path.isabs(relative):
                    continue
                if spec.match_file(relative):
                    self._dynamic[name] = skill
                    del self._conditional[name]
                    activated.append(name)
                    logger.debug("[skills] Activated conditional skill '%s' (matched path: %s)", name, relative)
                    break
        return activated


def get_skill_registry(
    project_dir: Path | str,
    user_home: Path | str | None = None,
    managed_dir: Path | str | None = None,
    *,
    bundled_dir: Path | str = BUNDLED_SKILLS_DIR,
    model: str | None = None,
) -> SkillRegistry:
    """`getSkills` + `getSkillDirCommands` for one run, as a registry."""
    home = Path(user_home) if user_home is not None else None
    user_dir = home / ".tddagents" / "skills" if home is not None else config_home() / "skills"
    policy_dir = Path(managed_dir if managed_dir is not None else MANAGED_DIR) / ".tddagents" / "skills"
    policy_off = os.environ.get(DISABLE_POLICY_SKILLS_ENV, "").strip().lower() in _TRUTHY
    loaded = [
        *load_skills_from_skills_dir(Path(bundled_dir), "bundled", loaded_from="bundled"),
        *([] if policy_off else load_skills_from_skills_dir(policy_dir, "policySettings")),
        *load_skills_from_skills_dir(user_dir, "userSettings"),
        *(s for d in get_project_dirs_up_to_home(Path(project_dir), home) for s in
          load_skills_from_skills_dir(d, "projectSettings")),
    ]
    seen: dict[str, str] = {}
    base: list[SkillCommand] = []
    conditional: list[SkillCommand] = []
    for skill, path in loaded:
        identity = _file_identity(path)
        if identity is not None and identity in seen:
            logger.debug("Skipping duplicate skill '%s' from %s (same file already loaded from %s)",
                         skill.name, skill.source, seen[identity])
            continue
        if identity is not None:
            seen[identity] = skill.source
        (conditional if skill.paths else base).append(skill)
    return SkillRegistry(base, conditional, model=model)


__all__ = [
    "BUNDLED_SKILLS_DIR",
    "SESSION_ID_PLACEHOLDER",
    "SKILL_DIR_PLACEHOLDER",
    "SkillCommand",
    "SkillRegistry",
    "coerce_description_to_string",
    "extract_description_from_markdown",
    "get_project_dirs_up_to_home",
    "get_skill_registry",
    "load_skills_from_skills_dir",
    "parse_boolean_frontmatter",
    "parse_frontmatter",
    "parse_skill_frontmatter_fields",
    "parse_skill_paths",
    "parse_slash_command_tools_from_frontmatter",
]
