"""
Context assembly: the system prompt, system context and user context for one model call.

Ported from claude-code:

* `src/constants/prompts.ts` -> `getSystemPrompt`: static sections in upstream order
  (intro, system, doing tasks, actions, using tools, tone), then
  `SYSTEM_PROMPT_DYNAMIC_BOUNDARY`, then dynamic sections (`env_info_simple` via
  `computeSimpleEnvInfo`). TDDAgents' own `tdd-contract.md` closes the static block, in
  the slot upstream gives `getOutputEfficiencySection`.
* `src/context.ts` -> `getGitStatus`, `getSystemContext`, `getUserContext`.
* `src/utils/api.ts` -> `appendSystemContext`, `prependUserContext`.

Memory files (`claudeMd`) come from `app/utils/claudemd.py`. Run state that changes
during a run — the TDD phase ledger and `TODO.md` — is not part of any of these: it reaches
the model as an attachment (`app/utils/attachments.py`), as upstream's todo state does.

Commands (git, uname) run through the `Workspace` protocol, so the environment described is
the one the agent's tools act on.
"""

from __future__ import annotations

import datetime
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

from app.constants.system_prompt_sections import (
    SYSTEM_PROMPT_DYNAMIC_BOUNDARY,
    DANGEROUS_uncachedSystemPromptSection,
    PromptSectionCache,
    SystemPromptSection,
    systemPromptSection,
)
from app.tdd.coverage_gate import TDD_PROMPT_VARS
from app.utils.claudemd import get_claude_mds, get_memory_files, is_env_truthy
from app.utils.frontmatter_parser import render_prompt
from app.utils.messages import Message

#: `context.ts` -> `MAX_STATUS_CHARS`.
MAX_STATUS_CHARS = 2000

#: `getSystemPrompt` static order; the file for each upstream section, then ours.
STATIC_SECTION_FILES: tuple[tuple[str, str], ...] = (
    ("intro", "identity.md"),
    ("system", "system.md"),
    ("doing_tasks", "doing-tasks.md"),
    ("actions", "actions-with-care.md"),
    ("using_your_tools", "tools.md"),
    ("project_environment", "environment.md"),
    ("tone_and_style", "tone.md"),
    ("tdd_contract", "tdd-contract.md"),
)

USER_CONTEXT_PREAMBLE = "As you answer the user's questions, you can use the following context:"
USER_CONTEXT_FOOTER = (
    "IMPORTANT: this context may or may not be relevant to your tasks. You should not respond "
    "to this context unless it is highly relevant to your task."
)


def get_base_prompts_dir() -> Path:
    return Path(__file__).resolve().parents[0] / "prompts"


def read_system_prompt_file(name: str) -> str:
    path = get_base_prompts_dir() / "system" / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _run(ws: Any, cmd: str) -> tuple[int, str]:
    """Run in the workspace; any infrastructure failure reads as a failed command."""
    try:
        res = ws.execute(cmd)
    except Exception:  # noqa: BLE001 - a workspace failure reads as a failed command
        return 1, ""
    return int(res.exit_code), str(res.stdout).strip()


# ── environment ──────────────────────────────────────────────────────────────

def is_git_repo(ws: Any) -> bool:
    code, out = _run(ws, "git rev-parse --is-inside-work-tree")
    return code == 0 and out == "true"


def prepend_bullets(items: Sequence[str | Sequence[str]]) -> list[str]:
    """`prompts.ts` -> `prependBullets`."""
    out: list[str] = []
    for item in items:
        if isinstance(item, str):
            out.append(f" - {item}")
        else:
            out.extend(f"  - {sub}" for sub in item)
    return out


def _venv_items(ws: Any) -> list[str]:
    """TDDAgents' addition (Phase 13): the project's own virtualenv, where dependencies go."""
    venv = getattr(ws, "virtual_env", None) if ws is not None else None
    return [f"Python virtual environment: {venv} (active for Bash and RunTests)"] if venv else []


def compute_simple_env_info(ws: Any, model_id: str) -> str:
    """`computeSimpleEnvInfo`, without the Claude-Code product lines."""
    cwd = str(getattr(ws, "root", ".")) if ws is not None else "."
    git = is_git_repo(ws) if ws is not None else False
    platform = _run(ws, "uname -s")[1].lower() if ws is not None else ""
    uname_sr = _run(ws, "uname -sr")[1] if ws is not None else ""
    items: list[str | Sequence[str]] = [
        f"Primary working directory: {cwd}",
        [f"Is a git repository: {'true' if git else 'false'}"],
        *_venv_items(ws),
        f"Platform: {platform or 'unknown'}",
        "Shell: bash",
        f"OS Version: {uname_sr or 'unknown'}",
        f"You are powered by the model {model_id}.",
    ]
    return "\n".join(["# Environment", "You have been invoked in the following environment: ", *prepend_bullets(items)])


