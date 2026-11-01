"""
Context usage: what fills the model's window, by category (`/context`, `get_context_usage`).

Ported from `reference/claude-code/src/utils/analyzeContext.ts` -> `TOOL_TOKEN_COUNT_OVERHEAD`,
`countTokensWithFallback`, `ContextData` (and its category, grid-square, memory-file, MCP-tool,
agent and skill shapes), `countToolDefinitionTokens`, `extractSectionName`, `countSystemTokens`,
`countMemoryFileTokens`, `countBuiltInToolTokens`, `countSlashCommandTokens`,
`countSkillTokens`, `countMcpToolTokens`, `countCustomAgentTokens`, `processAssistantMessage`,
`processUserMessage`, `processAttachment`, `approximateMessageTokens`, `analyzeContextUsage`;
`src/utils/api.ts` -> `toolToAPISchema` (its base `{name, description, input_schema}`);
`src/skills/loadSkillsDir.ts` -> `estimateSkillFrontmatterTokens`;
`src/tools/ToolSearchTool/prompt.ts` -> `isDeferredTool`.

The result is upstream's `ContextData` with its camelCase keys, so it is the SDK's
`SDKControlGetContextUsageResponse` (`src/entrypoints/sdk/controlSchemas.ts`) as it stands: an
optional key upstream leaves `undefined` is absent. The categories, their order, names and
theme colours, the reserved buffer, the free space and the grid are upstream's arithmetic.

**Counting.** Upstream counts each piece with the token-counting API (`countTokens`, then a
Haiku fallback). TDDAgents has no counting endpoint (its runs may be OpenAI's), so the counter
is a seam, `count_tokens`, defaulting to `rough_count_tokens`: the existing
`roughTokenCountEstimation` over the messages, plus `TOOL_TOKEN_COUNT_OVERHEAD` once when tools
are present, standing in for the tool preamble the API adds (the overhead upstream's per-tool
counts subtract). The message breakdown is upstream's rough estimate in any case.

**Messages** are LangChain's here: an `ai` message's blocks are its content plus its
`tool_calls` as `tool_use` blocks; a `tool` message is one `tool_result` block; a meta user
message carrying an attachment (`additional_kwargs["attachment"]`) is upstream's attachment
message; a `system` message (the compact boundary) is never input.

Not ported: the ant-only details (`systemTools`, `systemPromptSections`,
`deferredBuiltinTools`; TDDAgents is the external build, `USER_TYPE` is `external`), tool search
(no `ToolSearch` tool: nothing is deferred, so the deferred categories never appear and an MCP
tool is never `isLoaded`, as upstream's `isDeferredTool` says for an MCP tool without
`alwaysLoad`), `getRuntimeMainLoopModel` (no `opusplan` / plan-mode Haiku switch), the beta
headers of `getContextWindowForModel`, and the reactive-only and context-collapse reserved-buffer
suppressions (both feature-gated off, as in `app/services/compact/auto_compact.py`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from typing import Any, NotRequired, TypedDict

from langchain_core.messages import HumanMessage

from app.context import get_session_context
from app.services.api.claude import resolve_default_system_prompt
from app.services.compact.auto_compact import (
    AUTOCOMPACT_BUFFER_TOKENS,
    MANUAL_COMPACT_BUFFER_TOKENS,
    is_auto_compact_enabled,
)
from app.services.compact.micro_compact import microcompact_messages
from app.tool import Tool, find_tool_by_name
from app.tools.skill_tool.prompt import SKILL_TOOL_NAME
from app.utils.claudemd import get_memory_files
from app.utils.messages import (
    ATTACHMENT_KEY,
    Message,
    merge_split_assistant_messages,
    reorder_attachments_for_api,
    tool_calls_in,
)
from app.utils.system_prompt import build_effective_system_prompt
from app.utils.tokens import (
    get_context_window_for_model,
    get_current_usage,
    get_effective_context_window_size,
    rough_token_count_estimation,
    rough_token_count_estimation_for_messages,
)

logger = logging.getLogger(__name__)

RESERVED_CATEGORY_NAME = "Autocompact buffer"
MANUAL_COMPACT_BUFFER_NAME = "Compact buffer"
FREE_SPACE_NAME = "Free space"

#: `TOOL_TOKEN_COUNT_OVERHEAD`: the tool preamble the API adds once per call when tools are present.
TOOL_TOKEN_COUNT_OVERHEAD = 500

#: Agent sources as upstream names them (`SettingSource`); `built-in` and the rest pass through.
AGENT_SOURCES: Mapping[str, str] = {"user": "userSettings", "project": "projectSettings"}


# ── the result (`ContextData`, the SDK response) ───────────────────────────────

class ContextCategory(TypedDict):
    name: str
    tokens: int
    color: str
    #: These tokens are deferred and do not count toward the usage.
    isDeferred: NotRequired[bool]


class GridSquare(TypedDict):
    color: str
    isFilled: bool
    categoryName: str
    tokens: int
    percentage: int
    #: 0-1: how full this one square is.
    squareFullness: float


class MemoryFile(TypedDict):
    path: str
    type: str
    tokens: int


class McpTool(TypedDict):
    name: str
    serverName: str
    tokens: int
    isLoaded: NotRequired[bool]


class Agent(TypedDict):
    agentType: str
    source: str
    tokens: int


class SlashCommandInfo(TypedDict):
    totalCommands: int
    includedCommands: int
    tokens: int


class SkillFrontmatter(TypedDict):
    name: str
    source: str
    tokens: int


class SkillInfo(TypedDict):
    totalSkills: int
    includedSkills: int
    tokens: int
    skillFrontmatter: list[SkillFrontmatter]


class ToolCallsByType(TypedDict):
    name: str
    callTokens: int
    resultTokens: int


class AttachmentsByType(TypedDict):
    name: str
    tokens: int


class MessageBreakdownData(TypedDict):
    toolCallTokens: int
    toolResultTokens: int
    attachmentTokens: int
    assistantMessageTokens: int
    userMessageTokens: int
    toolCallsByType: list[ToolCallsByType]
    attachmentsByType: list[AttachmentsByType]


class ApiUsage(TypedDict):
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int


class ContextData(TypedDict):
    categories: list[ContextCategory]
    totalTokens: int
    maxTokens: int
    rawMaxTokens: int
    percentage: int
    gridRows: list[list[GridSquare]]
    model: str
    memoryFiles: list[MemoryFile]
    mcpTools: list[McpTool]
    agents: list[Agent]
    slashCommands: NotRequired[SlashCommandInfo]
    skills: NotRequired[SkillInfo]
    autoCompactThreshold: NotRequired[int]
    isAutoCompactEnabled: bool
    messageBreakdown: MessageBreakdownData
    #: The last API response's usage, when there is one.
    apiUsage: ApiUsage | None


# ── counting ────────────────────────────────────────────────────────────────

#: `countMessagesTokensWithAPI`'s shape: messages and tool schemas in, a count or `None` out.
TokenCounter = Callable[[Sequence[Message], Sequence[Mapping[str, Any]]], Awaitable[int | None]]


def json_stringify(value: Any) -> str:
    """`jsonStringify` (`JSON.stringify` without spacing)."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)


