"""
Tests for Part E6: Attachments and delta pattern (app/loop/context/attachments.py).
"""

from langchain_core.messages import HumanMessage

from app.loop.context.attachments import (
    TDD_STATE_KEY,
    DeltaManager,
    compute_tdd_state_attachment,
    last_announced_tdd_state,
    tdd_state_payload,
    wrap_in_system_reminder,
)
from app.loop.ledger import PhaseLedger, TddPhase


def test_initial_agent_announcement() -> None:
    manager = DeltaManager()
    agents = {
        "tester": "Writes failing unit tests.",
        "developer": "Writes minimal implementation to turn tests green.",
    }

    # Empty message history -> initial announcement
    delta = manager.compute_agent_delta(agents, messages=())
    assert delta is not None
    assert delta.is_initial is True
    assert set(delta.added_types) == {"tester", "developer"}
    assert delta.removed_types == ()
    assert len(delta.added_lines) == 2

    init_msg = manager.format_agent_delta_message(delta)
    expected_content = (
        "<system-attachment type=\"agent_listing_delta\">\n"
        "Available agents:\n"
        "- developer: Writes minimal implementation to turn tests green.\n"
        "- tester: Writes failing unit tests.\n"
        "</system-attachment>"
    )
    assert init_msg.content == expected_content


def test_no_delta_when_agents_unchanged() -> None:
    manager = DeltaManager()
    agents = {
        "tester": "Writes failing unit tests.",
        "developer": "Writes minimal implementation.",
    }

    # First turn: announce
    delta = manager.compute_agent_delta(agents, messages=())
    assert delta is not None
    msg = manager.format_agent_delta_message(delta)

    # Second turn with msg in history: no diff
    delta2 = manager.compute_agent_delta(agents, messages=(msg,))
    assert delta2 is None


def test_delta_when_agent_added_and_removed() -> None:
    manager = DeltaManager()
    initial_agents = {
        "tester": "Writes tests.",
        "developer": "Writes code.",
    }
    init_delta = manager.compute_agent_delta(initial_agents, messages=())
    assert init_delta is not None
    init_msg = manager.format_agent_delta_message(init_delta)

    # Next state: developer removed, refactorer added
    updated_agents = {
        "tester": "Writes tests.",
        "refactorer": "Improves code structure.",
    }
    next_delta = manager.compute_agent_delta(updated_agents, messages=(init_msg,))
    assert next_delta is not None
    assert next_delta.is_initial is False
    assert next_delta.added_types == ("refactorer",)
    assert next_delta.removed_types == ("developer",)

    # Format next delta message (both added and removed)
    next_msg = manager.format_agent_delta_message(next_delta)
    expected_next_content = (
        "<system-attachment type=\"agent_listing_delta\">\n"
        "Newly available agents: refactorer\n"
        "- refactorer: Improves code structure.\n"
        "Removed agents: developer\n"
        "</system-attachment>"
    )
    assert next_msg.content == expected_next_content
    assert next_msg.additional_kwargs["agent_listing_delta"]["is_initial"] is False
    assert next_msg.additional_kwargs["agent_listing_delta"]["added_types"] == ["refactorer"]
    assert next_msg.additional_kwargs["agent_listing_delta"]["removed_types"] == ["developer"]


def test_mcp_instructions_delta() -> None:
    manager = DeltaManager()
    mcp_servers = {
        "filesystem": "Provides local file operations",
        "github": "Interacts with GitHub repositories",
    }
    delta = manager.compute_mcp_delta(mcp_servers, messages=())
    assert delta is not None
    assert set(delta.added_names) == {"filesystem", "github"}
    assert delta.removed_names == ()

    msg = manager.format_mcp_delta_message(delta)
    expected_mcp_content = (
        "<system-attachment type=\"mcp_instructions_delta\">\n"
        "Connected MCP servers: filesystem, github\n"
        "## filesystem\n"
        "Provides local file operations\n"
        "## github\n"
        "Interacts with GitHub repositories\n"
        "</system-attachment>"
    )
    assert msg.content == expected_mcp_content
    assert msg.additional_kwargs["mcp_instructions_delta"]["added_names"] == ["filesystem", "github"]
    assert msg.additional_kwargs["mcp_instructions_delta"]["removed_names"] == []

    # Next turn: github disconnected
    updated_servers = {"filesystem": "Provides local file operations"}
    delta2 = manager.compute_mcp_delta(updated_servers, messages=(msg,))
    assert delta2 is not None
    assert delta2.removed_names == ("github",)
    assert delta2.added_names == ()

    msg2 = manager.format_mcp_delta_message(delta2)
    expected_disconnect = (
        "<system-attachment type=\"mcp_instructions_delta\">\n"
        "Disconnected MCP servers: github\n"
        "</system-attachment>"
    )
    assert msg2.content == expected_disconnect
    assert msg2.additional_kwargs["mcp_instructions_delta"]["removed_names"] == ["github"]

    # Next turn: both connected and disconnected
    delta3 = manager.compute_mcp_delta({"git": "git operations"}, messages=(msg,))
    assert delta3 is not None
    msg3 = manager.format_mcp_delta_message(delta3)
    expected_both = (
        "<system-attachment type=\"mcp_instructions_delta\">\n"
        "Connected MCP servers: git\n"
        "## git\n"
        "git operations\n"
        "Disconnected MCP servers: filesystem, github\n"
        "</system-attachment>"
    )
    assert msg3.content == expected_both


