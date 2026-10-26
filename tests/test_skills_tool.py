"""Phase 8: the Skill tool (`tools/SkillTool/SkillTool.ts`), its forked path
(`executeForkedSkill`, `utils/forkedAgent.ts`) and agent preloading (`runAgent.ts`'s
`skillsToPreload`, `resolveSkillName`)."""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.loop.agents.definition import AgentDefinition
from app.loop.context.app_state import AppState, AppStateStore
from app.loop.context.tool_context import tool_context_for
from app.loop.ledger import PhaseLedger, TddPhase
from app.loop.permissions.gate import has_permissions_to_use_tool
from app.loop.permissions.types import (
    PermissionBehavior,
    PermissionMode,
    PermissionRule,
    PermissionRuleSource,
    ToolPermissionContext,
)
from app.loop.skills.definition import SkillCommand
from app.loop.skills.loader import SkillRegistry
from app.loop.skills.preload import resolve_skill_name, skill_preload_messages
from app.loop.skills.tool import (
    MAX_RESULT_SIZE_CHARS,
    SKILL_TOOL_SCHEMA,
    build_skill_tool,
    context_with_allowed_tools,
    coordinator_skill_summary,
    extract_result_text,
    format_skill_loading_metadata,
    select_fork_agent,
    skill_has_only_safe_properties,
    with_allowed_tools,
)
from app.loop.tools.fs import build_write_file_tool
from app.loop.tools.orchestration import apply_context_modifier
from tests.test_loop_engine import CONFIG, FakeModel, asks_for, make_fake_deps

COMMAND = PermissionRuleSource.COMMAND


def _skill(name: str = "design", body: str = "Design tests for $ARGUMENTS.", **kw: Any) -> SkillCommand:
    kw.setdefault("source", "bundled")
    kw.setdefault("loaded_from", "bundled")
    kw.setdefault("skill_root", None)
    return SkillCommand(name=name, description="d", markdown_content=body, **kw)


def _tool(*skills: SkillCommand, model: FakeModel | None = None, config=CONFIG, definitions=None):
    deps = make_fake_deps(model or FakeModel([[AIMessage(content="forked report")]]))
    return build_skill_tool(SkillRegistry(skills), config=config, deps=deps, agent_definitions=definitions)


def _ctx(perm: ToolPermissionContext | None = None, ledger: PhaseLedger | None = None, **kw: Any):
    store = AppStateStore(AppState(phase_ledger=ledger or PhaseLedger(),
                                   tool_permission_context=perm or ToolPermissionContext()))
    return tool_context_for(store, messages=(HumanMessage(content="go"),), **kw)


def _call(tool, args: dict[str, Any], ctx):
    return asyncio.run(tool.call(args, ctx))


def _rule(behavior, content, tool="Skill"):
    return PermissionRule(tool_name=tool, rule_behavior=behavior, rule_content=content,
                          source=PermissionRuleSource.PROJECT_SETTINGS)


class TestShape:
    def test_upstreams_schema_and_limits(self):
        tool = _tool(_skill())
        assert tool.name == "Skill"
        assert SKILL_TOOL_SCHEMA["required"] == ["skill"]
        assert set(SKILL_TOOL_SCHEMA["properties"]) == {"skill", "args"}
        assert tool.max_result_size_chars == MAX_RESULT_SIZE_CHARS == 100_000
        assert tool.description({"skill": "x"}) == "Execute skill: x"
        assert tool.prompt.startswith("Execute a skill within the main conversation\n")
        assert "<command-name> tag" in tool.prompt
        assert not tool.is_read_only({}) and not tool.is_concurrency_safe({})
        assert tool.input_schema == SKILL_TOOL_SCHEMA