async def rough_count_tokens(messages: Sequence[Message], tools: Sequence[Mapping[str, Any]]) -> int | None:
    """
    The default counter: `roughTokenCountEstimation` over the messages and the tool schemas, and
    the API's tool preamble (`TOOL_TOKEN_COUNT_OVERHEAD`) once when there are tools.
    """
    total = rough_token_count_estimation_for_messages(messages)
    if tools:
        total += rough_token_count_estimation(json_stringify(list(tools))) + TOOL_TOKEN_COUNT_OVERHEAD
    return total


async def count_tokens_with_fallback(
    messages: Sequence[Message], tools: Sequence[Mapping[str, Any]], count_tokens: TokenCounter
) -> int | None:
    """`countTokensWithFallback`: the counter's answer, or `None` when it fails (logged)."""
    try:
        return await count_tokens(messages, tools)
    except Exception:  # noqa: BLE001 - a failed count reads as no count, as upstream's does
        logger.exception("countTokensWithFallback: counting failed (%d tools)", len(tools))
        return None


def _js_round(value: float) -> int:
    """`Math.round`: halves go up."""
    return math.floor(value + 0.5)


def _env_truthy(name: str) -> bool:
    value = os.environ.get(name)
    return value is not None and value.strip().lower() in ("1", "true", "yes", "on")


