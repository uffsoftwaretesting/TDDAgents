"""Tests for the install/cache half of app/utils/plugins/plugin_loader.py (upstream pluginLoader.ts)."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.utils.exec_file_no_throw import ExecResult
from app.utils.plugins import plugin_loader
from app.utils.plugins.plugin_directories import get_plugins_directory
from app.utils.plugins.plugin_loader import (
    cache_plugin,
    copy_dir,
    copy_plugin_to_versioned_cache,
    generate_temporary_cache_name_for_plugin,
    get_legacy_cache_path,
    get_plugin_cache_path,
    get_versioned_cache_path,
    get_versioned_cache_path_in,
    get_versioned_zip_cache_path,
    git_clone,
    install_from_git_subdir,
    install_from_github,
    install_from_local,
    install_from_npm,
    load_plugin_manifest,
    probe_seed_cache,
    probe_seed_cache_any_version,
    resolve_git_subdir_url,
    resolve_plugin_path,
    validate_git_url,
)
from tests.plugin_git_helpers import commit_more, make_repo, manifest, run_git


def _write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ── paths ─────────────────────────────────────────────────────────────────────

def test_cache_paths_live_under_the_plugins_directory():
    base = get_plugins_directory()
    assert get_plugin_cache_path() == os.path.join(base, "cache")
    assert get_versioned_cache_path("fmt@tools", "1.2.0") == os.path.join(base, "cache", "tools", "fmt", "1.2.0")
    assert get_versioned_zip_cache_path("fmt@tools", "1.2.0") == os.path.join(
        base, "cache", "tools", "fmt", "1.2.0.zip"
    )


def test_versioned_path_sanitizes_every_segment_against_traversal(tmp_path: Path):
    path = get_versioned_cache_path_in(str(tmp_path), "my.plugin@mk/t", "../../1.0+x")
    assert path == os.path.join(str(tmp_path), "cache", "mk-t", "my-plugin", "..-..-1.0-x")
    # A bare id has no marketplace: "unknown".
    assert get_versioned_cache_path_in("/b", "solo", "v").endswith(os.path.join("cache", "unknown", "solo", "v"))


def test_legacy_path_and_resolution_order(tmp_path: Path):
    legacy = get_legacy_cache_path("my plugin")
    assert legacy == os.path.join(get_plugin_cache_path(), "my-plugin")
    # Nothing exists: the versioned path for a new install, the legacy one without a version.
    assert asyncio.run(resolve_plugin_path("p@m", "1.0")) == get_versioned_cache_path("p@m", "1.0")
    assert asyncio.run(resolve_plugin_path("p@m")) == get_legacy_cache_path("p")
    # A legacy directory wins over a missing versioned one.
    os.makedirs(get_legacy_cache_path("p"))
    assert asyncio.run(resolve_plugin_path("p@m", "1.0")) == get_legacy_cache_path("p")
    # An existing versioned directory wins over everything.
    os.makedirs(get_versioned_cache_path("p@m", "1.0"))
    assert asyncio.run(resolve_plugin_path("p@m", "1.0")) == get_versioned_cache_path("p@m", "1.0")


def test_seed_probes(tmp_path: Path, monkeypatch: Any):
    seed_a, seed_b = tmp_path / "a", tmp_path / "b"
    monkeypatch.setenv("TDDAGENTS_PLUGIN_SEED_DIR", f"{seed_a}{os.pathsep}{seed_b}")
    assert asyncio.run(probe_seed_cache("p@m", "1.0")) is None
    # An empty version directory in the first seed does not count; the populated one in the second does.
    os.makedirs(get_versioned_cache_path_in(str(seed_a), "p@m", "1.0"))
    _write(Path(get_versioned_cache_path_in(str(seed_b), "p@m", "1.0")) / "f")
    assert asyncio.run(probe_seed_cache("p@m", "1.0")) == get_versioned_cache_path_in(str(seed_b), "p@m", "1.0")
    # Any-version: seed a holds one empty version (skipped), seed b exactly one populated version.
    assert asyncio.run(probe_seed_cache_any_version("p@m")) == get_versioned_cache_path_in(str(seed_b), "p@m", "1.0")
    # Two versions in a seed are ambiguous.
    _write(Path(get_versioned_cache_path_in(str(seed_b), "p@m", "2.0")) / "f")
    assert asyncio.run(probe_seed_cache_any_version("p@m")) is None


# ── copy ──────────────────────────────────────────────────────────────────────

def test_copy_dir_copies_files_and_rewrites_symlinks(tmp_path: Path):
    src = tmp_path / "src"
    _write(src / "a.txt", "A")
    _write(src / "sub" / "b.txt", "B")
    outside = _write(tmp_path / "outside.txt", "O")
    os.symlink(src / "sub" / "b.txt", src / "inner_link")
    os.symlink(outside, src / "outer_link")
    os.symlink(tmp_path / "missing", src / "broken_link")
    dest = tmp_path / "dest"
    asyncio.run(copy_dir(str(src), str(dest)))
    assert (dest / "a.txt").read_text() == "A"
    assert (dest / "sub" / "b.txt").read_text() == "B"
    # Inside the tree: a relative link to the copy, not to the source.
    assert os.readlink(dest / "inner_link") == os.path.join("sub", "b.txt")
    assert os.path.realpath(dest / "inner_link") == str((dest / "sub" / "b.txt").resolve())
    # Outside the tree: the absolute resolved target.
    assert os.readlink(dest / "outer_link") == str(outside.resolve())
    # Broken: the raw target, as is.
    assert os.readlink(dest / "broken_link") == str(tmp_path / "missing")


def test_copy_plugin_to_versioned_cache_copies_the_entry_source_without_git(tmp_path: Path):
    marketplace = tmp_path / "mkt"
    _write(marketplace / "plugins" / "p" / "README.md", "hi")
    _write(marketplace / "plugins" / "p" / ".git" / "HEAD", "ref")
    path = asyncio.run(copy_plugin_to_versioned_cache(
        "/ignored", "p@m", "1.0", {"name": "p", "source": "./plugins/p"}, str(marketplace)
    ))
    assert path == get_versioned_cache_path("p@m", "1.0")
    assert (Path(path) / "README.md").read_text() == "hi"
    assert not (Path(path) / ".git").exists()
    # A populated cache is returned without copying again.
    _write(marketplace / "plugins" / "p" / "NEW.md")
    assert asyncio.run(copy_plugin_to_versioned_cache("/ignored", "p@m", "1.0", None, None)) == path
    assert not (Path(path) / "NEW.md").exists()


def test_copy_plugin_to_versioned_cache_replaces_an_empty_cache_and_uses_the_fallback_source(tmp_path: Path):
    os.makedirs(get_versioned_cache_path("p@m", "1.0"))
    src = tmp_path / "downloaded"
    _write(src / "f.md", "F")
    path = asyncio.run(copy_plugin_to_versioned_cache(str(src), "p@m", "1.0"))
    assert (Path(path) / "f.md").read_text() == "F"


def test_copy_plugin_to_versioned_cache_prefers_a_seed_in_place(tmp_path: Path, monkeypatch: Any):
    seed = tmp_path / "seed"
    seed_version = Path(get_versioned_cache_path_in(str(seed), "p@m", "1.0"))
    _write(seed_version / "f")
    monkeypatch.setenv("TDDAGENTS_PLUGIN_SEED_DIR", str(seed))
    assert asyncio.run(copy_plugin_to_versioned_cache(str(tmp_path / "nothing"), "p@m", "1.0")) == str(seed_version)
    assert not os.path.exists(get_versioned_cache_path("p@m", "1.0"))


def test_copy_plugin_to_versioned_cache_errors(tmp_path: Path):
    marketplace = tmp_path / "mkt"
    marketplace.mkdir()
    with pytest.raises(ValueError, match="Path traversal detected"):
        asyncio.run(copy_plugin_to_versioned_cache("/x", "p@m", "1", {"name": "p", "source": "./../../etc"},
                                                   str(marketplace)))
    with pytest.raises(RuntimeError, match=r"Plugin source directory not found: .*\(from entry.source: \./gone\)"):
        asyncio.run(copy_plugin_to_versioned_cache("/x", "p@m", "1", {"name": "p", "source": "./gone"},
                                                   str(marketplace)))
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(RuntimeError, match="destination is empty after copy"):
        asyncio.run(copy_plugin_to_versioned_cache(str(empty), "q@m", "1"))


def test_zip_cache_mode_caches_a_zip_and_removes_the_directory(tmp_path: Path, monkeypatch: Any):
    import zipfile

    monkeypatch.setenv("TDDAGENTS_PLUGIN_USE_ZIP_CACHE", "1")
    src = tmp_path / "src"
    _write(src / "f", "payload")
    _write(src / ".git" / "HEAD", "ref")
    result = asyncio.run(copy_plugin_to_versioned_cache(str(src), "p@m", "1"))
    assert result == get_versioned_zip_cache_path("p@m", "1")
    assert not os.path.exists(get_versioned_cache_path("p@m", "1"))
    with zipfile.ZipFile(result) as archive:
        assert archive.namelist() == ["f"]
        assert archive.read("f") == b"payload"
    # An existing zip is returned as is.
    zip_path = get_versioned_zip_cache_path("p@m", "2")
    _write(Path(zip_path), "zip")
    assert asyncio.run(copy_plugin_to_versioned_cache(str(src), "p@m", "2")) == zip_path


# ── sources ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "https://github.com/o/r.git", "http://host/x", "file:///tmp/repo", "git@github.com:o/r.git",
])
def test_validate_git_url_accepts(url: str):
    assert validate_git_url(url) == url


@pytest.mark.parametrize("url", ["ssh://git@host/r", "not a url", "ftp://host/r", "user@host:r"])
def test_validate_git_url_rejects_everything_else_with_one_message(url: str):
    with pytest.raises(ValueError, match=f"^Invalid git URL: {url}$"):
        validate_git_url(url)


def test_github_urls_are_ssh_unless_remote(monkeypatch: Any):
    assert resolve_git_subdir_url("o/r") == "git@github.com:o/r.git"
    monkeypatch.setenv("TDDAGENTS_REMOTE", "1")
    assert resolve_git_subdir_url("o/r") == "https://github.com/o/r.git"
    assert resolve_git_subdir_url("https://example.com/r.git") == "https://example.com/r.git"


def test_install_from_github_rejects_a_bad_repo(tmp_path: Path):
    with pytest.raises(ValueError, match="Invalid GitHub repository format: a/b/c. Expected format: owner/repo"):
        asyncio.run(install_from_github("a/b/c", str(tmp_path / "t")))


def test_install_from_npm_installs_once_into_the_npm_cache(tmp_path: Path, monkeypatch: Any):
    calls: list[tuple[str, list[str]]] = []

    async def fake_exec(file: str, args: list[str], **_: Any) -> ExecResult:
        calls.append((file, list(args)))
        prefix = args[args.index("--prefix") + 1]
        _write(Path(prefix) / "node_modules" / "@s" / "pkg" / "index.js", "js")
        return ExecResult(stdout="", stderr="", code=0)

    monkeypatch.setattr(plugin_loader, "exec_file_no_throw_with_cwd", fake_exec)
    target = tmp_path / "t1"
    asyncio.run(install_from_npm("@s/pkg", str(target), registry="https://r.example", version="1.2.3"))
    npm_cache = os.path.join(get_plugins_directory(), "npm-cache")
    assert calls == [("npm", ["install", "@s/pkg@1.2.3", "--prefix", npm_cache, "--registry", "https://r.example"])]
    assert (target / "index.js").read_text() == "js"
    asyncio.run(install_from_npm("@s/pkg", str(tmp_path / "t2")))
    assert len(calls) == 1


def test_install_from_npm_failure_raises(tmp_path: Path, monkeypatch: Any):
    async def failing(file: str, args: list[str], **_: Any) -> ExecResult:
        return ExecResult(stdout="", stderr="E404", code=1)

    monkeypatch.setattr(plugin_loader, "exec_file_no_throw_with_cwd", failing)
    with pytest.raises(RuntimeError, match="Failed to install npm package: E404"):
        asyncio.run(install_from_npm("pkg", str(tmp_path / "t")))


def test_git_clone_a_ref_and_a_sha(tmp_path: Path):
    repo = tmp_path / "repo"
    first = make_repo(repo, {"a.txt": "one"})
    run_git(repo, "tag", "v1")
    commit_more(repo, {"a.txt": "two"})
    url = f"file://{repo}"
    asyncio.run(git_clone(url, str(tmp_path / "head")))
    assert (tmp_path / "head" / "a.txt").read_text() == "two"
    asyncio.run(git_clone(url, str(tmp_path / "tag"), ref="v1"))
    assert (tmp_path / "tag" / "a.txt").read_text() == "one"
    asyncio.run(git_clone(url, str(tmp_path / "sha"), sha=first))
    assert (tmp_path / "sha" / "a.txt").read_text() == "one"
    with pytest.raises(RuntimeError, match="Failed to clone repository"):
        asyncio.run(git_clone(f"file://{tmp_path / 'nope'}", str(tmp_path / "x")))


def test_install_from_git_subdir_extracts_only_the_subdirectory(tmp_path: Path):
    repo = tmp_path / "mono"
    sha = make_repo(repo, {"tools/plug/.tddagents-plugin/plugin.json": manifest("plug"), "other/x.txt": "x"})
    target = tmp_path / "target"
    resolved = asyncio.run(install_from_git_subdir(f"file://{repo}", str(target), "tools/plug"))
    assert resolved == sha
    assert (target / ".tddagents-plugin" / "plugin.json").exists()
    assert not (target / "other").exists()
    assert not (tmp_path / "target.clone").exists()
    with pytest.raises(RuntimeError, match="Subdirectory 'missing' not found in repository"):
        asyncio.run(install_from_git_subdir(f"file://{repo}", str(tmp_path / "t2"), "missing"))
    assert not (tmp_path / "t2.clone").exists()


def test_install_from_local_requires_the_source_and_drops_git(tmp_path: Path):
    with pytest.raises(RuntimeError, match="Source path does not exist"):
        asyncio.run(install_from_local(str(tmp_path / "none"), str(tmp_path / "t")))
    _write(tmp_path / "src" / ".git" / "HEAD")
    _write(tmp_path / "src" / "f")
    asyncio.run(install_from_local(str(tmp_path / "src"), str(tmp_path / "t")))
    assert (tmp_path / "t" / "f").exists() and not (tmp_path / "t" / ".git").exists()


@pytest.mark.parametrize(("source", "prefix"), [
    ("./x", "local"),
    ({"source": "npm", "package": "p"}, "npm"),
    ({"source": "pip", "package": "p"}, "pip"),
    ({"source": "github", "repo": "o/r"}, "github"),
    ({"source": "url", "url": "u"}, "git"),
    ({"source": "git-subdir", "url": "u", "path": "p"}, "subdir"),
    ({"source": "other"}, "unknown"),
])
def test_temporary_cache_names(source: Any, prefix: str):
    name = generate_temporary_cache_name_for_plugin(source)
    parts = name.split("_")
    assert parts[:2] == ["temp", prefix]
    assert parts[2].isdigit() and len(parts[3]) == 6


# ── cache_plugin and the manifest ─────────────────────────────────────────────

def test_cache_plugin_reads_the_manifest_and_names_the_cache_after_it(tmp_path: Path):
    src = tmp_path / "src"
    _write(src / ".tddagents-plugin" / "plugin.json", manifest("My Plug".replace(" ", "-"), version="1.0", extra=1))
    cached = asyncio.run(cache_plugin(str(src)))
    assert cached.path == os.path.join(get_plugin_cache_path(), "My-Plug")
    assert cached.manifest == {"name": "My-Plug", "version": "1.0"}
    assert cached.git_commit_sha is None
    # Caching again replaces the old copy; no temp directory is left behind.
    asyncio.run(cache_plugin(str(src)))
    assert sorted(os.listdir(get_plugin_cache_path())) == ["My-Plug"]


def test_cache_plugin_legacy_manifest_then_given_then_default(tmp_path: Path):
    legacy = tmp_path / "legacy"
    _write(legacy / "plugin.json", manifest("leg"))
    assert asyncio.run(cache_plugin(str(legacy))).manifest["name"] == "leg"
    bare = tmp_path / "bare"
    _write(bare / "f")
    given = asyncio.run(cache_plugin(str(bare), manifest={"name": "given", "source": "./x"}))
    assert given.manifest["name"] == "given"
    # Upstream's default manifest is named after the temp directory, so the "remove the old
    # copy" step deletes the fresh one and the rename fails: ported as is (every real caller
    # passes the marketplace entry as `manifest`).
    with pytest.raises(FileNotFoundError):
        asyncio.run(cache_plugin(str(bare)))


def test_cache_plugin_invalid_and_corrupt_manifests_raise_and_clean_nothing_up(tmp_path: Path):
    bad = tmp_path / "bad"
    _write(bad / ".tddagents-plugin" / "plugin.json", json.dumps({"name": "has space"}))
    with pytest.raises(RuntimeError, match="invalid manifest file at .*Validation errors: name: Plugin name cannot"):
        asyncio.run(cache_plugin(str(bad)))
    corrupt = tmp_path / "corrupt"
    _write(corrupt / ".tddagents-plugin" / "plugin.json", "{nope")
    with pytest.raises(RuntimeError, match="corrupt manifest file at .*JSON parse error"):
        asyncio.run(cache_plugin(str(corrupt)))


def test_cache_plugin_failed_install_removes_the_temp_dir():
    with pytest.raises(RuntimeError, match="Python package plugins are not yet supported"):
        asyncio.run(cache_plugin({"source": "pip", "package": "x"}))
    with pytest.raises(RuntimeError, match="Unsupported plugin source type"):
        asyncio.run(cache_plugin({"source": "weird"}))
    assert os.listdir(get_plugin_cache_path()) == []


def test_cache_plugin_from_git_subdir_keeps_the_resolved_sha(tmp_path: Path):
    repo = tmp_path / "mono"
    sha = make_repo(repo, {"p/.tddagents-plugin/plugin.json": manifest("subp")})
    cached = asyncio.run(cache_plugin({"source": "git-subdir", "url": f"file://{repo}", "path": "p"}))
    assert cached.git_commit_sha == sha
    assert cached.path.endswith("subp")


def test_load_plugin_manifest(tmp_path: Path):
    missing = asyncio.run(load_plugin_manifest(str(tmp_path / "none.json"), "p", "git:repo"))
    assert missing == {"name": "p", "description": "Plugin from git:repo"}
    good = _write(tmp_path / "good.json", manifest("p", keywords=["a"], unknown=True))
    assert asyncio.run(load_plugin_manifest(str(good), "p", "s")) == {"name": "p", "keywords": ["a"]}
    bad = _write(tmp_path / "bad.json", json.dumps({"version": 1}))
    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(load_plugin_manifest(str(bad), "p", "s"))
    assert "Plugin p has an invalid manifest file at" in str(excinfo.value)
    assert "\n\nValidation errors: " in str(excinfo.value)
    assert "version: Invalid input: expected string, received number" in str(excinfo.value)
    corrupt = _write(tmp_path / "corrupt.json", "[")
    with pytest.raises(RuntimeError, match="corrupt manifest file at .*\n\nJSON parse error"):
        asyncio.run(load_plugin_manifest(str(corrupt), "p", "s"))
