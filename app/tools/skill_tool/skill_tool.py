"""
The Skill tool (Phase 8).

Ported from `reference/claude-code/src/tools/SkillTool/SkillTool.ts` -> `SkillTool`
(`validateInput`, `checkPermissions`, `call`, `mapToolResultToToolResultBlockParam`,
`skillHasOnlySafeProperties`, `executeForkedSkill`);
`utils/processUserInput/processSlashCommand.tsx` -> `getMessagesForPromptSlashCommand`;
`utils/forkedAgent.ts` -> `prepareForkedCommandContext`, `createGetAppStateWithAllowedTools`,
`extractResultText`.

**Inline** (the default): the result says `Launching skill: <name>` and the skill's text joins
the conversation as an `isMeta` user message (`newMessages`), so it reaches the model next turn.
The skill's `allowed-tools` become `command`-source allow rules through a context modifier,
for the rest of the turn's calls and the turns after it, exactly as upstream's
`alwaysAllowRules.command`. A skill `model` becomes the context's `main_loop_model` the same
way (`resolveSkillModelOverride`: a `[1m]` session keeps `[1m]` on a model that supports it).

**TDD:** those rules are ordinary allow rules. The TDD phase tier runs above every rule in the
gate (`utils/permissions/permissions.py::tdd_phase_denial`), so a skill granting `WriteFile` cannot write
implementation in RED; it can only spare an 'ask' the phase already permits.

**Fork** (`context: fork`): the skill runs as a worker (`run_agent`, synchronous) on the agent
its `agent` field names — upstream's `general-purpose` fallback, else the first agent — with the
allowed tools granted on the worker's state only, and the worker's last text is the result.
**TDD:** when that agent has a `phase`, the fork goes through the Agent tool's phase-on-spawn
(`app/tdd/phase_on_spawn.py`, user decision 2026-10-25): GREEN needs a confirmed RED,
REFACTOR a completed cycle, RED restarts the cycle, and a refused move fails the call before any
worker runs. Only `RunTests` can record an observation.

Permissions are upstream's: a `Skill(name)` / `Skill(prefix:*)` deny or allow rule decides; a
skill with only safe properties (no `allowed-tools`, no `hooks`) is allowed; anything else asks,
which a headless run denies.

Divergences: the result messages are not tagged with the tool use id
(`tagMessagesWithToolUseID`, a UI concern); the `command_permissions` attachment, which upstream
never shows the model, is not emitted.
Not ported: remote skills, `addInvokedSkill` (no post-compact skill attachment), skill `hooks`
registration (no session-hook store), effort, progress events, and the user-input attachments
upstream computes for the skill text (no at-mention source in this build).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from app.query.config import RunConfig
from app.query.deps import LoopDeps
from app.skills.load_skills_dir import SkillCommand, SkillRegistry
from app.state.app_state_store import AppState
from app.tool import (
    BuiltTool,
    PermissionResult,
    ToolContext,
    ToolResult,
    ValidationResult,
    build_tool,
)
from app.tools.agent_tool.load_agents_dir import AgentDefinition
from app.tools.skill_tool.prompt import SKILL_TOOL_NAME, get_prompt
from app.types.permissions import (
    PermissionBehavior,
    PermissionRule,
    PermissionRuleSource,
    ToolPermissionContext,
)
from app.utils.messages import Message
from app.utils.model.model import parse_user_specified_model
from app.utils.permissions.permissions import extract_permission_context
from app.utils.settings.settings import (
    parse_tool_list_from_cli,
    permission_rule_value_from_string,
)
from app.utils.tokens import has_1m_context

logger = logging.getLogger(__name__)

SKILL_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "skill": {"type": "string", "description": 'The skill name. E.g., "commit", "review-pr", or "pdf"'},
        "args": {"type": "string", "description": "Optional arguments for the skill"},
    },
    "required": ["skill"],
}
#: `SkillTool.maxResultSizeChars`.
MAX_RESULT_SIZE_CHARS = 100_000
DEFAULT_FORK_AGENT = "general-purpose"
FORK_DEFAULT_RESULT = "Skill execution completed"


def _command_name(skill: str) -> str:
    trimmed = skill.strip()
    return trimmed.removeprefix("/")


def skill_has_only_safe_properties(command: SkillCommand) -> bool:
    """`skillHasOnlySafeProperties`: of our fields, only `allowed-tools` and `hooks` are unsafe."""
    return not command.allowed_tools and not command.hooks


def _rule_matches(rule_content: str, command_name: str) -> bool:
    normalized = rule_content.removeprefix("/")
    if normalized == command_name:
        return True
    return normalized.endswith(":*") and command_name.startswith(normalized[:-2])


def _skill_rules(rules: Sequence[PermissionRule]) -> list[PermissionRule]:
    """`getRuleByContentsForTool`: the tool's rules that carry content, in order."""
    return [r for r in rules if r.tool_name == SKILL_TOOL_NAME and r.rule_content is not None]


