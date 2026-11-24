"""
The active notice (2026-11-20): TDDAgents tells a user who looked away that it is waiting for them.

Ported from `reference/claude-code/src/services/notifier.ts` (`sendNotification`, `sendToChannel`,
`sendAuto`), `ink/useTerminalNotification.ts`, `ink/termio/osc.ts` (`osc`, `wrapForMultiplexer`),
`utils/env.ts` (`detectTerminal`), `utils/config.ts` (`preferredNotifChannel`, `messageIdleNotifThresholdMs`),
`hooks/useNotifyAfterTimeout.ts`, `components/permissions/PermissionRequest.tsx` (`getNotificationMessage`)
and `screens/REPL.tsx`'s `idle_prompt` effect. Divergence (user decision): `auto` rings the bell where
upstream has no method.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.ink.terminal_notification import TerminalNotification
from app.ink.termio.osc import BEL, ST, osc, wrap_for_multiplexer
from app.services.notifier import (
    DEFAULT_TITLE,
    NOTIFICATION_CHANNELS,
    NotificationOptions,
    send_notification,
    send_to_channel,
)
from app.utils.env import detect_terminal


class Recorder:
    """A `TerminalNotifier` that records what it was asked to do."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def notify_iterm2(self, message: str, title: str | None = None) -> None:
        self.calls.append(("iterm2", message, title))

    def notify_kitty(self, message: str, title: str, notification_id: int) -> None:
        self.calls.append(("kitty", message, title, isinstance(notification_id, int)))

    def notify_ghostty(self, message: str, title: str) -> None:
        self.calls.append(("ghostty", message, title))

    def notify_bell(self) -> None:
        self.calls.append(("bell",))


OPTS = NotificationOptions(message="done", notification_type="idle_prompt")


# ── osc.ts ──────────────────────────────────────────────────────────────────────


def test_osc_ends_with_bel_except_on_kitty() -> None:
    assert osc(9, "hi") == "\x1b]9;hi\x07"
    assert osc(99, "a", "b", terminal="kitty") == "\x1b]99;a;b" + ST


def test_multiplexers_get_a_dcs_passthrough() -> None:
    sequence = "\x1b]9;hi\x07"
    assert wrap_for_multiplexer(sequence, {}) == sequence
    assert wrap_for_multiplexer(sequence, {"TMUX": "/tmp/t,1,0"}) == "\x1bPtmux;\x1b\x1b]9;hi\x07\x1b\\"
    assert wrap_for_multiplexer(sequence, {"STY": "1.pts"}) == "\x1bP\x1b]9;hi\x07\x1b\\"


# ── useTerminalNotification.ts ──────────────────────────────────────────────────


def terminal(env: dict[str, str] | None = None, name: str | None = None) -> tuple[TerminalNotification, list[str]]:
    written: list[str] = []
    return TerminalNotification(written.append, terminal=name, environ=env or {}), written


def test_the_bell_is_raw_even_inside_tmux() -> None:
    term, written = terminal({"TMUX": "x"})
    term.notify_bell()
    assert written == [BEL]


def test_iterm2_shows_title_and_message() -> None:
    term, written = terminal()
    term.notify_iterm2("done", "TDDAgents")
    assert written == ["\x1b]9;\n\nTDDAgents:\ndone\x07"]
    term.notify_iterm2("done")
    assert written[-1] == "\x1b]9;\n\ndone\x07"


def test_kitty_sends_title_body_and_focus() -> None:
    term, written = terminal(name="kitty")
    term.notify_kitty("done", "TDDAgents", 42)
    assert written == ["\x1b]99;i=42:d=0:p=title;TDDAgents" + ST, "\x1b]99;i=42:p=body;done" + ST,
                       "\x1b]99;i=42:d=1:a=focus;" + ST]


def test_ghostty_notify() -> None:
    term, written = terminal({"TMUX": "x"})
    term.notify_ghostty("done", "TDDAgents")
    assert written == ["\x1bPtmux;\x1b\x1b]777;notify;TDDAgents;done\x07\x1b\\"]