def tool_to_api_schema(tool: Tool) -> dict[str, Any]:
    """`toolToAPISchema`'s base: the name, the prompt as the description, the input schema."""
    return {"name": tool.name, "description": tool.prompt, "input_schema": tool.input_schema}


async def count_tool_definition_tokens(tools: Sequence[Tool], count_tokens: TokenCounter) -> int:
    """`countToolDefinitionTokens`: one count for the tools' schemas together, 0 when there is none."""
    result = await count_tokens_with_fallback([], [tool_to_api_schema(t) for t in tools], count_tokens)
    if not result:
        logger.debug("countToolDefinitionTokens returned %s for %d tools", result, len(tools))
    return result or 0


def is_deferred_tool(tool: Tool) -> bool:
    """`isDeferredTool`: an MCP tool is (no `alwaysLoad` here); nothing else sets `shouldDefer`."""
    return bool(tool.is_mcp)


# ── the system prompt ───────────────────────────────────────────────────────

def extract_section_name(content: str) -> str:
    """`extractSectionName`: the first Markdown heading, else the first line, cut at 40 characters."""
    heading = re.search(r"^#+\s+(.+)$", content, re.MULTILINE)
    if heading:
        return heading.group(1).strip()
    first_line = next((line for line in content.split("\n") if line.strip()), "")
    return first_line[:40] + "…" if len(first_line) > 40 else first_line


async def count_system_tokens(
    effective_system_prompt: Sequence[str], system_context: Mapping[str, str], count_tokens: TokenCounter
) -> tuple[int, list[dict[str, Any]]]:
    """
    `countSystemTokens` -> `(systemPromptTokens, systemPromptSections)`: each non-empty section,
    named by its heading, then each system-context value (`gitStatus`), counted on its own.
    """
    entries = [(extract_section_name(c), c) for c in effective_system_prompt if c] + [
        (name, c) for name, c in system_context.items() if c]
    if not entries:
        return 0, []
    counts = await asyncio.gather(*(
        count_tokens_with_fallback([HumanMessage(content=c)], [], count_tokens) for _, c in entries))
    sections = [{"name": name, "tokens": n or 0} for (name, _), n in zip(entries, counts, strict=True)]
    return sum(n or 0 for n in counts), sections


# ── memory files, tools, agents, skills ─────────────────────────────────────

async def count_memory_file_tokens(workspace: Any, count_tokens: TokenCounter) -> tuple[int, list[MemoryFile]]:
    """
    `countMemoryFileTokens` -> `(claudeMdTokens, memoryFileDetails)`. Simple mode
    (`TDDAGENTS_SIMPLE`, upstream's `CLAUDE_CODE_SIMPLE`) loads none, so reports none.
    `filterInjectedMemoryFiles` keeps every file (its GrowthBook flag is off).
    """
    if _env_truthy("TDDAGENTS_SIMPLE"):
        return 0, []
    files = get_memory_files(workspace)
    counts = await asyncio.gather(*(
        count_tokens_with_fallback([HumanMessage(content=f.content)], [], count_tokens) for f in files))
    details: list[MemoryFile] = [
        {"path": f.path, "type": f.type, "tokens": n or 0} for f, n in zip(files, counts, strict=True)]
    return sum(d["tokens"] for d in details), details


async def count_built_in_tool_tokens(tools: Sequence[Tool], count_tokens: TokenCounter) -> int:
    """
    `countBuiltInToolTokens`' `builtInToolTokens`: every non-MCP tool's schema, counted together.
    Without tool search no built-in is deferred, so they are all always loaded.
    """
    built_in = [t for t in tools if not t.is_mcp]
    if not built_in:
        return 0
    return await count_tool_definition_tokens(built_in, count_tokens)


def _skill_tool_commands(skill_registry: Any) -> list[Any]:
    """`getSkillToolCommands(cwd)`: the run's model-invocable skills."""
    return [] if skill_registry is None else list(skill_registry.get_skill_tool_commands())


