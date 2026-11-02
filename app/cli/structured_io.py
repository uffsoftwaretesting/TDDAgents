"""
The SDK protocol over stdio: NDJSON in, NDJSON out, and the control requests both ways.

Ported from `reference/claude-code/src/cli/structuredIO.ts` -> `StructuredIO` (`read`,
`processLine`, `write`, `sendRequest`, `prependUserMessage`, `injectControlResponse`,
`getPendingPermissionRequests`, `setUnexpectedResponseCallback`, `createCanUseTool`,
`createHookCallback`, `handleElicitation`, `createSandboxAskCallback`, `sendMcpMessage`),
`serializeDecisionReason`, `buildRequiresActionDetails`, `MAX_RESOLVED_TOOL_USE_IDS`,
`SANDBOX_NETWORK_ACCESS_TOOL_NAME`, `executePermissionRequestHooksForSDK` and `exitWithMessage`.

Reading: the input arrives in blocks; complete lines are parsed one by one (`requestId` accepted
for `request_id`). `keep_alive` is dropped, `update_environment_variables` is applied to the
environment, a `control_response` settles the request it answers (a duplicate for a tool use
already settled is ignored; an unknown one goes to the orphan callback), an unknown type is logged
and skipped, and `user`, `control_request`, `assistant` and `system` messages are yielded. A line
that is not JSON, a `control_request` without its `request`, or a user message whose role is not
`user` ends the process with upstream's error. When the input closes, every pending request fails
with "Tool permission stream closed before response received".

Writing: everything goes through `outbound` (`Stream`), the single writer that keeps a
`control_request` from overtaking the stream events queued before it.

`create_can_use_tool` is the SDK host's permission prompt: an 'ask' becomes a `can_use_tool`
request raced against the PermissionRequest hooks (whichever decides first wins; the loser's
request is cancelled with `control_cancel_request`). **TDDAgents**: an `allow` whose
`updatedInput` differs is re-checked against the rule-based gate (the TDD phase tier among it), as
a PermissionRequest hook's `updatedInput` is, so no host answer can open a phase-forbidden write.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import uuid as uuid_module
from collections import OrderedDict
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, NoReturn

from app.cli.ndjson_safe_stringify import ndjson_safe_stringify
from app.types.permissions import PermissionBehavior, PermissionResult
from app.utils.control_message_compat import normalize_control_message_keys
from app.utils.permissions.permission_prompt_tool_result_schema import (
    parse_permission_prompt_output,
    permission_prompt_tool_result_to_permission_decision,
)
from app.utils.stream import Stream

logger = logging.getLogger(__name__)

#: `SANDBOX_NETWORK_ACCESS_TOOL_NAME`.
SANDBOX_NETWORK_ACCESS_TOOL_NAME = "SandboxNetworkAccess"
#: `MAX_RESOLVED_TOOL_USE_IDS`: the settled tool uses remembered against duplicate responses.
MAX_RESOLVED_TOOL_USE_IDS = 1000
#: The message a pending request fails with when the input closes.
STREAM_CLOSED_BEFORE_RESPONSE = "Tool permission stream closed before response received"

#: A response's payload in, the validated value out (upstream's zod `schema.parse`).
Parser = Callable[[Any], Any]


class StreamClosedError(RuntimeError):
    """`sendRequest` after the input closed (`Stream closed`)."""


class RequestAbortedError(RuntimeError):
    """`AbortError`: the request's signal fired."""


class ControlRequestError(RuntimeError):
    """The host answered a control request with `subtype: 'error'`."""


def exit_with_message(message: str) -> NoReturn:
    """`exitWithMessage`: the message on stderr, exit code 1."""
    print(message, file=sys.stderr)
    sys.exit(1)


def serialize_decision_reason(reason: Mapping[str, Any] | None) -> str | None:
    """
    `serializeDecisionReason`: the reason text the host is shown, for the reason types that
    carry one (`classifier` included: TDDAgents' auto mode is the external build's classifier).
    """
    if not reason:
        return None
    kind = reason.get("type")
    if kind in ("classifier", "hook", "asyncAgent", "sandboxOverride", "workingDir", "safetyCheck", "other"):
        text = reason.get("reason")
        return str(text) if text is not None else None
    return None


