"""
Bundled skills built in code (`skills/bundledSkills.ts` -> `registerBundledSkill` with
`getPromptForCommand`, `getBundledSkills`, `clearBundledSkills`; `skills/bundled/index.ts` ->
`initBundledSkills`).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from app.skills.bundled.index import init_bundled_skills
from app.skills.bundled_skills import (
    BundledSkillContext,
    BundledSkillDefinition,
    clear_bundled_skills,
    get_bundled_skills,
    register_bundled_skill,
)
from app.skills.load_skills_dir import create_bundled_skill_command, get_skill_registry
from app.utils.permissions.filesystem import get_bundled_skills_root
from app.utils.process_user_input.process_slash_command import (
    get_messages_for_prompt_slash_command,
)


def _echo(args: str, context: BundledSkillContext) -> str:
    return f"args={args} cwd={context.cwd} session={context.session_id}"


def _definition(name: str = "coded", **kw) -> BundledSkillDefinition:
    kw.setdefault("description", "Built in code")
    return BundledSkillDefinition(name=name, get_prompt_for_command=_echo, **kw)


def _skill(root: Path, name: str, body: str) -> None:
    (root / name).mkdir(parents=True)
    (root / name / "SKILL.md").write_text(body)


def _registry(tmp_path: Path, bundled: Path | None = None):
    project = tmp_path / "home" / "proj"
    project.mkdir(parents=True, exist_ok=True)
    return get_skill_registry(project, tmp_path / "home", tmp_path / "etc",
                              bundled_dir=bundled if bundled is not None else tmp_path / "bundled")


class TestRegistry:
    def test_register_get_and_clear(self):
        assert get_bundled_skills() == []
        first, second = _definition("a"), _definition("b")
        register_bundled_skill(first)
        register_bundled_skill(second)
        listed = get_bundled_skills()
        assert listed == [first, second]
        listed.clear()
        assert get_bundled_skills() == [first, second]
        clear_bundled_skills()
        assert get_bundled_skills() == []

    def test_init_registers_each_code_built_skill_once(self):
        init_bundled_skills()
        init_bundled_skills()
        assert [d.name for d in get_bundled_skills()] == ["update-config", "tdd-project-config"]

    def test_init_keeps_what_another_caller_registered(self):
        register_bundled_skill(_definition("other"))
        init_bundled_skills()
        assert [d.name for d in get_bundled_skills()] == ["other", "update-config", "tdd-project-config"]


class TestCommand:
    def test_the_command_registerBundledSkill_builds(self):
        cmd = create_bundled_skill_command(
            _definition(when_to_use="w", argument_hint="[x]", allowed_tools=("ReadFile",), model="haiku",
                        context="fork", agent="tester", hooks={"Stop": []}), "/proj")
        assert (cmd.name, cmd.description, cmd.source, cmd.loaded_from) == ("coded", "Built in code", "bundled",
                                                                            "bundled")
        assert cmd.has_user_specified_description is True
        assert (cmd.when_to_use, cmd.argument_hint, cmd.allowed_tools, cmd.model) == ("w", "[x]", ("ReadFile",),
                                                                                      "haiku")
        assert (cmd.context, cmd.agent, cmd.hooks) == ("fork", "tester", {"Stop": []})
        assert (cmd.content_length, cmd.progress_message, cmd.skill_root, cmd.paths) == (0, "running", None, None)
        assert (cmd.user_invocable, cmd.is_hidden, cmd.disable_model_invocation) == (True, False, False)

    def test_flags_carry_over(self):
        cmd = create_bundled_skill_command(_definition(user_invocable=False, disable_model_invocation=True), "/p")
        assert (cmd.user_invocable, cmd.is_hidden, cmd.disable_model_invocation) == (False, True, True)

    def test_the_prompt_is_computed_with_the_project_dir_and_session(self):
        cmd = create_bundled_skill_command(_definition(), "/proj")
        assert cmd.prompt_for_command("$ARGUMENTS go", session_id="s-1") == "args=$ARGUMENTS go cwd=/proj session=s-1"
        assert cmd.prompt_for_command("") == "args= cwd=/proj session="

    def test_files_are_extracted_once_and_name_the_base_directory(self):
        calls: list[str] = []

        def prompt(args: str, context: BundledSkillContext) -> str:
            calls.append(args)
            return "Read references/a.md"

        definition = BundledSkillDefinition(name="with-files", description="d", get_prompt_for_command=prompt,
                                            files={"references/a.md": "A", "b.md": "B"})
        cmd = create_bundled_skill_command(definition, "/proj")
        extracted = Path(get_bundled_skills_root()) / "with-files"
        assert cmd.skill_root == str(extracted) and not extracted.exists()
        expected = f"Base directory for this skill: {extracted}\n\nRead references/a.md"
        assert cmd.prompt_for_command("x") == expected
        assert (extracted / "references" / "a.md").read_text() == "A" and (extracted / "b.md").read_text() == "B"
        os.remove(extracted / "b.md")
        assert cmd.prompt_for_command("y") == expected
        assert not (extracted / "b.md").exists() and calls == ["x", "y"]

    def test_a_failed_extraction_leaves_the_prompt_without_a_base_directory(self):
        definition = BundledSkillDefinition(name="bad-files", description="d",
                                            get_prompt_for_command=lambda a, c: "body",
                                            files={"../escape.md": "x"})
        assert create_bundled_skill_command(definition, "/p").prompt_for_command("") == "body"


class TestSkillRegistry:
    def test_code_built_skills_come_first_and_shadow_a_file_skill(self, tmp_path):
        _skill(tmp_path / "bundled", "coded", "---\ndescription: from a file\n---\nX")
        _skill(tmp_path / "bundled", "filed", "---\ndescription: file\n---\nY")
        register_bundled_skill(_definition())
        reg = _registry(tmp_path)
        assert [c.name for c in reg.commands()] == ["coded", "coded", "filed"]
        found = reg.find_command("coded")
        assert found is not None and found.description == "Built in code"
        assert [c.name for c in reg.get_skill_tool_commands()][:1] == ["coded"]

    def test_the_prompt_reads_the_registrys_project_dir(self, tmp_path):
        register_bundled_skill(_definition())
        cmd = _registry(tmp_path).find_command("coded")
        assert cmd is not None
        assert cmd.prompt_for_command("a") == f"args=a cwd={tmp_path / 'home' / 'proj'} session="

    def test_never_conditional(self, tmp_path):
        register_bundled_skill(_definition())
        reg = _registry(tmp_path)
        assert reg.activate_conditional_skills_for_paths(["src/a.py"], str(tmp_path)) == []
        assert reg.find_command("coded") is not None

    def test_without_registration_the_registry_has_only_files(self, tmp_path):
        _skill(tmp_path / "bundled", "filed", "---\ndescription: file\n---\nY")
        assert [c.name for c in _registry(tmp_path).commands()] == ["filed"]


def test_a_typed_slash_command_runs_the_code_built_prompt(tmp_path):
    register_bundled_skill(_definition())
    cmd = _registry(tmp_path).find_command("coded")
    turn = get_messages_for_prompt_slash_command(cmd, "go", session_id="s-9")
    assert [m.content for m in turn.messages] == [
        ("<command-message>coded</command-message>\n<command-name>/coded</command-name>\n"
         "<command-args>go</command-args>"),
        f"args=go cwd={tmp_path / 'home' / 'proj'} session=s-9",
    ]
    assert turn.messages[1].additional_kwargs == {"is_meta": True}


def test_a_session_registers_the_code_built_skills(tmp_path):
    from app.cli.print import open_session_run
    from app.workspace.local import LocalWorkspace
    from tests.test_file_history_wiring import FINISHED, _never_asked, options

    ws = LocalWorkspace(str(tmp_path / "thread" / "project"))
    Path(ws.root, "go.mod").write_text("module x\n")

    async def session() -> str:
        async with open_session_run(run_id="skills-run", plan=FINISHED, workspace=ws, can_use_tool=_never_asked,
                                    **{**options(tmp_path, []), "project_root": Path(ws.root)}) as run:
            cmd = run.ctx.skill_registry.find_command("tdd-project-config")
            return str(cmd.prompt_for_command(""))

    text = asyncio.run(session())
    assert '<doc path="references/go.md">' in text and '<doc path="references/python.md">' not in text
