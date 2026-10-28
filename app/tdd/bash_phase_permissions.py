"""
TDD phase enforcement for `Bash` write targets (Phase 2) — barrier 2's missing side.

**The hole this closes.** The phase gate inspects a tool's declared path argument, so it
sees `WriteFile(file_path="calculator.py")` and refuses it in RED. It never saw
`Bash("echo 'def add(a,b): return a+b' > calculator.py")`, which writes the same file
through a tool whose only argument is a string. One barrier of three was therefore
bypassable by rephrasing, which is exactly the kind of hole a model finds by accident.

**The mechanism is upstream's, pointed at the ledger instead of the filesystem.**
`tools/BashTool/pathValidation.ts::validateOutputRedirections` extracts every output
redirection from a command and runs each target through `validatePath(..., 'create')`,
which `utils/permissions/pathValidation.ts` maps onto the `Edit` permission type — so
`Edit(...)` rules govern redirect targets without the `Edit` tool ever running. The same
extraction here feeds `is_test_path`, so the TDD phase rules govern them the same way.

Two details carried over deliberately:

* **The original command is checked, never the split subcommands.** The splitter strips
  redirections, so a per-subcommand check cannot see them —
  `bashPermissions.ts` keeps a standing comment about exactly this bug class.
* **Unparseable targets fail closed.** `extract_output_redirections` reports
  `has_dangerous_redirection` when a command cannot be parsed or a target uses expansion,
  and in a phase that forbids the write that is a refusal, not a shrug.

What this does **not** do, because upstream does not either: `tee`, `dd` and `truncate` get
no special handling. They are not read-only, so they still reach the permission gate on
their own merits; their targets are simply not extracted. Adding them would be an invention,
and the ledger — not this check — remains the authority on what was really proven.
"""

from __future__ import annotations

from app.tdd.ledger import PhaseLedger, TddPhase
from app.tdd.phase_permissions import BACK_TO_RED, is_test_path
from app.utils.bash.commands import extract_output_redirections

#: Redirecting here writes nothing, so no phase can object. Upstream short-circuits it too.
DEV_NULL = "/dev/null"


def redirect_targets(command: str) -> tuple[tuple[str, ...], bool]:
    """
    Every output-redirection target in `command`, plus whether any was unparseable.

    `/dev/null` is dropped: it is a discard, not a write.
    """
    extraction = extract_output_redirections(command)
    targets = tuple(
        redirection.target
        for redirection in extraction.redirections
        if redirection.target != DEV_NULL
    )
    return targets, extraction.has_dangerous_redirection


def check_bash_tdd_phase(command: str, ledger: PhaseLedger) -> tuple[bool, str]:
    """
    Decide whether `command`'s writes are allowed in the current phase.

    Returns `(permitted, reason)`. A command that redirects nowhere is always permitted
    here — it may still be refused by the ordinary Bash permission battery, which is a
    separate question from the phase.
    """
    targets, dangerous = redirect_targets(command)
    if not targets and not dangerous:
        return True, ""

    if ledger.phase == TddPhase.RED:
        if dangerous:
            return (
                False,
                ("TDD phase RED cannot verify where this command writes: its redirection "
                 "target uses shell expansion or could not be parsed. Write the test file "
                 "with WriteFile instead."),
            )
        for target in targets:
            if not is_test_path(target):
                return (
                    False,
                    (f"TDD phase RED denies writing to production file '{target}' through a "
                     "shell redirection. Only test files may be written before this "
                     "sub-requirement's tests are written and run."),
                )
    elif ledger.phase == TddPhase.GREEN:
        # REFACTOR may write both code and tests (Phase 12), so only GREEN guards the tests.
        if dangerous:
            return (
                False,
                (f"TDD phase {ledger.phase} cannot verify where this command writes: its "
                 "redirection target uses shell expansion or could not be parsed."),
            )
        for target in targets:
            if is_test_path(target):
                return (
                    False,
                    (f"TDD phase {ledger.phase} denies modifying test file '{target}' through "
                     "a shell redirection. The tests are what pins the behaviour being "
                     f"implemented; they can be improved in REFACTOR. {BACK_TO_RED}"),
                )

    return True, ""


__all__ = ["DEV_NULL", "check_bash_tdd_phase", "redirect_targets"]
