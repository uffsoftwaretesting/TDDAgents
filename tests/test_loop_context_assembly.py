"""
`app/loop/context/assembly.py`: ports of `getSystemPrompt`, `computeSimpleEnvInfo`,
`getGitStatus`, `getSystemContext`, `getUserContext`, `appendSystemContext` and
`prependUserContext`. Git and uname go through a scripted workspace, plus one real repo.
"""

import datetime
import subprocess
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.loop.context import assembly as a
from app.loop.context.assembly import (
    STATIC_SECTION_FILES,
    append_system_context,
    compute_simple_env_info,
    get_git_status,
    get_session_context,
    get_system_context,
    get_system_prompt,
    get_user_context,
    prepend_user_context,
    reset_session_context,
)
from app.loop.context.cleanup import invalidate_context_caches
from app.loop.prompts.sections import (
    SYSTEM_PROMPT_DYNAMIC_BOUNDARY,
    resolve_system_prompt_sections,
    split_system_prompt_sections,
)
from app.workspace.base import CommandResult
from app.workspace.local import LocalWorkspace
from tests.conftest import FakeWorkspace


def ok(out="", code=0):
    return CommandResult(stdout=out, stderr="", exit_code=code, duration=0.0, workspace="sandbox")


class Shell(FakeWorkspace):
    """Answers commands from a dict; unknown commands fail."""

    def __init__(self, answers=None, raise_on=()):
        super().__init__()
        self.answers = answers or {}
        self.raise_on = set(raise_on)

    def execute(self, cmd, timeout=None, env=None):
        self.command_log.append(cmd)
        if cmd in self.raise_on:
            raise RuntimeError("sandbox down")
        out = self.answers.get(cmd)
        if out is None:
            return ok(code=1)
        return out if isinstance(out, CommandResult) else ok(out)


REPO = {
    "git rev-parse --is-inside-work-tree": "true\n",
    "git symbolic-ref --quiet --short HEAD": "feature\n",
    "git symbolic-ref --quiet --short refs/remotes/origin/HEAD": "origin/trunk\n",
    "git --no-optional-locks status --short": " M a.py\n",
    "git --no-optional-locks log --oneline -n 5": "abc first\n",
    "git config user.name": "Dev\n",
}


@pytest.fixture(autouse=True)
def fresh_sessions():
    reset_session_context()
    yield
    reset_session_context()


# ── system prompt ────────────────────────────────────────────────────────────

def test_static_sections_follow_upstream_order_and_include_tdd_contract():
    assert STATIC_SECTION_FILES == (
        ("intro", "identity.md"), ("system", "system.md"), ("doing_tasks", "doing-tasks.md"),
        ("actions", "actions-with-care.md"), ("using_your_tools", "tools.md"),
        ("tone_and_style", "tone.md"), ("tdd_contract", "tdd-contract.md"),
    )
    for _, filename in STATIC_SECTION_FILES:
        assert a.read_system_prompt_file(filename).strip(), filename


def test_get_system_prompt_structure():
    sections = get_system_prompt(Shell({"uname -s": "Linux", "uname -sr": "Linux 6"}), "m-1")
    names = [s.name for s in sections]
    assert names == [n for n, _ in STATIC_SECTION_FILES] + [SYSTEM_PROMPT_DYNAMIC_BOUNDARY.name, "env_info_simple"]
    static, dynamic = split_system_prompt_sections(sections)
    assert len(static) == 7 and [s.name for s in dynamic] == ["env_info_simple"]
    rendered = resolve_system_prompt_sections(sections)
    assert rendered[0] == a.read_system_prompt_file("identity.md").strip()
    assert rendered[6] == a.read_system_prompt_file("tdd-contract.md").strip()
    assert rendered[-1].startswith("# Environment")


def test_read_system_prompt_file_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(a, "get_base_prompts_dir", lambda: tmp_path)
    assert a.read_system_prompt_file("identity.md") == ""
    (tmp_path / "system").mkdir()
    (tmp_path / "system" / "identity.md").write_text("me", encoding="utf-8")
    assert a.read_system_prompt_file("identity.md") == "me"


def test_compute_simple_env_info():
    ws = Shell({"git rev-parse --is-inside-work-tree": "true", "uname -s": "Linux\n", "uname -sr": "Linux 6.1\n"})
    assert compute_simple_env_info(ws, "o4-mini") == (
        "# Environment\nYou have been invoked in the following environment: \n"
        " - Primary working directory: .\n"
        "  - Is a git repository: true\n"
        " - Platform: linux\n"
        " - Shell: bash\n"
        " - OS Version: Linux 6.1\n"
        " - You are powered by the model o4-mini."
    )


