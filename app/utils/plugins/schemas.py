r"""
The plugin, marketplace and installation schemas, and the reserved-name checks.

Ported from `reference/claude-code/src/utils/plugins/schemas.ts` -> `ALLOWED_OFFICIAL_MARKETPLACE_NAMES`,
`NO_AUTO_UPDATE_OFFICIAL_MARKETPLACES`, `isMarketplaceAutoUpdate`, `BLOCKED_OFFICIAL_NAME_PATTERN`,
`isBlockedOfficialName`, `OFFICIAL_GITHUB_ORG`, `validateOfficialNameSource`, `PluginAuthorSchema`,
`PluginManifestSchema` (and its parts), `PluginHooksSchema`, `CommandMetadataSchema`, `LspServerConfigSchema`,
`MarketplaceSourceSchema`, `gitSha`, `PluginSourceSchema`, `isLocalPluginSource`, `isLocalMarketplaceSource`,
`PluginMarketplaceEntrySchema`, `PluginMarketplaceSchema`, `PluginIdSchema`, `DependencyRefSchema`,
`SettingsPluginEntrySchema`, `InstalledPluginSchema`, `InstalledPluginsFileSchemaV1/V2`, `PluginScopeSchema`,
`PluginInstallationEntrySchema`, `InstalledPluginsFileSchema`, `KnownMarketplaceSchema`,
`KnownMarketplacesFileSchema` and the inferred types; `src/utils/lazySchema.ts` -> `lazySchema`.

Upstream's Zod schemas become pydantic `TypedDict` adapters: a parse returns a plain dict, as
Zod returns a plain object, so every caller keeps upstream's shape (`entry["source"]`,
`json.dumps(config)`, deep equality). Zod's behaviours are kept field for field: unknown keys
are stripped (`extra="ignore"`) except in Zod's `.strict()` objects (`extra="forbid"`), no
type coercion (`strict=True`), `.refine` messages verbatim, `.default()` values filled in, and
the dependency transform normalizes to `"name"` / `"name@marketplace"`.

`safe_parse` / `parse` stand in for `schema.safeParse` / `schema.parse`, each failure an issue
with Zod's `path`, `message` and `code`. Zod's built-in messages are rendered in Zod 4's wording
(`Invalid input: expected string, received number`); a pydantic error without a Zod twin keeps
pydantic's message. The adapters are built on first use (the `_ADAPTERS` memo, upstream's
`lazySchema`). JavaScript regex semantics are kept: `$` is `\Z` (no match before a final
newline) and character classes are ASCII (`re.ASCII`); a JSON integer stays an `int`.

Divergences: the embedded `HooksSchema` and `McpServerConfigSchema` reuse this repository's
validators (`hooks_schema_errors`, `adheres_to_server_schema`) and keep the raw object instead of
a stripped copy; Zod's `.url()` (WHATWG `new URL`) is approximated by `is_url`. The `.describe()`
texts feed upstream's JSON-schema export only and are not carried. Issue reporting differs from
Zod in three known ways: a missing required field reads `expected value` where Zod names the
field's type; chained refinements stop at the first failure where Zod 4 reports every failing
check; and a plain union reports one `invalid_union` at its own path (Zod 4.x surfaces the lone
non-aborted branch's issues; the bundled Zod version is not pinned in the reference snapshot).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import (
    Annotated,
    Any,
    Generic,
    Literal,
    NotRequired,
    TypeAlias,
    TypedDict,
    TypeVar,
    cast,
)

from pydantic import (
    AfterValidator,
    BeforeValidator,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    with_config,
)
from pydantic_core import PydanticCustomError

from app.schemas.hooks import hooks_schema_errors
from app.services.mcp.types import adheres_to_server_schema

T = TypeVar("T")

# ── reserved names ────────────────────────────────────────────────────────────

#: `ALLOWED_OFFICIAL_MARKETPLACE_NAMES`: reserved for official marketplaces only.
ALLOWED_OFFICIAL_MARKETPLACE_NAMES: frozenset[str] = frozenset({
    "claude-code-marketplace",
    "claude-code-plugins",
    "claude-plugins-official",
    "anthropic-marketplace",
    "anthropic-plugins",
    "agent-skills",
    "life-sciences",
    "knowledge-work-plugins",
})

#: `NO_AUTO_UPDATE_OFFICIAL_MARKETPLACES`.
NO_AUTO_UPDATE_OFFICIAL_MARKETPLACES: frozenset[str] = frozenset({"knowledge-work-plugins"})

#: `BLOCKED_OFFICIAL_NAME_PATTERN` (case-insensitive).
BLOCKED_OFFICIAL_NAME_PATTERN = re.compile(
    r"(?:official[^a-z0-9]*(anthropic|claude)|(?:anthropic|claude)[^a-z0-9]*official"
    r"|^(?:anthropic|claude)[^a-z0-9]*(marketplace|plugins|official))",
    re.IGNORECASE | re.ASCII,
)

#: `NON_ASCII_PATTERN`: anything outside printable ASCII (homograph attacks).
NON_ASCII_PATTERN = re.compile(r"[^ -~]")

#: `OFFICIAL_GITHUB_ORG`.
OFFICIAL_GITHUB_ORG = "anthropics"


def is_marketplace_auto_update(marketplace_name: str, entry: Mapping[str, Any]) -> bool:
    """`isMarketplaceAutoUpdate`: the stored flag, else on for official marketplaces (minus the opt-outs)."""
    stored = entry.get("autoUpdate")
    if stored is not None:
        return bool(stored)
    normalized = marketplace_name.lower()
    return normalized in ALLOWED_OFFICIAL_MARKETPLACE_NAMES and normalized not in NO_AUTO_UPDATE_OFFICIAL_MARKETPLACES


def is_blocked_official_name(name: str) -> bool:
    """`isBlockedOfficialName`: the name impersonates an official marketplace."""
    if name.lower() in ALLOWED_OFFICIAL_MARKETPLACE_NAMES:
        return False
    if NON_ASCII_PATTERN.search(name):
        return True
    return BLOCKED_OFFICIAL_NAME_PATTERN.search(name) is not None


def validate_official_name_source(name: str, source: Mapping[str, Any]) -> str | None:
    """`validateOfficialNameSource`: a reserved name must come from github.com/anthropics/."""
    if name.lower() not in ALLOWED_OFFICIAL_MARKETPLACE_NAMES:
        return None
    if source.get("source") == "github":
        repo = source.get("repo") or ""
        if not str(repo).lower().startswith(f"{OFFICIAL_GITHUB_ORG}/"):
            return (
                f"The name '{name}' is reserved for official Anthropic marketplaces. Only repositories from "
                f"'github.com/{OFFICIAL_GITHUB_ORG}/' can use this name."
            )
        return None
    if source.get("source") == "git" and source.get("url"):
        url = str(source["url"]).lower()
        if "github.com/anthropics/" in url or "git@github.com:anthropics/" in url:
            return None
        return (
            f"The name '{name}' is reserved for official Anthropic marketplaces. Only repositories from "
            f"'github.com/{OFFICIAL_GITHUB_ORG}/' can use this name."
        )
    return (
        f"The name '{name}' is reserved for official Anthropic marketplaces and can only be used with GitHub "
        f"sources from the '{OFFICIAL_GITHUB_ORG}' organization."
    )


# ── Zod-style refinements ─────────────────────────────────────────────────────

_URL_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
#: WHATWG "special" schemes, which need a host (`file:` may have an empty one).
_SPECIAL_SCHEMES = frozenset({"http", "https", "ws", "wss", "ftp"})


def is_url(value: str) -> bool:
    """Zod's `.url()` (`new URL(value)` succeeds), approximated: a scheme, and a host where WHATWG wants one."""
    stripped = value.strip()
    match = _URL_SCHEME.match(stripped)
    if match is None:
        return False
    scheme = match.group(0)[:-1].lower()
    rest = stripped[len(scheme) + 1:]
    if scheme in _SPECIAL_SCHEMES:
        host = rest.lstrip("/\\").split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
        host = host.rsplit("@", 1)[-1]
        return bool(host) and " " not in host
    return True


