"""
The session environment script every shell command starts with (Phase 13).

Ported from `reference/claude-code/src/utils/sessionEnvironment.ts`
(`getSessionEnvironmentScript`) and the command assembly of `utils/shell/bashProvider.ts`
(`buildExecCommand`): upstream reads the script `CLAUDE_ENV_FILE` points to ("This allows
venv/conda activation to persist across shell commands") and injects it into each command,
after the user's shell snapshot and before the command itself:
`source <snapshot> && <session env script> && eval '<command>'`.

TDDAgents' variable is `TDDAGENTS_ENV_FILE` (the product rename). It is read from the
environment the command runs with, so each project's workspace points to its own script:
the session's `session-env.sh`, which activates the project's venv
(`app/workspace/pyenv.py::SessionPython.write_env_file`).

*Not ported:* the hook env files (`SessionStart` / `Setup` / `CwdChanged` / `FileChanged` hooks
writing to `CLAUDE_ENV_FILE`), since the hook layer has no env-file protocol (Phase 4), and the
memoization (the file is read per command, so a change applies at once).
"""

from __future__ import annotations

import logging
import shlex
from collections.abc import Mapping
from pathlib import Path

logger = logging.getLogger("TDDOrchestrator.SessionEnvironment")

#: Upstream's `CLAUDE_ENV_FILE`, under this project's name.
ENV_FILE_VAR = "TDDAGENTS_ENV_FILE"


def get_session_environment_script(environment: Mapping[str, str]) -> str | None:
    """`getSessionEnvironmentScript`: the trimmed script `TDDAGENTS_ENV_FILE` names, or `None`."""
    env_file = environment.get(ENV_FILE_VAR)
    if not env_file:
        return None
    try:
        script = Path(env_file).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.debug("Failed to read %s: %s", ENV_FILE_VAR, exc)
        return None
    return script or None


def build_command(command: str, environment: Mapping[str, str]) -> str:
    """
    `bashProvider.ts`'s command parts: the session environment script, then
    `eval '<command>'`, joined with `&&`. The login shell (`bash -l`) stands in for upstream's
    shell snapshot, which is sourced first.
    """
    parts: list[str] = []
    script = get_session_environment_script(environment)
    if script:
        parts.append(script)
    parts.append(f"eval {shlex.quote(command)}")
    return " && ".join(parts)


__all__ = ["ENV_FILE_VAR", "build_command", "get_session_environment_script"]
