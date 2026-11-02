"""Tests for app/utils/plugins/zip_cache.py and zip_cache_adapters.py (offline, temp directories)."""

from __future__ import annotations

import asyncio
import io
import json
import os
import stat
import zipfile
from pathlib import Path
from typing import Any

import pytest

from app.utils.plugins import marketplace_manager as mm
from app.utils.plugins import zip_cache as zc
from app.utils.plugins import zip_cache_adapters as zca
from app.utils.settings.settings import SettingsLocations


@pytest.fixture(autouse=True)
def _fresh_state():
    zc.reset_session_plugin_cache()
    mm.clear_marketplaces_cache()
    yield
    zc.reset_session_plugin_cache()
    mm.clear_marketplaces_cache()


@pytest.fixture
def zip_cache_dir(tmp_path: Path, monkeypatch: Any) -> Path:
    cache = tmp_path / "zipcache"
    monkeypatch.setenv("TDDAGENTS_PLUGIN_USE_ZIP_CACHE", "1")
    monkeypatch.setenv("TDDAGENTS_PLUGIN_CACHE_DIR", str(cache))
    return cache


def write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_paths_need_the_switch_and_the_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("TDDAGENTS_PLUGIN_USE_ZIP_CACHE", raising=False)
    monkeypatch.setenv("TDDAGENTS_PLUGIN_CACHE_DIR", str(tmp_path))
    assert zc.get_plugin_zip_cache_path() is None
    with pytest.raises(RuntimeError, match="^Plugin zip cache is not enabled$"):
        zc.get_zip_cache_plugins_dir()
    monkeypatch.setenv("TDDAGENTS_PLUGIN_USE_ZIP_CACHE", "1")
    assert zc.get_plugin_zip_cache_path() == str(tmp_path)
    assert zc.get_zip_cache_known_marketplaces_path() == str(tmp_path / "known_marketplaces.json")
    assert zc.get_zip_cache_installed_plugins_path() == str(tmp_path / "installed_plugins.json")
    assert zc.get_zip_cache_marketplaces_dir() == str(tmp_path / "marketplaces")
    assert zc.get_zip_cache_plugins_dir() == str(tmp_path / "plugins")
    monkeypatch.setenv("TDDAGENTS_PLUGIN_CACHE_DIR", "~/zc")
    assert zc.get_plugin_zip_cache_path() == os.path.join(str(Path.home()), "zc")


def test_zip_round_trip_keeps_exec_bits_resolves_file_links_and_skips_git(tmp_path: Path):
    src = tmp_path / "src"
    script = write(src / "bin" / "run.sh", "#!/bin/sh\n")
    script.chmod(0o755)
    write(src / "data.txt", "data")
    write(src / ".git" / "HEAD", "ref")
    os.symlink(src / "data.txt", src / "link.txt")
    os.symlink(src / "bin", src / "bin-link")
    os.symlink(src / "missing", src / "broken")
    data = run(zc.create_zip_from_directory(str(src)))
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert sorted(archive.namelist()) == ["bin/run.sh", "data.txt", "link.txt"]
        assert archive.getinfo("bin/run.sh").create_system == 3
    out = tmp_path / "out"
    zip_path = tmp_path / "p.zip"
    zip_path.write_bytes(data)
    run(zc.extract_zip_to_directory(str(zip_path), str(out)))
    assert (out / "link.txt").read_text() == "data" and not (out / "link.txt").is_symlink()
    assert stat.S_IMODE((out / "bin" / "run.sh").stat().st_mode) == 0o755
    assert stat.S_IMODE((out / "data.txt").stat().st_mode) & 0o111 == 0


def test_convert_directory_to_zip_in_place(tmp_path: Path):
    src = write(tmp_path / "d" / "f", "payload").parent
    target = tmp_path / "cache" / "p" / "1.zip"
    run(zc.convert_directory_to_zip_in_place(str(src), str(target)))
    assert not src.exists()
    with zipfile.ZipFile(target) as archive:
        assert archive.read("f") == b"payload"
    assert [n for n in os.listdir(target.parent) if n.startswith(".")] == []