class TestValidate:
    @pytest.mark.parametrize("skill, code, message", [
        ("  ", 1, "Invalid skill format:   "),
        ("nope", 2, "Unknown skill: nope"),
        ("/nope", 2, "Unknown skill: nope"),
        ("hidden", 4, "Skill hidden cannot be used with Skill tool due to disable-model-invocation"),
    ])
    def test_errors(self, skill, code, message):
        tool = _tool(_skill(), _skill("hidden", disable_model_invocation=True))
        result = tool.validate_input({"skill": skill}, _ctx())
        assert (result.valid, result.error_code, result.message) == (False, code, message)

    def test_a_known_skill_with_or_without_a_slash(self):
        tool = _tool(_skill())
        assert tool.validate_input({"skill": "design"}, _ctx()).valid
        assert tool.validate_input({"skill": " /design "}, _ctx()).valid


class TestPermissions:
    def _check(self, tool, perm, skill="design", args="a"):
        return asyncio.run(_maybe(tool.check_permissions({"skill": skill, "args": args}, _ctx(perm))))

    def test_a_skill_with_only_safe_properties_is_allowed(self):
        res = self._check(_tool(_skill()), ToolPermissionContext())
        assert res.behavior == "allow"
        assert res.updated_input == {"skill": "design", "args": "a"}
        assert res.decision_reason is None

    @pytest.mark.parametrize("extra", [{"allowed_tools": ("WriteFile",)}, {"hooks": {"Stop": []}}])
    def test_an_unsafe_skill_asks(self, extra):
        res = self._check(_tool(_skill(**extra)), ToolPermissionContext())
        assert (res.behavior, res.message) == ("ask", "Execute skill: design")
        assert res.updated_input == {"skill": "design", "args": "a"}

    def test_unknown_skill_asks(self):
        assert self._check(_tool(_skill()), ToolPermissionContext(), skill="ghost").behavior == "ask"

    @pytest.mark.parametrize("content", ["design", "/design", "des:*"])
    def test_deny_rules_win(self, content):
        rule = _rule(PermissionBehavior.DENY, content)
        perm = ToolPermissionContext(always_deny_rules=(rule,),
                                     always_allow_rules=(_rule(PermissionBehavior.ALLOW, "design"),))
        res = self._check(_tool(_skill()), perm)
        assert (res.behavior, res.message) == ("deny", "Skill execution blocked by permission rules")
        assert res.decision_reason == {"type": "rule", "rule": rule}

    def test_an_allow_rule_admits_an_unsafe_skill(self):
        rule = _rule(PermissionBehavior.ALLOW, "/design")
        perm = ToolPermissionContext(always_allow_rules=(rule,))
        res = self._check(_tool(_skill(allowed_tools=("WriteFile",))), perm)
        assert (res.behavior, res.decision_reason) == ("allow", {"type": "rule", "rule": rule})
        assert res.updated_input == {"skill": "design", "args": "a"}

    def test_rules_for_other_tools_or_other_skills_do_not_match(self):
        perm = ToolPermissionContext(always_deny_rules=(
            _rule(PermissionBehavior.DENY, "design", tool="Bash"),
            _rule(PermissionBehavior.DENY, "other"),
            _rule(PermissionBehavior.DENY, "designer:*"),
            # The prefix is everything before `:*`, not one character less.
            _rule(PermissionBehavior.DENY, "desx:*"),
            _rule(PermissionBehavior.DENY, None),
        ))
        assert self._check(_tool(_skill()), perm).behavior == "allow"


async def _maybe(value):
    return await value if asyncio.iscoroutine(value) else value


def test_only_allowed_tools_and_hooks_are_unsafe():
    assert skill_has_only_safe_properties(_skill(model="opus", context="fork", paths=("a",), agent="x"))
    assert not skill_has_only_safe_properties(_skill(allowed_tools=("Bash",)))
    assert not skill_has_only_safe_properties(_skill(hooks={"Stop": []}))
    assert skill_has_only_safe_properties(_skill(hooks={}))