# ── env.ts detectTerminal ───────────────────────────────────────────────────────


@pytest.mark.parametrize(("env", "name"), [
    ({}, None),
    ({"TERM_PROGRAM": "iTerm.app"}, "iTerm.app"),
    ({"TERM": "xterm-ghostty", "TERM_PROGRAM": "x"}, "ghostty"),
    ({"TERM": "xterm-kitty"}, "kitty"),
    ({"KITTY_WINDOW_ID": "1"}, "kitty"),
    ({"TMUX": "x"}, "tmux"),
    ({"STY": "x"}, "screen"),
    ({"WT_SESSION": "abc"}, "windows-terminal"),
    ({"GNOME_TERMINAL_SERVICE": ":1"}, "gnome-terminal"),
    ({"VSCODE_GIT_ASKPASS_MAIN": "/x/cursor/askpass"}, "cursor"),
    ({"TERMINAL_EMULATOR": "JetBrains-JediTerm"}, "pycharm"),
    ({"MSYSTEM": "MINGW64"}, "mingw64"),
])
def test_detect_terminal(env: dict[str, str], name: str | None) -> None:
    assert detect_terminal(env) == name


# ── notifier.ts ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("name", "expected", "call"), [
    ("iTerm.app", "iterm2", ("iterm2", "done", None)),
    ("kitty", "kitty", ("kitty", "done", DEFAULT_TITLE, True)),
    ("ghostty", "ghostty", ("ghostty", "done", DEFAULT_TITLE)),
    ("windows-terminal", "terminal_bell", ("bell",)),
    ("gnome-terminal", "terminal_bell", ("bell",)),
    (None, "terminal_bell", ("bell",)),
])
def test_auto_picks_the_terminals_own_method_else_the_bell(name: str | None, expected: str, call: Any) -> None:
    recorder = Recorder()
    assert send_to_channel("auto", OPTS, recorder, name) == expected
    assert recorder.calls == [call]


@pytest.mark.parametrize(("channel", "expected", "calls"), [
    ("iterm2", "iterm2", [("iterm2", "done", None)]),
    ("iterm2_with_bell", "iterm2_with_bell", [("iterm2", "done", None), ("bell",)]),
    ("kitty", "kitty", [("kitty", "done", DEFAULT_TITLE, True)]),
    ("ghostty", "ghostty", [("ghostty", "done", DEFAULT_TITLE)]),
    ("terminal_bell", "terminal_bell", [("bell",)]),
    ("notifications_disabled", "disabled", []),
    ("something-else", "none", []),
])
def test_each_channel(channel: str, expected: str, calls: list[Any]) -> None:
    recorder = Recorder()
    assert send_to_channel(channel, OPTS, recorder, "iTerm.app") == expected
    assert recorder.calls == calls


def test_a_title_reaches_the_terminal() -> None:
    recorder = Recorder()
    send_to_channel("ghostty", NotificationOptions("m", "t", title="Mine"), recorder)
    assert recorder.calls == [("ghostty", "m", "Mine")]


def test_a_failing_terminal_reports_error() -> None:
    class Broken(Recorder):
        def notify_bell(self) -> None:
            raise OSError("closed")

    assert send_to_channel("terminal_bell", OPTS, Broken()) == "error"


def test_the_channels_are_upstreams() -> None:
    assert NOTIFICATION_CHANNELS == ("auto", "iterm2", "iterm2_with_bell", "terminal_bell", "kitty", "ghostty",
                                     "notifications_disabled")


class Dispatcher:
    def __init__(self, fail: bool = False) -> None:
        self.inputs: list[dict[str, Any]] = []
        self.fail = fail

    def create_base_hook_input(self, _tool_use_id: Any) -> dict[str, Any]:
        return {"session_id": "s"}

    async def execute_hooks_outside_repl(self, hook_input: dict[str, Any], **_kw: Any) -> list[Any]:
        if self.fail:
            raise RuntimeError("hook down")
        self.inputs.append(hook_input)
        return []


