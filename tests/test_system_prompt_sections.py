"""
The static system sections and the Bash prompt are upstream's text verbatim (2026-10-28).

A live CPF run's agent wrote a newline-separated verification script ending most steps in
`|| true`, and guessed `docker-compose` without checking the machine. Upstream's Bash prompt
(`getSimplePrompt`) and its system sections steer exactly that, and ours were paraphrases.
Each test parses the reference source when the snapshot is present.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.context import read_system_prompt_file
from app.tools.bash_tool.bash_tool import get_bash_prompt

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "reference" / "claude-code" / "src"
PROMPTS_TS = SRC / "constants" / "prompts.ts"
BASH_TS = SRC / "tools" / "BashTool" / "prompt.ts"
needs_reference = pytest.mark.skipif(not PROMPTS_TS.is_file(),
                                     reason="the claude-code reference snapshot is not checked out")

#: This build's tool names for upstream's constants.
TOOL_NAMES = {
    "${FILE_READ_TOOL_NAME}": "ReadFile", "${FILE_EDIT_TOOL_NAME}": "Edit", "${FILE_WRITE_TOOL_NAME}": "WriteFile",
    "${GLOB_TOOL_NAME}": "Glob", "${GREP_TOOL_NAME}": "Grep", "${BASH_TOOL_NAME}": "Bash",
    "${taskToolName}": "TodoWrite",
}


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    return source[start:source.index("\n}\n", start)]


def _literals(block: str) -> list[str]:
    out = []
    for text in re.findall(r"`([^`]*)`", block):
        for constant, name in TOOL_NAMES.items():
            text = text.replace(constant, name)
        out.append(text)
    return out


def _returned_template(block: str) -> str:
    return block.rsplit("return `", 1)[1].rsplit("`", 1)[0]


def _bullets(title: str, items: list[str]) -> str:
    return "\n".join([title, *(f" - {item}" for item in items)])


def _section(name: str) -> str:
    return read_system_prompt_file(name).rstrip("\n")


@needs_reference
def test_system_is_upstreams():
    source = PROMPTS_TS.read_text()
    items = _literals(_function(source, "getSimpleSystemSection").split("const items = [", 1)[1].split("]", 1)[0])
    hooks = _returned_template(_function(source, "getHooksSection"))
    assert _section("system.md") == _bullets("# System", [*items[:4], hooks, *items[4:]])


@needs_reference
def test_actions_is_upstreams():
    assert _section("actions-with-care.md") == _returned_template(
        _function(PROMPTS_TS.read_text(), "getActionsSection"))


@needs_reference
def test_using_your_tools_is_upstreams():
    block = _function(PROMPTS_TS.read_text(), "getUsingYourToolsSection").split("const providedToolSubitems = [", 1)[1]
    subitems = _literals(block.split("const items = [", 1)[0])
    items = _literals(block.split("const items = [", 1)[1].split("].filter", 1)[0])
    expected = "\n".join(["# Using your tools", f" - {items[0]}", *(f"  - {s}" for s in subitems),
                          *(f" - {i}" for i in items[1:])])
    assert _section("tools.md") == expected


@needs_reference
def test_tone_and_style_is_upstreams_external_build():
    block = _function(PROMPTS_TS.read_text(), "getSimpleToneAndStyleSection")
    items = _literals(block.split("const items = [", 1)[1].split("].filter", 1)[0])
    assert "Your responses should be short and concise." in items  # the external build's item
    assert _section("tone.md") == _bullets("# Tone and style", items)


@needs_reference
def test_output_efficiency_is_upstreams_external_build():
    assert _section("output-efficiency.md") == _returned_template(
        _function(PROMPTS_TS.read_text(), "getOutputEfficiencySection"))


@needs_reference
def test_the_bash_prompt_is_upstreams_simple_prompt():
    source = BASH_TS.read_text()
    block = _function(source, "getSimplePrompt")
    preferences = _literals(block.split("const toolPreferenceItems = [", 1)[1].split("]\n", 1)[0])
    multiple = _literals(block.split("const multipleCommandsSubitems = [", 1)[1].split("]\n", 1)[0])
    git = _literals(block.split("const gitSubitems = [", 1)[1].split("]\n", 1)[0])
    prompt = get_bash_prompt()
    for item in preferences + multiple + git:
        assert item in prompt, item
    for line in (
        "Executes a given bash command and returns its output.",
        ("The working directory persists between commands, but shell state does not. The shell environment is "
         "initialized from the user's profile (bash or zsh)."),
        (" - If your command will create new directories or files, first use this tool to run `ls` to verify the "
         "parent directory exists and is the correct location."),
        "  - Do not retry failing commands in a sleep loop — diagnose the root cause.",
        "  - DO NOT use newlines to separate commands (newlines are ok in quoted strings).",
    ):
        assert line in prompt
    # Not ported: `run_in_background` (our Bash has no such parameter), so neither is its note
    # or the background sleep items; the sandbox section is empty without a sandbox.
    assert "run_in_background" not in prompt


def test_the_tdd_additions_stay():
    prompt = get_bash_prompt()
    assert "deterministic command-injection check" in prompt
    assert "# Committing changes with git" in prompt