def _custom(message: str) -> PydanticCustomError:
    return PydanticCustomError("custom", message)


def _refine(predicate: Callable[[str], bool], message: str, code: str = "custom") -> AfterValidator:
    def check(value: str) -> str:
        if not predicate(value):
            raise PydanticCustomError(code, message)
        return value

    return AfterValidator(check)


def _min_length(minimum: int, message: str | None = None) -> AfterValidator:
    text = message or f"Too small: expected string to have >={minimum} characters"
    return _refine(lambda v: len(v) >= minimum, text, "too_small")


def _starts_with(prefix: str) -> AfterValidator:
    return _refine(lambda v: v.startswith(prefix), f'Invalid string: must start with "{prefix}"', "invalid_format")


def _ends_with(suffix: str) -> AfterValidator:
    return _refine(lambda v: v.endswith(suffix), f'Invalid string: must end with "{suffix}"', "invalid_format")


def _regex(pattern: re.Pattern[str], message: str | None = None) -> AfterValidator:
    # The JS source: `\Z` stands in for JS's `$` (which never matches before a final newline).
    text = message or f"Invalid string: must match pattern /{pattern.pattern.replace(chr(92) + 'Z', '$')}/"
    return _refine(lambda v: pattern.search(v) is not None, text, "invalid_format")


_URL = _refine(is_url, "Invalid URL", "invalid_format")

#: `z.number()`: an integer stays an int (pydantic would make `5` a float `5.0`).
_JsNumber: TypeAlias = int | float

#: `RelativePath`.
RelativePath: TypeAlias = Annotated[str, _starts_with("./")]
#: `RelativeJSONPath`.
RelativeJSONPath: TypeAlias = Annotated[str, _starts_with("./"), _ends_with(".json")]
#: `RelativeMarkdownPath`.
RelativeMarkdownPath: TypeAlias = Annotated[str, _starts_with("./"), _ends_with(".md")]
#: `RelativeCommandPath`: a markdown file or any relative path (a skill directory).
RelativeCommandPath: TypeAlias = RelativePath


def _is_mcpb_file(value: str) -> bool:
    return value.endswith((".mcpb", ".dxt"))


#: `McpbPath`: a relative `.mcpb`/`.dxt` path, or a URL to one.
McpbPath: TypeAlias = (
    Annotated[str, _starts_with("./"), _refine(_is_mcpb_file, "MCPB file path must end with .mcpb or .dxt")]
    | Annotated[str, _URL, _refine(_is_mcpb_file, "MCPB URL must end with .mcpb or .dxt")]
)


def _no_path_separators(name: str) -> bool:
    return "/" not in name and "\\" not in name and ".." not in name and name != "."


#: `MarketplaceNameSchema`: shared by marketplace.json and the settings source arm.
MarketplaceName: TypeAlias = Annotated[
    str,
    _min_length(1, "Marketplace must have a name"),
    _refine(lambda n: " " not in n, 'Marketplace name cannot contain spaces. Use kebab-case (e.g., "my-marketplace")'),
    _refine(
        _no_path_separators,
        'Marketplace name cannot contain path separators (/ or \\), ".." sequences, or be "."',
    ),
    _refine(lambda n: not is_blocked_official_name(n),
            "Marketplace name impersonates an official Anthropic/Claude marketplace"),
    _refine(lambda n: n.lower() != "inline", 'Marketplace name "inline" is reserved for --plugin-dir session plugins'),
    _refine(lambda n: n.lower() != "builtin", 'Marketplace name "builtin" is reserved for built-in plugins'),
]

