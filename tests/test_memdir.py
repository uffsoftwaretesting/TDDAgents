"""
`app/memdir/` (auto memory) and its wiring: ports of `memdir/*.ts`, the memory prompt section,
agent memory, the auto-memory carve-outs, the ReadFile staleness note and the relevance prefetch.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import app.memdir.memdir as md
from app.memdir import memory_types as mt
from app.memdir.find_relevant_memories import RelevantMemory, find_relevant_memories
from app.memdir.memory_age import (
    memory_age,
    memory_age_days,
    memory_freshness_note,
    memory_freshness_text,
)
from app.memdir.memory_scan import (
    MemoryHeader,
    format_memory_manifest,
    scan_memory_files,
)
from app.memdir.paths import (
    find_canonical_git_root,
    get_auto_mem_entrypoint,
    get_auto_mem_path,
    get_auto_mem_path_setting,
    is_auto_mem_path,
    is_auto_memory_enabled,
    validate_memory_path,
)
from app.memdir.team_mem_paths import (
    PathTraversalError,
    get_team_mem_entrypoint,
    get_team_mem_path,
    is_team_mem_file,
    is_team_memory_enabled,
    realpath_deepest_existing,
    sanitize_path_key,
    validate_team_mem_key,
    validate_team_mem_write_path,
)
from app.memdir.team_mem_prompts import build_combined_memory_prompt
from app.utils.settings.settings import SettingsLocations
from tests.conftest import REPO_ROOT

MEMDIR_TS = REPO_ROOT / "reference" / "claude-code" / "src" / "memdir"
needs_reference = pytest.mark.skipif(not MEMDIR_TS.is_dir(), reason="claude-code reference snapshot not present")
ON = {"TDDAGENTS_ENABLE_AUTO_MEMORY": "1"}
DAY = 86_400_000


def _cut(raw: str) -> str:
    return md.truncate_entrypoint_content(raw).content


def _ts_strings(text: str) -> list[str]:
    """Single- and double-quoted TypeScript string literals, unescaped."""
    out = []
    for single, double in re.findall(r"'((?:[^'\\\n]|\\.)*)'|\"((?:[^\"\\\n]|\\.)*)\"", text):
        out.append((single or double).replace("\\'", "'").replace('\\"', '"'))
    return out


# ── memoryTypes / memdir texts against the reference ─────────────────────────

@needs_reference
def test_the_type_sections_are_upstreams_verbatim():
    source = (MEMDIR_TS / "memoryTypes.ts").read_text()
    for name in ("TYPES_SECTION_COMBINED", "TYPES_SECTION_INDIVIDUAL", "WHAT_NOT_TO_SAVE_SECTION",
                 "TRUSTING_RECALL_SECTION"):
        block = source.split(f"export const {name}: readonly string[] = [", 1)[1].split("\n]\n", 1)[0]
        expected = [s.replace("CLAUDE.md", "TDDAGENTS.md")
                    for s in _ts_strings("\n".join(line for line in block.split("\n")
                                                   if not line.strip().startswith("//")))]
        assert list(getattr(mt, name)) == expected, name
    drift = _ts_strings(source.split("export const MEMORY_DRIFT_CAVEAT =", 1)[1].split("\n\n", 1)[0])[0]
    assert mt.MEMORY_DRIFT_CAVEAT == drift
    when = source.split("export const WHEN_TO_ACCESS_SECTION: readonly string[] = [", 1)[1].split("]", 1)[0]
    assert list(mt.WHEN_TO_ACCESS_SECTION) == [*_ts_strings(when), drift]
    assert mt.MEMORY_FRONTMATTER_EXAMPLE[4] == "type: {{user, feedback, project, reference}}"


@needs_reference
def test_every_prompt_line_of_memdir_and_team_prompts_is_upstreams():
    past = md.build_searching_past_context_section("/m/", "/p", {"TDDAGENTS_MEMORY_SEARCH_PAST_CONTEXT": "1"})
    lines = md.build_memory_lines("auto memory", "/m/", skip_index=False, past_context=past)
    lines += md.how_to_save_lines(True)
    combined = build_combined_memory_prompt("/m/", "/m/team/").split("\n") + md.how_to_save_lines(True, True)
    ours = "\n".join(lines + combined).replace("WriteFile tool", "Write tool")
    for file in ("memdir.ts", "teamMemPrompts.ts"):
        body = (MEMDIR_TS / file).read_text()
        if file == "memdir.ts":  # the KAIROS daily-log prompt is not ported
            start = body.index("function buildAssistantDailyLogPrompt")
            body = body[:start] + body[body.index("export function buildSearchingPastContextSection"):]
        code = "\n".join(line for line in body.split("\n") if not line.strip().startswith(("//", "*", "/*")))
        for literal in _ts_strings(code):
            if len(literal) > 40 and "${" not in literal and "/" not in literal[:2] and not literal.startswith("tengu"):
                assert literal in ours, literal


def test_parse_memory_type():
    assert [mt.parse_memory_type(v) for v in ("user", "feedback", "nope", 3, None)] == [
        "user", "feedback", None, None, None]


# ── truncation (moved from claudemd) ─────────────────────────────────────────

def test_entrypoint_constants_and_truncation_fields():
    assert (md.MAX_ENTRYPOINT_LINES, md.MAX_ENTRYPOINT_BYTES, md.ENTRYPOINT_NAME) == (200, 25_000, "MEMORY.md")
    short = md.truncate_entrypoint_content("  a\nb ")
    assert (short.content, short.line_count, short.byte_count, short.was_line_truncated, short.was_byte_truncated) == (
        "a\nb", 2, 3, False, False)
    both = md.truncate_entrypoint_content("\n".join(["y" * 200] * 300))
    assert (both.line_count, both.was_line_truncated, both.was_byte_truncated) == (300, True, True)


def test__cut():
    assert _cut("  short  ") == "short"
    lines = "\n".join(f"{i}" for i in range(201))
    out = _cut(lines)
    assert out.startswith("0\n1\n")
    assert "\n199\n\n> WARNING: MEMORY.md is 201 lines (limit: 200). Only part of it was loaded." in out
    long_line = "x" * 25_001
    out = _cut("a\n" + long_line)
    assert out.startswith("a\n\n> WARNING: MEMORY.md is 24.4KB (limit: 24.4KB) — index entries are too long.")
    out = _cut(long_line)
    assert out.startswith("x" * 25_000 + "\n\n> WARNING")
    both = "\n".join(["y" * 200] * 300)
    out = _cut(both)
    assert "is 300 lines and 58.9KB." in out
    exact = "z" * 25_000
    assert _cut(exact) == exact
    assert _cut("\n".join(["q"] * 200)) == "\n".join(["q"] * 200)


def test_truncation_counts_newline_lines_not_words():
    text = "\n".join(["x y z"] * 150)
    assert _cut(text) == text


def test_truncation_byte_cut_boundaries():
    limit = md.MAX_ENTRYPOINT_BYTES
    # after the line cut the content is exactly at the byte cap: no further cut
    head = "\n".join(["a" * 125] + ["b" * 124] * 199)
    assert len(head) == limit
    out = _cut(head + "\nextra")
    assert out.startswith(head + "\n\n> WARNING")
    # the cut is the LAST newline at or before the cap, never a later one
    text = "a\n" + "b" * 100 + "\n" + "c" * limit + "\nd"
    assert _cut(text).startswith("a\n" + "b" * 100 + "\n\n> WARNING")
    # a newline exactly at the cap counts; one just past it does not
    at_cap = "x" * limit + "\n" + "y" * 10
    assert _cut(at_cap).startswith("x" * limit + "\n\n> WARNING")
    past = "x" * (limit + 1) + "\n" + "y"
    assert _cut(past).startswith("x" * limit + "\n\n> WARNING")


# ── paths and enablement ─────────────────────────────────────────────────────

@pytest.mark.parametrize("env, settings, expected", [
    ({}, None, False),
    (ON, None, True),
    ({"TDDAGENTS_ENABLE_AUTO_MEMORY": "0"}, None, False),
    ({"TDDAGENTS_DISABLE_AUTO_MEMORY": "0"}, None, True),
    ({"TDDAGENTS_DISABLE_AUTO_MEMORY": "1", **ON}, None, False),
    ({}, {"autoMemoryEnabled": True}, True),
    ({}, {"autoMemoryEnabled": False}, False),
    ({}, {"autoMemoryEnabled": "yes"}, False),
    ({"TDDAGENTS_DISABLE_AUTO_MEMORY": "1"}, {"autoMemoryEnabled": True}, False),
])
def test_auto_memory_is_opt_in(env, settings, expected):
    assert is_auto_memory_enabled(env, settings) is expected


def test_auto_memory_reads_the_process_env(monkeypatch):
    monkeypatch.setenv("TDDAGENTS_ENABLE_AUTO_MEMORY", "1")
    assert is_auto_memory_enabled() is True
    monkeypatch.delenv("TDDAGENTS_ENABLE_AUTO_MEMORY")
    assert is_auto_memory_enabled() is False


def test_validate_memory_path(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert validate_memory_path(None, True) is None and validate_memory_path("", True) is None
    assert validate_memory_path("/srv/mem//", False) == "/srv/mem/"
    assert validate_memory_path("~/mem", True) == f"{tmp_path}/mem/"
    assert validate_memory_path("~/mem", False) is None  # relative once not expanded
    for bad in ("rel/dir", "/a", "//server/share", "/x\0y", "~/", "~/..", "~/."):
        assert validate_memory_path(bad, True) is None, bad


def test_the_auto_memory_directory_and_its_setting(tmp_path, monkeypatch):
    from app.utils.tool_result_storage import config_home

    project = tmp_path / "proj"
    project.mkdir()
    default = get_auto_mem_path(str(project))
    assert default == f"{config_home()}/projects/{re.sub('[^A-Za-z0-9]', '-', str(project))}/memory/"
    assert get_auto_mem_entrypoint(str(project)) == default + "MEMORY.md"
    assert is_auto_mem_path(default + "a.md", str(project)) and not is_auto_mem_path("/etc/a.md", str(project))
    home = tmp_path / "home"
    (home / ".tddagents").mkdir(parents=True)
    (home / ".tddagents" / "settings.json").write_text('{"autoMemoryDirectory": "/srv/memory"}')
    (project / ".tddagents").mkdir()
    (project / ".tddagents" / "settings.json").write_text('{"autoMemoryDirectory": "/etc/ssh"}')
    locations = SettingsLocations(project_root=project, home=home, managed_dir=tmp_path / "none")
    # The user's setting wins; the project's own settings never move the carve-out.
    assert get_auto_mem_path_setting(locations) == "/srv/memory/"
    assert get_auto_mem_path(str(project), locations) == "/srv/memory/"
    assert get_auto_mem_path_setting(None) is None


def test_find_canonical_git_root(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git" / "worktrees" / "wt").mkdir(parents=True)
    (repo / ".git" / "worktrees" / "wt" / "commondir").write_text("../..\n")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {repo / '.git' / 'worktrees' / 'wt'}\n")
    plain = tmp_path / "plain"
    plain.mkdir()
    odd = tmp_path / "odd"
    odd.mkdir()
    (odd / ".git").write_text("not a pointer")
    bare_pointer = tmp_path / "bare"
    (bare_pointer / "gd").mkdir(parents=True)
    (bare_pointer / ".git").write_text("gitdir: gd")
    assert find_canonical_git_root(str(repo)) == str(repo)
    assert find_canonical_git_root(str(worktree)) == str(repo)
    assert find_canonical_git_root(str(plain)) is None and find_canonical_git_root(str(odd)) is None
    assert find_canonical_git_root(str(bare_pointer)) == str(bare_pointer)


# ── the prompt ───────────────────────────────────────────────────────────────

def test_load_memory_prompt_is_off_by_default_and_creates_the_directory(tmp_path):
    project = str(tmp_path / "p")
    assert md.load_memory_prompt(project, "/t", env={}) is None
    prompt = md.load_memory_prompt(project, "/t", env=ON)
    directory = get_auto_mem_path(project)
    assert prompt is not None and prompt.startswith(f"# auto memory\n\nYou have a persistent, file-based memory "
                                                    f"system at `{directory}`. {md.DIR_EXISTS_GUIDANCE}")
    assert os.path.isdir(directory)
    assert "**Step 2** — add a pointer" in prompt and "## Searching past context" not in prompt


def test_the_relevance_opt_in_drops_the_index_steps_and_past_context_adds_its_section(tmp_path):
    env = {**ON, "TDDAGENTS_MEMORY_RELEVANCE": "1", "TDDAGENTS_MEMORY_SEARCH_PAST_CONTEXT": "1"}
    prompt = md.load_memory_prompt(str(tmp_path), "/transcripts", env=env)
    assert prompt is not None
    assert "Saving a memory is a two-step process" not in prompt
    assert "Write each memory to its own file (e.g., `user_role.md`" in prompt
    assert 'Grep with pattern="<search term>" path="/transcripts/" glob="*.jsonl"' in prompt
    assert md.is_relevance_search_enabled(env) and not md.is_relevance_search_enabled({})


def test_team_memory_gives_the_combined_prompt(tmp_path):
    env = {**ON, "TDDAGENTS_TEAM_MEMORY": "1"}
    prompt = md.load_memory_prompt(str(tmp_path), "/t", env=env)
    team = get_team_mem_path(str(tmp_path))
    assert prompt is not None and prompt.startswith("# Memory\n") and f"shared team directory at `{team}`" in prompt
    assert os.path.isdir(team) and "## Memory scope" in prompt
    assert "Each directory (private and team) has its own `MEMORY.md` index" in prompt
    skip = build_combined_memory_prompt("/a/", "/a/team/", skip_index=True)
    assert "in the chosen directory (private or team, per the type's scope guidance) using" in skip
    assert not is_team_memory_enabled({"TDDAGENTS_TEAM_MEMORY": "1"})


def test_build_memory_prompt_includes_the_index_or_says_it_is_empty(tmp_path):
    empty = md.build_memory_prompt("Persistent Agent Memory", f"{tmp_path}/", ["- note"])
    assert empty.startswith("# Persistent Agent Memory\n") and "\n- note\n" in empty
    assert empty.endswith("## MEMORY.md\n\nYour MEMORY.md is currently empty. When you save new memories, they will "
                          "appear here.")
    (tmp_path / "MEMORY.md").write_text("  - [a](a.md) — hook  \n")
    assert md.build_memory_prompt("X", f"{tmp_path}/").endswith("## MEMORY.md\n\n- [a](a.md) — hook")


def test_ensure_memory_dir_exists_tolerates_failure(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    md.ensure_memory_dir_exists(str(blocker / "sub"))
    assert not (blocker / "sub").exists()


# ── age and scan ─────────────────────────────────────────────────────────────

def test_memory_age():
    now = 10 * DAY
    assert [memory_age_days(now - d * DAY, now) for d in (0, 1, 2)] == [0, 1, 2]
    assert memory_age_days(now + DAY, now) == 0
    assert [memory_age(now - d * DAY, now) for d in (0, 1, 47)] == ["today", "yesterday", "47 days ago"]
    assert memory_freshness_text(now - DAY, now) == ""
    assert memory_freshness_text(now - 3 * DAY, now).startswith("This memory is 3 days old. Memories are")
    assert memory_freshness_note(now, now) == ""
    stale = memory_freshness_text(now - 3 * DAY, now)
    assert memory_freshness_note(now - 3 * DAY, now) == f"<system-reminder>{stale}</system-reminder>\n"
    assert memory_age_days(time.time() * 1000) == 0


def _memory(directory: Path, name: str, body: str, age_days: float = 0) -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    stamp = time.time() - age_days * 86_400
    os.utime(path, (stamp, stamp))
    return path


def test_scan_memory_files_and_manifest(tmp_path):
    _memory(tmp_path, "MEMORY.md", "- index")
    _memory(tmp_path, "old.md", "---\ndescription: old one\ntype: user\n---\nx", age_days=5)
    _memory(tmp_path, "sub/new.md", "---\ntype: weird\n---\ny")
    _memory(tmp_path, "notes.txt", "ignored")
    headers = scan_memory_files(str(tmp_path))
    assert [(h.filename, h.description, h.type) for h in headers] == [
        ("sub/new.md", None, None), ("old.md", "old one", "user")]
    manifest = format_memory_manifest(headers)
    assert re.fullmatch(r"- sub/new\.md \(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z\)\n"
                        r"- \[user\] old\.md \(.+Z\): old one", manifest)
    assert scan_memory_files(str(tmp_path / "missing")) == []
    assert format_memory_manifest([MemoryHeader("a.md", "/a.md", 0, None, None)]) == "- a.md (1970-01-01T00:00:00.000Z)"


def test_find_relevant_memories(tmp_path):
    _memory(tmp_path, "a.md", "---\ndescription: about testing\n---\n")
    _memory(tmp_path, "b.md", "---\ndescription: about cats\n---\n")
    seen: list[tuple[str, str]] = []

    async def select(system: str, user: str) -> str:
        seen.append((system, user))
        return 'Here: {"selected_memories": ["b.md", "missing.md", "a.md"]}'

    picked = asyncio.run(find_relevant_memories("tests please", str(tmp_path), select, ["Bash"],
                                                frozenset({str(tmp_path / "zzz.md")})))
    assert [Path(m.path).name for m in picked] == ["b.md", "a.md"]
    system, user = seen[0]
    assert "useful to TDDAgents" in system
    assert user.startswith("Query: tests please\n\nAvailable memories:\n")
    assert "\n\nRecently used tools: Bash\n\nReply with JSON only" in user

    async def broken(system: str, user: str) -> str:
        raise RuntimeError("down")

    async def prose(system: str, user: str) -> str:
        return "no json here"

    async def not_a_list(system: str, user: str) -> str:
        return '{"selected_memories": "a.md"}'

    for selector in (broken, prose, not_a_list):
        assert asyncio.run(find_relevant_memories("q q", str(tmp_path), selector)) == []
    surfaced = frozenset({str(tmp_path / "a.md"), str(tmp_path / "b.md")})
    assert asyncio.run(find_relevant_memories("q q", str(tmp_path), select, (), surfaced)) == []


# ── team paths ───────────────────────────────────────────────────────────────

def test_team_paths(tmp_path):
    project = str(tmp_path / "p")
    team = get_team_mem_path(project)
    assert team == get_auto_mem_path(project) + "team/"
    assert get_team_mem_entrypoint(project) == team + "MEMORY.md"
    env = {**ON, "TDDAGENTS_TEAM_MEMORY": "1"}
    assert is_team_mem_file(team + "a.md", project, env=env) is True
    assert is_team_mem_file(team + "a.md", project, env=ON) is False
    assert is_team_mem_file("/etc/a.md", project, env=env) is False


@pytest.mark.parametrize("key, message", [
    ("a\0b", "Null byte in path key"), ("..%2Fx", "URL-encoded traversal"), ("a\\b", "Backslash in path key"),
    ("/abs", "Absolute path key"), ("．．／x", "Unicode-normalized traversal"),
])
def test_sanitize_path_key_refuses(key, message):
    with pytest.raises(PathTraversalError, match=message):
        sanitize_path_key(key)


def test_sanitize_path_key_accepts_plain_keys():
    assert sanitize_path_key("notes/a.md") == "notes/a.md"
    assert sanitize_path_key("%41.md") == "%41.md"


def test_validate_team_mem_paths(tmp_path):
    project = str(tmp_path / "p")
    team = get_team_mem_path(project)
    assert validate_team_mem_write_path(team + "x/a.md", project) == team + "x/a.md"  # team dir not created yet
    os.makedirs(team)
    assert validate_team_mem_key("b.md", project) == team + "b.md"
    for bad, message in ((team + "a\0", "Null byte in path"), ("/etc/passwd", "escapes team memory directory")):
        with pytest.raises(PathTraversalError, match=message):
            validate_team_mem_write_path(bad, project)
    with pytest.raises(PathTraversalError, match="Key escapes"):
        validate_team_mem_key("../x", project)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, team + "link")
    with pytest.raises(PathTraversalError, match="via symlink"):
        validate_team_mem_write_path(team + "link/a.md", project)
    with pytest.raises(PathTraversalError, match="via symlink"):
        validate_team_mem_key("link/a.md", project)


def test_realpath_deepest_existing(tmp_path):
    (tmp_path / "real").mkdir()
    assert realpath_deepest_existing(str(tmp_path / "real" / "new" / "f")) == str(
        (tmp_path / "real").resolve() / "new" / "f")
    os.symlink(tmp_path / "nowhere", tmp_path / "dangling")
    with pytest.raises(PathTraversalError, match="Dangling symlink"):
        realpath_deepest_existing(str(tmp_path / "dangling" / "f"))
    os.symlink(tmp_path / "loop", tmp_path / "loop")
    with pytest.raises(PathTraversalError, match="Symlink loop"):
        realpath_deepest_existing(str(tmp_path / "loop" / "f"))
    (tmp_path / "file").write_text("")
    assert realpath_deepest_existing(str(tmp_path / "file" / "x")).endswith("/file/x")


# ── wiring: system prompt, claudemd, carve-outs, ReadFile ───────────────────

def _ws(tmp_path: Path) -> Any:
    from app.workspace.local import LocalWorkspace

    return LocalWorkspace(str(tmp_path / "proj"))


def test_the_memory_section_follows_the_opt_in(tmp_path, monkeypatch):
    from app.context import get_memory_section

    ws = _ws(tmp_path)
    monkeypatch.delenv("TDDAGENTS_ENABLE_AUTO_MEMORY", raising=False)
    assert get_memory_section(ws) is None
    monkeypatch.setenv("TDDAGENTS_ENABLE_AUTO_MEMORY", "1")
    section = get_memory_section(ws)
    assert section is not None and f"memory system at `{get_auto_mem_path(str(ws.root))}`" in section


def test_claudemd_loads_both_indexes_and_wraps_the_team_one(tmp_path):
    from app.utils.claudemd import get_claude_mds, get_memory_files

    ws = _ws(tmp_path)
    root = str(ws.root)
    Path(get_auto_mem_entrypoint(root)).parent.mkdir(parents=True)
    Path(get_auto_mem_entrypoint(root)).write_text("private")
    Path(get_team_mem_entrypoint(root)).parent.mkdir(parents=True)
    Path(get_team_mem_entrypoint(root)).write_text("shared")
    files = get_memory_files(ws, home=str(tmp_path / "h"), managed_dir="/none",
                             env={**ON, "TDDAGENTS_TEAM_MEMORY": "1"})
    assert [(f.type, f.content) for f in files] == [("AutoMem", "private"), ("TeamMem", "shared")]
    rendered = get_claude_mds(files)
    assert (f"Contents of {get_team_mem_entrypoint(root)} (shared team memory, synced across the organization):\n\n"
            '<team-memory-content source="shared">\nshared\n</team-memory-content>') in rendered


def test_settings_turn_auto_memory_on_for_claudemd_and_the_detection(tmp_path, monkeypatch):
    from app.utils.claudemd import get_memory_files
    from app.utils.memory_file_detection import auto_memory_dir, auto_memory_target

    monkeypatch.delenv("TDDAGENTS_ENABLE_AUTO_MEMORY", raising=False)
    ws = _ws(tmp_path)
    root = str(ws.root)
    Path(get_auto_mem_entrypoint(root)).parent.mkdir(parents=True)
    Path(get_auto_mem_entrypoint(root)).write_text("private")
    from app.state.app_state_store import AppStateStore
    from app.tool import tool_context_for

    ctx = tool_context_for(AppStateStore(), workspace=ws)
    assert auto_memory_dir(ctx) is None and auto_memory_target(ctx, get_auto_mem_entrypoint(root)) is None
    (Path(root) / ".tddagents").mkdir(exist_ok=True)
    (Path(root) / ".tddagents" / "settings.local.json").write_text('{"autoMemoryEnabled": true}')
    assert [f.type for f in get_memory_files(ws, home=str(tmp_path / "h"), env={})] == ["AutoMem"]
    assert auto_memory_dir(ctx) == get_auto_mem_path(root)
    entry = get_auto_mem_entrypoint(root)
    assert auto_memory_target(ctx, entry) == entry
    assert auto_memory_target(ctx, "MEMORY.md") is None and auto_memory_target(ctx, "/etc/x") is None


def test_auto_memory_files_are_carved_out_for_read_write_and_the_tdd_tier(tmp_path, monkeypatch):
    from app.state.app_state_store import AppState, AppStateStore
    from app.tdd.ledger import PhaseLedger, TddPhase
    from app.tool import tool_context_for
    from app.tools.file_read_tool.file_read_tool import build_read_file_tool
    from app.tools.file_write_tool.file_write_tool import build_write_file_tool
    from app.types.permissions import PermissionBehavior
    from app.utils.permissions.permissions import tdd_phase_denial
    from tests.plan_helpers import CURRENT_ITEM_PLAN

    monkeypatch.setenv("TDDAGENTS_ENABLE_AUTO_MEMORY", "1")
    ws = _ws(tmp_path)
    store = AppStateStore(AppState(tdd_plan=CURRENT_ITEM_PLAN, phase_ledger=PhaseLedger(phase=TddPhase.RED)))
    ctx = tool_context_for(store, workspace=ws)
    path = get_auto_mem_path(str(ws.root)) + "user_role.md"
    write = asyncio.run(build_write_file_tool().check_permissions({"file_path": path, "content": "x"}, ctx))
    assert (write.behavior, write.decision_reason) == (
        PermissionBehavior.ALLOW, {"type": "other", "reason": "auto memory files are allowed for writing"})
    assert tdd_phase_denial(build_write_file_tool(), {"file_path": path, "content": "x"}, ctx) is None
    asyncio.run(build_write_file_tool().call({"file_path": path, "content": "---\ntype: user\n---\nme"}, ctx))
    assert Path(path).read_text().endswith("me")
    stamp = time.time() - 5 * 86_400
    os.utime(path, (stamp, stamp))
    read = asyncio.run(build_read_file_tool().check_permissions({"file_path": path}, ctx))
    assert read.decision_reason == {"type": "other", "reason": "auto memory files are allowed for reading"}
    content = asyncio.run(build_read_file_tool().call({"file_path": path}, ctx)).content
    assert content.startswith("<system-reminder>This memory is 5 days old.")
    monkeypatch.delenv("TDDAGENTS_ENABLE_AUTO_MEMORY")
    off = asyncio.run(build_write_file_tool().check_permissions({"file_path": path, "content": "x"}, ctx))
    assert off.decision_reason != {"type": "other", "reason": "auto memory files are allowed for writing"}


def test_a_fresh_memory_file_is_read_without_a_note(tmp_path, monkeypatch):
    from app.state.app_state_store import AppStateStore
    from app.tool import tool_context_for
    from app.tools.file_read_tool.file_read_tool import _memory_freshness_prefix

    monkeypatch.setenv("TDDAGENTS_ENABLE_AUTO_MEMORY", "1")
    ws = _ws(tmp_path)
    ctx = tool_context_for(AppStateStore(), workspace=ws)
    path = get_auto_mem_path(str(ws.root)) + "a.md"
    assert _memory_freshness_prefix(ctx, path) == ""  # missing file
    Path(path).parent.mkdir(parents=True)
    Path(path).write_text("x")
    assert _memory_freshness_prefix(ctx, path) == "" and _memory_freshness_prefix(ctx, "a.py") == ""


# ── relevant memories ────────────────────────────────────────────────────────

def test_memory_header():
    now = 10 * DAY
    assert md_header("/m/a.md", now, now) == "Memory (saved today): /m/a.md:"
    stale = md_header("/m/a.md", now - 3 * DAY, now)
    assert stale.endswith("Verify against current code before asserting as fact.\n\nMemory: /m/a.md:")


def md_header(path: str, mtime: float, now: float) -> str:
    from app.memdir.relevant_memories import memory_header

    return memory_header(path, mtime, now)


def test_read_memories_for_surfacing_caps_and_notes(tmp_path):
    from app.memdir.relevant_memories import (
        MAX_MEMORY_BYTES,
        MAX_MEMORY_LINES,
        read_memories_for_surfacing,
    )

    small = _memory(tmp_path, "small.md", "a\nb")
    long = _memory(tmp_path, "long.md", "\n".join(["l"] * (MAX_MEMORY_LINES + 5)))
    wide = _memory(tmp_path, "wide.md", "w" * 100 + "\n" + "x" * MAX_MEMORY_BYTES)
    one_line = _memory(tmp_path, "one.md", "z" * (MAX_MEMORY_BYTES + 10))
    out = read_memories_for_surfacing([RelevantMemory(str(p), 0) for p in (small, long, wide, one_line)]
                                      + [RelevantMemory(str(tmp_path / "gone.md"), 0)])
    by_name = {Path(m["path"]).name: m for m in out}
    assert set(by_name) == {"small.md", "long.md", "wide.md", "one.md"}
    assert (by_name["small.md"]["content"], by_name["small.md"]["limit"]) == ("a\nb", None)
    assert by_name["long.md"]["content"].endswith(
        f"(first {MAX_MEMORY_LINES} lines). Use the ReadFile tool to view the complete file at: {long}")
    assert by_name["long.md"]["limit"] == MAX_MEMORY_LINES
    assert by_name["wide.md"]["content"].startswith("w" * 100 + "\n\n> This memory file was truncated (4096 byte")
    assert len(by_name["one.md"]["content"].split("\n\n>")[0]) == MAX_MEMORY_BYTES
    assert by_name["small.md"]["header"].startswith("This memory is")  # mtime 0: very old


def test_collect_surfaced_and_recent_tools():
    from app.memdir.relevant_memories import (
        collect_recent_successful_tools,
        collect_surfaced_memories,
    )
    from app.utils.attachments import Attachment, create_attachment_messages

    attachment = Attachment("relevant_memories", {"memories": [
        {"path": "/m/a.md", "content": "abc", "mtimeMs": 0, "header": "H", "limit": None}]})
    messages = create_attachment_messages(attachment)
    assert [m.content for m in messages] == ["<system-reminder>\nH\n\nabc\n</system-reminder>"]
    assert collect_surfaced_memories([HumanMessage(content="x"), *messages]) == (frozenset({"/m/a.md"}), 3)
    turn = [
        HumanMessage(content="old"), AIMessage(content="", tool_calls=[{"name": "Old", "args": {}, "id": "0"}]),
        HumanMessage(content="now"),
        AIMessage(content="", tool_calls=[{"name": "Bash", "args": {}, "id": "1"},
                                          {"name": "Grep", "args": {}, "id": "2"},
                                          {"name": "Bash", "args": {}, "id": "3"},
                                          {"name": "ReadFile", "args": {}, "id": "4"},
                                          {"name": "Pending", "args": {}, "id": "5"}]),
        ToolMessage(content="ok", tool_call_id="1"), ToolMessage(content="ok", tool_call_id="2"),
        ToolMessage(content="bad", tool_call_id="3", status="error"), ToolMessage(content="ok", tool_call_id="4"),
    ]
    assert collect_recent_successful_tools(turn, 2) == ["Grep", "ReadFile"]


def test_the_prefetch_runs_once_and_is_consumed_without_waiting(tmp_path, monkeypatch):
    from app.memdir import relevant_memories as rm
    from app.state.app_state_store import AppStateStore
    from app.tool import tool_context_for

    monkeypatch.setenv("TDDAGENTS_ENABLE_AUTO_MEMORY", "1")
    ws = _ws(tmp_path)
    ctx = tool_context_for(AppStateStore(), workspace=ws)
    memory_dir = Path(get_auto_mem_path(str(ws.root)))
    _memory(memory_dir, "a.md", "---\ndescription: testing tips\n---\nuse pytest")
    release = asyncio.Event()

    async def select(system: str, user: str) -> str:
        await release.wait()
        return '{"selected_memories": ["a.md"]}'

    prompt = [HumanMessage(content="how do I test this")]
    assert rm.start_relevant_memory_prefetch(prompt, ctx, select) is None  # relevance opt-in off
    monkeypatch.setenv("TDDAGENTS_MEMORY_RELEVANCE", "1")
    assert rm.start_relevant_memory_prefetch([HumanMessage(content="oneword")], ctx, select) is None
    assert rm.start_relevant_memory_prefetch([], ctx, select) is None

    async def go() -> Any:
        prefetch = rm.start_relevant_memory_prefetch(prompt, ctx, select)
        assert prefetch is not None and rm.consume_memory_prefetch(prefetch, ctx) == []  # not settled: no wait
        release.set()
        await prefetch.task
        first = rm.consume_memory_prefetch(prefetch, ctx)
        return first, rm.consume_memory_prefetch(prefetch, ctx)

    first, again = asyncio.run(go())
    (attachment,) = first
    assert attachment.type == "relevant_memories" and again == []
    (memory,) = attachment.data["memories"]
    assert memory["content"].endswith("use pytest") and memory["header"].startswith("Memory (saved today): ")

    async def second_turn() -> Any:  # already read into the file state: filtered out
        prefetch = rm.start_relevant_memory_prefetch(prompt, ctx, select)
        assert prefetch is not None
        await prefetch.task
        return rm.consume_memory_prefetch(prefetch, ctx)

    assert asyncio.run(second_turn()) == []
    from app.utils.attachments import Attachment, create_attachment_messages

    big = {"path": "/x", "content": "y" * rm.MAX_SESSION_BYTES, "mtimeMs": 0, "header": "h", "limit": None}
    spent = [HumanMessage(content="a b"),
             *create_attachment_messages(Attachment("relevant_memories", {"memories": [big]}))]
    assert rm.start_relevant_memory_prefetch(spent, ctx, select) is None
    assert rm.consume_memory_prefetch(None, ctx) == []


def test_a_failed_prefetch_and_other_attachments_pass_through(tmp_path):
    from app.memdir import relevant_memories as rm
    from app.state.app_state_store import AppStateStore
    from app.tool import tool_context_for
    from app.utils.attachments import Attachment

    ctx = tool_context_for(AppStateStore(), workspace=_ws(tmp_path))

    async def go() -> Any:
        async def boom() -> list[Any]:
            raise RuntimeError("x")

        task = asyncio.ensure_future(boom())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return rm.consume_memory_prefetch(rm.MemoryPrefetch(task), ctx)

    assert asyncio.run(go()) == []
    other = Attachment("date_change", {"newDate": "x"})
    assert rm.filter_duplicate_memory_attachments([other], ctx) == [other]


def test_the_loop_yields_the_prefetched_memories_after_the_tools(tmp_path, monkeypatch):
    from dataclasses import replace

    from app.memdir import relevant_memories as rm
    from app.query.query import run_loop
    from app.query.state import initial_loop_state
    from app.state.app_state_store import AppStateStore
    from app.tool import tool_context_for
    from tests.test_loop_engine import (
        CONFIG,
        FakeModel,
        FakeTools,
        asks_for,
        make_fake_deps,
    )

    monkeypatch.setenv("TDDAGENTS_ENABLE_AUTO_MEMORY", "1")
    monkeypatch.setenv("TDDAGENTS_MEMORY_RELEVANCE", "1")
    ws = _ws(tmp_path)
    _memory(Path(get_auto_mem_path(str(ws.root))), "a.md", "---\ndescription: x\n---\nremember pytest")

    async def select(system: str, user: str) -> str:
        return '{"selected_memories": ["a.md"]}'

    monkeypatch.setattr(rm, "default_select", lambda context: select)
    state = initial_loop_state((HumanMessage(content="write the tests"),),
                               tool_context_for(AppStateStore(), workspace=ws))
    model = FakeModel([[asks_for("Nothing", "c1")], [AIMessage(content="done")]])

    class SlowTools(FakeTools):
        """The tools take a moment, as real ones do: the prefetch settles meanwhile."""

        async def __call__(self, *args: Any) -> Any:
            await asyncio.sleep(0.3)
            async for message in super().__call__(*args):
                yield message

    async def go() -> list[Any]:
        return [e async for e in run_loop(replace(state), CONFIG, make_fake_deps(model, tools=SlowTools()))]

    asyncio.run(go())
    second_turn = model.seen[1].messages
    surfaced = [m for m in second_turn if "remember pytest" in str(getattr(m, "content", ""))]
    assert len(surfaced) == 1 and str(surfaced[0].content).startswith("<system-reminder>\nMemory (saved today): ")
