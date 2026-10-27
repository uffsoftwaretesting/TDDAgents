"""
Running one agent hook: a small verifier loop that must answer through `StructuredOutput`.

Ported from:
- `reference/claude-code/src/utils/hooks/execAgentHook.ts` -> `execAgentHook`
  (`MAX_AGENT_TURNS = 50`, the 60 s default timeout, the verifier system prompt, the
  `dontAsk` + `Read(/<transcript>)` session rule)
- `reference/claude-code/src/utils/hooks/hookHelpers.ts` -> `createStructuredOutputTool`,
  `registerStructuredOutputEnforcement`
- `reference/claude-code/src/tools/SyntheticOutputTool/SyntheticOutputTool.ts` ->
  `SYNTHETIC_OUTPUT_TOOL_NAME`, `SyntheticOutputTool`

Outcomes, as upstream:

* `ok: true` -> `success` with an empty `hook_success` attachment;
* `ok: false` -> `blocking` ("Agent hook condition was not met: ..."), with **no**
  `prevent_continuation` (unlike a prompt hook);
* no structured output — the agent stopped without calling the tool, or hit
  `MAX_AGENT_TURNS` — -> `cancelled`, silently;
* aborted or timed out (the hook's `timeout` seconds, else 60 s) -> `cancelled`;
* any other error -> `non_blocking_error` ("Error executing agent hook: ...").

`MAX_AGENT_TURNS` is upstream's own ceiling, ported with its constant: it counts assistant
messages, which here, as upstream, are one per content block.

The loop is reached through `AgentHookRunner`, so tests drive it with a fake. The production
runner (`build_agent_hook_runner`) drives `run_loop` with:

* the verifier system prompt and no user/system context, as upstream's `query()` call;
* **the TDD stop hook does not apply.** The verifier is not a TDD worker and has no cycle to
  complete; its only Stop hook is upstream's structured-output enforcement ("You MUST call
  the StructuredOutput tool ...");
* `dontAsk` mode plus a session allow rule for reading the transcript, as upstream.

The verifier's pool is upstream's: the parent's tools (already the parent's phase-filtered
pool) minus `HOOK_AGENT_DISALLOWED_TOOLS`, plus `StructuredOutput`. Its context reads the
**parent's** app state — the phase ledger and permission context — with only upstream's two
overrides (`dontAsk`, the transcript read rule), so the TDD phase tier and the parent's deny
rules hold for every verifier call: in RED a verifier cannot write a production path.

*Divergence (TDD, the one):* upstream's verifier shares the parent's `setAppState`; here its
app-state writes are **discarded** (`discard_app_state_update`), so a verifier's `RunTests`
can never move the parent's phase ledger — only the TDD workers' `RunTests` may (barrier 1).
Upstream's `setResponseLength` spinner and analytics have no equivalent.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.schemas.hooks import AgentHook
from app.types.hooks import HookResult
from app.utils.abort_controller import AbortController
from app.utils.hooks.exec_prompt_hook import (
    add_arguments_to_prompt,
    get_small_fast_model,
    parse_hook_response,
)
from app.utils.messages import HookAttachment, HookBlockingError, Message
from app.utils.shell_command import ABORT_POLL_SECONDS, is_aborted

logger = logging.getLogger("TDDOrchestrator.Hooks")

#: `execAgentHook`: `hook.timeout ? hook.timeout * 1000 : 60000`.
DEFAULT_AGENT_HOOK_TIMEOUT_MS = 60000
#: `execAgentHook`: `const MAX_AGENT_TURNS = 50` — upstream's ceiling, kept.
MAX_AGENT_TURNS = 50
#: `SYNTHETIC_OUTPUT_TOOL_NAME`.
SYNTHETIC_OUTPUT_TOOL_NAME = "StructuredOutput"
#: `ALL_AGENT_DISALLOWED_TOOLS` (`src/constants/tools.ts`) as the external build has it,
#: mapped onto this repo's names: this repo's set (`tools/agent_tool/agent_tool_utils.py`) plus `Agent` and
#: its legacy alias `Task`, which upstream disallows for every non-ant user.
HOOK_AGENT_DISALLOWED_TOOLS: frozenset[str] = frozenset({"Agent", "Task"})

STRUCTURED_OUTPUT_PROMPT = (
    "Use this tool to return your verification result. You MUST call this tool exactly once "
    "at the end of your response."
)
STRUCTURED_OUTPUT_ENFORCEMENT_MESSAGE = (
    f"You MUST call the {SYNTHETIC_OUTPUT_TOOL_NAME} tool to complete this request. Call this tool now."
)
#: `createStructuredOutputTool`'s `inputJSONSchema`.
STRUCTURED_OUTPUT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean", "description": "Whether the condition was met"},
        "reason": {"type": "string", "description": "Reason, if the condition was not met"},
    },
    "required": ["ok"],
    "additionalProperties": False,
}


def agent_hook_system_prompt(transcript_path: str) -> str:
    """The verifier system prompt, product name renamed."""
    return (
        "You are verifying a stop condition in TDDAgents. Your task is to verify that the agent "
        "completed the given plan. The conversation transcript is available at: "
        f"{transcript_path}\nYou can read this file to analyze the conversation history if needed.\n\n"
        "Use the available tools to inspect the codebase and verify the condition.\n"
        "Use as few steps as possible - be efficient and direct.\n\n"
        f"When done, return your result using the {SYNTHETIC_OUTPUT_TOOL_NAME} tool with:\n"
        "- ok: true if the condition is met\n"
        "- ok: false with reason if the condition is not met"
    )


@dataclass(frozen=True, slots=True)
class AgentHookRequest:
    """Everything the verifier loop needs."""

    prompt: str
    system_prompt: str
    model: str
    tool_context: Any
    transcript_path: str
    abort: AbortController
    max_turns: int = MAX_AGENT_TURNS


@dataclass(frozen=True, slots=True)
class AgentHookRunOutcome:
    """What the verifier loop produced: the structured output, if any, and how it ended."""

    structured_output: Mapping[str, Any] | None = None
    hit_max_turns: bool = False
    turn_count: int = 0


class AgentHookRunner(Protocol):
    def __call__(self, request: AgentHookRequest) -> Awaitable[AgentHookRunOutcome]: ...


def _non_blocking(hook: AgentHook, hook_name: str, tool_use_id: str, hook_event: str, stderr: str) -> HookResult:
    return HookResult(
        hook=hook,
        outcome="non_blocking_error",
        attachment=HookAttachment(
            type="hook_non_blocking_error",
            hook_name=hook_name,
            tool_use_id=tool_use_id,
            hook_event=hook_event,
            stderr=stderr,
            stdout="",
            exit_code=1,
        ),
    )


async def exec_agent_hook(
    hook: AgentHook,
    hook_name: str,
    hook_event: str,
    json_input: str,
    signal: Any,
    tool_context: Any,
    tool_use_id: str,
    messages: Sequence[Message] | None,
    agent_name: str | None,
    *,
    agent_runner: AgentHookRunner,
    transcript_path: str = "",
    default_model: str | None = None,
) -> HookResult:
    """`execAgentHook`. `messages` is unused, kept for signature parity, as upstream."""
    effective_id = tool_use_id or f"hook-{uuid.uuid4()}"
    started = time.monotonic()
    try:
        processed = add_arguments_to_prompt(hook.prompt, json_input)
        timeout_ms = int(hook.timeout * 1000) if hook.timeout else DEFAULT_AGENT_HOOK_TIMEOUT_MS
        controller = AbortController()
        request = AgentHookRequest(
            prompt=processed,
            system_prompt=agent_hook_system_prompt(transcript_path),
            model=hook.model or get_small_fast_model(default_model),
            tool_context=tool_context,
            transcript_path=transcript_path,
            abort=controller,
        )
        if is_aborted(signal):
            return HookResult(hook=hook, outcome="cancelled")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_ms / 1000
        task = asyncio.ensure_future(agent_runner(request))
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0 or is_aborted(signal):
                    controller.abort("hook_timeout_or_parent_abort")
                    return HookResult(hook=hook, outcome="cancelled")
                done, _ = await asyncio.wait({task}, timeout=min(ABORT_POLL_SECONDS, remaining))
                if done:
                    break
            try:
                outcome = task.result()
            except Exception:
                if controller.signal.aborted:
                    return HookResult(hook=hook, outcome="cancelled")
                raise
        finally:
            if not task.done():
                task.cancel()

        parsed = None
        if outcome.structured_output is not None:
            parsed, _ = parse_hook_response(outcome.structured_output)
        if parsed is None:
            if outcome.hit_max_turns:
                logger.debug("Hooks: Agent hook did not complete within %d turns", MAX_AGENT_TURNS)
            else:
                logger.debug("Hooks: Agent hook did not return structured output (%s)", agent_name)
            return HookResult(hook=hook, outcome="cancelled")

        if not parsed["ok"]:
            return HookResult(
                hook=hook,
                outcome="blocking",
                blocking_error=HookBlockingError(
                    blocking_error=f"Agent hook condition was not met: {parsed.get('reason')}",
                    command=hook.prompt,
                ),
            )
        logger.debug("Hooks: Agent hook condition was met in %.2fs", time.monotonic() - started)
        return HookResult(
            hook=hook,
            outcome="success",
            attachment=HookAttachment(
                type="hook_success",
                hook_name=hook_name,
                tool_use_id=effective_id,
                hook_event=hook_event,
                content="",
            ),
        )
    except Exception as exc:  # noqa: BLE001 - upstream reports every error as non-blocking
        return _non_blocking(hook, hook_name, effective_id, hook_event, f"Error executing agent hook: {exc}")


# ── production runner ────────────────────────────────────────────────────────


@dataclass
class StructuredOutputHolder:
    """Where `StructuredOutput` puts what the model returned (upstream's attachment)."""

    data: Mapping[str, Any] | None = None
    calls: list[Mapping[str, Any]] = field(default_factory=list)


def create_structured_output_tool(holder: StructuredOutputHolder) -> Any:
    """`createStructuredOutputTool`: `SyntheticOutputTool` with the hook response schema."""
    from app.tool import PermissionResult, ToolResult, build_tool

    def call(args: dict[str, Any], context: Any) -> ToolResult:
        holder.calls.append(dict(args))
        holder.data = dict(args)
        return ToolResult(content="Structured output provided successfully")

    return build_tool(
        name=SYNTHETIC_OUTPUT_TOOL_NAME,
        prompt=STRUCTURED_OUTPUT_PROMPT,
        call=call,
        input_schema=STRUCTURED_OUTPUT_INPUT_SCHEMA,
        description=lambda args: "Return structured output in the requested format",
        is_concurrency_safe=lambda args: True,
        is_read_only=lambda args: True,
        check_permissions=lambda args, ctx: PermissionResult(behavior="allow", updated_input=args),
        max_result_size_chars=100_000,
    )


def disallowed_hook_agent_tools() -> frozenset[str]:
    """Every tool name a verifier may not use (`ALL_AGENT_DISALLOWED_TOOLS`, mapped)."""
    from app.tools.agent_tool.agent_tool_utils import ALL_AGENT_DISALLOWED_TOOLS

    return frozenset(ALL_AGENT_DISALLOWED_TOOLS) | HOOK_AGENT_DISALLOWED_TOOLS


def hook_agent_tools(parent_tools: Sequence[Any], structured_output_tool: Any) -> tuple[Any, ...]:
    """
    `execAgentHook`'s pool: the parent's tools minus the disallowed set and any existing
    `StructuredOutput` (a duplicate with another schema), then ours.
    """
    disallowed = disallowed_hook_agent_tools()
    kept = [
        tool
        for tool in parent_tools
        if tool.name not in disallowed and tool.name != SYNTHETIC_OUTPUT_TOOL_NAME
    ]
    return (*kept, structured_output_tool)


def hook_agent_app_state(parent_get_app_state: Callable[[], Any], transcript_path: str) -> Callable[[], Any]:
    """
    Upstream's `getAppState` override: the **parent's** state (ledger, rules) read live, with
    `dontAsk` and `Read(/<transcript>)` as a session rule on top.
    """
    from app.types.permissions import (
        PermissionBehavior,
        PermissionMode,
        PermissionRule,
        PermissionRuleSource,
    )

    def get_app_state() -> Any:
        state = parent_get_app_state()
        perms = state.tool_permission_context
        rule = PermissionRule(
            tool_name="Read",
            rule_behavior=PermissionBehavior.ALLOW,
            rule_content=f"/{transcript_path}",
            source=PermissionRuleSource.SESSION,
        )
        return replace(
            state,
            tool_permission_context=replace(
                perms, mode=PermissionMode.DONT_ASK, always_allow_rules=(*perms.always_allow_rules, rule)
            ),
        )

    return get_app_state


def structured_output_enforcement(holder: StructuredOutputHolder) -> Callable[[Any, Any], Awaitable[Any]]:
    """`registerStructuredOutputEnforcement`: the verifier may not stop without the tool."""
    from app.query.stop_hooks import StopHookResult

    async def stop_hook(state: Any, config: Any) -> StopHookResult:
        if holder.data is not None:
            return StopHookResult()
        return StopHookResult(blocking_errors=(HumanMessage(content=STRUCTURED_OUTPUT_ENFORCEMENT_MESSAGE),))

    return stop_hook


CallModelFactory = Callable[[str], Callable[[Any, Any], AsyncIterator[Message]]]


def _live_call_model(system_prompt: str) -> Callable[[Any, Any], AsyncIterator[Message]]:
    """The verifier's model call: its own system prompt, the context's tools, one stream."""

    async def call(state: Any, config: Any) -> AsyncIterator[Message]:  # pragma: no cover - live provider
        from app.config.config import Config
        from app.services.api.claude import (
            convert_tool_to_langchain,
            frame_content_blocks,
        )
        from app.utils.chat_model_factory import get_chat_model

        chat = get_chat_model(Config.CHAT_MODEL, model=config.model)
        tools = [convert_tool_to_langchain(t) for t in state.tool_context.tools]
        if tools:
            chat = chat.bind_tools(tools)
        stream = chat.astream([SystemMessage(content=system_prompt), *state.messages])
        async for message in frame_content_blocks(stream):
            yield message

    return call


def build_agent_hook_runner(call_model_factory: CallModelFactory | None = None) -> AgentHookRunner:
    """
    The production runner: `query()` on `run_loop`. `call_model_factory` maps the system
    prompt to a `call_model`; it defaults to the live provider and is what tests replace.
    """
    factory = call_model_factory or _live_call_model

    async def run(request: AgentHookRequest) -> AgentHookRunOutcome:
        from app.query.config import build_run_config
        from app.query.deps import LoopDeps
        from app.query.query import run_loop
        from app.query.state import CompactionResult, initial_loop_state
        from app.services.tools.tool_orchestration import run_tools
        from app.state.app_state_store import discard_app_state_update
        from app.tool import CancelToken

        holder = StructuredOutputHolder()
        parent = request.tool_context
        cancel = CancelToken()
        request.abort.signal.add_listener(lambda reason: cancel.cancel())
        first = HumanMessage(content=request.prompt)
        hook_agent_id = f"hook-agent-{uuid.uuid4()}"
        context = replace(
            parent,
            cancel=cancel,
            get_app_state=hook_agent_app_state(parent.get_app_state, request.transcript_path),
            set_app_state=discard_app_state_update,
            messages=(first,),
            in_flight_tool_ids=set(),
            tools=hook_agent_tools(getattr(parent, "tools", ()), create_structured_output_tool(holder)),
            permission_context=None,
            refresh_tools=None,
            can_use_tool=None,
            agent_id=hook_agent_id,
        )
        state = initial_loop_state((first,), context)
        config = replace(build_run_config(hook_agent_id, postgres_checkpointing=False), model=request.model)

        async def compact(s: Any, c: Any) -> CompactionResult:
            return CompactionResult(compacted=False, messages=s.messages, tracking=s.compaction_tracking)

        deps = LoopDeps(
            call_model=factory(request.system_prompt),
            run_tools=run_tools,
            compact=compact,
            stop_hooks=structured_output_enforcement(holder),
            uuid=lambda: str(uuid.uuid4()),
            now=time.time,
            emit_event=lambda event: None,
        )

        turns = 0
        hit_max = False
        events = run_loop(state, config, deps)
        try:
            async for event in events:
                if isinstance(event, AIMessage):
                    turns += 1
                    if turns >= request.max_turns:
                        hit_max = True
                        request.abort.abort("max_turns")
                        break
                if holder.data is not None:
                    request.abort.abort("structured_output")
                    break
        finally:
            # `run_loop` is an async generator; closing it runs its cleanup (history repair).
            close = getattr(events, "aclose", None)
            if close is not None:
                await close()
        return AgentHookRunOutcome(structured_output=holder.data, hit_max_turns=hit_max, turn_count=turns)

    return run


__all__ = [
    "DEFAULT_AGENT_HOOK_TIMEOUT_MS",
    "HOOK_AGENT_DISALLOWED_TOOLS",
    "MAX_AGENT_TURNS",
    "STRUCTURED_OUTPUT_ENFORCEMENT_MESSAGE",
    "STRUCTURED_OUTPUT_INPUT_SCHEMA",
    "SYNTHETIC_OUTPUT_TOOL_NAME",
    "AgentHookRequest",
    "AgentHookRunOutcome",
    "AgentHookRunner",
    "StructuredOutputHolder",
    "agent_hook_system_prompt",
    "build_agent_hook_runner",
    "create_structured_output_tool",
    "disallowed_hook_agent_tools",
    "exec_agent_hook",
    "hook_agent_app_state",
    "hook_agent_tools",
    "structured_output_enforcement",
]
