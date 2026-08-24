"""
RunTests — the Red/Green measurement, exposed as a tool.

Two things make this tool unlike the other seventeen, and both are deliberate:

* **It is pinned to the sandbox**, bypassing the workspace router entirely. What it
  measures *is* the research result, so it must run in the same environment on every run
  regardless of what an agent's `workspace:` field says.
* **It is exempt from the capability gate.** By §2.3's ladder `full` is the only mode that
  admits execution, but the roster gives the refactorer `workspace_write` *and* RunTests.
  The exemption resolves that without weakening anything: this tool runs no agent-authored
  command, only pytest with a fixed flag set, so granting it is not granting execution.
  The refactorer stays `workspace_write`, and nothing about holding RunTests lets it run
  anything else.

`_PYTEST_FLAGS` and the exit-code handling are reused verbatim from
`app/agents/langgraph/runner.py`, which the graph still calls; the two converge in Phase 2
when the nodes are replaced.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.agents.langgraph.runner import _PYTEST_FLAGS
from app.config.config import Config
from app.tools.base import (
    AnyTool,
    Capability,
    ToolContext,
    ToolResult,
    build_tool,
    err,
    ok,
)
from app.tools.exec import sandbox_adapter
from app.workspace.base import WorkspaceError


class RunTestsArgs(BaseModel):
    test_path: str = Field(
        default=".", description="File or directory to run. '.' runs the whole suite."
    )


RUN_TESTS_PROMPT = """Runs pytest in the sandbox and returns the full output.

Tests failing is a result, not an error — in TDD a failing test is frequently the point.
The output includes the exit code, and is verbose on purpose (`-vv --tb=long
--showlocals`) because the failure detail is the most valuable thing you will read.

Output can be large. If it exceeds the size limit it is saved to a file and you get the
head and tail plus the path, which you can then ReadFile selectively.

Always runs in the sandbox, whatever workspace you otherwise operate on."""


def _run_tests(args: RunTestsArgs, ctx: ToolContext) -> ToolResult:
    adapter = sandbox_adapter(ctx)
    if adapter is None:
        return err("RunTests requires the sandbox workspace.")

    try:
        # Cheap when pytest is already present, which it is after the first call.
        adapter.execute("python -c 'import pytest' 2>/dev/null || pip install pytest -q")

        command = f'PYTHONPATH=. python -m pytest "{args.test_path}" {_PYTEST_FLAGS}'
        # A test run is the largest and slowest command in the pipeline, so it gets its
        # own budget rather than the general command timeout.
        result = adapter.execute(command, timeout=Config.TEST_TIMEOUT)
    except WorkspaceError as exc:
        return err(f"Could not run the tests: {exc}")

    passed = result.exit_code == 0
    header = "All tests passed." if passed else f"Tests failed (exit {result.exit_code})."
    body = f"{header}\n\n--- STDOUT ---\n{result.stdout}"
    if result.stderr.strip():
        body += f"\n--- STDERR ---\n{result.stderr}"

    # is_error stays False even on failure: a red test is data the agent must reason
    # about, not a malfunction of the tool.
    return ok(body, exit_code=result.exit_code)


RunTests = build_tool(
    name="RunTests",
    args_schema=RunTestsArgs,
    prompt=RUN_TESTS_PROMPT,
    call=_run_tests,
    description=lambda args: f"Run tests in {args.test_path}",
    # Exempt from the capability ladder — see the module docstring.
    required_capability=lambda args: Capability.READ,
    required_workspace=lambda args: "sandbox",
)

RUN_TESTS_TOOLS: list[AnyTool] = [RunTests]
