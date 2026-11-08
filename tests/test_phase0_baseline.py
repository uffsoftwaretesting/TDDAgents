"""
Phase 0 of docs/refactoring_transition_plan.md: a runnable, honest baseline.

Covers the session Python environment, workspace environment merging, the end-of-run
export, the optional E2B key, the import health of the entry point, the single-ledger-writer
invariant, and the production runner driven end to end by a scripted model on a real
workspace with real file tools and real pytest.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.query.state import initial_loop_state
from app.state.app_state_store import AppState, AppStateStore
from app.tdd.ledger import PhaseLedger, TddPhase
from app.tool import tool_context_for
from app.types.permissions import PermissionMode
from app.workspace.local import LocalWorkspace, orchestrator_free_environment
from tests.conftest import REQUIRED_PACKAGES
from tests.source_scan import calls_to, original_nodes

REPO = Path(__file__).resolve().parent.parent

#: The first TDD-state announcement a fresh run attaches after the prompt (Phase 5).


def initial_tdd_state(sub_requirement: str) -> str:
    """The first TDD-state announcement of a one-item plan (Phase 11 adds the item)."""
    return (
        "<system-reminder>\nTDD phase ledger (written only by RunTests from observed test runs):\n"
        "Current Phase: RED\nTests Written: False\nGreen Passed: False\nRefactor Passed: False\n"
        "Coverage: not measured\n"
        f"Current Sub-requirement: 1. {sub_requirement}\nSub-requirements Done: 0/1\n</system-reminder>"
    )


# ── the test venv and the project's environment (2026-11-03) ─────────────────


def test_required_packages_pin_matches_requirements():
    reqs = (REPO / "requirements.txt").read_text().split()
    assert REQUIRED_PACKAGES == ("pip==26.0.1", "pytest==9.0.1", "pytest-cov==7.0.0", "coverage==7.11.3")
    assert all(pkg in reqs for pkg in REQUIRED_PACKAGES)


def test_no_session_venv_is_built_any_more():
    """User decision 2026-11-03: as claude-code, the project sets itself up from its own manifests."""
    assert not (REPO / "app" / "workspace" / "pyenv.py").exists()


def test_the_test_venv_is_real_and_isolated(test_venv):
    python = test_venv / "bin" / "python"
    script = "import pytest, pytest_cov, sys; print(pytest.__version__);print(sys.prefix)"
    out = subprocess.run([str(python), "-c", script], capture_output=True, text=True, check=True).stdout.split()
    assert out == ["9.0.1", str(test_venv)]
    # TDDAgents' own dependencies are not visible to generated code
    assert subprocess.run([str(python), "-c", "import langchain_core"], capture_output=True).returncode != 0


@pytest.mark.parametrize(("environ", "expected"), [
    ({"PATH": "/own/bin:/usr/bin::/own/bin/../bin"}, {"PATH": "/usr/bin:"}),
    ({"PATH": "/usr/bin", "VIRTUAL_ENV": "/own"}, {"PATH": "/usr/bin"}),
    ({"PATH": "/usr/bin", "VIRTUAL_ENV": "/other"}, {"PATH": "/usr/bin", "VIRTUAL_ENV": "/other"}),
    ({"HOME": "/h"}, {"HOME": "/h"}),
])
def test_the_orchestrators_interpreter_leaves_the_environment(environ, expected):
    assert orchestrator_free_environment(environ, prefix="/own") == expected


def test_a_project_command_never_resolves_the_orchestrators_python(tmp_path, monkeypatch):
    """PATH isolation: TDDAgents runs from its venv, and the project must not see it."""
    own_bin = os.path.join(sys.prefix, "bin")
    monkeypatch.setenv("PATH", f"{own_bin}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("VIRTUAL_ENV", sys.prefix)
    ws = LocalWorkspace(str(tmp_path / "w"))
    path = ws.execute('echo "$PATH"').stdout.strip().split(os.pathsep)
    assert own_bin not in path
    assert ws.execute('echo "${VIRTUAL_ENV:-unset}"').stdout.strip() == "unset"


# ── workspace environment ────────────────────────────────────────────────────

def test_workspace_env_is_merged_under_per_call_env(tmp_path):
    ws = LocalWorkspace(str(tmp_path / "w"), env={"A": "ws", "B": "ws"})
    out = ws.execute('echo "$A $B $HOME"', env={"B": "call"}).stdout.split()
    assert out[:2] == ["ws", "call"]
    assert out[2] == os.environ["HOME"]
    assert LocalWorkspace(str(tmp_path / "v")).execute('echo "${A:-unset}"').stdout.strip() == "unset"


def test_workspace_run_dir_and_for_run_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_WORKSPACES_DIR", str(tmp_path / "runs"))
    ws = LocalWorkspace.for_run("thread-1", env={"X": "1"})
    assert ws.root == (tmp_path / "runs" / "thread-1" / "project").resolve()
    assert ws.run_dir == ws.root.parent
    assert ws.execute('echo "$X"').stdout.strip() == "1"


def test_python_workspace_declares_the_test_venvs_pytest(python_workspace, test_venv):
    from app.tdd.project_config import load_project_config
    from tests.plan_helpers import TEST_PROJECT_CONFIG

    assert load_project_config(python_workspace) == TEST_PROJECT_CONFIG
    assert python_workspace.execute('"$TDDAGENTS_TEST_PYTHON" -m pytest --version').exit_code == 0
    assert python_workspace.execute('echo "$TDDAGENTS_TEST_PYTHON"').stdout.strip() == str(test_venv / "bin" / "python")


# ── entry point health and the optional E2B key ──────────────────────────────

def _clean_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("E2B_API_KEY", "OPENAI_API_KEY")}
    env.update(PYTHONPATH=str(REPO), **extra)
    if "MUTANT_UNDER_TEST" in env:
        # Under mutmut the child imports the mutated copy, whose trampolines need mutmut's config,
        # which only the parent has. "" is the trampolines' unmutated baseline.
        env["MUTANT_UNDER_TEST"] = ""
    return env


def test_app_main_imports_without_an_e2b_key(tmp_path):
    """The import chain app.main -> session driver -> runner -> factory was broken (ImportError)."""
    env = _clean_env(OPENAI_API_KEY="k")
    res = subprocess.run([sys.executable, "-c", "import app.main, app.query.deps, app.cli.print"],
                         cwd=tmp_path, env=env, capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stderr


def test_production_deps_are_complete():
    from app.query.deps import get_production_deps
    from app.services.api.claude import stream_call_model
    from app.services.tools.tool_orchestration import run_tools

    deps = get_production_deps()
    assert deps.call_model is stream_call_model
    assert deps.run_tools is run_tools
    assert isinstance(deps.uuid(), str) and len(deps.uuid()) == 36
    assert deps.uuid() != deps.uuid()
    assert deps.now() > 1_700_000_000
    deps.emit_event(object())


def test_openai_stays_required(tmp_path):
    res = subprocess.run([sys.executable, "-c", "import app.config.config"], cwd=tmp_path,
                         env=_clean_env(), capture_output=True, text=True,
                         check=False)
    assert "No API key configured" in res.stderr


def test_sandbox_requires_the_key_only_when_used(monkeypatch):
    from app.config.config import Config
    from app.sandbox.adapter import E2BAdapter, require_e2b_api_key
    from app.workspace.base import WorkspaceAuthError

    monkeypatch.setattr(Config, "E2B_API_KEY", None)
    with pytest.raises(WorkspaceAuthError, match="E2B_API_KEY is not configured"):
        require_e2b_api_key()
    with pytest.raises(WorkspaceAuthError):
        E2BAdapter.create()
    with pytest.raises(WorkspaceAuthError):
        E2BAdapter.connect("sbx")
    monkeypatch.setattr(Config, "E2B_API_KEY", "set")
    require_e2b_api_key()


# ── barrier 1: RunTests is the only ledger writer ────────────────────────────

def _ledger_writers() -> list[str]:
    """Every app/ file holding a set_app_state call whose updater touches phase_ledger.

    Scanned through `tests/source_scan.py`, so mutmut's mutant copies are never read.
    """
    return sorted(
        path.relative_to(REPO).as_posix()
        for path in (REPO / "app").rglob("*.py")
        if any("phase_ledger" in call for call in calls_to(original_nodes(path), "set_app_state"))
    )


def test_run_tests_is_the_only_ledger_writer():
    """
    `RunTests` writes every observation. Since Phase 7 the Agent tool also writes the ledger,
    since Phase 8 the Skill tool's forked path (user decision 2026-10-25) and since Phase 11 a
    plan change that moves to another sub-requirement, but all of them only
    through `phase_on_spawn` (`PhaseLedger.transition_to`), which can move the phase or erase
    evidence and never create an observation — pinned here so another writer, or a spawn
    write that bypasses `transition_to`, cannot appear unnoticed.
    """
    spawn_writers = ["app/tools/agent_tool/agent_tool.py", "app/tools/skill_tool/skill_tool.py"]
    advance = "app/tdd/plan.py"
    assert _ledger_writers() == [advance, "app/tdd/run_tests_tool/run_tests_tool.py", *spawn_writers]
    # Phase 11: a plan change that moves the work to another sub-requirement restarts the
    # ledger in RED, and nothing else: the same erase-only `transition_to`.
    advance_nodes = original_nodes(REPO / advance)
    advance_writes = [w for w in calls_to(advance_nodes, "set_app_state") if "phase_ledger" in w]
    assert advance_writes and all("phase_ledger=reset_ledger" in w for w in advance_writes)
    resets = [ast.unparse(n) for n in advance_nodes if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == "reset_ledger" for t in n.targets)]
    assert resets == ["reset_ledger = ledger.transition_to(TddPhase.RED)"]
    for path in spawn_writers:
        nodes = original_nodes(REPO / path)
        writes = calls_to(nodes, "set_app_state")
        assert writes and all("phase_ledger=spawn_ledger" in w for w in writes), path
        assigns = [ast.unparse(n) for n in nodes if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "spawn_ledger" for t in n.targets)]
        assert assigns and all(a.startswith("spawn_ledger = phase_on_spawn(") for a in assigns), path


def test_run_tests_is_the_only_test_floor_writer():
    """
    The session's test floor (2026-11-08) is an observation like the ledger's: only `RunTests` writes it,
    through `next_test_floor`. No plan change, spawn or rewind touches it, and nothing restores it.
    """
    mentions = sorted(
        path.relative_to(REPO).as_posix()
        for path in (REPO / "app").rglob("*.py")
        if any("test_floor=" in ast.unparse(node) for node in original_nodes(path) if isinstance(node, ast.Call))
    )
    assert mentions == ["app/tdd/run_tests_tool/run_tests_tool.py"]
    writes = [w for w in calls_to(original_nodes(REPO / mentions[0]), "set_app_state") if "test_floor" in w]
    assert len(writes) == 1 and "test_floor=next_test_floor(s.test_floor, record, judgement)" in writes[0]


def test_initial_state_reads_the_store_and_never_writes_it():
    writes: list[Any] = []
    store = AppStateStore(AppState(phase_ledger=PhaseLedger(phase=TddPhase.GREEN, tests_written=True)))
    ctx = tool_context_for(store)
    ctx = dataclasses.replace(ctx, set_app_state=writes.append)
    state = initial_loop_state((), ctx)
    assert state.phase_ledger == PhaseLedger(phase=TddPhase.GREEN, tests_written=True)
    assert writes == []


def test_engine_does_not_write_a_stale_state_ledger_back():
    """A LoopState ledger that differs from the store must not overwrite the store."""
    from app.query.config import build_run_config
    from app.query.deps import LoopDeps
    from app.query.query import drain, run_loop
    from app.query.state import CompactionResult
    from app.query.stop_hooks import StopHookResult

    store = AppStateStore()
    state = dataclasses.replace(initial_loop_state((), tool_context_for(store)),
                                phase_ledger=PhaseLedger(phase=TddPhase.GREEN, tests_written=True,
                                                         green_passed=True))

    async def model(s: Any, c: Any) -> AsyncIterator[Any]:
        yield AIMessage(content="done")

    async def no_compact(s: Any, c: Any) -> CompactionResult:
        return CompactionResult(compacted=False, messages=s.messages)

    async def no_stop(s: Any, c: Any) -> StopHookResult:
        return StopHookResult()

    async def no_tools(*a: Any, **k: Any) -> AsyncIterator[Any]:
        if False:
            yield None

    deps = LoopDeps(call_model=model, run_tools=no_tools, compact=no_compact, stop_hooks=no_stop,
                    uuid=lambda: "u", now=lambda: 0.0, emit_event=lambda e: None)
    asyncio.run(drain(run_loop(state, build_run_config("r"), deps)))
    assert store.get().phase_ledger == PhaseLedger()


# ── the production runner, end to end with a scripted model ──────────────────

def _scripted_deps(turns: list[list[AIMessage]], seen: list[Any] | None = None) -> Any:
    from app.query.deps import get_production_deps

    async def model(state: Any, config: Any) -> AsyncIterator[Any]:
        if seen is not None:
            seen.append(state)
        if not turns:
            raise AssertionError("model asked for more turns than scripted")
        for msg in turns.pop(0):
            yield msg

    return dataclasses.replace(get_production_deps(), call_model=model)


def _call(name: str, cid: str, **args: Any) -> AIMessage:
    return AIMessage(content=f"{name}", tool_calls=[{"name": name, "args": args, "id": cid}])


CALC_TEST = "import calculator\n\ndef test_add():\n    assert calculator.add(2, 3) == 5\n"
CALC_IMPL = "def add(a, b):\n    return a + b\n"


def test_runner_drives_a_real_red_green_cycle(python_workspace, tmp_path):
    from tests.session_run_helpers import run_item

    project = tmp_path / "project"
    (project / ".tddagents").mkdir(parents=True)
    (project / ".tddagents" / "settings.json").write_text(json.dumps({
        "permissions": {"defaultMode": "acceptEdits", "allow": ["Bash(python:*)", "Bash(pytest:*)"],
                        "additionalDirectories": [str(python_workspace.root / "tests"), str(tmp_path)]}}))
    (tmp_path / "home" / ".tddagents").mkdir(parents=True)
    (tmp_path / "home" / ".tddagents" / "settings.json").write_text(json.dumps({
        "permissions": {"deny": ["Bash(from-home)"]}}))
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "managed-settings.json").write_text(json.dumps({
        "permissions": {"ask": ["Bash(from-policy)"]}}))

    turns = [
        [_call("WriteFile", "c1", file_path="tests/test_calc.py", content=CALC_TEST)],
        [_call("RunTests", "c2", test_path="tests")],
        [_call("WriteFile", "c3", file_path="calculator.py", content=CALC_IMPL)],
        [_call("RunTests", "c4", test_path="tests")],
        [_call("RunTests", "c4b", test_path="tests")],
        [AIMessage(content="Red, green and refactor observed.")],
    ]
    seen: list[Any] = []
    result = asyncio.run(run_item("add two numbers", 0, {"session_id": "s"},
                                  workspace=python_workspace, deps=_scripted_deps(turns, seen),
                                  project_root=project, home=tmp_path / "home",
                                  managed_dir=tmp_path / "etc"))
    assert (result.status, result.terminal_reason) == ("success", "completed")
    assert (result.tests_written, result.green_passed) == (True, True)
    assert result.error_message is None and result.sub_requirement == "add two numbers"
    assert python_workspace.read_file("calculator.py") == CALC_IMPL
    ctx = seen[0].tool_context
    assert ctx.workspace is python_workspace
    # The permission context lives in app state (upstream `appState.toolPermissionContext`):
    # acceptEdits from project settings, headless, anchored at the workspace, with the
    # interpreter allow stripped by the TDD managed tier.
    assert ctx.permission_context is None
    perm = ctx.get_app_state().tool_permission_context
    assert (perm.mode, perm.should_avoid_permission_prompts) == (PermissionMode.ACCEPT_EDITS, True)
    assert [r.rule_content for r in perm.always_allow_rules] == ["pytest:*"]
    assert [r.rule_content for r in perm.stripped_dangerous_rules] == ["python:*"]
    # User and policy scopes come from the given home / managed dir, and an additional
    # directory inside the workspace is skipped because the workspace is the working dir.
    assert [r.rule_content for r in perm.always_deny_rules] == ["from-home"]
    assert [r.rule_content for r in perm.always_ask_rules] == ["from-policy"]
    assert perm.additional_working_directories == (str(tmp_path),)
    # Phase 7: the main thread also holds the Agent tool, as upstream's always does; Phase 8
    # adds TaskOutput and TaskStop (SendMessage is off without agent teams), Phase 11 UpdatePlan,
    # and AskUserQuestion is upstream's main-thread base tool, as are EnterPlanMode and ExitPlanMode.
    assert {t.name for t in ctx.tools} == {
        "ReadFile", "WriteFile", "Edit", "Glob", "Grep", "Bash", "RunTests", "TodoWrite", "Agent",
        "TaskOutput", "TaskStop", "Skill", "UpdatePlan", "AskUserQuestion", "WebFetch", "EnterPlanMode",
        "ExitPlanMode"}
    assert seen[0].messages[0].content == "add two numbers"
    # Phase 5: the user-input attachments follow the prompt — since Phase 8 the skill listing,
    # then the initial TDD state (upstream's source order).
    kinds = [m.additional_kwargs["attachment"]["type"] for m in seen[0].messages[1:3]]
    assert kinds == ["skill_listing", "tdd_state"]
    assert ctx.messages == seen[0].messages[:3]
    assert ctx.run_id.startswith("loop-") and len(ctx.run_id) == len("loop-") + 32
    assert seen[0].turn_count == 1
    # `refresh_tools`: the item completed, so the pool lost its phase rules (no current
    # sub-requirement) and is then returned *by identity* until the key moves again.
    assert ctx.refresh_tools is not None
    current = ctx.refresh_tools()
    assert current is not ctx.tools and current is ctx.refresh_tools()
    assert {t.name for t in current} == {t.name for t in ctx.tools}
    # With no current item a ledger move changes nothing; a new item brings the phase rules back.
    ctx.set_app_state(lambda st: dataclasses.replace(st, phase_ledger=PhaseLedger(phase=TddPhase.GREEN)))
    assert ctx.refresh_tools() is current
    ctx.set_app_state(lambda st: dataclasses.replace(st, tdd_plan=st.tdd_plan.add("next")))
    rebuilt = ctx.refresh_tools()
    assert rebuilt is not current and rebuilt is ctx.refresh_tools()


def test_runner_blocks_production_writes_in_red(python_workspace):
    """Barrier 2 at the runtime gate: the first write to production code in RED is refused."""
    from tests.session_run_helpers import run_item

    turns = [
        [_call("WriteFile", "c1", file_path="calculator.py", content=CALC_IMPL)],
        [AIMessage(content="giving up")],
    ]
    seen: list[Any] = []
    result = asyncio.run(run_item("x", 3, {"session_id": "s"}, workspace=python_workspace,
                                  deps=_scripted_deps(turns, seen)))
    assert not python_workspace.exists("calculator.py")
    refusal = next(m for m in seen[1].messages if getattr(m, "tool_call_id", None) == "c1")
    assert "TDD phase RED denies" in str(refusal.content)
    assert (result.index, result.tests_written, result.green_passed) == (3, False, False)


def test_runner_reports_failure_without_inventing_progress(python_workspace):
    from tests.session_run_helpers import run_item

    async def crashing(state: Any, config: Any) -> AsyncIterator[Any]:
        raise RuntimeError("provider down")
        yield  # pragma: no cover

    from app.query.deps import get_production_deps

    deps = dataclasses.replace(get_production_deps(), call_model=crashing)
    result = asyncio.run(run_item("x", 1, {"session_id": "s"}, workspace=python_workspace, deps=deps))
    assert (result.status, result.terminal_reason) == ("failed", "model_error")
    assert result.error_message == "Terminated early: model_error"
    assert (result.tests_written, result.green_passed) == (False, False)


def test_runner_crash_is_a_model_error(python_workspace, monkeypatch):
    from app import query_engine
    from tests.session_run_helpers import run_item

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(query_engine, "run_loop", boom)
    result = asyncio.run(run_item("x", 0, {"session_id": "s"}, workspace=python_workspace,
                                  deps=_scripted_deps([])))
    assert result.terminal_reason == "model_error" and result.status == "failed"


def test_runner_writes_the_transcript_outside_the_workspace(python_workspace):
    """Phase 6: upstream's `<config>/projects/<sanitized cwd>/<session>.jsonl`, plus the
    session's `events.jsonl`; nothing lands in the workspace."""
    from app.utils.session_storage_portable import get_project_dir
    from tests.session_run_helpers import run_item

    asyncio.run(run_item("x", 0, {"session_id": "s"}, workspace=python_workspace,
                         deps=_scripted_deps([[AIMessage(content="done")]])))
    project = get_project_dir(str(python_workspace.root))
    transcripts = list(project.glob("loop-*.jsonl"))
    assert len(transcripts) == 1
    assert (project / transcripts[0].stem / "events.jsonl").is_file()
    # The project's `.tddagents/` holds only its settings (Phase 13), never run output.
    assert sorted(p.name for p in (python_workspace.root / ".tddagents").iterdir()) == [
        "project.json", "settings.json"]