def test_attachments_extraction_edge_cases() -> None:

    manager = DeltaManager()
    from langchain_core.messages import AIMessage, HumanMessage

    # Messages without kwargs or non-dict kwargs
    m_no_kwargs = HumanMessage(content="no kwargs")
    m_bad_kwargs = AIMessage(
        content="bad",
        additional_kwargs={"agent_listing_delta": "invalid_string", "mcp_instructions_delta": 123},
    )

    assert manager.extract_announced_agents([m_no_kwargs, m_bad_kwargs]) is None
    assert manager.extract_announced_mcp_servers([m_no_kwargs, m_bad_kwargs]) is None

    # Extraction continues across non-matching messages without breaking
    init_delta = manager.compute_agent_delta({"tester": "writes tests"}, messages=())
    assert init_delta is not None
    init_msg = manager.format_agent_delta_message(init_delta)

    # When first message has no kwargs, extraction continues and finds init_msg
    announced_agents = manager.extract_announced_agents([m_no_kwargs, m_bad_kwargs, init_msg])
    assert announced_agents == {"tester"}

    init_mcp_delta = manager.compute_mcp_delta({"fs": "desc"}, messages=())
    assert init_mcp_delta is not None
    init_mcp_msg = manager.format_mcp_delta_message(init_mcp_delta)
    announced_mcp = manager.extract_announced_mcp_servers([m_no_kwargs, m_bad_kwargs, init_mcp_msg])
    assert announced_mcp == {"fs"}

    # Added only vs removed only formatting checks
    added_only = manager.compute_agent_delta({"tester": "writes tests", "dev": "writes code"}, messages=[init_msg])
    assert added_only is not None
    msg_added = manager.format_agent_delta_message(added_only)
    assert "Newly available agents: dev" in msg_added.content
    assert "Removed agents:" not in msg_added.content

    removed_only = manager.compute_agent_delta({}, messages=[init_msg])
    assert removed_only is not None
    msg_removed = manager.format_agent_delta_message(removed_only)
    assert "Removed agents: tester" in msg_removed.content
    assert "Newly available agents:" not in msg_removed.content

    # is_initial omitted in kwargs defaults to False
    msg_no_flag = HumanMessage(
        content="",
        additional_kwargs={"agent_listing_delta": {"added_types": ["extra"]}},
    )
    assert manager.extract_announced_agents([init_msg, msg_no_flag]) == {"tester", "extra"}

    # Subsequent is_initial=True resets announced pool
    msg_reset = HumanMessage(
        content="",
        additional_kwargs={"agent_listing_delta": {"is_initial": True, "added_types": ["fresh"]}},
    )
    assert manager.extract_announced_agents([init_msg, msg_reset]) == {"fresh"}

    # Raw object with no kwargs
    from typing import Any, cast
    assert manager.extract_announced_agents([cast(Any, object())]) is None
    assert manager.extract_announced_mcp_servers([cast(Any, object())]) is None

    # Delta with empty dict should return empty set (not None)
    msg_empty_delta = HumanMessage(content="", additional_kwargs={"agent_listing_delta": {}})
    assert manager.extract_announced_agents([msg_empty_delta]) == set()
    msg_empty_mcp = HumanMessage(content="", additional_kwargs={"mcp_instructions_delta": {}})
    assert manager.extract_announced_mcp_servers([msg_empty_mcp]) == set()

    # Compute delta when unchanged returns None
    assert manager.compute_mcp_delta(
        {"filesystem": "desc"},
        [
            HumanMessage(
                content="",
                additional_kwargs={"mcp_instructions_delta": {"added_names": ["filesystem"], "removed_names": []}},
            )
        ],
    ) is None


# ── TDD state attachment ─────────────────────────────────────────────────────

def test_wrap_in_system_reminder() -> None:
    assert wrap_in_system_reminder("x") == "<system-reminder>\nx\n</system-reminder>"


