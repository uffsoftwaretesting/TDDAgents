"""
The enabled plugins' slash commands (`commands/`) and skills (`skills/`), as skill commands.

Ported from `reference/claude-code/src/utils/plugins/loadPluginCommands.ts` -> `isSkillFile`,
`getCommandNameFromFile`, `collectMarkdownFiles`, `transformPluginSkillFiles`,
`loadCommandsFromDirectory`, `createPluginCommand`, `getPluginCommands`, `clearPluginCommandCache`,
`loadSkillsFromDirectory`, `getPluginSkills`, `clearPluginSkillsCache`; and
`reference/claude-code/src/utils/fsOperations.ts` -> `isDuplicatePath`.

A plugin command is a `PluginSkillCommand` (the run's `SkillRegistry` holds them after its skill
directories, `loadAllCommands`' order): its name is `<plugin>:<namespace…>:<name>`, its source
`plugin`, and its prompt (`getPromptForCommand`) is, in upstream's order, the base-directory
line (skills/ entries only), the argument substitution, `${CLAUDE_PLUGIN_ROOT}` /
`${CLAUDE_PLUGIN_DATA}`, the plugin's non-sensitive `${user_config.X}` values, the skill
directory and the session id. The skill-directory and session placeholders are this build's
`${TDDAGENTS_SKILL_DIR}` / `${TDDAGENTS_SESSION_ID}` (as every skill's, `load_skills_dir.py`); the
plugin variables keep upstream's names, as the plugin format does (`plugin_options_storage.py`).

Both loaders read `load_all_plugins_cache_only(locations)` (the run's settings) and are
memoized per `locations` until `clear_plugin_command_cache` / `clear_plugin_skills_cache`
(`clear_all_plugin_caches` calls both).

Not ported, as for every skill here (`load_skills_dir.py`): `effort`, `shell` and the inline
shell execution of `` !`cmd` `` blocks (`executeShellCommandsInPrompt`); bare mode (no `--bare`).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass
from typing import Any

from app.skills.load_skills_dir import (
    SESSION_ID_PLACEHOLDER,
    SKILL_DIR_PLACEHOLDER,
    SkillCommand,
    coerce_description_to_string,
    extract_description_from_markdown,
    parse_boolean_frontmatter,
    parse_frontmatter,
    parse_slash_command_tools_from_frontmatter,
)
from app.types.plugin import LoadedPlugin, get_plugin_error_message
from app.utils.argument_substitution import parse_argument_names, substitute_arguments
from app.utils.model.model import parse_user_specified_model
from app.utils.plugins.plugin_loader import load_all_plugins_cache_only
from app.utils.plugins.plugin_options_storage import (
    load_plugin_options,
    substitute_plugin_variables,
    substitute_user_config_in_content,
)
from app.utils.plugins.walk_plugin_markdown import walk_plugin_markdown
from app.utils.settings.settings import SettingsLocations

logger = logging.getLogger("TDDOrchestrator.Plugins")

_SKILL_FILE = re.compile(r"^skill\.md\Z", re.IGNORECASE)


@dataclass(frozen=True)
class _PluginRef:
    """`{ path, source }` for `substitutePluginVariables`."""

    path: str
    source: str | None


@dataclass(frozen=True)
class PluginSkillCommand(SkillCommand):
    """A plugin's `prompt` command (`createPluginCommand`), from `commands/`, `skills/` or inline content."""

    #: `pluginInfo.pluginManifest.name`: shown by `formatDescriptionWithSource`.
    plugin_name: str | None = None
    #: The plugin's directory (`${CLAUDE_PLUGIN_ROOT}`) and id (`${CLAUDE_PLUGIN_DATA}`, its options).
    plugin_path: str = ""
    plugin_source: str = ""
    #: `pluginManifest.userConfig`: the schema of `${user_config.X}`.
    user_config_schema: Mapping[str, Mapping[str, Any]] | None = None
    #: The run's settings, where the plugin's saved options are read.
    settings_locations: SettingsLocations | None = None
    #: Loaded from a `skills/` directory (`config.isSkillMode`).
    is_skill_mode: bool = False
    file_path: str = ""

    def prompt_for_command(self, args: str, session_id: str = "") -> str:
        """`getPromptForCommand` of a plugin command."""
        skill_dir = os.path.dirname(self.file_path)
        content = (
            f"Base directory for this skill: {skill_dir}\n\n{self.markdown_content}"
            if self.is_skill_mode
            else self.markdown_content
        )
        content = substitute_arguments(content, args, True, self.argument_names)
        content = substitute_plugin_variables(content, _PluginRef(self.plugin_path, self.plugin_source))
        if self.user_config_schema and self.settings_locations is not None:
            content = substitute_user_config_in_content(
                content, load_plugin_options(self.plugin_source, self.settings_locations), self.user_config_schema)
        if self.is_skill_mode:
            content = content.replace(SKILL_DIR_PLACEHOLDER, skill_dir)
        return content.replace(SESSION_ID_PLACEHOLDER, session_id)