def test_send_notification_runs_the_hooks_then_the_channel() -> None:
    dispatcher, recorder = Dispatcher(), Recorder()
    method = asyncio.run(send_notification(NotificationOptions("waiting", "idle_prompt", title="T"), recorder,
                                           dispatcher=dispatcher, channel="terminal_bell", terminal_name=None))
    assert method == "terminal_bell"
    assert recorder.calls == [("bell",)]
    [hook_input] = dispatcher.inputs
    assert (hook_input["hook_event_name"], hook_input["message"], hook_input["notification_type"],
            hook_input["title"]) == ("Notification", "waiting", "idle_prompt", "T")


def test_a_failing_hook_never_silences_the_terminal() -> None:
    recorder = Recorder()
    asyncio.run(send_notification(OPTS, recorder, dispatcher=Dispatcher(fail=True), channel="terminal_bell"))
    assert recorder.calls == [("bell",)]


def test_the_channel_comes_from_the_global_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.mcp.config import get_global_config_path

    path = get_global_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    recorder = Recorder()
    assert asyncio.run(send_notification(OPTS, recorder, terminal_name="windows-terminal")) == "terminal_bell"
    path.write_text(json.dumps({"preferredNotifChannel": "notifications_disabled"}))
    assert asyncio.run(send_notification(OPTS, recorder, terminal_name="windows-terminal")) == "disabled"


# ── the global config's two keys ────────────────────────────────────────────────


@pytest.mark.parametrize(("document", "channel", "threshold"), [
    ({}, "auto", 60000),
    ({"preferredNotifChannel": "kitty", "messageIdleNotifThresholdMs": 5}, "kitty", 5),
    ({"preferredNotifChannel": "", "messageIdleNotifThresholdMs": -1}, "auto", 60000),
    ({"preferredNotifChannel": 3, "messageIdleNotifThresholdMs": True}, "auto", 60000),
    ({"messageIdleNotifThresholdMs": 0}, "auto", 0),
])
def test_the_config_defaults(document: dict[str, Any], channel: str, threshold: int) -> None:
    from app.services.mcp.config import get_global_config_path
    from app.utils.config import get_message_idle_notif_threshold_ms, get_preferred_notif_channel

    path = get_global_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document))
    assert (get_preferred_notif_channel(), get_message_idle_notif_threshold_ms()) == (channel, threshold)


# ── PermissionRequest.tsx getNotificationMessage ────────────────────────────────


@pytest.mark.parametrize(("tool", "facing", "message"), [
    ("ExitPlanMode", None, "TDDAgents needs your approval for the plan"),
    ("EnterPlanMode", None, "TDDAgents wants to enter plan mode"),
    ("Bash", None, "TDDAgents needs your permission to use Bash"),
    ("ReadFile", "Read", "TDDAgents needs your permission to use Read"),
    ("AskUserQuestion", "", "TDDAgents needs your attention"),
    ("", None, "TDDAgents needs your attention"),
])
def test_the_permission_prompts_message(tool: str, facing: str | None, message: str) -> None:
    from app.components.permissions.permission_request import get_notification_message

    assert get_notification_message(tool, facing) == message


# ── the REPL ────────────────────────────────────────────────────────────────────


class IO:
    def __init__(self) -> None:
        self.out: list[str] = []
        self.raw: list[str] = []

    def write(self, text: str) -> None:
        self.out.append(text)

    def write_raw(self, data: str) -> None:
        self.raw.append(data)


def repl(io: IO) -> Any:
    from app.screens.repl import Repl
    from app.state.app_state_store import AppStateStore

    r = Repl(SimpleNamespace(on_message=None, ctx=SimpleNamespace(hook_dispatcher=None)), io)  # type: ignore[arg-type]
    r.run.store = AppStateStore()
    return r


