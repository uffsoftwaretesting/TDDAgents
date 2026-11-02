"""The plugin/marketplace linter (`app/utils/plugins/validate_plugin.py`), upstream's `validatePlugin.ts`."""

from __future__ import annotations

import asyncio
import json
import os

from app.utils.plugins import validate_plugin as vp


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    return str(path)


def _paths(result):
    return [e.path for e in result.errors]


GOOD_MANIFEST = {"name": "my-plugin", "version": "1.0.0", "description": "d", "author": {"name": "a"}}


# ── plugin.json ───────────────────────────────────────────────────────────────

def test_clean_plugin_manifest_has_no_errors_or_warnings(tmp_path):
    path = _write(tmp_path / ".tddagents-plugin" / "plugin.json", GOOD_MANIFEST)
    result = asyncio.run(vp.validate_plugin_manifest(path))
    assert result.success is True
    assert result.errors == [] and result.warnings == []
    assert result.file_type == "plugin"
    assert result.file_path == os.path.abspath(path)


def test_missing_file_directory_and_bad_json(tmp_path):
    missing = asyncio.run(vp.validate_plugin_manifest(str(tmp_path / "nope.json")))
    assert missing.errors == [vp.ValidationError("file", f"File not found: {tmp_path / 'nope.json'}", "ENOENT")]
    is_dir = asyncio.run(vp.validate_plugin_manifest(str(tmp_path)))
    assert is_dir.errors[0].message == f"Path is not a file: {tmp_path}"
    assert is_dir.errors[0].code == "EISDIR"
    bad = asyncio.run(vp.validate_plugin_manifest(_write(tmp_path / "p.json", "{oops")))
    assert bad.success is False
    assert bad.errors[0].path == "json"
    assert bad.errors[0].message.startswith("Invalid JSON syntax: ")


def test_marketplace_only_fields_warn_and_are_not_unrecognized(tmp_path):
    path = _write(tmp_path / "plugin.json", {**GOOD_MANIFEST, "category": "x", "tags": ["t"]})
    result = asyncio.run(vp.validate_plugin_manifest(path))
    assert result.success is True
    assert [w.path for w in result.warnings] == ["category", "tags"]
    assert result.warnings[0].message == (
        "Field 'category' belongs in the marketplace entry (marketplace.json), not plugin.json. "
        "It's harmless here but unused — TDDAgents ignores it at load time."
    )


def test_unknown_top_level_key_is_an_error(tmp_path):
    path = _write(tmp_path / "plugin.json", {**GOOD_MANIFEST, "verison": "1"})
    result = asyncio.run(vp.validate_plugin_manifest(path))
    assert result.success is False
    assert result.errors == [vp.ValidationError("root", 'Unrecognized key: "verison"', "unrecognized_keys")]
    assert result.warnings == []


def test_schema_errors_are_formatted(tmp_path):
    result = asyncio.run(vp.validate_plugin_manifest(_write(tmp_path / "plugin.json", {"name": "has space"})))
    assert result.errors == [vp.ValidationError(
        "name", 'Plugin name cannot contain spaces. Use kebab-case (e.g., "my-plugin")', "custom"
    )]


def test_path_traversal_in_component_paths(tmp_path):
    data = {**GOOD_MANIFEST, "commands": ["./ok.md", "./../x.md"], "agents": "./../a.md", "skills": ["./../s"]}
    result = asyncio.run(vp.validate_plugin_manifest(_write(tmp_path / "plugin.json", data)))
    assert _paths(result) == ["commands[1]", "agents[0]", "skills[0]"]
    assert result.errors[0].message == 'Path contains ".." which could be a path traversal attempt: ./../x.md'


def test_recommendation_warnings(tmp_path):
    result = asyncio.run(vp.validate_plugin_manifest(_write(tmp_path / "plugin.json", {"name": "My_Plugin"})))
    assert result.success is True
    assert [w.path for w in result.warnings] == ["name", "version", "description", "author"]
    assert result.warnings[0].message.startswith('Plugin name "My_Plugin" is not kebab-case. TDDAgents accepts it')


