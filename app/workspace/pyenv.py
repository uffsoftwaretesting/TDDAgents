"""
The per-session Python environment generated code is tested in.

Runs are local (docs/refactoring_transition_plan.md, Phase 0), so the interpreter that runs
the agents' tests is no longer the sandbox image's. Each session gets its own virtualenv at
`<run_dir>/venv`, beside the workspace tree and never inside it, with pytest installed:

* isolated from TDDAgents' own dependencies, so an agent's `pip install` cannot change the
  orchestrator, and generated code cannot accidentally import it;
* reproducible: pytest is pinned to the version TDDAgents itself is tested with;
* reused across the session's plan items, like the old sandbox.

The venv is created with `--without-pip` and filled by the orchestrator's own pip through
`pip --python`, because a system Python may lack `ensurepip` (Debian/Ubuntu split it out).
`SessionPython.env()` is what a workspace merges into every command, so `python`, `pytest`
and `pip` resolve to the session venv for `RunTests` and `Bash` alike.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

#: Directory, under a session's run dir, that holds its virtualenv.
SESSION_VENV_DIRNAME = "venv"

#: What the session venv must contain. Keep in step with requirements.txt.
REQUIRED_PACKAGES: tuple[str, ...] = ("pytest==9.0.1",)

Runner = Callable[..., Any]


class SessionPythonError(RuntimeError):
    """The session virtualenv could not be created or provisioned."""


@dataclass(frozen=True)
class SessionPython:
    venv_dir: Path

    @property
    def bin_dir(self) -> Path:
        return self.venv_dir / "bin"

    @property
    def python(self) -> Path:
        return self.bin_dir / "python"

    def env(self, base_path: str | None = None) -> dict[str, str]:
        """Environment overrides that make the venv's tools win on `PATH`."""
        path = base_path if base_path is not None else os.environ.get("PATH", "")
        return {
            "PATH": f"{self.bin_dir}{os.pathsep}{path}" if path else str(self.bin_dir),
            "VIRTUAL_ENV": str(self.venv_dir),
        }


def _run(runner: Runner, argv: Sequence[str], what: str) -> None:
    result = runner(list(argv), capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise SessionPythonError(f"could not {what}: {detail or f'exit {result.returncode}'}")


def has_packages(python: Path, runner: Runner) -> bool:
    probe = runner([str(python), "-c", "import pytest"], capture_output=True, text=True)
    return bool(probe.returncode == 0)


def ensure_session_python(
    run_dir: Path | str,
    *,
    creator: str = sys.executable,
    runner: Runner = subprocess.run,
) -> SessionPython:
    """Create (once) and provision the session venv under `run_dir`; idempotent."""
    session = SessionPython(Path(run_dir) / SESSION_VENV_DIRNAME)
    if not session.python.exists():
        _run(runner, [creator, "-m", "venv", "--without-pip", str(session.venv_dir)], "create the session venv")
    if not has_packages(session.python, runner):
        _run(
            runner,
            [creator, "-m", "pip", "--python", str(session.python), "install", "--quiet", *REQUIRED_PACKAGES],
            "install the test toolchain into the session venv",
        )
        if not has_packages(session.python, runner):
            raise SessionPythonError("pytest is still not importable in the session venv after installing it")
    return session


__all__ = [
    "REQUIRED_PACKAGES",
    "SESSION_VENV_DIRNAME",
    "SessionPython",
    "SessionPythonError",
    "ensure_session_python",
]
