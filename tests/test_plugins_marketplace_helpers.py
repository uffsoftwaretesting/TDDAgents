"""Tests for app/utils/plugins/marketplace_helpers.py: display helpers and the marketplace policy."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import pytest

from app.utils.plugins import git_availability
from app.utils.plugins import marketplace_helpers as mh
from app.utils.plugins import marketplace_manager as mm
from app.utils.settings.settings import SettingsLocations
from app.utils.string_utils import plural


@pytest.fixture(autouse=True)
def _fresh_cache():
    mm.clear_marketplaces_cache()
    git_availability.clear_git_availability_cache()
    yield
    mm.clear_marketplaces_cache()
    git_availability.clear_git_availability_cache()


@pytest.fixture
def locations(tmp_path: Path) -> SettingsLocations:
    project = tmp_path / "project"
    project.mkdir()
    managed = tmp_path / "managed"
    managed.mkdir()
    return SettingsLocations(project_root=project, home=tmp_path / "home", managed_dir=managed)


def policy(locations: SettingsLocations, **settings: Any) -> None:
    (locations.managed_dir / "managed-settings.json").write_text(json.dumps(settings))


GH = {"source": "github", "repo": "acme/tools"}


# ── display ───────────────────────────────────────────────────────────────────

def test_plural():
    assert (plural(1, "plugin"), plural(0, "plugin"), plural(2, "plugin")) == ("plugin", "plugins", "plugins")
    assert plural(3, "entry", "entries") == "entries"


def test_format_failure_details_shows_two_then_counts():
    failures = [{"name": "a", "reason": "r1"}, {"name": "b", "error": "e2"}, {"name": "c"}, {"name": "d"}]
    assert mh.format_failure_details(failures, True) == "a (r1); b (e2) and 2 more"
    assert mh.format_failure_details(failures[2:], True) == "c (unknown error); d (unknown error)"
    assert mh.format_failure_details(failures[:3], False) == "a, b and 1 more"


@pytest.mark.parametrize(("source", "display", "formatted"), [
    (GH, "acme/tools", "github:acme/tools"),
    ({**GH, "ref": "v1"}, "acme/tools", "github:acme/tools@v1"),
    ({"source": "git", "url": "https://h/r.git", "ref": "main"}, "https://h/r.git", "git:https://h/r.git@main"),
    ({"source": "url", "url": "https://h/m.json"}, "https://h/m.json", "https://h/m.json"),
    ({"source": "directory", "path": "/d"}, "/d", "dir:/d"),
    ({"source": "file", "path": "/f.json"}, "/f.json", "file:/f.json"),
    ({"source": "npm", "package": "pkg"}, "Unknown source", "npm:pkg"),
    ({"source": "settings", "name": "s", "plugins": [{}]}, "settings:s", "settings:s (1 plugin)"),
    ({"source": "settings", "name": "s", "plugins": [{}, {}]}, "settings:s", "settings:s (2 plugins)"),
    ({"source": "hostPattern", "hostPattern": "^h$"}, "Unknown source", "hostPattern:^h$"),
    ({"source": "pathPattern", "pathPattern": "^/p"}, "Unknown source", "pathPattern:^/p"),
    ({"source": "other"}, "Unknown source", "unknown source"),
])
def test_source_display_forms(source, display, formatted):
    assert mh.get_marketplace_source_display(source) == display
    assert mh.format_source_for_display(source) == formatted


def test_loading_error_messages():
    one = [mh.MarketplaceFailure(name="a", error="boom")]
    two = [*one, mh.MarketplaceFailure(name="b", error="bang")]
    assert mh.format_marketplace_loading_errors([], 0) is None
    assert mh.format_marketplace_loading_errors(one, 1) == mh.LoadingErrorMessage(
        type="warning", message="Warning: Failed to load marketplace 'a': boom")
    assert mh.format_marketplace_loading_errors(two, 3) == mh.LoadingErrorMessage(
        type="warning", message="Warning: Failed to load 2 marketplaces: a, b")
    assert mh.format_marketplace_loading_errors(two, 0) == mh.LoadingErrorMessage(
        type="error", message="Failed to load all marketplaces. Errors: a: boom; b: bang")
    assert mh.create_plugin_id("p", "m") == "p@m"


# ── source matching ───────────────────────────────────────────────────────────

def test_are_sources_equal_treats_empty_ref_and_path_as_absent():
    assert mh.are_sources_equal({**GH, "ref": ""}, GH)
    assert not mh.are_sources_equal({**GH, "ref": "v1"}, GH)
    assert mh.are_sources_equal({"source": "git", "url": "u", "path": ""}, {"source": "git", "url": "u"})
    assert not mh.are_sources_equal({"source": "git", "url": "u"}, {"source": "url", "url": "u"})
    assert mh.are_sources_equal({"source": "npm", "package": "p"}, {"source": "npm", "package": "p"})
    plugins = [{"name": "x", "source": "./x"}]
    assert mh.are_sources_equal({"source": "settings", "name": "s", "plugins": plugins},
                                {"source": "settings", "name": "s", "plugins": list(plugins)})
    assert not mh.are_sources_equal({"source": "settings", "name": "s", "plugins": plugins},
                                    {"source": "settings", "name": "s", "plugins": []})
    assert not mh.are_sources_equal({"source": "hostPattern", "hostPattern": "x"},
                                    {"source": "hostPattern", "hostPattern": "x"})


@pytest.mark.parametrize(("source", "host"), [
    (GH, "github.com"),
    ({"source": "git", "url": "git@gitlab.example.com:group/repo.git"}, "gitlab.example.com"),
    ({"source": "git", "url": "https://Git.Example.com/r.git"}, "git.example.com"),
    ({"source": "url", "url": "https://cdn.example.com/m.json"}, "cdn.example.com"),
    ({"source": "url", "url": "not a url"}, None),
    ({"source": "directory", "path": "/x"}, None),
])
def test_extract_host_from_source(source, host):
    assert mh.extract_host_from_source(source) == host


def test_github_repo_from_git_urls():
    assert mh.extract_github_repo_from_git_url("git@github.com:acme/tools.git") == "acme/tools"
    assert mh.extract_github_repo_from_git_url("https://github.com/acme/tools") == "acme/tools"
    assert mh.extract_github_repo_from_git_url("https://github.com/acme/tools.git") == "acme/tools"
    assert mh.extract_github_repo_from_git_url("https://gitlab.com/acme/tools") is None


def test_blocklist_constraints_are_wildcards_unless_set():
    assert mh.blocked_constraint_matches(None, "v1")
    assert mh.blocked_constraint_matches("", "v1")
    assert mh.blocked_constraint_matches("v1", "v1")
    assert not mh.blocked_constraint_matches("v1", None)
    equivalent = mh.are_sources_equivalent_for_blocklist
    assert equivalent({**GH, "ref": "v2"}, GH)
    assert not equivalent(GH, {**GH, "ref": "v2"})
    assert equivalent({"source": "git", "url": "git@github.com:acme/tools.git"}, GH)
    assert equivalent(GH, {"source": "git", "url": "https://github.com/acme/tools.git"})
    assert not equivalent({"source": "git", "url": "https://gitlab.com/acme/tools.git"}, GH)
    assert equivalent({"source": "settings", "name": "s", "plugins": [1]}, {"source": "settings", "name": "s"})
    assert equivalent({"source": "url", "url": "u"}, {"source": "url", "url": "u"})
    assert not equivalent({"source": "file", "path": "/a"}, {"source": "file", "path": "/b"})


# ── policy ────────────────────────────────────────────────────────────────────

def test_no_policy_allows_everything(locations: SettingsLocations):
    assert mh.get_strict_known_marketplaces(locations) is None
    assert mh.get_blocked_marketplaces(locations) is None
    assert mh.get_plugin_trust_message(locations) is None
    assert mh.get_host_patterns_from_allowlist(locations) == []
    assert mh.is_source_allowed_by_policy(GH, locations)
    assert not mh.is_source_in_blocklist(GH, locations)


def test_blocklist_wins_over_the_allowlist(locations: SettingsLocations):
    policy(locations, blockedMarketplaces=[GH], strictKnownMarketplaces=[GH], pluginTrustMessage="Ask IT")
    assert mh.is_source_in_blocklist({"source": "git", "url": "git@github.com:acme/tools.git"}, locations)
    assert not mh.is_source_allowed_by_policy(GH, locations)
    assert mh.get_plugin_trust_message(locations) == "Ask IT"


def test_allowlist_by_exact_source_host_pattern_and_path_pattern(locations: SettingsLocations, caplog):
    policy(locations, strictKnownMarketplaces=[
        GH,
        {"source": "hostPattern", "hostPattern": r"^git\.corp\.example$"},
        {"source": "pathPattern", "pathPattern": "^/opt/marketplaces/"},
        {"source": "hostPattern", "hostPattern": "("},
    ])
    allowed = mh.is_source_allowed_by_policy
    assert allowed(GH, locations)
    assert not allowed({**GH, "ref": "dev"}, locations)
    assert allowed({"source": "git", "url": "git@git.corp.example:team/m.git"}, locations)
    assert not allowed({"source": "url", "url": "https://evil.example/m.json"}, locations)
    assert allowed({"source": "directory", "path": "/opt/marketplaces/team"}, locations)
    assert not allowed({"source": "directory", "path": "/home/me/m"}, locations)
    assert mh.get_host_patterns_from_allowlist(locations) == [r"^git\.corp\.example$", "("]
    with caplog.at_level(logging.ERROR, logger="TDDOrchestrator.Plugins"):
        assert not mh.does_source_match_host_pattern(GH, {"source": "hostPattern", "hostPattern": "("})
    assert "Invalid hostPattern regex: (" in caplog.text
    assert not mh.does_source_match_path_pattern(GH, {"source": "pathPattern", "pathPattern": "."})


# ── loading and the empty-state reason ────────────────────────────────────────

def test_graceful_load_skips_blocked_and_collects_failures(tmp_path: Path, locations: SettingsLocations):
    root = tmp_path / "good"
    (root / ".tddagents-plugin").mkdir(parents=True)
    (root / ".tddagents-plugin" / "marketplace.json").write_text(json.dumps(
        {"name": "good", "owner": {"name": "O"}, "plugins": []}))
    asyncio.run(mm.add_marketplace_source({"source": "directory", "path": str(root)}, locations))
    config: dict[str, Any] = dict(asyncio.run(mm.load_known_marketplaces_config()))
    config["broken"] = {"source": {"source": "directory", "path": "/nowhere"}, "installLocation": "/nowhere",
                        "lastUpdated": "t"}
    config["blocked"] = {"source": GH, "installLocation": "/x", "lastUpdated": "t"}
    asyncio.run(mm.save_known_marketplaces_config(config))
    policy(locations, blockedMarketplaces=[GH])
    result = asyncio.run(mh.load_marketplaces_with_graceful_degradation(config, locations))
    assert [(m.name, m.data is not None) for m in result.marketplaces] == [("good", True), ("broken", False)]
    assert [f.name for f in result.failures] == ["broken"]


@pytest.mark.parametrize(("git", "allowlist", "configured", "failed", "reason"), [
    (False, None, 1, 0, "git-not-installed"),
    (True, [], 1, 0, "all-blocked-by-policy"),
    (True, [GH], 0, 0, "policy-restricts-sources"),
    (True, None, 0, 0, "no-marketplaces-configured"),
    (True, None, 2, 2, "all-marketplaces-failed"),
    (True, [GH], 2, 1, "all-plugins-installed"),
])
def test_empty_marketplace_reason(locations, monkeypatch, git, allowlist, configured, failed, reason):
    monkeypatch.setattr(
        "app.utils.plugins.git_availability.shutil.which", lambda cmd: "/usr/bin/" + cmd if git else None
    )
    if allowlist is not None:
        policy(locations, strictKnownMarketplaces=allowlist)
    assert asyncio.run(mh.detect_empty_marketplace_reason(
        configured_marketplace_count=configured, failed_marketplace_count=failed, locations=locations)) == reason
