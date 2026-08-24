"""
The tool registry, `resolve_tools`, and the LangChain shim.

The resolution order is load-bearing and each step has its own test: denylist first,
wildcard short-circuit, then allowlist intersection — with unknown names collected rather
than fatal.
"""

from __future__ import annotations

import pytest

from app.tools.base import BuiltTool
from app.tools.langchain import to_langchain_tool, to_langchain_tools
from app.tools.registry import ResolvedTools, ToolRegistry, resolve_tools
from tests.conftest import EchoArgs, make_read_tool, make_tool


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry(
        [
            make_read_tool("ReadFile"),
            make_read_tool("Grep"),
            make_tool("WriteFile"),
            make_tool("Bash"),
        ]
    )


class TestRegistry:
    def test_register_and_get(self):
        registry = ToolRegistry()
        tool = make_tool("Echo")
        registry.register(tool)
        assert registry.get("Echo") is tool
        assert "Echo" in registry
        assert len(registry) == 1

    def test_get_unknown_returns_none(self, registry):
        assert registry.get("Nope") is None
        assert "Nope" not in registry

    def test_duplicate_registration_is_rejected(self, registry):
        """Two tools answering to one name is a definition bug, not a runtime condition."""
        with pytest.raises(ValueError, match="already registered"):
            registry.register(make_tool("Bash"))

    def test_names_and_all_are_sorted(self, registry):
        """Order feeds the system prompt; an unstable one invalidates the prompt cache."""
        assert registry.names() == ["Bash", "Grep", "ReadFile", "WriteFile"]
        assert [tool.name for tool in registry.all()] == registry.names()

    def test_enabled_filters_out_disabled_tools(self):
        registry = ToolRegistry(
            [make_tool("On"), make_tool("Off", is_enabled=lambda: False)]
        )
        assert [tool.name for tool in registry.enabled()] == ["On"]
        assert len(registry) == 2


class TestWildcardResolution:
    def test_none_means_everything(self, registry):
        resolved = resolve_tools(None, registry)
        assert resolved.has_wildcard is True
        assert len(resolved.resolved) == 4

    def test_explicit_star_means_everything(self, registry):
        assert resolve_tools(["*"], registry).has_wildcard is True

    def test_star_alongside_another_name_is_not_a_wildcard(self, registry):
        """Only the exact list `["*"]` short-circuits, matching the original."""
        resolved = resolve_tools(["*", "Grep"], registry)
        assert resolved.has_wildcard is False
        assert [tool.name for tool in resolved.resolved] == ["Grep"]

    def test_wildcard_still_respects_the_denylist(self, registry):
        resolved = resolve_tools(None, registry, disallowed_tools=["Bash"])
        assert resolved.has_wildcard is True
        assert "Bash" not in [tool.name for tool in resolved.resolved]

    def test_wildcard_excludes_disabled_tools(self):
        registry = ToolRegistry([make_tool("On"), make_tool("Off", is_enabled=lambda: False)])
        assert [t.name for t in resolve_tools(None, registry).resolved] == ["On"]


class TestAllowlistResolution:
    def test_intersects_with_the_registry(self, registry):
        resolved = resolve_tools(["ReadFile", "Grep"], registry)
        assert [tool.name for tool in resolved.resolved] == ["Grep", "ReadFile"]
        assert resolved.valid == ("ReadFile", "Grep")
        assert resolved.invalid == ()

    def test_unknown_names_are_collected_not_fatal(self, registry):
        """One typo costs that entry, never the agent's whole toolbelt."""
        resolved = resolve_tools(["ReadFile", "Grpe", "Bahs"], registry)
        assert [tool.name for tool in resolved.resolved] == ["ReadFile"]
        assert resolved.invalid == ("Grpe", "Bahs")
        assert resolved.valid == ("ReadFile",)

    def test_every_name_unknown_yields_an_empty_toolbelt(self, registry):
        resolved = resolve_tools(["Nope"], registry)
        assert resolved.resolved == ()
        assert resolved.invalid == ("Nope",)

    def test_empty_list_yields_nothing(self, registry):
        """An explicit empty list is a real declaration, not a missing one."""
        resolved = resolve_tools([], registry)
        assert resolved.has_wildcard is False
        assert resolved.resolved == ()

    def test_duplicates_are_deduplicated(self, registry):
        resolved = resolve_tools(["Grep", "Grep", "ReadFile"], registry)
        assert [tool.name for tool in resolved.resolved] == ["Grep", "ReadFile"]

    def test_resolution_is_sorted(self, registry):
        resolved = resolve_tools(["WriteFile", "Bash", "Grep"], registry)
        assert [tool.name for tool in resolved.resolved] == ["Bash", "Grep", "WriteFile"]

    def test_disabled_tools_are_not_resolvable(self):
        registry = ToolRegistry([make_tool("Off", is_enabled=lambda: False)])
        resolved = resolve_tools(["Off"], registry)
        assert resolved.resolved == ()
        assert resolved.invalid == ("Off",)