@pytest.fixture(autouse=True)
def _plain_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """A terminal with no OSC method: `auto` rings the bell."""
    for name in ("TERM_PROGRAM", "TERM", "TMUX", "STY", "KITTY_WINDOW_ID", "WT_SESSION", "GNOME_TERMINAL_SERVICE",
                 "VTE_VERSION", "CURSOR_TRACE_ID", "VSCODE_GIT_ASKPASS_MAIN", "TERMINAL_EMULATOR", "MSYSTEM",
                 "KONSOLE_VERSION", "XTERM_VERSION", "TERMINATOR_UUID", "ALACRITTY_LOG", "TILIX_ID"):
        monkeypatch.delenv(name, raising=False)


def test_a_waiting_question_rings_after_the_idle_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.screens.repl as screen
    from app.hooks.use_can_use_tool import PermissionAnswer, PermissionPrompt
    from tests.test_repl import OPTIONS

    monkeypatch.setattr(screen, "DEFAULT_INTERACTION_THRESHOLD_MS", 20)

    async def go() -> IO:
        io = IO()
        r = repl(io)
        task = asyncio.create_task(r.ask_permission(PermissionPrompt("Bash", "ls", "", {}, OPTIONS)))
        await asyncio.sleep(0.1)
        r.pending.future.set_result(PermissionAnswer("yes"))
        await task
        return io

    assert asyncio.run(go()).raw == [BEL]


def test_a_question_answered_in_time_stays_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.screens.repl as screen
    from app.hooks.use_can_use_tool import PermissionAnswer, PermissionPrompt
    from tests.test_repl import OPTIONS

    monkeypatch.setattr(screen, "DEFAULT_INTERACTION_THRESHOLD_MS", 200)

    async def go() -> IO:
        io = IO()
        r = repl(io)
        task = asyncio.create_task(r.ask_permission(PermissionPrompt("Bash", "ls", "", {}, OPTIONS)))
        await asyncio.sleep(0.01)
        r.pending.future.set_result(PermissionAnswer("yes"))
        await task
        await asyncio.sleep(0.3)
        return io

    assert asyncio.run(go()).raw == []


def test_typing_while_a_question_waits_silences_it(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.screens.repl as screen
    from app.bootstrap.state import update_last_interaction_time
    from app.hooks.use_can_use_tool import PermissionAnswer, PermissionPrompt
    from tests.test_repl import OPTIONS

    monkeypatch.setattr(screen, "DEFAULT_INTERACTION_THRESHOLD_MS", 100)

    async def go() -> IO:
        io = IO()
        r = repl(io)
        task = asyncio.create_task(r.ask_permission(PermissionPrompt("Bash", "ls", "", {}, OPTIONS)))
        await asyncio.sleep(0.03)
        update_last_interaction_time()  # the user typed something that was not an answer
        await asyncio.sleep(0.15)
        r.pending.future.set_result(PermissionAnswer("yes"))
        await task
        return io

    assert asyncio.run(go()).raw == []


def _set_idle_threshold(ms: int) -> None:
    from app.services.mcp.config import get_global_config_path

    path = get_global_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"messageIdleNotifThresholdMs": ms}))


def test_a_finished_turn_notifies_a_user_who_stays_idle() -> None:
    """The plan finished while the user was away: `idle_prompt` "… is waiting for your input"."""
    from app.query.transitions import Terminal
    from app.tdd.plan import TddPlan

    _set_idle_threshold(10)

    async def go() -> IO:
        io = IO()
        r = repl(io)
        r.run.plan = TddPlan()
        r._turn_end(Terminal.COMPLETED)
        await asyncio.sleep(0.1)
        return io

    assert asyncio.run(go()).raw == [BEL]


def test_a_user_who_types_after_the_turn_is_not_notified() -> None:
    from app.bootstrap.state import update_last_interaction_time
    from app.query.transitions import Terminal
    from app.tdd.plan import TddPlan

    _set_idle_threshold(60)

    async def go() -> IO:
        io = IO()
        r = repl(io)
        r.run.plan = TddPlan()
        r._turn_end(Terminal.COMPLETED)
        await asyncio.sleep(0.01)
        update_last_interaction_time()
        await asyncio.sleep(0.15)
        return io

    assert asyncio.run(go()).raw == []


