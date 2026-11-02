"""
An MCP server's elicitation at the terminal: the form and URL dialogs
(`app/components/mcp/elicitation_dialog.py`) against a scripted dialog, and the REPL as the
session's `ask_host` (`app/screens/repl.py`), with the hooks still first (`SessionMcp.elicit`).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.cli.session_mcp import SessionMcp
from app.components.design_system.dialog import (
    CANCELLED,
    choice_hint,
    parse_choice,
    render_options,
    select,
)
from app.components.mcp.elicitation_dialog import (
    REQUIRED,
    ElicitationRequestEvent,
    ElicitationWaitingState,
    FormState,
    SchemaField,
    display_value,
    is_text_field,
    multi_select_issue,
    parse_field_line,
    render_field,
    run_form_dialog,
    run_url_prompt,
    run_url_waiting,
    waiting_state_for,
)
from app.screens.repl import Repl
from tests.dialog_fakes import ESC, FakeDialog
from tests.test_repl import FakeIO

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "title": "Your name", "description": "As on your badge", "minLength": 2},
        "age": {"type": "integer", "minimum": 0, "maximum": 150},
        "color": {"type": "string", "oneOf": [{"const": "r", "title": "Red"}, {"const": "g", "title": "Green"}]},
        "tags": {"type": "array", "items": {"enum": ["a", "b", "c"]}, "minItems": 2, "maxItems": 2},
        "subscribe": {"type": "boolean", "default": True},
    },
    "required": ["name", "color"],
}


def event(schema: Any = SCHEMA, **kwargs: Any) -> ElicitationRequestEvent:
    return ElicitationRequestEvent("srv", "Tell me about you", schema, **kwargs)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# ── the dialog primitives ────────────────────────────────────────────────────

def test_choice_parsing_and_rendering() -> None:
    assert [parse_choice(3, line) for line in ("1", " 3 ", "0", "4", "x", "")] == [0, 2, None, None, None, None]
    assert render_options(["A", "B"], ["first", None]) == ["  1. A — first", "  2. B"]
    assert render_options(["A"]) == ["  1. A"]
    assert choice_hint(2, "go back") == "Choose 1-2 · Esc to go back"
    dialog = FakeDialog("9", "2")
    assert run(select(dialog, ["Title"], ["A", "B"], "cancel")) == 1
    assert dialog.out == ["Title\n  1. A\n  2. B\nChoose 1-2 · Esc to cancel", "Choose 1-2."]
    assert run(select(FakeDialog(ESC), [], ["A"], "cancel")) is CANCELLED
    assert repr(CANCELLED) == "CANCELLED"


# ── the form, piece by piece ─────────────────────────────────────────────────

def test_form_state_from_schema() -> None:
    state = FormState.from_schema({
        "properties": {"n": {"type": "integer", "default": 500, "maximum": 10}, "s": {"type": "string", "default": "x"},
                       "b": {"type": "boolean", "default": False}, "plain": {"type": "string"}},
        "required": ["plain"]})
    assert [(f.name, f.is_required) for f in state.fields] == [
        ("n", False), ("s", False), ("b", False), ("plain", True)]
    assert state.values == {"n": 500, "s": "x", "b": False}
    assert state.errors == {"n": "Must be an integer <= 10"}
    assert FormState.from_schema({}).fields == []


def test_form_state_values_errors_and_required() -> None:
    state = FormState([SchemaField("a", {"type": "string"}, True), SchemaField("b", {"type": "array"}, True),
                       SchemaField("c", {"type": "string"}, False)])
    assert not state.validate_required()
    assert [f.name for f in state.fields_to_fix()] == ["a", "b"]
    assert state.errors == {"a": REQUIRED, "b": REQUIRED}
    state.set_field("a", "x")
    assert "a" not in state.errors and state.values["a"] == "x"
    state.set_error("c", "bad")
    state.set_field("c", "y")
    assert state.errors["c"] == "bad"  # only a `required` error is cleared by a value
    state.set_field("b", [])
    assert not state.has_value("b") and not state.validate_required()
    state.set_field("b", ["p"])
    state.set_field("a", "")
    assert not state.validate_required()
    state.set_field("a", "z")
    assert state.validate_required()
    state.set_field("a", None)
    assert "a" not in state.values
    state.set_error("c", None)
    assert "c" not in state.errors


def test_display_values_and_field_rendering() -> None:
    color = SchemaField("color", SCHEMA["properties"]["color"], True)
    tags = SchemaField("tags", SCHEMA["properties"]["tags"], False)
    sub = SchemaField("subscribe", SCHEMA["properties"]["subscribe"], False)
    age = SchemaField("age", SCHEMA["properties"]["age"], False)
    assert display_value(color, "g") == "Green" and display_value(color, None) == "not set"
    assert display_value(tags, ["a", "c"]) == "a, c" and display_value(tags, []) == "not set"
    assert display_value(sub, True) == "☒" and display_value(sub, False) == "☐"
    assert display_value(age, 3.0) == "3" and display_value(age, 2.5) == "2.5"
    state = FormState([color, age], values={"age": 3}, errors={})
    assert render_field(state, color, active=True) == ["❯ * color: not set"]
    assert render_field(state, age, active=False) == ["  ✔ age: 3"]
    state.errors["age"] = "too old"
    assert render_field(state, age, active=False) == ["  ⚠ age: 3", "      too old"]
    name = SchemaField("name", SCHEMA["properties"]["name"], False)
    assert render_field(state, name, active=False) == ["    Your name: not set", "      As on your badge"]


@pytest.mark.parametrize("field_name, line, expected", [
    ("color", "2", "g"), ("color", "gr", "g"), ("color", "3", None), ("color", "blue", None), ("color", "0", None),
    ("tags", "1, 3", ["a", "c"]), ("tags", "3 3 1", ["c", "a"]), ("tags", "4", None), ("tags", "x", None),
    ("subscribe", "y", True), ("subscribe", "NO", False), ("subscribe", "maybe", None),
    ("name", " Ann ", " Ann "), ("age", "42", "42"),
])
def test_parse_field_line(field_name: str, line: str, expected: Any) -> None:
    f = SchemaField(field_name, SCHEMA["properties"][field_name], False)
    assert parse_field_line(f, line) == expected


def test_an_empty_line_keeps_the_field() -> None:
    from app.components.mcp.elicitation_dialog import KEEP

    assert parse_field_line(SchemaField("x", {"type": "string"}, False), "   ") is KEEP


def test_multi_select_issue() -> None:
    schema = {"type": "array", "items": {"enum": ["a", "b", "c"]}, "minItems": 2, "maxItems": 2}
    assert multi_select_issue(SchemaField("t", schema, False), []) is None
    assert multi_select_issue(SchemaField("t", schema, True), []) == "Select at least 2 items"
    assert multi_select_issue(SchemaField("t", schema, False), ["a"]) == "Select at least 2 items"
    assert multi_select_issue(SchemaField("t", schema, False), ["a", "b", "c"]) == "Select at most 2 items"
    assert multi_select_issue(SchemaField("t", {**schema, "minItems": 1, "maxItems": 1}, False),
                              ["a", "b"]) == "Select at most 1 item"
    assert multi_select_issue(SchemaField("t", schema, False), ["a", "b"]) is None


def test_is_text_field() -> None:
    assert all(is_text_field({"type": t}) for t in ("string", "number", "integer"))
    assert not is_text_field({"type": "boolean"}) and not is_text_field({"type": "array"})


# ── the form dialog ──────────────────────────────────────────────────────────

def test_a_form_is_walked_field_by_field_and_accepted() -> None:
    dialog = FakeDialog("A", "Ann", "abc", "200", "42", "blue", "red", "1", "1,2", "", "1")
    response = run(run_form_dialog(dialog, event()))
    assert response == {"action": "accept",
                        "content": {"subscribe": True, "name": "Ann", "age": 42, "color": "r", "tags": ["a", "b"]}}
    text = dialog.text()
    assert dialog.out[0].startswith("MCP server “srv” requests your input\n\nTell me about you\n")
    assert "Must be at least 2 characters" in text and "Must be an integer between 0 and 150" in text
    assert "Select at least 2 items" in text and "Not an option; try again." in text
    assert "  1. Red\n  2. Green\nChoose 1-2, or type the start of an option" in text
    assert "y/n · Enter keeps it as is · Esc to cancel" in text
    assert dialog.asked[-1].endswith("  1. Accept\n  2. Decline\nChoose 1-2 · Esc to cancel")


def test_accept_with_a_required_field_unset_asks_it_again() -> None:
    schema = {"properties": {"name": {"type": "string"}, "note": {"type": "string"}}, "required": ["name"]}
    dialog = FakeDialog("", "", "1", "Bob", "1")
    response = run(run_form_dialog(dialog, event(schema)))
    assert response == {"action": "accept", "content": {"name": "Bob"}}
    assert REQUIRED in dialog.asked[3]  # the required field again, with its error


def test_an_invalid_default_must_be_fixed_before_accepting() -> None:
    schema = {"properties": {"n": {"type": "integer", "default": 99, "maximum": 10}}}
    dialog = FakeDialog("", "1", "5", "1")
    assert run(run_form_dialog(dialog, event(schema))) == {"action": "accept", "content": {"n": 5}}
    assert "Must be an integer <= 10" in dialog.asked[0]


def test_decline_and_cancel() -> None:
    schema = {"properties": {"x": {"type": "string"}}}
    assert run(run_form_dialog(FakeDialog("", "2"), event(schema))) == {"action": "decline"}
    assert run(run_form_dialog(FakeDialog(ESC), event(schema))) == {"action": "cancel"}
    assert run(run_form_dialog(FakeDialog("", ESC), event(schema))) == {"action": "cancel"}
    assert run(run_form_dialog(FakeDialog("", "x", "1"), event(schema))) == {"action": "accept", "content": {}}


def test_a_form_without_fields_goes_straight_to_accept() -> None:
    dialog = FakeDialog("1")
    assert run(run_form_dialog(dialog, event({"properties": {}}))) == {"action": "accept", "content": {}}
    assert dialog.asked == ["  1. Accept\n  2. Decline\nChoose 1-2 · Esc to cancel"]
    assert run(run_form_dialog(FakeDialog("1"), event(None))) == {"action": "accept", "content": {}}


def test_a_natural_language_date_is_parsed_by_the_model() -> None:
    async def haiku(system: str, user: str) -> str:
        return "2026-11-03"

    schema = {"properties": {"when": {"type": "string", "format": "date"}}}
    dialog = FakeDialog("tomorrow", "1")
    assert run(run_form_dialog(dialog, event(schema), haiku)) == {
        "action": "accept", "content": {"when": "2026-11-03"}}
    without = FakeDialog("tomorrow", "2026-11-04", "1")
    assert run(run_form_dialog(without, event(schema))) == {"action": "accept", "content": {"when": "2026-11-04"}}
    assert "Must be a valid date, e.g. 2024-03-15, today, next Monday" in without.text()


# ── the URL dialog ───────────────────────────────────────────────────────────

class Browser:
    def __init__(self) -> None:
        self.opened: list[str] = []

    async def __call__(self, url: str) -> bool:
        self.opened.append(url)
        return True


URL_EVENT = ElicitationRequestEvent("srv", "Log in please", None, "url", "https://auth.example/x", "e1",
                                    waiting_state_for("e1"))


def test_url_prompt() -> None:
    browser = Browser()
    dialog = FakeDialog("1")
    assert run(run_url_prompt(dialog, URL_EVENT, browser)) == {"action": "accept"}
    assert browser.opened == ["https://auth.example/x"]
    assert dialog.asked[0] == ("MCP server “srv” wants to open a URL\n\nLog in please\n\nhttps://auth.example/x\n"
                               "  1. Accept\n  2. Decline\nChoose 1-2 · Esc to cancel")
    assert run(run_url_prompt(FakeDialog("2"), URL_EVENT, browser)) == {"action": "decline"}
    assert run(run_url_prompt(FakeDialog(ESC), URL_EVENT, browser)) == {"action": "cancel"}
    assert browser.opened == ["https://auth.example/x"]


def test_waiting_state() -> None:
    assert waiting_state_for("e1") == ElicitationWaitingState("Skip confirmation")
    assert waiting_state_for(None) is None


def test_url_waiting_phase() -> None:
    browser = Browser()
    dialog = FakeDialog("1", "2")
    assert run(run_url_waiting(dialog, URL_EVENT, browser, asyncio.Event())) == "dismiss"
    assert browser.opened == ["https://auth.example/x"]
    assert dialog.asked[0] == ("MCP server “srv” — waiting for completion\n\nLog in please\n\nhttps://auth.example/x\n"
                               "Waiting for the server to confirm completion…\n  1. Reopen URL\n"
                               "  2. Skip confirmation\nChoose 1-2 · Esc to cancel")
    assert run(run_url_waiting(FakeDialog(ESC), URL_EVENT, browser, asyncio.Event())) == "cancel"
    plain = ElicitationRequestEvent("srv", "m", None, "url", "u")
    no_id = FakeDialog("3", "2")
    assert run(run_url_waiting(no_id, plain, browser, asyncio.Event())) == "dismiss"
    assert "  2. Continue without waiting" in no_id.asked[0]
    retry = ElicitationRequestEvent("srv", "m", None, "url", "u", "e", ElicitationWaitingState("Retry now", True))
    assert run(run_url_waiting(FakeDialog("3"), retry, browser, asyncio.Event())) == "cancel"
    assert run(run_url_waiting(FakeDialog("2"), retry, browser, asyncio.Event())) == "retry"
    done = asyncio.Event()
    done.set()
    assert run(run_url_waiting(FakeDialog(), URL_EVENT, browser, done)) == "dismiss"


def test_the_completion_notification_dismisses_the_waiting_dialog() -> None:
    async def go() -> tuple[str, FakeDialog]:
        completed = asyncio.Event()
        dialog = FakeDialog()  # the user types nothing
        waiting = asyncio.create_task(run_url_waiting(dialog, URL_EVENT, Browser(), completed))
        await asyncio.sleep(0.01)
        completed.set()
        return await waiting, dialog

    result, dialog = run(go())
    assert result == "dismiss" and len(dialog.asked) == 1


# ── the REPL as the session's elicitation host ───────────────────────────────

class Engine:
    def __init__(self) -> None:
        self.interrupted: list[Any] = []

    def interrupt(self, *args: Any) -> None:
        self.interrupted.append(args)


def fake_run(mcp: Any) -> Any:
    return SimpleNamespace(on_message=None, ctx=SimpleNamespace(), mcp=mcp, engine=Engine())


async def type_when(io: FakeIO, marker: str, line: str) -> None:
    while marker not in io.text():
        await asyncio.sleep(0.005)
    await asyncio.sleep(0)
    io.lines.put_nowait(line)


async def feed(repl: Repl, io: FakeIO) -> None:
    while True:
        line = await io.lines.get()
        if line is None:
            return
        await repl.handle_line(line)


def test_the_repl_becomes_the_sessions_elicitation_host() -> None:
    mcp = SessionMcp(manager=SimpleNamespace(clients=[], tools=[]), cwd=".")
    repl = Repl(fake_run(mcp), FakeIO())
    assert mcp.ask_host == repl.ask_elicitation
    assert mcp.emit_elicitation_complete == repl.elicitation_complete
    bare: Any = SimpleNamespace(on_message=None, ctx=SimpleNamespace())
    assert Repl(bare, FakeIO()).query_haiku is None


def test_a_form_elicitation_through_the_session_hooks_then_the_repl() -> None:
    from mcp.types import ElicitRequestFormParams

    async def go() -> tuple[Any, FakeIO]:
        io = FakeIO()
        mcp = SessionMcp(manager=SimpleNamespace(clients=[], tools=[]), cwd=".")
        repl = Repl(fake_run(mcp), io)
        reader = asyncio.create_task(feed(repl, io))
        typist = asyncio.create_task(type_when(io, "x: not set", "hello"))
        accept = asyncio.create_task(type_when(io, "1. Accept", "1"))
        params = ElicitRequestFormParams.model_validate({"message": "Need x", "requestedSchema": {
            "type": "object", "properties": {"x": {"type": "string"}}}})
        result = await mcp.elicit("srv", params)
        io.lines.put_nowait(None)
        await asyncio.gather(reader, typist, accept)
        return result, io

    result, io = run(go())
    assert result.action == "accept" and result.content == {"x": "hello"}
    assert "MCP server “srv” requests your input" in io.text()


def test_esc_cancels_the_elicitation_without_aborting_the_turn() -> None:
    async def go() -> tuple[Any, Any]:
        io = FakeIO()
        run_ = fake_run(SessionMcp(manager=SimpleNamespace(clients=[], tools=[]), cwd="."))
        repl = Repl(run_, io)
        repl.turn = asyncio.create_task(asyncio.sleep(10))  # a turn in flight
        asking = asyncio.create_task(repl.ask_elicitation("srv", "m", {"properties": {"x": {"type": "string"}}},
                                                          "form", None, None))
        while repl.pending is None:
            await asyncio.sleep(0.005)
        repl.interrupt()
        result = await asking
        repl.turn.cancel()
        return result, run_.engine.interrupted

    result, interrupted = run(go())
    assert result == {"action": "cancel"} and interrupted == []


def test_an_accepted_url_answers_at_once_then_waits_for_the_completion() -> None:
    async def go() -> tuple[Any, Repl, list[str], bool]:
        io = FakeIO()
        repl = Repl(fake_run(SessionMcp(manager=SimpleNamespace(clients=[], tools=[]), cwd=".")), io)
        browser = Browser()
        repl.open_browser = browser
        reader = asyncio.create_task(feed(repl, io))
        typist = asyncio.create_task(type_when(io, "wants to open a URL", "1"))
        result = await repl.ask_elicitation("srv", "Log in", None, "url", "https://a.example", "e9")
        await typist
        while "waiting for completion" not in io.text():
            await asyncio.sleep(0.005)
        held = repl._asking.locked()
        repl.elicitation_complete("srv", "unknown")  # ignored
        repl.elicitation_complete("srv", "e9")
        while repl._asking.locked():
            await asyncio.sleep(0.005)
        io.lines.put_nowait(None)
        await reader
        return result, repl, browser.opened, held

    result, repl, opened, held = run(go())
    assert result == {"action": "accept"} and opened == ["https://a.example"] and held
    assert repl._elicitation_waits == {} and repl.pending is None


def test_a_declined_url_releases_the_screen_and_forgets_the_wait() -> None:
    async def go() -> tuple[Any, Repl]:
        io = FakeIO()
        repl = Repl(fake_run(None), io)
        reader = asyncio.create_task(feed(repl, io))
        typist = asyncio.create_task(type_when(io, "wants to open a URL", "2"))
        result = await repl.ask_elicitation("srv", "m", None, "url", "https://a.example", "e1")
        await typist
        io.lines.put_nowait(None)
        await reader
        return result, repl

    result, repl = run(go())
    assert result == {"action": "decline"} and not repl._asking.locked() and repl._elicitation_waits == {}


def test_a_form_date_uses_the_runs_small_fast_model(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[Any] = []

    def query_haiku_for(ctx: Any) -> Any:
        built.append(ctx)

        async def query(system: str, user: str) -> str:
            return "2026-11-03"

        return query

    monkeypatch.setattr("app.services.api.claude.query_haiku_for", query_haiku_for)

    async def go() -> Any:
        io = FakeIO()
        repl = Repl(fake_run(None), io)
        reader = asyncio.create_task(feed(repl, io))
        typists = [asyncio.create_task(type_when(io, "when: not set", "tomorrow")),
                   asyncio.create_task(type_when(io, "1. Accept", "1"))]
        result = await repl.ask_elicitation(
            "srv", "m", {"properties": {"when": {"type": "string", "format": "date"}}}, "form", None, None)
        await asyncio.gather(*typists)
        await repl._query_haiku("s", "u")  # built once, then reused
        io.lines.put_nowait(None)
        await reader
        return result

    assert run(go()) == {"action": "accept", "content": {"when": "2026-11-03"}}
    assert len(built) == 1


def test_open_browser_defaults_to_the_system_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []

    async def open_browser(url: str) -> bool:
        opened.append(url)
        return False

    monkeypatch.setattr("app.utils.browser.open_browser", open_browser)
    repl = Repl(fake_run(None), FakeIO())
    assert run(repl._open_browser("https://x")) is False and opened == ["https://x"]
