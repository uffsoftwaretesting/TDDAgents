"""
The `update-config` bundled skill (`reference/claude-code/src/skills/bundled/updateConfig.ts`): its
registration, both argument paths of `getPromptForCommand`, and its texts, upstream's verbatim
after the recorded substitutions (checked against the reference snapshot when it is present).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.constants.product import CLI_COMMAND
from app.skills.bundled.index import init_bundled_skills
from app.skills.bundled.update_config import (
    DESCRIPTION,
    HOOK_VERIFICATION_FLOW,
    HOOKS_DOCS,
    HOOKS_ONLY_PREFIX,
    SETTINGS_EXAMPLES_DOCS,
    SKILL_DIR,
    SKILL_NAME,
    UPDATE_CONFIG_PROMPT,
    generate_settings_schema,
    get_prompt_for_command,
    update_config_skill,
)
from app.skills.bundled_skills import BundledSkillContext, get_bundled_skills
from app.skills.load_skills_dir import get_skill_registry
from app.services.plugins.plugin_operations import PLUGIN_CLI
from app.tools.skill_tool.skill_tool import skill_has_only_safe_properties
from app.utils.process_user_input.process_slash_command import get_messages_for_prompt_slash_command
from app.utils.settings.schema_output import generate_settings_json_schema
from app.utils.settings.types import settings_json_schema

REPO = Path(__file__).resolve().parent.parent
UPDATE_CONFIG_TS = REPO / "reference" / "claude-code" / "src" / "skills" / "bundled" / "updateConfig.ts"
needs_reference = pytest.mark.skipif(not UPDATE_CONFIG_TS.is_file(),
                                     reason="the claude-code reference snapshot is not checked out")

_CONTEXT = BundledSkillContext(cwd="/nonexistent")


# ── upstream's literals ──────────────────────────────────────────────────────

def _template(source: str, name: str) -> str:
    """The value of `const <name> = `…``: escapes resolved, `${X}` interpolations kept as written."""
    start = source.index(f"const {name} = `") + len(f"const {name} = `")
    out: list[str] = []
    i = start
    while source[i] != "`":
        if source[i] == "\\":
            out.append(source[i + 1])
            i += 2
            continue
        out.append(source[i])
        i += 1
    return "".join(out)


def _description(source: str) -> str:
    start = source.index("description:\n      '") + len("description:\n      '")
    return source[start:source.index("',\n", start)]


Substitution = tuple[str | re.Pattern[str], str]

#: This build's tool names (`WriteFile`, `ReadFile`) where upstream names its `Write` and `Read`.
TOOL_NAMES: list[Substitution] = [
    ("Write|Edit", "WriteFile|Edit"),
    ('"tool_name": "Write"', '"tool_name": "WriteFile"'),
    ("`Bash`, `Write`, `Edit`, `Read`,", "`Bash`, `WriteFile`, `Edit`, `ReadFile`,"),
    ('"Edit(.claude)", "Read"]', '"Edit(.claude)", "ReadFile"]'),
    ('"Write(/etc/*)"', '"WriteFile(/etc/*)"'),
    ('`"Read"` - allows all Read operations', '`"ReadFile"` - allows all ReadFile operations'),
    ("the Write tool doesn't", "the WriteFile tool doesn't"),
    ("Use Read tool", "Use ReadFile tool"),
    ('(e.g., "Bash", "Write", "Edit")', '(e.g., "Bash", "WriteFile", "Edit")'),
]
#: A PostToolUse hook's `tool_response` is the tool's result text in this build, not an object.
TOOL_RESPONSE: list[Substitution] = [
    ('"tool_response": { "success": true }', '"tool_response": "..."'),
    (".tool_response.filePath // .tool_input.file_path", ".tool_input.file_path"),
    (".tool_input.file_path // .tool_response.filePath", ".tool_input.file_path"),
]
#: No `/hooks` menu in this build (the settings watcher reloads hooks; a restart is the fallback).
NO_HOOKS_MENU: list[Substitution] = [
    ("Tell the user to open `/hooks` once (reloads config) or restart — you can't do this yourself; `/hooks` is a "
     "user UI menu and opening it ends this turn.",
     "Tell the user to restart (resume the session with `--thread-id`) — you can't do this yourself."),
    ("(or needs `/hooks`/restart per the watcher caveat)", "(or needs a restart per the watcher caveat)"),
    ("Point them at `/hooks` to review, edit, or disable it later. ", ""),
]
#: Settings this build does not read, sections dropped, the plugin sources it has.
SETTINGS_ONLY: list[Substitution] = [
    ('### Environment Variables\n```json\n{\n  "env": {\n    "DEBUG": "true",\n    "MY_API_KEY": "value"\n  }\n}\n'
     "```\n\n", ""),
    ('### Model & Agent\n```json\n{\n  "model": "sonnet",  // or "opus", "haiku", full model ID\n'
     '  "agent": "agent-name",\n', '### Thinking\n```json\n{\n'),
    (re.compile(r"### Attribution \(Commits & PRs\)\n.*?hide that attribution\.\n\n", re.DOTALL), ""),
    ("where source is `claude-code-marketplace`, `claude-plugins-official`, or `builtin`.",
     "where source is a marketplace's name (`{{PLUGIN_CLI}} marketplace list`) or `builtin`."),
    (re.compile(r"\n### Other Settings\n.*\Z", re.DOTALL), ""),
]
PROMPT_ONLY: list[Substitution] = [
    (re.compile(r"## Decision: Config Tool vs Direct Edit\n.*?\n\n(?=## Workflow)", re.DOTALL), ""),
    (re.compile(r"### Environment Variables\n\nUser: \"Set DEBUG=true\".*?```\n\n", re.DOTALL), ""),
    # This build's command line (`app/constants/product.py`).
    ("Run `claude --debug`", "Run `{{CLI_COMMAND}} --debug`"),
    ("${SETTINGS_EXAMPLES_DOCS}", "{{SETTINGS_EXAMPLES_DOCS}}"),
    ("${HOOKS_DOCS}", "{{HOOKS_DOCS}}"),
    ("${HOOK_VERIFICATION_FLOW}", "{{HOOK_VERIFICATION_FLOW}}"),
]
DESCRIPTION_ONLY: list[Substitution] = [
    ('env vars ("set X=Y"), ', ""),
    ('"set DEBUG=true", ', ""),
    ('"when claude stops show X"', '"when TDDAgents stops show X"'),
    (" For simple settings like theme/model, use Config tool.", ""),
]
#: Every text: the product's name and its config directory.
PRODUCT: list[Substitution] = [
    ("Claude Code", "TDDAgents"),
    (".claude/", ".tddagents/"),
    ("(.claude)", "(.tddagents)"),
    ("/tmp/claude-hook-check.txt", "/tmp/tddagents-hook-check.txt"),
]

_ONLY: dict[str, list[Substitution]] = {
    "SETTINGS_EXAMPLES_DOCS": [*SETTINGS_ONLY],
    "HOOKS_DOCS": [],
    "HOOK_VERIFICATION_FLOW": [*NO_HOOKS_MENU],
    "UPDATE_CONFIG_PROMPT": [*PROMPT_ONLY],
    "description": [*DESCRIPTION_ONLY],
}


def _apply(text: str, substitutions: list[Substitution], *, required: bool) -> str:
    for old, new in substitutions:
        if isinstance(old, re.Pattern):
            match = old.search(text)
            assert match, old.pattern  # upstream changed: the substitution must be reviewed
            text = text[:match.start()] + new + text[match.end():]
            continue
        if required:
            assert old in text, old
        text = text.replace(old, new)
    return text


def _expected(name: str) -> str:
    source = UPDATE_CONFIG_TS.read_text(encoding="utf-8")
    text = _description(source) if name == "description" else _template(source, name)
    text = _apply(text, _ONLY[name], required=True)
    text = _apply(text, [*TOOL_NAMES, *TOOL_RESPONSE, *PRODUCT], required=False)
    return re.sub(r"\bClaude\b", "TDDAgents", text)


def _file(name: str) -> str:
    return (SKILL_DIR / name).read_text(encoding="utf-8")


@needs_reference
@pytest.mark.parametrize("name, file", [
    ("SETTINGS_EXAMPLES_DOCS", "settings_examples_docs.md"),
    ("HOOKS_DOCS", "hooks_docs.md"),
    ("HOOK_VERIFICATION_FLOW", "hook_verification_flow.md"),
    ("UPDATE_CONFIG_PROMPT", "prompt.md"),
])
def test_each_text_is_upstreams(name, file):
    assert _file(file) == _expected(name)


@needs_reference
def test_the_description_is_upstreams():
    assert DESCRIPTION == _expected("description")


@needs_reference
def test_every_substitution_still_applies_to_upstream():
    source = UPDATE_CONFIG_TS.read_text(encoding="utf-8")
    texts = "".join(_template(source, name) for name in ("SETTINGS_EXAMPLES_DOCS", "HOOKS_DOCS",
                                                         "HOOK_VERIFICATION_FLOW", "UPDATE_CONFIG_PROMPT"))
    for old, _new in [*TOOL_NAMES, *TOOL_RESPONSE, *PRODUCT]:
        assert isinstance(old, str) and old in texts, old


def test_the_composed_texts():
    assert "{{" not in SETTINGS_EXAMPLES_DOCS and f"`{PLUGIN_CLI} marketplace list`" in SETTINGS_EXAMPLES_DOCS
    assert UPDATE_CONFIG_PROMPT == (
        _file("prompt.md").replace("{{CLI_COMMAND}}", CLI_COMMAND)
        .replace("{{SETTINGS_EXAMPLES_DOCS}}", SETTINGS_EXAMPLES_DOCS)
        .replace("{{HOOKS_DOCS}}", HOOKS_DOCS).replace("{{HOOK_VERIFICATION_FLOW}}", HOOK_VERIFICATION_FLOW))
    assert HOOKS_DOCS == _file("hooks_docs.md") and HOOK_VERIFICATION_FLOW == _file("hook_verification_flow.md")
    for text in (DESCRIPTION, SETTINGS_EXAMPLES_DOCS, HOOKS_DOCS, HOOK_VERIFICATION_FLOW, UPDATE_CONFIG_PROMPT):
        assert "Claude" not in text and "claude" not in text and "{{" not in text


def test_the_texts_name_this_builds_settings_files_and_tools():
    for path in ("`~/.tddagents/settings.json`", "`.tddagents/settings.json`", "`.tddagents/settings.local.json`"):
        assert path in SETTINGS_EXAMPLES_DOCS
    assert '"matcher": "WriteFile|Edit"' in HOOKS_DOCS and '"matcher": "Write|' not in UPDATE_CONFIG_PROMPT
    assert ".tool_response.filePath" not in UPDATE_CONFIG_PROMPT
    assert "/hooks" not in UPDATE_CONFIG_PROMPT and "/tmp/tddagents-hook-check.txt" in HOOK_VERIFICATION_FLOW
    assert UPDATE_CONFIG_PROMPT.endswith(
        "6. **Use --debug** - Run `python -m app.main --debug` to see hook execution logs\n")
    assert "6. **Prove the hook fires**" in HOOK_VERIFICATION_FLOW and "7. **Handoff.**" in HOOK_VERIFICATION_FLOW
    assert "Config tool" not in UPDATE_CONFIG_PROMPT and '"env"' not in UPDATE_CONFIG_PROMPT


# ── getPromptForCommand ──────────────────────────────────────────────────────

def _full() -> str:
    """The whole prompt and the schema, as `getPromptForCommand` builds them without a request."""
    return f"{UPDATE_CONFIG_PROMPT}\n\n## Full Settings JSON Schema\n\n```json\n{generate_settings_json_schema()}\n```"


class TestPrompt:
    def test_hooks_only_returns_the_hook_docs_and_the_flow_with_the_task(self):
        text = get_prompt_for_command(f"{HOOKS_ONLY_PREFIX}  Constructing a PostToolUse hook  ", _CONTEXT)
        assert text == f"{HOOKS_DOCS}\n\n{HOOK_VERIFICATION_FLOW}\n\n## Task\n\nConstructing a PostToolUse hook"

    def test_hooks_only_without_a_request_has_no_task(self):
        expected = f"{HOOKS_DOCS}\n\n{HOOK_VERIFICATION_FLOW}"
        assert get_prompt_for_command(f"{HOOKS_ONLY_PREFIX}   ", _CONTEXT) == expected
        assert get_prompt_for_command(HOOKS_ONLY_PREFIX, _CONTEXT) == expected

    def test_the_prefix_must_lead(self):
        text = get_prompt_for_command(f" {HOOKS_ONLY_PREFIX} x", _CONTEXT)
        assert text == f"{_full()}\n\n## User Request\n\n {HOOKS_ONLY_PREFIX} x"

    def test_the_full_prompt_with_the_user_request(self):
        assert get_prompt_for_command("allow npm commands", _CONTEXT) == (
            f"{_full()}\n\n## User Request\n\nallow npm commands")

    def test_the_full_prompt_without_arguments(self):
        assert get_prompt_for_command("", _CONTEXT) == _full()

    def test_the_full_prompt_ends_with_the_generated_settings_schema(self):
        schema = generate_settings_schema()
        assert schema == generate_settings_json_schema()
        assert _full().endswith(f"\n\n## Full Settings JSON Schema\n\n```json\n{schema}\n```")
        assert json.loads(schema) == settings_json_schema()

    def test_the_full_prompt_carries_every_section(self):
        for section in (SETTINGS_EXAMPLES_DOCS, HOOKS_DOCS, HOOK_VERIFICATION_FLOW):
            assert section in UPDATE_CONFIG_PROMPT
        assert UPDATE_CONFIG_PROMPT.startswith("# Update Config Skill\n\nModify TDDAgents configuration")


# ── registration ─────────────────────────────────────────────────────────────

class TestRegistration:
    def test_the_definition(self):
        definition = update_config_skill()
        assert (definition.name, definition.description, definition.allowed_tools) == (
            SKILL_NAME, DESCRIPTION, ("ReadFile",))
        assert SKILL_NAME == "update-config"
        assert definition.user_invocable and not definition.disable_model_invocation
        assert (definition.argument_hint, definition.when_to_use, definition.files) == (None, None, None)
        assert definition.get_prompt_for_command is get_prompt_for_command

    def test_registered_first_by_init_bundled_skills(self):
        init_bundled_skills()
        assert [d.name for d in get_bundled_skills()] == ["update-config", "tdd-project-config"]

    def test_a_skill_the_model_and_the_user_reach(self, tmp_path):
        init_bundled_skills()
        project = tmp_path / "proj"
        project.mkdir()
        registry = get_skill_registry(project, tmp_path, tmp_path / "etc", bundled_dir=tmp_path / "bundled")
        command = registry.find_command(SKILL_NAME)
        assert command is not None and command.source == "bundled"
        assert SKILL_NAME in [c.name for c in registry.get_skill_tool_commands()]
        # Upstream's `allowedTools: ['Read']` makes the Skill tool ask, as it does upstream.
        assert not skill_has_only_safe_properties(command)
        turn = get_messages_for_prompt_slash_command(command, f"{HOOKS_ONLY_PREFIX} format on edit")
        assert turn.messages[1].content == f"{HOOKS_DOCS}\n\n{HOOK_VERIFICATION_FLOW}\n\n## Task\n\nformat on edit"


def test_new_init_loads_this_skill_with_the_hooks_only_prefix():
    from app.commands.init import NEW_INIT_PROMPT

    assert f"`skill: '{SKILL_NAME}'` and args starting with `{HOOKS_ONLY_PREFIX}`" in NEW_INIT_PROMPT
    assert '**"Constructing a Hook"** flow' in NEW_INIT_PROMPT
    assert HOOK_VERIFICATION_FLOW.startswith("## Constructing a Hook (with verification)\n")
