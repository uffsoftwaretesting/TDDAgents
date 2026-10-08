"""
`app/loop/context/memory.py`, the port of claude-code's `utils/claudemd.ts` memory loader.

Host-side files (Managed/User/AutoMem) live under tmp dirs passed as `home`/`managed_dir`;
workspace files go through a real LocalWorkspace or the root-less FakeWorkspace.
"""

import os
from pathlib import Path

import pytest

from app.loop.context import memory as m
from app.loop.context.memory import (
    MEMORY_INSTRUCTION_PROMPT,
    HostSource,
    MemoryFileInfo,
    WorkspaceSource,
    extract_include_paths,
    format_file_size,
    get_claude_mds,
    get_memory_files,
    lex_blocks,
    parse_frontmatter,
    parse_frontmatter_paths,
    parse_memory_file_content,
    process_md_rules,
    process_memory_file,
    split_path_in_frontmatter,
    strip_html_comments,
    truncate_entrypoint_content,
)
from app.workspace.local import LocalWorkspace
from tests.conftest import FakeWorkspace


@pytest.fixture
def dirs(tmp_path):
    home = tmp_path / "home"
    managed = tmp_path / "managed"
    ws_root = tmp_path / "ws"
    for d in (home, managed, ws_root):
        d.mkdir()
    return home, managed, ws_root


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def load(dirs, env=None):
    home, managed, ws_root = dirs
    return get_memory_files(LocalWorkspace(str(ws_root)), home=str(home), managed_dir=str(managed), env=env or {})


def test_constants():
    assert m.MAX_INCLUDE_DEPTH == 5
    assert m.MAX_MEMORY_CHARACTER_COUNT == 40000
    assert (m.MAX_ENTRYPOINT_LINES, m.MAX_ENTRYPOINT_BYTES, m.ENTRYPOINT_NAME) == (200, 25_000, "MEMORY.md")
    assert (m.MEMORY_FILE_NAME, m.LOCAL_MEMORY_FILE_NAME, m.CONFIG_DIR_NAME) == (
        "TDDAGENTS.md", "TDDAGENTS.local.md", ".tddagents")
    assert m.MANAGED_DIR == "/etc/tddagents"
    assert MEMORY_INSTRUCTION_PROMPT.startswith("Codebase and user instructions are shown below.")
    assert {".md", ".py", ".R", ".r", ".patch"} <= m.TEXT_FILE_EXTENSIONS
    assert ".png" not in m.TEXT_FILE_EXTENSIONS


# ── load order and types ─────────────────────────────────────────────────────

def test_full_hierarchy_in_upstream_order(dirs):
    home, managed, ws = dirs
    write(managed / "TDDAGENTS.md", "managed")
    write(managed / ".tddagents/rules/m.md", "managed rule")
    write(home / ".tddagents/TDDAGENTS.md", "user")
    write(home / ".tddagents/rules/u.md", "user rule")
    write(ws / "TDDAGENTS.md", "project")
    write(ws / ".tddagents/TDDAGENTS.md", "dot project")
    write(ws / ".tddagents/rules/sub/r.md", "project rule")
    write(ws / "TDDAGENTS.local.md", "local")
    files = load(dirs)
    assert [(f.type, f.content) for f in files] == [
        ("Managed", "managed"), ("Managed", "managed rule"),
        ("User", "user"), ("User", "user rule"),
        ("Project", "project"), ("Project", "dot project"), ("Project", "project rule"),
        ("Local", "local"),
    ]
    assert files[4].path == "TDDAGENTS.md"
    assert files[6].path == ".tddagents/rules/sub/r.md"
    assert files[0].path == str(managed / "TDDAGENTS.md")


def test_upstream_claude_md_names_are_not_read(dirs):
    _, _, ws = dirs
    write(ws / "CLAUDE.md", "claude code's file")
    write(ws / ".claude/rules/r.md", "x")
    assert load(dirs) == []


def test_empty_and_whitespace_files_are_skipped(dirs):
    _, _, ws = dirs
    write(ws / "TDDAGENTS.md", "  \n\n")
    assert load(dirs) == []


