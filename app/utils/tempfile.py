"""
Temporary file paths.

Ported from `reference/claude-code/src/utils/tempfile.ts` -> `generateTempFilePath`. With a content hash
the name is the first 16 hex digits of the content's SHA-256, so the same content gets the same path in
every process (upstream's reason: the path reaches the prompt, and a new name per process would break the
prompt cache); without one it is a random UUID. The default prefix names TDDAgents where upstream names
Claude.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import uuid


def generate_temp_file_path(prefix: str = "tddagents-prompt", extension: str = ".md", *,
                            content_hash: str | None = None) -> str:
    """`generateTempFilePath`: `<tmpdir>/<prefix>-<id><extension>`."""
    identifier = (hashlib.sha256(content_hash.encode("utf-8")).hexdigest()[:16] if content_hash
                  else str(uuid.uuid4()))
    return os.path.join(tempfile.gettempdir(), f"{prefix}-{identifier}{extension}")


__all__ = ["generate_temp_file_path"]
