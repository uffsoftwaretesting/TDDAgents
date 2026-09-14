"""
The closed vocabulary (A2).

The property under test is the one that string literals never had: the set is closed. A
reason that does not exist cannot be constructed, so a typo is a ValueError at the site
that made it rather than a run that quietly fails to route. Everything else here pins the
spellings, because they are a wire format — they reach logs, checkpoints and the event log
that Part L2 derives the flow classification from.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.loop.transitions import Continue, Terminal, Terminated, Transition


class TestTheSetIsClosed:
    def test_an_unknown_continue_reason_cannot_be_constructed(self):
        """The point of A2: `status = "next_trun"` used to be a silent routing failure."""
        with pytest.raises(ValueError):
            Continue("next_trun")

    def test_an_unknown_terminal_reason_cannot_be_constructed(self):
        with pytest.raises(ValueError):
            Terminal("plan_failed")

    def test_a_legacy_status_literal_is_not_a_reason(self):
        """
        `green_passed`, `red_confirmed` and friends were flow *status* strings. They are
        ledger facts now, not routing decisions, and must not reappear as reasons.
        """
        for legacy in ("green_passed", "red_confirmed", "tests_written", "next_req"):
            with pytest.raises(ValueError):
                Continue(legacy)
            with pytest.raises(ValueError):
                Terminal(legacy)


class TestTheSpellings:
    def test_continue_reasons(self):
        assert {c.value for c in Continue} == {
            "next_turn",
            "stop_hook_blocking",
            "reactive_compact_retry",
            "max_output_tokens_recovery",
        }

    def test_terminal_reasons(self):
        assert {t.value for t in Terminal} == {
            "completed",
            "blocking_limit",
            "model_error",
            "prompt_too_long",
            "aborted_streaming",
            "aborted_tools",
            "stop_hook_prevented",
            "hook_stopped",
        }

    def test_the_two_vocabularies_do_not_overlap(self):
        """A reason means continue or stop. One string meaning both would be unreadable."""
        assert {c.value for c in Continue}.isdisjoint({t.value for t in Terminal})

    def test_a_reason_read_back_as_a_plain_string_still_compares_equal(self):
        """
        The shape a checkpoint or a log line brings it back in. `StrEnum` is what lets the
        vocabulary be closed in code and still be an ordinary string on the way to Postgres.
        """
        from_checkpoint: str = "next_turn"
        assert from_checkpoint == Continue.NEXT_TURN
        assert isinstance(Continue.NEXT_TURN, str)

    def test_a_reason_formats_as_its_value_not_as_its_member_name(self):
        assert f"{Terminal.STOP_HOOK_PREVENTED}" == "stop_hook_prevented"

    def test_a_reason_round_trips_through_its_string(self):
        for continue_reason in Continue:
            assert Continue(str(continue_reason)) is continue_reason
        for terminal_reason in Terminal:
            assert Terminal(str(terminal_reason)) is terminal_reason


class TestTransition:
    def test_it_records_why_the_previous_iteration_continued(self):
        assert Transition(reason=Continue.NEXT_TURN).reason is Continue.NEXT_TURN

    def test_it_is_frozen(self):
        transition = Transition(reason=Continue.NEXT_TURN)
        with pytest.raises(FrozenInstanceError):
            transition.reason = Continue.STOP_HOOK_BLOCKING  # type: ignore[misc]

    def test_two_reasons_compare_unequal(self):
        assert Transition(reason=Continue.NEXT_TURN) != Transition(
            reason=Continue.STOP_HOOK_BLOCKING
        )


class TestTerminated:
    def test_it_carries_the_reason_the_run_ended(self):
        assert Terminated(reason=Terminal.COMPLETED).reason is Terminal.COMPLETED

    def test_it_is_frozen(self):
        terminated = Terminated(reason=Terminal.COMPLETED)
        with pytest.raises(FrozenInstanceError):
            terminated.reason = Terminal.MODEL_ERROR  # type: ignore[misc]

    def test_it_is_not_a_message(self):
        """
        The loop yields `Message | Terminated`, and consumers tell them apart by type.
        A Terminated that quacked like a message would be appended to a conversation.
        """
        from langchain_core.messages import BaseMessage

        assert not isinstance(Terminated(reason=Terminal.COMPLETED), BaseMessage)
