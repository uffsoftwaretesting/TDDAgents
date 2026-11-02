"""Tests for app/utils/plugins/reconciler.py (offline: directory marketplaces and settings files)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from app.utils.plugins import marketplace_manager as mm
from app.utils.plugins import reconciler as rc
from app.utils.settings.settings import SettingsLocations


@pytest.fixture(autouse=True)
def _fresh_cache():
    mm.clear_marketplaces_cache()
    yield
    mm.clear_marketplaces_cache()


@pytest.fixture
def locations(tmp_path: Path) -> SettingsLocations:
    project = tmp_path / "project"
    project.mkdir()
    managed = tmp_path / "managed"
    managed.mkdir()
    return SettingsLocations(project_root=project, home=tmp_path / "home", managed_dir=managed)


def make_marketplace(root: Path, name: str) -> Path:
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    manifest = {"name": name, "owner": {"name": "Owner"}, "plugins": [{"name": "p", "source": "./p"}]}
    (root / ".claude-plugin" / "marketplace.json").write_text(json.dumps(manifest))
    return root


def declare(locations: SettingsLocations, marketplaces: dict[str, Any]) -> None:
    path = locations.project_root / ".tddagents" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"extraKnownMarketplaces": marketplaces}))


def directory(path: Path | str) -> dict[str, Any]:
    return {"source": "directory", "path": str(path)}


# ── normalize / diff ──────────────────────────────────────────────────────────

def test_normalize_resolves_relative_local_paths_against_the_project(tmp_path: Path):
    project = str(tmp_path / "proj")
    relative = {"source": "directory", "path": "./mkt"}
    assert rc.normalize_source(relative, project) == directory(tmp_path / "proj" / "mkt")
    assert rc.normalize_source({"source": "file", "path": "../m.json"}, project) == {
        "source": "file", "path": str(tmp_path / "m.json")}
    # Absolute paths and remote sources are left alone.
    assert rc.normalize_source(directory("/abs"), project) == directory("/abs")
    assert rc.normalize_source({"source": "github", "repo": "o/r"}, project) == {"source": "github", "repo": "o/r"}


def test_diff_classifies_missing_changed_up_to_date_and_fallback(tmp_path: Path):
    declared: dict[str, Any] = {
        "absent": {"source": directory("/a")},
        "same": {"source": {"source": "directory", "path": "./same"}},
        "moved": {"source": directory("/new")},
        "fallback": {"source": {"source": "github", "repo": "o/official"}, "sourceIsFallback": True},
    }
    materialized = {
        "same": {"source": directory(tmp_path / "same")},
        "moved": {"source": directory("/old")},
        "fallback": {"source": directory("/a-seed-copy")},
    }
    diff = rc.diff_marketplaces(declared, materialized, project_root=str(tmp_path))
    assert diff.missing == ["absent"]
    assert diff.up_to_date == ["same", "fallback"]
    assert diff.source_changed == [
        rc.SourceChange(name="moved", declared_source=directory("/new"), materialized_source=directory("/old"))
    ]


# ── reconcile ─────────────────────────────────────────────────────────────────

def test_nothing_declared_is_an_empty_result(locations: SettingsLocations):
    assert asyncio.run(rc.reconcile_marketplaces(locations)) == rc.ReconcileResult()


def test_reconcile_installs_missing_then_is_idempotent(tmp_path: Path, locations: SettingsLocations):
    root = make_marketplace(tmp_path / "mkt", "team")
    declare(locations, {"team": {"source": directory(root)}})
    events: list[rc.ReconcileProgressEvent] = []
    result = asyncio.run(rc.reconcile_marketplaces(locations, rc.ReconcileOptions(on_progress=events.append)))
    assert (result.installed, result.updated, result.failed, result.up_to_date) == (["team"], [], [], [])
    assert events == [
        rc.ReconcileProgressEvent(type="installing", name="team", action="install", index=1, total=1),
        rc.ReconcileProgressEvent(type="installed", name="team", already_materialized=False),
    ]
    assert asyncio.run(mm.load_known_marketplaces_config())["team"]["source"] == directory(root)
    again = asyncio.run(rc.reconcile_marketplaces(locations))
    assert (again.installed, again.up_to_date) == ([], ["team"])


def test_reconcile_updates_a_changed_source_and_reports_failures(tmp_path: Path, locations: SettingsLocations):
    old = make_marketplace(tmp_path / "old", "team")
    new = make_marketplace(tmp_path / "new", "team")
    asyncio.run(mm.add_marketplace_source(directory(old), locations))
    declare(locations, {"team": {"source": directory(new)}, "ghost": {"source": directory(tmp_path / "nowhere")}})
    events: list[rc.ReconcileProgressEvent] = []
    result = asyncio.run(rc.reconcile_marketplaces(locations, rc.ReconcileOptions(on_progress=events.append)))
    assert result.updated == ["team"]
    assert asyncio.run(mm.load_known_marketplaces_config())["team"]["source"] == directory(new)
    # A missing marketplace whose path does not exist is attempted and reported, never skipped.
    assert [f.name for f in result.failed] == ["ghost"]
    assert "ghost" in [e.name for e in events if e.type == "failed"]


def test_an_update_to_a_dead_local_path_keeps_the_materialized_entry(tmp_path: Path, locations: SettingsLocations):
    old = make_marketplace(tmp_path / "old", "team")
    asyncio.run(mm.add_marketplace_source(directory(old), locations))
    declare(locations, {"team": {"source": directory(tmp_path / "gone")}})
    result = asyncio.run(rc.reconcile_marketplaces(locations))
    assert (result.skipped, result.updated, result.failed) == (["team"], [], [])
    assert asyncio.run(mm.load_known_marketplaces_config())["team"]["source"] == directory(old)


def test_skip_option_and_a_corrupt_state_file(tmp_path: Path, locations: SettingsLocations):
    root = make_marketplace(tmp_path / "mkt", "team")
    declare(locations, {"team": {"source": directory(root)}})
    state = Path(mm.get_known_marketplaces_file())
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("{not json")
    seen: list[tuple[str, dict[str, Any]]] = []

    def skip(name: str, source: dict[str, Any]) -> bool:
        seen.append((name, source))
        return True

    result = asyncio.run(rc.reconcile_marketplaces(locations, rc.ReconcileOptions(skip=skip)))
    # The corrupt file reconciles as empty: the declaration is missing, and skipped by request.
    assert (result.skipped, result.installed) == (["team"], [])
    assert seen == [("team", directory(root))]
