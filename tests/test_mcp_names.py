"""Phase 8 MCP: names (`services/mcp/normalization.ts`, `mcpStringUtils.ts`) and env expansion
(`services/mcp/envExpansion.ts`)."""

from __future__ import annotations

import pytest

from app.services.mcp.env_expansion import expand_env_vars_in_string
from app.services.mcp.normalization import (
    build_mcp_tool_name,
    get_mcp_display_name,
    get_mcp_prefix,
    mcp_info_from_string,
    normalize_name_for_mcp,
)


@pytest.mark.parametrize(
    ("name", "normalized"),
    [
        ("my-server", "my-server"),
        ("my server.v2", "my_server_v2"),
        ("a__b", "a__b"),
        ("claude.ai  Slack!", "claude_ai_Slack"),
        ("ünï", "_n_"),
    ],
)
def test_normalize_name_for_mcp(name, normalized):
    assert normalize_name_for_mcp(name) == normalized


def test_tool_names_and_prefixes():
    assert get_mcp_prefix("db tools") == "mcp__db_tools__"
    assert build_mcp_tool_name("db tools", "run.query") == "mcp__db_tools__run_query"
    assert get_mcp_display_name("mcp__db_tools__run_query", "db tools") == "run_query"


@pytest.mark.parametrize(
    ("value", "info"),
    [
        ("mcp__srv__tool", ("srv", "tool")),
        ("mcp__srv__a__b", ("srv", "a__b")),
        ("mcp__srv", ("srv", None)),
        ("mcp__", None),
        ("Bash", None),
        ("xmcp__srv__t", None),
    ],
)
def test_mcp_info_from_string(value, info):
    assert mcp_info_from_string(value) == info


def test_env_expansion(monkeypatch):
    monkeypatch.setenv("TOKEN", "abc")
    monkeypatch.delenv("MISSING", raising=False)
    monkeypatch.delenv("OTHER", raising=False)
    assert expand_env_vars_in_string("Bearer ${TOKEN}") == ("Bearer abc", [])
    assert expand_env_vars_in_string("${MISSING:-https://x}/mcp") == ("https://x/mcp", [])
    assert expand_env_vars_in_string("${MISSING}-${OTHER}-${MISSING}") == (
        "${MISSING}-${OTHER}-${MISSING}", ["MISSING", "OTHER", "MISSING"])
    assert expand_env_vars_in_string("${A:-x:-y}") == ("x", [])  # JS `split(":-", 2)` drops the rest
    assert expand_env_vars_in_string("no vars") == ("no vars", [])
    monkeypatch.setenv("EMPTY", "")
    assert expand_env_vars_in_string("[${EMPTY:-d}]") == ("[]", [])


def test_only_the_leading_prefix_is_removed_for_display():
    from app.services.mcp.normalization import get_mcp_display_name

    assert get_mcp_display_name("mcp__a__mcp__a__x", "a") == "mcp__a__x"


def test_a_claude_ai_name_keeps_a_trailing_x():
    from app.services.mcp.normalization import normalize_name_for_mcp

    assert normalize_name_for_mcp("claude.ai My ToolX ") == "claude_ai_My_ToolX"