class TestInline:
    def test_the_skill_text_joins_as_a_meta_message(self):
        result = _call(_tool(_skill()), {"skill": "/design", "args": "add()"}, _ctx(run_id="r1"))
        assert result.content == "Launching skill: design"
        assert not result.is_error
        (message,) = result.new_messages
        assert isinstance(message, HumanMessage)
        assert message.content == "Design tests for add()."
        assert message.additional_kwargs == {"is_meta": True}
        assert result.context_modifier is None
        assert result.metadata == {"success": True, "commandName": "design", "allowedTools": None, "model": None}

    def test_the_skill_directory_and_session_are_substituted(self):
        skill = _skill(body="See ${TDDAGENTS_SKILL_DIR}/references in ${TDDAGENTS_SESSION_ID}", skill_root="/s")
        (message,) = _call(_tool(skill), {"skill": "design"}, _ctx(run_id="r9")).new_messages
        assert message.content == "Base directory for this skill: /s\n\nSee /s/references in r9"

    def test_the_model_is_reported(self):
        result = _call(_tool(_skill(model="claude-opus-4-6")), {"skill": "design"}, _ctx())
        assert result.metadata["model"] == "claude-opus-4-6"

    def test_allowed_tools_become_command_allow_rules(self):
        skill = _skill(allowed_tools=("Bash(git status:*)", "Bash(git status:*) ReadFile"))
        result = _call(_tool(skill), {"skill": "design"}, _ctx())
        assert result.metadata["allowedTools"] == ["Bash(git status:*)", "Bash(git status:*)", "ReadFile"]
        ctx = _ctx(perm=ToolPermissionContext(always_allow_rules=(_rule(PermissionBehavior.ALLOW, "x"),)))
        assert result.context_modifier is not None
        apply_context_modifier(ctx, result.context_modifier)
        rules = ctx.get_app_state().tool_permission_context.always_allow_rules
        assert rules[1:] == (
            PermissionRule("Bash", PermissionBehavior.ALLOW, "git status:*", COMMAND),
            PermissionRule("ReadFile", PermissionBehavior.ALLOW, None, COMMAND),
        )
        assert ctx.permission_context is None

    def test_an_unknown_skill_at_call_time_raises(self):
        with pytest.raises(ValueError, match="Unknown command: ghost"):
            _call(_tool(_skill()), {"skill": "ghost"}, _ctx())

    def test_the_coordinator_gets_a_summary_not_the_skill(self):
        config = dataclasses.replace(CONFIG, gates=dataclasses.replace(CONFIG.gates, coordinator_mode=True))
        skill = _skill(when_to_use="before RED", allowed_tools=("WriteFile",), model="m")
        result = _call(_tool(skill, config=config), {"skill": "design"}, _ctx())
        (message,) = result.new_messages
        assert result.content == "Launching skill: design"
        assert message.content == coordinator_skill_summary(skill)
        assert message.additional_kwargs == {"is_meta": True}
        assert result.context_modifier is None
        assert result.metadata == {"success": True, "commandName": "design", "model": "m"}
        # A worker in coordinator mode loads the skill itself.
        worker = _call(_tool(skill, config=config), {"skill": "design"}, _ctx(agent_id="w1"))
        assert worker.new_messages[0].content.startswith("Design tests for")


def test_the_coordinator_summary_is_upstreams():
    skill = _skill(when_to_use="w", allowed_tools=("A", "B"))
    assert coordinator_skill_summary(skill) == (
        'Skill "/design" is available for workers.\nDescription: d\nWhen to use: w\n'
        "This skill grants workers additional tool permissions: A, B\n\n"
        'Instruct a worker to use this skill by including "Use the /design skill" in your Agent prompt. '
        "The worker has access to the Skill tool and will receive the skill's content and permissions when it "
        "invokes it."
    )
    bare = dataclasses.replace(skill, description="", when_to_use=None, allowed_tools=())
    assert coordinator_skill_summary(bare).split("\n")[:2] == ['Skill "/design" is available for workers.', ""]


