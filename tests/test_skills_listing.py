"""Phase 8: the skill listing (`tools/SkillTool/prompt.ts` -> `formatCommandsWithinBudget`,
`getCharBudget`; `utils/attachments.ts` -> `getSkillListingAttachments`; `utils/messages.ts` ->
`normalizeAttachmentForAPI`'s `skill_listing`)."""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import HumanMessage

from app.skills.load_skills_dir import SkillCommand, SkillRegistry
from app.tool import ToolResult, build_tool
from app.tools.skill_tool.prompt import (
    DEFAULT_CHAR_BUDGET,
    MAX_LISTING_DESC_CHARS,
    format_commands_within_budget,
    get_char_budget,
)
from app.utils.attachments import (
    Attachment,
    get_skill_listing_attachments,
    normalize_attachment_for_api,
)


def _cmd(name: str, description: str = "does things", source: str = "userSettings", **kw) -> SkillCommand:
    return SkillCommand(name=name, description=description, markdown_content="x", source=source,
                        loaded_from="bundled" if source == "bundled" else "skills", skill_root=None, **kw)


class TestBudget:
    def test_the_budget(self, monkeypatch):
        monkeypatch.delenv("SLASH_COMMAND_TOOL_CHAR_BUDGET", raising=False)
        assert get_char_budget() == DEFAULT_CHAR_BUDGET == 8_000
        assert get_char_budget(200_000) == 8_000
        assert get_char_budget(100_001) == 4_000
        monkeypatch.setenv("SLASH_COMMAND_TOOL_CHAR_BUDGET", "123")
        assert get_char_budget(200_000) == 123
        monkeypatch.setenv("SLASH_COMMAND_TOOL_CHAR_BUDGET", "zero")
        assert get_char_budget() == 8_000

    def test_entries_within_the_budget_are_full(self):
        assert format_commands_within_budget([]) == ""
        cmds = [_cmd("a", when_to_use="when x"), _cmd("b")]
        assert format_commands_within_budget(cmds) == "- a: does things - when x\n- b: does things"

    def test_a_long_description_is_capped(self):
        out = format_commands_within_budget([_cmd("a", "z" * 400)])
        assert out == "- a: " + "z" * (MAX_LISTING_DESC_CHARS - 1) + "…"

    def test_over_budget_trims_non_bundled_descriptions(self, monkeypatch):
        monkeypatch.setenv("SLASH_COMMAND_TOOL_CHAR_BUDGET", "122")
        cmds = [_cmd("bund", "b" * 60, source="bundled"), _cmd("u1", "u" * 60), _cmd("u2", "v" * 60)]
        lines = format_commands_within_budget(cmds).split("\n")
        assert lines[0] == "- bund: " + "b" * 60
        # remaining = 122 - (68 + 1) = 53; names overhead = (2+4)*2 + 1 = 13; 40 // 2 = 20 per desc.
        assert lines[1] == "- u1: " + "u" * 19 + "…"
        assert lines[2] == "- u2: " + "v" * 19 + "…"

    def test_one_below_the_minimum_description_keeps_only_names(self, monkeypatch):
        # 121: (52 - 13) // 2 = 19 < MIN_DESC_LENGTH.
        monkeypatch.setenv("SLASH_COMMAND_TOOL_CHAR_BUDGET", "121")
        cmds = [_cmd("bund", "b" * 60, source="bundled"), _cmd("u1", "u" * 60), _cmd("u2", "v" * 60)]
        assert format_commands_within_budget(cmds).split("\n")[1:] == ["- u1", "- u2"]

    def test_far_over_budget_keeps_only_names(self, monkeypatch):
        monkeypatch.setenv("SLASH_COMMAND_TOOL_CHAR_BUDGET", "40")
        cmds = [_cmd("bund", "b" * 20, source="bundled"), _cmd("u1", "u" * 60)]
        assert format_commands_within_budget(cmds) == "- bund: " + "b" * 20 + "\n- u1"

    def test_entries_that_exactly_fit_the_budget_are_full(self, monkeypatch):
        cmds = [_cmd("a"), _cmd("b")]
        full = "- a: does things\n- b: does things"
        monkeypatch.setenv("SLASH_COMMAND_TOOL_CHAR_BUDGET", str(len(full)))
        assert format_commands_within_budget(cmds) == full

    def test_only_bundled_entries_over_budget_stay_full_one_per_line(self, monkeypatch):
        monkeypatch.setenv("SLASH_COMMAND_TOOL_CHAR_BUDGET", "5")
        cmds = [_cmd("b1", source="bundled"), _cmd("b2", source="bundled")]
        assert format_commands_within_budget(cmds) == "- b1: does things\n- b2: does things"

    def test_truncate_at_its_edges(self):
        from app.tools.skill_tool.prompt import truncate

        assert truncate("abcde", 5) == "abcde"
        assert truncate("abc", 2) == "a…"
        assert truncate("abc", 1) == "…"

    def test_only_bundled_over_budget_stays_full(self, monkeypatch):
        monkeypatch.setenv("SLASH_COMMAND_TOOL_CHAR_BUDGET", "5")
        cmds = [_cmd("bund", "b" * 20, source="bundled")]
        assert format_commands_within_budget(cmds) == "- bund: " + "b" * 20


