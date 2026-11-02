"""
Shared fixtures for the Phase 1A substrate tests.

These tests are offline by design: no E2B credentials, no sandbox, no LLM, no Postgres.
They cover LocalWorkspace directly and exercise the sync engine against an in-memory
FakeWorkspace, so the whole suite runs in seconds and can be part of an ordinary edit
loop. The live E2B half of the parity check belongs to the Phase 1B harness.

`app/config/config.py` raises at import time when the three API keys are unset, so the
environment is seeded before any `app.*` import happens.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("E2B_API_KEY", "test-e2b-key")

# Allows `pytest tests/` from the repo root without an installed package.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest  # noqa: E402

LIVE_ENV_FLAG = "RUN_LIVE_LLM_TESTS"


def pytest_configure(config: Any) -> None:
    config.addinivalue_line(
        "markers", f"live: calls a real provider; runs only with {LIVE_ENV_FLAG}=1 and real keys in .env"
    )


def pytest_collection_modifyitems(config: Any, items: list[Any]) -> None:
    """Live tests never run by accident: a key in .env is not consent to spend it."""
    if os.environ.get(LIVE_ENV_FLAG) == "1":
        return
    skip = pytest.mark.skip(reason=f"live test; set {LIVE_ENV_FLAG}=1 with real keys in .env")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _isolated_config_home(tmp_path_factory: Any, monkeypatch: Any) -> None:
    """
    Transcripts, events and tool results live under the config home
    (`<config>/projects/...`, as upstream's `~/.claude/projects`). No test may write to the
    real `~/.tddagents`; a test that needs a specific home sets `TDDAGENTS_CONFIG_DIR` itself.
    """
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path_factory.mktemp("config-home")))


@pytest.fixture(autouse=True)
def _offline_token_counts(monkeypatch: Any) -> None:
    """
    The suite is offline: the provider's token-count endpoint (`app/services/token_estimation.py`)
    gives no count, so `/context` and `get_context_usage` fall back to the rough estimate. A test
    of the counting itself passes its own counter.
    """

    async def no_count(*args: Any) -> None:
        return None

    monkeypatch.setattr("app.services.token_estimation._provider_count", no_count)


@pytest.fixture(autouse=True)
def _trusted_session() -> Any:
    """
    Upstream skips its setup screens under test (`showSetupScreens`: `"production" === 'test'`):
    every test's process trusts its folder (`sessionTrustAccepted`), so an interactive session opens
    without the trust dialog. A test of the dialog turns it off (`set_session_trust_accepted(False)`).
    """
    from app.bootstrap.state import set_session_trust_accepted
    from app.utils.config import reset_trust_dialog_accepted_cache_for_testing

    set_session_trust_accepted(True)
    reset_trust_dialog_accepted_cache_for_testing()
    yield
    set_session_trust_accepted(False)
    reset_trust_dialog_accepted_cache_for_testing()


@pytest.fixture(autouse=True)
def _isolated_bundled_skills_root(tmp_path_factory: Any, monkeypatch: Any) -> Any:
    """
    Launching a bundled skill extracts its files under the per-process bundled-skills root
    (`app/skills/bundled_skills.py`). No test may write to the real `/tmp/tddagents-<uid>`: each
    gets its own temp dir and a fresh root, as a new process would.
    """
    from app.skills.bundled_skills import clear_extracted_bundled_skills
    from app.utils.permissions.filesystem import get_bundled_skills_root

    monkeypatch.setenv("TDDAGENTS_TMPDIR", str(tmp_path_factory.mktemp("tddagents-tmp")))
    get_bundled_skills_root.cache_clear()
    clear_extracted_bundled_skills()
    yield
    get_bundled_skills_root.cache_clear()
    clear_extracted_bundled_skills()


def read_dotenv_key(name: str) -> str | None:
    """A real credential from the repo's .env (the conftest placeholders do not count)."""
    env_path = REPO_ROOT / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{name}="):
                value = line.split("=", 1)[1].strip().strip("'\"")
                if value and not value.startswith("test-"):
                    return value
    return None


from app.workspace.base import (  # noqa: E402
    CommandResult,
    FileEntry,
    WorkspaceKind,
    WorkspaceNotFound,
    normalize_path,
)
from app.workspace.local import LocalWorkspace  # noqa: E402


class FakeWorkspace:
    """
    An in-memory Workspace, used as the sandbox side in sync tests.

    It implements the protocol faithfully enough that the engine cannot tell the
    difference, and it lets a test assert on exactly which operations ran — including
    failing one on demand, which is how the mid-flush abort case is exercised.
    """

    def __init__(
        self, kind: WorkspaceKind = "sandbox", files: dict[str, str] | None = None
    ) -> None:
        self.kind: WorkspaceKind = kind
        self.files: dict[str, str] = dict(files or {})
        self.command_log: list[str] = []
        self.command_results: dict[str, CommandResult] = {}
        self.fail_write_on: set[str] = set()
        self.spawn_log: list[tuple[str, str | None]] = []
        self.spawn_hold: dict[str, asyncio.Event] = {}
        self.spawn_cwd: dict[str, str] = {}

    # ── Workspace protocol ───────────────────────────────────────────────────

    def read_file(self, path: str) -> str:
        key = normalize_path(path)
        if key not in self.files:
            raise WorkspaceNotFound(f"File not found: {path}")
        return self.files[key]

    def write_file(self, path: str, content: str) -> None:
        key = normalize_path(path)
        if key in self.fail_write_on:
            raise RuntimeError(f"Injected write failure for '{key}'")
        self.files[key] = content

    def delete_file(self, path: str) -> None:
        key = normalize_path(path)
        if key not in self.files:
            raise WorkspaceNotFound(f"File not found: {path}")
        del self.files[key]

    def list_files(self, path: str = ".", depth: int = 1) -> list[FileEntry]:
        prefix = "" if normalize_path(path) == "." else normalize_path(path) + "/"
        return [
            FileEntry(path=p, is_dir=False, size=len(c.encode("utf-8")))
            for p, c in sorted(self.files.items())
            if p.startswith(prefix)
        ]

    def exists(self, path: str) -> bool:
        return normalize_path(path) in self.files

    def move(self, old: str, new: str) -> None:
        self.files[normalize_path(new)] = self.read_file(old)
        del self.files[normalize_path(old)]

    def execute(self, cmd: str, timeout: float | None = None, env: dict | None = None) -> CommandResult:
        self.command_log.append(cmd)
        if cmd in self.command_results:
            return self.command_results[cmd]
        return CommandResult(stdout="", stderr="", exit_code=0, duration=0.0, workspace=self.kind)

    async def spawn(self, cmd: str, *, output_fd: int, cwd: str | None = None,
                    cwd_file: str | None = None) -> FakeShellProcess:
        """
        A scripted `spawn`: the command's `command_results` entry is its output (stdout, then
        stderr, into the one file) and exit code; a `spawn_hold` event keeps it running until
        set; a `spawn_cwd` entry is the directory it leaves the shell in (written to `cwd_file`).
        """
        self.command_log.append(cmd)
        self.spawn_log.append((cmd, cwd))
        empty = CommandResult(stdout="", stderr="", exit_code=0, duration=0.0, workspace=self.kind)
        result = self.command_results.get(cmd) or empty
        os.write(output_fd, (result.stdout + result.stderr).encode("utf-8"))
        if cwd_file is not None and cmd in self.spawn_cwd:
            Path(cwd_file).write_text(self.spawn_cwd[cmd] + "\n", encoding="utf-8")
        return FakeShellProcess(result.exit_code, self.spawn_hold.get(cmd))


class FakeShellProcess:
    """A scripted shell process: it exits with `code` at once, or when its hold is set or it is killed."""

    def __init__(self, code: int, hold: asyncio.Event | None = None) -> None:
        self.code = code
        self.hold = hold
        self.killed = False

    @property
    def pid(self) -> int | None:
        return None

    async def wait(self) -> int:
        if self.hold is not None:
            await self.hold.wait()
        return 137 if self.killed else self.code

    def kill_tree(self) -> None:
        self.killed = True
        if self.hold is not None:
            self.hold.set()


@pytest.fixture
def fake_sandbox() -> FakeWorkspace:
    return FakeWorkspace(kind="sandbox")


@pytest.fixture
def make_fake_workspace():
    """
    Factory for tests that need to control both sides — notably the mid-flush abort
    case, where the failure has to be injected on the local side and a real
    LocalWorkspace has no way to fail a write on command.
    """

    def _make(
        kind: WorkspaceKind = "sandbox", files: dict[str, str] | None = None
    ) -> FakeWorkspace:
        return FakeWorkspace(kind=kind, files=files)

    return _make


@pytest.fixture
def local_ws(tmp_path: Path) -> LocalWorkspace:
    return LocalWorkspace(tmp_path / "workspace")


@pytest.fixture
def baseline_path(tmp_path: Path) -> Path:
    return tmp_path / "baseline.json"


def shared_test_venv(root: Path | None = None) -> Path:
    """
    The real venv (pytest installed) every integration test runs generated code in, built once
    per interpreter and package set and then reused by every pytest session.

    mutmut runs each mutant as a new pytest session in a forked child, and its time budget
    counts only the tests' call phase, never fixture setup. A venv built per session was
    rebuilt per mutant, invisibly to that budget, so a loaded host turned mutants into false
    timeouts. It is built here with plain `venv` and `pip`, never through the mutated
    `ensure_session_python`, so no mutant can leave a broken shared venv behind; a file lock
    serialises concurrent children, and the venv is moved into place only once complete.
    """
    import fcntl
    import hashlib
    import shutil
    import subprocess
    import tempfile

    from app.workspace.pyenv import REQUIRED_PACKAGES

    key = hashlib.sha256(repr((sys.executable, sys.version, REQUIRED_PACKAGES)).encode()).hexdigest()[:16]
    base = root if root is not None else Path(tempfile.gettempdir())
    venv = base / f"tddagents-test-venv-{key}"
    base.mkdir(parents=True, exist_ok=True)
    with open(base / f"tddagents-test-venv-{key}.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not (venv / "bin" / "python").exists():
            building = Path(tempfile.mkdtemp(prefix=f"{venv.name}-building-", dir=base))
            try:
                subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(building)], check=True)
                subprocess.run([sys.executable, "-m", "pip", "--python", str(building / "bin" / "python"),
                                "install", "--quiet", *REQUIRED_PACKAGES], check=True)
                building.rename(venv)
            finally:
                shutil.rmtree(building, ignore_errors=True)
    return venv


@pytest.fixture(scope="session")
def session_python() -> Any:
    """One real session venv (pytest installed) shared by every integration test."""
    from app.workspace.pyenv import SessionPython

    return SessionPython(shared_test_venv())


@pytest.fixture
def python_workspace(tmp_path: Path, session_python: Any) -> LocalWorkspace:
    """
    A project as production makes one (Phase 13, `app/workspace/project.py`): a folder with
    TDDAgents' seeded settings whose commands run with the session venv activated by the session
    environment script. The venv itself is the shared test venv, not the project's own `.venv`.
    """
    from app.workspace.project import prepare_project

    env = session_python.write_env_file(tmp_path / "session-env.sh")
    return LocalWorkspace(str(prepare_project(tmp_path / "workspace")), env=env)


@pytest.fixture
def no_retry_sleep(monkeypatch: Any) -> list[float]:
    """`with_retry`'s backoff, recorded instead of slept: the delays in milliseconds."""
    delays: list[float] = []

    async def record(delay_ms: float) -> None:
        delays.append(delay_ms)

    monkeypatch.setattr("app.services.api.with_retry._default_sleep", record)
    return delays