class TestAllowedTools:
    def test_rules_are_deduplicated_and_ordered(self):
        perm = with_allowed_tools(ToolPermissionContext(), ["ReadFile", "Bash(ls)", "ReadFile"])
        assert perm.always_allow_rules == (
            PermissionRule("ReadFile", PermissionBehavior.ALLOW, None, COMMAND),
            PermissionRule("Bash", PermissionBehavior.ALLOW, "ls", COMMAND),
        )

    def test_the_contexts_own_permission_copy_is_extended_too(self):
        ctx = _ctx(permission_context=ToolPermissionContext(mode=PermissionMode.DONT_ASK))
        out = context_with_allowed_tools(ctx, ["Glob"])
        assert out.permission_context.mode == PermissionMode.DONT_ASK
        assert out.permission_context.always_allow_rules == (PermissionRule("Glob", PermissionBehavior.ALLOW, None,
                                                                            COMMAND),)
        assert context_with_allowed_tools(ctx, []) is ctx


class TestTddTier:
    """A skill's `allowed-tools` never reach past the phase tier (barrier 2)."""

    def _gate(self, ctx, path):
        tool = build_write_file_tool()
        return asyncio.run(has_permissions_to_use_tool(tool, {"file_path": path, "content": "x"}, ctx, "t1"))

    def _red_ctx(self, tmp_path):
        from app.workspace.local import LocalWorkspace

        perm = ToolPermissionContext(should_avoid_permission_prompts=True)
        return _ctx(perm=perm, ledger=PhaseLedger(phase=TddPhase.RED), workspace=LocalWorkspace(tmp_path))

    def test_a_skill_granting_writefile_cannot_write_implementation_in_red(self, tmp_path):
        ctx = self._red_ctx(tmp_path)
        assert self._gate(ctx, "tests/test_calc.py").behavior == "deny"  # headless: no rule, no prompt
        result = _call(_tool(_skill(allowed_tools=("WriteFile",))), {"skill": "design"}, ctx)
        assert result.context_modifier is not None
        apply_context_modifier(ctx, result.context_modifier)
        assert self._gate(ctx, "tests/test_calc.py").behavior == "allow"
        denied = self._gate(ctx, "calc.py")
        assert denied.behavior == "deny"
        assert denied.decision_reason is not None and denied.decision_reason["type"] == "phase_rule"


TESTER = AgentDefinition(name="tester", description="Writes tests.", prompt="You write tests.", phase=TddPhase.RED)
EXPLORE = AgentDefinition(name="Explore", description="Searches.", prompt="You search.")


class TestFork:
    def test_agent_selection(self):
        defs = {d.name: d for d in (TESTER, EXPLORE)}
        assert select_fork_agent(_skill(agent="Explore"), defs) is EXPLORE
        assert select_fork_agent(_skill(agent="ghost"), defs) is TESTER
        general = AgentDefinition(name="general-purpose", description="g", prompt="p")
        assert select_fork_agent(_skill(), {**defs, "general-purpose": general}) is general
        assert select_fork_agent(_skill(agent="ghost"), {**defs, "general-purpose": general}) is general
        with pytest.raises(ValueError, match="^No agent available for forked execution$"):
            select_fork_agent(_skill(), {})

    def test_a_forked_skill_runs_as_a_worker(self):
        model = FakeModel([[asks_for("Nothing", "c1")], [AIMessage(content="forked report")]])
        skill = _skill(context="fork", agent="Explore", allowed_tools=("Glob",))
        tool = _tool(skill, model=model, definitions={"Explore": EXPLORE})
        store = AppStateStore(AppState(phase_ledger=PhaseLedger(phase=TddPhase.RED)))
        ctx = tool_context_for(store, run_id="r1")
        result = _call(tool, {"skill": "design", "args": "add()"}, ctx)
        assert result.content == 'Skill "design" completed (forked execution).\n\nResult:\nforked report'
        assert result.new_messages == ()
        meta = result.metadata
        assert meta is not None
        assert (meta["status"], meta["commandName"], meta["result"], meta["success"]) == (
            "forked", "design", "forked report", True)
        assert meta["agentId"].startswith("a")
        seen = model.seen[0]
        assert [m.content for m in seen.messages] == ["Design tests for add()."]
        worker = seen.tool_context
        assert worker.agent_type == "Explore" and worker.agent_id == meta["agentId"]
        assert worker.agent_system_prompt is not None and worker.agent_system_prompt[0] == "You search."
        rules = worker.get_app_state().tool_permission_context.always_allow_rules
        assert PermissionRule("Glob", PermissionBehavior.ALLOW, None, COMMAND) in rules
        # The grant is the worker's alone, and the fork never moves the ledger.
        assert store.get().tool_permission_context.always_allow_rules == ()
        assert store.get().phase_ledger.phase == TddPhase.RED

    def test_a_worker_with_no_text_reports_the_default(self):
        model = FakeModel([[AIMessage(content="")]])
        tool = _tool(_skill(context="fork"), model=model, definitions={"Explore": EXPLORE})
        result = _call(tool, {"skill": "design"}, _ctx())
        assert result.content.endswith("Result:\nSkill execution completed")


