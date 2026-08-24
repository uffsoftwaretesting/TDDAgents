"""
The Tool protocol, its fail-closed factory, and the single choke point every tool call
passes through.

Ported from claude-code's `Tool.ts`. Three details are taken from the real type rather
than from a paraphrase, because each one changes an implementation decision:

* **`is_read_only` takes the input.** `Bash("ls")` is read-only and parallelizable while
  `Bash("rm -rf")` is neither; only a per-input predicate can express that, and
  `is_concurrency_safe` then just delegates to it.
* **`max_result_chars` is per tool.** ReadFile unbounded, Grep 20 000, Bash 30 000,
  everything else 100 000. ReadFile is unbounded on purpose: persisting its output would
  create a circular ReadFile -> file -> ReadFile loop, and it already self-bounds.
* **`description` and `prompt` are two different things** — a short input-aware line for
  logging, and the long text the model reads. Collapsing them loses compact call logging.

`execute_tool` below is the *only* supported way to invoke a tool. Calling `tool.call`
directly bypasses validation, both hook events, both enforcement gates, result governance
and the sync checkpoint — which is the whole reason the pipeline exists in one function.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Generic, Literal, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from app.config.config import Config
from app.hooks.dispatcher import HookDispatcher, HookEvent
from app.workspace.base import Workspace, WorkspaceError, WorkspaceKind

logger = logging.getLogger("TDDOrchestrator.Tools")

ArgsT = TypeVar("ArgsT", bound=BaseModel)

# ReadFile's limit. A real int rather than math.inf so the field stays typed `int`; no
# tool result will ever approach it, and comparing against a float would infect the
# arithmetic in _govern_size.
UNBOUNDED_RESULT_CHARS = 2**62

DEFAULT_MAX_RESULT_CHARS = 100_000
GREP_MAX_RESULT_CHARS = 20_000
BASH_MAX_RESULT_CHARS = 30_000

# Head/tail kept when a result is persisted rather than returned whole.
PREVIEW_HEAD_CHARS = 2_000
PREVIEW_TAIL_CHARS = 1_000


class Capability(StrEnum):
    """
    What a call needs permission to do.

    This is the axis `permission_mode` gates. It is deliberately *not* the same question
    as `is_read_only`: that one asks "is this safe to run twice in parallel", which a
    concurrency scheduler needs and a permission check does not.
    """

    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"


PermissionMode = Literal["read_only", "workspace_write", "full"]

#: Which capabilities each mode admits. read_only < workspace_write < full.
CAPABILITIES_BY_MODE: dict[PermissionMode, frozenset[Capability]] = {
    "read_only": frozenset({Capability.READ}),
    "workspace_write": frozenset({Capability.READ, Capability.WRITE}),
    "full": frozenset({Capability.READ, Capability.WRITE, Capability.EXECUTE}),
}

WorkspaceSpec = Literal["sandbox", "local", "both"]


# ─────────────────────────────────────────────────────────────────────────────
# Result types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ValidationResult:
    """The outcome of a tool's own input check, beyond what the schema can express."""

    ok: bool = True
    message: str = ""

    @classmethod
    def invalid(cls, message: str) -> "ValidationResult":
        return cls(ok=False, message=message)


@dataclass(frozen=True)
class PermissionResult:
    """
    The outcome of a tool's own permission check.

    `updated_input` lets a tool normalize its arguments as part of allowing the call,
    mirroring the `{behavior: 'allow', updatedInput}` shape in the original.
    """

    behavior: Literal["allow", "deny"] = "allow"
    message: str = ""
    updated_input: BaseModel | None = None

    @classmethod
    def deny(cls, message: str) -> "PermissionResult":
        return cls(behavior="deny", message=message)


@dataclass
class ToolResult:
    """
    What comes back from `execute_tool`, in every case including failure.

    A tool that fails returns `is_error=True` with the reason in `content`; it does not
    raise. That is what lets one bad call in a concurrent batch leave its siblings alone,
    and what lets a non-zero `exit_code` be something the model reasons about rather than
    infrastructure failure that burns retries.
    """

    content: str
    tool_name: str = ""
    is_error: bool = False
    exit_code: int | None = None
    truncated: bool = False
    persisted_path: str | None = None
    additional_context: str = ""
    hook_stopped_continuation: bool = False
    call_index: int = 0
    call_id: str = ""

    def rendered(self) -> str:
        """The full text handed back to the model, hook feedback included."""
        if not self.additional_context:
            return self.content
        return f"{self.content}\n\n<hook-feedback>\n{self.additional_context}\n</hook-feedback>"


