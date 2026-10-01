"""
Unit tests for AbortSignal, AbortController, and three-controller abort tree substrate (Part F3).
"""

from __future__ import annotations

import logging
import pytest

from app.loop.context import CancelToken
from app.loop.streaming.abort import (
    AbortController,
    AbortSignal,
    bridge_cancel_token,
    create_child_abort_controller,
)


class TestAbortSignal:
    def test_initial_state(self) -> None:
        signal = AbortSignal()
        assert signal.aborted is False
        assert signal.reason is None

    def test_trigger_abort(self) -> None:
        signal = AbortSignal()
        signal._trigger_abort("user_cancelled")
        assert signal.aborted is True
        assert signal.reason == "user_cancelled"

    def test_abort_is_idempotent(self) -> None:
        signal = AbortSignal()
        signal._trigger_abort("first_reason")
        signal._trigger_abort("second_reason")
        assert signal.aborted is True
        assert signal.reason == "first_reason"

    def test_listener_fires_on_abort(self) -> None:
        signal = AbortSignal()
        called_with: list[str | None] = []

        def listener(reason: str | None) -> None:
            called_with.append(reason)

        signal.add_listener(listener)
        # Adding same listener twice should be deduped
        signal.add_listener(listener)
        signal._trigger_abort("test_reason")
        assert called_with == ["test_reason"]

    def test_listener_added_after_abort_executes_immediately(self) -> None:
        signal = AbortSignal()
        signal._trigger_abort("already_aborted")

        called_with: list[str | None] = []
        signal.add_listener(lambda r: called_with.append(r))
        assert called_with == ["already_aborted"]

    def test_listener_exceptions_are_logged_and_swallowed(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        signal = AbortSignal()

        def bad_cb(r: str | None) -> None:
            raise RuntimeError("cb explosion")

        with caplog.at_level(logging.WARNING):
            signal.add_listener(bad_cb)
            signal._trigger_abort("boom")
            assert len(caplog.records) == 1
            assert caplog.records[-1].message == "Error in abort listener callback: cb explosion"

        caplog.clear()
        with caplog.at_level(logging.WARNING):
            signal.add_listener(bad_cb)
            assert len(caplog.records) == 1
            assert caplog.records[-1].message == "Error in immediately invoked abort listener: cb explosion"

    def test_remove_listener(self) -> None:
        signal = AbortSignal()
        called = False

        def listener(reason: str | None) -> None:
            nonlocal called
            called = True

        signal.add_listener(listener)
        signal.remove_listener(listener)
        # Calling remove again does nothing
        signal.remove_listener(listener)

        signal._trigger_abort("reason")
        assert called is False

    def test_multiple_listeners_and_exception_handling(self, caplog: pytest.LogCaptureFixture) -> None:
        signal = AbortSignal()
        seen: list[int] = []

        def bad_listener(r: str | None) -> None:
            raise RuntimeError("listener failed")

        signal.add_listener(lambda r: seen.append(1))
        signal.add_listener(bad_listener)
        signal.add_listener(lambda r: seen.append(2))

        with caplog.at_level(logging.WARNING):
            signal._trigger_abort("test")

        assert seen == [1, 2]
        assert "Error in abort listener callback: listener failed" in caplog.text

    def test_immediate_listener_exception_handled(self, caplog: pytest.LogCaptureFixture) -> None:
        signal = AbortSignal()
        signal._trigger_abort("aborted")

        def bad_listener(r: str | None) -> None:
            raise RuntimeError("immediate failure")

        with caplog.at_level(logging.WARNING):
            signal.add_listener(bad_listener)

        assert "Error in immediately invoked abort listener: immediate failure" in caplog.text


class TestAbortController:
    def test_controller_controls_signal(self) -> None:
        ctrl = AbortController()
        assert ctrl.signal.aborted is False

        ctrl.abort("stop")
        assert ctrl.signal.aborted is True
        assert ctrl.signal.reason == "stop"


class TestChildAbortController:
    def test_parent_abort_propagates_to_child(self) -> None:
        parent = AbortController()
        child = create_child_abort_controller(parent)

        assert child.signal.aborted is False
        parent.abort("parent_stopped")
        assert child.signal.aborted is True
        assert child.signal.reason == "parent_stopped"

    def test_child_abort_does_not_affect_parent(self) -> None:
        parent = AbortController()
        child = create_child_abort_controller(parent)

        child.abort("child_only")
        assert child.signal.aborted is True
        assert child.signal.reason == "child_only"
        assert parent.signal.aborted is False
        assert parent.signal.reason is None

    def test_creating_child_from_already_aborted_parent(self) -> None:
        parent = AbortController()
        parent.abort("early_abort")

        child = create_child_abort_controller(parent)
        assert child.signal.aborted is True
        assert child.signal.reason == "early_abort"

    def test_three_layer_cascade(self) -> None:
        turn = AbortController()
        sibling = create_child_abort_controller(turn)
        tool = create_child_abort_controller(sibling)

        assert not turn.signal.aborted
        assert not sibling.signal.aborted
        assert not tool.signal.aborted

        # Aborting sibling aborts tool, but not turn
        sibling.abort("sibling_error")
        assert not turn.signal.aborted
        assert sibling.signal.aborted
        assert tool.signal.aborted


class TestBridgeCancelToken:
    def test_token_cancelled_sets_controller_abort(self) -> None:
        token = CancelToken()
        token.cancel()
        _, ctrl = bridge_cancel_token(token)
        assert ctrl.signal.aborted is True
        assert ctrl.signal.reason == "token_cancelled"

    def test_controller_abort_sets_token_cancelled(self) -> None:
        token = CancelToken()
        ctrl = AbortController()
        bridge_cancel_token(token, ctrl)

        assert token.cancelled is False
        ctrl.abort("user_interrupt")
        assert token.cancelled is True

    def test_bridge_with_none_controller(self) -> None:
        token = CancelToken()
        t, ctrl = bridge_cancel_token(token, None)
        assert t is token
        assert ctrl.signal.aborted is False
        ctrl.abort("abc")
        assert token.cancelled is True
