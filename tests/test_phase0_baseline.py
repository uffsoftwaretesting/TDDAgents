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
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.loop.context import AppState, AppStateStore, tool_context_for
from app.loop.ledger import PhaseLedger, TddPhase
from app.loop.permissions.types import ToolPermissionContext
from app.loop.state import initial_loop_state
from app.session.export import EXPORT_EXCLUDES, export_run_workspace
from app.workspace import pyenv
from app.workspace.local import LocalWorkspace
from app.workspace.pyenv import (
    REQUIRED_PACKAGES,
    SESSION_VENV_DIRNAME,
    SessionPython,
    SessionPythonError,
    ensure_session_python,
)

REPO = Path(__file__).resolve().parent.parent


# ── session Python environment ───────────────────────────────────────────────

class FakeRunner:
    """Scripted subprocess.run: records argv, answers by the first matching rule."""

    def __init__(self, rules: list[tuple[str, Any]], create_python: bool = True) -> None:
        self.rules = rules
        self.calls: list[list[str]] = []
        self.create_python = create_python

    def __call__(self, argv: list[str], **kwargs: Any) -> Any:
        assert kwargs == {"capture_output": True, "text": True}
        self.calls.append(argv)
        if self.create_python and "venv" in argv:
            python = Path(argv[-1]) / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.write_text("")
        for needle, code in self.rules:
            if needle in " ".join(argv):
                if isinstance(code, tuple):
                    code = code[0] if len(self.calls) <= code[1] else code[2]
                return SimpleNamespace(returncode=code, stdout="out", stderr="boom" if code else "")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def test_required_packages_pin_matches_requirements():
    reqs = (REPO / "requirements.txt").read_text().split()
    assert REQUIRED_PACKAGES == ("pytest==9.0.1",)
    assert all(pkg in reqs for pkg in REQUIRED_PACKAGES)
    assert SESSION_VENV_DIRNAME == "venv"


def test_session_python_paths_and_env(tmp_path):
    sp = SessionPython(tmp_path / "venv")
    assert sp.bin_dir == tmp_path / "venv" / "bin"
    assert sp.python == tmp_path / "venv" / "bin" / "python"
    assert sp.env("/usr/bin") == {"PATH": f"{sp.bin_dir}{os.pathsep}/usr/bin", "VIRTUAL_ENV": str(sp.venv_dir)}
    assert sp.env("") == {"PATH": str(sp.bin_dir), "VIRTUAL_ENV": str(sp.venv_dir)}