# ─────────────────────────────────────────────────────────────────────────────
# Context
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CancelToken:
    """
    Cooperative cancellation, checked between batches by the executor.

    Deliberately not a thread-kill: a tool mid-flight against the sandbox has already paid
    for its round trip, and abandoning it would leave the ledger describing a workspace
    that no longer matches.
    """

    cancelled: bool = False

    def cancel(self) -> None:
        self.cancelled = True


@dataclass
class ToolContext:
    """
    Everything a tool is allowed to reach. A trimmed port of `ToolUseContext`.

    `workspace` is already resolved by `app/workspace/router.py`; `workspace_spec` is the
    declaration it was resolved from, and is what the workspace gate checks. Keeping both
    is what lets a tool be rejected for asking for the host even when the agent happens to
    have been handed a DualWorkspace.
    """

    workspace: Workspace
    workspace_spec: WorkspaceSpec = "sandbox"
    permission_mode: PermissionMode = "read_only"
    sandbox_id: str | None = None
    agent_id: str = ""
    agent_type: str = ""
    session_id: str = ""
    workdir: str = "."
    turns_remaining: int = 0
    ledger: dict[str, str] = field(default_factory=dict)
    sync_engine: Any = None
    hook_dispatcher: HookDispatcher | None = None
    cancel_token: CancelToken = field(default_factory=CancelToken)
    todos: list[dict[str, str]] = field(default_factory=list)
    background_commands: dict[str, Any] = field(default_factory=dict)
    _result_counter: int = 0
    _counter_lock: threading.Lock = field(default_factory=threading.Lock)

    def next_result_id(self) -> int:
        """
        Locked because a batch of concurrency-safe tools shares one context, and two of
        them can persist an oversized result at the same time — Grep is both read-only and
        capable of a 20 000-char overflow. Without the lock they would race for a filename
        and one result would silently overwrite the other.
        """
        with self._counter_lock:
            self._result_counter += 1
            return self._result_counter


# ─────────────────────────────────────────────────────────────────────────────
# The protocol
# ─────────────────────────────────────────────────────────────────────────────

class Tool(Protocol[ArgsT]):
    """The capability surface a tool exposes. `BuiltTool` is the only implementation."""

    name: str
    args_schema: type[ArgsT]
    max_result_chars: int

    def description(self, args: ArgsT) -> str: ...
    def prompt(self) -> str: ...
    def call(self, args: ArgsT, ctx: ToolContext) -> ToolResult: ...
    def is_enabled(self) -> bool: ...
    def is_read_only(self, args: ArgsT) -> bool: ...
    def is_concurrency_safe(self, args: ArgsT) -> bool: ...
    def is_destructive(self, args: ArgsT) -> bool: ...
    def required_capability(self, args: ArgsT) -> Capability: ...
    def required_workspace(self, args: ArgsT) -> WorkspaceKind | None: ...
    def validate_input(self, args: ArgsT, ctx: ToolContext) -> ValidationResult: ...
    def check_permissions(self, args: ArgsT, ctx: ToolContext) -> PermissionResult: ...


# ─────────────────────────────────────────────────────────────────────────────
# The factory
# ─────────────────────────────────────────────────────────────────────────────

#: Fail-closed defaults for every commonly-stubbed member, so an omitted method is always
#: the safe answer: not read-only, not parallelizable, not destructive, needs write
#: permission. A tool that forgets to declare itself read-only loses parallelism; one that
#: forgot the other way round would escape the permission gate.
TOOL_DEFAULTS: dict[str, Any] = {
    "max_result_chars": DEFAULT_MAX_RESULT_CHARS,
    "is_enabled": lambda: True,
    "is_read_only": lambda args: False,
    "is_concurrency_safe": lambda args: False,
    "is_destructive": lambda args: False,
    "required_capability": lambda args: Capability.WRITE,
    "required_workspace": lambda args: None,
    "validate_input": lambda args, ctx: ValidationResult(),
    "check_permissions": lambda args, ctx: PermissionResult(),
}


@dataclass
class BuiltTool(Generic[ArgsT]):
    """
    A tool assembled from a partial definition plus `TOOL_DEFAULTS`.

    The members are callable *attributes* rather than methods, which is what lets
    `build_tool` fill in the missing ones. Structurally this still satisfies `Tool`.
    """

    name: str
    args_schema: type[ArgsT]
    prompt_text: str
    call: Callable[[ArgsT, ToolContext], ToolResult]
    description: Callable[[ArgsT], str]
    max_result_chars: int
    is_enabled: Callable[[], bool]
    is_read_only: Callable[[ArgsT], bool]
    is_concurrency_safe: Callable[[ArgsT], bool]
    is_destructive: Callable[[ArgsT], bool]
    required_capability: Callable[[ArgsT], Capability]
    required_workspace: Callable[[ArgsT], WorkspaceKind | None]
    validate_input: Callable[[ArgsT, ToolContext], ValidationResult]
    check_permissions: Callable[[ArgsT, ToolContext], PermissionResult]
    user_facing_name: str = ""

    def prompt(self) -> str:
        return self.prompt_text