def test_no_workspace_loads_host_files_only(dirs):
    home, managed, _ = dirs
    write(home / ".tddagents/TDDAGENTS.md", "user")
    files = get_memory_files(None, home=str(home), managed_dir=str(managed), env={})
    assert [f.content for f in files] == ["user"]


def test_rootless_sandbox_workspace(dirs):
    home, managed, _ = dirs
    ws = FakeWorkspace(files={"TDDAGENTS.md": "p", ".tddagents/rules/a.md": "r", "TDDAGENTS.local.md": "l"})
    files = get_memory_files(ws, home=str(home), managed_dir=str(managed), env={})
    assert [(f.type, f.path) for f in files] == [
        ("Project", "TDDAGENTS.md"), ("Project", ".tddagents/rules/a.md"), ("Local", "TDDAGENTS.local.md")]


def test_default_home_is_user_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    write(tmp_path / ".tddagents/TDDAGENTS.md", "from real home")
    files = get_memory_files(None, managed_dir=str(tmp_path / "none"), env={})
    assert [f.content for f in files] == ["from real home"]


# ── conditional rules and frontmatter ────────────────────────────────────────

def test_conditional_rules_are_not_loaded_eagerly(dirs):
    _, _, ws = dirs
    write(ws / ".tddagents/rules/always.md", "---\ndescription: x\n---\nalways")
    write(ws / ".tddagents/rules/py.md", "---\npaths: src/**/*.py\n---\nonly py")
    write(ws / ".tddagents/rules/all.md", "---\npaths: '**'\n---\nmatch all")
    files = load(dirs)
    assert [f.content for f in files] == ["match all", "always"]


def test_process_md_rules_conditional_selection():
    ws = FakeWorkspace(files={"r/a.md": "---\npaths: x/*\n---\nA", "r/b.md": "B", "r/c.txt": "C"})
    src = WorkspaceSource(ws)
    assert [f.content for f in process_md_rules(src, "r", "Project", set(), False)] == ["B"]
    cond = process_md_rules(src, "r", "Project", set(), False, conditional_rule=True)
    assert [(f.content, f.globs) for f in cond] == [("A", ("x/*",))]


def test_parse_frontmatter():
    assert parse_frontmatter("---\na: 1\n---\nbody") == ({"a": 1}, "body")
    # `\s*` also consumes following blank lines, in JS as in Python
    assert parse_frontmatter("---  \na: 1\n---  \n\nbody") == ({"a": 1}, "body")
    assert parse_frontmatter("---\na: 1\n---x\nbody") == ({"a": 1}, "x\nbody")  # upstream regex quirk
    assert parse_frontmatter("no fm") == ({}, "no fm")
    assert parse_frontmatter("---\n- a\n---\nb") == ({}, "b")
    assert parse_frontmatter("---\na: [\n---\nb") == ({}, "b")
    assert parse_frontmatter(" ---\na: 1\n---\nb") == ({}, " ---\na: 1\n---\nb")


@pytest.mark.parametrize("raw, expected", [
    ("---\npaths: src/*.py\n---\nc", ("c", ("src/*.py",))),
    ("---\npaths: src/**\n---\nc", ("c", ("src",))),
    ("---\npaths: '**'\n---\nc", ("c", None)),
    ("---\npaths: '**, **'\n---\nc", ("c", None)),
    ("---\npaths: '/**'\n---\nc", ("c", None)),
    ("---\npaths: ''\n---\nc", ("c", None)),
    ("---\nother: 1\n---\nc", ("c", None)),
    ("---\npaths: [a, 'b/{x,y}']\n---\nc", ("c", ("a", "b/x", "b/y"))),
])
def test_parse_frontmatter_paths(raw, expected):
    assert parse_frontmatter_paths(raw) == expected


@pytest.mark.parametrize("value, expected", [
    ("a, b", ["a", "b"]),
    ("src/*.{ts,tsx}", ["src/*.ts", "src/*.tsx"]),
    ("{a,b}/{c,d}", ["a/c", "a/d", "b/c", "b/d"]),
    ("x/{a, b},y", ["x/a", "x/b", "y"]),
    (["a,b", "c"], ["a", "b", "c"]),
    (3, []),
    (" , ,", []),
])
def test_split_path_in_frontmatter(value, expected):
    assert split_path_in_frontmatter(value) == expected


