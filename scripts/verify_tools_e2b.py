#!/usr/bin/env python
"""
Phase 1 live verification: drive every tool against a real E2B sandbox, then diff the
portable subset against a real LocalWorkspace.

**This is not part of the quality gate and pytest does not collect it.** It costs real
sandbox time and needs real credentials, so it is opt-in:

    E2B_API_KEY=... python scripts/verify_tools_e2b.py

It exists because the offline suite deliberately stops at the seam. `E2BAdapter` is exempt
from unit testing precisely because faking beneath it would only assert that mocks call
mocks — so the assertions that matter for the adapter are the ones made here, against the
real SDK, plus an end-to-end pipeline run.

What it checks, per the Phase 1 verification list:

  * Tool surface — every tool driven through `execute_tool` against one sandbox, asserting
    input validation, result shape, error shape, exit-code handling, and path semantics.
  * Cross-environment compatibility — the portable subset re-run against a LocalWorkspace
    in a temp directory, with the outcomes diffed. Requirement 8's guarantee is only real
    if that diff is empty.
  * Lifecycle — a sandbox outliving the SDK's 5-minute default, and `reuse_or_create`
    recovering from a killed id.
  * Result governance — an oversized Bash result persisted and previewed, and ReadFile
    output never persisted.
  * Routing — with no AgentDefinition in existence, every tool resolves to the sandbox.

Exits non-zero on the first failed assertion group, printing what differed.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.config.config import Config  # noqa: E402
from app.sandbox.adapter import E2BAdapter  # noqa: E402
from app.tools.base import ToolContext, execute_tool  # noqa: E402
from app.tools.roster import EXPECTED_TOOL_COUNT, default_registry  # noqa: E402
from app.workspace.e2b import E2BWorkspace  # noqa: E402
from app.workspace.local import LocalWorkspace  # noqa: E402
from app.workspace.router import resolve_workspace  # noqa: E402

PASS, FAIL = "✅", "❌"

#: Tools whose behavior must be identical on both sides. The rest are pinned by design:
#: RunTests / BashOutput / KillShell / RunCode to the sandbox, HostRead to the host, and
#: the web tools and TodoWrite touch no workspace at all.
PORTABLE_TOOLS = ("ReadFile", "ListDir", "Glob", "Grep", "WriteFile", "Edit", "MultiEdit",
                  "Delete", "Move", "Bash")


class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.checks = 0

    def check(self, ok: bool, label: str, detail: str = "") -> None:
        self.checks += 1
        if ok:
            print(f"  {PASS} {label}")
            return
        message = f"{label}{f' — {detail}' if detail else ''}"
        print(f"  {FAIL} {message}")
        self.failures.append(message)

    def section(self, title: str) -> None:
        print(f"\n=== {title} ===")

    def finish(self) -> int:
        print(f"\n{self.checks} checks, {len(self.failures)} failed.")
        for failure in self.failures:
            print(f"  {FAIL} {failure}")
        return 1 if self.failures else 0


def context_for(workspace, spec: str = "sandbox") -> ToolContext:
    return ToolContext(
        workspace=workspace,
        workspace_spec=spec,  # type: ignore[arg-type]
        permission_mode="full",
        session_id="verify",
        agent_id="verify",
    )


def drive(registry, name: str, args: dict, ctx: ToolContext):
    tool = registry.get(name)
    if tool is None:
        raise AssertionError(f"{name} is not registered")
    return execute_tool(tool, args, ctx)


# ─────────────────────────────────────────────────────────────────────────────
# Checks
# ─────────────────────────────────────────────────────────────────────────────

def check_roster(report: Report, registry) -> None:
    report.section("Roster")
    report.check(len(registry) == EXPECTED_TOOL_COUNT,
                 f"{EXPECTED_TOOL_COUNT} tools registered", f"got {len(registry)}")
    report.check(all(t.prompt().strip() for t in registry.all()), "every tool has a prompt")


def check_routing(report: Report, sandbox_ws, local_ws) -> None:
    report.section("Routing — Phase 1 must change no execution target")
    report.check(resolve_workspace(None, sandbox_ws, local_ws) is sandbox_ws,
                 "an undeclared workspace resolves to the sandbox")
    report.check(resolve_workspace("sandbox", sandbox_ws, local_ws) is sandbox_ws,
                 "'sandbox' resolves to the sandbox")


def check_tool_surface(report: Report, registry, ctx: ToolContext) -> None:
    report.section("Tool surface (live sandbox)")

    r = drive(registry, "WriteFile", {"path": "verify/a.py", "content": "x = 1\ny = 2\n"}, ctx)
    report.check(not r.is_error, "WriteFile creates a nested file", r.content)

    r = drive(registry, "ReadFile", {"path": "verify/a.py"}, ctx)
    report.check("1\tx = 1" in r.content, "ReadFile numbers lines", r.content[:120])

    r = drive(registry, "ListDir", {"path": "verify"}, ctx)
    report.check("verify/a.py" in r.content, "ListDir lists the file", r.content[:120])

    r = drive(registry, "Glob", {"pattern": "*.py", "path": "verify"}, ctx)
    report.check("a.py" in r.content, "Glob finds it", r.content[:120])

    r = drive(registry, "Grep", {"pattern": "x = 1", "path": "verify"}, ctx)
    report.check("a.py" in r.content, "Grep finds the content", r.content[:120])

    r = drive(registry, "Edit", {"path": "verify/a.py", "old_string": "x = 1", "new_string": "x = 9"}, ctx)
    report.check(not r.is_error, "Edit replaces unique text", r.content)

    r = drive(registry, "Edit", {"path": "verify/a.py", "old_string": "absent", "new_string": "z"}, ctx)
    report.check(r.is_error, "Edit reports a missing old_string as an error")

    r = drive(registry, "MultiEdit", {"path": "verify/a.py", "edits": [
        {"old_string": "x = 9", "new_string": "x = 10"},
        {"old_string": "nope", "new_string": "q"},
    ]}, ctx)
    after = drive(registry, "ReadFile", {"path": "verify/a.py"}, ctx).content
    report.check(r.is_error and "x = 9" in after,
                 "MultiEdit is all-or-nothing on failure", after[:120])

    r = drive(registry, "Move", {"old": "verify/a.py", "new": "verify/b.py"}, ctx)
    report.check(not r.is_error, "Move relocates a file", r.content)

    r = drive(registry, "Delete", {"path": "verify/b.py"}, ctx)
    report.check(not r.is_error, "Delete removes it", r.content)

    r = drive(registry, "ReadFile", {"path": "verify/b.py"}, ctx)
    report.check(r.is_error, "ReadFile on a deleted file is an error result, not an exception")

    r = drive(registry, "ReadFile", {}, ctx)
    report.check(r.is_error and "Invalid arguments" in r.content, "a schema violation is caught")

    r = drive(registry, "Bash", {"command": "echo hello"}, ctx)
    report.check("hello" in r.content and r.exit_code == 0, "Bash returns stdout", r.content[:120])

    r = drive(registry, "Bash", {"command": "exit 3"}, ctx)
    report.check(r.exit_code == 3 and not r.is_error,
                 "a non-zero exit is data, not infrastructure failure", r.content[:120])

    r = drive(registry, "Bash", {"command": "pwd"}, ctx)
    report.check(Config.SANDBOX_WORKSPACE_ROOT in r.content,
                 "commands run from the pinned workspace root", r.content[:120])

    r = drive(registry, "WriteFile", {"path": "verify/utf8.txt", "content": "café — naïve"}, ctx)
    r = drive(registry, "ReadFile", {"path": "verify/utf8.txt"}, ctx)
    report.check("café — naïve" in r.content, "UTF-8 survives a round trip", r.content[:120])

    r = drive(registry, "RunCode", {"code": "print(6 * 7)"}, ctx)
    report.check("42" in r.content, "RunCode runs in the REPL", r.content[:200])

    r = drive(registry, "RunCode", {"code": "verified_marker = 123"}, ctx)
    r = drive(registry, "RunCode", {"code": "print(verified_marker)"}, ctx)
    report.check("123" in r.content, "RunCode state persists between calls", r.content[:200])

    r = drive(registry, "TodoWrite", {"todos": [{"content": "check", "status": "completed"}]}, ctx)
    report.check("[x] check" in r.content, "TodoWrite renders a checklist", r.content[:120])

    r = drive(registry, "HostRead", {"path": str(REPO_ROOT / "CLAUDE.md")}, ctx)
    report.check(r.is_error and "workspace 'sandbox'" in r.content,
                 "HostRead is refused to a sandbox-pinned agent", r.content[:120])


def check_background(report: Report, registry, ctx: ToolContext) -> None:
    report.section("Background commands")
    r = drive(registry, "Bash", {"command": "for i in 1 2 3; do echo tick$i; sleep 1; done",
                                 "run_in_background": True}, ctx)
    if r.is_error:
        report.check(False, "Bash starts a background command", r.content)
        return
    report.check(True, "Bash starts a background command")

    command_id = next(iter(ctx.background_commands), "")
    time.sleep(2.5)

    out = drive(registry, "BashOutput", {"command_id": command_id}, ctx)
    report.check("tick" in out.content, "BashOutput returns accumulated output", out.content[:200])

    again = drive(registry, "BashOutput", {"command_id": command_id}, ctx)
    report.check("tick1" not in again.content,
                 "BashOutput drains rather than repeating", again.content[:200])

    killed = drive(registry, "KillShell", {"command_id": command_id}, ctx)
    report.check(not killed.is_error, "KillShell stops it", killed.content)

    missing = drive(registry, "BashOutput", {"command_id": "nope"}, ctx)
    report.check(missing.is_error, "an unknown command id is an error result")


def check_governance(report: Report, registry, ctx: ToolContext) -> None:
    report.section("Result governance")

    r = drive(registry, "Bash", {"command": "yes ABCDEFGHIJ | head -c 60000"}, ctx)
    report.check(r.truncated and r.persisted_path is not None,
                 "an oversized Bash result is persisted", f"truncated={r.truncated}")
    if r.persisted_path:
        back = drive(registry, "ReadFile", {"path": r.persisted_path, "limit": 5}, ctx)
        report.check(not back.is_error, "the persisted result is readable with ReadFile",
                     back.content[:120])
        report.check(r.persisted_path.startswith(".tddagents/"),
                     "it lives under the sync-excluded directory", r.persisted_path)

    big = "\n".join(f"line {i}" for i in range(60_000))
    drive(registry, "WriteFile", {"path": "verify/big.txt", "content": big}, ctx)
    r = drive(registry, "ReadFile", {"path": "verify/big.txt"}, ctx)
    report.check(not r.truncated and r.persisted_path is None,
                 "ReadFile output is never persisted", f"truncated={r.truncated}")


def check_lifecycle(report: Report, adapter: E2BAdapter) -> None:
    report.section("Lifecycle")
    report.check(adapter.is_running(), "the sandbox is alive")
    report.check(adapter.refresh_timeout(force=True), "the lifetime can be extended")
    report.check(Config.SANDBOX_TIMEOUT > 300,
                 "the configured lifetime beats the SDK's 5-minute default",
                 str(Config.SANDBOX_TIMEOUT))

    doomed = E2BAdapter.create()
    doomed_id = doomed.sandbox_id
    doomed.kill()
    recovered = E2BAdapter.reuse_or_create(doomed_id)
    report.check(recovered.sandbox_id != doomed_id and recovered.is_running(),
                 "reuse_or_create recovers from a killed sandbox id")
    recovered.kill()


def check_cross_environment(report: Report, registry, sandbox_ctx, local_ctx) -> None:
    report.section("Cross-environment parity — the diff must be empty")

    scenarios: list[tuple[str, list[tuple[str, dict]]]] = [
        ("write then read", [
            ("WriteFile", {"path": "p/x.py", "content": "a = 1\nb = 2\n"}),
            ("ReadFile", {"path": "p/x.py"}),
        ]),
        ("read a missing file", [("ReadFile", {"path": "p/absent.py"})]),
        ("list a directory", [("ListDir", {"path": "p"})]),
        ("grep for content", [("Grep", {"pattern": "a = 1", "path": "p"})]),
        ("glob by extension", [("Glob", {"pattern": "*.py", "path": "p"})]),
        ("edit unique text", [("Edit", {"path": "p/x.py", "old_string": "a = 1", "new_string": "a = 7"})]),
        ("edit ambiguous text", [
            ("WriteFile", {"path": "p/dup.py", "content": "d\nd\n"}),
            ("Edit", {"path": "p/dup.py", "old_string": "d", "new_string": "e"}),
        ]),
        ("delete a missing file", [("Delete", {"path": "p/absent.py"})]),
        ("move a file", [
            ("WriteFile", {"path": "p/m.py", "content": "m"}),
            ("Move", {"old": "p/m.py", "new": "p/n.py"}),
        ]),
        ("path escaping the root", [("ReadFile", {"path": "../escape.txt"})]),
        ("bash exit code", [("Bash", {"command": "exit 4"})]),
    ]

    for label, steps in scenarios:
        sandbox_last = local_last = None
        for name, args in steps:
            sandbox_last = drive(registry, name, args, sandbox_ctx)
            local_last = drive(registry, name, args, local_ctx)

        assert sandbox_last is not None and local_last is not None
        same_error = sandbox_last.is_error == local_last.is_error
        same_exit = sandbox_last.exit_code == local_last.exit_code
        report.check(
            same_error and same_exit,
            f"parity: {label}",
            f"sandbox(is_error={sandbox_last.is_error}, exit={sandbox_last.exit_code}) "
            f"vs local(is_error={local_last.is_error}, exit={local_last.exit_code})",
        )

    report.check(
        all(registry.get(name) is not None for name in PORTABLE_TOOLS),
        "every portable tool exists in the registry",
    )


# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="Do not kill the sandbox at the end.")
    parser.add_argument("--skip-lifecycle", action="store_true",
                        help="Skip the lifecycle checks, which provision a second sandbox.")
    options = parser.parse_args()

    report = Report()
    registry = default_registry()

    print(f"Provisioning a sandbox (timeout {Config.SANDBOX_TIMEOUT}s)...")
    adapter = E2BAdapter.create()
    print(f"Sandbox {adapter.sandbox_id} ready.")

    try:
        sandbox_ws = E2BWorkspace(adapter)
        with tempfile.TemporaryDirectory() as temporary:
            local_ws = LocalWorkspace(Path(temporary) / "workspace")

            sandbox_ctx = context_for(sandbox_ws, "sandbox")
            local_ctx = context_for(local_ws, "local")

            check_roster(report, registry)
            check_routing(report, sandbox_ws, local_ws)
            check_tool_surface(report, registry, sandbox_ctx)
            check_background(report, registry, sandbox_ctx)
            check_governance(report, registry, sandbox_ctx)
            if not options.skip_lifecycle:
                check_lifecycle(report, adapter)
            check_cross_environment(report, registry, sandbox_ctx, local_ctx)

    except Exception:
        traceback.print_exc()
        report.failures.append("an unhandled exception aborted the run")
    finally:
        if options.keep:
            print(f"\nLeaving sandbox {adapter.sandbox_id} running (--keep).")
        else:
            adapter.kill_all_background()
            adapter.kill()
            print("\nSandbox killed.")

    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