#: The plugin-name field shared by the manifest and marketplace entries.
PluginName: TypeAlias = Annotated[
    str,
    _min_length(1, "Plugin name cannot be empty"),
    _refine(lambda n: " " not in n, 'Plugin name cannot contain spaces. Use kebab-case (e.g., "my-plugin")'),
]

_STRICT = ConfigDict(strict=True)
_STRICT_FORBID = ConfigDict(strict=True, extra="forbid")


@with_config(_STRICT)
class PluginAuthor(TypedDict):
    """`PluginAuthorSchema`."""

    name: Annotated[str, _min_length(1, "Author name cannot be empty")]
    email: NotRequired[str]
    url: NotRequired[str]


# ── embedded schemas from elsewhere ───────────────────────────────────────────

def _hooks(value: dict[str, Any]) -> dict[str, Any]:
    errors = hooks_schema_errors(value)
    if errors:
        raise _custom("; ".join(errors))
    return value


def _mcp_server(value: dict[str, Any]) -> dict[str, Any]:
    if not adheres_to_server_schema(value):
        raise _custom("Does not adhere to MCP server configuration schema")
    return value


#: `HooksSchema` (validated by `app/schemas/hooks.py`; the raw object is kept).
HooksSettings: TypeAlias = Annotated[dict[str, Any], AfterValidator(_hooks)]
#: `McpServerConfigSchema` (validated by `app/services/mcp/types.py`; the raw object is kept).
McpServerConfigDict: TypeAlias = Annotated[dict[str, Any], AfterValidator(_mcp_server)]


@with_config(_STRICT)
class PluginHooks(TypedDict):
    """`PluginHooksSchema`: a plugin's hooks.json."""

    description: NotRequired[str]
    hooks: HooksSettings


@with_config(_STRICT)
class _CommandMetadataFields(TypedDict):
    source: NotRequired[RelativeCommandPath]
    content: NotRequired[str]
    description: NotRequired[str]
    argumentHint: NotRequired[str]
    model: NotRequired[str]
    allowedTools: NotRequired[list[str]]


def _source_xor_content(data: _CommandMetadataFields) -> _CommandMetadataFields:
    source, content = data.get("source"), data.get("content")
    if not ((source and not content) or (not source and content)):
        raise _custom('Command must have either "source" (file path) or "content" (inline markdown), but not both')
    return data


#: `CommandMetadata` (`CommandMetadataSchema`): exactly one of `source` / `content`.
CommandMetadata: TypeAlias = _CommandMetadataFields
_CommandMetadataChecked: TypeAlias = Annotated[_CommandMetadataFields, AfterValidator(_source_xor_content)]


@with_config(_STRICT_FORBID)
class PluginUserConfigOption(TypedDict):
    """`PluginUserConfigOptionSchema` (strict: unknown keys fail)."""

    type: Literal["string", "number", "boolean", "directory", "file"]
    title: str
    description: str
    required: NotRequired[bool]
    default: NotRequired[str | _JsNumber | bool | list[str]]
    multiple: NotRequired[bool]
    sensitive: NotRequired[bool]
    min: NotRequired[_JsNumber]
    max: NotRequired[_JsNumber]


_USER_CONFIG_KEY = re.compile(r"^[A-Za-z_]\w*\Z", re.ASCII)
UserConfigKey: TypeAlias = Annotated[
    str,
    _regex(
        _USER_CONFIG_KEY,
        "Option keys must be valid identifiers (letters, digits, underscore; no leading digit) — they become "
        "CLAUDE_PLUGIN_OPTION_<KEY> env vars in hooks",
    ),
]


@with_config(_STRICT_FORBID)
class PluginManifestChannel(TypedDict):
    """One entry of `PluginManifestChannelsSchema` (strict)."""

    server: Annotated[str, _min_length(1)]
    displayName: NotRequired[str]
    userConfig: NotRequired[dict[str, PluginUserConfigOption]]


_NonEmptyString: TypeAlias = Annotated[str, _min_length(1)]
_FileExtension: TypeAlias = Annotated[
    str,
    _min_length(2),
    _refine(lambda e: e.startswith("."), 'File extensions must start with dot (e.g., ".ts", not "ts")'),
]


def _command_without_spaces(command: str) -> bool:
    return not (" " in command and not command.startswith("/"))


def _non_empty_mapping(value: dict[str, str]) -> dict[str, str]:
    if not value:
        raise _custom("extensionToLanguage must have at least one mapping")
    return value


def _whole_float_to_int(value: Any) -> Any:
    """A JSON `1000.0` is the JS number 1000, which `.int()` accepts."""
    return int(value) if isinstance(value, float) and value.is_integer() else value


#: `z.number().int()`.
_JsInt: TypeAlias = Annotated[int, BeforeValidator(_whole_float_to_int)]


def _positive(value: int) -> int:
    if value <= 0:
        raise PydanticCustomError("too_small", "Too small: expected number to be >0")
    return value


def _non_negative(value: int) -> int:
    if value < 0:
        raise PydanticCustomError("too_small", "Too small: expected number to be >=0")
    return value


