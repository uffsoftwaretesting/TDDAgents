"""
The settings JSON Schema (`reference/claude-code/src/utils/settings/types.ts` -> `SettingsSchema`,
`schemaOutput.ts` -> `generateSettingsJSONSchema`): the keys this build reads, upstream's
descriptions, and agreement with this build's own validators.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from app.schemas.hooks import hooks_json_schema, hooks_schema_errors
from app.types.permissions import PERMISSION_MODES
from app.utils.hooks.hook_events import HookEvent
from app.utils.settings import schema_builder as zb
from app.utils.settings.schema_output import generate_settings_json_schema
from app.utils.settings.types import (
    CUSTOMIZATION_SURFACES,
    PLUGIN_SETTINGS_KEYS,
    SUPPORTED_SETTINGS_KEYS,
    plugin_settings_schema_errors,
    settings_json_schema,
)

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "reference" / "claude-code" / "src"
needs_reference = pytest.mark.skipif(not (SRC / "utils" / "settings" / "types.ts").is_file(),
                                     reason="the claude-code reference snapshot is not checked out")

SCHEMA = settings_json_schema()
VALIDATOR = Draft202012Validator(SCHEMA)


# ── the builder (Zod's toJSONSchema shapes) ──────────────────────────────────

def test_builder_shapes():
    assert zb.string(min_length=1, url=True, pattern="^x") == {
        "type": "string", "minLength": 1, "format": "uri", "pattern": "^x"}
    assert zb.string(length=40) == {"type": "string", "minLength": 40, "maxLength": 40}
    assert zb.number(positive=True) == {"type": "number", "exclusiveMinimum": 0}
    assert zb.number() == {"type": "number"} and zb.boolean() == {"type": "boolean"}
    assert zb.literal("a") == {"type": "string", "const": "a"}
    assert zb.enum(("a", "b")) == {"type": "string", "enum": ["a", "b"]}
    assert zb.array(zb.boolean(), min_items=1) == {"type": "array", "items": {"type": "boolean"}, "minItems": 1}
    assert zb.record(zb.boolean()) == {"type": "object", "propertyNames": {"type": "string"},
                                       "additionalProperties": {"type": "boolean"}}
    assert zb.union(zb.boolean(), zb.number()) == {"anyOf": [{"type": "boolean"}, {"type": "number"}]}
    assert zb.obj({"a": zb.req(zb.boolean()), "b": zb.opt(zb.number())}) == {
        "type": "object", "properties": {"a": {"type": "boolean"}, "b": {"type": "number"}}, "required": ["a"]}
    assert zb.obj({"b": zb.opt(zb.number())}, passthrough=True) == {
        "type": "object", "properties": {"b": {"type": "number"}}, "additionalProperties": {}}
    assert zb.describe(zb.boolean(), "d") == {"type": "boolean", "description": "d"}
    assert zb.document(zb.boolean()) == {"$schema": "https://json-schema.org/draft/2020-12/schema",
                                         "type": "boolean"}


# ── the document ─────────────────────────────────────────────────────────────

def test_the_document_is_a_valid_schema_of_the_supported_keys():
    Draft202012Validator.check_schema(SCHEMA)
    assert SCHEMA["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert (SCHEMA["type"], SCHEMA["additionalProperties"]) == ("object", {})
    assert tuple(SCHEMA["properties"]) == SUPPORTED_SETTINGS_KEYS
    assert PLUGIN_SETTINGS_KEYS <= set(SUPPORTED_SETTINGS_KEYS) and "required" not in SCHEMA
    assert all(prop.get("description") for prop in SCHEMA["properties"].values())


def test_every_supported_key_is_read_by_the_app():
    """No key is documented that this build ignores: each is read somewhere outside the schema itself."""
    sources = "\n".join(path.read_text(encoding="utf-8") for path in (REPO / "app").rglob("*.py")
                        if path.name not in ("types.py",) and "prompts" not in path.parts)
    for key in SUPPORTED_SETTINGS_KEYS:
        assert f'"{key}"' in sources, key


def test_permission_modes_and_surfaces():
    permissions = SCHEMA["properties"]["permissions"]
    assert permissions["properties"]["defaultMode"]["enum"] == [str(m) for m in PERMISSION_MODES]
    assert permissions["additionalProperties"] == {}
    surfaces = SCHEMA["properties"]["strictPluginOnlyCustomization"]["anyOf"][1]["items"]["enum"]
    assert surfaces == list(CUSTOMIZATION_SURFACES) == ["skills", "agents", "hooks", "mcp"]


def test_hooks_events_are_every_hook_event():
    assert hooks_json_schema()["propertyNames"]["enum"] == [str(e) for e in HookEvent]
    assert SCHEMA["properties"]["hooks"]["propertyNames"]["enum"][0] == "PreToolUse"


def test_generated_text():
    text = generate_settings_json_schema()
    assert json.loads(text) == SCHEMA
    assert text.startswith('{\n  "$schema": "https://json-schema.org/draft/2020-12/schema",\n  "type": "object"')
    assert "—" in text and "\\u2014" not in text  # non-ASCII as is, as `JSON.stringify`
    assert "Claude" not in text and ".claude/" not in text.replace(".claude-plugin/", "")


# ── agreement with this build's validators ───────────────────────────────────

HOOK_DOCS: list[Any] = [
    {},
    {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "x", "timeout": 5}]}]},
    {"Stop": [{"hooks": [{"type": "prompt", "prompt": "p"}, {"type": "agent", "prompt": "a", "model": "m"},
                         {"type": "http", "url": "https://h.example/x", "headers": {"A": "b"}}]}]},
    {"PostToolUse": [{"hooks": [{"type": "command", "command": "x", "shell": "bash", "async": True}]}]},
    {"NoSuchEvent": [{"hooks": []}]},
    {"Stop": {"hooks": []}},
    {"Stop": [{"matcher": 3, "hooks": []}]},
    {"Stop": [{"matcher": "x"}]},
    {"Stop": [{"hooks": [{"type": "command"}]}]},
    {"Stop": [{"hooks": [{"type": "nope", "command": "x"}]}]},
    {"Stop": [{"hooks": [{"type": "command", "command": 1}]}]},
    {"Stop": [{"hooks": [{"type": "command", "command": "x", "shell": "fish"}]}]},
    {"Stop": [{"hooks": [{"type": "command", "command": "x", "timeout": "5"}]}]},
    {"Stop": [{"hooks": [{"type": "prompt"}]}]},
    {"Stop": [{"hooks": [{"type": "http", "url": "https://h.example/x", "allowedEnvVars": "A"}]}]},
    "not an object",
]


@pytest.mark.parametrize("doc", HOOK_DOCS)
def test_the_hooks_schema_agrees_with_the_hooks_validator(doc):
    schema_valid = VALIDATOR.is_valid({"hooks": doc})
    assert schema_valid == (hooks_schema_errors(doc) == []), doc


GITHUB = {"source": "github", "repo": "o/r"}
PLUGIN_DOCS: list[dict[str, Any]] = [
    {"enabledPlugins": {"a@m": True, "b@m": ["1.0"]}},
    {"enabledPlugins": {"a@m": "yes"}},
    {"extraKnownMarketplaces": {"m": {"source": GITHUB, "autoUpdate": True}}},
    {"extraKnownMarketplaces": {"m": {"source": {"source": "github"}}}},
    {"extraKnownMarketplaces": {"m": {"source": {"source": "ftp", "url": "x"}}}},
    {"extraKnownMarketplaces": {"m": {}}},
    {"strictKnownMarketplaces": [GITHUB, {"source": "hostPattern", "hostPattern": "^x$"}]},
    {"blockedMarketplaces": [{"source": "file"}]},
    {"pluginConfigs": {"p@m": {"options": {"a": 1, "b": ["x"], "c": True}}}},
    {"pluginConfigs": {"p@m": {"options": {"a": {"nested": 1}}}}},
    {"pluginTrustMessage": "vetted"},
    {"pluginTrustMessage": 3},
]


@pytest.mark.parametrize("doc", PLUGIN_DOCS)
def test_the_plugin_keys_agree_with_the_plugin_validator(doc):
    assert VALIDATOR.is_valid(doc) == (plugin_settings_schema_errors(doc) == []), doc


def test_other_keys_are_typed():
    assert VALIDATOR.is_valid({"permissions": {"allow": ["Bash(npm:*)"], "defaultMode": "acceptEdits",
                                               "unknownKey": 1}, "anything": {"else": True}})
    bad_documents: list[dict[str, Any]] = [
        {"permissions": {"defaultMode": "sometimes"}}, {"permissions": {"allow": "Bash"}},
        {"disableAllHooks": "yes"}, {"allowedMcpServers": [{"serverName": "bad name"}]},
        {"deniedMcpServers": [{"serverCommand": []}]}, {"strictPluginOnlyCustomization": ["skills", 1]}]
    for bad in bad_documents:
        assert not VALIDATOR.is_valid(bad), bad


# ── upstream's descriptions ──────────────────────────────────────────────────

def _descriptions(node: Any) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        if isinstance(node.get("description"), str):
            found.append(node["description"])
        for value in node.values():
            found.extend(_descriptions(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_descriptions(value))
    return found


def _upstream_text() -> str:
    """The three source files, string concatenations joined and JS escapes resolved."""
    text = "".join((SRC / rel).read_text(encoding="utf-8") for rel in (
        "utils/settings/types.ts", "schemas/hooks.ts", "utils/plugins/schemas.ts"))
    text = re.sub(r"(['\"`])\s*\+\s*\n\s*(['\"`])", "", text)
    return text.replace("\\'", "'").replace("\\\\", "\\").replace("\\u2014", "—")


@needs_reference
def test_every_description_is_upstreams_after_the_product_substitutions():
    upstream = _upstream_text()
    for text in set(_descriptions(SCHEMA)):
        original = (text.replace("TDDAgents needs", "Claude Code needs").replace("TDDAgents will", "Claude will")
                    .replace(".tddagents/", ".claude/"))
        assert original in upstream, text