def _recording_deps(seen: list[Any]) -> Any:
    from app.query.deps import get_production_deps

    async def model(state: Any, config: Any) -> AsyncIterator[Any]:
        seen.append((state, config))
        yield AIMessage(content="done")

    return dataclasses.replace(get_production_deps(), call_model=model)


def test_runner_hands_the_loop_its_run_config_and_context(python_workspace):
    import re

    from tests.session_run_helpers import run_item

    seen: list[Any] = []
    asyncio.run(run_item("spec", 0, {"session_id": "s"}, workspace=python_workspace,
                         deps=_recording_deps(seen)))
    state, config = seen[0]
    assert re.fullmatch(r"loop-[0-9a-f]{32}", config.run_id)
    from app.utils.session_storage_portable import get_transcript_path

    assert get_transcript_path(config.run_id, str(python_workspace.root)).is_file()
    # Phase 8: the skill listing precedes the TDD state (upstream's source order).
    contents = [m.content for m in state.tool_context.messages]
    assert contents[0] == "spec" and contents[-1] == initial_tdd_state("spec")
    assert "The following skills are available for use with the Skill tool" in contents[1]
    assert len(contents) == 3


def test_runner_defaults_to_the_production_deps(monkeypatch, python_workspace):
    import app.query.deps as factory
    from tests.session_run_helpers import run_item

    seen: list[Any] = []
    deps = _recording_deps(seen)
    monkeypatch.setattr(factory, "get_production_deps", lambda: deps)
    result = asyncio.run(run_item("spec", 0, {"session_id": "abc"}, workspace=python_workspace))
    assert seen[0][0].tool_context.workspace is python_workspace
    # Phase 4: the production stop hooks enforce barrier 3. A model that stops without the
    # cycle is refused every time (Phase 12: no latch) until the user aborts — never `completed`.
    assert len(seen) == 3
    assert "TDD cycle incomplete: RED." in str(seen[1][0].messages[-1].content)
    assert "TDD cycle incomplete: RED." in str(seen[2][0].messages[-1].content)
    assert result.terminal_reason == "aborted_streaming"
    assert result.status == "failed"


