"""
The interaction clock counts every keystroke, not only a submitted line.

Ported from `reference/claude-code/src/bootstrap/state.ts` (`updateLastInteractionTime`, `flushInteractionTime`:
a keystroke marks the clock dirty, the next frame stamps it once), `ink/components/App.tsx`
(`processKeysInBatch`: any user key or mouse event counts; terminal responses and mode-1003 motion without a
button do not), `ink/parse-keypress.ts` (`SGR_MOUSE_RE`, `parseMouseEvent`) and `ink/ink.tsx` (a flush before each
render). The prompt_toolkit seam (`app/screens/terminal_io.py`) feeds every key batch through it.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from prompt_toolkit.key_binding.key_processor import KeyPress
from prompt_toolkit.keys import Keys

from app.bootstrap.state import flush_interaction_time, get_last_interaction_time, update_last_interaction_time
from app.ink.components.app import is_user_input, process_keys_in_batch
from app.ink.parse_keypress import parse_mouse_event


@pytest.fixture(autouse=True)
def _clean_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.bootstrap.state as state

    monkeypatch.setattr(state, "_LAST_INTERACTION_TIME", [0.0])
    monkeypatch.setattr(state, "_INTERACTION_TIME_DIRTY", [False])


# ── bootstrap/state.ts ──────────────────────────────────────────────────────────


def test_a_keystroke_only_marks_the_clock_until_the_next_flush() -> None:
    update_last_interaction_time()
    assert get_last_interaction_time() == 0.0
    flush_interaction_time()
    assert get_last_interaction_time() > 0.0


def test_immediate_stamps_at_once() -> None:
    update_last_interaction_time(immediate=True)
    assert get_last_interaction_time() > 0.0


def test_a_flush_with_nothing_dirty_keeps_the_old_time() -> None:
    update_last_interaction_time(immediate=True)
    first = get_last_interaction_time()
    flush_interaction_time()
    assert get_last_interaction_time() == first


def test_a_flush_clears_the_dirty_mark() -> None:
    update_last_interaction_time()
    flush_interaction_time()
    stamped = get_last_interaction_time()
    flush_interaction_time()
    assert get_last_interaction_time() == stamped


# ── parse-keypress.ts parseMouseEvent ───────────────────────────────────────────


@pytest.mark.parametrize(("data", "button"), [
    ("\x1b[<0;10;5M", 0), ("\x1b[<2;1;1m", 2), ("\x1b[<32;4;4M", 32), ("\x1b[<35;4;4M", 35),
])
def test_an_sgr_report_gives_its_button(data: str, button: int) -> None:
    parsed = parse_mouse_event(data)
    assert parsed is not None and parsed.button == button


@pytest.mark.parametrize("data", ["\x1b[<64;1;1M", "\x1b[<65;1;1M", "\x1b[M !!", "x", "\x1b[<a;1;1M", ""])
def test_wheel_events_and_other_data_are_not_mouse_events(data: str) -> None:
    assert parse_mouse_event(data) is None


def test_press_and_release_and_position() -> None:
    press, release = parse_mouse_event("\x1b[<1;7;9M"), parse_mouse_event("\x1b[<1;7;9m")
    assert press is not None and release is not None
    assert (press.action, press.col, press.row) == ("press", 7, 9)
    assert release.action == "release"


# ── App.tsx processKeysInBatch ──────────────────────────────────────────────────


@pytest.mark.parametrize("key_press", [
    KeyPress("a", "a"), KeyPress(Keys.ControlM, "\r"), KeyPress(Keys.Escape, "\x1b"),
    KeyPress(Keys.ControlC, "\x03"), KeyPress(Keys.BracketedPaste, "pasted"), KeyPress(Keys.Up, "\x1b[A"),
    KeyPress(Keys.Vt100MouseEvent, "\x1b[<0;1;1M"), KeyPress(Keys.Vt100MouseEvent, "\x1b[<32;1;1M"),
    KeyPress(Keys.Vt100MouseEvent, "\x1b[<64;1;1M"), KeyPress(Keys.Vt100MouseEvent, "\x1b[M !!"),
])
def test_user_keys_and_mouse_events_count(key_press: KeyPress) -> None:
    assert is_user_input(key_press) is True


@pytest.mark.parametrize("key_press", [
    KeyPress(Keys.CPRResponse, "\x1b[12;1R"),
    KeyPress(Keys.Vt100MouseEvent, "\x1b[<35;1;1M"),  # mode-1003 motion, no button held
    KeyPress(Keys.Vt100MouseEvent, "\x1b[<39;1;1M"),  # motion with a modifier, no button
])
def test_terminal_responses_and_passive_motion_do_not_count(key_press: KeyPress) -> None:
    assert is_user_input(key_press) is False


def test_a_batch_with_any_user_key_marks_the_clock_once() -> None:
    process_keys_in_batch([KeyPress(Keys.CPRResponse, "\x1b[1;1R"), KeyPress("x", "x")])
    flush_interaction_time()
    assert get_last_interaction_time() > 0.0


def test_a_batch_of_responses_alone_leaves_the_clock() -> None:
    process_keys_in_batch([KeyPress(Keys.CPRResponse, "\x1b[1;1R"), KeyPress(Keys.Vt100MouseEvent, "\x1b[<35;1;1M")])
    flush_interaction_time()
    assert get_last_interaction_time() == 0.0


def test_an_empty_batch_leaves_the_clock() -> None:
    process_keys_in_batch([])
    flush_interaction_time()
    assert get_last_interaction_time() == 0.0


# ── the prompt_toolkit seam ─────────────────────────────────────────────────────


def test_typing_without_enter_moves_the_clock() -> None:
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from app.screens.terminal_io import TerminalIO

    async def go() -> float:
        with create_pipe_input() as pipe:
            io = TerminalIO(lambda: None, input=pipe, output=DummyOutput())
            task = asyncio.ensure_future(io.read_line("> "))
            await asyncio.sleep(0.05)
            before = get_last_interaction_time()
            pipe.send_text("hel")  # no Enter
            await asyncio.sleep(0.1)
            flush_interaction_time()
            moved = get_last_interaction_time() - before
            pipe.send_text("lo\r")
            assert await asyncio.wait_for(task, 2) == "hello"
            return moved

    assert asyncio.run(go()) > 0


def test_the_status_ticker_flushes_the_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.screens.terminal_io as seam
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    monkeypatch.setattr(seam, "STATUS_REFRESH_SECONDS", 0.01)

    async def go() -> float:
        with create_pipe_input() as pipe:
            io = seam.TerminalIO(lambda: None, input=pipe, output=DummyOutput())
            task = asyncio.ensure_future(io.read_line("> "))
            await asyncio.sleep(0.05)
            update_last_interaction_time()  # a keystroke that no flush has stamped yet
            await asyncio.sleep(0.1)
            stamped = get_last_interaction_time()
            pipe.send_text("\r")
            await asyncio.wait_for(task, 2)
            return stamped

    assert asyncio.run(go()) > 0


# ── the REPL's notifications read the flushed clock ─────────────────────────────


class IO:
    def __init__(self) -> None:
        self.out: list[str] = []
        self.raw: list[str] = []

    def write(self, text: str) -> None:
        self.out.append(text)

    def write_raw(self, data: str) -> None:
        self.raw.append(data)


def _repl(io: IO) -> Any:
    from app.screens.repl import Repl
    from app.state.app_state_store import AppStateStore

    r = Repl(SimpleNamespace(on_message=None, ctx=SimpleNamespace(hook_dispatcher=None)), io)  # type: ignore[arg-type]
    r.run.store = AppStateStore()
    return r


@pytest.fixture
def _plain_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("TERM_PROGRAM", "TERM", "TMUX", "STY", "KITTY_WINDOW_ID", "WT_SESSION"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.usefixtures("_plain_terminal")
def test_keys_arriving_while_a_question_waits_hold_the_notification(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.screens.repl as screen
    from app.hooks.use_can_use_tool import PermissionAnswer, PermissionPrompt
    from tests.test_repl import OPTIONS

    monkeypatch.setattr(screen, "DEFAULT_INTERACTION_THRESHOLD_MS", 120)

    async def go() -> IO:
        io = IO()
        r = _repl(io)
        task = asyncio.create_task(r.ask_permission(PermissionPrompt("Bash", "ls", "", {}, OPTIONS)))
        for _ in range(6):  # keystrokes without Enter, marked dirty only, as the seam does
            await asyncio.sleep(0.04)
            process_keys_in_batch([KeyPress("x", "x")])
        assert io.raw == []
        r.pending.future.set_result(PermissionAnswer("yes"))
        await task
        return io

    assert asyncio.run(go()).raw == []


@pytest.mark.usefixtures("_plain_terminal")
def test_once_the_keys_stop_the_question_notifies(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.screens.repl as screen
    from app.hooks.use_can_use_tool import PermissionAnswer, PermissionPrompt
    from tests.test_repl import OPTIONS

    monkeypatch.setattr(screen, "DEFAULT_INTERACTION_THRESHOLD_MS", 60)

    async def go() -> IO:
        io = IO()
        r = _repl(io)
        task = asyncio.create_task(r.ask_permission(PermissionPrompt("Bash", "ls", "", {}, OPTIONS)))
        await asyncio.sleep(0.03)
        process_keys_in_batch([KeyPress("x", "x")])  # one keystroke, then silence
        await asyncio.sleep(0.25)
        r.pending.future.set_result(PermissionAnswer("yes"))
        await task
        return io

    assert "\x07" in asyncio.run(go()).raw


# ── useNotifyAfterTimeout.ts shouldNotify ───────────────────────────────────────


def test_should_notify_follows_the_time_since_the_last_interaction(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.hooks.use_notify_after_timeout as hook

    monkeypatch.setattr("app.hooks.use_notify_after_timeout.time.time", lambda: 100.0)
    monkeypatch.setattr(hook, "get_last_interaction_time", lambda: 94.0)
    assert hook.get_time_since_last_interaction_ms() == 6000.0
    assert hook.has_recent_interaction(6000) is False
    assert hook.should_notify(6000) is True
    assert hook.has_recent_interaction(6001) is True
    assert hook.should_notify(6001) is False


def test_the_threshold_is_upstreams() -> None:
    from app.hooks.use_notify_after_timeout import DEFAULT_INTERACTION_THRESHOLD_MS

    assert DEFAULT_INTERACTION_THRESHOLD_MS == 6000
