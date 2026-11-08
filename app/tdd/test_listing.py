"""
The project's own listing of its collected test files: the declared `listTests` command (TDDAgents, user
decision 2026-11-08).

TDD-only (no upstream counterpart). In GREEN, REFACTOR and the final run the JUnit report must name every
declared test file (`app/tdd/collection_gate.py`), yet several runners' reports name none: Maven surefire,
`go-junit-report`, cargo-nextest and the .NET loggers write a class or a package where a file would be.
TDDAgents embeds no per-language adapter, as claude-code runs the project's own commands, so the project
declares a command that prints the test files its runner collects (`listTests` in
`.tddagents/project.json`, `app/tdd/project_config.py`):

* its stdout lists them, one path per line, relative to the project root; an absolute path inside the
  project is made relative to it, and a line outside the project is ignored;
* `run_suite` runs it only when the JUnit report names no files, after the test command, in the same
  workspace, with the same environment and in the sandbox whenever the test command ran in it;
* a non-zero exit, or a command that could not run, is stated as a fact, and the files stay unknown.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.tdd.project_config import ProjectConfig, project_relative_path


@dataclass(frozen=True, slots=True)
class TestListing:
    """One run of the `listTests` command: its exit code and the files its stdout lists (`None`: unknown)."""

    command: str
    #: `None` when the command could not be executed at all (`error` says why).
    exit_code: int | None
    files: frozenset[str] | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    __test__ = False

    @property
    def fact(self) -> str | None:
        """What kept the listing from naming the files, stated as a fact; `None` when it named them."""
        if self.error is not None:
            return f"the listTests command could not be executed: {self.error}"
        if self.exit_code != 0:
            return f"the listTests command exited {self.exit_code}"
        return None


def parse_test_listing(stdout: str, root: str | os.PathLike[str] | None) -> frozenset[str]:
    """The project-relative paths `stdout` lists, one per line; blank lines and paths outside the project go."""
    files: set[str] = set()
    for line in stdout.splitlines():
        if not line.strip():
            continue
        relative = project_relative_path(line.strip().replace("\\", "/"), root)
        if relative is not None and relative != ".":
            files.add(relative)
    return frozenset(files)


def run_test_listing(ws: Any, config: ProjectConfig, *, timeout: float,
                     env: Mapping[str, str] | None = None, sandbox: bool = False) -> TestListing | None:
    """Run the declared `listTests` command in `ws` (sandboxed with `sandbox`); `None` when none is declared."""
    command = config.list_tests
    if command is None:
        return None
    try:
        res = ws.execute(command, timeout=timeout, env=dict(env or {}), sandbox=sandbox)
    except Exception as exc:  # noqa: BLE001 - a listing that cannot run is stated as such, the files unknown
        return TestListing(command=command, exit_code=None, error=str(exc) or type(exc).__name__)
    files = parse_test_listing(res.stdout, getattr(ws, "root", None)) if res.exit_code == 0 else None
    return TestListing(command=command, exit_code=res.exit_code, files=files, stdout=res.stdout, stderr=res.stderr)


__all__ = ["TestListing", "parse_test_listing", "run_test_listing"]
