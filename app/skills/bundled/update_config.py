"""
The `update-config` bundled skill: configuring TDDAgents through its settings files.

Ported from `reference/claude-code/src/skills/bundled/updateConfig.ts` -> `SETTINGS_EXAMPLES_DOCS`,
`HOOKS_DOCS`, `HOOK_VERIFICATION_FLOW`, `UPDATE_CONFIG_PROMPT` and `registerUpdateConfigSkill` (its
description, `allowedTools`, `userInvocable` and `getPromptForCommand`).

`getPromptForCommand` has upstream's two paths:

- **`[hooks-only]`** (what `/init`'s `NEW_INIT_PROMPT` loads before writing its first hook): the
  hook reference and the verification flow, then `## Task` with the rest of the argument, if any.
- **Anything else:** the whole prompt, then `## User Request` with the argument, if any.

The texts are `app/prompts/skills/update-config/` (`prompt.md` is `UPDATE_CONFIG_PROMPT`, its
`${…}` interpolations written `{{…}}`), upstream's verbatim after the substitutions
`tests/test_update_config_skill.py` pins:

- **Product:** "Claude Code" and "Claude" → "TDDAgents", `.claude/` → `.tddagents/` (the settings
  sources of `app/utils/settings/settings.py`), the hook-check sentinel's file name.
- **Tool names:** `Write` → `WriteFile`, `Read` → `ReadFile` (matchers, rules, prose), and
  `allowedTools: ['Read']` → `("ReadFile",)`. Path rules stay `Edit(…)`.
- **`tool_response`:** a PostToolUse hook receives the tool's result text here, so the hook input
  sample shows a string and the examples read `.tool_input.file_path` alone (upstream's
  `.tool_response.filePath // …` fails in `jq` on a string).
- **Hooks are read when a session starts** (`app/utils/hooks/hooks_config_snapshot.py`; no
  settings watcher, no `/hooks` menu): the verification flow's in-turn proof (step 6) and its
  `/hooks` handoff become one handoff that names the restart.
- **Settings this build does not read, dropped:** `env`, `model`, `agent` (the block keeps
  `alwaysThinkingEnabled`, under "Thinking"), `attribution` (TDDAgents never commits), the "Other
  Settings" list, and the description's env-var trigger and example.
- **No Config tool, no `--debug`, no generated settings schema**: the "Config Tool vs Direct Edit"
  section, the description's Config-tool sentence, the `--debug` troubleshooting step and the
  `## Full Settings JSON Schema` section are dropped (settings are read leniently here, except the
  `hooks` block and the plugin keys; there is no whole-file schema to print).
- **Plugin sources:** a marketplace's name (listed by `{{PLUGIN_CLI}} marketplace list`) or
  `builtin`; upstream's official marketplaces hold `.claude-plugin/` packages, which this build does
  not load.

Upstream's `allowedTools` makes the Skill tool ask before the skill runs
(`skillHasOnlySafeProperties`), here as there.
"""

from __future__ import annotations

from pathlib import Path

from app.services.plugins.plugin_operations import PLUGIN_CLI
from app.skills.bundled_skills import BundledSkillContext, BundledSkillDefinition
from app.utils.frontmatter_parser import render_prompt

SKILL_NAME = "update-config"
SKILL_DIR = Path(__file__).resolve().parents[2] / "prompts" / "skills" / SKILL_NAME

#: The argument prefix that asks for the hook reference only (`NEW_INIT_PROMPT`'s hooks step).
HOOKS_ONLY_PREFIX = "[hooks-only]"


def _read(name: str) -> str:
    return (SKILL_DIR / name).read_text(encoding="utf-8")


SETTINGS_EXAMPLES_DOCS = render_prompt(_read("settings_examples_docs.md"), {"PLUGIN_CLI": PLUGIN_CLI},
                                       strip_comments=False)
HOOKS_DOCS = _read("hooks_docs.md")
HOOK_VERIFICATION_FLOW = _read("hook_verification_flow.md")
UPDATE_CONFIG_PROMPT = render_prompt(
    _read("prompt.md"),
    {
        "SETTINGS_EXAMPLES_DOCS": SETTINGS_EXAMPLES_DOCS,
        "HOOKS_DOCS": HOOKS_DOCS,
        "HOOK_VERIFICATION_FLOW": HOOK_VERIFICATION_FLOW,
    },
    strip_comments=False,
)

DESCRIPTION = (
    "Use this skill to configure the TDDAgents harness via settings.json. Automated behaviors (\"from now on "
    "when X\", \"each time X\", \"whenever X\", \"before/after X\") require hooks configured in settings.json - "
    "the harness executes these, not TDDAgents, so memory/preferences cannot fulfill them. Also use for: "
    "permissions (\"allow X\", \"add permission\", \"move permission to\"), hook troubleshooting, or any changes "
    "to settings.json/settings.local.json files. Examples: \"allow npm commands\", \"add bq permission to global "
    "settings\", \"move permission to user settings\", \"when TDDAgents stops show X\"."
)


def get_prompt_for_command(args: str, context: BundledSkillContext) -> str:
    """`getPromptForCommand`: the hook reference for `[hooks-only]`, otherwise the whole prompt."""
    if args.startswith(HOOKS_ONLY_PREFIX):
        request = args[len(HOOKS_ONLY_PREFIX):].strip()
        prompt = HOOKS_DOCS + "\n\n" + HOOK_VERIFICATION_FLOW
        if request:
            prompt += f"\n\n## Task\n\n{request}"
        return prompt

    prompt = UPDATE_CONFIG_PROMPT
    if args:
        prompt += f"\n\n## User Request\n\n{args}"
    return prompt


def update_config_skill() -> BundledSkillDefinition:
    """`registerUpdateConfigSkill`'s definition."""
    return BundledSkillDefinition(
        name=SKILL_NAME,
        description=DESCRIPTION,
        allowed_tools=("ReadFile",),
        user_invocable=True,
        get_prompt_for_command=get_prompt_for_command,
    )


__all__ = [
    "DESCRIPTION",
    "HOOKS_DOCS",
    "HOOKS_ONLY_PREFIX",
    "HOOK_VERIFICATION_FLOW",
    "SETTINGS_EXAMPLES_DOCS",
    "SKILL_DIR",
    "SKILL_NAME",
    "UPDATE_CONFIG_PROMPT",
    "get_prompt_for_command",
    "update_config_skill",
]
