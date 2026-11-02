"""
Parse and validate an MCPB/DXT `manifest.json`, and derive an extension id.

Ported from `reference/claude-code/src/utils/dxt/helpers.ts` -> `validateManifest`,
`parseAndValidateManifestFromText`, `parseAndValidateManifestFromBytes`, `generateExtensionId`.

The schema is `@anthropic-ai/mcpb`'s `McpbManifestSchema` (`app/utils/dxt/mcpb.py`). A failure
reads as upstream's: Zod 3's `error.flatten()` (`fieldErrors` grouped by the issue's first path
segment in first-seen order, then the path-less `formErrors`), `field: msg, msg` parts joined
with `; `, after `Invalid manifest: `.

Divergence: a JSON syntax error is Python's `json` message (`Expecting value: line 1 column 1
(char 0)`), not V8's `JSON.parse` wording, after the same `Invalid JSON in manifest.json: ` prefix.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from app.utils.dxt.mcpb import McpbManifest, safe_parse_manifest

_WHITESPACE = re.compile(r"\s+")
_UNSAFE_ID_CHARS = re.compile(r"[^a-z0-9\-_.]")
_DASHES = re.compile(r"-+")
_EDGE_DASHES = re.compile(r"^-+|-+\Z")


async def validate_manifest(manifest_json: Any) -> McpbManifest:
    """`validateManifest`: the parsed manifest, or `ValueError('Invalid manifest: ...')`."""
    result = safe_parse_manifest(manifest_json)
    if result.success and result.data is not None:
        return result.data
    field_errors: dict[str, list[str]] = {}
    form_errors: list[str] = []
    for issue in result.issues:
        if issue.path:
            field_errors.setdefault(str(issue.path[0]), []).append(issue.message)
        else:
            form_errors.append(issue.message)
    parts = [f"{name}: {', '.join(errors)}" for name, errors in field_errors.items()] + form_errors
    raise ValueError(f"Invalid manifest: {'; '.join(p for p in parts if p)}")


async def parse_and_validate_manifest_from_text(manifest_text: str) -> McpbManifest:
    """`parseAndValidateManifestFromText`."""
    try:
        manifest_json = json.loads(manifest_text)
    except ValueError as exc:
        raise ValueError(f"Invalid JSON in manifest.json: {exc}") from exc
    return await validate_manifest(manifest_json)


async def parse_and_validate_manifest_from_bytes(manifest_data: bytes) -> McpbManifest:
    """`parseAndValidateManifestFromBytes`: UTF-8 (`TextDecoder`: a leading BOM dropped, bad bytes replaced)."""
    text = manifest_data.decode("utf-8", errors="replace").removeprefix("﻿")
    return await parse_and_validate_manifest_from_text(text)


def _sanitize(value: str) -> str:
    out = _WHITESPACE.sub("-", value.lower())
    out = _UNSAFE_ID_CHARS.sub("", out)
    out = _DASHES.sub("-", out)
    return _EDGE_DASHES.sub("", out)


def generate_extension_id(
    manifest: McpbManifest, prefix: Literal["local.unpacked", "local.dxt"] | None = None
) -> str:
    """`generateExtensionId`: `[<prefix>.]<author>.<name>`, each part sanitized."""
    sanitized_author = _sanitize(manifest["author"]["name"])
    sanitized_name = _sanitize(manifest["name"])
    if prefix:
        return f"{prefix}.{sanitized_author}.{sanitized_name}"
    return f"{sanitized_author}.{sanitized_name}"


__all__ = [
    "generate_extension_id",
    "parse_and_validate_manifest_from_bytes",
    "parse_and_validate_manifest_from_text",
    "validate_manifest",
]