# ── html comments and lexing ─────────────────────────────────────────────────

def test_strip_html_comments_block_level_only():
    text = "a\n<!-- note -->\nb `<!-- code -->` c\n```\n<!-- fenced -->\n```\nd <!-- inline --> e\n"
    out, stripped = strip_html_comments(text)
    assert stripped is True
    assert out == "a\nb `<!-- code -->` c\n```\n<!-- fenced -->\n```\nd <!-- inline --> e\n"


def test_strip_html_comments_multiline_residue_and_unclosed():
    assert strip_html_comments("<!-- a\nb -->\nkeep") == ("keep", True)
    assert strip_html_comments("<!-- note --> Use bun\n") == (" Use bun\n", True)
    assert strip_html_comments("x\n<!-- unclosed\nrest") == ("x\n<!-- unclosed\nrest", False)
    assert strip_html_comments("plain") == ("plain", False)
    assert strip_html_comments("   <!-- indented ok -->\nz") == ("z", True)
    assert strip_html_comments("    <!-- code -->\n") == ("    <!-- code -->\n", False)


def test_lex_blocks():
    text = "p1\n\n    code\n    more\n\n~~~~\nx\n~~~\n~~~~\n<!-- c -->\np2\n"
    assert lex_blocks(text) == [
        ("text", "p1\n\n"), ("code", "    code\n    more\n\n"), ("code", "~~~~\nx\n~~~\n~~~~\n"),
        ("html", "<!-- c -->\n"), ("text", "p2\n"),
    ]
    assert lex_blocks("```\nnever closed") == [("code", "```\nnever closed")]
    assert lex_blocks("para\n    not code\n") == [("text", "para\n    not code\n")]
    assert lex_blocks("\tcode\n") == [("code", "\tcode\n")]


# ── @include ─────────────────────────────────────────────────────────────────

def test_extract_include_paths():
    text = ("See @./a.md and @b.md, @~/c.md @/abs/d.md @/ @@x @#bad\n"
            "email me@host.com `@code.md`\n```\n@fenced.md\n```\n"
            "@e.md#section @f\\ g.md @./a.md\n<!-- @hidden.md --> @after.md\n")
    assert extract_include_paths(lex_blocks(text)) == [
        "./a.md", "b.md,", "~/c.md", "/abs/d.md", "e.md", "f g.md", "after.md"]


def test_extract_include_skips_non_comment_html_and_bare_hash():
    assert extract_include_paths([("html", "<div> @x.md</div>")]) == []
    assert extract_include_paths([("html", "<!-- only comment -->")]) == []
    assert extract_include_paths([("text", "@#only")]) == []
    assert extract_include_paths([("text", "@%x @(y)")]) == []


def test_includes_are_loaded_after_parent_with_parent_set():
    ws = FakeWorkspace(files={
        "TDDAGENTS.md": "main @docs/a.md",
        "docs/a.md": "A @b.md @../TDDAGENTS.md",
        "docs/b.md": "B",
    })
    files = get_memory_files(ws, home="/nonexistent", managed_dir="/nonexistent", env={})
    assert [(f.path, f.parent) for f in files] == [
        ("TDDAGENTS.md", None), ("docs/a.md", "TDDAGENTS.md"), ("docs/b.md", "docs/a.md")]


def test_include_depth_limit():
    files = {f"f{i}.md": f"x{i} @f{i + 1}.md" for i in range(10)}
    files["TDDAGENTS.md"] = "root @f0.md"
    loaded = process_memory_file(WorkspaceSource(FakeWorkspace(files=files)), "TDDAGENTS.md", "Project", set(), False)
    assert [f.path for f in loaded] == ["TDDAGENTS.md", "f0.md", "f1.md", "f2.md", "f3.md"]


def test_external_includes_only_for_user_memory(dirs, tmp_path):
    home, managed, ws_root = dirs
    write(tmp_path / "outside.md", "outside")
    write(ws_root / "TDDAGENTS.md", f"project @{tmp_path}/outside.md @../escape.md")
    write(home / ".tddagents/TDDAGENTS.md", f"user @{tmp_path}/outside.md")
    files = load(dirs)
    assert [(f.type, f.content) for f in files] == [("User", f"user @{tmp_path}/outside.md"),
                                                    ("User", "outside"),
                                                    ("Project", f"project @{tmp_path}/outside.md @../escape.md")]


