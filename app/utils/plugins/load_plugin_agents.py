"""
The enabled plugins' agents (`agents/` and the manifest's agent paths), as agent definitions.

Ported from `reference/claude-code/src/utils/plugins/loadPluginAgents.ts` -> `VALID_MEMORY_SCOPES`,
`loadAgentsFromDirectory`, `loadAgentFromFile`, `loadPluginAgents`, `clearPluginAgentCache`.

A plugin agent is named `<plugin>:<namespace…>:<name>` (the frontmatter `name`, else the file
name), its `description` is the frontmatter `description`, else `when-to-use`, else `Agent from
<plugin> plugin`, and its prompt has `${CLAUDE_PLUGIN_ROOT}` / `${CLAUDE_PLUGIN_DATA}` and the
plugin's non-sensitive `${user_config.X}` substituted. As upstream, `permissionMode`, `hooks` and
`mcpServers` are ignored for plugin agents (logged): they would escalate what the agent can do
beyond what the user approved at install time.

`load_plugin_agents(locations)` reads `load_all_plugins_cache_only(locations)` and is memoized
per `locations` until `clear_plugin_agent_cache` (called by `clear_all_plugin_caches`). The
caller joins them to the other agents (`get_agent_definitions_with_overrides(plugin_agents=…)`).

TDDAgents' `phase` field is read as for every agent (`parse_agent_phase`), and an invalid one
fails the load loudly (`AgentFrontmatterError`, never swallowed): dropping it silently would
disable the TDD invariant. The memory scopes are this build's (`run`, `session`, `none` besides
upstream's three). Not ported (no subsystem here): `color`, `effort`, `isolation`, and the
memory prompt `getSystemPrompt` appends (this build appends agent memory when the worker runs).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

from app.memdir.paths import is_auto_memory_enabled
from app.skills.load_skills_dir import (
    coerce_description_to_string,
    parse_slash_command_tools_from_frontmatter,
)
from app.tools.agent_tool.load_agents_dir import (
    AgentDefinition,
    AgentFrontmatterError,
    parse_agent_phase,
    parse_agent_tools_from_frontmatter,
    parse_markdown_frontmatter,
)
from app.types.plugin import LoadedPlugin, get_plugin_error_message
from app.utils.plugins.load_plugin_commands import is_duplicate_path
from app.utils.plugins.plugin_loader import load_all_plugins_cache_only
from app.utils.plugins.plugin_options_storage import (
    load_plugin_options,
    substitute_plugin_variables,
    substitute_user_config_in_content,
)
from app.utils.plugins.walk_plugin_markdown import walk_plugin_markdown
from app.utils.settings.settings import SettingsLocations

logger = logging.getLogger("TDDOrchestrator.Plugins")

#: `VALID_MEMORY_SCOPES`, with TDDAgents' run-scoped memories (`load_agents_dir.py`).
VALID_MEMORY_SCOPES = ("user", "project", "local", "run", "session", "none")
#: The fields a plugin agent may not set (`loadAgentFromFile`).
IGNORED_PLUGIN_AGENT_FIELDS = ("permissionMode", "hooks", "mcpServers")
#: `FILE_WRITE_TOOL_NAME`, `FILE_EDIT_TOOL_NAME`, `FILE_READ_TOOL_NAME`.
_MEMORY_FILE_TOOLS = ("WriteFile", "Edit", "ReadFile")
_LEADING_INT = re.compile(r"^\s*[+-]?\d+")


def _positive_int(value: Any) -> int | None:
    """`parsePositiveIntFromFrontmatter`."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    match = _LEADING_INT.match(str(value))
    parsed = int(match.group(0)) if match else None
    return parsed if parsed is not None and parsed > 0 else None


