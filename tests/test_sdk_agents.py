"""`--agents` and the SDK's `initialize.agents`: `parseAgentsFromJson` (`loadAgentsDir.ts`)."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage

import app.main as entry
from app.cli.print import HeadlessOptions, main_thread_tools, run_headless
from app.query.config import build_run_config
from app.query.deps import get_production_deps
from app.tools.agent_tool.load_agents_dir import parse_agent_from_json, parse_agents_from_json
from tests.test_stream_json import Feed, Model, control, lines_of, responses, run_options, user

REVIEWER = {"description": "Reviews code", "prompt": "You are a code reviewer", "tools": ["ReadFile", "Grep"],
            "model": "inherit", "maxTurns": 3, "permissionMode": "plan", "ignored": "x"}


def test_a_json_agent_is_a_flag_settings_agent() -> None:
    agent = parse_agent_from_json("reviewer", REVIEWER)
    assert agent is not None
    assert (agent.name, agent.description, agent.prompt.strip(), agent.source) == (
        "reviewer", "Reviews code", "You are a code reviewer", "flagSettings")
    assert (agent.tools, agent.max_turns, agent.permission_mode, agent.phase) == (("ReadFile", "Grep"), 3, "plan", None)
    assert "ignored" not in agent.raw_frontmatter


@pytest.mark.parametrize("definition", [
    "text", {"prompt": "p"}, {"description": "", "prompt": "p"}, {"description": "d"},
    {"description": "d", "prompt": ""},
])
def test_an_invalid_json_agent_is_skipped(definition: Any) -> None:
    assert parse_agent_from_json("bad", definition) is None


def test_the_agents_object() -> None:
    agents = parse_agents_from_json({"reviewer": REVIEWER, "bad": {"description": "d"}})
    assert list(agents) == ["reviewer"]
    assert parse_agents_from_json(["not", "an", "object"]) == {}


def test_the_agents_flag() -> None:
    args = entry.parse_args(["--agents", json.dumps({"reviewer": REVIEWER})])
    assert list(entry.session_options(args).agents) == ["reviewer"]
    with pytest.raises(argparse.ArgumentTypeError, match="--agents is not valid JSON"):
        entry.agents_flag("{")


def test_flag_agents_join_and_override_the_roster(tmp_path: Path) -> None:
    tester = parse_agents_from_json({"tester": {"description": "Mine", "prompt": "Override"}})
    tools = main_thread_tools(build_run_config("r"), get_production_deps(), tmp_path, tmp_path / "home",
                              extra_agents={**parse_agents_from_json({"reviewer": REVIEWER}), **tester})
    agent_tool = next(t for t in tools if t.name == "Agent")
    assert "reviewer" in agent_tool.prompt and "Reviews code" in agent_tool.prompt
    assert "Mine" in agent_tool.prompt


def test_initialize_adds_agents_to_the_session(python_workspace: Any, tmp_path: Path) -> None:
    feed = Feed(control("i", "initialize", agents={"reviewer": REVIEWER}), user("hi", "u1"))
    written: list[str] = []

    def write(text: str) -> None:
        written.append(text)
        if any(m.get("type") == "result" for m in lines_of([text])):
            feed.close()

    model = Model([[AIMessage(content="ok")]])
    options = HeadlessOptions(output_format="stream-json", input_format="stream-json", verbose=True)
    asyncio.run(run_headless("sdk-agents", None, workspace=python_workspace, options=options,
                             input_blocks=feed.blocks(), write_stdout=write, **run_options(tmp_path, model)))
    out = lines_of(written)
    init = responses(out)["i"]["response"]
    assert {"name": "reviewer", "description": "Reviews code"} in init["agents"]
    system_init = next(m for m in out if m.get("subtype") == "init")
    assert "reviewer" in system_init["agents"]
    agent_tool = next(t for t in model.seen[0].tool_context.tools if t.name == "Agent")
    assert "reviewer" in agent_tool.prompt
