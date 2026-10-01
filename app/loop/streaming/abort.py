"""
Abort signal and three-controller abort tree substrate (Part F3).

Ported from:
- `reference/claude-code/src/utils/abortController.ts` -> `createAbortController`, `createChildAbortController`
- `reference/claude-code/src/services/tools/StreamingToolExecutor.ts` -> three-layer controller hierarchy
"""

from __future__ import annotations

import logging
from typing import Callable

from app.loop.context import CancelToken

logger = logging.getLogger(__name__)

AbortListener = Callable[[str | None], None]


class AbortSignal:
    """
    Carries the aborted state, reason, and listener callbacks for cooperative cancellation.
    """

    __slots__ = ("_aborted", "_reason", "_listeners")

    def __init__(self) -> None:
        self._aborted: bool = False
        self._reason: str | None = None
        self._listeners: list[AbortListener] = []

    @property
    def aborted(self) -> bool:
        return self._aborted

    @property
    def reason(self) -> str | None:
        return self._reason

    def add_listener(self, listener: AbortListener) -> None:
        """
        Register a callback to be invoked when abort fires.
        If already aborted, the callback is invoked immediately.
        """
        if self._aborted:
            try:
                listener(self._reason)
            except Exception as exc:
                logger.warning("Error in immediately invoked abort listener: %s", exc)
            return

        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_listener(self, listener: AbortListener) -> None:
        """
        Unregister an abort callback.
        """
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _trigger_abort(self, reason: str | None = None) -> None:
        if self._aborted:
            return

        self._aborted = True
        self._reason = reason

        listeners_copy = list(self._listeners)
        for listener in listeners_copy:
            try:
                listener(reason)
            except Exception as exc:
                logger.warning("Error in abort listener callback: %s", exc)


class AbortController:
    """
    Controls an AbortSignal, matching standard W3C AbortController semantics.
    """

    __slots__ = ("_signal",)

    def __init__(self) -> None:
        self._signal = AbortSignal()

    @property
    def signal(self) -> AbortSignal:
        return self._signal

    def abort(self, reason: str | None = None) -> None:
        """
        Trigger the abort event with an optional reason string.
        """
        self._signal._trigger_abort(reason)


def create_child_abort_controller(parent: AbortController) -> AbortController:
    """
    Creates a child AbortController that aborts when its parent aborts.
    Aborting the child does NOT affect the parent.

    Ported from `createChildAbortController` in `reference/claude-code/src/utils/abortController.ts`.
    """
    child = AbortController()

    if parent.signal.aborted:
        child.abort(parent.signal.reason)
        return child

    def on_parent_abort(reason: str | None) -> None:
        child.abort(reason)

    parent.signal.add_listener(on_parent_abort)
    return child


def bridge_cancel_token(
    cancel_token: CancelToken, controller: AbortController | None = None
) -> tuple[CancelToken, AbortController]:
    """
    Bidirectionally bridges a CancelToken and an AbortController so that cancelling
    either one updates and triggers the other.
    """
    ctrl = controller if controller is not None else AbortController()

    if cancel_token.cancelled and not ctrl.signal.aborted:
        ctrl.abort("token_cancelled")

    def on_abort(reason: str | None) -> None:
        cancel_token.cancelled = True

    ctrl.signal.add_listener(on_abort)
    return cancel_token, ctrl