@dataclass(frozen=True)
class _MarkdownFile:
    """`PluginMarkdownFile`."""

    file_path: str
    base_dir: str
    frontmatter: dict[str, Any]
    content: str


def is_duplicate_path(file_path: str, loaded_paths: set[str]) -> bool:
    """`isDuplicatePath`: the resolved path already loaded (and recorded otherwise)."""
    resolved = os.path.realpath(file_path)
    if resolved in loaded_paths:
        return True
    loaded_paths.add(resolved)
    return False


def is_skill_file(file_path: str) -> bool:
    """`isSkillFile`: the file is a `SKILL.md` (any case)."""
    return _SKILL_FILE.search(os.path.basename(file_path)) is not None


def _namespace(directory: str, base_dir: str) -> str:
    relative = directory[len(base_dir):].lstrip("/") if directory.startswith(base_dir) else ""
    return ":".join(relative.split("/")) if relative else ""


def get_command_name_from_file(file_path: str, base_dir: str, plugin_name: str) -> str:
    """`getCommandNameFromFile`: `<plugin>[:<namespace>]:<name>`; a skill is named after its directory."""
    if is_skill_file(file_path):
        skill_directory = os.path.dirname(file_path)
        namespace = _namespace(os.path.dirname(skill_directory), base_dir)
        base_name = os.path.basename(skill_directory)
    else:
        namespace = _namespace(os.path.dirname(file_path), base_dir)
        base_name = re.sub(r"\.md$", "", os.path.basename(file_path))
    return f"{plugin_name}:{namespace}:{base_name}" if namespace else f"{plugin_name}:{base_name}"


def _read_markdown(file_path: str, base_dir: str) -> _MarkdownFile:
    with open(file_path, encoding="utf-8") as handle:
        frontmatter, content = parse_frontmatter(handle.read())
    return _MarkdownFile(file_path, base_dir, frontmatter, content)


async def _collect_markdown_files(dir_path: str, base_dir: str, loaded_paths: set[str]) -> list[_MarkdownFile]:
    """`collectMarkdownFiles`: every `.md` under `dir_path`, a skill directory being a leaf."""
    files: list[_MarkdownFile] = []

    async def on_file(full_path: str, _namespace: list[str]) -> None:
        if is_duplicate_path(full_path, loaded_paths):
            return
        files.append(_read_markdown(full_path, base_dir))

    await walk_plugin_markdown(dir_path, on_file, stop_at_skill_dir=True, log_label="commands")
    return files


def transform_plugin_skill_files(files: list[_MarkdownFile]) -> list[_MarkdownFile]:
    """`transformPluginSkillFiles`: a directory holding a `SKILL.md` contributes that file only."""
    by_dir: dict[str, list[_MarkdownFile]] = {}
    for file in files:
        by_dir.setdefault(os.path.dirname(file.file_path), []).append(file)
    result: list[_MarkdownFile] = []
    for directory, dir_files in by_dir.items():
        skill_files = [f for f in dir_files if is_skill_file(f.file_path)]
        if skill_files:
            if len(skill_files) > 1:
                logger.debug("Multiple skill files found in %s, using %s", directory,
                             os.path.basename(skill_files[0].file_path))
            result.append(skill_files[0])
        else:
            result.extend(dir_files)
    return result


