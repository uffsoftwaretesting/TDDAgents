"""
Upstream's verification agent and its contract are on by default (user decision 2026-11-20).

Upstream gates both behind `feature('VERIFICATION_AGENT')` and GrowthBook's `tengu_hive_evidence`
(`reference/claude-code/src/tools/AgentTool/builtInAgents.ts:65-68`, `constants/prompts.ts:392-394`), off in
public builds. Here one switch, `TDDAGENTS_VERIFICATION_AGENT`, governs the roster, the Agent tool's listing, the
contract and the coordinator's prompt together; it is on unless set false.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.context import get_session_specific_guidance_section
from app.coordinator.coordinator_mode import get_coordinator_system_prompt
from app.tools.agent_tool.load_agents_dir import (
    VERIFICATION_AGENT_TYPE,
    get_agent_definitions_with_overrides,
    is_verification_agent_enabled,
)
from app.tools.agent_tool.prompt import get_prompt
from app.types.permissions import PermissionMode

TOOLS = frozenset({"Agent", "AskUserQuestion"})


def _everything(tmp_path: Path) -> tuple[bool, bool, bool, bool]:
    defs = get_agent_definitions_with_overrides(project_dir=tmp_path, user_home=tmp_path)
    listing = get_prompt(list(defs.values()))
    contract = get_session_specific_guidance_section(TOOLS, False) or ""
    return (VERIFICATION_AGENT_TYPE in defs, f"- {VERIFICATION_AGENT_TYPE}:" in listing,
            'subagent_type="verification"' in contract, "verification" in get_coordinator_system_prompt())


def test_with_no_switch_the_verifier_is_everywhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TDDAGENTS_VERIFICATION_AGENT", raising=False)
    assert is_verification_agent_enabled() is True
    assert _everything(tmp_path) == (True, True, True, True)


@pytest.mark.parametrize("value", ["0", "false", "FALSE", " no ", "off"])
def test_switched_off_it_is_nowhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("TDDAGENTS_VERIFICATION_AGENT", value)
    assert is_verification_agent_enabled() is False
    assert _everything(tmp_path) == (False, False, False, False)


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "", "anything"])
def test_any_other_value_keeps_it_on(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("TDDAGENTS_VERIFICATION_AGENT", value)
    assert is_verification_agent_enabled() is True


def test_the_verifier_is_upstreams_background_bubble_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`verificationAgent.ts`: background, project edits disallowed; `bubble` lets its prompts reach the user."""
    monkeypatch.delenv("TDDAGENTS_VERIFICATION_AGENT", raising=False)
    verifier = get_agent_definitions_with_overrides(project_dir=tmp_path, user_home=tmp_path)[VERIFICATION_AGENT_TYPE]
    assert verifier.permission_mode == PermissionMode.BUBBLE
    assert verifier.background is True
    assert verifier.source == "built-in"
    assert "VERDICT: PASS" in verifier.prompt and "VERDICT: FAIL" in verifier.prompt


def test_the_tdd_contract_sends_a_fail_through_a_new_plan_item() -> None:
    from app.context import read_system_prompt_file

    contract = read_system_prompt_file("tdd-contract.md")
    assert "the verification agent's `FAIL`" in contract and "`UpdatePlan`" in contract


def test_a_session_offers_the_verifier_to_its_agent_tool(python_workspace, tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """Integration: `open_session_run` builds the Agent tool from the roster, which now holds the verifier."""
    import asyncio

    from app.cli.print import open_session_run
    from app.tdd.plan import TddPlan
    from app.types.permissions import ToolPermissionContext

    monkeypatch.delenv("TDDAGENTS_VERIFICATION_AGENT", raising=False)

    async def go() -> str:
        async with open_session_run(run_id="loop-verifier-roster", plan=TddPlan(), workspace=python_workspace,
                                    permission_context=ToolPermissionContext(), project_root=tmp_path,
                                    home=tmp_path / "home", managed_dir=tmp_path / "etc") as run:
            agent = next(t for t in run.ctx.tools if t.name == "Agent")
            return str(agent.prompt)

    assert f"- {VERIFICATION_AGENT_TYPE}:" in asyncio.run(go())