async def count_slash_command_tokens(
    tools: Sequence[Tool], skill_registry: Any, count_tokens: TokenCounter
) -> tuple[int, int]:
    """`countSlashCommandTokens` -> `(slashCommandTokens, totalCommands)`: the Skill tool's schema."""
    total = len(_skill_tool_commands(skill_registry))
    skill_tool = find_tool_by_name(tools, SKILL_TOOL_NAME)
    if skill_tool is None:
        return 0, 0
    return await count_tool_definition_tokens([skill_tool], count_tokens), total


def estimate_skill_frontmatter_tokens(skill: Any) -> int:
    """`estimateSkillFrontmatterTokens`: the name, description and `when_to_use`, roughly."""
    text = " ".join(part for part in (skill.name, skill.description, skill.when_to_use) if part)
    return rough_token_count_estimation(text)


def count_skill_tokens(tools: Sequence[Tool], skill_registry: Any) -> list[SkillFrontmatter]:
    """
    `countSkillTokens`' `skillFrontmatter`: each skill the Skill tool lists, by its frontmatter
    (its body loads only on invocation). None without the Skill tool; a failure reports none.
    """
    try:
        skills = _skill_tool_commands(skill_registry)
        if find_tool_by_name(tools, SKILL_TOOL_NAME) is None:
            return []
        return [{"name": s.user_facing_name(), "source": s.source, "tokens": estimate_skill_frontmatter_tokens(s)}
                for s in skills]
    except Exception:  # noqa: BLE001 - skills must not fail the whole analysis (upstream's catch)
        logger.exception("countSkillTokens failed")
        return []


async def count_mcp_tool_tokens(tools: Sequence[Tool], count_tokens: TokenCounter) -> tuple[int, list[McpTool]]:
    """
    `countMcpToolTokens` -> `(mcpToolTokens, mcpToolDetails)`: one count for every MCP tool,
    less the one preamble, shared out by each tool's rough size (name, description, schema).
    """
    mcp_tools = [t for t in tools if t.is_mcp]
    total = max(0, await count_tool_definition_tokens(mcp_tools, count_tokens) - TOOL_TOKEN_COUNT_OVERHEAD)
    estimates = [rough_token_count_estimation(json_stringify(tool_to_api_schema(t))) for t in mcp_tools]
    estimate_total = sum(estimates) or 1
    details: list[McpTool] = []
    for tool, estimate in zip(mcp_tools, estimates, strict=True):
        parts = tool.name.split("__")
        details.append({
            "name": tool.name,
            "serverName": (parts[1] if len(parts) > 1 else "") or "unknown",
            "tokens": _js_round(estimate / estimate_total * total),
            "isLoaded": not is_deferred_tool(tool),
        })
    return total, details


def _active_agents(agent_definitions: Mapping[str, Any] | Iterable[Any]) -> list[Any]:
    if isinstance(agent_definitions, Mapping):
        return list(agent_definitions.values())
    return list(agent_definitions)


async def count_custom_agent_tokens(
    agent_definitions: Mapping[str, Any] | Iterable[Any], count_tokens: TokenCounter
) -> tuple[int, list[Agent]]:
    """
    `countCustomAgentTokens` -> `(agentTokens, agentDetails)`: each non-built-in agent's type and
    description (upstream's `whenToUse`), the text the Agent tool lists it by.
    """
    custom = [a for a in _active_agents(agent_definitions) if a.source != "built-in"]
    counts = await asyncio.gather(*(
        count_tokens_with_fallback([HumanMessage(content=" ".join([a.name, a.description or ""]))], [],
                                   count_tokens)
        for a in custom))
    details: list[Agent] = [
        {"agentType": a.name, "source": AGENT_SOURCES.get(a.source, a.source), "tokens": n or 0}
        for a, n in zip(custom, counts, strict=True)]
    return sum(d["tokens"] for d in details), details


# ── messages ────────────────────────────────────────────────────────────────