def _substitute_allowed_tools(raw: Any, plugin: _PluginRef) -> Any:
    if isinstance(raw, str):
        return substitute_plugin_variables(raw, plugin)
    if isinstance(raw, list):
        return [substitute_plugin_variables(t, plugin) if isinstance(t, str) else t for t in raw]
    return raw


def create_plugin_command(
    command_name: str,
    file: _MarkdownFile,
    plugin: LoadedPlugin,
    locations: SettingsLocations,
    is_skill: bool,
    is_skill_mode: bool = False,
) -> PluginSkillCommand | None:
    """`createPluginCommand`: the command, or `None` (logged) when it cannot be built."""
    try:
        frontmatter, content = file.frontmatter, file.content
        validated = coerce_description_to_string(frontmatter.get("description"), command_name)
        description = validated or extract_description_from_markdown(
            content, "Plugin skill" if is_skill else "Plugin command")
        ref = _PluginRef(plugin.path, plugin.source)
        allowed_tools = parse_slash_command_tools_from_frontmatter(
            _substitute_allowed_tools(frontmatter.get("allowed-tools"), ref))
        model_raw = frontmatter.get("model")
        model = parse_user_specified_model(str(model_raw)) if model_raw and model_raw != "inherit" else None
        user_invocable_raw = frontmatter.get("user-invocable")
        user_invocable = True if user_invocable_raw is None else parse_boolean_frontmatter(user_invocable_raw)
        skill_like = is_skill or is_skill_mode
        argument_hint = frontmatter.get("argument-hint")
        return PluginSkillCommand(
            name=command_name,
            description=description,
            markdown_content=content,
            source="plugin",
            loaded_from="plugin" if skill_like else None,
            skill_root=None,
            display_name=str(frontmatter["name"]) if frontmatter.get("name") else None,
            has_user_specified_description=validated is not None,
            allowed_tools=allowed_tools,
            argument_hint=str(argument_hint) if argument_hint is not None else None,
            argument_names=tuple(parse_argument_names(frontmatter.get("arguments"))),
            when_to_use=frontmatter.get("when_to_use"),
            version=frontmatter.get("version"),
            model=model,
            disable_model_invocation=parse_boolean_frontmatter(frontmatter.get("disable-model-invocation")),
            user_invocable=user_invocable,
            progress_message="loading" if skill_like else "running",
            plugin_name=plugin.manifest.get("name"),
            plugin_path=plugin.path,
            plugin_source=plugin.source,
            user_config_schema=plugin.manifest.get("userConfig"),
            settings_locations=locations,
            is_skill_mode=is_skill_mode,
            file_path=file.file_path,
        )
    except Exception as exc:  # noqa: BLE001 - upstream logs a command it cannot build and skips it
        logger.error("Failed to create command from %s: %s", file.file_path, exc)
        return None


async def load_commands_from_directory(
    commands_path: str, plugin: LoadedPlugin, locations: SettingsLocations, loaded_paths: set[str]
) -> list[PluginSkillCommand]:
    """`loadCommandsFromDirectory`."""
    files = transform_plugin_skill_files(await _collect_markdown_files(commands_path, commands_path, loaded_paths))
    commands: list[PluginSkillCommand] = []
    for file in files:
        name = get_command_name_from_file(file.file_path, file.base_dir, plugin.name)
        command = create_plugin_command(name, file, plugin, locations, is_skill_file(file.file_path))
        if command is not None:
            commands.append(command)
    return commands


def _apply_metadata(frontmatter: Mapping[str, Any], metadata: Mapping[str, Any]) -> dict[str, Any]:
    """The `CommandMetadata` overrides on a command's frontmatter."""
    final = dict(frontmatter)
    if metadata.get("description"):
        final["description"] = metadata["description"]
    if metadata.get("argumentHint"):
        final["argument-hint"] = metadata["argumentHint"]
    if metadata.get("model"):
        final["model"] = metadata["model"]
    if metadata.get("allowedTools"):
        final["allowed-tools"] = ",".join(metadata["allowedTools"])
    return final