@with_config(_STRICT_FORBID)
class LspServerConfig(TypedDict):
    """`LspServerConfigSchema` (strict). `transport` defaults to `"stdio"`."""

    command: Annotated[
        str,
        _min_length(1),
        _refine(_command_without_spaces, "Command should not contain spaces. Use args array for arguments."),
    ]
    args: NotRequired[list[_NonEmptyString]]
    extensionToLanguage: Annotated[dict[_FileExtension, _NonEmptyString], AfterValidator(_non_empty_mapping)]
    transport: NotRequired[Literal["stdio", "socket"]]
    env: NotRequired[dict[str, str]]
    initializationOptions: NotRequired[Any]
    settings: NotRequired[Any]
    workspaceFolder: NotRequired[str]
    startupTimeout: NotRequired[Annotated[_JsInt, AfterValidator(_positive)]]
    shutdownTimeout: NotRequired[Annotated[_JsInt, AfterValidator(_positive)]]
    restartOnCrash: NotRequired[bool]
    maxRestarts: NotRequired[Annotated[_JsInt, AfterValidator(_non_negative)]]


def _lsp_defaults(config: LspServerConfig) -> LspServerConfig:
    config.setdefault("transport", "stdio")
    return config


_LspServerConfigChecked: TypeAlias = Annotated[LspServerConfig, AfterValidator(_lsp_defaults)]

# ── dependencies ──────────────────────────────────────────────────────────────

#: `DEP_REF_REGEX`.
DEP_REF_REGEX = re.compile(r"^[a-z0-9][-a-z0-9._]*(@[a-z0-9][-a-z0-9._]*)?(@\^[^@]*)?\Z", re.IGNORECASE | re.ASCII)
_DEP_NAME = re.compile(r"^[a-z0-9][-a-z0-9._]*\Z", re.IGNORECASE | re.ASCII)
_DEP_VERSION_SUFFIX = re.compile(r"@\^[^@]*\Z", re.ASCII)


def normalize_dependency_ref(value: Any) -> Any:
    """
    `DependencyRefSchema`'s transform: `"plugin"`, `"plugin@mkt"`, `"plugin@mkt@^1.2"` (version
    stripped) or `{name, marketplace?, ...}`, all to `"name"` / `"name@marketplace"`.
    """
    if isinstance(value, str):
        if DEP_REF_REGEX.search(value) is None:
            raise PydanticCustomError(
                "invalid_format", "Dependency must be a plugin name, optionally qualified with @marketplace"
            )
        return _DEP_VERSION_SUFFIX.sub("", value)
    if isinstance(value, dict):
        name = value.get("name")
        marketplace = value.get("marketplace")
        if not isinstance(name, str) or not name or _DEP_NAME.search(name) is None:
            raise PydanticCustomError("invalid_union", "Invalid input")
        if marketplace is not None and (
            not isinstance(marketplace, str) or not marketplace or _DEP_NAME.search(marketplace) is None
        ):
            raise PydanticCustomError("invalid_union", "Invalid input")
        return f"{name}@{marketplace}" if marketplace else name
    raise PydanticCustomError("invalid_union", "Invalid input")


#: `DependencyRefSchema` (always a string after the transform).
DependencyRef: TypeAlias = Annotated[str, BeforeValidator(normalize_dependency_ref)]

# ── the manifest ──────────────────────────────────────────────────────────────

_HooksEntry: TypeAlias = RelativeJSONPath | HooksSettings
_McpServersEntry: TypeAlias = RelativeJSONPath | McpbPath | dict[str, McpServerConfigDict]
_LspServersEntry: TypeAlias = RelativeJSONPath | dict[str, _LspServerConfigChecked]


@with_config(_STRICT)
class _PluginManifestOptional(TypedDict):
    """Every manifest field but `name`, optional (the parts' `.partial()` shapes)."""

    version: NotRequired[str]
    description: NotRequired[str]
    author: NotRequired[PluginAuthor]
    homepage: NotRequired[Annotated[str, _URL]]
    repository: NotRequired[str]
    license: NotRequired[str]
    keywords: NotRequired[list[str]]
    dependencies: NotRequired[list[DependencyRef]]
    hooks: NotRequired[_HooksEntry | list[_HooksEntry]]
    commands: NotRequired[RelativeCommandPath | list[RelativeCommandPath] | dict[str, _CommandMetadataChecked]]
    agents: NotRequired[RelativeMarkdownPath | list[RelativeMarkdownPath]]
    skills: NotRequired[RelativePath | list[RelativePath]]
    outputStyles: NotRequired[RelativePath | list[RelativePath]]
    channels: NotRequired[list[PluginManifestChannel]]
    mcpServers: NotRequired[_McpServersEntry | list[_McpServersEntry]]
    lspServers: NotRequired[_LspServersEntry | list[_LspServersEntry]]
    settings: NotRequired[dict[str, Any]]
    userConfig: NotRequired[dict[UserConfigKey, PluginUserConfigOption]]


@with_config(_STRICT)
class PluginManifest(_PluginManifestOptional):
    """`PluginManifestSchema`: plugin.json. Unknown top-level keys are stripped."""

    name: PluginName


# ── sources ───────────────────────────────────────────────────────────────────

_SCOPED_PACKAGE = re.compile(r"^@[a-z0-9][a-z0-9-._]*/[a-z0-9][a-z0-9-._]*\Z", re.ASCII)
_REGULAR_PACKAGE = re.compile(r"^[a-z0-9][a-z0-9-._]*\Z", re.ASCII)


def _npm_name_traversal_free(name: str) -> bool:
    return ".." not in name and "//" not in name


def _npm_name_format(name: str) -> bool:
    return _SCOPED_PACKAGE.search(name) is not None or _REGULAR_PACKAGE.search(name) is not None


#: `NpmPackageNameSchema`.
NpmPackageName: TypeAlias = Annotated[
    str,
    _refine(_npm_name_traversal_free, "Package name cannot contain path traversal patterns"),
    _refine(_npm_name_format, "Invalid npm package name format"),
]