def test_compute_simple_env_info_fallbacks(tmp_path):
    out = compute_simple_env_info(None, "m")
    assert " - Primary working directory: .\n  - Is a git repository: false\n - Platform: unknown\n" in out
    assert " - OS Version: unknown\n" in out
    local = LocalWorkspace(str(tmp_path))
    out = compute_simple_env_info(local, "m")
    assert f" - Primary working directory: {local.root}\n" in out
    assert "  - Is a git repository: false\n" in out


def test_prepend_bullets():
    assert a.prepend_bullets(["a", ["b", "c"], "d"]) == [" - a", "  - b", "  - c", " - d"]


def test_run_swallows_infrastructure_errors():
    assert a._run(Shell(raise_on={"x"}), "x") == (1, "")
    assert a._run(Shell({"y": "  out \n"}), "y") == (0, "out")


def test_is_git_repo_requires_true():
    assert a.is_git_repo(Shell({"git rev-parse --is-inside-work-tree": "true"})) is True
    assert a.is_git_repo(Shell({"git rev-parse --is-inside-work-tree": "false"})) is False
    assert a.is_git_repo(Shell({"git rev-parse --is-inside-work-tree": ok("true", code=128)})) is False


# ── git status / system context ──────────────────────────────────────────────

def test_get_git_status_full():
    assert get_git_status(Shell(REPO)) == (
        "This is the git status at the start of the conversation. Note that this status is a snapshot in time, "
        "and will not update during the conversation.\n\n"
        "Current branch: feature\n\n"
        "Main branch (you will usually use this for PRs): trunk\n\n"
        "Git user: Dev\n\n"
        "Status:\nM a.py\n\n"
        "Recent commits:\nabc first"
    )


def status_of(ws) -> str:
    out = get_git_status(ws)
    assert out is not None
    return out


def test_get_git_status_clean_no_user_and_default_branch_fallbacks():
    answers = dict(REPO)
    answers.pop("git config user.name")
    answers["git --no-optional-locks status --short"] = ""
    answers.pop("git symbolic-ref --quiet --short refs/remotes/origin/HEAD")
    answers["git show-ref --verify --quiet refs/remotes/origin/master"] = ""
    out = status_of(Shell(answers))
    assert "Git user" not in out
    assert "Status:\n(clean)" in out
    assert "Main branch (you will usually use this for PRs): master" in out


def test_branch_resolution():
    assert a.get_branch(Shell({"git symbolic-ref --quiet --short HEAD": "dev"})) == "dev"
    assert a.get_branch(Shell({})) == "HEAD"
    assert a.get_branch(Shell({"git symbolic-ref --quiet --short HEAD": ""})) == "HEAD"


def test_default_branch_resolution():
    assert a.get_default_branch(Shell({})) == "main"
    assert a.get_default_branch(Shell({"git show-ref --verify --quiet refs/remotes/origin/main": ""})) == "main"
    assert a.get_default_branch(Shell({"git symbolic-ref --quiet --short refs/remotes/origin/HEAD": "weird"})) == "main"


def test_get_git_status_truncates_long_status():
    answers = dict(REPO)
    answers["git --no-optional-locks status --short"] = "x" * (a.MAX_STATUS_CHARS + 1)
    out = status_of(Shell(answers))
    assert ("Status:\n" + "x" * a.MAX_STATUS_CHARS + "\n... (truncated because it exceeds 2k characters. "
            'If you need more information, run "git status" using BashTool)') in out
    answers["git --no-optional-locks status --short"] = "y" * a.MAX_STATUS_CHARS
    assert "truncated" not in status_of(Shell(answers))


def test_get_git_status_not_a_repo():
    assert get_git_status(None) is None
    assert get_git_status(Shell({})) is None
    assert get_system_context(Shell({})) == {}
    assert get_system_context(Shell(REPO))["gitStatus"].startswith("This is the git status")


