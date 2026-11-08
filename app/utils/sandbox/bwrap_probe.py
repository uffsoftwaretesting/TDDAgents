"""
The one-off bubblewrap probe (TDDAgents; recorded divergence, user decision 2026-11-08).

Upstream's `checkDependencies` only asks whether `bwrap` is on `PATH`. A bwrap that is installed but
cannot create a sandbox (user namespaces disabled, an AppArmor profile, a container without the
capability) exits 1 for every command, and RunTests would read that exit as a failed test run, RED's
evidence. So the adapter runs `bwrap --ro-bind / / --unshare-net true` once: a failure is a dependency
error, named with bwrap's first line of stderr, and a required sandbox refuses to start.

**Mutation-exempt seam** (`[tool.mutmut] do_not_mutate`): the subprocess launch. Surface:
`subprocess.run(argv, capture_output=True, text=True, timeout=)`. Verified end to end by
`tests/test_sandbox_session.py` with a fake `bwrap` that fails, and against the real one when installed.
"""

from __future__ import annotations

import subprocess

#: The probe: a read-only root and an empty network namespace, running `true`.
PROBE_ARGV = ("bwrap", "--ro-bind", "/", "/", "--unshare-net", "true")
PROBE_TIMEOUT_SECONDS = 10


def probe_bwrap() -> str | None:
    """`None` when bwrap can create a sandbox, else the dependency error to report."""
    try:
        completed = subprocess.run(list(PROBE_ARGV), capture_output=True, text=True,
                                   timeout=PROBE_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"bubblewrap (bwrap) cannot create a sandbox: {error}"
    if completed.returncode == 0:
        return None
    first_line = next((line for line in completed.stderr.splitlines() if line.strip()), "")
    detail = first_line.strip() or f"exit {completed.returncode}"
    return f"bubblewrap (bwrap) cannot create a sandbox: {detail}"


__all__ = ["PROBE_ARGV", "PROBE_TIMEOUT_SECONDS", "probe_bwrap"]