def with_allowed_tools(
    permission_context: ToolPermissionContext, allowed_tools: Sequence[str]
) -> ToolPermissionContext:
    """`alwaysAllowRules.command` extended with `allowed_tools` (deduplicated, order kept)."""
    rules = list(permission_context.always_allow_rules)
    for value in allowed_tools:
        tool_name, content = permission_rule_value_from_string(value)
        rule = PermissionRule(tool_name=tool_name, rule_behavior=PermissionBehavior.ALLOW,
                              rule_content=content, source=PermissionRuleSource.COMMAND)
        if rule not in rules:
            rules.append(rule)
    return replace(permission_context, always_allow_rules=tuple(rules))


def get_app_state_with_allowed_tools(
    base: Callable[[], AppState], allowed_tools: Sequence[str]
) -> Callable[[], AppState]:
    """`createGetAppStateWithAllowedTools`."""
    if not allowed_tools:
        return base

    def get() -> AppState:
        state = base()
        return replace(state, tool_permission_context=with_allowed_tools(state.tool_permission_context,
                                                                         allowed_tools))

    return get


def context_with_allowed_tools(context: ToolContext, allowed_tools: Sequence[str]) -> ToolContext:
    """The context modifier's permission half: app state and, when set, the context's own copy."""
    if not allowed_tools:
        return context
    perm = context.permission_context
    return replace(
        context,
        get_app_state=get_app_state_with_allowed_tools(context.get_app_state, allowed_tools),
        permission_context=with_allowed_tools(perm, allowed_tools) if isinstance(perm, ToolPermissionContext)
        else perm,
    )


def format_skill_loading_metadata(skill_name: str) -> str:
    """`formatSkillLoadingMetadata`."""
    return "\n".join([f"<command-message>{skill_name}</command-message>",
                      f"<command-name>{skill_name}</command-name>", "<skill-format>true</skill-format>"])


def coordinator_skill_summary(command: SkillCommand) -> str:
    """`getMessagesForPromptSlashCommand`'s coordinator branch: the skill described, not loaded."""
    parts = [f'Skill "/{command.name}" is available for workers.']
    if command.description:
        parts.append(f"Description: {command.description}")
    if command.when_to_use:
        parts.append(f"When to use: {command.when_to_use}")
    if command.allowed_tools:
        parts.append(f"This skill grants workers additional tool permissions: {', '.join(command.allowed_tools)}")
    parts.append(
        f'\nInstruct a worker to use this skill by including "Use the /{command.name} skill" in your Agent prompt. '
        "The worker has access to the Skill tool and will receive the skill's content and permissions when it "
        "invokes it."
    )
    return "\n".join(parts)


def model_supports_1m(model: str) -> bool:
    """`modelSupports1M` on the resolved id (`getCanonicalName` has no table to consult here)."""
    canonical = parse_user_specified_model(model).lower()
    return "claude-sonnet-4" in canonical or "opus-4-6" in canonical