def test_a_new_turn_end_rearms_the_idle_notice() -> None:
    from app.query.transitions import Terminal
    from app.tdd.plan import TddPlan

    _set_idle_threshold(50)

    async def go() -> tuple[IO, Any]:
        io = IO()
        r = repl(io)
        r.run.plan = TddPlan()
        r._turn_end(Terminal.COMPLETED)
        first = r._idle_notice
        r._turn_end(Terminal.COMPLETED)
        await asyncio.sleep(0.15)
        return io, first

    io, first = asyncio.run(go())
    assert first.cancelled()
    assert io.raw == [BEL]


def test_handle_line_stamps_the_interaction_clock() -> None:
    from app.bootstrap.state import get_last_interaction_time

    async def go() -> float:
        r = repl(IO())
        r.pending = SimpleNamespace(parse=lambda line: line, future=asyncio.get_running_loop().create_future(),
                                    retry="", cancelled=None, aborts_turn=True, text="")
        before = get_last_interaction_time()
        await asyncio.sleep(0.01)
        with contextlib.suppress(Exception):  # only the clock matters here
            await r.handle_line("1")
        return get_last_interaction_time() - before

    assert asyncio.run(go()) > 0


def test_a_notification_in_flight_is_held_until_done() -> None:
    async def go() -> tuple[int, int]:
        r = repl(IO())
        task = r._notify("m", "idle_prompt")
        held = len(r._notifications)
        await task
        return held, len(r._notifications)

    assert asyncio.run(go()) == (1, 0)


def test_a_finished_plan_reaches_a_notification_hook_and_the_bell(python_workspace: Any, tmp_path: Path) -> None:
    """
    Integration: a real session completes its plan; the user stays idle; `idle_prompt` runs the project's
    Notification command hook (`executeNotificationHooks`) and rings the bell; `/exit` cancels what is left.
    """
    from app.tdd.plan import TddPlan
    from tests.test_repl import FakeIO, Model, cycle_turns, session, until

    _set_idle_threshold(20)
    marker = tmp_path / "notified.json"
    script_file = tmp_path / "hook.sh"
    script_file.write_text(f"cat > {marker}\n")
    settings = tmp_path / ".tddagents" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"hooks": {"Notification": [
        {"matcher": "idle_prompt", "hooks": [{"type": "command", "command": f"sh {script_file}"}]}]}}))

    class RawIO(FakeIO):
        def __init__(self) -> None:
            super().__init__()
            self.raw: list[str] = []

        def write_raw(self, data: str) -> None:
            self.raw.append(data)

    async def script(repl: Any, io: Any) -> None:
        repl.io = RawIO.__new__(RawIO)
        repl.io.__dict__.update(io.__dict__)
        repl.io.raw = []
        await until(lambda: "Every sub-requirement is done" in io.text())
        await until(lambda: marker.exists() and marker.read_text().strip() != "")
        # The channel comes after the hooks (`sendNotification`): the marker can exist before the bell rings.
        await until(lambda: BEL in repl.io.raw)
        io.lines.put_nowait("/exit")

    _terminal, repl, _io = asyncio.run(session(python_workspace, tmp_path, Model(cycle_turns()),
                                               TddPlan.from_texts(["add"]), script))
    payload = json.loads(marker.read_text())
    assert (payload["hook_event_name"], payload["notification_type"], payload["message"]) == (
        "Notification", "idle_prompt", "TDDAgents is waiting for your input")
    assert BEL in getattr(repl.io, "raw")  # noqa: B009 - the fake IO's own attribute
    assert repl._idle_notice is None or repl._idle_notice.done()


# ── more edge cases ─────────────────────────────────────────────────────────────


def test_terminal_io_write_raw_writes_and_flushes(capsys: pytest.CaptureFixture[str]) -> None:
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from app.screens.terminal_io import TerminalIO

    with create_pipe_input() as pipe:
        TerminalIO(lambda: None, input=pipe, output=DummyOutput()).write_raw(BEL)
    assert capsys.readouterr().out == BEL