class MessageBreakdown:
    """`MessageBreakdown`: the running totals `approximateMessageTokens` fills."""

    def __init__(self) -> None:
        self.total_tokens = 0
        self.tool_call_tokens = 0
        self.tool_result_tokens = 0
        self.attachment_tokens = 0
        self.assistant_message_tokens = 0
        self.user_message_tokens = 0
        self.tool_calls_by_type: dict[str, int] = {}
        self.tool_results_by_type: dict[str, int] = {}
        self.attachments_by_type: dict[str, int] = {}


def _add(totals: dict[str, int], name: str, tokens: int) -> None:
    totals[name] = totals.get(name, 0) + tokens


def assistant_blocks(message: Message) -> list[dict[str, Any]]:
    """An `ai` message's content blocks, its tool calls among them as `tool_use` blocks (once each)."""
    content = message.content
    if isinstance(content, str):
        blocks: list[dict[str, Any]] = [{"type": "text", "text": content}] if content else []
    else:
        blocks = [b if isinstance(b, dict) else {"type": "text", "text": str(b)} for b in content]
    present = {b.get("id") for b in blocks if b.get("type") == "tool_use"}
    blocks += [{"type": "tool_use", "id": call.get("id"), "name": call.get("name"), "input": call.get("args")}
               for call in tool_calls_in(message) if call.get("id") not in present]
    return blocks


def _tool_result_block(message: Message) -> dict[str, Any]:
    block: dict[str, Any] = {
        "type": "tool_result", "tool_use_id": getattr(message, "tool_call_id", None), "content": message.content}
    if getattr(message, "status", None) == "error":
        block["is_error"] = True
    return block


def _attachment_of(message: Message) -> Mapping[str, Any] | None:
    attachment = message.additional_kwargs.get(ATTACHMENT_KEY)
    return attachment if isinstance(attachment, Mapping) else None


def process_assistant_message(message: Message, breakdown: MessageBreakdown) -> None:
    """`processAssistantMessage`: each block, a tool call by its tool's name."""
    for block in assistant_blocks(message):
        tokens = rough_token_count_estimation(json_stringify(block))
        if block.get("type") == "tool_use":
            breakdown.tool_call_tokens += tokens
            _add(breakdown.tool_calls_by_type, str(block.get("name") or "unknown"), tokens)
        else:
            breakdown.assistant_message_tokens += tokens


def process_user_message(message: Message, breakdown: MessageBreakdown, names: Mapping[str, str]) -> None:
    """
    `processUserMessage`: a string whole, else each block, a tool result by the name of the tool
    that asked for it. A `tool` message is a user message's `tool_result` block upstream.
    """
    if message.type == "tool":
        blocks: list[Any] = [_tool_result_block(message)]
    elif isinstance(message.content, str):
        breakdown.user_message_tokens += rough_token_count_estimation(message.content)
        return
    else:
        blocks = list(message.content)
    for block in blocks:
        tokens = rough_token_count_estimation(json_stringify(block))
        if isinstance(block, dict) and block.get("type") == "tool_result":
            breakdown.tool_result_tokens += tokens
            tool_use_id = block.get("tool_use_id")
            _add(breakdown.tool_results_by_type, (names.get(tool_use_id) if tool_use_id else None) or "unknown",
                 tokens)
        else:
            breakdown.user_message_tokens += tokens


def process_attachment(attachment: Mapping[str, Any], breakdown: MessageBreakdown) -> None:
    """`processAttachment`: the attachment record, by its type."""
    tokens = rough_token_count_estimation(json_stringify(attachment))
    breakdown.attachment_tokens += tokens
    _add(breakdown.attachments_by_type, str(attachment.get("type") or "unknown"), tokens)


def normalize_messages_for_api(messages: Sequence[Message]) -> list[Message]:
    """
    `normalizeMessagesForAPI`, as this build's request makes it (`claude.py::build_request_messages`):
    no `system` record, one assistant message per response, attachments after a batch's results.
    """
    sendable = [m for m in messages if m.type != "system"]
    return reorder_attachments_for_api(merge_split_assistant_messages(sendable))


