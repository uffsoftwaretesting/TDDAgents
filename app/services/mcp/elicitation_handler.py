"""
Answering an MCP server's elicitation: hooks first, then whoever hosts the session, then the
result hooks.

Ported from `reference/claude-code/src/services/mcp/elicitationHandler.ts` ->
`getElicitationMode`, `runElicitationHooks`, `runElicitationResultHooks`; and from
`reference/claude-code/src/cli/print.ts` -> `registerElicitationHandlers` (its
`ElicitRequestSchema` request handler and its `ElicitationCompleteNotificationSchema`
notification handler), as `build_elicitation_callback` and
`build_elicitation_complete_handler`.

The hooks are reached through the run's `HookDispatcher`, passed in as the first argument
(upstream reads the session's hook configuration from module state). `None` runs no hooks.

* `run_elicitation_hooks`: an `Elicitation` hook may answer for the user (its `action` and
  `content`) or block, which declines. A hook failure answers nothing, so the host is asked.
* `run_elicitation_result_hooks`: an `ElicitationResult` hook sees the answer and may replace
  it (`content` falls back to the answer's own) or block, which declines. Every outcome fires
  an `elicitation_response` Notification hook, fire-and-forget (upstream's `void`).
* `build_elicitation_callback(dispatcher, ask_host)`: the connection's elicitation callback
  (`connect_to_server(..., elicitation_callback=...)`): hooks, then `ask_host` (upstream's
  `structuredIO.handleElicitation`), then the result hooks.
* `build_elicitation_complete_handler(dispatcher, emit)`: a server's
  `notifications/elicitation/complete` (URL mode) fires an `elicitation_complete` Notification
  hook and hands the server and elicitation id to `emit` (upstream enqueues the SDK's
  `system/elicitation_complete` message there).

The Python SDK's elicitation callback receives no abort signal: a request the server cancels
cancels the callback's task instead, so the hooks run without one.

Not ported: `registerElicitationHandler`, the REPL's path, which queues the request in
`AppState.elicitation` for the React `ElicitationDialog` (with its URL-mode waiting state,
`findElicitationInQueue` and the completion flag the dialog reacts to), and the analytics events
(`tengu_mcp_elicitation_shown`, `tengu_mcp_elicitation_response`).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from mcp.types import (
    ElicitRequestFormParams,
    ElicitRequestParams,
    ElicitRequestURLParams,
    ElicitResult,
)

from app.utils.hooks.hooks import (
    HookDispatcher,
    execute_elicitation_hooks,
    execute_elicitation_result_hooks,
    execute_notification_hooks,
)

logger = logging.getLogger(__name__)

#: `connect_to_server(..., elicitation_callback=...)`: `(server_name, params) -> ElicitResult`.
ElicitationCallback = Callable[[str, ElicitRequestParams], Awaitable[ElicitResult]]
#: `connect_to_server(..., on_elicitation_complete=...)`: `(server_name, elicitation_id)`.
OnElicitationComplete = Callable[[str, str], Awaitable[None]]
#: `structuredIO.handleElicitation`: `(server_name, message, requested_schema, mode, url,
#: elicitation_id) -> {action, content?}`.
AskHost = Callable[
    [str, str, Mapping[str, Any] | None, str, str | None, str | None], Awaitable[Mapping[str, Any]]
]
#: Where an `elicitation_complete` goes after its hooks: `(server_name, elicitation_id)`.
EmitElicitationComplete = Callable[[str, str], None]


def get_elicitation_mode(params: ElicitRequestParams) -> str:
    """`getElicitationMode`: `url` or `form`."""
    return "url" if params.mode == "url" else "form"


def _url_of(params: ElicitRequestParams) -> str | None:
    return params.url if isinstance(params, ElicitRequestURLParams) else None


def _elicitation_id_of(params: ElicitRequestParams) -> str | None:
    return params.elicitation_id if isinstance(params, ElicitRequestURLParams) else None


def _requested_schema_of(params: ElicitRequestParams) -> dict[str, Any] | None:
    return params.requested_schema if isinstance(params, ElicitRequestFormParams) else None


def _notify(dispatcher: HookDispatcher | None, message: str, notification_type: str) -> None:
    """`void executeNotificationHooks({message, notificationType})`."""
    if dispatcher is not None:
        dispatcher.spawn_background(execute_notification_hooks(dispatcher, message, notification_type))


async def run_elicitation_hooks(
    dispatcher: HookDispatcher | None,
    server_name: str,
    params: ElicitRequestParams,
    signal: Any = None,
) -> ElicitResult | None:
    """`runElicitationHooks`: a hook's answer, a decline when a hook blocks, else `None`."""
    try:
        outcome = await execute_elicitation_hooks(
            dispatcher, server_name, params.message, requested_schema=_requested_schema_of(params),
            signal=signal, mode=get_elicitation_mode(params), url=_url_of(params),
            elicitation_id=_elicitation_id_of(params),
        )
        if outcome.blocking_error is not None:
            return ElicitResult(action="decline")
        response = outcome.elicitation_response
        if response is not None:
            return ElicitResult.model_validate({"action": response.action, "content": response.content})
        return None
    except Exception as exc:  # noqa: BLE001 - upstream logs the failed hook and asks the user
        logger.error("MCP server %s: Elicitation hook error: %s", server_name, exc)
        return None


