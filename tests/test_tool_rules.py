"""Permission-rule parsing: `ToolName` and `ToolName(content)`."""

from __future__ import annotations

import pytest

from app.tools.rules import (
    PermissionRule,
    escape_rule_content,
    parse_rule,
    rule_matches,
    split_targets,
    unescape_rule_content,
)


class TestParseRule:
    def test_bare_tool_name(self):
        assert parse_rule("Bash") == PermissionRule(tool_name="Bash", rule_content=None)

    def test_tool_name_with_content(self):
        assert parse_rule("Bash(pip *)") == PermissionRule(tool_name="Bash", rule_content="pip *")

    @pytest.mark.parametrize("spec", ["Bash()", "Bash(*)"])
    def test_empty_and_wildcard_content_are_tool_wide(self, spec):
        """`Bash()` and `Bash(*)` both mean the tool unconditionally, not a rule named '*'."""
        assert parse_rule(spec) == PermissionRule(tool_name="Bash", rule_content=None)

    def test_missing_tool_name_is_not_a_rule(self):
        assert parse_rule("(foo)") == PermissionRule(tool_name="(foo)", rule_content=None)

    def test_trailing_text_after_close_paren_is_not_a_rule(self):
        assert parse_rule("Bash(a) trailing").tool_name == "Bash(a) trailing"
        assert parse_rule("Bash(a) trailing").rule_content is None

    def test_unmatched_open_paren_is_not_a_rule(self):
        assert parse_rule("Bash(a").tool_name == "Bash(a"

    def test_close_before_open_is_not_a_rule(self):
        assert parse_rule("Bash)a(").tool_name == "Bash)a("

    def test_escaped_parens_survive_the_round_trip(self):
        rule = parse_rule(r'Bash(python -c "print\(1\)")')
        assert rule.tool_name == "Bash"
        assert rule.rule_content == 'python -c "print(1)"'

    def test_last_unescaped_close_paren_wins(self):
        rule = parse_rule(r"Bash(a\)b)")
        assert rule.tool_name == "Bash"
        assert rule.rule_content == "a)b"

    def test_agent_target_spec(self):
        rule = parse_rule("Agent(researcher,refactorer)")
        assert rule.tool_name == "Agent"
        assert rule.rule_content == "researcher,refactorer"

    def test_empty_string(self):
        assert parse_rule("") == PermissionRule(tool_name="", rule_content=None)


class TestEscaping:
    def test_escape_backslashes_before_parens(self):
        assert escape_rule_content("a()") == r"a\(\)"
        assert escape_rule_content("a\\b") == "a\\\\b"

    def test_round_trip(self):
        for original in ["plain", "psycopg2.connect()", "a\\b", "((()))", 'echo "x\\ny"']:
            assert unescape_rule_content(escape_rule_content(original)) == original

    def test_unescape_order_does_not_double_unescape(self):
        # An escaped backslash followed by a literal paren must not become an escaped paren.
        assert unescape_rule_content(escape_rule_content("a\\(")) == "a\\("


class TestSplitTargets:
    def test_splits_and_strips(self):
        assert split_targets("researcher, refactorer") == ["researcher", "refactorer"]

    def test_single_target(self):
        assert split_targets("researcher") == ["researcher"]

    def test_none_and_empty(self):
        assert split_targets(None) == []
        assert split_targets("") == []

    def test_drops_empty_segments(self):
        assert split_targets("a,,b,") == ["a", "b"]


class TestRuleMatches:
    def test_tool_wide_rule_matches_any_command(self):
        assert rule_matches("Bash", "Bash", "anything at all") is True
        assert rule_matches("Bash", "Bash", None) is True

    def test_different_tool_never_matches(self):
        assert rule_matches("Bash(pip *)", "Grep", "pip install x") is False

    def test_glob_content(self):
        assert rule_matches("Bash(pip *)", "Bash", "pip install requests") is True
        assert rule_matches("Bash(pip *)", "Bash", "npm install") is False

    def test_exact_content(self):
        assert rule_matches("Bash(git commit)", "Bash", "git commit") is True

    def test_whitespace_delimited_prefix(self):
        """`git commit` must catch `git commit -m x` without catching `git commit-tree`."""
        assert rule_matches("Bash(git commit)", "Bash", 'git commit -m "x"') is True
        assert rule_matches("Bash(git commit)", "Bash", "git commit-tree") is False

    def test_content_rule_with_no_command_does_not_match(self):
        assert rule_matches("Bash(pip *)", "Bash", None) is False

    def test_matching_is_case_sensitive(self):
        assert rule_matches("Bash(PIP *)", "Bash", "pip install x") is False