_GIT_SHA = re.compile(r"^[a-f0-9]{40}\Z", re.ASCII)
#: `gitSha`: a full 40-character lowercase commit SHA.
GitSha: TypeAlias = Annotated[
    str,
    _refine(lambda s: len(s) >= 40, "Too small: expected string to have exactly 40 characters", "too_small"),
    _refine(lambda s: len(s) <= 40, "Too big: expected string to have exactly 40 characters", "too_big"),
    _regex(_GIT_SHA, "Must be a full 40-character lowercase git commit SHA"),
]


@with_config(_STRICT)
class NpmPluginSource(TypedDict):
    source: Literal["npm"]
    package: NpmPackageName | str
    version: NotRequired[str]
    registry: NotRequired[Annotated[str, _URL]]


@with_config(_STRICT)
class PipPluginSource(TypedDict):
    source: Literal["pip"]
    package: str
    version: NotRequired[str]
    registry: NotRequired[Annotated[str, _URL]]


@with_config(_STRICT)
class UrlPluginSource(TypedDict):
    source: Literal["url"]
    url: str
    ref: NotRequired[str]
    sha: NotRequired[GitSha]


@with_config(_STRICT)
class GithubPluginSource(TypedDict):
    source: Literal["github"]
    repo: str
    ref: NotRequired[str]
    sha: NotRequired[GitSha]


@with_config(_STRICT)
class GitSubdirPluginSource(TypedDict):
    source: Literal["git-subdir"]
    url: str
    path: Annotated[str, _min_length(1)]
    ref: NotRequired[str]
    sha: NotRequired[GitSha]


#: `PluginSource` (`PluginSourceSchema`): a `./` path in the marketplace, or a remote source.
PluginSource: TypeAlias = (
    RelativePath
    | NpmPluginSource
    | PipPluginSource
    | UrlPluginSource
    | GithubPluginSource
    | GitSubdirPluginSource
)


def _remote_source_only(entry: SettingsMarketplacePlugin) -> SettingsMarketplacePlugin:
    if isinstance(entry["source"], str):
        raise _custom(
            "Plugins in a settings-sourced marketplace must use remote sources (github, git-subdir, npm, url, pip). "
            'Relative-path sources like "./foo" have no marketplace repository to resolve against.'
        )
    return entry


@with_config(_STRICT)
class SettingsMarketplacePlugin(TypedDict):
    """`SettingsMarketplacePluginSchema`: a plugin entry declared inline in settings."""

    name: PluginName
    source: PluginSource
    description: NotRequired[str]
    version: NotRequired[str]
    strict: NotRequired[bool]


@with_config(_STRICT)
class UrlMarketplaceSource(TypedDict):
    source: Literal["url"]
    url: Annotated[str, _URL]
    headers: NotRequired[dict[str, str]]


@with_config(_STRICT)
class GithubMarketplaceSource(TypedDict):
    source: Literal["github"]
    repo: str
    ref: NotRequired[str]
    path: NotRequired[str]
    sparsePaths: NotRequired[list[str]]


@with_config(_STRICT)
class GitMarketplaceSource(TypedDict):
    source: Literal["git"]
    url: str
    ref: NotRequired[str]
    path: NotRequired[str]
    sparsePaths: NotRequired[list[str]]


@with_config(_STRICT)
class NpmMarketplaceSource(TypedDict):
    source: Literal["npm"]
    package: NpmPackageName


@with_config(_STRICT)
class FileMarketplaceSource(TypedDict):
    source: Literal["file"]
    path: str


@with_config(_STRICT)
class DirectoryMarketplaceSource(TypedDict):
    source: Literal["directory"]
    path: str


@with_config(_STRICT)
class HostPatternMarketplaceSource(TypedDict):
    source: Literal["hostPattern"]
    hostPattern: str


@with_config(_STRICT)
class PathPatternMarketplaceSource(TypedDict):
    source: Literal["pathPattern"]
    pathPattern: str


_SETTINGS_RESERVED_NAME = (
    "Reserved official marketplace names cannot be used with settings sources. validateOfficialNameSource only "
    "accepts github/git sources from anthropics/* for these names; a settings source would be rejected after "
    "loadAndCacheMarketplace has already written to disk with cleanupNeeded=false."
)


@with_config(_STRICT)
class SettingsMarketplaceSource(TypedDict):
    """The `settings` arm: a marketplace manifest declared inline in settings."""

    source: Literal["settings"]
    name: Annotated[
        MarketplaceName,
        _refine(lambda n: n.lower() not in ALLOWED_OFFICIAL_MARKETPLACE_NAMES, _SETTINGS_RESERVED_NAME),
    ]
    plugins: list[Annotated[SettingsMarketplacePlugin, AfterValidator(_remote_source_only)]]
    owner: NotRequired[PluginAuthor]


#: `MarketplaceSource` (`MarketplaceSourceSchema`), discriminated on `source`.
MarketplaceSource: TypeAlias = (
    UrlMarketplaceSource
    | GithubMarketplaceSource
    | GitMarketplaceSource
    | NpmMarketplaceSource
    | FileMarketplaceSource
    | DirectoryMarketplaceSource
    | HostPatternMarketplaceSource
    | PathPatternMarketplaceSource
    | SettingsMarketplaceSource
)
_MarketplaceSourceField: TypeAlias = Annotated[MarketplaceSource, Field(discriminator="source")]


def is_local_plugin_source(source: Any) -> bool:
    """`isLocalPluginSource`: a `./` path inside the marketplace."""
    return isinstance(source, str) and source.startswith("./")


def is_local_marketplace_source(source: Mapping[str, Any]) -> bool:
    """`isLocalMarketplaceSource`: a `file` / `directory` source (a user path, read-only)."""
    return source.get("source") in ("file", "directory")