class TestForkWorkerContext:
    """What the forked worker is built from: its model, prompt, workspace and sharing."""

    def _fork(self, skill: SkillCommand, agent: AgentDefinition, **ctx_kw: Any):
        model = FakeModel([[AIMessage(content="done")]])
        tool = _tool(skill, model=model, definitions={agent.name: agent})
        ctx = tool_context_for(AppStateStore(AppState(phase_ledger=PhaseLedger())), run_id="r1", **ctx_kw)
        _call(tool, {"skill": skill.name}, ctx)
        return model, ctx

    @staticmethod
    def _env_model(model: FakeModel) -> str:
        prompt = model.seen[0].tool_context.agent_system_prompt
        assert prompt is not None
        return prompt[-1].rsplit("You are powered by the model ", 1)[1].rstrip(".")

    def test_the_skills_model_wins(self):
        model, _ = self._fork(_skill(context="fork", model="o3"), dataclasses.replace(EXPLORE, model="o1"))
        assert self._env_model(model) == model.seen_configs[0].model == "o3" != CONFIG.model

    def test_the_agents_model_without_a_skill_model(self):
        model, _ = self._fork(_skill(context="fork"), dataclasses.replace(EXPLORE, model="o3"))
        assert self._env_model(model) == model.seen_configs[0].model == "o3"

    def test_the_runs_model_when_neither_names_one(self):
        model, _ = self._fork(_skill(context="fork"), EXPLORE)
        assert self._env_model(model) == model.seen_configs[0].model == CONFIG.model

    def test_the_env_block_names_the_workspace(self, tmp_path):
        from app.workspace.local import LocalWorkspace

        model, _ = self._fork(_skill(context="fork"), EXPLORE, workspace=LocalWorkspace(str(tmp_path)))
        prompt = model.seen[0].tool_context.agent_system_prompt
        assert prompt is not None and f"Working directory: {tmp_path}" in prompt[-1]

    def test_the_session_is_substituted_in_the_forked_body(self):
        model, _ = self._fork(_skill(context="fork", body="run ${TDDAGENTS_SESSION_ID}"), EXPLORE)
        assert [m.content for m in model.seen[0].messages] == ["run r1"]

    def test_the_worker_runs_synchronously_on_the_parents_cancel_token(self):
        model, ctx = self._fork(_skill(context="fork"), EXPLORE)
        assert model.seen[0].tool_context.cancel is ctx.cancel

    def test_an_agent_with_memory_reads_it_through_the_forked_context(self, tmp_path):
        from app.loop.agents.memory import AgentMemoryStore

        agent = dataclasses.replace(EXPLORE, memory="run")
        model, _ = self._fork(_skill(context="fork"), agent, agent_memory_store=AgentMemoryStore(base_dir=tmp_path))
        prompt = model.seen[0].tool_context.agent_system_prompt
        assert prompt is not None and prompt[0].startswith("You search.\n\n")


