"""
The settings JSON Schema (`reference/claude-code/src/utils/settings/types.ts` -> `SettingsSchema`,
`schemaOutput.ts` -> `generateSettingsJSONSchema`): every key upstream declares, upstream's
descriptions, Zod's two output modes, and agreement with this build's own validators.
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
from app.utils.settings.schema_output import generate_settings_json_schema, generate_settings_schema
from app.utils.settings.types import (
    CUSTOMIZATION_SURFACES,
    GATED_OFF_SETTINGS_KEYS,
    PLUGIN_SETTINGS_KEYS,
    SETTINGS_KEYS,
    STRICT_SETTINGS_KEYS,
    SUPPORTED_SETTINGS_KEYS,
    UNREAD_UPSTREAM_SETTINGS_KEYS,
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
    assert zb.number(minimum=0, maximum=1) == {"type": "number", "minimum": 0, "maximum": 1}
    assert zb.integer() == {"type": "integer", "minimum": -zb.MAX_SAFE_INTEGER, "maximum": zb.MAX_SAFE_INTEGER}
    assert zb.integer(minimum=-1) == {"type": "integer", "minimum": -1, "maximum": 9007199254740991}
    assert zb.integer(positive=True) == {"type": "integer", "exclusiveMinimum": 0, "maximum": 9007199254740991}
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


def test_builder_key_order_is_zods():
    """`toJSONSchema` writes a description first, and an array's `minItems` before its type."""
    assert list(zb.describe(zb.integer(positive=True), "d")) == ["description", "type", "exclusiveMinimum", "maximum"]
    assert list(zb.describe(zb.describe(zb.boolean(), "old"), "new").items()) == [
        ("description", "new"), ("type", "boolean")]
    assert list(zb.array(zb.boolean(), min_items=1)) == ["minItems", "type", "items"]
    assert list(zb.array(zb.boolean())) == ["type", "items"]


# ── the document ─────────────────────────────────────────────────────────────