def load_agent_from_file(
    file_path: str,
    plugin: LoadedPlugin,
    namespace: list[str],
    locations: SettingsLocations,
    loaded_paths: set[str],
) -> AgentDefinition | None:
    """`loadAgentFromFile`: the agent, or `None` (a duplicate path, or a file that failed to load)."""
    if is_duplicate_path(file_path, loaded_paths):
        return None
    try:
        with open(file_path, encoding="utf-8") as handle:
            frontmatter, markdown = parse_markdown_frontmatter(handle.read())
        base_name = frontmatter.get("name") or re.sub(r"\.md$", "", os.path.basename(file_path))
        agent_type = ":".join([plugin.name, *namespace, str(base_name)])
        description = (
            coerce_description_to_string(frontmatter.get("description"), agent_type)
            or coerce_description_to_string(frontmatter.get("when-to-use"), agent_type)
            or f"Agent from {plugin.name} plugin"
        )
        tools = parse_agent_tools_from_frontmatter(frontmatter.get("tools"))
        model_raw = frontmatter.get("model")
        model: str | None = None
        if isinstance(model_raw, str) and model_raw.strip():
            trimmed = model_raw.strip()
            model = "inherit" if trimmed.lower() == "inherit" else trimmed
        background = True if frontmatter.get("background") in ("true", True) else None
        system_prompt = substitute_plugin_variables(markdown.strip(), plugin)
        if plugin.manifest.get("userConfig"):
            system_prompt = substitute_user_config_in_content(
                system_prompt, load_plugin_options(plugin.source, locations), plugin.manifest["userConfig"])
        memory: str | None = None
        memory_raw = frontmatter.get("memory")
        if memory_raw is not None:
            if memory_raw in VALID_MEMORY_SCOPES:
                memory = str(memory_raw)
            else:
                logger.debug("Plugin agent file %s has invalid memory value '%s'. Valid options: %s", file_path,
                             memory_raw, ", ".join(VALID_MEMORY_SCOPES))
        for ignored in IGNORED_PLUGIN_AGENT_FIELDS:
            if ignored in frontmatter:
                logger.warning(
                    "Plugin agent file %s sets %s, which is ignored for plugin agents. Use .tddagents/agents/ for "
                    "this level of control.", file_path, ignored)
        max_turns_raw = frontmatter.get("maxTurns")
        max_turns = _positive_int(max_turns_raw)
        if max_turns_raw is not None and max_turns is None:
            logger.debug("Plugin agent file %s has invalid maxTurns '%s'. Must be a positive integer.", file_path,
                         max_turns_raw)
        disallowed = (parse_agent_tools_from_frontmatter(frontmatter["disallowedTools"])
                      if "disallowedTools" in frontmatter else None)
        if is_auto_memory_enabled() and memory and tools is not None:
            tools = (*tools, *(t for t in _MEMORY_FILE_TOOLS if t not in tools))
        return AgentDefinition(
            name=agent_type,
            description=description,
            prompt=system_prompt,
            phase=parse_agent_phase(frontmatter.get("phase"), agent_type),
            tools=tools,
            disallowed_tools=disallowed,
            memory=memory,
            max_turns=max_turns,
            model=model,
            background=background,
            skills=parse_slash_command_tools_from_frontmatter(frontmatter.get("skills")),
            source="plugin",
            plugin=plugin.source,
            base_dir=os.path.dirname(file_path),
            raw_frontmatter=dict(frontmatter),
        )
    except AgentFrontmatterError:
        raise
    except Exception as exc:  # noqa: BLE001 - upstream logs an agent file it cannot load and skips it
        logger.error("Failed to load agent from %s: %s", file_path, exc)
        return None


async def load_agents_from_directory(
    agents_path: str, plugin: LoadedPlugin, locations: SettingsLocations, loaded_paths: set[str]
) -> list[AgentDefinition]:
    """`loadAgentsFromDirectory`: every `.md` under `agents_path`, namespaced by subdirectory."""
    agents: list[AgentDefinition] = []
    invalid: list[AgentFrontmatterError] = []

    async def on_file(full_path: str, namespace: list[str]) -> None:
        try:
            agent = load_agent_from_file(full_path, plugin, namespace, locations, loaded_paths)
        except AgentFrontmatterError as error:
            # Kept from the walker's per-directory catch, so the load fails loudly afterwards.
            invalid.append(error)
            return
        if agent is not None:
            agents.append(agent)

    await walk_plugin_markdown(agents_path, on_file, log_label="agents")
    if invalid:
        raise invalid[0]
    return agents


async def _agents_of(plugin: LoadedPlugin, locations: SettingsLocations) -> list[AgentDefinition]:
    loaded_paths: set[str] = set()
    agents: list[AgentDefinition] = []
    if plugin.agents_path:
        agents.extend(await load_agents_from_directory(plugin.agents_path, plugin, locations, loaded_paths))
    for agent_path in plugin.agents_paths or ():
        try:
            if os.path.isdir(agent_path):
                agents.extend(await load_agents_from_directory(agent_path, plugin, locations, loaded_paths))
            elif os.path.isfile(agent_path) and agent_path.endswith(".md"):
                agent = load_agent_from_file(agent_path, plugin, [], locations, loaded_paths)
                if agent is not None:
                    agents.append(agent)
        except OSError as exc:
            logger.error("Failed to load agents from plugin %s custom path %s: %s", plugin.name, agent_path, exc)
    return agents


_cache: dict[SettingsLocations, list[AgentDefinition]] = {}


async def load_plugin_agents(locations: SettingsLocations) -> list[AgentDefinition]:
    """`loadPluginAgents` (memoized per run's `locations`): the enabled plugins' agents."""
    cached = _cache.get(locations)
    if cached is not None:
        return list(cached)
    result = await load_all_plugins_cache_only(locations)
    if result.errors:
        logger.debug("Plugin loading errors: %s", ", ".join(get_plugin_error_message(e) for e in result.errors))
    per_plugin = await asyncio.gather(*(_agents_of(p, locations) for p in result.enabled))
    agents = [a for found in per_plugin for a in found]
    logger.debug("Total plugin agents loaded: %d", len(agents))
    _cache[locations] = agents
    return list(agents)


def clear_plugin_agent_cache() -> None:
    """`clearPluginAgentCache`."""
    _cache.clear()


__all__ = [
    "IGNORED_PLUGIN_AGENT_FIELDS",
    "VALID_MEMORY_SCOPES",
    "clear_plugin_agent_cache",
    "load_agent_from_file",
    "load_agents_from_directory",
    "load_plugin_agents",
]