# ── marketplaces ──────────────────────────────────────────────────────────────

@with_config(_STRICT)
class PluginMarketplaceEntry(_PluginManifestOptional):
    """`PluginMarketplaceEntrySchema`: the manifest, partial, plus the entry fields. `strict` defaults to True."""

    name: PluginName
    source: PluginSource
    category: NotRequired[str]
    tags: NotRequired[list[str]]
    strict: NotRequired[bool]


def _entry_defaults(entry: PluginMarketplaceEntry) -> PluginMarketplaceEntry:
    entry.setdefault("strict", True)
    return entry


_PluginMarketplaceEntryChecked: TypeAlias = Annotated[PluginMarketplaceEntry, AfterValidator(_entry_defaults)]


@with_config(_STRICT)
class PluginMarketplaceMetadata(TypedDict):
    pluginRoot: NotRequired[str]
    version: NotRequired[str]
    description: NotRequired[str]


@with_config(_STRICT)
class PluginMarketplace(TypedDict):
    """`PluginMarketplaceSchema`: marketplace.json."""

    name: MarketplaceName
    owner: PluginAuthor
    plugins: list[_PluginMarketplaceEntryChecked]
    forceRemoveDeletedPlugins: NotRequired[bool]
    metadata: NotRequired[PluginMarketplaceMetadata]
    allowCrossMarketplaceDependenciesOn: NotRequired[list[str]]


# ── ids and settings entries ──────────────────────────────────────────────────

#: `PluginIdSchema`'s pattern: `plugin@marketplace`.
PLUGIN_ID_PATTERN = re.compile(r"^[a-z0-9][-a-z0-9._]*@[a-z0-9][-a-z0-9._]*\Z", re.IGNORECASE | re.ASCII)
PluginId: TypeAlias = Annotated[str, _regex(PLUGIN_ID_PATTERN, "Plugin ID must be in format: plugin@marketplace")]


@with_config(_STRICT)
class SettingsPluginEntryObject(TypedDict):
    id: PluginId
    version: NotRequired[str]
    required: NotRequired[bool]
    config: NotRequired[dict[str, Any]]


#: `SettingsPluginEntrySchema`.
SettingsPluginEntry: TypeAlias = PluginId | SettingsPluginEntryObject

# ── installed plugins ─────────────────────────────────────────────────────────

#: `PluginScopeSchema`.
PluginScope: TypeAlias = Literal["managed", "user", "project", "local"]
PLUGIN_SCOPES: tuple[PluginScope, ...] = ("managed", "user", "project", "local")


@with_config(_STRICT)
class InstalledPlugin(TypedDict):
    """`InstalledPluginSchema` (V1)."""

    version: str
    installedAt: str
    lastUpdated: NotRequired[str]
    installPath: str
    gitCommitSha: NotRequired[str]


@with_config(_STRICT)
class InstalledPluginsFileV1(TypedDict):
    """`InstalledPluginsFileSchemaV1`."""

    version: Literal[1]
    plugins: dict[PluginId, InstalledPlugin]


@with_config(_STRICT)
class PluginInstallationEntry(TypedDict):
    """`PluginInstallationEntrySchema` (V2)."""

    scope: PluginScope
    projectPath: NotRequired[str]
    installPath: str
    version: NotRequired[str]
    installedAt: NotRequired[str]
    lastUpdated: NotRequired[str]
    gitCommitSha: NotRequired[str]


@with_config(_STRICT)
class InstalledPluginsFileV2(TypedDict):
    """`InstalledPluginsFileSchemaV2`."""

    version: Literal[2]
    plugins: dict[PluginId, list[PluginInstallationEntry]]


@with_config(_STRICT)
class KnownMarketplace(TypedDict):
    """`KnownMarketplaceSchema`: one entry of known_marketplaces.json."""

    source: _MarketplaceSourceField
    installLocation: str
    lastUpdated: str
    autoUpdate: NotRequired[bool]


#: `KnownMarketplacesFile`.
KnownMarketplacesFile: TypeAlias = dict[str, KnownMarketplace]

# ── adapters (`lazySchema`) ───────────────────────────────────────────────────


_ADAPTERS: dict[str, TypeAdapter[Any]] = {}


@dataclass(frozen=True)
class Schema(Generic[T]):
    """
    One schema, the stand-in for a Zod schema object (`safe_parse` / `parse`). Its pydantic
    adapter is built on first use and kept (`lazySchema`).
    """

    kind: str
    target: Any = field(compare=False)

    def adapter(self) -> TypeAdapter[T]:
        adapter = _ADAPTERS.get(self.kind)
        if adapter is None:
            adapter = TypeAdapter(self.target)
            _ADAPTERS[self.kind] = adapter
        return cast("TypeAdapter[T]", adapter)


