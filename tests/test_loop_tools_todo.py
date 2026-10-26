"""
`TodoWrite`: `app/loop/tools/todo.py`.

The list is state, not a file. The thing worth pinning is that a subagent's checklist never
overwrites the coordinator's, and that an all-complete list is cleared from state while
still being reported in full.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

import pytest

from app.loop.context.app_state import AppState, AppStateStore
from app.loop.context.tool_context import tool_context_for
from app.loop.tools.todo import (
    TODO_INPUT_SCHEMA,
    TODO_RESULT_MESSAGE,
    TODO_STATUSES,
    TODO_WRITE_TOOL_NAME,
    TodoItem,
    build_todo_write_tool,
    read_todos,
)
from app.loop.tools.validation import validate_against_schema


def tool() -> Any:
    """
    Built per test, never at module import.

    A module-level `build_todo_write_tool()` runs during collection, so mutmut's stats pass
    attributes it to no test and every mutant inside it is reported as surviving while
    actually being unreachable. See the gotcha in CLAUDE.md.
    """
    return build_todo_write_tool()


def item(content: str, status: str = "pending", active: str | None = None) -> dict[str, str]:
    return {"content": content, "status": status, "activeForm": active or f"Doing {content}"}


def run(args: dict[str, Any], context: Any) -> Any:
    return asyncio.run(tool().call(args, context))


def context_for(**kwargs: Any) -> Any:
    return tool_context_for(AppStateStore(AppState()), **kwargs)


# ── the contract ────────────────────────────────────────────────────────────

def test_the_tool_is_named_and_prompted():
    built = tool()
    assert built.name == TODO_WRITE_TOOL_NAME == "TodoWrite"
    assert "todo list" in built.prompt.lower()
    assert "activeForm" in built.prompt


def test_it_is_neither_read_only_nor_concurrency_safe():
    """Upstream declares neither, so the fail-closed defaults apply: it writes, alone."""
    built = tool()
    assert built.is_read_only({}) is False
    assert built.is_concurrency_safe({}) is False


def test_the_statuses_are_upstreams():
    assert TODO_STATUSES == ("pending", "in_progress", "completed")


# ── schema ──────────────────────────────────────────────────────────────────

def test_a_well_formed_list_validates():
    args = {"todos": [item("Write the failing test", "in_progress")]}
    assert validate_against_schema("TodoWrite", TODO_INPUT_SCHEMA, args) is None


def test_an_empty_list_is_valid():
    assert validate_against_schema("TodoWrite", TODO_INPUT_SCHEMA, {"todos": []}) is None


def test_a_missing_active_form_is_reported_with_its_path():
    args = {"todos": [{"content": "x", "status": "pending"}]}
    message = validate_against_schema("TodoWrite", TODO_INPUT_SCHEMA, args)
    assert message is not None and "todos[0].activeForm" in message


def test_an_unknown_status_is_rejected():
    args = {"todos": [item("x", status="almost")]}
    assert validate_against_schema("TodoWrite", TODO_INPUT_SCHEMA, args) is not None


def test_an_unknown_top_level_parameter_is_rejected():
    """Strict at the top level, so the model learns it invented a parameter."""
    args = {"todos": [item("x")], "priority": "high"}
    assert validate_against_schema("TodoWrite", TODO_INPUT_SCHEMA, args) is not None


def test_an_extra_key_on_an_item_is_tolerated():
    """Items are not strict upstream, so a harmless extra field does not fail the call."""
    args = {"todos": [{**item("x"), "id": 3}]}
    assert validate_against_schema("TodoWrite", TODO_INPUT_SCHEMA, args) is None


def test_an_empty_content_string_is_rejected():
    args = {"todos": [item("", status="pending")]}
    assert validate_against_schema("TodoWrite", TODO_INPUT_SCHEMA, args) is not None


def test_a_missing_todos_key_is_rejected():
    assert validate_against_schema("TodoWrite", TODO_INPUT_SCHEMA, {}) is not None


# ── behaviour ───────────────────────────────────────────────────────────────

def test_writing_a_list_stores_it_and_reports_upstreams_message():
    context = context_for()
    result = run({"todos": [item("Write the failing test", "in_progress")]}, context)
    assert result.content == TODO_RESULT_MESSAGE
    assert result.is_error is False

    stored = read_todos(context)
    assert [t.content for t in stored] == ["Write the failing test"]
    assert stored[0].status == "in_progress"
    assert stored[0].active_form == "Doing Write the failing test"


def test_a_second_write_replaces_the_list():
    context = context_for()
    run({"todos": [item("one")]}, context)
    run({"todos": [item("two"), item("three")]}, context)
    assert [t.content for t in read_todos(context)] == ["two", "three"]


def test_an_all_complete_list_is_cleared_from_state():
    """What was finished belongs to the transcript; state keeps only what is outstanding."""
    context = context_for()
    run({"todos": [item("a", "completed"), item("b", "completed")]}, context)
    assert read_todos(context) == ()


def test_a_partially_complete_list_is_kept():
    context = context_for()
    run({"todos": [item("a", "completed"), item("b", "in_progress")]}, context)
    assert [t.status for t in read_todos(context)] == ["completed", "in_progress"]


def test_an_empty_list_is_stored_as_empty_without_counting_as_complete():
    context = context_for()
    run({"todos": [item("a")]}, context)
    run({"todos": []}, context)
    assert read_todos(context) == ()


def test_a_subagents_list_does_not_overwrite_the_main_one():
    store = AppStateStore(AppState())
    main = tool_context_for(store)
    worker = tool_context_for(store, agent_id="tester")

    run({"todos": [item("coordinator work")]}, main)
    run({"todos": [item("tester work")]}, worker)

    assert [t.content for t in read_todos(main)] == ["coordinator work"]
    assert [t.content for t in read_todos(worker)] == ["tester work"]


def test_reading_before_any_write_gives_an_empty_list():
    assert read_todos(context_for()) == ()


def test_non_mapping_entries_are_ignored_rather_than_crashing():
    context = context_for()
    run({"todos": [item("real"), "junk", 42]}, context)
    assert [t.content for t in read_todos(context)] == ["real"]


def test_a_state_without_a_todos_field_does_not_fail_the_turn():
    """The tool is informational; an app state that cannot hold todos is not a turn failure."""

    @dataclasses.dataclass(frozen=True)
    class Bare:
        pass

    class BareStore:
        def __init__(self) -> None:
            self.state: Any = Bare()

        def get(self) -> Any:
            return self.state

        def update(self, updater: Any) -> None:
            self.state = updater(self.state)

    store = BareStore()
    context = tool_context_for(AppStateStore(AppState()))
    object.__setattr__(context, "get_app_state", store.get)
    object.__setattr__(context, "set_app_state", store.update)

    assert run({"todos": [item("x")]}, context).content == TODO_RESULT_MESSAGE
    assert read_todos(context) == ()


@pytest.mark.parametrize("count", [0, 1, 5])
def test_the_description_counts_the_items(count):
    assert tool().description({"todos": [item("x")] * count}) == f"Update {count} todo items"


def test_a_todo_item_round_trips_through_its_dictionary_form():
    original = TodoItem(content="c", status="pending", active_form="a")
    assert TodoItem.from_input(original.to_dict()) == original
