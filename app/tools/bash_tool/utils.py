"""
Keeping the shell inside the project.

Ported from `reference/claude-code/src/tools/BashTool/utils.ts` -> `resetCwdIfOutsideProject`,
`stdErrAppendShellResetMessage`, and `utils/envUtils.ts` -> `shouldMaintainProjectWorkingDir`
(`CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR`, here `TDDAGENTS_BASH_MAINTAIN_PROJECT_WORKING_DIR`).
"""

from __future__ import annotations

import os
from typing import Any

from app.bootstrap.state import set_cwd
from app.tools.bash_tool.bash_security import is_env_truthy
from app.utils.cwd import get_cwd, get_original_cwd_for
from app.utils.permissions.filesystem import path_in_allowed_working_path
from app.utils.permissions.permissions import extract_permission_context

#: `CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR`, under this project's name.
MAINTAIN_PROJECT_WORKING_DIR_VAR = "TDDAGENTS_BASH_MAINTAIN_PROJECT_WORKING_DIR"


def should_maintain_project_working_dir() -> bool:
    """`shouldMaintainProjectWorkingDir`: every command starts at the project root."""
    return is_env_truthy(os.environ.get(MAINTAIN_PROJECT_WORKING_DIR_VAR))


def reset_cwd_if_outside_project(context: Any) -> bool:
    """
    `resetCwdIfOutsideProject`: back to the original directory when the shell left the allowed
    working directories (or when it must always start there). `True` when the model should be
    told: a reset caused by leaving the project, not by the always-maintain setting.
    """
    cwd, original = get_cwd(context), get_original_cwd_for(context)
    maintain = should_maintain_project_working_dir()
    outside = cwd != original and not path_in_allowed_working_path(
        cwd, extract_permission_context(context), original)
    if maintain or outside:
        set_cwd(getattr(context, "workspace", None), original)
        return not maintain
    return False


def std_err_append_shell_reset_message(stderr: str, context: Any) -> str:
    """`stdErrAppendShellResetMessage`."""
    return f"{stderr.strip()}\nShell cwd was reset to {get_original_cwd_for(context)}"