def test_user_include_relative_and_home(dirs):
    home, _, _ = dirs
    write(home / ".tddagents/TDDAGENTS.md", "u @./x.md")
    write(home / ".tddagents/x.md", "X")
    assert [f.content for f in load(dirs)] == ["u @./x.md", "X"]


def test_home_include_resolution(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    src = HostSource()
    assert src.resolve_include("~/a.md", "/x/y.md") == (src, os.path.join(str(tmp_path), "a.md"))
    assert src.resolve_include("/abs/../b.md", "/x/y.md") == (src, "/b.md")
    assert src.resolve_include("c.md", "/x/y.md") == (src, "/x/c.md")
    ws_src = WorkspaceSource(FakeWorkspace())
    got = ws_src.resolve_include("~/a.md", "TDDAGENTS.md")
    assert got is not None and isinstance(got[0], HostSource) and got[1] == os.path.join(str(tmp_path), "a.md")
    got = ws_src.resolve_include("/etc/x.md", "TDDAGENTS.md")
    assert got is not None and isinstance(got[0], HostSource) and got[1] == "/etc/x.md"
    assert ws_src.resolve_include("../../x.md", "a/TDDAGENTS.md") is None
    assert ws_src.resolve_include("x.md", "a/TDDAGENTS.md") == (ws_src, "a/x.md")


def test_non_text_includes_are_skipped():
    ws = FakeWorkspace(files={"TDDAGENTS.md": "m @img.png @notes.TXT", "img.png": "bin", "notes.TXT": "N"})
    files = process_memory_file(WorkspaceSource(ws), "TDDAGENTS.md", "Project", set(), False)
    assert [f.path for f in files] == ["TDDAGENTS.md", "notes.TXT"]


def test_cycles_and_duplicates_are_processed_once():
    ws = FakeWorkspace(files={"TDDAGENTS.md": "a @x.md", "x.md": "x @TDDAGENTS.md @x.md"})
    processed: set[str] = set()
    files = process_memory_file(WorkspaceSource(ws), "TDDAGENTS.md", "Project", processed, False)
    assert [f.path for f in files] == ["TDDAGENTS.md", "x.md"]
    assert process_memory_file(WorkspaceSource(ws), "./TDDAGENTS.md", "Project", processed, False) == []


def test_host_symlinks_dedup_and_rules_dir_cycles(tmp_path):
    real = tmp_path / "real.md"
    write(real, "R")
    os.symlink(real, tmp_path / "link.md")
    processed: set[str] = set()
    src = HostSource()
    assert len(process_memory_file(src, str(tmp_path / "link.md"), "User", processed, True)) == 1
    assert process_memory_file(src, str(real), "User", processed, True) == []
    rules = tmp_path / "rules"
    write(rules / "a.md", "A")
    write(rules / "b.txt", "B")
    os.symlink(rules, rules / "loop")
    assert src.rule_files(str(rules)) == [str(rules / "a.md")]
    assert src.rule_files(str(tmp_path / "missing")) == []


def test_unreadable_files_are_skipped(tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_bytes(b"\xff\xfe")
    assert HostSource().read(str(bad)) is None
    assert HostSource().read(str(tmp_path)) is None

    class Broken(FakeWorkspace):
        def list_files(self, path=".", depth=1):
            from app.workspace.base import WorkspaceNotFound
            raise WorkspaceNotFound("x")

    assert WorkspaceSource(Broken()).rule_files("r") == []
    assert WorkspaceSource(FakeWorkspace()).read("missing.md") is None


# ── parse_memory_file_content ────────────────────────────────────────────────

def test_parse_memory_file_content_flags_transformed_content():
    info, includes = parse_memory_file_content("plain @x.md", "a.md", "Project")
    assert info == MemoryFileInfo(path="a.md", type="Project", content="plain @x.md")
    assert includes == ["x.md"]
    info, _ = parse_memory_file_content("---\npaths: a\n---\n<!-- c -->\nbody", "a.md", "Project")
    assert info is not None
    assert (info.content, info.globs, info.content_differs_from_disk) == ("body", ("a",), True)
    assert info.raw_content == "---\npaths: a\n---\n<!-- c -->\nbody"
    assert parse_memory_file_content("x", "a.png", "Project") == (None, [])
    info, _ = parse_memory_file_content("x", "Makefile", "Project")
    assert info is not None and info.content == "x"


def test_auto_mem_content_is_truncated():
    info, _ = parse_memory_file_content("\n".join(["l"] * 201), "MEMORY.md", "AutoMem")
    assert info is not None
    assert info.content.endswith("Keep index entries to one line under ~200 chars; move detail into topic files.")


# ── truncation and sizes ─────────────────────────────────────────────────────

def test_truncate_entrypoint_content():
    assert truncate_entrypoint_content("  short  ") == "short"
    lines = "\n".join(f"{i}" for i in range(201))
    out = truncate_entrypoint_content(lines)
    assert out.startswith("0\n1\n")
    assert "\n199\n\n> WARNING: MEMORY.md is 201 lines (limit: 200). Only part of it was loaded." in out
    long_line = "x" * 25_001
    out = truncate_entrypoint_content("a\n" + long_line)
    assert out.startswith("a\n\n> WARNING: MEMORY.md is 24.4KB (limit: 24.4KB) — index entries are too long.")
    out = truncate_entrypoint_content(long_line)
    assert out.startswith("x" * 25_000 + "\n\n> WARNING")
    both = "\n".join(["y" * 200] * 300)
    out = truncate_entrypoint_content(both)
    assert "is 300 lines and 58.9KB." in out
    exact = "z" * 25_000
    assert truncate_entrypoint_content(exact) == exact
    assert truncate_entrypoint_content("\n".join(["q"] * 200)) == "\n".join(["q"] * 200)


@pytest.mark.parametrize("size, expected", [
    (0, "0 bytes"), (1023, "1023 bytes"), (1024, "1KB"), (1536, "1.5KB"),
    (1024 * 1024, "1MB"), (int(2.5 * 1024 ** 3), "2.5GB"), (1024 ** 3 - 1, "1024MB"),
])
def test_format_file_size(size, expected):
    assert format_file_size(size) == expected


# ── AutoMem ──────────────────────────────────────────────────────────────────

def test_auto_memory_off_by_default_and_opt_in(dirs):
    home, managed, ws_root = dirs
    ws = LocalWorkspace(str(ws_root))
    entry = m.get_auto_mem_entrypoint(str(ws.root), str(home))
    write(Path(entry), "remembered")
    assert get_memory_files(ws, home=str(home), managed_dir=str(managed), env={}) == []
    for truthy in ("1", "true", "YES", " on "):
        files = get_memory_files(ws, home=str(home), managed_dir=str(managed),
                                 env={"TDDAGENTS_ENABLE_AUTO_MEMORY": truthy})
        assert [(f.type, f.content, f.path) for f in files] == [("AutoMem", "remembered", entry)]
    assert get_memory_files(ws, home=str(home), managed_dir=str(managed),
                            env={"TDDAGENTS_ENABLE_AUTO_MEMORY": "0"}) == []


def test_auto_memory_path_and_env_default(monkeypatch, tmp_path):
    assert m.get_auto_mem_entrypoint("/a/b c", str(tmp_path)) == str(
        tmp_path / ".tddagents/projects/-a-b-c/memory/MEMORY.md")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert m.get_auto_mem_entrypoint("p") == str(tmp_path / ".tddagents/projects/p/memory/MEMORY.md")
    monkeypatch.setenv("TDDAGENTS_ENABLE_AUTO_MEMORY", "1")
    assert m.is_auto_memory_enabled() is True
    monkeypatch.delenv("TDDAGENTS_ENABLE_AUTO_MEMORY")
    assert m.is_auto_memory_enabled() is False


def test_auto_memory_for_sandbox_and_missing_or_binary(tmp_path):
    entry = m.get_auto_mem_entrypoint("sandbox", str(tmp_path))
    env = {"TDDAGENTS_ENABLE_AUTO_MEMORY": "1"}
    assert get_memory_files(FakeWorkspace(), home=str(tmp_path), managed_dir="/none", env=env) == []
    write(Path(entry), "sbx")
    files = get_memory_files(FakeWorkspace(), home=str(tmp_path), managed_dir="/none", env=env)
    assert [f.content for f in files] == ["sbx"]


def test_auto_memory_not_duplicated_when_already_loaded(tmp_path, monkeypatch):
    entry = m.get_auto_mem_entrypoint("sandbox", str(tmp_path))
    write(Path(entry), "dup")
    managed = tmp_path / "managed"
    managed.mkdir()
    monkeypatch.setattr(m, "MEMORY_FILE_NAME", os.path.relpath(entry, managed))
    files = get_memory_files(None, home=str(tmp_path), managed_dir=str(managed),
                             env={"TDDAGENTS_ENABLE_AUTO_MEMORY": "1"})
    assert [f.type for f in files] == ["Managed"]


# ── rendering ────────────────────────────────────────────────────────────────

def test_get_claude_mds_rendering():
    files = [
        MemoryFileInfo("/etc/tddagents/TDDAGENTS.md", "Managed", "m"),
        MemoryFileInfo("/h/.tddagents/TDDAGENTS.md", "User", "u"),
        MemoryFileInfo("TDDAGENTS.md", "Project", "  p  "),
        MemoryFileInfo("TDDAGENTS.local.md", "Local", "l"),
        MemoryFileInfo("/m/MEMORY.md", "AutoMem", "a"),
        MemoryFileInfo("empty.md", "Project", ""),
    ]
    assert get_claude_mds(files) == (
        f"{MEMORY_INSTRUCTION_PROMPT}\n\n"
        "Contents of /etc/tddagents/TDDAGENTS.md (user's private global instructions for all projects):\n\nm\n\n"
        "Contents of /h/.tddagents/TDDAGENTS.md (user's private global instructions for all projects):\n\nu\n\n"
        "Contents of TDDAGENTS.md (project instructions, checked into the codebase):\n\np\n\n"
        "Contents of TDDAGENTS.local.md (user's private project instructions, not checked in):\n\nl\n\n"
        "Contents of /m/MEMORY.md (user's auto-memory, persists across conversations):\n\na"
    )
    assert get_claude_mds([]) == ""
    assert get_claude_mds([MemoryFileInfo("x", "Project", "")]) == ""


def test_get_large_memory_files():
    small = MemoryFileInfo("a", "Project", "x" * 40000)
    big = MemoryFileInfo("b", "Project", "x" * 40001)
    assert m.get_large_memory_files([small, big]) == [big]


def test_env_truthy():
    assert m.is_env_truthy(None) is False
    assert m.is_env_truthy("off") is False
    assert m.is_env_truthy("On") is True


def test_sanitize_path():
    assert m.sanitize_path("/a_b.c-d") == "-a-b-c-d"


# ── mutation-driven pins ─────────────────────────────────────────────────────

def test_include_external_policy_per_scope(dirs, tmp_path):
    """External (host) includes: only User memory may follow them, as upstream."""
    home, managed, ws = dirs
    ext = tmp_path / "ext.md"
    write(ext, "EXTERNAL")
    write(managed / "TDDAGENTS.md", f"m @{ext}")
    write(managed / ".tddagents/rules/r.md", f"mr @{ext}")
    write(ws / "TDDAGENTS.md", f"p @{ext}")
    write(ws / ".tddagents/TDDAGENTS.md", f"dp @{ext}")
    write(ws / ".tddagents/rules/r.md", f"pr @{ext}")
    write(ws / "TDDAGENTS.local.md", f"l @{ext}")
    assert "EXTERNAL" not in [f.content for f in load(dirs)]
    write(home / ".tddagents/rules/u.md", f"ur @{ext}")
    contents = [f.content for f in load(dirs)]
    assert contents.count("EXTERNAL") == 1
    assert contents[contents.index("EXTERNAL") - 1] == f"ur @{ext}"


def test_user_include_chain_follows_nested_external_includes(dirs):
    home, _, _ = dirs
    write(home / ".tddagents/TDDAGENTS.md", "u @./a.md")
    write(home / ".tddagents/a.md", "A @./b.md")
    write(home / ".tddagents/b.md", "B")
    assert [f.content for f in load(dirs)] == ["u @./a.md", "A @./b.md", "B"]


def test_managed_rules_come_only_from_the_rules_dir(dirs):
    _, managed, _ = dirs
    write(managed / ".tddagents/other.md", "not a rule")
    write(managed / ".tddagents/rules/r.md", "rule")
    assert [f.content for f in load(dirs)] == ["rule"]


def test_host_rules_dirs_are_walked_recursively(dirs):
    home, _, _ = dirs
    write(home / ".tddagents/rules/a/b/deep.md", "deep")
    write(home / ".tddagents/rules/top.md", "top")
    assert [f.content for f in load(dirs)] == ["deep", "top"]


def test_a_bad_include_does_not_stop_later_ones():
    ws = FakeWorkspace(files={"TDDAGENTS.md": "m @../../escape.md @/etc/x.md @ok.md", "ok.md": "OK"})
    files = process_memory_file(WorkspaceSource(ws), "TDDAGENTS.md", "Project", set(), False)
    assert [f.path for f in files] == ["TDDAGENTS.md", "ok.md"]


def test_split_path_tracks_nested_brace_depth():
    assert split_path_in_frontmatter("{a,{b,c}},d") == split_path_in_frontmatter("{a,{b,c}}") + ["d"]


def test_lex_blocks_fence_and_html_edges():
    assert lex_blocks("```\n```\nafter\n") == [("code", "```\n```\n"), ("text", "after\n")]
    assert lex_blocks("```\nx\n```\n    y\n") == [("code", "```\nx\n```\n"), ("text", "    y\n")]
    assert lex_blocks("<!-- c -->\n    y\n") == [("html", "<!-- c -->\n"), ("text", "    y\n")]


def test_strip_keeps_text_before_a_residue_block():
    assert strip_html_comments("a\n<!-- c --> keep\n") == ("a\n keep\n", True)


def test_include_extraction_edges():
    blocks = lex_blocks("@Docs.md @a.md#x#y\n~~~\n@fenced.md\n~~~\n  <!-- c --> @ind.md\n<!-- c -->@tight.md\n"
                        "`c`@after-code.md\n")
    assert extract_include_paths(blocks) == ["Docs.md", "a.md", "ind.md", "tight.md", "after-code.md"]
    assert extract_include_paths(lex_blocks("<!-- unclosed\n@x.md\n")) == []
    assert extract_include_paths(lex_blocks("<!-- c -->\n@later.md\n")) == ["later.md"]


@pytest.mark.parametrize("size, expected", [(1024 ** 3, "1GB"), (1024 ** 4, "1024GB")])
def test_format_file_size_unit_boundaries(size, expected):
    assert format_file_size(size) == expected


def test_truncation_counts_newline_lines_not_words():
    text = "\n".join(["x y z"] * 150)
    assert truncate_entrypoint_content(text) == text


def test_truncation_byte_cut_boundaries():
    limit = m.MAX_ENTRYPOINT_BYTES
    # after the line cut the content is exactly at the byte cap: no further cut
    head = "\n".join(["a" * 125] + ["b" * 124] * 199)
    assert len(head) == limit
    out = truncate_entrypoint_content(head + "\nextra")
    assert out.startswith(head + "\n\n> WARNING")
    # the cut is the LAST newline at or before the cap, never a later one
    text = "a\n" + "b" * 100 + "\n" + "c" * limit + "\nd"
    assert truncate_entrypoint_content(text).startswith("a\n" + "b" * 100 + "\n\n> WARNING")
    # a newline exactly at the cap counts; one just past it does not
    at_cap = "x" * limit + "\n" + "y" * 10
    assert truncate_entrypoint_content(at_cap).startswith("x" * limit + "\n\n> WARNING")
    past = "x" * (limit + 1) + "\n" + "y"
    assert truncate_entrypoint_content(past).startswith("x" * limit + "\n\n> WARNING")


def test_sanitize_path_replaces_nothing_alphanumeric():
    assert m.sanitize_path("/Ab9") == "-Ab9"