def test_session_python_env_defaults_to_process_path(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", "/a:/b")
    assert SessionPython(tmp_path).env()["PATH"] == f"{tmp_path / 'bin'}{os.pathsep}/a:/b"


def test_ensure_creates_then_provisions(tmp_path):
    # import probe fails once (before install), then succeeds
    runner = FakeRunner([("import pytest", (1, 2, 0))])
    sp = ensure_session_python(tmp_path / "run", creator="/py", runner=runner)
    assert sp.venv_dir == tmp_path / "run" / "venv"
    assert runner.calls[0] == ["/py", "-m", "venv", "--without-pip", str(sp.venv_dir)]
    assert runner.calls[1] == [str(sp.python), "-c", "import pytest"]
    assert runner.calls[2] == ["/py", "-m", "pip", "--python", str(sp.python), "install", "--quiet",
                               *REQUIRED_PACKAGES]
    assert runner.calls[3] == [str(sp.python), "-c", "import pytest"]


def test_ensure_is_idempotent(tmp_path):
    (tmp_path / "venv" / "bin").mkdir(parents=True)
    (tmp_path / "venv" / "bin" / "python").write_text("")
    runner = FakeRunner([])
    ensure_session_python(tmp_path, creator="/py", runner=runner)
    assert runner.calls == [[str(tmp_path / "venv" / "bin" / "python"), "-c", "import pytest"]]


@pytest.mark.parametrize("failing, message", [
    ("--without-pip", "could not create the session venv: boom"),
    ("install", "could not install the test toolchain into the session venv: boom"),
])
def test_ensure_reports_provisioning_failures(tmp_path, failing, message):
    runner = FakeRunner([(failing, 1), ("import pytest", 1)], create_python=failing != "--without-pip")
    with pytest.raises(SessionPythonError, match=f"^{message}$"):
        ensure_session_python(tmp_path, creator="/py", runner=runner)


def test_ensure_fails_when_install_does_not_take(tmp_path):
    runner = FakeRunner([("import pytest", 1)])
    with pytest.raises(SessionPythonError, match="still not importable"):
        ensure_session_python(tmp_path, creator="/py", runner=runner)


def test_failure_without_output_reports_exit_code(tmp_path):
    def runner(argv, **kw):
        return SimpleNamespace(returncode=7, stdout="", stderr="")

    with pytest.raises(SessionPythonError, match="exit 7"):
        ensure_session_python(tmp_path, creator="/py", runner=runner)


def test_session_python_is_real_and_isolated(session_python):
    out = subprocess.run([str(session_python.python), "-c", "import pytest, sys; print(pytest.__version__);"
                          "print(sys.prefix)"], capture_output=True, text=True, check=True).stdout.split()
    assert out[0] == "9.0.1"
    assert out[1] == str(session_python.venv_dir)
    # TDDAgents' own dependencies are not visible to generated code
    probe = subprocess.run([str(session_python.python), "-c", "import langchain_core"], capture_output=True)
    assert probe.returncode != 0
    assert pyenv.has_packages(session_python.python, subprocess.run) is True


# ── workspace environment ────────────────────────────────────────────────────

def test_workspace_env_is_merged_under_per_call_env(tmp_path):
    ws = LocalWorkspace(str(tmp_path / "w"), env={"A": "ws", "B": "ws"})
    out = ws.execute('echo "$A $B $HOME"', env={"B": "call"}).stdout.split()
    assert out[:2] == ["ws", "call"]
    assert out[2] == os.environ["HOME"]
    assert LocalWorkspace(str(tmp_path / "v")).execute('echo "${A:-unset}"').stdout.strip() == "unset"


def test_workspace_run_dir_and_for_run_env(tmp_path, monkeypatch):
    from app.config.config import Config

    monkeypatch.setattr(Config, "LOCAL_WORKSPACE_ROOT", str(tmp_path / "runs"))
    ws = LocalWorkspace.for_run("thread-1", env={"X": "1"})
    assert ws.root == (tmp_path / "runs" / "thread-1" / "workspace").resolve()
    assert ws.run_dir == ws.root.parent
    assert ws.execute('echo "$X"').stdout.strip() == "1"


def test_python_workspace_runs_the_session_interpreter(python_workspace, session_python):
    assert python_workspace.execute("python -c 'import sys; print(sys.prefix)'").stdout.strip() == str(
        session_python.venv_dir)
    assert python_workspace.execute("python -m pytest --version").exit_code == 0


# ── export ───────────────────────────────────────────────────────────────────

def test_export_copies_the_tree_without_scratch(tmp_path):
    src = tmp_path / "src"
    for rel in ("calc.py", "tests/test_calc.py", ".tddagents/tool-results/x", "__pycache__/c.pyc",
                "pkg/__pycache__/m.pyc", ".pytest_cache/v", ".git/HEAD", "made_by_bash.txt"):
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text(rel)
    dst = tmp_path / "out"
    (dst / "metrics_and_logging").mkdir(parents=True)
    exported = export_run_workspace(src, dst)
    assert exported == ["calc.py", "made_by_bash.txt", "tests/test_calc.py"]
    assert (dst / "tests" / "test_calc.py").read_text() == "tests/test_calc.py"
    assert (dst / "metrics_and_logging").is_dir()
    for excluded in EXPORT_EXCLUDES:
        assert not (dst / excluded).exists()
    assert not (dst / "pkg" / "__pycache__").exists()


def test_export_missing_source_exports_nothing(tmp_path):
    assert export_run_workspace(tmp_path / "nope", tmp_path / "out") == []
    assert not (tmp_path / "out").exists()


def test_export_excludes_constant():
    assert EXPORT_EXCLUDES == (".tddagents", "__pycache__", ".pytest_cache", ".mypy_cache", ".git")


# ── entry point health and the optional E2B key ──────────────────────────────

def _clean_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("E2B_API_KEY", "OPENAI_API_KEY", "POSTGRES_URL")}
    env.update(PYTHONPATH=str(REPO), **extra)
    return env