def compute_env_info(ws: Any, model_id: str, additional_working_directories: Sequence[str] = ()) -> str:
    """
    `constants/prompts.ts` -> `computeEnvInfo`, the `<env>` block subagents get. No marketing
    name or knowledge cutoff: upstream knows them only for Claude models, and prints neither
    for a model it does not know.
    """
    cwd = str(getattr(ws, "root", ".")) if ws is not None else "."
    git = is_git_repo(ws) if ws is not None else False
    platform = _run(ws, "uname -s")[1].lower() if ws is not None else ""
    uname_sr = _run(ws, "uname -sr")[1] if ws is not None else ""
    dirs = list(additional_working_directories)
    additional = f"Additional working directories: {', '.join(dirs)}\n" if dirs else ""
    return (
        "Here is useful information about the environment you are running in:\n"
        "<env>\n"
        f"Working directory: {cwd}\n"
        f"Is directory a git repo: {'Yes' if git else 'No'}\n"
        f"{''.join(f'{line}\n' for line in _venv_items(ws))}"
        f"{additional}Platform: {platform or 'unknown'}\n"
        "Shell: bash\n"
        f"OS Version: {uname_sr or 'unknown'}\n"
        "</env>\n"
        f"You are powered by the model {model_id}."
    )


#: `enhanceSystemPromptWithEnvDetails`' notes, verbatim.
AGENT_NOTES = """Notes:
- Agent threads always have their cwd reset between bash calls, as a result please only use absolute file paths.
- In your final response, share file paths (always absolute, never relative) that are relevant to the task. \
Include code snippets only when the exact text is load-bearing (e.g., a bug you found, a function signature the \
caller asked for) — do not recap code you merely read.
- For clear communication with the user the assistant MUST avoid using emojis.
- Do not use a colon before tool calls. Text like "Let me read the file:" followed by a read tool call should just \
be "Let me read the file." with a period."""


def enhance_system_prompt_with_env_details(
    prompt_sections: Sequence[str],
    model: str,
    additional_working_directories: Sequence[str] = (),
    workspace: Any = None,
) -> tuple[str, ...]:
    """
    `constants/prompts.ts` -> `enhanceSystemPromptWithEnvDetails`: the agent's prompt, the
    notes, then the env block. The skill-discovery guidance is not ported (its tool is
    behind `EXPERIMENTAL_SKILL_SEARCH`, off in the external build).
    """
    return (*prompt_sections, AGENT_NOTES, compute_env_info(workspace, model, additional_working_directories))


def _file_section(filename: str) -> Callable[[], str]:
    """A static section's text, with the TDD values it names filled in (Phase 12)."""
    return lambda: render_prompt(read_system_prompt_file(filename), TDD_PROMPT_VARS, strip_comments=False)


def get_mcp_instructions_section(mcp_clients: Sequence[Any]) -> str | None:
    """
    The `mcp_instructions` section's compute: `None` while the delta attachment announces the
    instructions instead. The gate is read inside the compute, as upstream reads it, so a
    mid-run flip never serves a stale value.
    """
    from app.services.mcp.client import get_mcp_instructions
    from app.utils.attachments import is_mcp_instructions_delta_enabled

    return None if is_mcp_instructions_delta_enabled() else get_mcp_instructions(mcp_clients)


def get_system_prompt(ws: Any, model_id: str, mcp_clients: Sequence[Any] = ()) -> list[SystemPromptSection]:
    """
    `getSystemPrompt`: static (cacheable) sections, the boundary, then dynamic sections —
    `env_info_simple`, then upstream's uncached `mcp_instructions` (servers can connect between
    turns).
    """
    static = [systemPromptSection(name, _file_section(filename)) for name, filename in STATIC_SECTION_FILES]
    dynamic = [
        systemPromptSection("env_info_simple", lambda: compute_simple_env_info(ws, model_id)),
        DANGEROUS_uncachedSystemPromptSection(
            "mcp_instructions",
            lambda: get_mcp_instructions_section(mcp_clients),
            "MCP servers connect/disconnect between turns",
        ),
    ]
    return [*static, SYSTEM_PROMPT_DYNAMIC_BOUNDARY, *dynamic]


# ── system and user context ──────────────────────────────────────────────────

def get_default_branch(ws: Any) -> str:
    """`gitFilesystem.ts` -> `computeDefaultBranch`: origin/HEAD, else origin/main|master, else main."""
    code, out = _run(ws, "git symbolic-ref --quiet --short refs/remotes/origin/HEAD")
    if code == 0 and out.startswith("origin/"):
        return out[len("origin/"):]
    for candidate in ("main", "master"):
        if _run(ws, f"git show-ref --verify --quiet refs/remotes/origin/{candidate}")[0] == 0:
            return candidate
    return "main"


