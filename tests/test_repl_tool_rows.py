"""
The REPL's tool rows, their results and the status line (2026-11-14): upstream's
`AssistantToolUseMessage`, `UserToolResultMessage`, `FallbackToolUseErrorMessage` and `SpinnerWithVerb`.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from app.components.fallback_tool_use_error_message import MAX_RENDERED_LINES, fallback_tool_use_error_text
from app.components.messages.assistant_tool_use_message import TOOL_USE_DOT, render_tool_use_row
from app.components.messages.collapsed_read_search_content import CollapsedReadSearchRenderer
from app.components.messages.user_tool_result_message.user_tool_result_message import (
    INTERRUPTED_BY_USER,
    TOOL_USE_REJECTED,
    tool_result_text,
)
from app.components.spinner.spinner import (
    SHOW_TOKENS_AFTER_MS,
    pick_spinner_verb,
    spinner_message,
    spinner_status_line,
)
from app.constants.spinner_verbs import SPINNER_VERBS, get_spinner_verbs
from app.services.tools.tool_execution import CANCEL_MESSAGE
from app.tool import ToolResult, build_tool
from app.utils.messages import (
    INTERRUPT_MESSAGE_FOR_TOOL_USE,
    REJECT_MESSAGE,
    REJECT_MESSAGE_WITH_REASON_PREFIX,
    extract_tag,
)


def _tool(name: str = "Probe", **kwargs: Any) -> Any:
    async def call(args: dict[str, Any], ctx: Any) -> ToolResult:
        return ToolResult(content="ok")

    return build_tool(name=name, prompt="p", call=call, **kwargs)


def _call(name: str = "Probe", args: dict[str, Any] | None = None, call_id: str = "c1") -> dict[str, Any]:
    return {"name": name, "args": args or {}, "id": call_id}


# ── extractTag ────────────────────────────────────────────────────────────────

def test_extract_tag_takes_the_first_top_level_element():
    assert extract_tag("<tool_use_error>Cancelled: x</tool_use_error>", "tool_use_error") == "Cancelled: x"
    assert extract_tag("a <T k='v'>in</t> b", "t") == "in"  # attributes, any case
    assert extract_tag("<x><x>inner</x></x>", "x") == "<x>inner"
    assert extract_tag("<x></x>", "x") is None  # empty content
    assert extract_tag("   ", "x") is None and extract_tag("<x>a</x>", " ") is None
    assert extract_tag("no tag here", "x") is None
    assert extract_tag("</x><x>after</x>", "x") is None  # nested one level below a closed one: skipped
    assert extract_tag("<a.b>dot</a.b>", "a.b") == "dot"  # the name is escaped
    assert extract_tag("<axb>no</axb>", "a.b") is None


# ── FallbackToolUseErrorMessage ───────────────────────────────────────────────

def test_the_fallback_error_text():
    assert fallback_tool_use_error_text("<tool_use_error>Cancelled: parallel tool call Bash(x) errored"
                                        "</tool_use_error>", False) == "Cancelled: parallel tool call Bash(x) errored"
    assert fallback_tool_use_error_text("Error: boom", False) == "Error: boom"
    assert fallback_tool_use_error_text("  boom  ", False) == "Error: boom"
    assert fallback_tool_use_error_text("<error>inside</error>", False) == "Error: inside"
    assert fallback_tool_use_error_text("x <sandbox_violations>v\nw</sandbox_violations>", False) == "Error: x"
    assert fallback_tool_use_error_text("InputValidationError: bad", False) == "Invalid tool parameters"
    assert fallback_tool_use_error_text("InputValidationError: bad", True) == "Error: InputValidationError: bad"
    assert fallback_tool_use_error_text([{"type": "text"}], False) == "Tool execution failed"


def test_the_fallback_error_keeps_ten_lines_unless_verbose():
    many = "\n".join(f"line {i}" for i in range(MAX_RENDERED_LINES + 2))
    text = fallback_tool_use_error_text(many, False)
    assert text.split("\n")[:MAX_RENDERED_LINES] == ["Error: line 0", *(f"line {i}" for i in range(1, 10))]
    assert text.split("\n")[-1] == "… +2 lines"
    one_more = "\n".join(f"l{i}" for i in range(MAX_RENDERED_LINES + 1))
    assert fallback_tool_use_error_text(one_more, False).endswith("\n… +1 line")
    exact = "\n".join(f"l{i}" for i in range(MAX_RENDERED_LINES))
    assert "…" not in fallback_tool_use_error_text(exact, False)
    assert fallback_tool_use_error_text(many, True) == "Error: " + many


# ── AssistantToolUseMessage ──────────────────────────────────────────────────

def test_the_tool_row_is_the_name_and_the_tool_s_rendering():
    tool = _tool(user_facing_name=lambda args: "Write",
                 render_tool_use_message=lambda args, verbose, context=None: f"{args['p']}{'!' if verbose else ''}")
    assert render_tool_use_row(_call(args={"p": "a.ts"}), [tool]) == f"{TOOL_USE_DOT} Write(a.ts)"
    assert render_tool_use_row(_call(args={"p": "a.ts"}), [tool], verbose=True) == f"{TOOL_USE_DOT} Write(a.ts!)"
    empty = _tool(render_tool_use_message=lambda args, verbose, context=None: "")
    assert render_tool_use_row(_call(), [empty]) == f"{TOOL_USE_DOT} Probe"  # '': the name alone
    hidden = _tool(render_tool_use_message=lambda args, verbose, context=None: None)
    assert render_tool_use_row(_call(), [hidden]) is None  # null: no row
    nameless = _tool(user_facing_name=lambda args: "")
    assert render_tool_use_row(_call(), [nameless]) is None
    assert render_tool_use_row(_call("Gone"), [tool]) == f"{TOOL_USE_DOT} Gone"  # unknown tool: its raw name


def test_the_tool_row_gets_the_context():
    seen: list[Any] = []

    def render(args: dict[str, Any], verbose: bool, context: Any = None) -> str:
        seen.append(context)
        return "x"

    tool = _tool(render_tool_use_message=render)
    render_tool_use_row(_call(), [tool], context="ctx")
    assert seen == ["ctx"]


# ── UserToolResultMessage ────────────────────────────────────────────────────

def _result(content: Any, *, error: bool = False, output: Any = None, call_id: str = "c1") -> ToolMessage:
    kwargs: dict[str, Any] = {"is_error": True} if error else {}
    if output is not None:
        kwargs["tool_use_result"] = output
    return ToolMessage(content=content, tool_call_id=call_id, status="error" if error else "success",
                       additional_kwargs=kwargs)


def test_the_result_dispatch():
    tool = _tool(render_tool_result_message=lambda output, verbose: f"rendered {output['n']}{'+' if verbose else ''}")
    call = _call()
    assert tool_result_text(_result(CANCEL_MESSAGE, error=True), call, [tool]) == INTERRUPTED_BY_USER
    assert tool_result_text(_result(REJECT_MESSAGE, error=True), call, [tool]) == INTERRUPTED_BY_USER
    assert tool_result_text(_result(INTERRUPT_MESSAGE_FOR_TOOL_USE, error=True), call, [tool]) == INTERRUPTED_BY_USER
    assert tool_result_text(_result(f"x {INTERRUPT_MESSAGE_FOR_TOOL_USE}", error=True), call, [tool]) == "Interrupted"
    assert tool_result_text(_result(REJECT_MESSAGE_WITH_REASON_PREFIX + "do Y", error=True), call, [tool]) == (
        TOOL_USE_REJECTED)
    assert tool_result_text(_result(REJECT_MESSAGE_WITH_REASON_PREFIX + "do Y"), call, [tool]) == (
        REJECT_MESSAGE_WITH_REASON_PREFIX.split("\n")[0])  # not an error: the success path, its first line
    assert tool_result_text(_result("<tool_use_error>Error: no</tool_use_error>", error=True), call, [tool]) == (
        "Error: no")
    assert tool_result_text(_result("ok", output={"n": 3}), call, [tool]) == "rendered 3"
    assert tool_result_text(_result("ok", output={"n": 3}), call, [tool], verbose=True) == "rendered 3+"


def test_the_reject_prefix_is_not_reject_message():
    # Why a rejection with a reason reaches `UserToolErrorMessage` and not `UserToolRejectMessage`.
    assert not REJECT_MESSAGE_WITH_REASON_PREFIX.startswith(REJECT_MESSAGE)


def test_a_result_without_a_rendering_shows_its_first_line():
    plain = _tool()
    call = _call()
    assert tool_result_text(_result("first\nsecond"), call, [plain]) == "first"
    assert tool_result_text(_result("   "), call, [plain]) == "(no output)"
    assert tool_result_text(_result([{"type": "text", "text": "block one"}, {"type": "image"}]), call, [plain]) == (
        "block one")
    assert tool_result_text(_result("x" * 300), call, [plain]) == "x" * 200
    silent = _tool(render_tool_result_message=lambda output, verbose: None)
    assert tool_result_text(_result("shown", output={"a": 1}), call, [silent]) == "shown"
    empty = _tool(render_tool_result_message=lambda output, verbose: "")
    assert tool_result_text(_result("hidden", output={"a": 1}), call, [empty]) is None
    assert tool_result_text(_result("no call"), None, [plain]) == "no call"


# ── the renderer: a call prints with its result ─────────────────────────────

def _renderer(tools: list[Any], *, verbose: bool = False) -> CollapsedReadSearchRenderer:
    return CollapsedReadSearchRenderer(lambda: tools, lambda: None, lambda event: None, verbose=verbose)


def test_each_call_prints_with_its_result_in_call_order_of_results():
    tool = _tool(user_facing_name=lambda args: "Write",
                 render_tool_use_message=lambda args, verbose, context=None: args["p"],
                 render_tool_result_message=lambda output, verbose: f"Wrote {output['n']} lines")
    renderer = _renderer([tool])
    message = AIMessage(content="", tool_calls=[_call("Probe", {"p": "a.json"}, "1"),
                                                _call("Probe", {"p": "b.json"}, "2")])
    assert renderer.feed(message) == []  # nothing prints until a result arrives
    assert [c["id"] for c in renderer.running_calls()] == ["1", "2"]
    assert renderer.feed(_result("ok", output={"n": 3}, call_id="2")) == [
        f"{TOOL_USE_DOT} Write(b.json)", "  ⎿  Wrote 3 lines"]
    assert [c["id"] for c in renderer.running_calls()] == ["1"]
    assert renderer.feed(_result("<tool_use_error>Cancelled: parallel tool call x errored</tool_use_error>",
                                 error=True, call_id="1")) == [
        f"{TOOL_USE_DOT} Write(a.json)", "  ⎿  Cancelled: parallel tool call x errored"]
    assert renderer.running_calls() == []


def test_a_call_without_a_result_prints_its_row_at_the_end_of_the_turn():
    renderer = _renderer([_tool(render_tool_use_message=lambda args, verbose, context=None: "")])
    renderer.feed(AIMessage(content="", tool_calls=[_call()]))
    assert renderer.feed(object()) == []  # an unrelated event keeps the call waiting
    assert renderer.running_calls() != []
    assert renderer.flush() == [f"{TOOL_USE_DOT} Probe"]
    assert renderer.running_calls() == [] and renderer.flush() == []


def test_a_result_for_no_known_call_still_reads():
    renderer = _renderer([_tool()])
    assert renderer.feed(_result("orphan", call_id="zz")) == ["  ⎿  orphan"]


def test_a_multi_line_result_continues_under_the_gutter():
    tool = _tool(render_tool_result_message=lambda output, verbose: "one\ntwo")
    renderer = _renderer([tool])
    renderer.feed(AIMessage(content="", tool_calls=[_call()]))
    assert renderer.feed(_result("x", output={"a": 1})) == [
        f"{TOOL_USE_DOT} Probe", "  ⎿  one", "     two"]


# ── the status line ──────────────────────────────────────────────────────────

def test_the_status_line():
    assert spinner_message("Running npm install", "Writing tests", "Baking") == "Running npm install…"
    assert spinner_message(None, "Writing tests", "Baking") == "Writing tests…"
    assert spinner_message(None, None, "Baking") == "Baking…"
    assert spinner_status_line("Baking…", 12_000, 500) == "⛨ Baking… (esc to interrupt)"  # before 30 s: hint only
    assert spinner_status_line("Baking…", SHOW_TOKENS_AFTER_MS, 500) == "⛨ Baking… (esc to interrupt)"
    assert spinner_status_line("Baking…", 43_000, 1234) == "⛨ Baking… (43s · ↓ 1.2k tokens · esc to interrupt)"
    assert spinner_status_line("Baking…", 43_000, 0) == "⛨ Baking… (43s · esc to interrupt)"  # no tokens yet
    assert spinner_status_line("Baking…", 2_000, 7, verbose=True) == "⛨ Baking… (2s · ↓ 7 tokens · esc to interrupt)"


def test_the_spinner_verbs():
    assert len(SPINNER_VERBS) == 187 and SPINNER_VERBS[0] == "Accomplishing" and "Beboppin'" in SPINNER_VERBS
    assert get_spinner_verbs() == list(SPINNER_VERBS)
    assert get_spinner_verbs({"spinnerVerbs": {"mode": "replace", "verbs": ["Testing"]}}) == ["Testing"]
    assert get_spinner_verbs({"spinnerVerbs": {"mode": "replace", "verbs": []}}) == list(SPINNER_VERBS)
    assert get_spinner_verbs({"spinnerVerbs": {"mode": "append", "verbs": ["Testing"]}})[-1] == "Testing"
    assert get_spinner_verbs({"spinnerVerbs": "nonsense"}) == list(SPINNER_VERBS)
    assert pick_spinner_verb(["A", "B"], choice=lambda verbs: verbs[1]) == "B"
    assert pick_spinner_verb([]) == "Working"


# ── the REPL's status line ───────────────────────────────────────────────────

class _StatusIO:
    def __init__(self) -> None:
        self.status: Any = None
        self.out: list[str] = []

    def set_status(self, status: Any) -> None:
        self.status = status

    async def read_line(self, prompt: str) -> str | None:
        return None

    def write(self, text: str) -> None:
        self.out.append(text)


class _Tracker:
    def __init__(self) -> None:
        self.output = 100

    def total_usage(self) -> dict[str, int]:
        return {"output_tokens": self.output}


def test_the_repl_s_status_line_follows_the_turn():
    import asyncio
    from types import SimpleNamespace

    from app.screens.repl import Repl
    from app.tools.todo_write_tool.todo_write_tool import TodoItem

    bash = _tool("Bash", user_facing_name=lambda args: "Bash",
                 get_activity_description=lambda args, context=None: f"Running {args['command']}")
    todos: dict[str, Any] = {"main": ()}
    tracker = _Tracker()
    ctx = SimpleNamespace(tools=[bash], cost_tracker=tracker,
                          get_app_state=lambda: SimpleNamespace(todos=todos))
    io = _StatusIO()

    async def go() -> None:
        repl = Repl(SimpleNamespace(on_message=None, ctx=ctx), io)  # type: ignore[arg-type]
        assert io.status == repl.status_line  # the terminal asks the REPL
        now = [10.0]
        repl.clock = lambda: now[0]
        assert repl.status_line() is None  # no turn
        gate = asyncio.Event()

        async def turn() -> None:
            await gate.wait()

        repl._spinner_verbs = lambda: ["Baking"]  # type: ignore[method-assign]
        repl._start(turn())
        await asyncio.sleep(0)
        assert repl.status_line() == "⛨ Baking… (esc to interrupt)"
        todos["main"] = (TodoItem("t", "in_progress", "Writing the failing test"),)
        assert repl.status_line() == "⛨ Writing the failing test… (esc to interrupt)"
        repl.renderer.feed(AIMessage(content="", tool_calls=[_call("Bash", {"command": "npm install"})]))
        now[0] += 43
        tracker.output = 1334
        assert repl.status_line() == "⛨ Running npm install… (43s · ↓ 1.2k tokens · esc to interrupt)"
        repl.renderer.feed(AIMessage(content="", tool_calls=[_call("Gone", {}, "c2")]))
        repl.renderer.feed(_result("ok", call_id="c1"))
        assert repl.status_line() == "⛨ Gone… (43s · ↓ 1.2k tokens · esc to interrupt)"  # an unknown tool: its name
        repl.pending = object()  # type: ignore[assignment]
        assert repl.status_line() is None  # a question is on screen
        repl.pending = None
        gate.set()
        await asyncio.sleep(0.01)
        assert repl.status_line() is None  # the turn is over

    asyncio.run(go())


def test_the_status_line_without_a_tracker_or_settings():
    from types import SimpleNamespace

    from app.screens.repl import Repl

    io = _StatusIO()
    repl = Repl(SimpleNamespace(on_message=None, ctx=SimpleNamespace()), io)  # type: ignore[arg-type]
    assert repl._output_tokens() == 0
    assert repl._spinner_verbs() == list(SPINNER_VERBS)


# ── the terminal's status line, against a fake session ──────────────────────

def test_the_terminal_message_puts_the_status_above_the_prompt():
    from app.screens.terminal_io import TerminalIO

    io = object.__new__(TerminalIO)
    io._status = lambda: None
    assert io._message("❯ ")() == "❯ "
    io._status = lambda: "⛨ Baking… (esc to interrupt)"
    assert io._message("❯ ")() == "⛨ Baking… (esc to interrupt)\n❯ "


def test_the_terminal_repaints_only_when_the_status_changed(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from app.screens import terminal_io

    monkeypatch.setattr(terminal_io, "STATUS_REFRESH_SECONDS", 0.01)
    io = object.__new__(terminal_io.TerminalIO)
    repaints: list[int] = []
    app = SimpleNamespace(is_running=True, invalidate=lambda: repaints.append(1))
    io._session = SimpleNamespace(app=app)  # type: ignore[assignment]
    lines = iter(["a", "a", "a", "b", "b", None, None, "c"])
    last = ["c"]

    def status() -> str | None:
        try:
            last[0] = next(lines)  # type: ignore[assignment]
        except StopIteration:
            pass
        return last[0]

    io._status = status

    async def go() -> None:
        task = asyncio.ensure_future(io._refresh_status())
        await asyncio.sleep(0.2)
        task.cancel()

    asyncio.run(go())
    assert len(repaints) == 3  # a → b, b → None, None → c; never for an unchanged line
    app.is_running = False
    repaints.clear()
    lines = iter(["x", "y"])
    asyncio.run(go())
    assert repaints == []  # no application running: nothing to repaint


# ── the diff package's line tokens ───────────────────────────────────────────

def test_split_lines_keeps_each_newline():
    from app.utils.diff import split_lines

    assert split_lines("") == []
    assert split_lines("a") == ["a"]
    assert split_lines("a\n") == ["a\n"]
    assert split_lines("a\nb") == ["a\n", "b"]
    assert split_lines("a\n\n") == ["a\n", "\n"]
    assert split_lines("\n") == ["\n"]
    assert split_lines("a\r\nb\r\n") == ["a\r\n", "b\r\n"]  # only \n splits; \r stays in the line


def test_a_final_newline_added_or_removed_is_a_changed_last_line():
    from app.utils.diff import NO_NEWLINE_MARKER, get_structured_patch_from_contents

    (added,) = get_structured_patch_from_contents("a\nb", "a\nb\n")
    assert added["lines"] == [" a", "-b", NO_NEWLINE_MARKER, "+b"]
    (removed,) = get_structured_patch_from_contents("a\nb\n", "a\nb")
    assert removed["lines"] == [" a", "-b", "+b", NO_NEWLINE_MARKER]
    assert NO_NEWLINE_MARKER == "\\ No newline at end of file"
    from app.components.file_edit_tool_updated_message import updated_summary

    assert updated_summary([added]) == "Added 1 line, removed 1 line"  # the marker is no line


# ── the context reaches a tool's result rendering ───────────────────────────

def test_the_result_rendering_gets_the_context():
    seen: list[Any] = []

    def render(output: Any, verbose: bool, context: Any = None) -> str:
        seen.append(context)
        return "r"

    tool = _tool(render_tool_result_message=render)
    assert tool_result_text(_result("x", output={"a": 1}), _call(), [tool], context="ctx") == "r"
    renderer = CollapsedReadSearchRenderer(lambda: [tool], lambda: "run-ctx", lambda event: None)
    renderer.feed(AIMessage(content="", tool_calls=[_call()]))
    renderer.feed(_result("x", output={"a": 1}))
    assert seen == ["ctx", "run-ctx"]


def test_a_mount_point_is_matched_through_its_real_path(tmp_path):
    from app.utils.sandbox.runtime import linux_sandbox_utils as sandbox

    real = tmp_path / "real"
    real.mkdir()
    (real / ".mcp.json").write_text("")
    link = tmp_path / "link"
    link.symlink_to(real)
    sandbox._bwrap_mount_points[str(link / ".mcp.json")] = None
    try:
        assert sandbox.is_bwrap_mount_point(str(real / ".mcp.json"))
        (real / "dir").mkdir()
        sandbox._bwrap_mount_points[str(real / "dir")] = None
        assert not sandbox.is_bwrap_mount_point(str(real / "dir"))  # a directory is never a config file
    finally:
        sandbox._bwrap_mount_points.pop(str(link / ".mcp.json"), None)
        sandbox._bwrap_mount_points.pop(str(real / "dir"), None)