PluginAuthorSchema: Schema[PluginAuthor] = Schema("PluginAuthor", PluginAuthor)
PluginManifestSchema: Schema[PluginManifest] = Schema("PluginManifest", PluginManifest)
PluginHooksSchema: Schema[PluginHooks] = Schema("PluginHooks", PluginHooks)
CommandMetadataSchema: Schema[CommandMetadata] = Schema("CommandMetadata", _CommandMetadataChecked)
LspServerConfigSchema: Schema[LspServerConfig] = Schema("LspServerConfig", _LspServerConfigChecked)
MarketplaceSourceSchema: Schema[MarketplaceSource] = Schema("MarketplaceSource", _MarketplaceSourceField)
PluginSourceSchema: Schema[PluginSource] = Schema("PluginSource", PluginSource)
PluginMarketplaceEntrySchema: Schema[PluginMarketplaceEntry] = Schema(
    "PluginMarketplaceEntry", _PluginMarketplaceEntryChecked
)
PluginMarketplaceSchema: Schema[PluginMarketplace] = Schema("PluginMarketplace", PluginMarketplace)
PluginIdSchema: Schema[str] = Schema("PluginId", PluginId)
DependencyRefSchema: Schema[str] = Schema("DependencyRef", DependencyRef)
SettingsPluginEntrySchema: Schema[SettingsPluginEntry] = Schema("SettingsPluginEntry", SettingsPluginEntry)
InstalledPluginSchema: Schema[InstalledPlugin] = Schema("InstalledPlugin", InstalledPlugin)
InstalledPluginsFileSchemaV1: Schema[InstalledPluginsFileV1] = Schema("InstalledPluginsFileV1", InstalledPluginsFileV1)
InstalledPluginsFileSchemaV2: Schema[InstalledPluginsFileV2] = Schema("InstalledPluginsFileV2", InstalledPluginsFileV2)
InstalledPluginsFileSchema: Schema[InstalledPluginsFileV1 | InstalledPluginsFileV2] = Schema(
    "InstalledPluginsFile", InstalledPluginsFileV1 | InstalledPluginsFileV2
)
PluginInstallationEntrySchema: Schema[PluginInstallationEntry] = Schema(
    "PluginInstallationEntry", PluginInstallationEntry
)
PluginScopeSchema: Schema[PluginScope] = Schema("PluginScope", PluginScope)
KnownMarketplaceSchema: Schema[KnownMarketplace] = Schema("KnownMarketplace", KnownMarketplace)
KnownMarketplacesFileSchema: Schema[KnownMarketplacesFile] = Schema("KnownMarketplacesFile", KnownMarketplacesFile)
PluginUserConfigOptionSchema: Schema[PluginUserConfigOption] = Schema(
    "PluginUserConfigOption", PluginUserConfigOption
)
MarketplaceNameSchema: Schema[str] = Schema("MarketplaceName", MarketplaceName)

# ── safeParse / parse ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SchemaIssue:
    """A Zod issue: `path` (keys and indexes), `message`, `code`."""

    path: tuple[str | int, ...]
    message: str
    code: str

    def path_string(self) -> str:
        """`issue.path.join('.')`."""
        return ".".join(str(p) for p in self.path)


@dataclass(frozen=True)
class SafeParseResult(Generic[T]):
    """`SafeParseReturnType`: `success` with `data`, or the `issues`."""

    success: bool
    data: T | None
    issues: tuple[SchemaIssue, ...] = ()


class SchemaValidationError(ValueError):
    """A `ZodError`: `issues`, and Zod's JSON rendering of them as the message."""

    def __init__(self, issues: Sequence[SchemaIssue]) -> None:
        self.issues = tuple(issues)
        rendered = [{"code": i.code, "path": list(i.path), "message": i.message} for i in self.issues]
        super().__init__(json.dumps(rendered, indent=2))


def format_issues(issues: Sequence[SchemaIssue]) -> str:
    """`issues.map(e => `${e.path.join('.')}: ${e.message}`).join(', ')`, upstream's error rendering."""
    return ", ".join(f"{i.path_string()}: {i.message}" for i in issues)