def test_atomic_write_leaves_no_temporary_file_on_failure(tmp_path: Path, monkeypatch):
    target = tmp_path / "x" / "a.json"
    run(zc.atomic_write_to_zip_cache(str(target), "text"))
    run(zc.atomic_write_to_zip_cache(str(target), b"bytes"))
    assert target.read_bytes() == b"bytes"

    def boom(src: str, dst: str) -> None:
        raise OSError("rename failed")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="rename failed"):
        run(zc.atomic_write_to_zip_cache(str(target), "new"))
    assert os.listdir(target.parent) == ["a.json"]


def test_session_cache_is_created_once_and_cleaned_up():
    first = run(zc.get_session_plugin_cache_path())
    assert os.path.basename(first).startswith("tddagents-plugin-session-") and os.path.isdir(first)
    assert run(zc.get_session_plugin_cache_path()) == first
    run(zc.cleanup_session_plugin_cache())
    assert not os.path.exists(first)
    run(zc.cleanup_session_plugin_cache())
    assert run(zc.get_session_plugin_cache_path()) != first
    run(zc.cleanup_session_plugin_cache())


def test_marketplace_json_path_and_supported_sources():
    assert zc.get_marketplace_json_relative_path("my team/x") == os.path.join("marketplaces", "my-team-x.json")
    assert [s for s in ("github", "git", "url", "settings", "directory", "file", "npm")
            if zc.is_marketplace_source_supported_by_zip_cache({"source": s})] == ["github", "git", "url", "settings"]


def test_sync_marketplaces_saves_their_json_and_merges_the_known_file(tmp_path, zip_cache_dir, monkeypatch):
    # In zip-cache mode the plugins directory is the zip cache directory itself (upstream's
    # CLAUDE_CODE_PLUGIN_CACHE_DIR), so the registered marketplace is already in its known file.
    locations = SettingsLocations(project_root=tmp_path, home=tmp_path / "home", managed_dir=tmp_path / "managed")
    root = tmp_path / "mkt"
    manifest = {"name": "team", "owner": {"name": "O"}, "plugins": []}
    write(root / ".tddagents-plugin" / "marketplace.json", json.dumps(manifest))
    run(mm.add_marketplace_source({"source": "directory", "path": str(root)}, locations))
    assert mm.get_known_marketplaces_file() == str(zip_cache_dir / "known_marketplaces.json")
    run(zca.sync_marketplaces_to_zip_cache())
    assert json.loads((zip_cache_dir / "marketplaces" / "team.json").read_text()) == manifest
    assert run(zca.read_marketplace_json("team")) == manifest
    assert run(zca.read_marketplace_json("absent")) is None
    team = json.loads((zip_cache_dir / "known_marketplaces.json").read_text())["team"]

    # Entries cached by an earlier container are kept; the live configuration wins on a clash.
    old = {"source": {"source": "github", "repo": "o/r"}, "installLocation": "/gone",
           "lastUpdated": "2026-01-01T00:00:00.000Z"}
    write(zip_cache_dir / "known_marketplaces.json", json.dumps({"old": old, "team": {**old, "installLocation": "/x"}}))

    async def live() -> dict[str, Any]:
        return {"team": team}

    monkeypatch.setattr(zca, "load_known_marketplaces_config_safe", live)
    run(zca.sync_marketplaces_to_zip_cache())
    assert json.loads((zip_cache_dir / "known_marketplaces.json").read_text()) == {"old": old, "team": team}
    assert set(run(zca.read_zip_cache_known_marketplaces())) == {"old", "team"}


def test_invalid_zip_cache_files_read_as_empty(zip_cache_dir):
    assert run(zca.read_zip_cache_known_marketplaces()) == {}
    write(zip_cache_dir / "known_marketplaces.json", json.dumps({"x": {"source": 3}}))
    assert run(zca.read_zip_cache_known_marketplaces()) == {}
    write(zip_cache_dir / "marketplaces" / "bad.json", json.dumps({"name": "bad"}))
    assert run(zca.read_marketplace_json("bad")) is None


def test_saving_reads_a_url_marketplace_file_itself(tmp_path, zip_cache_dir):
    location = write(tmp_path / "downloaded.json", '{"name": "u"}')
    run(zca.save_marketplace_json_to_zip_cache("u", str(location)))
    assert (zip_cache_dir / "marketplaces" / "u.json").read_text() == '{"name": "u"}'
    run(zca.save_marketplace_json_to_zip_cache("none", str(tmp_path / "nowhere")))
    assert not (zip_cache_dir / "marketplaces" / "none.json").exists()