def test_runner_tolerates_a_workspace_without_a_run_dir(make_fake_workspace):
    from tests.session_run_helpers import run_item

    ws = make_fake_workspace()
    assert not hasattr(ws, "run_dir")
    result = asyncio.run(run_item("x", 0, {"session_id": "s"}, workspace=ws, deps=_recording_deps([])))
    assert result.terminal_reason == "aborted_streaming"


def test_session_workspace_is_the_seeded_project_with_no_environment(tmp_path, monkeypatch):
    import app.cli.print as runner_mod

    monkeypatch.setenv("TDDAGENTS_WORKSPACES_DIR", str(tmp_path / "runs"))
    monkeypatch.delenv("TDDAGENTS_ENV_FILE", raising=False)
    ws = runner_mod.session_workspace("t-9")
    project = (tmp_path / "runs" / "t-9" / "project").resolve()
    assert ws.root == project and ws.run_dir == project.parent
    # No venv and no session environment script (2026-11-03, as upstream).
    assert not (project / ".venv").exists() and not (project.parent / "session-env.sh").exists()
    assert ws.execute('echo "${TDDAGENTS_ENV_FILE:-unset}"').stdout.strip() == "unset"
    # The project has its seeded settings, and no git of ours (Phase 13, as upstream).
    assert not (project / ".git").exists() and not (project / ".gitignore").exists()
    shipped = (REPO / ".tddagents" / "settings.json").read_text()
    assert (project / ".tddagents" / "settings.json").read_text() == shipped