async def approximate_message_tokens(messages: Sequence[Message], count_tokens: TokenCounter) -> MessageBreakdown:
    """
    `approximateMessageTokens`: the breakdown by kind (rough estimates), and the total the
    counter gives for the messages as the API receives them.
    """
    compacted = microcompact_messages(messages, is_main_thread=True).messages
    breakdown = MessageBreakdown()
    names: dict[str, str] = {}
    for message in compacted:
        if message.type == "ai":
            for block in assistant_blocks(message):
                if block.get("type") == "tool_use" and block.get("id"):
                    names[str(block["id"])] = str(block.get("name") or "unknown")
    for message in compacted:
        if message.type == "ai":
            process_assistant_message(message, breakdown)
        elif message.type in ("human", "tool"):
            attachment = _attachment_of(message)
            if attachment is not None:
                process_attachment(attachment, breakdown)
            else:
                process_user_message(message, breakdown, names)
    breakdown.total_tokens = await count_tokens_with_fallback(
        normalize_messages_for_api(compacted), [], count_tokens) or 0
    return breakdown


# ── the grid ────────────────────────────────────────────────────────────────

def _category_squares(category: ContextCategory, squares: int, context_window: int, total: int) -> list[GridSquare]:
    """`createCategorySquares`: full squares, the last one partly full when the share is not whole."""
    exact = category["tokens"] / context_window * total
    whole = math.floor(exact)
    fraction = exact - whole
    percentage = _js_round(category["tokens"] / context_window * 100)
    return [
        {"color": category["color"], "isFilled": True, "categoryName": category["name"],
         "tokens": category["tokens"], "percentage": percentage,
         "squareFullness": fraction if i == whole and fraction > 0 else 1.0}
        for i in range(squares)
    ]


def build_grid_rows(
    categories: Sequence[ContextCategory], context_window: int, terminal_width: int | None
) -> list[list[GridSquare]]:
    """
    `analyzeContextUsage`'s grid: 10x10 (20x10 for a 1M window), 5x5 (5x10) below 80 columns.
    Every non-deferred category takes at least one square, free space fills the middle, and the
    reserved buffer closes it.
    """
    narrow = terminal_width is not None and terminal_width != 0 and terminal_width < 80
    width = (5 if narrow else 20) if context_window >= 1_000_000 else (5 if narrow else 10)
    height = 10 if context_window >= 1_000_000 else (5 if narrow else 10)
    total = width * height

    def squares(cat: ContextCategory) -> int:
        share = _js_round(cat["tokens"] / context_window * total)
        return share if cat["name"] == FREE_SPACE_NAME else max(1, share)

    shown = [c for c in categories if not c.get("isDeferred")]
    reserved_names = (RESERVED_CATEGORY_NAME, MANUAL_COMPACT_BUFFER_NAME)
    reserved = next((c for c in shown if c["name"] in reserved_names), None)
    grid: list[GridSquare] = []
    for cat in shown:
        if cat["name"] in reserved_names or cat["name"] == FREE_SPACE_NAME:
            continue
        for square in _category_squares(cat, squares(cat), context_window, total):
            if len(grid) < total:
                grid.append(square)
    free = next((c for c in categories if c["name"] == FREE_SPACE_NAME), None)
    free_target = total - (squares(reserved) if reserved is not None else 0)
    while len(grid) < free_target:
        grid.append({
            "color": "promptBorder", "isFilled": True, "categoryName": FREE_SPACE_NAME,
            "tokens": free["tokens"] if free else 0,
            "percentage": _js_round(free["tokens"] / context_window * 100) if free else 0,
            "squareFullness": 1.0,
        })
    if reserved is not None:
        for square in _category_squares(reserved, squares(reserved), context_window, total):
            if len(grid) < total:
                grid.append(square)
    return [grid[i * width:(i + 1) * width] for i in range(height)]


