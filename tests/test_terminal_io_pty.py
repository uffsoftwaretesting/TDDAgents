"""
The terminal seam end to end (2026-11-14): the real `TerminalIO` on a pseudo-terminal, its raw bytes read
back. `app/screens/terminal_io.py` is exempt from unit and mutation testing (CLAUDE.md, the seam modules);
this is its end-to-end check of what the REPL's screen fixes promise:

* a log record that reaches the terminal goes through prompt_toolkit (after the input line is erased), not
  over the input line, and it is the message alone (`TERMINAL_LOG_FORMAT`);
* TDDAgents' own warnings stay off the terminal (`TerminalLogFilter`);
* the status line is drawn above the prompt and repainted once a second when it changed.

`PROMPT_TOOLKIT_NO_CPR` keeps prompt_toolkit from waiting for a cursor-position answer nobody sends.
"""

from __future__ import annotations

import os
import pty
import re
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

DRIVER = '''
import asyncio, logging, sys, time
from app.utils.debug import TERMINAL_LOG_FORMAT, install_terminal_log_filter
logging.basicConfig(level=logging.WARNING, format=TERMINAL_LOG_FORMAT)
install_terminal_log_filter()
logging.getLogger("TDDMain").setLevel(logging.INFO)
from app.screens.terminal_io import TerminalIO


async def main():
    io = TerminalIO(lambda: None)
    start = time.monotonic()
    io.set_status(lambda: f"STATUS-{int(time.monotonic() - start)}")
    with io:
        if sys.argv[1:] == ["unrouted"]:
            io._restore_logs()  # the handler back on the real stderr: what main.py had before
        reader = asyncio.ensure_future(io.read_line("PROMPT> "))
        await asyncio.sleep(0.5)
        io.write("WRITTEN-LINE")
        logging.getLogger("TDDMain").info("LOGGED-LINE")
        logging.getLogger("app.x").warning("FILTERED-LINE")
        await asyncio.sleep(2.6)
        reader.cancel()
        try:
            await reader
        except BaseException:
            pass

asyncio.run(main())
'''


def _run_on_a_pty(tmp_path: Path, *args: str) -> bytes:
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER)
    master, slave = pty.openpty()
    env = {**os.environ, "PROMPT_TOOLKIT_NO_CPR": "1", "TERM": "xterm", "PYTHONPATH": str(REPO),
           "COLUMNS": "120", "LINES": "40"}
    proc = subprocess.Popen([sys.executable, str(driver), *args], stdin=slave, stdout=slave, stderr=slave,
                            env=env, close_fds=True)
    os.close(slave)
    out = b""
    deadline = time.monotonic() + 20
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError:  # the child closed its side
                    break
                if not chunk:
                    break
                out += chunk
            elif proc.poll() is not None:
                break
    finally:
        os.close(master)
        if proc.poll() is None:
            proc.kill()
        proc.wait(5)
    return out


def _text(raw: bytes) -> str:
    return re.sub(rb"\x1b\[[0-9;?]*[A-Za-z]|\x08", b"", raw).decode("utf-8", "replace")


@pytest.fixture(scope="module")
def routed(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    return _run_on_a_pty(tmp_path_factory.mktemp("pty-routed"))


def test_the_status_line_is_above_the_prompt_and_repaints_when_it_changes(routed: bytes) -> None:
    text = _text(routed)
    assert "STATUS-0\r\r\nPROMPT>" in text  # the status line, then the input line under it
    later = {int(n) for n in re.findall(r"STATUS-(\d+)", text)} | {int(d) for d in re.findall(r"\n(\d)\r", text)}
    assert max(later) >= 2  # repainted after it changed (the differential renderer may rewrite one digit)


def test_a_log_line_goes_above_the_prompt_as_its_message_alone(routed: bytes) -> None:
    assert routed.count(b"LOGGED-LINE") == 1
    assert routed.index(b"WRITTEN-LINE") < routed.index(b"LOGGED-LINE")  # in order, through prompt_toolkit
    before = routed[:routed.index(b"LOGGED-LINE")]
    assert b"\x1b[J" in before[before.rindex(b"PROMPT>"):]  # the input line was erased first
    line = _text(routed).split("LOGGED-LINE")[0].rsplit("\n", 1)[-1]
    assert line.strip() == ""  # no timestamp, no level, nothing before it on its line
    assert b"[INFO]" not in routed


def test_tddagents_own_warnings_stay_off_the_terminal(routed: bytes) -> None:
    assert b"FILTERED-LINE" not in routed


def test_unrouted_the_log_line_lands_on_the_input_line(tmp_path: Path) -> None:
    """The bug the routing fixes: a handler bound to the real stderr writes right after the prompt."""
    raw = _run_on_a_pty(tmp_path, "unrouted")
    assert raw.index(b"LOGGED-LINE") < raw.index(b"WRITTEN-LINE")
    before = raw[:raw.index(b"LOGGED-LINE")]
    assert b"\x1b[J" not in before[before.rindex(b"PROMPT>"):]