def test_app_main_imports_without_an_e2b_key(tmp_path):
    """The import chain app.main -> orchestrator -> runner -> factory was broken (ImportError)."""
    env = _clean_env(OPENAI_API_KEY="k", POSTGRES_URL="postgresql://x")
    res = subprocess.run([sys.executable, "-c", "import app.main, app.loop.factory, app.loop.runner"],
                         cwd=tmp_path, env=env, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_production_deps_are_complete():
    from app.loop.factory import get_production_deps
    from app.loop.model import stream_call_model
    from app.loop.tools.orchestration import run_tools

    deps = get_production_deps()
    assert deps.call_model is stream_call_model
    assert deps.run_tools is run_tools
    assert isinstance(deps.uuid(), str) and len(deps.uuid()) == 36
    assert deps.uuid() != deps.uuid()
    assert deps.now() > 1_700_000_000
    deps.emit_event(object())


def test_openai_and_postgres_stay_required(tmp_path):
    res = subprocess.run([sys.executable, "-c", "import app.config.config"], cwd=tmp_path,
                         env=_clean_env(POSTGRES_URL="postgresql://x"), capture_output=True, text=True)
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

    A set, not a list: under mutmut a module carries one copy of each function per mutant.
    """
    hits = set()
    for path in sorted((REPO / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "set_app_state" and "phase_ledger" in ast.unparse(node)):
                hits.add(path.relative_to(REPO).as_posix())
    return sorted(hits)


def test_run_tests_is_the_only_ledger_writer():
    assert _ledger_writers() == ["app/loop/tools/run_tests.py"]


def test_initial_state_reads_the_store_and_never_writes_it():
    writes: list[Any] = []
    store = AppStateStore(AppState(phase_ledger=PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True)))
    ctx = tool_context_for(store)
    ctx = dataclasses.replace(ctx, set_app_state=writes.append)
    state = initial_loop_state((), ctx)
    assert state.phase_ledger == PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True)
    assert writes == []


def test_engine_does_not_write_a_stale_state_ledger_back():
    """A LoopState ledger that differs from the store must not overwrite the store."""
    from app.loop.config import build_run_config
    from app.loop.deps import CompactionResult, LoopDeps, StopHookResult
    from app.loop.engine import drain, run_loop

    store = AppStateStore()
    state = dataclasses.replace(initial_loop_state((), tool_context_for(store)),
                                phase_ledger=PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True,
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
    asyncio.run(drain(run_loop(state, build_run_config("r", postgres_checkpointing=False), deps)))
    assert store.get().phase_ledger == PhaseLedger()


# ── the production runner, end to end with a scripted model ──────────────────

def _scripted_deps(turns: list[list[AIMessage]], seen: list[Any] | None = None) -> Any:
    from app.loop.factory import get_production_deps

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


def test_runner_drives_a_real_red_green_cycle(python_workspace):
    from app.loop.runner import tdd_loop_runner

    turns = [
        [_call("WriteFile", "c1", file_path="tests/test_calc.py", content=CALC_TEST)],
        [_call("RunTests", "c2", test_path="tests")],
        [_call("WriteFile", "c3", file_path="calculator.py", content=CALC_IMPL)],
        [_call("RunTests", "c4", test_path="tests")],
        [AIMessage(content="Red then green observed.")],
    ]
    seen: list[Any] = []
    result = asyncio.run(tdd_loop_runner("add two numbers", 0, {"session_id": "s"},
                                         workspace=python_workspace, deps=_scripted_deps(turns, seen)))
    assert (result.status, result.terminal_reason) == ("success", "completed")
    assert (result.red_confirmed, result.green_passed) == (True, True)
    assert result.error_message is None and result.sub_requirement == "add two numbers"
    assert python_workspace.read_file("calculator.py") == CALC_IMPL
    ctx = seen[0].tool_context
    assert ctx.workspace is python_workspace
    assert ctx.permission_context == ToolPermissionContext(should_avoid_permission_prompts=True)
    assert {t.name for t in ctx.tools} == {"ReadFile", "WriteFile", "Edit", "Glob", "Grep", "Bash", "RunTests"}
    assert seen[0].messages[0].content == "add two numbers"
    assert seen[0].turn_count == 1


def test_runner_blocks_production_writes_in_red(python_workspace):
    """Barrier 2 at the runtime gate: the first write to production code in RED is refused."""
    from app.loop.runner import tdd_loop_runner

    turns = [
        [_call("WriteFile", "c1", file_path="calculator.py", content=CALC_IMPL)],
        [AIMessage(content="giving up")],
    ]
    seen: list[Any] = []
    result = asyncio.run(tdd_loop_runner("x", 3, {"session_id": "s"}, workspace=python_workspace,
                                         deps=_scripted_deps(turns, seen)))
    assert not python_workspace.exists("calculator.py")
    refusal = [m for m in seen[1].messages if getattr(m, "tool_call_id", None) == "c1"][0]
    assert "TDD phase RED denies" in str(refusal.content)
    assert (result.index, result.red_confirmed, result.green_passed) == (3, False, False)


def test_runner_reports_failure_without_inventing_progress(python_workspace):
    from app.loop.runner import tdd_loop_runner

    async def crashing(state: Any, config: Any) -> AsyncIterator[Any]:
        raise RuntimeError("provider down")
        yield  # pragma: no cover

    from app.loop.factory import get_production_deps

    deps = dataclasses.replace(get_production_deps(), call_model=crashing)
    result = asyncio.run(tdd_loop_runner("x", 1, {"session_id": "s"}, workspace=python_workspace, deps=deps))
    assert (result.status, result.terminal_reason) == ("failed", "model_error")
    assert result.error_message == "Terminated early: model_error"
    assert (result.red_confirmed, result.green_passed) == (False, False)


def test_runner_crash_is_a_model_error(python_workspace, monkeypatch):
    from app.loop import runner as runner_mod

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(runner_mod, "run_loop", boom)
    result = asyncio.run(runner_mod.tdd_loop_runner("x", 0, {"session_id": "s"}, workspace=python_workspace,
                                                    deps=_scripted_deps([])))
    assert result.terminal_reason == "model_error" and result.status == "failed"


def test_runner_writes_the_transcript_beside_the_workspace(python_workspace):
    from app.loop.runner import tdd_loop_runner

    asyncio.run(tdd_loop_runner("x", 0, {"session_id": "s"}, workspace=python_workspace,
                                deps=_scripted_deps([[AIMessage(content="done")]])))
    transcripts = list((python_workspace.run_dir / ".tddagents" / "transcripts").glob("transcript_loop-*.jsonl"))
    assert len(transcripts) == 1
    assert not (python_workspace.root / ".tddagents").exists()


def _recording_deps(seen: list[Any]) -> Any:
    from app.loop.factory import get_production_deps

    async def model(state: Any, config: Any) -> AsyncIterator[Any]:
        seen.append((state, config))
        yield AIMessage(content="done")

    return dataclasses.replace(get_production_deps(), call_model=model)


def test_runner_hands_the_loop_its_run_config_and_context(python_workspace):
    import re

    from app.loop.runner import tdd_loop_runner

    seen: list[Any] = []
    asyncio.run(tdd_loop_runner("spec", 0, {"session_id": "s"}, workspace=python_workspace,
                                deps=_recording_deps(seen)))
    state, config = seen[0]
    assert re.fullmatch(r"loop-[0-9a-f]{8}", config.run_id)
    assert config.gates.postgres_checkpointing is False
    [transcript] = (python_workspace.run_dir / ".tddagents" / "transcripts").glob("*.jsonl")
    assert transcript.name == f"transcript_{config.run_id}.jsonl"
    assert [m.content for m in state.tool_context.messages] == ["spec"]


def test_runner_defaults_to_the_session_workspace_and_production_deps(monkeypatch, python_workspace):
    from app.loop import factory
    from app.loop import runner as runner_mod

    asked: list[Any] = []

    def fake_session_workspace(thread_id: str) -> Any:
        asked.append(thread_id)
        return python_workspace

    seen: list[Any] = []
    monkeypatch.setattr(runner_mod, "session_workspace", fake_session_workspace)
    deps = _recording_deps(seen)
    monkeypatch.setattr(factory, "get_production_deps", lambda: deps)
    result = asyncio.run(runner_mod.tdd_loop_runner("spec", 0, {"session_id": "abc"}))
    assert asked == ["abc"]
    assert seen[0][0].tool_context.workspace is python_workspace
    assert result.terminal_reason == "completed"


def test_runner_tolerates_a_workspace_without_a_run_dir(monkeypatch, make_fake_workspace, tmp_path):
    from app.loop import transcript as transcript_mod
    from app.loop.runner import tdd_loop_runner

    bases: list[Any] = []
    real = transcript_mod.TranscriptLogger

    def logger(run_id: str, base_dir: Any = None) -> Any:
        bases.append(base_dir)
        return real(run_id=run_id, base_dir=tmp_path)

    monkeypatch.setattr(transcript_mod, "TranscriptLogger", logger)
    ws = make_fake_workspace()
    assert not hasattr(ws, "run_dir")
    result = asyncio.run(tdd_loop_runner("x", 0, {"session_id": "s"}, workspace=ws, deps=_recording_deps([])))
    assert bases == [None]
    assert result.terminal_reason == "completed"


def test_runner_needs_a_session_id_for_its_default_workspace():
    from app.loop.runner import default_workspace

    with pytest.raises(ValueError, match="^tdd_loop_runner needs a session_id to locate the session workspace$"):
        default_workspace({})
    with pytest.raises(ValueError):
        default_workspace({"session_id": ""})


def test_session_workspace_wires_the_session_venv(tmp_path, monkeypatch):
    from app.config.config import Config
    from app.loop import runner as runner_mod

    monkeypatch.setattr(Config, "LOCAL_WORKSPACE_ROOT", str(tmp_path / "runs"))
    seen: list[Path] = []

    def fake_ensure(run_dir: Path) -> SessionPython:
        seen.append(Path(run_dir))
        return SessionPython(Path(run_dir) / "venv")

    monkeypatch.setattr("app.workspace.pyenv.ensure_session_python", fake_ensure)
    ws = runner_mod.session_workspace("t-9")
    assert seen == [(tmp_path / "runs" / "t-9").resolve()]
    assert ws.root == (tmp_path / "runs" / "t-9" / "workspace").resolve()
    assert ws.execute('echo "$VIRTUAL_ENV"').stdout.strip() == str(seen[0] / "venv")
    assert runner_mod.default_workspace({"session_id": "t-9"}).root == ws.root


def test_orchestrator_binds_the_session_workspace_into_the_runner(monkeypatch, tmp_path):
    import functools

    from app.graph import orchestrator as orch

    made: list[str] = []

    def fake_session_workspace(thread_id: str) -> LocalWorkspace:
        made.append(thread_id)
        return LocalWorkspace(str(tmp_path / thread_id))

    captured: dict[str, Any] = {}

    class FakeShell:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(orch, "session_workspace", fake_session_workspace)
    monkeypatch.setattr(orch, "SessionShell", FakeShell)
    monkeypatch.setattr(orch, "build_checkpointer", lambda: "ckpt")
    o = orch.TDDOrchestrator(task_key="tdd-abc")
    assert made == ["tdd-abc"]
    assert captured["checkpointer"] == "ckpt"
    runner = captured["loop_runner"]
    assert isinstance(runner, functools.partial)
    from app.loop.runner import tdd_loop_runner

    assert runner.func is tdd_loop_runner
    assert runner.keywords == {"workspace": o.workspace}


# ── edges the mutation run surfaced ──────────────────────────────────────────

def test_production_hooks_are_inert_passthroughs():
    from app.loop.factory import get_production_deps, production_compact, production_stop_hooks

    assert get_production_deps().compact is production_compact
    assert get_production_deps().stop_hooks is production_stop_hooks
    state = initial_loop_state((HumanMessage(content="hi"),), tool_context_for(AppStateStore()))
    compacted = asyncio.run(production_compact(state, None))  # type: ignore[arg-type]
    assert compacted.compacted is False
    assert compacted.messages == state.messages
    assert compacted.tracking is state.compaction_tracking
    assert asyncio.run(production_stop_hooks(state, None)).prevent_continuation is False  # type: ignore[arg-type]


def test_export_creates_missing_destination_parents(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("x")
    assert export_run_workspace(src, tmp_path / "deep" / "er" / "out") == ["a.py"]
    assert (tmp_path / "deep" / "er" / "out" / "a.py").read_text() == "x"


def test_provisioning_failure_reports_stdout_when_stderr_is_empty(tmp_path):
    def runner(argv: list[str], **kw: Any) -> Any:
        return subprocess.CompletedProcess(argv, 3, stdout="  only stdout  ", stderr="")

    with pytest.raises(SessionPythonError, match="^could not create the session venv: only stdout$"):
        ensure_session_python(tmp_path, runner=runner)


def test_install_that_does_not_take_names_the_cause_exactly(tmp_path):
    def runner(argv: list[str], **kw: Any) -> Any:
        code = 1 if argv[1:3] == ["-c", "import pytest"] else 0
        return subprocess.CompletedProcess(argv, code, stdout="", stderr="")

    (tmp_path / "venv" / "bin").mkdir(parents=True)
    (tmp_path / "venv" / "bin" / "python").write_text("")
    with pytest.raises(SessionPythonError,
                       match="^pytest is still not importable in the session venv after installing it$"):
        ensure_session_python(tmp_path, runner=runner)


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
    from app.loop.tools.run_tests import _default_test_runner
    from app.workspace.base import CommandResult

    ws = make_fake_workspace()
    ws.execute = lambda cmd, timeout=None: CommandResult(exit_code=1, stdout="out", stderr="err", duration=0.0,
                                                         workspace="local")
    ctx = tool_context_for(AppStateStore(), workspace=ws)
    res = asyncio.run(_default_test_runner("tests", ctx))
    assert (res.exit_code, res.stdout, res.stderr) == (1, "out", "err")


def test_run_tests_rejects_a_runner_result_without_an_exit_code():
    from app.loop.tools.run_tests import RunTestsUnavailable, _default_test_runner

    ctx: Any = SimpleNamespace(test_runner=lambda path: object())
    with pytest.raises(RunTestsUnavailable, match="^the attached test runner returned no exit code$"):
        asyncio.run(_default_test_runner("tests", ctx))


def test_run_tests_tool_is_serial():
    from app.loop.tools.run_tests import build_run_tests_tool

    assert build_run_tests_tool().is_concurrency_safe({}) is False