# ── edges the mutation run surfaced ──────────────────────────────────────────

def test_production_compact_is_an_inert_passthrough():
    from app.query.deps import get_production_deps, production_compact

    assert get_production_deps().compact is production_compact
    state = initial_loop_state((HumanMessage(content="hi"),), tool_context_for(AppStateStore()))
    compacted = asyncio.run(production_compact(state, None))  # type: ignore[arg-type]
    assert compacted.compacted is False
    assert compacted.messages == state.messages
    assert compacted.tracking is state.compaction_tracking


async def _as_coroutine(awaitable: Any) -> Any:
    return await awaitable


def test_production_stop_hooks_enforce_barrier_three():
    """Phase 4: the production stop hooks are no longer a no-op — RED cannot terminate."""
    from app.query.deps import get_production_deps
    from app.query.stop_hooks import StopHookResult

    state = initial_loop_state((HumanMessage(content="hi"),), tool_context_for(AppStateStore()))
    pending = get_production_deps().stop_hooks(state, None)  # type: ignore[arg-type]
    result: StopHookResult = asyncio.run(_as_coroutine(pending))
    assert result.prevent_continuation is False
    assert len(result.blocking_errors) == 1
    assert "TDD cycle incomplete: RED." in str(result.blocking_errors[0].content)


