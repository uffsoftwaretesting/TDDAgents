"""
An MCP server asking the user for input, at the terminal: a form, or a URL to open.

Ported from `reference/claude-code/src/components/mcp/ElicitationDialog.tsx` ->
`ElicitationDialog`, `ElicitationFormDialog` (its fields, values, validation errors,
`commitTextField`, `validateMultiSelect`, `validateRequired`, the accept / decline buttons) and
`ElicitationURLDialog` (the prompt phase and the waiting phase); and the event it shows,
`services/mcp/elicitationHandler.ts` -> `ElicitationRequestEvent`, `ElicitationWaitingState`.

Upstream draws the whole form and moves a focus through it with the arrows, Space and Enter. The
REPL reads lines (`app/components/design_system/dialog.py`), so the form is walked field by field,
in the schema's order, one line each, and ends on Accept / Decline:

* a text field (string, number, integer) takes the typed text, checked as upstream checks it
  (`app/utils/mcp/elicitation_validation.py`), a date or date-time in natural language included
  when the small fast model is given (`query_haiku`); an invalid value is shown with its error and
  asked again;
* a boolean takes `y` / `n` (upstream's `yes` / `no` typeahead);
* an enum shows its options numbered and takes a number, or the start of a label (typeahead);
* a multi-select takes several numbers (`1,3`), checked against `minItems` / `maxItems`;
* an empty line keeps the field as it is (its default, its earlier value, or unset), so a field
  can be skipped; there is no way to unset a value once set (upstream's Backspace);
* Accept with a required field unset (`This field is required`) or a field in error asks those
  fields again, then Accept / Decline again; Esc anywhere cancels.

A URL elicitation shows the server, its message and the URL, and takes Accept / Decline. Accept
opens the browser and answers `accept` at once; the dialog then waits (`run_url_waiting`): Reopen
URL, the waiting state's action (`Skip confirmation` for an elicitation with an id, else `Continue
without waiting`), or Esc; the server's completion notification dismisses it.

Not ported: the scroll window, the date display in the user's locale, the overlay registration
and `useNotifyAfterTimeout` (a Notification hook after a while without an answer).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.components.design_system.dialog import CANCELLED, DialogIO, choice_hint, render_options
from app.utils.mcp.date_time_parser import QueryHaiku
from app.utils.mcp.elicitation_validation import (
    get_enum_label,
    get_enum_labels,
    get_enum_values,
    get_multi_select_label,
    get_multi_select_labels,
    get_multi_select_values,
    is_enum_schema,
    is_multi_select_enum_schema,
    validate_elicitation_input,
    validate_elicitation_input_async,
)
from app.utils.string_utils import plural

REQUIRED = "This field is required"
#: `figures`.
TICK, WARNING, POINTER = "✔", "⚠", "❯"
CHECKBOX_ON, CHECKBOX_OFF, RADIO_ON, RADIO_OFF = "☒", "☐", "◉", "◯"
NOT_SET = "not set"

FormValue = str | int | float | bool | list[str]
OpenBrowser = Callable[[str], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class ElicitationWaitingState:
    """`ElicitationWaitingState`: the waiting phase's action button."""

    action_label: str
    show_cancel: bool = False


@dataclass(frozen=True, slots=True)
class ElicitationRequestEvent:
    """`ElicitationRequestEvent`: what the dialog shows (the SDK's request id and signal are the caller's)."""

    server_name: str
    message: str
    requested_schema: Mapping[str, Any] | None = None
    mode: str = "form"
    url: str | None = None
    elicitation_id: str | None = None
    waiting_state: ElicitationWaitingState | None = None


def waiting_state_for(elicitation_id: str | None) -> ElicitationWaitingState | None:
    """The REPL handler's waiting state: `Skip confirmation` for an elicitation the server can complete."""
    return ElicitationWaitingState("Skip confirmation") if elicitation_id else None


def is_text_field(schema: Mapping[str, Any]) -> bool:
    """`isTextField`."""
    return schema.get("type") in ("string", "number", "integer")