def _type_name(value: Any) -> str:
    """Zod 4's `received` type name."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


_EXPECTED_BY_TYPE = {
    "string_type": "string",
    "bool_type": "boolean",
    "int_type": "int",
    "float_type": "number",
    "list_type": "array",
    "dict_type": "record",
    "model_type": "object",
    "typed_dict_type": "object",
}


@dataclass(frozen=True)
class _Walked:
    path: tuple[str | int, ...]
    reached: Any
    missing: bool
    collapsed: bool
    bad_key: bool


def _walk(value: Any, loc: Sequence[str | int], kind: str) -> _Walked:
    """
    Follow pydantic's `loc` through the input to Zod's path. A segment naming the value's
    `source` tag is a discriminated-union branch (Zod reports inside it); `[key]` is a record
    key; the last segment of a `missing` error is the absent key; any other segment that is not
    a key or index is a plain-union branch label, which Zod reports as one `invalid_union` at
    the union's own path.
    """
    path: list[str | int] = []
    current: Any = value
    tagged: set[int] = set()
    for i, segment in enumerate(loc):
        last = i == len(loc) - 1
        if isinstance(current, dict) and current.get("source") == segment and id(current) not in tagged:
            tagged.add(id(current))
        elif (isinstance(current, dict) and segment in current) or (
            isinstance(current, list) and isinstance(segment, int) and 0 <= segment < len(current)
        ):
            path.append(segment)
            current = cast(Any, current)[segment]
        elif segment == "[key]":
            return _Walked(tuple(path), current, False, False, True)
        elif last and kind == "missing" and isinstance(current, dict):
            path.append(segment)
            return _Walked(tuple(path), None, True, False, False)
        else:
            return _Walked(tuple(path), current, False, True, False)
    return _Walked(tuple(path), current, False, False, False)


def _issue_from_error(error: Mapping[str, Any], value: Any) -> SchemaIssue:
    kind = str(error["type"])
    walked = _walk(value, error.get("loc", ()), kind)
    path = walked.path
    if walked.bad_key:
        return SchemaIssue(path, "Invalid key in record", "invalid_key")
    if walked.collapsed:
        return SchemaIssue(path, "Invalid input", "invalid_union")
    if walked.missing:
        return SchemaIssue(path, "Invalid input: expected value, received undefined", "invalid_type")
    if kind in _EXPECTED_BY_TYPE:
        received = _type_name(walked.reached)
        return SchemaIssue(path, f"Invalid input: expected {_EXPECTED_BY_TYPE[kind]}, received {received}",
                           "invalid_type")
    if kind == "extra_forbidden":
        return SchemaIssue(path[:-1], f'Unrecognized key: "{path[-1] if path else ""}"', "unrecognized_keys")
    if kind == "literal_error":
        expected = str((error.get("ctx") or {}).get("expected", "")).replace("'", '"')
        if " or " in expected or "," in expected:
            options = "|".join(part.strip() for part in re.split(r",| or ", expected) if part.strip())
            return SchemaIssue(path, f"Invalid option: expected one of {options}", "invalid_value")
        return SchemaIssue(path, f"Invalid input: expected {expected}", "invalid_value")
    if kind in ("union_tag_invalid", "union_tag_not_found"):
        # Zod reports a discriminator mismatch at the discriminator itself.
        return SchemaIssue((*path, "source"), "Invalid input", "invalid_union")
    ctx = error.get("ctx") or {}
    if kind == "value_error" and "error" in ctx:
        return SchemaIssue(path, str(ctx["error"]), "custom")
    return SchemaIssue(path, str(error.get("msg", "Invalid input")), kind)


def issues_from_validation_error(exc: ValidationError, value: Any) -> tuple[SchemaIssue, ...]:
    """A pydantic `ValidationError` as Zod issues, in order, without repeats."""
    issues: list[SchemaIssue] = []
    unknown_keys: dict[tuple[str | int, ...], list[str]] = {}
    for error in exc.errors():
        issue = _issue_from_error(error, value)
        if issue.code == "unrecognized_keys":
            # `.strict()` reports one issue per object, naming every unknown key.
            keys = unknown_keys.setdefault(issue.path, [])
            if not keys:
                issues.append(issue)
            keys.append(issue.message.removeprefix("Unrecognized key: "))
            continue
        if issue not in issues:
            issues.append(issue)
    return tuple(_merged_unrecognized(i, unknown_keys) for i in issues)


def _merged_unrecognized(
    issue: SchemaIssue, unknown_keys: Mapping[tuple[str | int, ...], Sequence[str]]
) -> SchemaIssue:
    if issue.code != "unrecognized_keys":
        return issue
    keys = unknown_keys[issue.path]
    plural = "s" if len(keys) > 1 else ""
    return SchemaIssue(issue.path, f"Unrecognized key{plural}: {', '.join(keys)}", issue.code)


def safe_parse(schema: Schema[T], value: Any) -> SafeParseResult[T]:
    """`schema.safeParse(value)`. The input is never mutated: the data is a fresh copy."""
    try:
        data = schema.adapter().validate_python(_deep_copy(value))
    except ValidationError as exc:
        return SafeParseResult(False, None, issues_from_validation_error(exc, value))
    return SafeParseResult(True, data)


def parse(schema: Schema[T], value: Any) -> T:
    """`schema.parse(value)`: the data, or `SchemaValidationError`."""
    result = safe_parse(schema, value)
    if not result.success:
        raise SchemaValidationError(result.issues)
    return cast(T, result.data)


def _deep_copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _deep_copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_copy(v) for v in value]
    return value


def unrecognized_key_issues(
    value: Any, known: Sequence[str] | frozenset[str], path: tuple[str | int, ...] = ()
) -> list[SchemaIssue]:
    """Zod's `.strict()` check for one object: an `unrecognized_keys` issue for each unknown key."""
    if not isinstance(value, dict):
        return []
    unknown = [k for k in value if k not in known]
    if not unknown:
        return []
    keys = ", ".join(f'"{k}"' for k in unknown)
    plural = "s" if len(unknown) > 1 else ""
    return [SchemaIssue(path, f"Unrecognized key{plural}: {keys}", "unrecognized_keys")]


def typed_dict_keys(typed_dict: Any) -> frozenset[str]:
    """Every key a `TypedDict` declares (the shape `.strict()` checks against)."""
    return frozenset(typed_dict.__required_keys__) | frozenset(typed_dict.__optional_keys__)


__all__ = [
    "ALLOWED_OFFICIAL_MARKETPLACE_NAMES",
    "BLOCKED_OFFICIAL_NAME_PATTERN",
    "DEP_REF_REGEX",
    "NO_AUTO_UPDATE_OFFICIAL_MARKETPLACES",
    "OFFICIAL_GITHUB_ORG",
    "PLUGIN_ID_PATTERN",
    "PLUGIN_SCOPES",
    "CommandMetadata",
    "DirectoryMarketplaceSource",
    "FileMarketplaceSource",
    "GitMarketplaceSource",
    "GitSubdirPluginSource",
    "GithubMarketplaceSource",
    "GithubPluginSource",
    "InstalledPlugin",
    "InstalledPluginsFileV1",
    "InstalledPluginsFileV2",
    "KnownMarketplace",
    "KnownMarketplacesFile",
    "LspServerConfig",
    "MarketplaceSource",
    "NpmPluginSource",
    "PipPluginSource",
    "PluginAuthor",
    "PluginHooks",
    "PluginInstallationEntry",
    "PluginManifest",
    "PluginManifestChannel",
    "PluginMarketplace",
    "PluginMarketplaceEntry",
    "PluginScope",
    "PluginSource",
    "PluginUserConfigOption",
    "SafeParseResult",
    "Schema",
    "SchemaIssue",
    "SchemaValidationError",
    "SettingsMarketplacePlugin",
    "SettingsMarketplaceSource",
    "UrlMarketplaceSource",
    "UrlPluginSource",
    "format_issues",
    "is_blocked_official_name",
    "is_local_marketplace_source",
    "is_local_plugin_source",
    "is_marketplace_auto_update",
    "is_url",
    "issues_from_validation_error",
    "normalize_dependency_ref",
    "parse",
    "safe_parse",
    "typed_dict_keys",
    "unrecognized_key_issues",
    "validate_official_name_source",
]