def test_git_status_on_real_repo(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    (tmp_path / "a.txt").write_text("x")
    out = status_of(LocalWorkspace(str(tmp_path)))
    assert "Current branch: main" in out
    assert "?? a.txt" in out


# ── user context ─────────────────────────────────────────────────────────────

def test_get_user_context_with_memory_and_date(tmp_path):
    ws = FakeWorkspace(files={"TDDAGENTS.md": "rules"})
    ctx = get_user_context(ws, today=lambda: datetime.date(2026, 10, 5), env={}, home=str(tmp_path))
    assert list(ctx) == ["claudeMd", "currentDate"]
    assert ctx["claudeMd"].endswith(
        "Contents of TDDAGENTS.md (project instructions, checked into the codebase):\n\nrules")
    assert ctx["currentDate"] == "Today's date is 2026-10-05."


def test_get_user_context_without_memory_or_disabled(tmp_path):
    ws = FakeWorkspace(files={"TDDAGENTS.md": "rules"})
    ctx = get_user_context(ws, env={"TDDAGENTS_DISABLE_MEMORY_FILES": "1"}, home=str(tmp_path))
    assert list(ctx) == ["currentDate"]
    assert ctx["currentDate"] == f"Today's date is {datetime.date.today().isoformat()}."
    assert list(get_user_context(FakeWorkspace(), env={}, home=str(tmp_path))) == ["currentDate"]


def test_get_user_context_reads_process_env_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("TDDAGENTS_DISABLE_MEMORY_FILES", "true")
    assert "claudeMd" not in get_user_context(FakeWorkspace(files={"TDDAGENTS.md": "r"}), home=str(tmp_path))


# ── append / prepend ─────────────────────────────────────────────────────────

def test_append_system_context():
    assert append_system_context(["a", "b"], {"gitStatus": "s", "x": "y"}) == ["a", "b", "gitStatus: s\nx: y"]
    assert append_system_context(["a", ""], {}) == ["a"]


def test_prepend_user_context():
    history = [HumanMessage(content="hi"), AIMessage(content="yo")]
    out = prepend_user_context(history, {"claudeMd": "M", "currentDate": "D"})
    assert out[1:] == history
    assert out[0].additional_kwargs == {"is_meta": True}
    assert out[0].content == (
        "<system-reminder>\nAs you answer the user's questions, you can use the following context:\n"
        "# claudeMd\nM\n# currentDate\nD\n\n      IMPORTANT: this context may or may not be relevant to your "
        "tasks. You should not respond to this context unless it is highly relevant to your task.\n"
        "</system-reminder>\n"
    )
    assert prepend_user_context(history, {}) == history


# ── session memoization ──────────────────────────────────────────────────────

def test_session_context_is_computed_once_per_run(monkeypatch):
    calls = []

    def user(ws):
        calls.append("u")
        return {"u": "1"}

    def system(ws):
        calls.append("s")
        return {"s": "1"}

    monkeypatch.setattr(a, "get_user_context", user)
    monkeypatch.setattr(a, "get_system_context", system)
    first = get_session_context("run-1", None)
    assert get_session_context("run-1", None) is first
    assert (first.user_context, first.system_context) == ({"u": "1"}, {"s": "1"})
    assert get_session_context("run-2", None) is not first
    assert calls == ["u", "s", "u", "s"]


def test_reset_session_context(monkeypatch):
    monkeypatch.setattr(a, "get_user_context", lambda ws: {})
    monkeypatch.setattr(a, "get_system_context", lambda ws: {})
    one, two = get_session_context("r1", None), get_session_context("r2", None)
    reset_session_context("r1")
    assert get_session_context("r1", None) is not one
    assert get_session_context("r2", None) is two
    reset_session_context("missing")
    reset_session_context()
    assert get_session_context("r2", None) is not two


def test_invalidate_context_caches_clears_session(monkeypatch):
    monkeypatch.setattr(a, "get_user_context", lambda ws: {})
    monkeypatch.setattr(a, "get_system_context", lambda ws: {})
    first = get_session_context("r", None)
    invalidate_context_caches()
    assert get_session_context("r", None) is first
    invalidate_context_caches(run_id="r")
    assert get_session_context("r", None) is not first


def test_invalidate_one_run_keeps_the_others(monkeypatch):
    monkeypatch.setattr(a, "get_user_context", lambda ws: {})
    monkeypatch.setattr(a, "get_system_context", lambda ws: {})
    kept = get_session_context("other", None)
    get_session_context("r", None)
    invalidate_context_caches(run_id="r")
    assert get_session_context("other", None) is kept


def test_default_branch_prefers_main_over_master():
    both = {"git show-ref --verify --quiet refs/remotes/origin/main": "",
            "git show-ref --verify --quiet refs/remotes/origin/master": ""}
    assert a.get_default_branch(Shell(both)) == "main"


def test_user_context_passes_env_and_home(tmp_path, monkeypatch):
    monkeypatch.delenv("TDDAGENTS_ENABLE_AUTO_MEMORY", raising=False)
    from app.loop.context.memory import get_auto_mem_entrypoint

    entry = Path(get_auto_mem_entrypoint("sandbox", str(tmp_path)))
    entry.parent.mkdir(parents=True)
    entry.write_text("remembered", encoding="utf-8")
    (tmp_path / ".tddagents").mkdir(exist_ok=True)
    (tmp_path / ".tddagents" / "TDDAGENTS.md").write_text("user rules", encoding="utf-8")
    ctx = get_user_context(FakeWorkspace(), env={"TDDAGENTS_ENABLE_AUTO_MEMORY": "1"}, home=str(tmp_path))
    assert "remembered" in ctx["claudeMd"]
    assert "user rules" in ctx["claudeMd"]


def test_session_context_uses_the_workspace_for_system_context():
    session = get_session_context("git-run", Shell(REPO))
    assert session.system_context["gitStatus"].startswith("This is the git status")