AnyTool = BuiltTool[Any]


def build_tool(
    *,
    name: str,
    args_schema: type[ArgsT],
    prompt: str,
    call: Callable[[ArgsT, ToolContext], ToolResult],
    description: Callable[[ArgsT], str] | None = None,
    **overrides: Any,
) -> BuiltTool[ArgsT]:
    """
    Builds a complete tool from the parts that actually differ between tools.

    Everything omitted comes from `TOOL_DEFAULTS`, so defaults live in exactly one place
    and no call site needs a `getattr(tool, 'x', default)` dance.
    """
    unknown = set(overrides) - set(TOOL_DEFAULTS)
    if unknown:
        raise TypeError(f"build_tool got unexpected argument(s): {', '.join(sorted(unknown))}")

    resolved = {**TOOL_DEFAULTS, **overrides}
    return BuiltTool(
        name=name,
        args_schema=args_schema,
        prompt_text=prompt,
        call=call,
        description=description if description is not None else (lambda args: name),
        user_facing_name=name,
        **resolved,
    )


# ─────────────────────────────────────────────────────────────────────────────
# The choke point
# ─────────────────────────────────────────────────────────────────────────────

def ok(content: str, **fields: Any) -> ToolResult:
    """
    A successful result. The tool name is filled in by `execute_tool`.

    Tools do not repeat their own name at every return: it is already known one level up,
    and 56 hand-written copies of it were 56 chances to paste the wrong one.
    """
    return ToolResult(content=content, **fields)


def err(message: str, **fields: Any) -> ToolResult:
    """A failure result. As with `ok`, the tool name is stamped by `execute_tool`."""
    return ToolResult(content=message, is_error=True, **fields)


def _gate_capability(tool: AnyTool, args: BaseModel, ctx: ToolContext) -> ToolResult | None:
    """
    `permission_mode` gates *what*, independently of the tool allowlist.

    A mistaken frontmatter entry can put a tool in an agent's list; it cannot grant the
    agent the capability to use it. That is the point of checking here rather than during
    tool resolution.
    """
    required = tool.required_capability(args)
    allowed = CAPABILITIES_BY_MODE[ctx.permission_mode]
    if required in allowed:
        return None
    return err(
        f"Permission denied: {tool.name} needs '{required}' capability, but this agent "
        f"runs with permission_mode '{ctx.permission_mode}'."
    )


def _gate_workspace(tool: AnyTool, args: BaseModel, ctx: ToolContext) -> ToolResult | None:
    """
    `workspace` gates *where*, orthogonally to `permission_mode`.

    Only tools that genuinely require one side declare it; everything else follows the
    workspace the router already resolved.
    """
    required = tool.required_workspace(args)
    if required is None:
        return None
    if ctx.workspace_spec == "both" or ctx.workspace_spec == required:
        return None
    return err(
        f"Workspace denied: {tool.name} operates on the '{required}' workspace, but this "
        f"agent declares workspace '{ctx.workspace_spec}'."
    )


def _govern_size(tool: AnyTool, result: ToolResult, ctx: ToolContext) -> ToolResult:
    """
    Keeps an oversized result out of the context window without throwing it away.

    Over-limit content is written into the sandbox under `.tddagents/tool_results/` — an
    ordinary workspace path, so the agent can ReadFile it selectively, and an excluded one,
    so it never reaches the ledger or `workspace_output_*`.
    """
    limit = tool.max_result_chars
    if len(result.content) <= limit:
        return result

    path = f"{Config.TOOL_RESULTS_DIR}/{tool.name}-{ctx.next_result_id()}.txt"
    try:
        ctx.workspace.write_file(path, result.content)
    except WorkspaceError as exc:
        # Failing to persist must not lose the result; truncate in place instead.
        logger.warning("Could not persist oversized %s result: %s", tool.name, exc)
        result.content = result.content[:limit]
        result.truncated = True
        return result

    head = result.content[:PREVIEW_HEAD_CHARS]
    tail = result.content[-PREVIEW_TAIL_CHARS:]
    omitted = len(result.content) - PREVIEW_HEAD_CHARS - PREVIEW_TAIL_CHARS

    result.content = (
        f"{head}\n\n... [{omitted} characters omitted; full output saved to {path} — "
        f"read it with ReadFile] ...\n\n{tail}"
    )
    result.truncated = True
    result.persisted_path = path
    return result