class TestDenylistPrecedence:
    def test_denylist_beats_the_allowlist(self, registry):
        """A denial cannot be undone by also listing the tool as allowed."""
        resolved = resolve_tools(["Bash", "Grep"], registry, disallowed_tools=["Bash"])
        assert [tool.name for tool in resolved.resolved] == ["Grep"]
        assert resolved.invalid == ("Bash",)

    def test_denylist_entries_may_carry_a_pattern(self, registry):
        resolved = resolve_tools(["Bash"], registry, disallowed_tools=["Bash(rm *)"])
        assert resolved.resolved == ()

    def test_empty_denylist_changes_nothing(self, registry):
        assert len(resolve_tools(["Bash"], registry, disallowed_tools=[]).resolved) == 1


class TestAgentTargetScoping:
    def test_targets_are_parsed_from_the_spec(self, registry):
        registry.register(make_tool("Agent"))
        resolved = resolve_tools(["Agent(researcher,refactorer)"], registry)
        assert resolved.allowed_agent_types == ("researcher", "refactorer")

    def test_bare_agent_spec_carries_no_targets(self, registry):
        registry.register(make_tool("Agent"))
        assert resolve_tools(["Agent"], registry).allowed_agent_types == ()

    def test_targets_are_parsed_even_when_the_tool_is_absent(self, registry):
        """Phase 1B ships no Agent tool; capturing the targets now is what makes Phase 5 additive."""
        resolved = resolve_tools(["Agent(researcher)"], registry)
        assert resolved.allowed_agent_types == ("researcher",)
        assert resolved.invalid == ("Agent(researcher)",)

    def test_no_agent_spec_means_no_targets(self, registry):
        assert resolve_tools(["Grep"], registry).allowed_agent_types == ()


class TestResolvedToolsDefaults:
    def test_defaults_are_empty(self):
        resolved = ResolvedTools()
        assert resolved.has_wildcard is False
        assert resolved.resolved == ()
        assert resolved.allowed_agent_types == ()


class TestLangChainShim:
    def test_emits_a_function_schema(self):
        tool = make_read_tool("Grep")
        schema = to_langchain_tool(tool)
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "Grep"
        assert schema["function"]["description"] == tool.prompt()

    def test_parameters_come_from_the_pydantic_schema(self):
        schema = to_langchain_tool(make_read_tool("Echo"))
        assert schema["function"]["parameters"] == EchoArgs.model_json_schema()

    def test_uses_the_long_prompt_not_the_short_description(self):
        """The model reads `prompt()`; `description(args)` is for compact logging."""
        tool = make_tool("Grep", description=lambda args: "short line")
        assert to_langchain_tool(tool)["function"]["description"] != "short line"

    def test_batch_conversion_preserves_order(self):
        tools: list[BuiltTool] = [make_tool("B"), make_tool("A")]
        assert [s["function"]["name"] for s in to_langchain_tools(tools)] == ["B", "A"]

    def test_empty_list(self):
        assert to_langchain_tools([]) == []


class TestAgentSpecGuard:
    """
    The Agent special case is `tool_name == "Agent" AND rule_content` — both halves. With
    `or`, any parenthesized spec on any tool would be read as a delegation target list.
    """

    def test_a_pattern_on_another_tool_is_not_a_target_list(self, registry):
        assert resolve_tools(["Bash(rm *)"], registry).allowed_agent_types == ()

    def test_a_pattern_on_another_tool_still_resolves_the_tool(self, registry):
        resolved = resolve_tools(["Bash(rm *)"], registry)
        assert [t.name for t in resolved.resolved] == ["Bash"]

    def test_only_the_agent_spec_contributes_targets(self, registry):
        registry.register(make_tool("Agent"))
        resolved = resolve_tools(["Bash(rm *)", "Agent(researcher)"], registry)
        assert resolved.allowed_agent_types == ("researcher",)


class TestResolutionReporting:
    def test_valid_records_the_spec_string_not_the_tool_name(self, registry):
        """`Bash(rm *)` is what the definition said; reporting bare `Bash` would lose it."""
        assert resolve_tools(["Bash(rm *)"], registry).valid == ("Bash(rm *)",)

    def test_invalid_records_the_whole_spec(self, registry):
        assert resolve_tools(["Nope(x)"], registry).invalid == ("Nope(x)",)

    def test_wildcard_reports_no_valid_or_invalid_names(self, registry):
        resolved = resolve_tools(None, registry)
        assert resolved.valid == ()
        assert resolved.invalid == ()

    def test_a_denied_tool_is_reported_as_invalid(self, registry):
        resolved = resolve_tools(["Bash"], registry, disallowed_tools=["Bash"])
        assert resolved.invalid == ("Bash",)
        assert resolved.valid == ()