def test_extract_result_text():
    assert extract_result_text([]) == "Execution completed"
    assert extract_result_text([HumanMessage(content="u")], "dflt") == "dflt"
    blocks = AIMessage(content=[{"type": "text", "text": "a"}, {"type": "tool_use", "id": "x"},
                                {"type": "text", "text": "b"}])
    assert extract_result_text([AIMessage(content="old"), blocks, HumanMessage(content="u")]) == "a\nb"
    assert extract_result_text([AIMessage(content="old"), AIMessage(content="")], "dflt") == "dflt"
    # A text block without text contributes an empty line (JS joins `undefined` as "").
    bare = AIMessage(content=[{"type": "text"}, {"type": "text", "text": "b"}])
    assert extract_result_text([bare]) == "\nb"


class TestPreload:
    def test_skills_load_as_meta_messages_in_frontmatter_order(self):
        registry = SkillRegistry([_skill("a", "A $ARGUMENTS."), _skill("plug:b", "B"), _skill("c", "C")])
        messages = skill_preload_messages(["c", "b", "ghost", "a"], registry, agent_type="tester", session_id="s")
        assert [m.content for m in messages] == [
            [{"type": "text", "text": format_skill_loading_metadata("c")}, {"type": "text", "text": "C"}],
            [{"type": "text", "text": format_skill_loading_metadata("b")}, {"type": "text", "text": "B"}],
            [{"type": "text", "text": format_skill_loading_metadata("a")}, {"type": "text", "text": "A ."}],
        ]
        assert all(m.additional_kwargs == {"is_meta": True} for m in messages)

    def test_the_session_is_substituted(self):
        registry = SkillRegistry([_skill("a", "in ${TDDAGENTS_SESSION_ID}")])
        (message,) = skill_preload_messages(["a"], registry, agent_type="tester", session_id="s7")
        assert message.content[1] == {"type": "text", "text": "in s7"}

    def test_a_model_disabled_skill_cannot_be_preloaded(self):
        registry = SkillRegistry([_skill("a", disable_model_invocation=True)])
        assert skill_preload_messages(["a"], registry, agent_type="tester") == []

    def test_resolution_order(self):
        cmds = [_skill("x"), _skill("tester:y"), _skill("p:y"), _skill("q:z"), _skill("n", display_name="nick")]
        assert resolve_skill_name("x", cmds, "tester") == "x"
        assert resolve_skill_name("nick", cmds, "tester") == "nick"
        assert resolve_skill_name("y", cmds, "tester") == "tester:y"
        assert resolve_skill_name("y", cmds, "other") == "tester:y"
        assert resolve_skill_name("z", cmds, "tester") == "q:z"
        assert resolve_skill_name("w", cmds, "tester") is None
        # A plugin agent's own prefix wins over another plugin's same-named skill.
        plugged = [_skill("other:style"), _skill("plug:style")]
        assert resolve_skill_name("style", plugged, "plug:reviewer") == "plug:style"

    def test_the_metadata_is_upstreams(self):
        assert format_skill_loading_metadata("s") == (
            "<command-message>s</command-message>\n<command-name>s</command-name>\n<skill-format>true</skill-format>")


DEVELOPER = AgentDefinition(name="developer", description="Implements.", prompt="You implement.",
                            phase=TddPhase.GREEN)


