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


# ── the intro, the file tools and the read-only agents (stage 1) ──────────────

TOOLS = SRC / "tools"
#: The user's product substitutions (2026-10-28).
PRODUCT = (
    ("for Claude Code, Anthropic's official CLI for Claude",
     "for TDDAgents, UFF Software Testing Team's official CLI tool for TDDAgents"),
    ("for Claude Code.", "for TDDAgents."),
)


def _template(text: str) -> str:
    return text.split("return `", 1)[1].rsplit("`", 1)[0]


def _tool_prompt(directory: str) -> str:
    from app.tools.utils import get_prompt

    return get_prompt(REPO / "app" / "tools" / directory / "prompt.md", "")


@needs_reference
def test_the_intro_is_upstreams_without_an_output_style():
    source = PROMPTS_TS.read_text()
    body = _template(_function(source, "getSimpleIntroSection")).strip("\n")
    body = re.sub(r"\$\{outputStyleConfig !== null \? '[^']*' : '([^']*)'\}", r"\1", body)
    cyber_source = (SRC / "constants" / "cyberRiskInstruction.ts").read_text()
    found = re.search(r"CYBER_RISK_INSTRUCTION = `([^`]*)`", cyber_source)
    assert found is not None
    cyber = found.group(1)
    assert _section("identity.md") == body.replace("${CYBER_RISK_INSTRUCTION}", cyber)


@needs_reference
def test_write_is_upstreams():
    source = (TOOLS / "FileWriteTool" / "prompt.ts").read_text()
    pre = _template(_function(source, "getPreReadInstruction")).replace("\\n", "\n")
    body = _template(_function(source, "getWriteToolDescription")).replace("${getPreReadInstruction()}", pre)
    body = body.replace("${FILE_READ_TOOL_NAME}", "ReadFile").replace("\\u2014", "—")
    assert _tool_prompt("file_write_tool") == body


@needs_reference
def test_edit_is_upstreams_external_build():
    source = (TOOLS / "FileEditTool" / "prompt.ts").read_text()
    pre = _template(_function(source, "getPreReadInstruction")).replace("\\n", "\n")
    body = _template(_function(source, "getDefaultEditDescription"))
    body = (body.replace("${getPreReadInstruction()}", pre).replace("${prefixFormat}", "line number + tab")
            .replace("${minimalUniquenessHint}", "").replace("${FILE_READ_TOOL_NAME}", "ReadFile")
            .replace("from Read tool output", "from ReadFile tool output").replace("\\`", "`"))
    assert _tool_prompt("file_edit_tool") == body


@needs_reference
def test_glob_is_upstreams():
    source = (TOOLS / "GlobTool" / "prompt.ts").read_text()
    found = re.search(r"export const DESCRIPTION = `([^`]*)`", source)
    assert found is not None and _tool_prompt("glob_tool") == found.group(1)


@needs_reference
def test_grep_is_upstreams():
    source = (TOOLS / "GrepTool" / "prompt.ts").read_text()
    body = _template(_function(source, "getDescription"))
    body = (body.replace("${GREP_TOOL_NAME}", "Grep").replace("${BASH_TOOL_NAME}", "Bash")
            .replace("${AGENT_TOOL_NAME}", "Agent").replace("\\`", "`").replace("\\\\", "\\"))
    assert _tool_prompt("grep_tool") == body.strip()  # the loader strips the body's trailing newline


def _agent(name: str) -> object:
    from app.tools.agent_tool.load_agents_dir import load_agent_definition_from_path

    return load_agent_definition_from_path(REPO / "app" / "prompts" / "agents" / name / "AGENT.md")


def _agent_template(source: str, function: str) -> str:
    text = _template(_function(source, function))
    text = (text.replace("${globGuidance}", "- Use Glob for broad file pattern matching")
            .replace("${grepGuidance}", "- Use Grep for searching file contents with regex")
            .replace("${searchToolsHint}", "Glob, Grep, and ReadFile")
            .replace("${FILE_READ_TOOL_NAME}", "ReadFile").replace("${BASH_TOOL_NAME}", "Bash")
            .replace("(no Write, touch", "(no WriteFile, touch"))
    text = re.sub(r"\$\{(embedded|hasEmbeddedSearchTools\(\)) \? ', grep' : ''\}", "", text)
    for upstream, ours in PRODUCT:
        text = text.replace(upstream, ours)
    return text


@needs_reference
@pytest.mark.parametrize("name, source, function", [
    ("explore", "exploreAgent.ts", "getExploreSystemPrompt"),
    ("plan", "planAgent.ts", "getPlanV2SystemPrompt"),
])
def test_the_read_only_agents_are_upstreams(name: str, source: str, function: str):
    text = (TOOLS / "AgentTool" / "built-in" / source).read_text()
    agent = _agent(name)
    assert agent.prompt == _agent_template(text, function)  # type: ignore[attr-defined]
    assert "Claude" not in agent.prompt  # type: ignore[attr-defined]