def build_requires_action_details(
    tool: Any, input_args: dict[str, Any], tool_use_id: str, request_id: str
) -> dict[str, Any]:
    """`buildRequiresActionDetails`: what a session waiting on the host is waiting for."""
    try:
        description = None
        activity = getattr(tool, "get_activity_description", None)
        if activity is not None:
            description = activity(input_args)
        if description is None:
            summary = getattr(tool, "get_tool_use_summary", None)
            description = summary(input_args) if summary is not None else None
        if description is None:
            description = tool.user_facing_name(input_args)
    except Exception:  # noqa: BLE001 - a bad description must never break permission handling
        description = tool.name
    return {"tool_name": tool.name, "action_description": description, "tool_use_id": tool_use_id,
            "request_id": request_id, "input": input_args}


@dataclass
class _Pending:
    request: dict[str, Any]
    future: asyncio.Future[Any]
    parse: Parser | None


async def stdin_blocks() -> AsyncIterator[str]:
    """
    The process's stdin as text blocks (upstream's `process.stdin`): a pipe or terminal through
    the event loop, a regular file read whole.
    """
    stdin = sys.stdin
    try:
        regular = _is_regular(stdin.fileno())
    except (AttributeError, OSError, ValueError):
        regular = True
    if regular:
        data = stdin.read()
        if data:
            yield data
        return
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    transport, _ = await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), stdin)
    try:
        while chunk := await reader.read(65536):
            yield chunk.decode("utf-8", errors="replace")
    finally:
        transport.close()


def _is_regular(fd: int) -> bool:
    import stat

    return stat.S_ISREG(os.fstat(fd).st_mode)


