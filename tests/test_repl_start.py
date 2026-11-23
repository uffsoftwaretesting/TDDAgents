"""
The interactive start (2026-11-17): straight to a titled prompt, no menu, no trust dialog for TDDAgents'
own session folders; the first message typed is the request.

Upstream: `main.tsx` opens the REPL with an empty input when there is no prompt and runs SessionStart
(`startup`) at once (`processSessionStartHooks('startup')`); `components/Messages.tsx` renders
`<LogoV2 />` first (`components/LogoV2/LogoV2.tsx`: a rounded box titled with the product and version).
"""

from __future__ import annotations

import asyncio
import os
import pty
import re
import select
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from app.cli.print import open_session_run
from app.components.logo_v2.logo_v2 import DEFAULT_WIDTH, HELP_HINT, display_folder, render_logo
from app.components.trust_dialog.trust_dialog import is_tddagents_workspace, show_trust_dialog
from app.constants.product import VERSION
from app.hooks.use_can_use_tool import PermissionAnswer, PermissionPrompt, build_interactive_can_use_tool
from app.screens.repl import Repl
from app.tdd.plan import TddPlan
from app.types.permissions import PermissionMode, ToolPermissionContext
from app.utils.config import check_has_trust_dialog_accepted
from tests.test_repl import FakeIO, Model, deps_for, until

REPO = Path(__file__).resolve().parents[1]


# ── the title ────────────────────────────────────────────────────────────────

def test_the_title_panel():
    text = render_logo("claude-sonnet-5-5", "/home/u/.tddagents/workspaces/t/project", color=False, home="/home/u")
    lines = text.splitlines()
    assert lines[0].startswith(f"╭─  TDDAgents v{VERSION}  ─") and lines[0].endswith("╮")
    assert "│  ⛨  TDDAgents" in text and "claude-sonnet-5-5 · Red → Green → Refactor" in text
    assert "~/.tddagents/workspaces/t/project" in text
    assert lines[-2].startswith("╰") and lines[-1] == f"  {HELP_HINT}"
    assert all(len(line) <= DEFAULT_WIDTH for line in lines) and len(lines[0]) == DEFAULT_WIDTH
    assert "\x1b[" not in text  # plain without colour


def test_the_title_in_colour_and_narrow():
    coloured = render_logo("m", "/p", color=True)
    assert "\x1b[" in coloured and "TDDAgents" in coloured
    narrow = render_logo("m", "/p", width=40, color=False)
    assert all(len(line) <= 40 for line in narrow.splitlines())


def test_the_folder_is_shown_from_home():
    assert display_folder("/home/u/x/y", "/home/u") == "~/x/y"
    assert display_folder("/home/u", "/home/u") == "~"
    assert display_folder("/home/user2/x", "/home/u") == "/home/user2/x"  # a sibling is not under home
    assert display_folder("/srv/p", "/home/u") == "/srv/p"


# ── trust ────────────────────────────────────────────────────────────────────

def test_tddagents_own_session_folders_are_trusted_without_asking(tmp_path: Path, monkeypatch: Any) -> None:
    from app.bootstrap.state import set_session_trust_accepted

    monkeypatch.setenv("TDDAGENTS_WORKSPACES_DIR", str(tmp_path / "ws"))
    set_session_trust_accepted(False)
    own = tmp_path / "ws" / "tdd-1" / "project"
    own.mkdir(parents=True)
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert is_tddagents_workspace(own) and not is_tddagents_workspace(other)
    assert not is_tddagents_workspace(tmp_path / "ws-other" / "p")
    io = FakeIO()
    asyncio.run(show_trust_dialog(io, own, asyncio.Event(), home=tmp_path / "home"))
    assert io.out == [] and check_has_trust_dialog_accepted(own)  # no dialog, trusted for good


# ── a session opened with no request ─────────────────────────────────────────