def test_tdd_state_attachment_first_announcement() -> None:
    ledger = PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True, green_passed=False)
    msg = compute_tdd_state_attachment([], ledger)
    assert msg is not None
    assert msg.content == (
        "<system-reminder>\nTDD phase ledger (written only by RunTests from observed test runs):\n"
        "Current Phase: GREEN\nRed Confirmed: True\nGreen Passed: False\n</system-reminder>"
    )
    assert msg.additional_kwargs == {
        "is_meta": True,
        TDD_STATE_KEY: {"phase": "GREEN", "red_confirmed": True, "green_passed": False, "todo": None},
    }


def test_tdd_state_attachment_includes_todo_when_non_blank() -> None:
    msg = compute_tdd_state_attachment([], PhaseLedger(), "  - [ ] write test  \n")
    assert msg is not None
    assert str(msg.content).endswith(
        "Green Passed: False\n\nContents of TODO.md:\n- [ ] write test\n</system-reminder>")
    blank = compute_tdd_state_attachment([], PhaseLedger(), "   ")
    assert blank is not None and "TODO.md" not in str(blank.content)


def test_tdd_state_attachment_is_a_delta() -> None:
    ledger = PhaseLedger()
    first = compute_tdd_state_attachment([], ledger)
    assert first is not None
    history = [HumanMessage(content="hi"), first, HumanMessage(content="later")]
    assert compute_tdd_state_attachment(history, ledger) is None
    changed = compute_tdd_state_attachment(history, PhaseLedger(red_confirmed=True))
    assert changed is not None
    assert compute_tdd_state_attachment(history, ledger, "todo") is not None


def test_last_announced_tdd_state_uses_latest_and_ignores_noise() -> None:
    old = HumanMessage(content="", additional_kwargs={TDD_STATE_KEY: {"phase": "RED"}})
    new = HumanMessage(content="", additional_kwargs={TDD_STATE_KEY: {"phase": "GREEN"}})
    noise = HumanMessage(content="", additional_kwargs={TDD_STATE_KEY: "not a dict"})

    class Bare:
        pass

    assert last_announced_tdd_state([old, new, noise, Bare()]) == {"phase": "GREEN"}  # type: ignore[list-item]
    assert last_announced_tdd_state([]) is None


def test_tdd_state_payload() -> None:
    assert tdd_state_payload(PhaseLedger(phase=TddPhase.REFACTOR, red_confirmed=True, green_passed=True), "t") == {
        "phase": "REFACTOR", "red_confirmed": True, "green_passed": True, "todo": "t"}


# ── mutation-driven pins for the delta reconstruction ────────────────────────

def _delta_msg(key, **info):
    return HumanMessage(content="", additional_kwargs={key: info})


def test_agent_reconstruction_skips_noise_and_applies_removals_in_order() -> None:
    m = DeltaManager()
    noise = HumanMessage(content="plain")
    history = [
        noise,
        _delta_msg("agent_listing_delta", is_initial=True, added_types=["a", "b"]),
        HumanMessage(content="x", additional_kwargs={"agent_listing_delta": "not a dict"}),
        _delta_msg("agent_listing_delta", added_types=["c"], removed_types=["a"]),
    ]
    assert m.extract_announced_agents(history) == {"b", "c"}
    # missing is_initial means an incremental delta, not a reset
    assert m.extract_announced_agents([
        _delta_msg("agent_listing_delta", is_initial=True, added_types=["a"]),
        _delta_msg("agent_listing_delta", added_types=["b"]),
    ]) == {"a", "b"}
    # an initial announcement without added_types resets to empty
    assert m.extract_announced_agents([
        _delta_msg("agent_listing_delta", is_initial=True, added_types=["a"]),
        _delta_msg("agent_listing_delta", is_initial=True),
    ]) == set()
    # removals without additions
    assert m.extract_announced_agents([
        _delta_msg("agent_listing_delta", is_initial=True, added_types=["a", "b"]),
        _delta_msg("agent_listing_delta", removed_types=["b"]),
    ]) == {"a"}


def test_mcp_reconstruction_skips_noise_and_applies_removals() -> None:
    m = DeltaManager()
    history = [
        HumanMessage(content="plain"),
        _delta_msg("mcp_instructions_delta", added_names=["s1", "s2"]),
        _delta_msg("mcp_instructions_delta", removed_names=["s1"]),
    ]
    assert m.extract_announced_mcp_servers(history) == {"s2"}


def test_agent_delta_message_lists_are_comma_separated() -> None:
    from app.loop.context.attachments import AgentListingDelta

    msg = DeltaManager().format_agent_delta_message(
        AgentListingDelta(added_types=("a", "b"), added_lines=(), removed_types=("c", "d"), is_initial=False))
    assert "Newly available agents: a, b" in str(msg.content)
    assert "Removed agents: c, d" in str(msg.content)
