"""
The message substrate seam.

`tool_calls_in` is the single place the loop asks "did the model ask for anything", so it
runs against every message a model streams back. The cases that matter are the ones that
are not an `AIMessage` carrying calls, because those are the ones that could raise inside
the streaming branch and take the turn down with them.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.loop.messages import Message, tool_calls_in


class TestAMessageAsking:
    def test_the_calls_come_back(self):
        message = AIMessage(
            content="", tool_calls=[{"name": "RunTests", "args": {"path": "tests/"}, "id": "c1"}]
        )
        calls = tool_calls_in(message)
        assert len(calls) == 1
        assert calls[0]["name"] == "RunTests"
        assert calls[0]["args"] == {"path": "tests/"}
        assert calls[0]["id"] == "c1"

    def test_several_calls_keep_the_order_the_model_gave(self):
        message = AIMessage(
            content="",
            tool_calls=[
                {"name": "ReadFile", "args": {}, "id": "c1"},
                {"name": "RunTests", "args": {}, "id": "c2"},
            ],
        )
        assert [c["name"] for c in tool_calls_in(message)] == ["ReadFile", "RunTests"]

    def test_the_result_is_a_tuple_so_it_cannot_be_appended_to(self):
        message = AIMessage(content="", tool_calls=[{"name": "X", "args": {}, "id": "c1"}])
        assert isinstance(tool_calls_in(message), tuple)


class TestAMessageNotAsking:
    def test_an_assistant_message_with_no_calls(self):
        assert tool_calls_in(AIMessage(content="done")) == ()

    def test_a_message_type_that_has_no_tool_calls_attribute_at_all(self):
        """
        The mutation that found this: `getattr` without its default raises AttributeError
        here, inside the streaming branch, which would abort the turn rather than answer
        "not asking".
        """
        assert not hasattr(HumanMessage(content="spec"), "tool_calls")
        assert tool_calls_in(HumanMessage(content="spec")) == ()

    def test_other_message_types_answer_the_same_way(self):
        assert tool_calls_in(SystemMessage(content="rules")) == ()
        assert tool_calls_in(ToolMessage(content="ran", tool_call_id="c1")) == ()

    def test_tool_calls_set_to_nothing_is_not_asking(self):
        class Blank(HumanMessage):
            tool_calls: None = None

        message: Message = Blank(content="spec")
        assert tool_calls_in(message) == ()