def _ctx(registry: SkillRegistry | None, tools=("Skill",), agent_id=None):
    """The source reads `tools`, `agent_id` and `skill_registry` (the field the runner adds)."""
    built = tuple(build_tool(name=n, prompt=n, call=lambda a, c: ToolResult(content="")) for n in tools)
    return SimpleNamespace(tools=built, agent_id=agent_id, skill_registry=registry)


def _listing(ctx):
    return get_skill_listing_attachments([HumanMessage(content="t")], ctx)


class TestListingAttachment:
    def test_the_first_listing_announces_every_skill_once(self):
        reg = SkillRegistry([_cmd("a"), _cmd("b")])
        (att,) = _listing(_ctx(reg))
        assert att == Attachment("skill_listing", {"content": "- a: does things\n- b: does things",
                                                   "skillCount": 2, "isInitial": True})
        assert _listing(_ctx(reg)) == []

    def test_a_new_skill_is_announced_as_a_delta(self, tmp_path):
        reg = SkillRegistry([_cmd("a")], [_cmd("late", paths=("src",))])
        _listing(_ctx(reg))
        reg.activate_conditional_skills_for_paths(["src/x.py"], str(tmp_path))
        (att,) = _listing(_ctx(reg))
        assert att.data == {"content": "- late: does things", "skillCount": 1, "isInitial": False}

    def test_each_agent_gets_its_own_listing(self):
        reg = SkillRegistry([_cmd("a")])
        assert len(_listing(_ctx(reg))) == 1
        assert len(_listing(_ctx(reg, agent_id="w1"))) == 1
        assert _listing(_ctx(reg, agent_id="w1")) == []
        assert len(_listing(_ctx(reg, agent_id="w2"))) == 1
        assert _listing(_ctx(reg)) == []

    def test_model_disabled_skills_are_not_listed(self):
        reg = SkillRegistry([_cmd("hidden", disable_model_invocation=True)])
        assert _listing(_ctx(reg)) == []

    def test_no_skill_tool_or_no_registry_means_no_listing(self):
        assert _listing(_ctx(SkillRegistry([_cmd("a")]), tools=("ReadFile",))) == []
        assert _listing(_ctx(None)) == []

    def test_the_budget_follows_the_registrys_model(self, monkeypatch):
        import app.utils.attachments as skills_mod

        seen: list[object] = []

        def record(cmds: object, window: object = None) -> str:
            seen.append(window)
            return "x"

        monkeypatch.setattr(skills_mod, "format_commands_within_budget", record)
        _listing(_ctx(SkillRegistry([_cmd("a")], model="claude-sonnet-4-6")))
        _listing(_ctx(SkillRegistry([_cmd("a")])))
        from app.utils.tokens import get_context_window_for_model

        assert seen == [get_context_window_for_model("claude-sonnet-4-6"), None]


def test_the_listing_text_for_the_model():
    att = Attachment("skill_listing", {"content": "- a: b", "skillCount": 1, "isInitial": True})
    assert normalize_attachment_for_api(att) == [
        "The following skills are available for use with the Skill tool:\n\n- a: b"]
    assert normalize_attachment_for_api(Attachment("skill_listing", {"content": ""})) == []
