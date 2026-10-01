"""
Tests for Part E6: Attachments and delta pattern (app/loop/context/attachments.py).
"""

from app.loop.context.attachments import DeltaManager


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
