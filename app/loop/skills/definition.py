"""
A skill command (Phase 8).

Ported from `reference/claude-code/src/skills/loadSkillsDir.ts` -> `createSkillCommand` (the
`PromptCommand` a `SKILL.md` becomes) and `skills/bundledSkills.ts` -> `registerBundledSkill`.
TDDAgents' bundled skills live as files under `app/prompts/skills/` rather than in code, so a
bundled command carries its directory as `skill_root` — upstream's `prependBaseDir` for a
bundled skill with `files` — and the model reads `references/` from there on demand.

`getPromptForCommand` is `prompt_for_command`: the base-directory line, argument substitution,
then the skill-dir and session placeholders, here `${TDDAGENTS_SKILL_DIR}` and
`${TDDAGENTS_SESSION_ID}` (upstream's `CLAUDE_*`, renamed as every env name in this build).
Not ported: the inline shell execution of `` !`cmd` `` blocks (`executeShellCommandsInPrompt`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.loop.skills.arguments import substitute_arguments

SKILL_DIR_PLACEHOLDER = "${TDDAGENTS_SKILL_DIR}"
SESSION_ID_PLACEHOLDER = "${TDDAGENTS_SESSION_ID}"


@dataclass(frozen=True)
class SkillCommand:
    """`PromptCommand` for a skill (bundled or from a skills directory)."""

    name: str
    description: str
    markdown_content: str
    #: `bundled`, `policySettings`, `userSettings` or `projectSettings`.
    source: str
    #: `bundled` or `skills`.
    loaded_from: str
    skill_root: str | None
    display_name: str | None = None
    has_user_specified_description: bool = False
    allowed_tools: tuple[str, ...] = ()
    argument_hint: str | None = None
    argument_names: tuple[str, ...] = ()
    when_to_use: str | None = None
    version: str | None = None
    model: str | None = None
    disable_model_invocation: bool = False
    user_invocable: bool = True
    #: `fork` runs the skill as a worker; `None` is inline.
    context: str | None = None
    agent: str | None = None
    paths: tuple[str, ...] | None = None
    hooks: dict[str, Any] | None = None
    progress_message: str = "running"

    @property
    def is_hidden(self) -> bool:
        return not self.user_invocable

    @property
    def content_length(self) -> int:
        return len(self.markdown_content)

    def user_facing_name(self) -> str:
        return self.display_name or self.name

    def prompt_for_command(self, args: str, session_id: str = "") -> str:
        """`getPromptForCommand`: the skill's text as the model receives it."""
        content = (
            f"Base directory for this skill: {self.skill_root}\n\n{self.markdown_content}"
            if self.skill_root
            else self.markdown_content
        )
        content = substitute_arguments(content, args, True, self.argument_names)
        if self.skill_root:
            content = content.replace(SKILL_DIR_PLACEHOLDER, self.skill_root)
        return content.replace(SESSION_ID_PLACEHOLDER, session_id)


__all__ = ["SESSION_ID_PLACEHOLDER", "SKILL_DIR_PLACEHOLDER", "SkillCommand"]