# ── marketplace.json ──────────────────────────────────────────────────────────

def _marketplace(**extra):
    return {
        "name": "my-market",
        "owner": {"name": "o"},
        "metadata": {"description": "d"},
        "plugins": [{"name": "a", "source": "./plugins/a"}],
        **extra,
    }


def test_clean_marketplace(tmp_path):
    result = asyncio.run(vp.validate_marketplace_manifest(_write(tmp_path / "marketplace.json", _marketplace())))
    assert result.success is True
    assert result.errors == [] and result.warnings == []
    assert result.file_type == "marketplace"


def test_marketplace_strict_top_level_and_entries(tmp_path):
    data = _marketplace(extra=1, plugins=[{"name": "a", "source": "./a", "typo": True}])
    result = asyncio.run(vp.validate_marketplace_manifest(_write(tmp_path / "marketplace.json", data)))
    # Zod 4 pushes an object's unrecognized-keys issue after its shape's (nested) issues.
    assert [(e.path, e.message) for e in result.errors] == [
        ("plugins.0", 'Unrecognized key: "typo"'),
        ("root", 'Unrecognized key: "extra"'),
    ]


def test_unknown_keys_are_reported_with_the_type_errors(tmp_path):
    data = _marketplace(extra=1, other=2, plugins=[{"name": 3, "source": "./a"}])
    result = asyncio.run(vp.validate_marketplace_manifest(_write(tmp_path / "marketplace.json", data)))
    assert [(e.path, e.message) for e in result.errors] == [
        ("plugins.0.name", "Invalid input: expected string, received number"),
        ("root", 'Unrecognized keys: "extra", "other"'),
    ]


def test_marketplace_source_traversal_hints(tmp_path):
    data = _marketplace(plugins=[
        {"name": "a", "source": "../../plugins/a"},
        {"name": "b", "source": "./x/../y"},
        {"name": "c", "source": {"source": "git-subdir", "url": "o/r", "path": "../evil"}},
    ])
    result = asyncio.run(vp.validate_marketplace_manifest(_write(tmp_path / "marketplace.json", data)))
    messages = {e.path: e.message for e in result.errors}
    assert messages["plugins[0].source"] == (
        'Path contains "..": ../../plugins/a. Plugin source paths are resolved relative to the marketplace root '
        "(the directory containing .tddagents-plugin/), not relative to marketplace.json. "
        'Use "./plugins/a" instead of "../../plugins/a".'
    )
    assert messages["plugins[1].source"].endswith('Use "./plugins/my-plugin" instead of "./x/../y".')
    assert messages["plugins[2].source.path"] == (
        'Path contains ".." which could be a path traversal attempt: ../evil'
    )


def test_marketplace_duplicates_empty_and_metadata_warnings(tmp_path):
    dup = _marketplace(plugins=[{"name": "a", "source": "./a"}, {"name": "a", "source": "./b"}])
    result = asyncio.run(vp.validate_marketplace_manifest(_write(tmp_path / "m1" / "marketplace.json", dup)))
    assert _paths(result) == ["plugins[0].name", "plugins[1].name"]
    assert result.errors[0].message == 'Duplicate plugin name "a" found in marketplace'
    empty = {"name": "my-market", "owner": {"name": "o"}, "plugins": []}
    result = asyncio.run(vp.validate_marketplace_manifest(_write(tmp_path / "m2" / "marketplace.json", empty)))
    assert result.success is True
    assert [w.path for w in result.warnings] == ["plugins", "metadata.description"]


