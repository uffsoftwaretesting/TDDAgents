"""
Static surface check for the terminal seam (`app/screens/terminal_io.py`): every prompt_toolkit
name and keyword it uses still exists on the installed package.
"""

from __future__ import annotations

import inspect

from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.patch_stdout import patch_stdout


def test_prompt_session_surface():
    assert "key_bindings" in inspect.signature(PromptSession.__init__).parameters
    assert inspect.iscoroutinefunction(PromptSession.prompt_async)
    assert "message" in inspect.signature(PromptSession.prompt_async).parameters


def test_key_bindings_surface():
    assert "eager" in inspect.signature(KeyBindings.add).parameters
    assert KeyPressEvent is not None


def test_patch_stdout_surface():
    assert "raw" in inspect.signature(patch_stdout).parameters


def test_the_seam_builds_without_a_terminal_session():
    from app.screens import terminal_io

    assert hasattr(terminal_io.TerminalIO, "read_line") and hasattr(terminal_io.TerminalIO, "write")