def test_local_execute_replaces_undecodable_output(tmp_path):
    ws = LocalWorkspace(tmp_path)
    res = ws.execute("printf 'a\\377b'")
    assert res.exit_code == 0
    assert res.stdout == "a�b"


def test_local_resolve_names_the_escaping_path(tmp_path):
    from app.workspace.base import WorkspacePathError

    with pytest.raises(WorkspacePathError, match=r"^Path '\.\./x' escapes the workspace root\.$"):
        LocalWorkspace(tmp_path).resolve("../x")


def test_run_tests_carries_workspace_stderr(make_fake_workspace):
    from app.tdd.run_tests_tool.run_tests_tool import _default_test_runner
    from app.workspace.base import CommandResult

    from tests.plan_helpers import TEST_PROJECT_CONFIG

    ws = make_fake_workspace()
    ws.execute = lambda cmd, timeout=None, env=None: CommandResult(exit_code=1, stdout="out", stderr="err",
                                                                   duration=0.0, workspace="local")
    ctx = tool_context_for(AppStateStore(AppState(project_config=TEST_PROJECT_CONFIG)), workspace=ws)
    res = asyncio.run(_default_test_runner(ctx))
    assert (res.exit_code, res.stdout, res.stderr, res.command) == (1, "out", "err", TEST_PROJECT_CONFIG.test)