def test_marketplace_entry_version_mismatch_warning(tmp_path):
    root = tmp_path / "repo"
    _write(root / "plugins" / "a" / ".tddagents-plugin" / "plugin.json", {"name": "a", "version": "2.0.0"})
    _write(root / "plugins" / "b" / ".tddagents-plugin" / "plugin.json", {"name": "b", "version": "1.0.0"})
    data = _marketplace(plugins=[
        {"name": "a", "source": "./plugins/a", "version": "1.0.0"},
        {"name": "b", "source": "./plugins/b", "version": "1.0.0"},
        {"name": "c", "source": "./plugins/missing", "version": "1.0.0"},
    ])
    path = _write(root / ".tddagents-plugin" / "marketplace.json", data)
    result = asyncio.run(vp.validate_marketplace_manifest(path))
    assert result.success is True
    assert [w.path for w in result.warnings] == ["plugins[0].version"]
    assert result.warnings[0].message.startswith(
        'Entry declares version "1.0.0" but ./plugins/a/.tddagents-plugin/plugin.json says "2.0.0".'
    )


def test_marketplace_schema_error(tmp_path):
    data = {"name": "my-market", "plugins": []}
    result = asyncio.run(vp.validate_marketplace_manifest(_write(tmp_path / "marketplace.json", data)))
    assert result.success is False
    assert _paths(result) == ["owner"]
    assert result.warnings == []


# ── component files and hooks ─────────────────────────────────────────────────

def test_component_without_frontmatter_warns():
    result = vp.validate_component_file("/x.md", "# Title", "command")
    assert result.success is True
    assert [w.path for w in result.warnings] == ["frontmatter"]


def test_component_frontmatter_checks():
    content = "---\ndescription: [a]\nname: 3\nallowed-tools: [Read, 1]\nshell: zsh\n---\nbody"
    result = vp.validate_component_file("/x.md", content, "agent")
    assert [(e.path, e.message) for e in result.errors] == [
        ("description", "description must be a string, got array. At runtime this value is dropped."),
        ("name", "name must be a string, got number."),
        ("allowed-tools", "allowed-tools array must contain only strings."),
        ("shell", "shell must be 'bash' or 'powershell', got 'zsh'."),
    ]


def test_component_frontmatter_valid_and_missing_description():
    content = "---\ndescription: d\nshell: PowerShell\nallowed-tools: Read\n---\n"
    ok = vp.validate_component_file("/x.md", content, "skill")
    assert ok.success is True and ok.warnings == []
    no_desc = vp.validate_component_file("/x.md", "---\nname: n\n---\n", "skill")
    assert no_desc.warnings[0].message == (
        "No description in frontmatter. A description helps users and Claude understand when to use this skill."
    )


def test_component_frontmatter_bad_yaml_and_non_mapping():
    bad = vp.validate_component_file("/x.md", "---\na: [b\n---\n", "command")
    assert bad.success is False
    assert bad.errors[0].message.startswith("YAML frontmatter failed to parse: ")
    assert bad.errors[0].message.endswith(
        "At runtime this command loads with empty metadata (all frontmatter fields silently dropped)."
    )
    listed = vp.validate_component_file("/x.md", "---\n- a\n---\n", "command")
    assert listed.errors[0].message == "Frontmatter must be a YAML mapping (key: value pairs), got an array."
    empty = vp.validate_component_file("/x.md", "---\n---\n", "command")
    assert empty.errors[0].message == "Frontmatter must be a YAML mapping (key: value pairs), got null."
    scalar = vp.validate_component_file("/x.md", "---\nhello\n---\n", "command")
    assert scalar.errors[0].message == "Frontmatter must be a YAML mapping (key: value pairs), got string."


def test_component_type_errors_for_tools_and_shell():
    result = vp.validate_component_file("/x.md", "---\ndescription: d\nallowed-tools: 3\nshell: 1\n---\n", "agent")
    assert [e.message for e in result.errors] == [
        "allowed-tools must be a string or array of strings, got number.",
        "shell must be a string, got number.",
    ]