def _format_message_breakdown(breakdown: MessageBreakdown) -> MessageBreakdownData:
    """The message breakdown as upstream returns it: tools by calls + results, attachments by size."""
    tools: dict[str, dict[str, int]] = {}
    for name, tokens in breakdown.tool_calls_by_type.items():
        tools[name] = {**tools.get(name, {"callTokens": 0, "resultTokens": 0}), "callTokens": tokens}
    for name, tokens in breakdown.tool_results_by_type.items():
        tools[name] = {**tools.get(name, {"callTokens": 0, "resultTokens": 0}), "resultTokens": tokens}
    by_tool: list[ToolCallsByType] = sorted(
        ({"name": name, "callTokens": t["callTokens"], "resultTokens": t["resultTokens"]} for name, t in tools.items()),
        key=lambda t: -(t["callTokens"] + t["resultTokens"]))
    attachments: list[AttachmentsByType] = sorted(
        ({"name": name, "tokens": tokens} for name, tokens in breakdown.attachments_by_type.items()),
        key=lambda a: -a["tokens"])
    return {
        "toolCallTokens": breakdown.tool_call_tokens,
        "toolResultTokens": breakdown.tool_result_tokens,
        "attachmentTokens": breakdown.attachment_tokens,
        "assistantMessageTokens": breakdown.assistant_message_tokens,
        "userMessageTokens": breakdown.user_message_tokens,
        "toolCallsByType": by_tool,
        "attachmentsByType": attachments,
    }


# ── the analysis ────────────────────────────────────────────────────────────

def _effective_system_prompt(
    tool_context: Any, tools: Sequence[Tool], custom_system_prompt: str | None, append_system_prompt: str | None
) -> list[str]:
    """`getSystemPrompt` + `buildEffectiveSystemPrompt`, from the run's section cache."""
    agent_prompt = tool_context.agent_system_prompt
    default: list[str] = []
    if agent_prompt is None and not custom_system_prompt:
        default = resolve_default_system_prompt(tool_context, tool_context.run_id, tools)
    from app.context import tdd_contract_section

    return build_effective_system_prompt(
        agent_system_prompt=agent_prompt, custom_system_prompt=custom_system_prompt,
        default_system_prompt=default, append_system_prompt=append_system_prompt,
        tdd_contract=tdd_contract_section() if custom_system_prompt else None)