def test_run_tests_rejects_a_runner_result_without_an_exit_code():
    from app.tdd.run_tests_tool.run_tests_tool import (
        RunTestsUnavailable,
        _default_test_runner,
    )

    ctx: Any = SimpleNamespace(test_runner=lambda context: object())
    with pytest.raises(RunTestsUnavailable, match="^the attached test runner returned no exit code$"):
        asyncio.run(_default_test_runner(ctx))


def test_run_tests_tool_is_serial():
    from app.tdd.run_tests_tool.run_tests_tool import build_run_tests_tool

    assert build_run_tests_tool().is_concurrency_safe({}) is False


def test_the_runner_refreshes_the_pool_only_when_the_phase_moves(python_workspace):
    """
    `refreshTools` wiring: RED hides the production writers, and the GREEN that RunTests
    records must bring them back — on the next turn, not the next sub-requirement.
    """
    from app.tdd.ledger import TddPhase
    from tests.session_run_helpers import run_item

    seen: list[Any] = []
    turns = [
        [_call("WriteFile", "c1", file_path="tests/test_calc.py", content=CALC_TEST)],
        [_call("RunTests", "c2", test_path="tests")],
        [_call("WriteFile", "c3", file_path="calculator.py", content=CALC_IMPL)],
        [AIMessage(content="done")],
    ]
    asyncio.run(run_item("add", 0, {"session_id": "s"}, workspace=python_workspace,
                         deps=_scripted_deps(turns, seen)))

    phases = [s.phase_ledger.phase for s in seen]
    assert phases[0] == TddPhase.RED and phases[-1] == TddPhase.GREEN

    rosters = [tuple(t.name for t in s.tool_context.tools) for s in seen]
    # Identity is what the engine compares: unchanged phase, unchanged tuple.
    assert seen[0].tool_context.tools is seen[1].tool_context.tools
    assert "WriteFile" in rosters[-1]


def test_the_shared_test_venv_is_built_once_and_reused(tmp_path):
    # The integration tests' venv is reused across pytest sessions (and mutmut's forked
    # children), so fixture setup never hides inside a mutant's time budget.
    from tests.conftest import shared_test_venv

    first = shared_test_venv(tmp_path)
    marker = first / "built-once"
    marker.write_text("")
    assert shared_test_venv(tmp_path) == first and marker.exists()
    probe = subprocess.run([str(first / "bin" / "python"), "-c", "import pip, pytest, pytest_cov"], check=False)
    assert probe.returncode == 0
    assert not [p for p in tmp_path.iterdir() if "-building-" in p.name]
