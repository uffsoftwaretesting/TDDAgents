"""
The per-session Python environment generated code is tested in.

Runs are local (docs/refactoring_transition_plan.md, Phase 0), so the interpreter that runs
the agents' tests is no longer the sandbox image's. Each session's project gets its own
virtualenv at `<project>/.venv` (Phase 13, gitignored), with pip, pytest and pytest-cov installed:

* isolated from TDDAgents' own dependencies, so an agent's `pip install` cannot change the
  orchestrator, and generated code cannot accidentally import it;
* reproducible: pytest is pinned to the version TDDAgents itself is tested with;
* reused across the session's plan items, like the old sandbox;
* the project's own: the agents install what the project needs into it (`pip` resolves to it),
  so the suite they run is the suite anyone re-running the project gets.

The venv is created with `--without-pip` and filled by the orchestrator's own pip through
`pip --python`, because a system Python may lack `ensurepip` (Debian/Ubuntu split it out).
pip itself is installed into it the same way. It reaches every command as upstream's venv
activation does: a session environment script (`SessionPython.write_env_file`, upstream's
`CLAUDE_ENV_FILE`) runs before each command, so `python`, `pytest` and `pip` resolve to the
session venv for `RunTests` and `Bash` alike.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.utils.session_environment import ENV_FILE_VAR

#: Directory, inside a session's project, that holds its virtualenv.
SESSION_VENV_DIRNAME = ".venv"

#: What the session venv must contain. Keep in step with requirements.txt. pytest-cov measures
#: the coverage REFACTOR is judged by (Phase 12, `app/tdd/coverage_gate.py`).
REQUIRED_PACKAGES: tuple[str, ...] = ("pip==26.0.1", "pytest==9.0.1", "pytest-cov==7.0.0", "coverage==7.11.3")

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

    def write_env_file(self, path: Path | str) -> dict[str, str]:
        """
        Write the session environment script that activates this venv (upstream's
        `CLAUDE_ENV_FILE` use: "venv/conda activation to persist across shell commands") and
        return the environment that points every command at it (`app/utils/session_environment.py`).
        """
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        # What `bin/activate` does for a non-interactive shell, without its creation-time path (a
        # venv built elsewhere and moved still activates).
        target.write_text(
            f"export VIRTUAL_ENV={shlex.quote(str(self.venv_dir))}\n"
            'export PATH="$VIRTUAL_ENV/bin:$PATH"\n',
            encoding="utf-8",
        )
        return {ENV_FILE_VAR: str(target)}


def _run(runner: Runner, argv: Sequence[str], what: str) -> None:
    result = runner(list(argv), capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise SessionPythonError(f"could not {what}: {detail or f'exit {result.returncode}'}")


def has_packages(python: Path, runner: Runner) -> bool:
    probe = runner([str(python), "-c", "import pip, pytest, pytest_cov"], capture_output=True, text=True)
    return bool(probe.returncode == 0)


def ensure_session_python(
    project_dir: Path | str,
    *,
    creator: str = sys.executable,
    runner: Runner = subprocess.run,
) -> SessionPython:
    """Create (once) and provision the session venv in `project_dir`; idempotent."""
    session = SessionPython(Path(project_dir) / SESSION_VENV_DIRNAME)
    if not session.python.exists():
        _run(runner, [creator, "-m", "venv", "--without-pip", str(session.venv_dir)], "create the session venv")
    if not has_packages(session.python, runner):
        _run(
            runner,
            [creator, "-m", "pip", "--python", str(session.python), "install", "--quiet", *REQUIRED_PACKAGES],
            "install the test toolchain into the session venv",
        )
        if not has_packages(session.python, runner):
            raise SessionPythonError(
                "pip, pytest and pytest-cov are still not importable in the session venv after installing them")
    return session


__all__ = [
    "REQUIRED_PACKAGES",
    "SESSION_VENV_DIRNAME",
    "SessionPython",
    "SessionPythonError",
    "ensure_session_python",
]