def test_the_document_is_a_valid_schema_of_every_key():
    Draft202012Validator.check_schema(SCHEMA)
    assert SCHEMA["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert (SCHEMA["type"], SCHEMA["additionalProperties"]) == ("object", {})
    assert tuple(SCHEMA["properties"]) == SETTINGS_KEYS and len(SETTINGS_KEYS) == 82
    assert PLUGIN_SETTINGS_KEYS <= set(SUPPORTED_SETTINGS_KEYS) and "required" not in SCHEMA
    # `sandbox` is `SandboxSettingsSchema().optional()`, the one key upstream does not `.describe()`.
    assert [k for k, prop in SCHEMA["properties"].items() if not prop.get("description")] == ["sandbox"]
    assert STRICT_SETTINGS_KEYS == set(SETTINGS_KEYS)
    assert UNREAD_UPSTREAM_SETTINGS_KEYS == tuple(k for k in SETTINGS_KEYS if k not in SUPPORTED_SETTINGS_KEYS)
    assert [k for k in SETTINGS_KEYS if k in SUPPORTED_SETTINGS_KEYS] == list(SUPPORTED_SETTINGS_KEYS)
    assert not set(GATED_OFF_SETTINGS_KEYS) & set(SETTINGS_KEYS)


def test_the_unread_keys_are_typed_as_upstream():
    props = SCHEMA["properties"]
    assert props["model"] == {"description": "Override the default model used by TDDAgents", "type": "string"}
    assert props["env"] == {"description": "Environment variables to set for TDDAgents sessions", "type": "object",
                            "propertyNames": {"type": "string"}, "additionalProperties": {"type": "string"}}
    assert props["$schema"]["const"] == "https://json.schemastore.org/claude-code-settings.json"
    # `.nonnegative().int()`: Zod prints `.int()`'s bounds only (the validator still checks >= 0).
    assert props["cleanupPeriodDays"]["minimum"] == -zb.MAX_SAFE_INTEGER
    assert props["xaaIdp"]["properties"]["callbackPort"]["exclusiveMinimum"] == 0
    assert props["xaaIdp"]["properties"]["issuer"]["format"] == "uri"
    assert props["xaaIdp"]["required"] == ["issuer", "clientId"]
    assert props["effortLevel"]["enum"] == ["low", "medium", "high"]  # the external build's levels
    assert props["sandbox"]["additionalProperties"] == {}  # `.passthrough()`
    assert "additionalProperties" not in props["sandbox"]["properties"]["network"]
    assert props["sshConfigs"]["items"]["properties"]["startDirectory"]["description"].endswith(
        "`python -m app.main ssh <config> [dir]`.")
    assert props["permissions"]["properties"]["disableAutoMode"]["enum"] == ["disable"]


def test_every_supported_key_is_read_by_the_app():
    """The keys said to be read are read somewhere outside the schema itself."""
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


def _closed(node: Any) -> Any:
    """`node` with every object that has properties and no `additionalProperties` closed."""
    if isinstance(node, list):
        return [_closed(item) for item in node]
    if not isinstance(node, dict):
        return node
    closed = {key: _closed(value) for key, value in node.items()}
    if node.get("type") == "object" and "properties" in node and "additionalProperties" not in node:
        closed["additionalProperties"] = False
    return closed


def test_generated_text_in_both_modes():
    text = generate_settings_schema()
    assert json.loads(text) == SCHEMA  # `update-config`: the input mode
    assert text.startswith('{\n  "$schema": "https://json-schema.org/draft/2020-12/schema",\n  "type": "object"')
    assert "—" in text and "\\u2014" not in text  # non-ASCII as is, as `JSON.stringify`
    # Upstream's own service names stay: the subscription plan is the one "Claude".
    assert text.count("Claude") == 1 and "Claude Pro/Max" in text and ".claude" not in text
    output = json.loads(generate_settings_json_schema())  # an Edit refusal: the output mode
    assert output == _closed(SCHEMA) and output != SCHEMA
    assert output["additionalProperties"] == {} and output["properties"]["sandbox"]["additionalProperties"] == {}
    assert output["properties"]["statusLine"]["additionalProperties"] is False
    assert list(output["properties"]["statusLine"])[-1] == "additionalProperties"
    assert output["properties"]["env"]["additionalProperties"] == {"type": "string"}  # a record stays a record


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
    """The four source files, string concatenations joined and JS escapes resolved."""
    text = "".join((SRC / rel).read_text(encoding="utf-8") for rel in (
        "utils/settings/types.ts", "entrypoints/sandboxTypes.ts", "schemas/hooks.ts", "utils/plugins/schemas.ts"))
    text = re.sub(r"(['\"`])\s*\+\s*\n\s*(['\"`])", "", text)
    return text.replace("\\'", "'").replace("\\\\", "\\").replace("\\u2014", "—")


#: Upstream's gated top-level keys whose code is in the reference snapshot, declared here.
GATED_ON_KEYS = ("xaaIdp", "disableDeepLinkRegistration", "voiceEnabled", "defaultView")


def _upstream_top_level_keys() -> list[str]:
    """`SettingsSchema`'s own keys, in declaration order: six spaces in, or twelve inside a gated spread."""
    text = (SRC / "utils" / "settings" / "types.ts").read_text(encoding="utf-8")
    body = text[text.index("export const SettingsSchema = lazySchema("):]
    body = body[:body.index("\n    .passthrough(),\n)")]
    keys: list[str] = []
    in_spread = False
    for line in body.splitlines():
        if line.startswith("      ...("):
            in_spread = True
        elif line.startswith("        : {}),"):
            in_spread = False
        match = re.match(r"( +)(\$?[A-Za-z_]+): (?:z\b|[A-Z]\w*Schema\(\))", line)
        if match and len(match.group(1)) == (12 if in_spread else 6):
            keys.append(match.group(2))
    return keys


@needs_reference
def test_the_keys_are_upstreams_top_level_keys():
    """Every key of upstream's `SettingsSchema`, in its order, but the gated keys whose code is absent."""
    upstream = _upstream_top_level_keys()
    assert len(upstream) == len(set(upstream)) == len(SETTINGS_KEYS) + len(GATED_OFF_SETTINGS_KEYS)
    assert [k for k in upstream if k not in GATED_OFF_SETTINGS_KEYS] == list(SETTINGS_KEYS)
    assert set(GATED_ON_KEYS) <= set(SETTINGS_KEYS)


#: Each gated key and the snapshot files its code lives in: all present when declared, one absent when not.
GATED_KEY_EVIDENCE: dict[str, tuple[str, ...]] = {
    "xaaIdp": ("services/mcp/xaaIdpLogin.ts", "commands/mcp/xaaIdpCommand.ts"),
    "disableDeepLinkRegistration": ("utils/deepLink/registerProtocol.ts",),
    "voiceEnabled": ("commands/voice/voice.ts", "services/voice.ts", "hooks/useVoiceEnabled.ts"),
    "defaultView": ("tools/BriefTool/BriefTool.ts",),
    "minSleepDurationMs": ("tools/SleepTool/SleepTool.ts",),
    "maxSleepDurationMs": ("tools/SleepTool/SleepTool.ts",),
    "assistant": ("assistant/index.ts", "assistant/gate.ts"),
    "assistantName": ("assistant/index.ts",),
}


@needs_reference
@pytest.mark.parametrize("key", list(GATED_KEY_EVIDENCE))
def test_a_gated_key_is_declared_exactly_when_its_code_is_in_the_snapshot(key: str):
    present = all((SRC / rel).is_file() for rel in GATED_KEY_EVIDENCE[key])
    assert (key in SETTINGS_KEYS) == present and (key in GATED_OFF_SETTINGS_KEYS) == (not present)


@needs_reference
def test_classifier_permissions_is_off_because_the_external_classifier_is_a_stub():
    stub = (SRC / "utils" / "permissions" / "bashClassifier.ts").read_text(encoding="utf-8")
    assert stub.startswith("// Stub for external builds")
    assert "classifierPermissionsEnabled" in GATED_OFF_SETTINGS_KEYS


def test_auto_mode_is_upstreams_external_shape():
    auto_mode = SCHEMA["properties"]["autoMode"]
    assert auto_mode["description"] == "Auto mode classifier prompt customization"
    assert list(auto_mode["properties"]) == ["allow", "soft_deny", "environment"]
    assert "additionalProperties" not in auto_mode and "required" not in auto_mode
    assert all(p["type"] == "array" and p["items"] == {"type": "string"} for p in auto_mode["properties"].values())
    assert VALIDATOR.is_valid({"autoMode": {"allow": ["a"], "soft_deny": [], "environment": ["e"], "deny": 1}})
    for bad in ({"autoMode": {"allow": "a"}}, {"autoMode": {"environment": [1]}}, {"autoMode": "x"}):
        assert not VALIDATOR.is_valid(bad), bad


#: This build's text -> upstream's, most specific first.
PRODUCT_SUBSTITUTIONS = (
    ("TDDAgents' client_id", "Claude Code's client_id"), ("TDDAgents' co-authored", "Claude's co-authored"),
    ("TDDAgents' system prompt", "Claude's system prompt"), ("TDDAgents responses", "Claude responses"),
    ("TDDAgents needs", "Claude Code needs"), ("TDDAgents will", "Claude will"), ("TDDAgents", "Claude Code"),
    ("TDDAGENTS.md", "CLAUDE.md"), (".tddagents/", ".claude/"), ("~/.tddagents", "~/.claude"),
    ("tddagents-plugin", "claude-plugin"), ("python -m app.main ssh", "claude ssh"),
)


@needs_reference
def test_every_description_is_upstreams_after_the_product_substitutions():
    upstream = _upstream_text()
    for text in set(_descriptions(SCHEMA)):
        original = text
        for ours, theirs in PRODUCT_SUBSTITUTIONS:
            original = original.replace(ours, theirs)
        assert original in upstream, text