def resolve_skill_model_override(skill_model: str, current_model: str) -> str:
    """`resolveSkillModelOverride`: keep a `[1m]` session's window when the skill's model allows it."""
    if has_1m_context(skill_model) or not has_1m_context(current_model):
        return skill_model
    return skill_model + "[1m]" if model_supports_1m(skill_model) else skill_model


def _meta(content: str) -> HumanMessage:
    return HumanMessage(content=content, additional_kwargs={"is_meta": True})


def _text_of(message: AIMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "\n".join(str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text")


def extract_result_text(agent_messages: Sequence[Message], default_text: str = "Execution completed") -> str:
    """`extractResultText`: the last assistant message's text, or the default."""
    last = next((m for m in reversed(agent_messages) if isinstance(m, AIMessage)), None)
    if last is None:
        return default_text
    return _text_of(last) or default_text


def select_fork_agent(command: SkillCommand, definitions: Mapping[str, AgentDefinition]) -> AgentDefinition:
    """`prepareForkedCommandContext`'s agent: the named one, `general-purpose`, else the first."""
    agents = list(definitions.values())
    name = command.agent or DEFAULT_FORK_AGENT
    found = next((a for a in agents if a.name == name), None) or next(
        (a for a in agents if a.name == DEFAULT_FORK_AGENT), None
    )
    if found is None and agents:
        found = agents[0]
    if found is None:
        raise ValueError("No agent available for forked execution")
    return found


def build_skill_tool(
    registry: SkillRegistry,
    *,
    config: RunConfig,
    deps: LoopDeps,
    agent_definitions: Mapping[str, AgentDefinition] | None = None,
) -> BuiltTool:
    """The Skill tool for one run; `config`/`deps`/`agent_definitions` drive forked skills."""

    definitions = dict(agent_definitions or {})

    def validate_input(args: dict[str, Any], context: ToolContext) -> ValidationResult:
        skill = str(args["skill"])  # required by the schema, which is validated first
        if not skill.strip():
            return ValidationResult(valid=False, message=f"Invalid skill format: {skill}", error_code=1)
        name = _command_name(skill)
        command = registry.find_command(name)
        if command is None:
            return ValidationResult(valid=False, message=f"Unknown skill: {name}", error_code=2)
        if command.disable_model_invocation:
            return ValidationResult(
                valid=False,
                message=f"Skill {name} cannot be used with {SKILL_TOOL_NAME} tool due to disable-model-invocation",
                error_code=4,
            )
        return ValidationResult(valid=True)

    def check_permissions(args: dict[str, Any], context: ToolContext) -> PermissionResult:
        name = _command_name(str(args["skill"]))
        perm = extract_permission_context(context)
        updated = {"skill": args.get("skill"), "args": args.get("args")}
        for rule in _skill_rules(perm.always_deny_rules):
            if _rule_matches(str(rule.rule_content), name):
                return PermissionResult(behavior="deny", message="Skill execution blocked by permission rules",
                                        decision_reason={"type": "rule", "rule": rule})
        for rule in _skill_rules(perm.always_allow_rules):
            if _rule_matches(str(rule.rule_content), name):
                return PermissionResult(behavior="allow", updated_input=updated,
                                        decision_reason={"type": "rule", "rule": rule})
        command = registry.find_command(name)
        if command is not None and skill_has_only_safe_properties(command):
            return PermissionResult(behavior="allow", updated_input=updated)
        return PermissionResult(behavior="ask", message=f"Execute skill: {name}", updated_input=updated)

    async def call(args: dict[str, Any], context: ToolContext) -> ToolResult:
        name = _command_name(str(args["skill"]))
        skill_args = str(args.get("args") or "")
        command = registry.find_command(name)
        if command is None:
            raise ValueError(f"Unknown command: {name}")
        if command.context == "fork":
            return await _forked(command, name, skill_args, context)
        if config.gates.coordinator_mode and not context.agent_id:
            return ToolResult(
                content=f"Launching skill: {name}",
                new_messages=(_meta(coordinator_skill_summary(command)),),
                metadata={"success": True, "commandName": name, "model": command.model},
            )
        content = command.prompt_for_command(skill_args, session_id=context.run_id)
        allowed = parse_tool_list_from_cli(command.allowed_tools)
        skill_model = command.model

        def modify(ctx: ToolContext) -> ToolContext:
            modified = context_with_allowed_tools(ctx, allowed)
            if skill_model:
                current = ctx.main_loop_model or config.model
                modified = replace(modified, main_loop_model=resolve_skill_model_override(skill_model, current))
            return modified

        logger.debug("SkillTool returning 1 newMessages for skill %s", name)
        return ToolResult(
            content=f"Launching skill: {name}",
            new_messages=(_meta(content),),
            context_modifier=modify if allowed or skill_model else None,
            metadata={"success": True, "commandName": name, "allowedTools": allowed or None,
                      "model": command.model},
        )

    async def _forked(command: SkillCommand, name: str, skill_args: str, context: ToolContext) -> ToolResult:
        # Imported here: the worker loop pulls in the Agent tool's modules, which import this
        # package's light modules (`app.skills`), never this one.
        from app.context import enhance_system_prompt_with_env_details
        from app.tdd.phase_on_spawn import phase_on_spawn
        from app.tools.agent_tool.agent_tool import agent_system_prompt
        from app.tools.agent_tool.run_agent import create_agent_id, run_agent
        from app.utils.model.model import get_agent_model

        skill_content = command.prompt_for_command(skill_args, session_id=context.run_id)
        allowed = parse_tool_list_from_cli(command.allowed_tools)
        agent = select_fork_agent(command, definitions)
        ledger = context.get_app_state().phase_ledger
        spawn_ledger = phase_on_spawn(ledger, agent)
        if spawn_ledger is not ledger:
            context.set_app_state(lambda s: replace(s, phase_ledger=spawn_ledger))
        forked_context = context_with_allowed_tools(context, allowed)
        model = get_agent_model(agent.model, config.model, command.model)
        additional = forked_context.get_app_state().tool_permission_context.additional_working_directories
        system_prompt = enhance_system_prompt_with_env_details(
            [agent_system_prompt(agent, forked_context)], model, additional, context.workspace
        )
        agent_id = create_agent_id()
        logger.debug("SkillTool executing forked skill %s with agent %s", name, agent.name)
        messages: list[Message] = []
        events: AsyncIterator[Message] = run_agent(
            agent_definition=agent,
            prompt_messages=[HumanMessage(content=skill_content)],
            tool_context=forked_context,
            config=config,
            deps=deps,
            available_tools=context.tools,
            system_prompt=system_prompt,
            is_async=False,
            model_override=command.model,
            agent_id=agent_id,
        )
        async for message in events:
            messages.append(message)
        result = extract_result_text(messages, FORK_DEFAULT_RESULT)
        return ToolResult(
            content=f'Skill "{name}" completed (forked execution).\n\nResult:\n{result}',
            metadata={"success": True, "commandName": name, "status": "forked", "agentId": agent_id,
                      "result": result},
        )

    return build_tool(
        name=SKILL_TOOL_NAME,
        prompt=get_prompt(),
        call=call,
        input_schema=SKILL_TOOL_SCHEMA,
        description=lambda args: f"Execute skill: {args.get('skill')}",
        validate_input=validate_input,
        check_permissions=check_permissions,
        max_result_size_chars=MAX_RESULT_SIZE_CHARS,
    )


__all__ = [
    "MAX_RESULT_SIZE_CHARS",
    "SKILL_TOOL_SCHEMA",
    "build_skill_tool",
    "context_with_allowed_tools",
    "coordinator_skill_summary",
    "extract_result_text",
    "format_skill_loading_metadata",
    "get_app_state_with_allowed_tools",
    "model_supports_1m",
    "resolve_skill_model_override",
    "select_fork_agent",
    "skill_has_only_safe_properties",
    "with_allowed_tools",
]