def _sync_after_write(tool: AnyTool, ctx: ToolContext) -> None:
    """
    The tool-call sync checkpoint.

    Reconciliation lives here, once, rather than inside each write-capable tool, because a
    tool only knows about the writes it made itself — and the lost-files bug this closes is
    precisely about files nobody's tool made a record of.
    """
    if ctx.sync_engine is None:
        return
    try:
        updated, _ = ctx.sync_engine.reconcile_ledger(ctx.ledger)
        ctx.ledger.clear()
        ctx.ledger.update(updated)
    except WorkspaceError as exc:
        logger.warning("Ledger reconciliation after %s failed: %s", tool.name, exc)


def execute_tool(
    tool: AnyTool,
    raw_args: dict[str, Any],
    ctx: ToolContext,
    call_index: int = 0,
    call_id: str = "",
) -> ToolResult:
    """
    Runs one tool call end to end. Never raises.

    Order matters and is the same order the original uses: cheap rejections first, the
    expensive `call` last, and both hook events wrapped around it.
    """
    if not tool.is_enabled():
        return _stamp(err(f"{tool.name} is not available in this environment."),
                      call_index, call_id, tool.name)

    try:
        args = tool.args_schema.model_validate(raw_args)
    except ValidationError as exc:
        return _stamp(err(f"Invalid arguments for {tool.name}: {exc}"), call_index, call_id, tool.name)

    validation = tool.validate_input(args, ctx)
    if not validation.ok:
        return _stamp(err(validation.message), call_index, call_id, tool.name)

    dispatcher = ctx.hook_dispatcher
    additional_context = ""

    if dispatcher is not None:
        pre = dispatcher.run(
            HookEvent.PRE_TOOL_USE,
            tool_name=tool.name,
            tool_input=args.model_dump(mode="json"),
            ctx_fields=_hook_fields(ctx),
            command=_command_of(args),
        )
        if pre.additional_context:
            additional_context = pre.additional_context
        if pre.denied:
            return _stamp(
                err(f"Blocked by a PreToolUse hook: {pre.reason}"),
                call_index,
                call_id,
                tool.name,
            )
        if pre.updated_input is not None:
            try:
                args = tool.args_schema.model_validate(pre.updated_input)
            except ValidationError as exc:
                return _stamp(
                    err(f"A PreToolUse hook returned invalid updatedInput: {exc}"),
                    call_index,
                    call_id,
                    tool.name,
                )

    permission = tool.check_permissions(args, ctx)
    if permission.behavior == "deny":
        return _stamp(err(permission.message), call_index, call_id, tool.name)
    if permission.updated_input is not None:
        args = permission.updated_input

    for gate in (_gate_capability, _gate_workspace):
        denial = gate(tool, args, ctx)
        if denial is not None:
            return _stamp(denial, call_index, call_id, tool.name)

    try:
        result = tool.call(args, ctx)
    except WorkspaceError as exc:
        result = err(f"{tool.name} failed: {exc}")
    except Exception as exc:  # noqa: BLE001 - a tool bug must not take down the batch
        logger.exception("Unhandled error in %s", tool.name)
        result = err(f"{tool.name} raised an unexpected error: {exc}")

    result = _govern_size(tool, result, ctx)

    if dispatcher is not None:
        post = dispatcher.run(
            HookEvent.POST_TOOL_USE,
            tool_name=tool.name,
            tool_input=args.model_dump(mode="json"),
            tool_response=result.content,
            ctx_fields=_hook_fields(ctx),
            command=_command_of(args),
        )
        # A PostToolUse hook cannot un-run the tool: the write already landed in the
        # sandbox. Its output stays as ground truth, and the hook's objection rides along
        # as feedback the next turn reads.
        additional_context = "\n".join(x for x in (additional_context, post.additional_context) if x)
        if post.denied:
            additional_context = "\n".join(x for x in (additional_context, post.reason) if x)
        if post.denied or post.stop_continuation:
            result.hook_stopped_continuation = True

    result.additional_context = additional_context

    if not tool.is_read_only(args):
        _sync_after_write(tool, ctx)

    return _stamp(result, call_index, call_id, tool.name)


def _stamp(result: ToolResult, call_index: int, call_id: str, name: str = "") -> ToolResult:
    """
    Records where this result belongs in the model's original call order, and whose result
    it is.

    The name is authoritative here rather than at each `return` inside a tool, so a tool
    physically cannot mislabel its own output.
    """
    result.call_index = call_index
    result.call_id = call_id
    if name:
        result.tool_name = name
    return result


def _hook_fields(ctx: ToolContext) -> dict[str, Any]:
    return {
        "session_id": ctx.session_id,
        "cwd": ctx.workdir,
        "permission_mode": ctx.permission_mode,
        "agent_id": ctx.agent_id,
        "agent_type": ctx.agent_type,
    }


def _command_of(args: BaseModel) -> str | None:
    """
    The string a hook's `if:` condition matches against.

    Only shell-running tools carry one; for everything else the condition can still match
    on the tool name alone.
    """
    return getattr(args, "command", None)