@pytest.mark.parametrize("channel", ["", "AUTO", "bell", "system"])
def test_an_unknown_channel_sends_nothing(channel: str) -> None:
    recorder = Recorder()
    assert send_to_channel(channel, OPTS, recorder, "iTerm.app") == "none"
    assert recorder.calls == []


def _prompt_notification(monkeypatch: pytest.MonkeyPatch, prompt: Any) -> list[dict[str, Any]]:
    import app.screens.repl as screen
    from app.hooks.use_can_use_tool import PermissionAnswer

    monkeypatch.setattr(screen, "DEFAULT_INTERACTION_THRESHOLD_MS", 20)
    dispatcher = Dispatcher()

    async def go() -> None:
        r = repl(IO())
        r.run.ctx.hook_dispatcher = dispatcher
        task = asyncio.create_task(r.ask_permission(prompt))
        await asyncio.sleep(0.12)
        r.pending.future.set_result(PermissionAnswer("yes"))
        await task

    asyncio.run(go())
    return dispatcher.inputs


@pytest.mark.parametrize(("tool", "message"), [
    ("Bash", "TDDAgents needs your permission to use Bash"),
    ("ExitPlanMode", "TDDAgents needs your approval for the plan"),
    ("EnterPlanMode", "TDDAgents wants to enter plan mode"),
])
def test_each_prompt_tells_the_notification_hook_what_it_waits_for(
        monkeypatch: pytest.MonkeyPatch, tool: str, message: str) -> None:
    from app.hooks.use_can_use_tool import PermissionPrompt
    from tests.test_repl import OPTIONS

    [hook_input] = _prompt_notification(monkeypatch, PermissionPrompt(tool, "x", "", {}, OPTIONS))
    assert (hook_input["notification_type"], hook_input["message"]) == ("permission_prompt", message)


def test_a_question_from_the_model_notifies_that_it_needs_attention(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.screens.repl as screen

    monkeypatch.setattr(screen, "DEFAULT_INTERACTION_THRESHOLD_MS", 20)
    dispatcher = Dispatcher()
    question = {"question": "Which?", "header": "Pick", "options": [{"label": "A"}, {"label": "B"}],
                "multiSelect": False}

    async def go() -> Any:
        r = repl(IO())
        r.run.ctx.hook_dispatcher = dispatcher
        task = asyncio.create_task(r.ask_questions([question]))
        await asyncio.sleep(0.12)
        r.pending.future.set_result("A")
        return await task

    assert asyncio.run(go()) == {"Which?": "A"}
    [hook_input] = dispatcher.inputs
    assert hook_input["message"] == "TDDAgents needs your attention"


def test_a_notification_fires_once_per_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.screens.repl as screen
    from app.hooks.use_can_use_tool import PermissionPrompt
    from tests.test_repl import OPTIONS

    monkeypatch.setattr(screen, "DEFAULT_INTERACTION_THRESHOLD_MS", 20)
    dispatcher = Dispatcher()

    async def go() -> None:
        r = repl(IO())
        r.run.ctx.hook_dispatcher = dispatcher
        task = asyncio.create_task(r.ask_permission(PermissionPrompt("Bash", "x", "", {}, OPTIONS)))
        await asyncio.sleep(0.2)  # ten thresholds of silence
        from app.hooks.use_can_use_tool import PermissionAnswer

        r.pending.future.set_result(PermissionAnswer("yes"))
        await task

    asyncio.run(go())
    assert len(dispatcher.inputs) == 1


def test_no_idle_notice_while_a_turn_runs_or_a_question_waits() -> None:
    from app.query.transitions import Terminal

    _set_idle_threshold(10)

    async def go() -> IO:
        io = IO()
        r = repl(io)
        r.pending = object()  # a question is on screen
        r._turn_end(Terminal.COMPLETED)
        await asyncio.sleep(0.08)
        return io

    assert asyncio.run(go()).raw == []
