"""
`${VAR}` / `${VAR:-default}` expansion in MCP server configs (Phase 8).

Ported from `reference/claude-code/src/services/mcp/envExpansion.ts` ->
`expandEnvVarsInString`. A variable that is unset and has no default stays as its literal
`${VAR}` text and is reported as missing (the config still loads); a variable set to the
empty string counts as set.
"""

from __future__ import annotations

import os
import re

_VAR = re.compile(r"\$\{([^}]+)\}")


def expand_env_vars_in_string(value: str) -> tuple[str, list[str]]:
    """`expandEnvVarsInString`: the expanded text and every missing name, in order."""
    missing: list[str] = []

    def replace(match: re.Match[str]) -> str:
        # JS `split(':-', 2)` keeps the first two pieces only.
        pieces = match.group(1).split(":-")
        name, default = pieces[0], (pieces[1] if len(pieces) > 1 else None)
        env_value = os.environ.get(name)
        if env_value is not None:
            return env_value
        if default is not None:
            return default
        missing.append(name)
        return match.group(0)

    return _VAR.sub(replace, value), missing


__all__ = ["expand_env_vars_in_string"]
