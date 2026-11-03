"""
`/init`: a built-in `prompt` command that asks the model to write the project's `TDDAGENTS.md`.

Ported from `reference/claude-code/src/commands/init.ts` -> `OLD_INIT_PROMPT`, `NEW_INIT_PROMPT`
and `command` (its `description` getter, `progressMessage`, `source: 'builtin'` and
`getPromptForCommand`, which first calls `maybeMarkProjectOnboardingComplete`).

The texts are `app/prompts/commands/init/old_init.md` and `new_init.md`, upstream's verbatim with
the product substitutions ("Claude Code" and "Claude" → "TDDAgents", `CLAUDE.md` →
`TDDAGENTS.md`, `CLAUDE.local.md` → `TDDAGENTS.local.md`, `.claude/` → `.tddagents/`, and the
prefix line without the claude.ai URL), pinned by `tests/test_init_command.py`. `NEW_INIT_PROMPT`
also follows "the TDDAgents equivalent where a route exists, otherwise dropped" for what this
build lacks: the hooks step loads the `update-config` skill (`app/skills/bundled/update_config.py`)
with `[hooks-only]` as upstream's does, its flow without the in-turn live proof (hooks are read when a
session starts here), the `Write|Edit` matchers name this build's `WriteFile`, the `/verify` bundled-skill example
and the `claude-plugins-official` plugin suggestions are dropped (their packages are
`.claude-plugin/` ones, which this build's plugin loader does not read), and `/plugin` is the
`plugin` CLI (`{{PLUGIN_CLI}}`).

The gate is `TDDAGENTS_NEW_INIT` (upstream `CLAUDE_CODE_NEW_INIT`, behind the `NEW_INIT` build
flag; `USER_TYPE === 'ant'` is not ported). Upstream reads the session's cwd from module state;
here the command carries the project folder whose onboarding it marks.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.project_onboarding_state import maybe_mark_project_onboarding_complete
from app.services.plugins.plugin_operations import PLUGIN_CLI
from app.skills.load_skills_dir import SkillCommand
from app.tools.bash_tool.bash_security import is_env_truthy
from app.utils.frontmatter_parser import render_prompt

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts" / "commands" / "init"

#: Upstream `CLAUDE_CODE_NEW_INIT`.
NEW_INIT_ENV = "TDDAGENTS_NEW_INIT"

#: The `description` getter's two answers.
NEW_INIT_DESCRIPTION = "Initialize new TDDAGENTS.md file(s) and optional skills/hooks with codebase documentation"
OLD_INIT_DESCRIPTION = "Initialize a new TDDAGENTS.md file with codebase documentation"

#: `progressMessage`.
INIT_PROGRESS_MESSAGE = "analyzing your codebase"


def _read(name: str) -> str:
    # Upstream's template literals end without a newline; the files end with one.
    return (PROMPTS_DIR / name).read_text(encoding="utf-8").removesuffix("\n")


OLD_INIT_PROMPT = _read("old_init.md")
NEW_INIT_PROMPT = render_prompt(_read("new_init.md"), {"PLUGIN_CLI": PLUGIN_CLI}, strip_comments=False)


def is_new_init_enabled() -> bool:
    """`feature('NEW_INIT') && isEnvTruthy(process.env.CLAUDE_CODE_NEW_INIT)`, under this build's name."""
    return is_env_truthy(os.environ.get(NEW_INIT_ENV))


def get_init_description() -> str:
    """The `description` getter."""
    return NEW_INIT_DESCRIPTION if is_new_init_enabled() else OLD_INIT_DESCRIPTION


@dataclass(frozen=True)
class InitCommand(SkillCommand):
    """`/init`'s `PromptCommand`; `cwd` is the project folder (upstream's `getCwd()`)."""

    cwd: str = ""

    def prompt_for_command(self, args: str, session_id: str = "") -> str:
        """`getPromptForCommand`: the onboarding marked complete when it is, then the gated text."""
        maybe_mark_project_onboarding_complete(self.cwd)
        return NEW_INIT_PROMPT if is_new_init_enabled() else OLD_INIT_PROMPT


def init_command(cwd: str | Path) -> InitCommand:
    """The command for the project at `cwd` (`contentLength: 0`: the content is built when invoked)."""
    return InitCommand(
        name="init",
        description=get_init_description(),
        markdown_content="",
        source="builtin",
        loaded_from=None,
        skill_root=None,
        progress_message=INIT_PROGRESS_MESSAGE,
        cwd=str(cwd),
    )


__all__ = [
    "INIT_PROGRESS_MESSAGE",
    "InitCommand",
    "NEW_INIT_DESCRIPTION",
    "NEW_INIT_ENV",
    "NEW_INIT_PROMPT",
    "OLD_INIT_DESCRIPTION",
    "OLD_INIT_PROMPT",
    "PROMPTS_DIR",
    "get_init_description",
    "init_command",
    "is_new_init_enabled",
]