async def run_elicitation_result_hooks(
    dispatcher: HookDispatcher | None,
    server_name: str,
    result: ElicitResult,
    signal: Any = None,
    mode: str | None = None,
    elicitation_id: str | None = None,
) -> ElicitResult:
    """
    `runElicitationResultHooks`: the answer after the `ElicitationResult` hooks (replaced,
    declined by a block, or unchanged), then an `elicitation_response` notification.
    """
    try:
        outcome = await execute_elicitation_result_hooks(
            dispatcher, server_name, result.action, content=result.content, signal=signal, mode=mode,
            elicitation_id=elicitation_id,
        )
        if outcome.blocking_error is not None:
            _notify(dispatcher, f'Elicitation response for server "{server_name}": decline', "elicitation_response")
            return ElicitResult(action="decline")
        response = outcome.elicitation_result_response
        final = result
        if response is not None:
            content = response.content if response.content is not None else result.content
            final = ElicitResult.model_validate({"action": response.action, "content": content})
        _notify(dispatcher, f'Elicitation response for server "{server_name}": {final.action}',
                "elicitation_response")
        return final
    except Exception as exc:  # noqa: BLE001 - upstream logs it and keeps the user's answer
        logger.error("MCP server %s: ElicitationResult hook error: %s", server_name, exc)
        _notify(dispatcher, f'Elicitation response for server "{server_name}": {result.action}',
                "elicitation_response")
        return result


def build_elicitation_callback(dispatcher: HookDispatcher | None, ask_host: AskHost) -> ElicitationCallback:
    """`registerElicitationHandlers`' request handler: hooks, then the host, then the result hooks."""

    async def handle(server_name: str, params: ElicitRequestParams) -> ElicitResult:
        logger.debug("MCP server %s: Elicitation request received in print mode: %s", server_name,
                     params.model_dump(by_alias=True, exclude_none=True))
        mode = get_elicitation_mode(params)
        hook_response = await run_elicitation_hooks(dispatcher, server_name, params)
        if hook_response is not None:
            logger.debug("MCP server %s: Elicitation resolved by hook: %s", server_name,
                         hook_response.model_dump(by_alias=True, exclude_none=True))
            return hook_response
        elicitation_id = _elicitation_id_of(params)
        raw = await ask_host(server_name, params.message, _requested_schema_of(params), mode, _url_of(params),
                             elicitation_id)
        return await run_elicitation_result_hooks(
            dispatcher, server_name, ElicitResult.model_validate(dict(raw)), None, mode, elicitation_id)

    return handle


def build_elicitation_complete_handler(
    dispatcher: HookDispatcher | None, emit: EmitElicitationComplete | None = None
) -> OnElicitationComplete:
    """`registerElicitationHandlers`' completion handler: the Notification hook, then `emit`."""

    async def on_complete(server_name: str, elicitation_id: str) -> None:
        logger.debug("MCP server %s: Elicitation completion notification: %s", server_name, elicitation_id)
        _notify(dispatcher, f'MCP server "{server_name}" confirmed elicitation {elicitation_id} complete',
                "elicitation_complete")
        if emit is not None:
            emit(server_name, elicitation_id)

    return on_complete


__all__ = [
    "AskHost",
    "ElicitationCallback",
    "EmitElicitationComplete",
    "OnElicitationComplete",
    "build_elicitation_callback",
    "build_elicitation_complete_handler",
    "get_elicitation_mode",
    "run_elicitation_hooks",
    "run_elicitation_result_hooks",
]