def test_a_session_with_no_request_waits_and_the_first_message_is_the_request(python_workspace, tmp_path):
    model = Model([[AIMessage(content="What should it do?")]])
    io = FakeIO()
    holder: dict[str, Repl] = {}

    async def ask(prompt: PermissionPrompt) -> PermissionAnswer:
        return await holder["repl"].ask_permission(prompt)

    async def go() -> Repl:
        perm = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS, should_avoid_permission_prompts=False)
        async with open_session_run(run_id="loop-start-1", plan=TddPlan(), workspace=python_workspace,
                                    planning=True, deps=deps_for(model), permission_context=perm,
                                    project_root=tmp_path, home=tmp_path / "home", managed_dir=tmp_path / "etc",
                                    can_use_tool=build_interactive_can_use_tool(ask)) as run:
            repl = Repl(run, io)
            holder["repl"] = repl
            runner = asyncio.create_task(repl.run_session(None, None))
            await until(lambda: bool(io.prompts) and not repl.busy)
            assert model.seen == []  # no turn until the user types
            assert run.awaiting_request
            io.lines.put_nowait("Build a calculator")
            await until(lambda: len(model.seen) == 1 and not repl.busy)
            io.lines.put_nowait("and make it scientific")  # the second message is not the request
            await until(lambda: len(model.seen) == 2 and not repl.busy)
            io.lines.put_nowait("/exit")
            await asyncio.wait_for(runner, 30)
            return repl

    model.turns.append([AIMessage(content="Noted.")])
    repl = asyncio.run(go())
    assert repl.first_request == "Build a calculator"
    first = [str(m.content) for m in model.seen[0].messages if isinstance(m, HumanMessage)]
    assert "Build a calculator" in first
    assert io.out[0].startswith("╭─  TDDAgents v") and "⏸ plan mode on" in io.out


# ── end to end: `python -m app.main` on a pseudo-terminal ───────────────────

def _strip(raw: bytes) -> str:
    return re.sub(rb"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x08", b"", raw).decode("utf-8", "replace")


def test_main_opens_straight_on_a_titled_prompt_and_leaves_on_exit(tmp_path: Path) -> None:
    """The real entry point, no model call: title, plan-mode line, prompt; `/exit` leaves with 0."""
    settings = tmp_path / "flags.json"
    settings.write_text('{"sandbox": {"enabled": false}}')
    env = {**os.environ, "PROMPT_TOOLKIT_NO_CPR": "1", "TERM": "xterm", "COLUMNS": "100", "LINES": "40",
           "TDDAGENTS_WORKSPACES_DIR": str(tmp_path / "ws"), "TDDAGENTS_CONFIG_DIR": str(tmp_path / "cfg"),
           "HOME": str(tmp_path / "home")}
    (tmp_path / "home").mkdir()
    master, slave = pty.openpty()
    proc = subprocess.Popen([sys.executable, "-m", "app.main", "--settings", str(settings)], cwd=REPO,
                            stdin=slave, stdout=slave, stderr=slave, env=env, close_fds=True)
    os.close(slave)
    out = b""
    sent = False
    deadline = time.monotonic() + 90
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                out += chunk
            elif proc.poll() is not None:
                break
            if not sent and b"\xe2\x9d\xaf" in out and b"plan mode on" in out:  # the prompt glyph
                os.write(master, b"/exit\r")
                sent = True
    finally:
        os.close(master)
        if proc.poll() is None:
            proc.kill()
        proc.wait(10)
    text = _strip(out)
    assert sent, text[-2000:]
    assert proc.returncode == 1, text[-2000:]  # left before any plan was approved: not completed
    assert "TDDAgents · no approved plan" in text  # the exit report box on a terminal
    assert f"TDDAgents v{VERSION}" in text and "Red → Green → Refactor" in text and HELP_HINT in text
    assert "⏸ plan mode on" in text
    assert text.index(f"TDDAgents v{VERSION}") < text.index("plan mode on")
    for gone in ("USE CASE MENU", "WARNING: The Analyst", "TDDAGENTS SESSION (INTERACTIVE)",
                 "Do you trust the files in this folder", "Yes, I trust this folder", "/plan add <text>"):
        assert gone not in text