def test_hooks_json_absent_bad_and_invalid(tmp_path):
    absent = asyncio.run(vp.validate_hooks_json(str(tmp_path / "hooks.json")))
    assert absent.success is True and absent.file_type == "hooks"
    bad = asyncio.run(vp.validate_hooks_json(_write(tmp_path / "bad.json", "{")))
    assert bad.errors[0].message.endswith("At runtime this breaks the entire plugin load.")
    invalid = asyncio.run(vp.validate_hooks_json(_write(tmp_path / "invalid.json", {"description": 1})))
    assert invalid.success is False
    assert set(_paths(invalid)) == {"description", "hooks"}
    good = asyncio.run(vp.validate_hooks_json(_write(tmp_path / "good.json", {"hooks": {}})))
    assert good.success is True


def test_validate_plugin_contents_reports_files_with_findings(tmp_path):
    _write(tmp_path / "skills" / "good" / "SKILL.md", "---\ndescription: d\n---\n")
    _write(tmp_path / "skills" / "loose.md", "no frontmatter")  # not a skill: single files are ignored
    (tmp_path / "skills" / "empty").mkdir()  # speculative SKILL.md path is skipped
    _write(tmp_path / "agents" / "nested" / "a.md", "no frontmatter")
    _write(tmp_path / "commands" / "c.md", "---\ndescription: d\n---\n")
    _write(tmp_path / "commands" / "notes.txt", "ignored")
    _write(tmp_path / "hooks" / "hooks.json", "{")
    results = asyncio.run(vp.validate_plugin_contents(str(tmp_path)))
    assert [(os.path.relpath(r.file_path, tmp_path), r.file_type) for r in results] == [
        ("agents/nested/a.md", "agent"),
        ("hooks/hooks.json", "hooks"),
    ]


def test_collect_markdown_missing_dir_is_empty(tmp_path):
    assert asyncio.run(vp.collect_markdown(str(tmp_path / "none"), False)) == []


# ── validate_manifest ─────────────────────────────────────────────────────────

def test_directory_prefers_marketplace_then_plugin_then_reports_none(tmp_path):
    both = tmp_path / "both"
    _write(both / ".tddagents-plugin" / "marketplace.json", _marketplace())
    _write(both / ".tddagents-plugin" / "plugin.json", GOOD_MANIFEST)
    assert asyncio.run(vp.validate_manifest(str(both))).file_type == "marketplace"
    plugin_only = tmp_path / "plugin"
    _write(plugin_only / ".tddagents-plugin" / "plugin.json", GOOD_MANIFEST)
    assert asyncio.run(vp.validate_manifest(str(plugin_only))).file_type == "plugin"
    empty = tmp_path / "empty"
    empty.mkdir()
    result = asyncio.run(vp.validate_manifest(str(empty)))
    assert result.success is False
    assert result.errors[0].path == "directory"


def test_detect_manifest_type():
    assert vp.detect_manifest_type("/a/plugin.json") == "plugin"
    assert vp.detect_manifest_type("/a/marketplace.json") == "marketplace"
    assert vp.detect_manifest_type("/a/.tddagents-plugin/x.json") == "plugin"
    assert vp.detect_manifest_type("/a/x.json") == "unknown"


def test_unknown_file_is_guessed_by_plugins_array(tmp_path):
    market = _write(tmp_path / "x.json", _marketplace())
    assert asyncio.run(vp.validate_manifest(market)).file_type == "marketplace"
    plugin = _write(tmp_path / "y.json", GOOD_MANIFEST)
    assert asyncio.run(vp.validate_manifest(plugin)).file_type == "plugin"
    broken = _write(tmp_path / "z.json", "{")
    assert asyncio.run(vp.validate_manifest(broken)).errors[0].path == "json"
    missing = asyncio.run(vp.validate_manifest(str(tmp_path / "missing.json")))
    assert missing.errors == [vp.ValidationError("file", f"File not found: {tmp_path / 'missing.json'}")]