class StructuredIO:
    """`StructuredIO`."""

    def __init__(
        self, input_blocks: AsyncIterable[str], replay_user_messages: bool = False,
        *, write_stdout: Callable[[str], None] | None = None,
        exit_with: Callable[[str], NoReturn] = exit_with_message,
    ) -> None:
        self._input = input_blocks
        self.replay_user_messages = replay_user_messages
        self._pending: dict[str, _Pending] = {}
        self.input_closed = False
        self._unexpected_response_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None
        #: `resolvedToolUseIds`, oldest first.
        self._resolved_tool_use_ids: OrderedDict[str, None] = OrderedDict()
        self._prepended_lines: list[str] = []
        #: `outbound`: the one FIFO every stdout message goes through.
        self.outbound: Stream[dict[str, Any]] = Stream()
        self._write_stdout = write_stdout or _write_to_stdout
        self._exit = exit_with
        self.structured_input = self._read()
        #: Called with each `can_use_tool` request's details while the host decides
        #: (`notifySessionStateChanged('requires_action')`), and with `None` when none is pending.
        self.on_permission_state: Callable[[dict[str, Any] | None], None] | None = None

    # ── reading ──────────────────────────────────────────────────────────────

    def _track_resolved_tool_use_id(self, request: Mapping[str, Any]) -> None:
        inner = request.get("request") or {}
        if inner.get("subtype") == "can_use_tool":
            self._resolved_tool_use_ids[str(inner.get("tool_use_id"))] = None
            if len(self._resolved_tool_use_ids) > MAX_RESOLVED_TOOL_USE_IDS:
                self._resolved_tool_use_ids.popitem(last=False)

    def prepend_user_message(self, content: str) -> None:
        """`prependUserMessage`: a user turn read before the next input line."""
        self._prepended_lines.append(json.dumps({
            "type": "user", "session_id": "", "message": {"role": "user", "content": content},
            "parent_tool_use_id": None,
        }) + "\n")

    async def _read(self) -> AsyncIterator[dict[str, Any]]:
        content = ""

        async def split_and_process() -> AsyncIterator[dict[str, Any]]:
            nonlocal content
            while True:
                if self._prepended_lines:
                    content = "".join(self._prepended_lines) + content
                    self._prepended_lines = []
                newline = content.find("\n")
                if newline == -1:
                    return
                line, content = content[:newline], content[newline + 1:]
                message = await self.process_line(line)
                if message is not None:
                    yield message

        async for message in split_and_process():
            yield message
        async for block in self._input:
            content += block
            async for message in split_and_process():
                yield message
        if content:
            last = await self.process_line(content)
            if last is not None:
                yield last
        self.input_closed = True
        for pending in list(self._pending.values()):
            if not pending.future.done():
                pending.future.set_exception(StreamClosedError(STREAM_CLOSED_BEFORE_RESPONSE))

    def get_pending_permission_requests(self) -> list[dict[str, Any]]:
        """`getPendingPermissionRequests`."""
        return [p.request for p in self._pending.values()
                if (p.request.get("request") or {}).get("subtype") == "can_use_tool"]

    def set_unexpected_response_callback(self, callback: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        """`setUnexpectedResponseCallback`: where a response nobody waits for goes."""
        self._unexpected_response_callback = callback

    def _settle(self, pending: _Pending, response: Mapping[str, Any]) -> None:
        if pending.future.done():
            return
        if response.get("subtype") == "error":
            pending.future.set_exception(ControlRequestError(str(response.get("error"))))
            return
        payload = response.get("response")
        if pending.parse is None:
            pending.future.set_result({})
            return
        try:
            pending.future.set_result(pending.parse(payload))
        except Exception as error:  # noqa: BLE001 - a schema failure rejects the request, as zod's parse
            pending.future.set_exception(error)

    def inject_control_response(self, message: Mapping[str, Any]) -> None:
        """
        `injectControlResponse`: settle a pending request from elsewhere, and tell the host to
        cancel its own prompt for it.
        """
        response = message.get("response") or {}
        request_id = response.get("request_id")
        pending = self._pending.pop(str(request_id), None) if request_id else None
        if pending is None:
            return
        self._track_resolved_tool_use_id(pending.request)
        self.outbound.enqueue({"type": "control_cancel_request", "request_id": request_id})
        self._settle(pending, response)

    async def process_line(self, line: str) -> dict[str, Any] | None:
        """`processLine`."""
        if not line:
            return None
        try:
            message = normalize_control_message_keys(json.loads(line))
        except ValueError as error:
            self._exit(f"Error parsing streaming input line: {line}: {error}")
        if not isinstance(message, dict):
            self._exit(f"Error parsing streaming input line: {line}: not an object")
        kind = message.get("type")
        if kind == "keep_alive":
            return None
        if kind == "update_environment_variables":
            variables = message.get("variables") or {}
            for key, value in variables.items():
                os.environ[str(key)] = str(value)
            logger.debug("[structuredIO] applied update_environment_variables: %s", ", ".join(variables))
            return None
        if kind == "control_response":
            return await self._process_control_response(message)
        if kind not in ("user", "control_request", "assistant", "system"):
            logger.warning("Ignoring unknown message type: %s", kind)
            return None
        if kind == "control_request":
            if not message.get("request"):
                self._exit("Error: Missing request on control_request")
            return message
        if kind in ("assistant", "system"):
            return message
        role = (message.get("message") or {}).get("role")
        if role != "user":
            self._exit(f"Error: Expected message role 'user', got '{role}'")
        return message

    async def _process_control_response(self, message: dict[str, Any]) -> dict[str, Any] | None:
        response = message.get("response") or {}
        request_id = str(response.get("request_id"))
        pending = self._pending.get(request_id)
        if pending is None:
            payload = response.get("response") if response.get("subtype") == "success" else None
            tool_use_id = payload.get("toolUseID") if isinstance(payload, Mapping) else None
            if isinstance(tool_use_id, str) and tool_use_id in self._resolved_tool_use_ids:
                logger.debug("Ignoring duplicate control_response for already-resolved toolUseID=%s request_id=%s",
                             tool_use_id, request_id)
                return None
            if self._unexpected_response_callback is not None:
                await self._unexpected_response_callback(message)
            return None
        self._track_resolved_tool_use_id(pending.request)
        del self._pending[request_id]
        self._settle(pending, response)
        if response.get("subtype") == "error":
            return None
        return message if self.replay_user_messages else None

    # ── writing ──────────────────────────────────────────────────────────────

    async def write(self, message: Mapping[str, Any]) -> None:
        """`write`: one NDJSON line on stdout."""
        self._write_stdout(ndjson_safe_stringify(message) + "\n")

    def send_request(
        self, request: dict[str, Any], parse: Parser | None, signal: Any = None, request_id: str | None = None,
    ) -> Awaitable[Any]:
        """
        `sendRequest`: a `control_request` to the host, awaited until its `control_response`. As the
        JavaScript async function runs to its first `await` when called, the request is checked and
        enqueued at the call, and the returned awaitable waits for the answer. `signal` (a
        `CancelToken`) cancels it: the host is sent `control_cancel_request` and the call fails at once
        with `RequestAbortedError`.
        """
        request_id = request_id or str(uuid_module.uuid4())
        message = {"type": "control_request", "request_id": request_id, "request": request}
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        if self.input_closed:
            future.set_exception(StreamClosedError("Stream closed"))
            return future
        if signal is not None and signal.cancelled:
            future.set_exception(RequestAbortedError("Request aborted"))
            return future
        self._pending[request_id] = _Pending(request=message, future=future, parse=parse)
        self.outbound.enqueue(message)

        def aborted() -> None:
            self.outbound.enqueue({"type": "control_cancel_request", "request_id": request_id})
            self._track_resolved_tool_use_id(message)
            self._pending.pop(request_id, None)
            if not future.done():
                future.set_exception(RequestAbortedError("Request aborted"))

        remove = signal.add_listener(aborted) if signal is not None else None

        async def answer() -> Any:
            try:
                return await future
            finally:
                if remove is not None:
                    remove()
                self._pending.pop(request_id, None)

        return answer()

    # ── what the run asks the host ───────────────────────────────────────────

    def create_can_use_tool(
        self, on_permission_prompt: Callable[[dict[str, Any]], None] | None = None
    ) -> Callable[..., Awaitable[PermissionResult]]:
        """`createCanUseTool`: the host's permission prompt, raced against PermissionRequest hooks."""
        from app.tool import CancelToken

        async def can_use_tool(
            tool: Any, input_args: dict[str, Any], result: PermissionResult, context: Any, tool_use_id: str
        ) -> PermissionResult:
            if result.behavior in (PermissionBehavior.ALLOW, PermissionBehavior.DENY):
                return result
            hook_abort = CancelToken()
            remove_parent = context.cancel.add_listener(hook_abort.cancel)
            hook_task = asyncio.ensure_future(execute_permission_request_hooks_for_sdk(
                tool, tool_use_id, input_args, context))
            request_id = str(uuid_module.uuid4())
            if on_permission_prompt is not None:
                on_permission_prompt(build_requires_action_details(tool, input_args, tool_use_id, request_id))
            request: dict[str, Any] = {
                "subtype": "can_use_tool", "tool_name": tool.name, "input": input_args,
                "permission_suggestions": _suggestions(result), "blocked_path": _blocked_path(result),
                "decision_reason": serialize_decision_reason(result.decision_reason), "tool_use_id": tool_use_id,
                "agent_id": getattr(context, "agent_id", None),
            }
            sdk_task = asyncio.ensure_future(self.send_request(
                {k: v for k, v in request.items() if v is not None}, parse_permission_prompt_output,
                hook_abort, request_id))
            try:
                done, _ = await asyncio.wait({hook_task, sdk_task}, return_when=asyncio.FIRST_COMPLETED)
                if hook_task in done:
                    decision = hook_task.result()
                    if decision is not None:
                        hook_abort.cancel()
                        sdk_task.add_done_callback(_swallow)
                        return decision
                answer = await sdk_task
                return await _decision_from_host(answer, tool, input_args, context)
            except Exception as error:  # noqa: BLE001 - any failure is a deny, as upstream's catch
                sdk_task.add_done_callback(_swallow)
                return permission_prompt_tool_result_to_permission_decision(
                    {"behavior": "deny", "message": f"Tool permission request failed: {error}",
                     "toolUseID": tool_use_id}, tool, input_args, context)
            finally:
                remove_parent()
                if not hook_task.done():
                    hook_task.cancel()
                if not self.get_pending_permission_requests() and self.on_permission_state is not None:
                    self.on_permission_state(None)

        return can_use_tool

    def create_hook_callback(self, callback_id: str, timeout: float | None = None) -> Any:
        """`createHookCallback`: a `callback` hook answered by the host (`hook_callback`)."""

        async def callback(hook_input: Mapping[str, Any], tool_use_id: str | None, abort: Any = None) -> dict[str, Any]:
            request: dict[str, Any] = {"subtype": "hook_callback", "callback_id": callback_id,
                                       "input": dict(hook_input)}
            if tool_use_id:
                request["tool_use_id"] = tool_use_id
            try:
                output = await self.send_request(request, _hook_output, abort)
                return dict(output)
            except Exception as error:  # noqa: BLE001 - a failing callback is an empty hook output
                print(f"Error in hook callback {callback_id}: {error}", file=sys.stderr)
                return {}

        from app.schemas.hooks import CallbackHook

        return CallbackHook(callback=callback, timeout=timeout)

    async def handle_elicitation(
        self, server_name: str, message: str, requested_schema: Mapping[str, Any] | None = None,
        signal: Any = None, mode: str | None = None, url: str | None = None, elicitation_id: str | None = None,
    ) -> dict[str, Any]:
        """`handleElicitation`: the host answers an MCP server's elicitation; any failure cancels."""
        request = {"subtype": "elicitation", "mcp_server_name": server_name, "message": message, "mode": mode,
                   "url": url, "elicitation_id": elicitation_id,
                   "requested_schema": dict(requested_schema) if requested_schema is not None else None}
        try:
            result = await self.send_request({k: v for k, v in request.items() if v is not None},
                                             _elicitation_response, signal)
            return dict(result)
        except Exception:  # noqa: BLE001 - upstream answers every failure with a cancel
            return {"action": "cancel"}

    def create_sandbox_ask_callback(self) -> Callable[[Mapping[str, Any]], Awaitable[bool]]:
        """`createSandboxAskCallback`: network access asked as a synthetic tool's permission."""

        async def ask(host_pattern: Mapping[str, Any]) -> bool:
            host = host_pattern.get("host")
            try:
                result = await self.send_request({
                    "subtype": "can_use_tool", "tool_name": SANDBOX_NETWORK_ACCESS_TOOL_NAME,
                    "input": {"host": host}, "tool_use_id": str(uuid_module.uuid4()),
                    "description": f"Allow network connection to {host}?",
                }, parse_permission_prompt_output)
            except Exception:  # noqa: BLE001 - any failure denies the connection
                return False
            return bool(result.get("behavior") == "allow")

        return ask

    async def send_mcp_message(self, server_name: str, message: Mapping[str, Any]) -> Any:
        """`sendMcpMessage`: a JSON-RPC message to an SDK MCP server, and its response."""
        response = await self.send_request(
            {"subtype": "mcp_message", "server_name": server_name, "message": dict(message)}, _mcp_response)
        return response["mcp_response"]


def _swallow(task: asyncio.Future[Any]) -> None:
    """`sdkPromise.catch(() => {})`: the losing request's expected failure is not an error."""
    if not task.cancelled():
        task.exception()


def _suggestions(result: PermissionResult) -> list[Any] | None:
    if result.suggestions is None:
        return None
    from app.utils.permissions.permission_update import permission_update_to_json

    return [permission_update_to_json(update) for update in result.suggestions]


def _blocked_path(result: PermissionResult) -> str | None:
    reason = result.decision_reason or {}
    blocked = reason.get("blockedPath") or reason.get("blocked_path")
    return str(blocked) if blocked else None


def _hook_output(raw: Any) -> dict[str, Any]:
    """`hookJSONOutputSchema().parse`: an object."""
    if not isinstance(raw, Mapping):
        raise ValueError("hook callback output: Expected object")
    return dict(raw)


def _elicitation_response(raw: Any) -> dict[str, Any]:
    """`SDKControlElicitationResponseSchema().parse`: an `action` of accept, decline or cancel."""
    if not isinstance(raw, Mapping) or raw.get("action") not in ("accept", "decline", "cancel"):
        raise ValueError("elicitation response: Expected an action of accept, decline or cancel")
    return dict(raw)


def _mcp_response(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or "mcp_response" not in raw:
        raise ValueError("mcp_message response: mcp_response missing")
    return dict(raw)


async def _decision_from_host(
    answer: Mapping[str, Any], tool: Any, input_args: dict[str, Any], context: Any
) -> PermissionResult:
    """
    `permissionPromptToolResultToPermissionDecision`, then (TDDAgents) a changed input re-checked
    against the rule-based gate, the TDD phase tier among it, before it is allowed.
    """
    from app.utils.permissions.permissions import check_rule_based_permissions

    decision = permission_prompt_tool_result_to_permission_decision(answer, tool, input_args, context)
    final_input = decision.updated_input
    if decision.behavior == PermissionBehavior.ALLOW and final_input is not None and final_input != input_args:
        recheck = await check_rule_based_permissions(tool, final_input, context)
        if recheck is not None and recheck.behavior == PermissionBehavior.DENY:
            return recheck
    return decision


async def execute_permission_request_hooks_for_sdk(
    tool: Any, tool_use_id: str, input_args: dict[str, Any], context: Any
) -> PermissionResult | None:
    """`executePermissionRequestHooksForSDK`: the first hook that decides, or `None`."""
    from app.utils.permissions.permissions import (
        extract_permission_context,
        run_permission_request_hooks_for_headless_agent,
    )

    mode = extract_permission_context(context).mode
    return await run_permission_request_hooks_for_headless_agent(tool, input_args, tool_use_id, context, mode)


def _write_to_stdout(text: str) -> None:
    """`writeToStdout`."""
    sys.stdout.write(text)
    sys.stdout.flush()


__all__ = [
    "MAX_RESOLVED_TOOL_USE_IDS",
    "SANDBOX_NETWORK_ACCESS_TOOL_NAME",
    "STREAM_CLOSED_BEFORE_RESPONSE",
    "ControlRequestError",
    "RequestAbortedError",
    "StreamClosedError",
    "StructuredIO",
    "build_requires_action_details",
    "execute_permission_request_hooks_for_sdk",
    "exit_with_message",
    "serialize_decision_reason",
    "stdin_blocks",
]