async def analyze_context_usage(
    messages: Sequence[Message],
    model: str,
    tools: Sequence[Tool],
    agent_definitions: Mapping[str, Any] | Iterable[Any],
    tool_context: Any,
    *,
    terminal_width: int | None = None,
    custom_system_prompt: str | None = None,
    append_system_prompt: str | None = None,
    original_messages: Sequence[Message] | None = None,
    count_tokens: TokenCounter = rough_count_tokens,
) -> ContextData:
    """
    `analyzeContextUsage`: the window, by category, in upstream's order. `tool_context` is the
    run's main-thread `ToolContext`: its workspace, `run_id` (the memoized prompt sections and
    system context), MCP clients, skill registry and agent prompt stand for upstream's globals.
    `original_messages` (before microcompact) give the API usage, as the status line reads it.
    """
    context_window = get_context_window_for_model(model)
    effective = _effective_system_prompt(tool_context, tools, custom_system_prompt, append_system_prompt)
    system_context = get_session_context(tool_context.run_id, tool_context.workspace).system_context
    skill_registry = tool_context.skill_registry

    # Upstream's one `Promise.all` of seven; `asyncio.gather` is typed up to six arguments.
    messages_task = asyncio.ensure_future(approximate_message_tokens(messages, count_tokens))
    (
        (system_prompt_tokens, _sections),
        (claude_md_tokens, memory_files),
        built_in_tool_tokens,
        (mcp_tool_tokens, mcp_tools),
        (agent_tokens, agents),
        (slash_command_tokens, total_commands),
    ) = await asyncio.gather(
        count_system_tokens(effective, system_context, count_tokens),
        count_memory_file_tokens(tool_context.workspace, count_tokens),
        count_built_in_tool_tokens(tools, count_tokens),
        count_mcp_tool_tokens(tools, count_tokens),
        count_custom_agent_tokens(agent_definitions, count_tokens),
        count_slash_command_tokens(tools, skill_registry, count_tokens),
    )
    breakdown = await messages_task
    skill_frontmatter = count_skill_tokens(tools, skill_registry)
    skill_frontmatter_tokens = sum(s["tokens"] for s in skill_frontmatter)
    message_tokens = breakdown.total_tokens

    auto_compact = is_auto_compact_enabled()
    threshold = get_effective_context_window_size(model) - AUTOCOMPACT_BUFFER_TOKENS if auto_compact else None

    cats: list[ContextCategory] = []
    if system_prompt_tokens > 0:
        cats.append({"name": "System prompt", "tokens": system_prompt_tokens, "color": "promptBorder"})
    system_tools_tokens = built_in_tool_tokens - skill_frontmatter_tokens
    if system_tools_tokens > 0:
        cats.append({"name": "System tools", "tokens": system_tools_tokens, "color": "inactive"})
    if mcp_tool_tokens > 0:
        cats.append({"name": "MCP tools", "tokens": mcp_tool_tokens, "color": "cyan_FOR_SUBAGENTS_ONLY"})
    if agent_tokens > 0:
        cats.append({"name": "Custom agents", "tokens": agent_tokens, "color": "permission"})
    if claude_md_tokens > 0:
        cats.append({"name": "Memory files", "tokens": claude_md_tokens, "color": "claude"})
    if skill_frontmatter_tokens > 0:
        cats.append({"name": "Skills", "tokens": skill_frontmatter_tokens, "color": "warning"})
    if message_tokens > 0:
        cats.append({"name": "Messages", "tokens": message_tokens, "color": "purple_FOR_SUBAGENTS_ONLY"})

    actual_usage = sum(0 if c.get("isDeferred") else c["tokens"] for c in cats)
    if threshold is not None:
        reserved_tokens = context_window - threshold
        cats.append({"name": RESERVED_CATEGORY_NAME, "tokens": reserved_tokens, "color": "inactive"})
    else:
        reserved_tokens = MANUAL_COMPACT_BUFFER_TOKENS
        cats.append({"name": MANUAL_COMPACT_BUFFER_NAME, "tokens": reserved_tokens, "color": "inactive"})
    free_tokens = max(0, context_window - actual_usage - reserved_tokens)
    cats.append({"name": FREE_SPACE_NAME, "tokens": free_tokens, "color": "promptBorder"})

    usage = get_current_usage(original_messages if original_messages is not None else messages)
    api_usage: ApiUsage | None = None
    total_tokens = actual_usage
    if usage is not None:
        api_usage = {
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "cache_creation_input_tokens": usage["cache_creation_input_tokens"],
            "cache_read_input_tokens": usage["cache_read_input_tokens"],
        }
        total_tokens = (usage["input_tokens"] + usage["cache_creation_input_tokens"]
                        + usage["cache_read_input_tokens"])

    data: ContextData = {
        "categories": cats,
        "totalTokens": total_tokens,
        "maxTokens": context_window,
        "rawMaxTokens": context_window,
        "percentage": _js_round(total_tokens / context_window * 100),
        "gridRows": build_grid_rows(cats, context_window, terminal_width),
        "model": model,
        "memoryFiles": memory_files,
        "mcpTools": mcp_tools,
        "agents": agents,
        "isAutoCompactEnabled": auto_compact,
        "messageBreakdown": _format_message_breakdown(breakdown),
        "apiUsage": api_usage,
    }
    if slash_command_tokens > 0:
        data["slashCommands"] = {
            "totalCommands": total_commands, "includedCommands": total_commands, "tokens": slash_command_tokens}
    if skill_frontmatter_tokens > 0:
        data["skills"] = {
            "totalSkills": len(skill_frontmatter), "includedSkills": len(skill_frontmatter),
            "tokens": skill_frontmatter_tokens, "skillFrontmatter": skill_frontmatter}
    if threshold is not None:
        data["autoCompactThreshold"] = threshold
    return data


__all__ = [
    "MANUAL_COMPACT_BUFFER_NAME",
    "RESERVED_CATEGORY_NAME",
    "TOOL_TOKEN_COUNT_OVERHEAD",
    "ContextCategory",
    "ContextData",
    "GridSquare",
    "TokenCounter",
    "analyze_context_usage",
    "approximate_message_tokens",
    "build_grid_rows",
    "count_custom_agent_tokens",
    "count_mcp_tool_tokens",
    "count_memory_file_tokens",
    "count_system_tokens",
    "count_tokens_with_fallback",
    "count_tool_definition_tokens",
    "extract_section_name",
    "rough_count_tokens",
]
