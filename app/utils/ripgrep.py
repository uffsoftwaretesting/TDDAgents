"""
Running ripgrep inside a workspace — the substrate of the loop's `Glob` and `Grep`.

Ported from `reference/claude-code/src/utils/ripgrep.ts`:

* `getRipgrepConfig` -> `ripgrep_command`: upstream picks a *system* `rg` when
  `USE_BUILTIN_RIPGREP` is set falsy, otherwise the *builtin* binary vendored with the
  package. Here the vendored binary is the pinned PyPI `ripgrep` wheel: on the host it is
  the copy installed next to this interpreter; in the sandbox it is the same pinned wheel,
  installed outside the workspace root so `rg --files --hidden` never lists it.
* `testRipgrepOnFirstUse` -> `ensure_ripgrep`: memoized per workspace; in the sandbox it
  provisions the wheel first if `rg --version` does not answer.
* `ripGrep` / `ripGrepRaw` -> `rip_grep`: exit 0 and 1 are both success (1 = no matches);
  a missing or non-executable binary raises; an EAGAIN failure is retried once
  single-threaded (`-j 1`); a timeout with no output raises `RipgrepTimeoutError`; any
  other failure returns whatever output there was.

Divergences: commands go through `Workspace.execute` (a shell) rather than `execFile`, so
argv is `shlex`-quoted and "binary missing" is the shell's exit 127 rather than ENOENT;
`execute` returns no partial output on timeout, so a timeout always raises; there is no
`MAX_BUFFER_SIZE`; and the timeout has no WSL variant (the workspace may be remote).
"""

from __future__ import annotations

import os
import shlex
import sysconfig
import weakref
from pathlib import Path
from typing import Any

from app.workspace.base import WorkspaceTimeout

#: The pinned wheel; keep in step with `requirements.txt`.
RIPGREP_PIP_SPEC = "ripgrep==15.1.0"

#: Where the sandbox copy is installed (`pip --target`), outside any workspace root.
SANDBOX_RIPGREP_DIR = "/tmp/tddagents-ripgrep"

#: `ripGrepRaw`: `defaultTimeout` off WSL, in seconds.
RIPGREP_TIMEOUT_SECONDS = 20

#: `testRipgrepOnFirstUse` runs `--version` with a 5000 ms timeout.
VERSION_PROBE_TIMEOUT_SECONDS = 5

#: Shell exit codes for "command not found" / "not executable" (upstream: ENOENT / EACCES).
CRITICAL_EXIT_CODES = (126, 127)


class RipgrepTimeoutError(Exception):
    """`ripgrep.ts` -> `RipgrepTimeoutError`: distinguishes "timed out" from "no matches"."""


class RipgrepUnavailableError(Exception):
    """rg could not be started or provisioned (upstream rejects with ENOENT/EACCES/EPERM)."""


_status: "weakref.WeakKeyDictionary[Any, bool]" = weakref.WeakKeyDictionary()


def is_env_defined_falsy(value: str | None) -> bool:
    """`envUtils.ts` -> `isEnvDefinedFalsy`."""
    return value is not None and value.strip().lower() in ("0", "false", "no", "off")


def builtin_ripgrep_path() -> str:
    """The wheel's binary next to this interpreter (upstream's vendored `builtin` mode)."""
    return str(Path(sysconfig.get_path("scripts")) / "rg")


def ripgrep_command(ws: Any) -> str:
    """`getRipgrepConfig`: system `rg` on request, otherwise the vendored binary."""
    if is_env_defined_falsy(os.environ.get("USE_BUILTIN_RIPGREP")):
        return "rg"
    if getattr(ws, "kind", "local") == "local":
        return builtin_ripgrep_path()
    return f"{SANDBOX_RIPGREP_DIR}/bin/rg"


def _probe(ws: Any, command: str) -> bool:
    try:
        res = ws.execute(shlex.join([command, "--version"]), timeout=VERSION_PROBE_TIMEOUT_SECONDS)
    except Exception:
        return False
    return bool(res.exit_code == 0 and res.stdout.startswith("ripgrep "))


def provision_command() -> str:
    return shlex.join(
        ["python3", "-m", "pip", "install", "--quiet", "--target", SANDBOX_RIPGREP_DIR, RIPGREP_PIP_SPEC]
    )


def provision_sandbox_ripgrep(ws: Any) -> None:
    """Install the pinned wheel into the sandbox. Called once the sandbox exists."""
    res = ws.execute(provision_command())
    if res.exit_code != 0:
        raise RipgrepUnavailableError(f"Could not install {RIPGREP_PIP_SPEC} in the sandbox: {res.stderr.strip()}")


def ensure_ripgrep(ws: Any) -> None:
    """`testRipgrepOnFirstUse`, memoized per workspace; provisions the sandbox copy if needed."""
    try:
        if _status.get(ws):
            return
    except TypeError:
        pass  # not weak-referenceable: probe every time
    command = ripgrep_command(ws)
    working = _probe(ws, command)
    if not working and command.startswith(SANDBOX_RIPGREP_DIR):
        provision_sandbox_ripgrep(ws)
        working = _probe(ws, command)
    if not working:
        raise RipgrepUnavailableError(f"ripgrep is not available at {command}")
    try:
        _status[ws] = True
    except TypeError:
        pass


def _lines(stdout: str) -> list[str]:
    return [line.rstrip("\r") for line in stdout.strip().split("\n") if line.rstrip("\r")]


def _is_eagain(stderr: str) -> bool:
    return "os error 11" in stderr or "Resource temporarily unavailable" in stderr


def rip_grep(ws: Any, args: list[str], target: str) -> list[str]:
    """`ripgrep.ts` -> `ripGrep`: run rg in the workspace, returning its output lines."""
    ensure_ripgrep(ws)
    command = ripgrep_command(ws)
    single_thread = False
    while True:
        argv = [command, *(["-j", "1"] if single_thread else []), *args, target]
        try:
            res = ws.execute(shlex.join(argv), timeout=RIPGREP_TIMEOUT_SECONDS)
        except WorkspaceTimeout as exc:
            raise RipgrepTimeoutError(
                f"Ripgrep search timed out after {RIPGREP_TIMEOUT_SECONDS} seconds. The search may have "
                "matched files but did not complete in time. Try searching a more specific path or pattern."
            ) from exc
        if res.exit_code == 0:
            return _lines(res.stdout)
        if res.exit_code == 1:
            return []
        if res.exit_code in CRITICAL_EXIT_CODES:
            raise RipgrepUnavailableError(f"ripgrep could not be executed (exit {res.exit_code}): {res.stderr.strip()}")
        if not single_thread and _is_eagain(res.stderr):
            single_thread = True
            continue
        return _lines(res.stdout)


def to_relative_path(path: str) -> str:
    """`toRelativePath`: rg prints `./x` for a `.` target; workspace paths have no prefix."""
    while path.startswith("./"):
        path = path[2:]
    return path


__all__ = [
    "RIPGREP_PIP_SPEC",
    "RipgrepTimeoutError",
    "RipgrepUnavailableError",
    "ensure_ripgrep",
    "provision_sandbox_ripgrep",
    "rip_grep",
    "ripgrep_command",
    "to_relative_path",
]
