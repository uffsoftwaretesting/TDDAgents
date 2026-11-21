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
extraction here feeds the project's declared test-file patterns (`classify_path`), so the TDD phase
rules govern them the same way. A declared manifest is writable in every phase, and with no
declaration a redirect is refused (the tier fails closed).

Two details carried over deliberately:

* **The original command is checked, never the split subcommands.** The splitter strips
  redirections, so a per-subcommand check cannot see them —
  `bashPermissions.ts` keeps a standing comment about exactly this bug class.
* **A target the tier cannot resolve is upstream's to ask about** (user decision 2026-11-21; it
  replaced "unparseable targets fail closed"). `extract_output_redirections` reports
  `has_dangerous_redirection` for a target built from shell expansion (`hasDangerousExpansion`,
  `reference/claude-code/src/utils/bash/commands.ts` l.830: `$`, `%`, backticks, globs, `{`, `!`,
  `=` and every `~`). Which file it lands on is not known here, so the tier judges only the targets it
  can resolve; the unresolvable one reaches upstream's `checkPathConstraints`
  (`tools/BashTool/pathValidation.ts` l.1013), which asks "Shell expansion syntax in paths requires
  manual approval" (l.1052), and without the sandbox the install gate asks the same, an ask
  `bypassPermissions` cannot skip. The user decides; with nobody to ask (headless, workers) it is
  denied. With no project declaration every redirect is still refused.
* **After a `cd` in the command, a relative target cannot be placed either** (2026-11-21). Any `cd`,
  `pushd` or `popd` other than the `cd <cwd>` models prepend (`compoundCommandHasCd`,
  `filterCdCwdSubcommands`, `bashPermissions.ts`) moves where the shell writes, so the tier judges only
  the absolute targets and leaves the relative ones to upstream's ask ("Compound command contains cd with
  output redirection", `validateOutputRedirections`, `pathValidation.ts` l.930-945). Before, `cd /tmp/spike
  && cat > main.go` in RED was refused as if `main.go` were the project's.

What this does **not** do, because upstream does not either: `tee`, `dd` and `truncate` get
no special handling. They are not read-only, so they still reach the permission gate on
their own merits; their targets are simply not extracted. Adding them would be an invention,
and the ledger — not this check — remains the authority on what was really proven.
"""

from __future__ import annotations

import os
import posixpath

from app.tdd.ledger import PhaseLedger, TddPhase
from app.tdd.phase_permissions import BACK_TO_RED, NO_PROJECT_CONFIG, classify_path
from app.tdd.project_config import TestFileRules
from app.utils.bash.commands import extract_output_redirections, split_command

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


def shell_directory(root: str | None, cwd_offset: str | None) -> str | None:
    """The shell's directory as an absolute path: the project `root` joined with the tracked `cwd_offset`."""
    if root is None:
        return None
    return posixpath.normpath(posixpath.join(root, cwd_offset or "."))


def changes_directory(command: str, cwd: str | None) -> bool:
    """
    `compoundCommandHasCd` (`reference/claude-code/src/tools/BashTool/bashPermissions.ts` l.2181-2200): any
    subcommand that is a `cd`, `pushd` or `popd` (`isNormalizedCdCommand`, l.2603), once the `cd <cwd>` models
    prepend is dropped (`filterCdCwdSubcommands`, l.1362-1377, called at l.2144-2157).
    """
    from app.tools.bash_tool.bash_permissions import is_normalized_cd_command

    own = f"cd {cwd}" if cwd is not None else None
    return any(is_normalized_cd_command(sub) for sub in split_command(command) if sub != own)


def check_bash_tdd_phase(command: str, ledger: PhaseLedger, cwd_offset: str | None = None, *,
                         rules: TestFileRules | None, root: str | None = None) -> tuple[bool, str]:
    """
    Decide whether `command`'s writes are allowed in the current phase.

    Returns `(permitted, reason)`. A command that redirects nowhere is always permitted
    here — it may still be refused by the ordinary Bash permission battery, which is a
    separate question from the phase. `cwd_offset` is where the shell is
    (`app/utils/path.py::tracked_cwd_offset`): a relative target is judged where it lands.
    """
    targets, dangerous = redirect_targets(command)
    # `dangerous` is upstream's to ask about (see the module docstring); with no declaration it is refused.
    if not targets and not dangerous:
        return True, ""
    if rules is None:
        return False, f"TDD phase {ledger.phase} denies this command's redirection: {NO_PROJECT_CONFIG}"
    if changes_directory(command, shell_directory(root, cwd_offset)):
        # After a `cd` the final directory is unknown (`compoundCommandHasCd`), so a relative target cannot be
        # placed: it is left to upstream's ask (`validateOutputRedirections`), as a shell-expansion target is.
        targets = tuple(t for t in targets if os.path.isabs(t))
    elif cwd_offset is not None:
        targets = tuple(t if os.path.isabs(t) or t.startswith("~")
                        else posixpath.normpath(posixpath.join(cwd_offset, t)) for t in targets)
    kinds = {target: classify_path(target, rules, root) for target in targets}

    if ledger.phase == TddPhase.RED:
        for target in targets:
            if kinds[target] == "production":
                return (
                    False,
                    (f"TDD phase RED denies writing to production file '{target}' through a "
                     "shell redirection. Only test files may be written before this "
                     "sub-requirement's tests are written and run."),
                )
    elif ledger.phase == TddPhase.GREEN:
        # REFACTOR may write both code and tests (Phase 12), so only GREEN guards the tests.
        for target in targets:
            if kinds[target] == "test":
                return (
                    False,
                    (f"TDD phase {ledger.phase} denies modifying test file '{target}' through "
                     "a shell redirection. The tests are what pins the behaviour being "
                     f"implemented; they can be improved in REFACTOR. {BACK_TO_RED}"),
                )

    return True, ""


__all__ = ["DEV_NULL", "check_bash_tdd_phase", "redirect_targets"]