@dataclass(frozen=True, slots=True)
class SchemaField:
    name: str
    schema: Mapping[str, Any]
    is_required: bool


@dataclass
class FormState:
    """The form's fields, values and errors (`formValues`, `validationErrors`)."""

    fields: list[SchemaField]
    values: dict[str, FormValue] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_schema(cls, requested_schema: Mapping[str, Any]) -> FormState:
        """The fields in the schema's order; the defaults as values; an invalid text default's error."""
        properties: Mapping[str, Any] = requested_schema.get("properties") or {}
        required = list(requested_schema.get("required") or [])
        state = cls([SchemaField(name, schema, name in required) for name, schema in properties.items()])
        for name, schema in properties.items():
            if isinstance(schema, Mapping) and schema.get("default") is not None:
                state.values[name] = schema["default"]
                if is_text_field(schema):
                    validation = validate_elicitation_input(_js_string(schema["default"]), schema)
                    if not validation.is_valid and validation.error:
                        state.errors[name] = validation.error
        return state

    def set_field(self, name: str, value: FormValue | None) -> None:
        """`setField`: a value set (clearing a `required` error) or removed."""
        if value is None:
            self.values.pop(name, None)
            return
        self.values[name] = value
        if self.errors.get(name) == REQUIRED:
            self.errors.pop(name)

    def set_error(self, name: str, error: str | None) -> None:
        """`updateValidationError`."""
        if error:
            self.errors[name] = error
        else:
            self.errors.pop(name, None)

    def has_value(self, name: str) -> bool:
        value = self.values.get(name)
        return value is not None and not (isinstance(value, list) and not value)

    def validate_required(self) -> bool:
        """`validateRequired`: every required field has a non-empty value."""
        for f in self.fields:
            if f.is_required:
                value = self.values.get(f.name)
                if value is None or value == "" or (isinstance(value, list) and not value):
                    return False
        return True

    def fields_to_fix(self) -> list[SchemaField]:
        """Accept refused: each unset required field marked, then every field to ask again, in order."""
        for f in self.fields:
            if f.is_required and f.name not in self.values:
                self.set_error(f.name, REQUIRED)
        return [f for f in self.fields
                if (f.is_required and f.name not in self.values) or f.name in self.errors]


