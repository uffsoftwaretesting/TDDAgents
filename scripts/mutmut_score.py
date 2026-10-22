#!/usr/bin/env python3
"""
The quality gate's mutation score, computed by status.

Run from the repo root after `mutmut run`:

    .venv/bin/python scripts/mutmut_score.py

It reads `mutmut results --all true` (every mutant with its status) and reports the gate figure
`killed / (total - timeout)`, plus the not-killed mutants grouped by function — the triage list.
Grouping must filter on the status: `mutmut results` without `--all` prints every *non-killed*
mutant, timeouts included, and counting those lines as survivors once turned 107 timeouts into
"runner survivors" (Phase 7). A run with `not checked` mutants was interrupted or failed stats
collection, and has no score.
"""

from __future__ import annotations

import subprocess
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

MUTMUT = Path(__file__).resolve().parents[1] / ".venv" / "bin" / "mutmut"


@dataclass(frozen=True)
class Summary:
    counts: dict[str, int]
    survivors_by_function: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def score(self) -> float:
        unchecked = self.counts.get("not checked", 0)
        if unchecked:
            raise ValueError(f"{unchecked} mutants were not checked; the run is incomplete")
        measured = self.total - self.counts.get("timeout", 0)
        if measured <= 0:
            raise ValueError("no mutants were measured")
        return self.counts.get("killed", 0) / measured


def summarize(lines: Iterable[str]) -> Summary:
    """Tally `name: status` lines; every status but killed and timeout is a survivor to triage."""
    counts: Counter[str] = Counter()
    survivors: Counter[str] = Counter()
    for line in lines:
        name, sep, status = line.strip().rpartition(": ")
        if not sep or "__mutmut_" not in name:
            continue
        counts[status] += 1
        if status not in ("killed", "timeout"):
            survivors[name.rsplit("__mutmut_", 1)[0]] += 1
    return Summary(counts=dict(counts), survivors_by_function=dict(survivors))


def render(summary: Summary) -> str:
    timeout = summary.counts.get("timeout", 0)
    others = ", ".join(f"{status}: {n}" for status, n in summary.counts.items() if status != "timeout")
    killed = summary.counts.get("killed", 0)
    lines = [
        f"mutants: {summary.total} (timeout {timeout})",
        others,
        f"score: {summary.score:.1%} ({killed} / {summary.total - timeout})",
        "not killed, by function:",
    ]
    for name, n in sorted(summary.survivors_by_function.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"  {n}  {name}")
    return "\n".join(lines)


def main() -> int:
    out = subprocess.run([str(MUTMUT), "results", "--all", "true"], capture_output=True, text=True, check=True)
    print(render(summarize(out.stdout.splitlines())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