class TestBackslashCounting:
    """
    `_is_escaped` walks left counting backslashes and calls the character escaped on an
    odd count. Every off-by-one in that walk needs a case with the right parity and the
    right position to be visible at all.
    """

    def test_zero_backslashes_is_not_escaped(self):
        assert parse_rule("Bash(a)") == PermissionRule("Bash", "a")

    def test_one_backslash_escapes(self):
        """`\\)` is literal, so the real close paren is the final one."""
        assert parse_rule(r"Bash(a\)b)") == PermissionRule("Bash", "a)b")

    def test_two_backslashes_do_not_escape(self):
        r"""`\\` is a literal backslash, so the `)` after it still closes."""
        rule = parse_rule(r"Bash(a\\)")
        assert rule.tool_name == "Bash"
        assert rule.rule_content == "a\\"

    def test_three_backslashes_escape(self):
        r"""`\\\)` is a literal backslash then an escaped paren — odd count, escaped."""
        rule = parse_rule(r"Bash(a\\\)b)")
        assert rule.tool_name == "Bash"
        assert rule.rule_content == "a\\)b"

    def test_four_backslashes_do_not_escape(self):
        rule = parse_rule(r"Bash(a\\\\)")
        assert rule.tool_name == "Bash"
        assert rule.rule_content == "a\\\\"

    def test_backslash_at_the_very_start_of_the_string(self):
        r"""The walk must stop at index 0 rather than reading past it."""
        assert parse_rule(r"\(x)").tool_name == r"\(x)"

    def test_escaped_open_paren_is_not_the_opening(self):
        rule = parse_rule(r"Bash\((real)")
        assert rule.tool_name == r"Bash\("
        assert rule.rule_content == "real"


class TestParenIndexBoundaries:
    def test_single_character_tool_name(self):
        """The opening paren sits at index 1 — the first index an off-by-one would skip."""
        assert parse_rule("B(x)") == PermissionRule("B", "x")

    def test_close_paren_without_an_open_paren(self):
        """A stray `)` must not be read as closing a paren that was never opened."""
        assert parse_rule("Bash)") == PermissionRule("Bash)", None)

    def test_open_paren_at_index_zero_has_no_tool_name(self):
        assert parse_rule("(x)") == PermissionRule("(x)", None)

    def test_only_the_last_close_paren_counts(self):
        assert parse_rule("Bash(a(b)c)") == PermissionRule("Bash", "a(b)c")

    def test_adjacent_parens(self):
        assert parse_rule("Bash(())") == PermissionRule("Bash", "()")

    def test_content_that_is_a_single_close_paren(self):
        assert parse_rule(r"Bash(\))") == PermissionRule("Bash", ")")

    def test_two_character_string(self):
        assert parse_rule("()") == PermissionRule("()", None)

    def test_close_paren_at_index_one(self):
        assert parse_rule("a)").tool_name == "a)"


class TestEscapeHelpersDirectly:
    def test_escape_is_not_a_no_op(self):
        assert escape_rule_content("a(b)c") == r"a\(b\)c"

    def test_unescape_is_not_a_no_op(self):
        assert unescape_rule_content(r"a\(b\)c") == "a(b)c"

    def test_backslash_doubling(self):
        assert escape_rule_content("\\") == "\\\\"
        assert unescape_rule_content("\\\\") == "\\"

    def test_escaping_is_ordered_backslashes_first(self):
        r"""Escaping `(` before `\` would produce `\\(` and unescape to the wrong string."""
        assert unescape_rule_content(escape_rule_content("\\(")) == "\\("
