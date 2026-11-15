"""
How a RunTests call is shown.

TDDAgents' own: RunTests has no upstream counterpart. It follows upstream's tools that take no
input to show, such as `TaskStopTool/UI.tsx` -> `renderToolUseMessage` (`''`): the row is the
tool's name alone (`buildTool`'s default, `RunTests`), without parentheses.

Its result line (user decision 2026-11-14) is what the run observed, from the structured output RunTests
returns (`run_tests_output`): the exit code, the JUnit counts, the coverage and the phase the run was
judged in, `exit 1 · 12 tests, 2 failed · coverage 84.0% · RED`. Facts only, as RunTests' own text.
"""

from __future__ import annotations

from typing import Any


def render_tool_use_message(input: dict[str, Any], verbose: bool) -> str | None:
    """The name alone: RunTests always runs the whole declared suite, so there is no input to show."""
    return ""


def _tests_part(output: dict[str, Any]) -> str:
    tests = output.get("tests")
    if tests is None:
        return "no JUnit report"
    counts = [f"{tests} {'test' if tests == 1 else 'tests'}"]
    for key, word in (("failures", "failed"), ("errors", "errored"), ("skipped", "skipped")):
        if output.get(key):
            counts.append(f"{output[key]} {word}")
    if len(counts) == 1 and tests > 0 and output.get("exitCode") == 0:
        counts[0] += " passed"
    return ", ".join(counts)


def render_tool_result_message(output: Any, verbose: bool) -> str | None:
    """One line: `exit <code> · <tests> · coverage <n>% · <phase>`, the parts there are."""
    if not isinstance(output, dict) or "exitCode" not in output:
        return None
    parts = [f"exit {output['exitCode']}", _tests_part(output)]
    if output.get("coverage") is not None:
        parts.append(f"coverage {float(output['coverage']):.1f}%")
    if output.get("phase"):
        parts.append(str(output["phase"]))
    return " · ".join(parts)


__all__ = ["render_tool_result_message", "render_tool_use_message"]