def get_branch(ws: Any) -> str:
    """`gitFilesystem.ts` -> `computeBranch`: the HEAD symref's branch, or 'HEAD' when detached."""
    code, out = _run(ws, "git symbolic-ref --quiet --short HEAD")
    return out if code == 0 and out else "HEAD"


def get_git_status(ws: Any) -> str | None:
    """`context.ts` -> `getGitStatus`."""
    if ws is None or not is_git_repo(ws):
        return None
    branch = get_branch(ws)
    main_branch = get_default_branch(ws)
    status = _run(ws, "git --no-optional-locks status --short")[1]
    log = _run(ws, "git --no-optional-locks log --oneline -n 5")[1]
    user_name = _run(ws, "git config user.name")[1]
    if len(status) > MAX_STATUS_CHARS:
        status = (status[:MAX_STATUS_CHARS] + "\n... (truncated because it exceeds 2k characters. "
                  'If you need more information, run "git status" using BashTool)')
    parts = [
        (
            "This is the git status at the start of the conversation. Note that this status is a snapshot "
            "in time, and will not update during the conversation."
        ),
        f"Current branch: {branch}",
        f"Main branch (you will usually use this for PRs): {main_branch}",
        *([f"Git user: {user_name}"] if user_name else []),
        f"Status:\n{status or '(clean)'}",
        f"Recent commits:\n{log}",
    ]
    return "\n\n".join(parts)


def get_system_context(ws: Any) -> dict[str, str]:
    """`getSystemContext`: git status when the workspace is a repository."""
    git_status = get_git_status(ws)
    return {"gitStatus": git_status} if git_status else {}


def get_user_context(
    ws: Any,
    *,
    today: Callable[[], datetime.date] = datetime.date.today,
    env: Mapping[str, str] | None = None,
    home: str | None = None,
) -> dict[str, str]:
    """`getUserContext`: rendered memory files, then today's date."""
    environ = env if env is not None else os.environ
    claude_md = ""
    if not is_env_truthy(environ.get("TDDAGENTS_DISABLE_MEMORY_FILES")):
        claude_md = get_claude_mds(get_memory_files(ws, env=environ, home=home))
    context: dict[str, str] = {}
    if claude_md:
        context["claudeMd"] = claude_md
    context["currentDate"] = f"Today's date is {today().isoformat()}."
    return context


def append_system_context(system_prompt: Sequence[str], context: Mapping[str, str]) -> list[str]:
    """`api.ts` -> `appendSystemContext`."""
    tail = "\n".join(f"{key}: {value}" for key, value in context.items())
    return [s for s in (*system_prompt, tail) if s]


def prepend_user_context(messages: Sequence[Message], context: Mapping[str, str]) -> list[Message]:
    """`api.ts` -> `prependUserContext`: one meta user message wrapping the context."""
    if not context:
        return list(messages)
    body = "\n".join(f"# {key}\n{value}" for key, value in context.items())
    content = (
        f"<system-reminder>\n{USER_CONTEXT_PREAMBLE}\n{body}\n\n      {USER_CONTEXT_FOOTER}\n"
        "</system-reminder>\n"
    )
    return [HumanMessage(content=content, additional_kwargs={"is_meta": True}), *messages]


# ── per-run memoization ──────────────────────────────────────────────────────

@dataclass
class SessionContext:
    """
    What upstream memoizes for a session: `getUserContext`, `getSystemContext`, and the
    resolved system prompt sections. Computed once per run so the request prefix is stable;
    `reset_session_context` is the clear upstream performs after compaction.
    """

    user_context: dict[str, str]
    system_context: dict[str, str]
    section_cache: PromptSectionCache = field(default_factory=PromptSectionCache)


_SESSIONS: dict[str, SessionContext] = {}


def get_session_context(run_id: str, ws: Any) -> SessionContext:
    session = _SESSIONS.get(run_id)
    if session is None:
        session = SessionContext(user_context=get_user_context(ws), system_context=get_system_context(ws))
        _SESSIONS[run_id] = session
    return session


def reset_session_context(run_id: str | None = None) -> None:
    """Forget one run's memoized context, or every run's when `run_id` is None."""
    if run_id is None:
        _SESSIONS.clear()
    else:
        _SESSIONS.pop(run_id, None)


__all__ = [
    "AGENT_NOTES",
    "SessionContext",
    "append_system_context",
    "compute_env_info",
    "compute_simple_env_info",
    "enhance_system_prompt_with_env_details",
    "get_git_status",
    "get_session_context",
    "get_system_context",
    "get_system_prompt",
    "get_user_context",
    "prepend_user_context",
    "reset_session_context",
]