@needs_reference
def test_read_is_upstreams_default_template():
    from app.tools.file_read_tool.file_read_tool import build_read_file_tool

    source = (TOOLS / "FileReadTool" / "prompt.ts").read_text()
    body = _template(_function(source, "renderPromptTemplate"))
    pdf_line = re.search(r"\? '\\n([^']*)'", body)
    assert pdf_line is not None
    body = re.sub(r"\$\{\s*isPDFSupported\(\)[^}]*\}", "\n" + pdf_line.group(1), body, flags=re.DOTALL)

    def constant(name: str) -> str:
        found = re.search(rf"export const {name} =\s*\n?\s*(['\"])((?:(?!\1)[^\\]|\\.)*)\1", source)
        assert found is not None
        return found.group(2)

    body = (body.replace("${MAX_LINES_TO_READ}", "2000").replace("${maxSizeInstruction}", "")
            .replace("${offsetInstruction}", constant("OFFSET_INSTRUCTION_DEFAULT"))
            .replace("${lineFormat}", constant("LINE_FORMAT_INSTRUCTION")).replace("${BASH_TOOL_NAME}", "Bash")
            .replace("Claude Code", "TDDAgents"))
    assert build_read_file_tool().prompt == body


@needs_reference
def test_the_verification_agent_is_upstreams():
    """`verificationAgent.ts`: the prompt verbatim, upstream's `whenToUse`, flags and reminder."""
    source = (TOOLS / "AgentTool" / "built-in" / "verificationAgent.ts").read_text()
    body = source.split("const VERIFICATION_SYSTEM_PROMPT = `", 1)[1].split("`\n\nconst ", 1)[0]
    body = (body.replace("${BASH_TOOL_NAME}", "Bash").replace("${WEB_FETCH_TOOL_NAME}", "WebFetch")
            .replace("\\`", "`").replace("\\\\", "\\").replace("CLAUDE.md", "TDDAGENTS.md"))
    when = re.search(r"VERIFICATION_WHEN_TO_USE =\s*'([^']*)'", source)
    reminder = re.search(r"criticalSystemReminder_EXPERIMENTAL:\s*'([^']*)'", source)
    assert when is not None and reminder is not None
    agent = _agent("verification")
    assert agent.prompt == body  # type: ignore[attr-defined]
    assert agent.description == when.group(1)  # type: ignore[attr-defined]
    assert agent.critical_system_reminder == reminder.group(1)  # type: ignore[attr-defined]
    assert (agent.background, agent.model, agent.tools) == (True, "inherit", None)  # type: ignore[attr-defined]
    assert agent.disallowed_tools == ("Agent", "WriteFile", "Edit")  # type: ignore[attr-defined]
    assert "Claude" not in agent.prompt  # type: ignore[attr-defined]


@needs_reference
def test_the_session_specific_guidance_is_upstreams_with_the_contract_on():
    """`getSessionSpecificGuidanceSection`: each item verbatim; the verification contract is on (2026-10-28)."""
    from app.context import get_session_specific_guidance_section

    source = PROMPTS_TS.read_text()
    block = _function(source, "getSessionSpecificGuidanceSection")
    agent_block = _function(source, "getAgentToolSection")
    names = {"${ASK_USER_QUESTION_TOOL_NAME}": "AskUserQuestion", "${AGENT_TOOL_NAME}": "Agent",
             "${searchTools}": "the Glob or Grep", "${EXPLORE_AGENT.agentType}": "Explore",
             "${EXPLORE_AGENT_MIN_QUERIES}": "3", "${SKILL_TOOL_NAME}": "Skill",
             '"${VERIFICATION_AGENT_TYPE}"': '"verification"', "\\u2014": "—", "\\`": "`"}

    def item(start: str, text: str = block) -> str:
        found = re.search(r"`(" + re.escape(start) + r"(?:[^`\\]|\\.)*)`", text)
        assert found is not None, start
        out = found.group(1)
        for key, value in names.items():
            out = out.replace(key, value)
        return out

    expected = [
        item("If you do not understand why the user has denied"),
        item("Use the ${AGENT_TOOL_NAME} tool with specialized agents", agent_block),
        item("For simple, directed codebase searches"),
        item("For broader codebase exploration and deep research"),
        item("/<skill-name> (e.g., /commit)"),
        item("The contract: when non-trivial implementation happens"),
    ]
    tools = frozenset({"AskUserQuestion", "Agent", "Skill"})
    assert get_session_specific_guidance_section(tools, True) == "\n".join(
        ["# Session-specific guidance", *(f" - {text}" for text in expected)])
    # Without the Agent tool there is no contract (upstream: `hasAgentTool && …`); nothing at all is None.
    assert "The contract" not in (get_session_specific_guidance_section(frozenset({"AskUserQuestion"}), True) or "")
    assert get_session_specific_guidance_section(frozenset({"Skill"}), False) is None
    assert get_session_specific_guidance_section(frozenset(), False) is None