class TestForkPhaseOnSpawn:
    """A forked skill on a phase agent moves the ledger exactly as the Agent tool's spawn does."""

    def _run(self, agent: AgentDefinition, ledger: PhaseLedger):
        model = FakeModel([[AIMessage(content="done")]])
        tool = _tool(_skill(context="fork", agent=agent.name), model=model, definitions={agent.name: agent})
        store = AppStateStore(AppState(phase_ledger=ledger))
        return model, store, tool, tool_context_for(store)

    def test_a_developer_before_red_is_refused_before_any_worker_runs(self):
        model, store, tool, ctx = self._run(DEVELOPER, PhaseLedger())
        with pytest.raises(ValueError, match="Agent 'developer' cannot start: Cannot transition to GREEN"):
            _call(tool, {"skill": "design"}, ctx)
        assert model.seen == []
        assert store.get().phase_ledger == PhaseLedger()

    def test_a_developer_after_red_moves_the_ledger_to_green(self):
        model, store, tool, ctx = self._run(DEVELOPER, PhaseLedger(red_confirmed=True))
        _call(tool, {"skill": "design"}, ctx)
        assert store.get().phase_ledger == PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True)
        assert model.seen[0].tool_context.get_app_state().phase_ledger.phase == TddPhase.GREEN

    def test_a_tester_restarts_the_cycle(self):
        done = PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True, green_passed=True)
        _, store, tool, ctx = self._run(TESTER, done)
        _call(tool, {"skill": "design"}, ctx)
        assert store.get().phase_ledger == PhaseLedger()

    def test_a_no_phase_agent_leaves_the_ledger_untouched(self):
        ledger = PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True)
        _, store, tool, ctx = self._run(EXPLORE, ledger)
        _call(tool, {"skill": "design"}, ctx)
        assert store.get().phase_ledger is ledger


class TestModelOverride:
    """`contextModifier` swaps `mainLoopModel` (`resolveSkillModelOverride`)."""

    def _modified(self, skill_model, current=None, **kw):
        result = _call(_tool(_skill(model=skill_model, **kw)), {"skill": "design"}, _ctx())
        assert result.context_modifier is not None
        return result.context_modifier(_ctx(main_loop_model=current))

    def test_the_skill_model_becomes_the_main_loop_model(self):
        assert self._modified("claude-opus-4-6").main_loop_model == "claude-opus-4-6"

    def test_a_1m_session_keeps_1m_on_a_model_that_supports_it(self):
        assert self._modified("claude-opus-4-6", "claude-sonnet-4-6[1m]").main_loop_model == "claude-opus-4-6[1m]"
        assert self._modified("claude-sonnet-4-5", "claude-opus-4-6[1m]").main_loop_model == "claude-sonnet-4-5[1m]"
        assert self._modified("o4-mini", "claude-opus-4-6[1m]").main_loop_model == "o4-mini"
        assert self._modified("claude-opus-4-6[1m]", "x").main_loop_model == "claude-opus-4-6[1m]"

    def test_without_the_session_on_the_context_the_runs_model_is_current(self):
        config = dataclasses.replace(CONFIG, model="claude-opus-4-6[1m]")
        tool = _tool(_skill(model="claude-sonnet-4-6"), config=config)
        result = _call(tool, {"skill": "design"}, _ctx())
        assert result.context_modifier(_ctx()).main_loop_model == "claude-sonnet-4-6[1m]"

    def test_1m_disabled_never_appends(self, monkeypatch):
        monkeypatch.setenv("TDDAGENTS_DISABLE_1M_CONTEXT", "1")
        assert self._modified("claude-opus-4-6", "claude-opus-4-6[1m]").main_loop_model == "claude-opus-4-6"

    def test_model_and_allowed_tools_together(self):
        out = self._modified("claude-opus-4-6", allowed_tools=("Glob",))
        assert out.main_loop_model == "claude-opus-4-6"
        assert PermissionRule("Glob", PermissionBehavior.ALLOW, None, COMMAND) in (
            out.get_app_state().tool_permission_context.always_allow_rules)

    def test_no_model_and_no_tools_means_no_modifier(self):
        assert _call(_tool(_skill()), {"skill": "design"}, _ctx()).context_modifier is None


def test_the_listing_reads_the_contexts_registry():
    from app.loop.attachments.skills import get_skill_listing_attachments

    registry = SkillRegistry([_skill()])
    ctx = _ctx(skill_registry=registry)
    ctx.tools = (_tool(_skill()),)
    (att,) = get_skill_listing_attachments([], ctx)
    assert att.data["skillCount"] == 1
