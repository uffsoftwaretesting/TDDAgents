#!/usr/bin/env python3
"""
The quality gate's mutation score, computed by status.

Run from the repo root after `mutmut run`:

    .venv/bin/python scripts/mutmut_score.py --timeouts       # the timed-out mutants, one per line
    .venv/bin/mutmut run --max-children 1 <those names>        # re-run each alone, on an idle host
    .venv/bin/python scripts/mutmut_score.py --timeouts-reverified

It reads `mutmut results --all true` (every mutant with its status) and reports the gate figure
`(killed + timeout) / total`, plus the not-killed mutants grouped by function — the triage list.

**No mutant leaves the denominator** (Phase 11, user decision). mutmut times a mutant out by
wall clock, so a loaded host turns slow-but-passing runs into timeouts. A timeout is therefore
re-run alone first: one that now passes is a survivor, one that fails is killed, and one that
still hangs is a confirmed hang, which counts as detected (the suite did not pass), as Stryker
counts it. Until `--timeouts-reverified` says that was done, a run with timeouts has no score.
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

    timeouts: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def score(self, *, timeouts_reverified: bool) -> float:
        unchecked = self.counts.get("not checked", 0)
        if unchecked:
            raise ValueError(f"{unchecked} mutants were not checked; the run is incomplete")
        if self.total <= 0:
            raise ValueError("no mutants were measured")
        timeout = self.counts.get("timeout", 0)
        if timeout and not timeouts_reverified:
            raise ValueError(
                f"{timeout} timeouts are not re-verified: re-run them alone (scripts/mutmut_score.py "
                "--timeouts lists them), then score with --timeouts-reverified")
        return (self.counts.get("killed", 0) + timeout) / self.total


def summarize(lines: Iterable[str]) -> Summary:
    """Tally `name: status` lines; every status but killed and timeout is a survivor to triage."""
    counts: Counter[str] = Counter()
    survivors: Counter[str] = Counter()
    timeouts: list[str] = []
    for line in lines:
        name, sep, status = line.strip().rpartition(": ")
        if not sep or "__mutmut_" not in name:
            continue
        counts[status] += 1
        if status == "timeout":
            timeouts.append(name)
        elif status != "killed":
            survivors[name.rsplit("__mutmut_", 1)[0]] += 1
    return Summary(counts=dict(counts), survivors_by_function=dict(survivors), timeouts=tuple(timeouts))


def render(summary: Summary, *, timeouts_reverified: bool) -> str:
    timeout = summary.counts.get("timeout", 0)
    others = ", ".join(f"{status}: {n}" for status, n in summary.counts.items() if status != "timeout")
    killed = summary.counts.get("killed", 0)
    state = "re-verified: still hanging, counted as detected" if timeouts_reverified else "NOT re-verified"
    try:
        score = f"{summary.score(timeouts_reverified=timeouts_reverified):.1%} " \
                f"(({killed} killed + {timeout} timeout) / {summary.total})"
    except ValueError as exc:
        score = f"none — {exc}"
    lines = [
        f"mutants: {summary.total} (timeout {timeout}, {state})",
        others,
        f"score: {score}",
        "not killed, by function:",
    ]
    for name, n in sorted(summary.survivors_by_function.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"  {n}  {name}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    out = subprocess.run([str(MUTMUT), "results", "--all", "true"], capture_output=True, text=True, check=True)
    summary = summarize(out.stdout.splitlines())
    if "--timeouts" in args:
        print("\n".join(summary.timeouts))
        return 0
    print(render(summary, timeouts_reverified="--timeouts-reverified" in args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