async def _load_command_path(
    command_path: str, plugin: LoadedPlugin, locations: SettingsLocations, loaded_paths: set[str]
) -> list[PluginSkillCommand]:
    """One of the manifest's `commandsPaths`: a directory, or one `.md` file (with its metadata)."""
    try:
        if os.path.isdir(command_path):
            commands = await load_commands_from_directory(command_path, plugin, locations, loaded_paths)
            if not commands:
                logger.warning(
                    "Warning: No commands found in plugin %s custom directory: %s. Expected .md files or SKILL.md "
                    "in subdirectories.", plugin.name, command_path)
            return commands
        if os.path.isfile(command_path) and command_path.endswith(".md"):
            if is_duplicate_path(command_path, loaded_paths):
                return []
            file = _read_markdown(command_path, os.path.dirname(command_path))
            command_name: str | None = None
            metadata: Mapping[str, Any] | None = None
            for name, entry in (plugin.commands_metadata or {}).items():
                source = entry.get("source")
                if source and command_path == os.path.normpath(os.path.join(plugin.path, source)):
                    command_name, metadata = f"{plugin.name}:{name}", entry
                    break
            if command_name is None:
                command_name = f"{plugin.name}:{re.sub(r'\.md$', '', os.path.basename(command_path))}"
            if metadata is not None:
                file = _MarkdownFile(file.file_path, file.base_dir, _apply_metadata(file.frontmatter, metadata),
                                     file.content)
            command = create_plugin_command(command_name, file, plugin, locations, False)
            return [command] if command is not None else []
        return []
    except Exception as exc:  # noqa: BLE001 - one bad path must not stop the plugin's other commands
        logger.error("Failed to load commands from plugin %s custom path %s: %s", plugin.name, command_path, exc)
        return []


def _inline_commands(plugin: LoadedPlugin, locations: SettingsLocations) -> list[PluginSkillCommand]:
    """The `commands` metadata entries with inline `content` (no `source` file)."""
    commands: list[PluginSkillCommand] = []
    for name, metadata in (plugin.commands_metadata or {}).items():
        if not metadata.get("content") or metadata.get("source"):
            continue
        try:
            frontmatter, content = parse_frontmatter(str(metadata["content"]))
            command_name = f"{plugin.name}:{name}"
            file = _MarkdownFile(f"<inline:{command_name}>", plugin.path, _apply_metadata(frontmatter, metadata),
                                 content)
            command = create_plugin_command(command_name, file, plugin, locations, False)
            if command is not None:
                commands.append(command)
        except Exception as exc:  # noqa: BLE001 - one bad entry must not stop the others
            logger.error("Failed to load inline content command %s from plugin %s: %s", name, plugin.name, exc)
    return commands


async def _commands_of(plugin: LoadedPlugin, locations: SettingsLocations) -> list[PluginSkillCommand]:
    loaded_paths: set[str] = set()
    commands: list[PluginSkillCommand] = []
    if plugin.commands_path:
        try:
            commands.extend(await load_commands_from_directory(plugin.commands_path, plugin, locations, loaded_paths))
        except Exception as exc:  # noqa: BLE001 - upstream logs and goes on
            logger.error("Failed to load commands from plugin %s default directory: %s", plugin.name, exc)
    for found in await asyncio.gather(*(
            _load_command_path(p, plugin, locations, loaded_paths) for p in plugin.commands_paths or ())):
        commands.extend(found)
    commands.extend(_inline_commands(plugin, locations))
    return commands


