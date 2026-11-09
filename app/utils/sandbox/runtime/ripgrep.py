"""
Running ripgrep for the sandbox's mandatory-deny scan.

Ported from `@anthropic-ai/sandbox-runtime` 0.0.46 `src/utils/ripgrep.ts` -> `ripGrep`: the configured
command with its arguments, then ripgrep's, then the target; exit 0 is the matching lines, exit 1 is no
match, anything else raises; a 10 s timeout. `argv0` overrides `argv[0]` (a multicall binary).

**Mutation-exempt seam** (`[tool.mutmut] do_not_mutate`): the subprocess launch. Its surface is
`subprocess.run(executable=..., timeout=...)`; it is verified end to end by the mandatory-deny tests,
which run the vendored ripgrep over a real directory.
"""

from __future__ import annotations

import subprocess

from app.utils.sandbox.runtime.sandbox_config import RipgrepConfig

#: `ripGrep`'s `timeout: 10_000`.
RIPGREP_TIMEOUT_SECONDS = 10


class RipgrepError(RuntimeError):
    """`ripgrep failed with exit code <n>: <stderr>`."""


def rip_grep(args: list[str], target: str, config: RipgrepConfig) -> list[str]:
    """`ripGrep`."""
    argv = [config.command, *(config.args or []), *args, target]
    executable = None
    if config.argv0:
        executable, argv[0] = argv[0], config.argv0
    try:
        completed = subprocess.run(argv, executable=executable, capture_output=True, text=True,
                                   timeout=RIPGREP_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RipgrepError(f"ripgrep failed: {error}") from error
    if completed.returncode == 0:
        return [line for line in completed.stdout.strip().split("\n") if line]
    if completed.returncode == 1:
        return []
    raise RipgrepError(f"ripgrep failed with exit code {completed.returncode}: {completed.stderr}")


__all__ = ["RIPGREP_TIMEOUT_SECONDS", "RipgrepError", "rip_grep"]