def _js_string(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def display_value(f: SchemaField, value: Any) -> str:
    """A field's value as the form shows it: enum labels, booleans as checkboxes."""
    if value is None or (isinstance(value, list) and not value):
        return NOT_SET
    if is_multi_select_enum_schema(f.schema):
        return ", ".join(get_multi_select_label(f.schema, str(v)) for v in value)
    if is_enum_schema(f.schema):
        return get_enum_label(f.schema, str(value))
    if f.schema.get("type") == "boolean":
        return CHECKBOX_ON if value else CHECKBOX_OFF
    return _js_string(value)


def render_field(state: FormState, f: SchemaField, active: bool) -> list[str]:
    """One field: pointer, checkbox (⚠ error, ✔ set, * required), label and value; description; error."""
    if f.name in state.errors:
        checkbox = WARNING
    elif state.has_value(f.name):
        checkbox = TICK
    else:
        checkbox = "*" if f.is_required else " "
    label = str(f.schema.get("title") or f.name)
    lines = [f"{POINTER if active else ' '} {checkbox} {label}: {display_value(f, state.values.get(f.name))}"]
    if f.schema.get("description"):
        lines.append(f"      {f.schema['description']}")
    if f.name in state.errors:
        lines.append(f"      {state.errors[f.name]}")
    return lines


def render_form(server_name: str, message: str, state: FormState) -> list[str]:
    """The dialog's title, the server's message, and every field."""
    lines = [f"MCP server “{server_name}” requests your input", "", message]
    for f in state.fields:
        lines += render_field(state, f, active=False)
    return lines


def _field_instructions(f: SchemaField) -> list[str]:
    """How to answer one field, and its options when it has them."""
    keep = "Enter keeps it as is · Esc to cancel"
    if is_multi_select_enum_schema(f.schema):
        return [*render_options(get_multi_select_labels(f.schema)),
                f"Choose any of 1-{len(get_multi_select_values(f.schema))}, e.g. 1,3 · {keep}"]
    if is_enum_schema(f.schema):
        return [*render_options(get_enum_labels(f.schema)),
                f"Choose 1-{len(get_enum_values(f.schema))}, or type the start of an option · {keep}"]
    if f.schema.get("type") == "boolean":
        return [f"y/n · {keep}"]
    return [f"Type something… · {keep}"]


def _typeahead(text: str, labels: Sequence[str]) -> int | None:
    """`runTypeahead`: the first label the typed text starts."""
    lowered = text.lower()
    return next((i for i, label in enumerate(labels) if label.lower().startswith(lowered)), None)


#: A field line's reading: keep the field, or a value to commit.
KEEP = object()


def parse_field_line(f: SchemaField, line: str) -> Any:
    """
    One line for field `f`: `KEEP` (empty), the value (a boolean, an enum value, the selected
    values of a multi-select, or a text field's raw text), or `None` to ask again.
    """
    if not line.strip():
        return KEEP
    text = line.strip()
    if is_multi_select_enum_schema(f.schema):
        values = get_multi_select_values(f.schema)
        tokens = text.replace(",", " ").split()
        if not all(token.isdigit() and 1 <= int(token) <= len(values) for token in tokens):
            return None
        return [values[int(token) - 1] for token in dict.fromkeys(tokens)]
    if is_enum_schema(f.schema):
        values = get_enum_values(f.schema)
        if text.isdigit():
            index = int(text) - 1
            return values[index] if 0 <= index < len(values) else None
        match = _typeahead(text, get_enum_labels(f.schema))
        return values[match] if match is not None else None
    if f.schema.get("type") == "boolean":
        match = _typeahead(text, ["yes", "no"])
        return None if match is None else match == 0
    return line


def multi_select_issue(f: SchemaField, selected: Sequence[str]) -> str | None:
    """`validateMultiSelect`: too few (an optional field left empty is not), or too many."""
    minimum, maximum = f.schema.get("minItems"), f.schema.get("maxItems")
    if minimum is not None and len(selected) < minimum and (len(selected) > 0 or f.is_required):
        return f"Select at least {minimum} {plural(minimum, 'item')}"
    if maximum is not None and len(selected) > maximum:
        return f"Select at most {maximum} {plural(maximum, 'item')}"
    return None


async def _ask_field(io: DialogIO, state: FormState, f: SchemaField, query_haiku: QueryHaiku | None) -> bool:
    """Ask one field until it is kept or valid; `False` when the user pressed Esc."""
    while True:
        text = "\n".join([*render_field(state, f, active=True), *_field_instructions(f)])
        answer = await io.ask(text, lambda line: parse_field_line(f, line), "Not an option; try again.", CANCELLED)
        if answer is CANCELLED:
            return False
        if answer is KEEP:
            return True
        if isinstance(answer, list):
            issue = multi_select_issue(f, answer)
            state.set_field(f.name, answer or None)
            state.set_error(f.name, issue)
        elif isinstance(answer, bool) or not is_text_field(f.schema) or is_enum_schema(f.schema):
            state.set_field(f.name, answer)
            state.set_error(f.name, None)
        else:
            validation = await validate_elicitation_input_async(answer, f.schema, query_haiku)
            if validation.is_valid and validation.value is not None:
                state.set_field(f.name, validation.value)
            state.set_error(f.name, None if validation.is_valid else validation.error)
        if f.name not in state.errors:
            return True


async def run_form_dialog(
    io: DialogIO, event: ElicitationRequestEvent, query_haiku: QueryHaiku | None = None
) -> dict[str, Any]:
    """`ElicitationFormDialog`: `{action: accept, content}`, `{action: decline}` or `{action: cancel}`."""
    state = FormState.from_schema(event.requested_schema or {})
    io.write("\n".join(render_form(event.server_name, event.message, state)))
    to_ask = list(state.fields)
    while True:
        for f in to_ask:
            if not await _ask_field(io, state, f, query_haiku):
                return {"action": "cancel"}
        summary = render_form(event.server_name, event.message, state) if to_ask else []
        text = "\n".join([*summary[3:], *render_options(["Accept", "Decline"]), choice_hint(2, "cancel")])
        choice = await io.ask(text, _accept_or_decline, "Choose 1-2.", CANCELLED)
        if choice is CANCELLED:
            return {"action": "cancel"}
        if choice == "decline":
            return {"action": "decline"}
        if state.validate_required() and not state.errors:
            return {"action": "accept", "content": dict(state.values)}
        to_ask = state.fields_to_fix()


def _accept_or_decline(line: str) -> str | None:
    return {"1": "accept", "2": "decline"}.get(line.strip())


def render_url(event: ElicitationRequestEvent, title: str, extra: Sequence[str] = ()) -> list[str]:
    return [title, "", event.message, "", str(event.url or ""), *extra]


async def run_url_prompt(io: DialogIO, event: ElicitationRequestEvent, open_browser: OpenBrowser) -> dict[str, Any]:
    """
    `ElicitationURLDialog`'s prompt phase: Accept opens the URL and answers `accept` (the caller then
    runs the waiting phase); Decline answers `decline`; Esc `cancel`.
    """
    lines = render_url(event, f"MCP server “{event.server_name}” wants to open a URL")
    text = "\n".join([*lines, *render_options(["Accept", "Decline"]), choice_hint(2, "cancel")])
    choice = await io.ask(text, _accept_or_decline, "Choose 1-2.", CANCELLED)
    if choice is CANCELLED:
        return {"action": "cancel"}
    if choice == "decline":
        return {"action": "decline"}
    await open_browser(str(event.url))
    return {"action": "accept"}


async def run_url_waiting(
    io: DialogIO, event: ElicitationRequestEvent, open_browser: OpenBrowser, completed: asyncio.Event
) -> str:
    """
    `ElicitationURLDialog`'s waiting phase, until it is dismissed: `retry` / `dismiss` (the action
    button, or the server's completion), or `cancel` (Esc, or the Cancel button).
    """
    state = event.waiting_state
    show_cancel = state.show_cancel if state is not None else False
    action_label = state.action_label if state is not None else "Continue without waiting"
    finished = "retry" if show_cancel else "dismiss"
    buttons = ["Reopen URL", action_label, *(["Cancel"] if show_cancel else [])]
    lines = render_url(event, f"MCP server “{event.server_name}” — waiting for completion",
                       ["Waiting for the server to confirm completion…"])
    text = "\n".join([*lines, *render_options(buttons), choice_hint(len(buttons), "cancel")])

    def parse(line: str) -> str | None:
        stripped = line.strip()
        return {str(i): name for i, name in enumerate(("open", "action", "cancel")[:len(buttons)], start=1)}.get(
            stripped)

    while True:
        if completed.is_set():
            return finished
        asking = asyncio.ensure_future(io.ask(text, parse, f"Choose 1-{len(buttons)}.", CANCELLED))
        done = asyncio.ensure_future(completed.wait())
        await asyncio.wait({asking, done}, return_when=asyncio.FIRST_COMPLETED)
        if not asking.done():
            asking.cancel()
            await asyncio.gather(asking, return_exceptions=True)
            return finished
        done.cancel()
        choice = asking.result()
        if choice is CANCELLED or choice == "cancel":
            return "cancel"
        if choice == "action":
            return finished
        await open_browser(str(event.url))


__all__ = [
    "REQUIRED",
    "ElicitationRequestEvent",
    "ElicitationWaitingState",
    "FormState",
    "SchemaField",
    "display_value",
    "is_text_field",
    "multi_select_issue",
    "parse_field_line",
    "render_field",
    "render_form",
    "run_form_dialog",
    "run_url_prompt",
    "run_url_waiting",
    "waiting_state_for",
]