async def load_skills_from_directory(
    skills_path: str, plugin: LoadedPlugin, locations: SettingsLocations, loaded_paths: set[str]
) -> list[PluginSkillCommand]:
    """`loadSkillsFromDirectory`: `skills_path/SKILL.md` itself, else each `<dir>/SKILL.md` under it."""
    direct = os.path.join(skills_path, "SKILL.md")
    if os.path.isfile(direct):
        if is_duplicate_path(direct, loaded_paths):
            return []
        try:
            file = _read_markdown(direct, os.path.dirname(direct))
        except OSError as exc:
            logger.error("Failed to load skill from %s: %s", direct, exc)
            return []
        skill = create_plugin_command(f"{plugin.name}:{os.path.basename(skills_path)}", file, plugin, locations,
                                      True, is_skill_mode=True)
        return [skill] if skill is not None else []
    try:
        entries = sorted(os.scandir(skills_path), key=lambda e: e.name)
    except OSError as exc:
        if not isinstance(exc, FileNotFoundError):
            logger.error("Failed to load skills from directory %s: %s", skills_path, exc)
        return []
    skills: list[PluginSkillCommand] = []
    for entry in entries:
        if not entry.is_dir() and not entry.is_symlink():
            continue
        skill_file = os.path.join(skills_path, entry.name, "SKILL.md")
        try:
            file = _read_markdown(skill_file, os.path.dirname(skill_file))
        except OSError as exc:
            if not isinstance(exc, (FileNotFoundError, NotADirectoryError)):
                logger.error("Failed to load skill from %s: %s", skill_file, exc)
            continue
        if is_duplicate_path(skill_file, loaded_paths):
            continue
        skill = create_plugin_command(f"{plugin.name}:{entry.name}", file, plugin, locations, True,
                                      is_skill_mode=True)
        if skill is not None:
            skills.append(skill)
    return skills


async def _skills_of(plugin: LoadedPlugin, locations: SettingsLocations) -> list[PluginSkillCommand]:
    loaded_paths: set[str] = set()
    skills: list[PluginSkillCommand] = []
    for path in [*([plugin.skills_path] if plugin.skills_path else []), *(plugin.skills_paths or ())]:
        try:
            skills.extend(await load_skills_from_directory(path, plugin, locations, loaded_paths))
        except Exception as exc:  # noqa: BLE001 - upstream logs and goes on
            logger.error("Failed to load skills from plugin %s path %s: %s", plugin.name, path, exc)
    return skills


Loader = Callable[[LoadedPlugin, SettingsLocations], Coroutine[Any, Any, list[PluginSkillCommand]]]


class _Memo:
    """lodash `memoize` per run's `locations`, cleared by the `clear_*` functions."""

    def __init__(self, loader: Loader, label: str) -> None:
        self.loader = loader
        self.label = label
        self.cache: dict[SettingsLocations, list[PluginSkillCommand]] = {}

    async def __call__(self, locations: SettingsLocations) -> list[PluginSkillCommand]:
        cached = self.cache.get(locations)
        if cached is not None:
            return list(cached)
        result = await load_all_plugins_cache_only(locations)
        if result.errors:
            logger.debug("Plugin loading errors: %s", ", ".join(get_plugin_error_message(e) for e in result.errors))
        per_plugin = await asyncio.gather(*(self.loader(p, locations) for p in result.enabled))
        loaded = [c for commands in per_plugin for c in commands]
        logger.debug("Total plugin %s loaded: %d", self.label, len(loaded))
        self.cache[locations] = loaded
        return list(loaded)


#: `getPluginCommands`: the enabled plugins' `commands/` entries, custom command paths and inline commands.
get_plugin_commands = _Memo(_commands_of, "commands")
#: `getPluginSkills`: the enabled plugins' `skills/` entries and custom skill paths.
get_plugin_skills = _Memo(_skills_of, "skills")


def clear_plugin_command_cache() -> None:
    """`clearPluginCommandCache`."""
    get_plugin_commands.cache.clear()


def clear_plugin_skills_cache() -> None:
    """`clearPluginSkillsCache`."""
    get_plugin_skills.cache.clear()


async def get_all_plugin_commands(locations: SettingsLocations) -> list[PluginSkillCommand]:
    """`loadAllCommands`' plugin half, in its order: `pluginCommands`, then `pluginSkills`."""
    commands, skills = await asyncio.gather(get_plugin_commands(locations), get_plugin_skills(locations))
    return [*commands, *skills]


__all__ = [
    "PluginSkillCommand",
    "clear_plugin_command_cache",
    "clear_plugin_skills_cache",
    "create_plugin_command",
    "get_all_plugin_commands",
    "get_command_name_from_file",
    "get_plugin_commands",
    "get_plugin_skills",
    "is_duplicate_path",
    "is_skill_file",
    "load_commands_from_directory",
    "load_skills_from_directory",
    "transform_plugin_skill_files",
]
