"""
Walk a plugin directory for markdown files.

Ported from `reference/claude-code/src/utils/plugins/walkPluginMarkdown.ts` -> `SKILL_MD_RE`, `walkPluginMarkdown`.

`on_file(full_path, namespace)` runs for each `.md` file; `namespace` is the subdirectory path
under the root. With `stop_at_skill_dir`, a directory holding `SKILL.md` is a leaf: its `.md`
files are reported and its subdirectories are not scanned. A directory that cannot be read is
logged and skipped. Upstream fans the callbacks out with `Promise.all`; here they are gathered
the same way (`asyncio.gather`).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Awaitable, Callable

logger = logging.getLogger("TDDOrchestrator.Plugins")

#: `SKILL_MD_RE`.
SKILL_MD_RE = re.compile(r"^skill\.md\Z", re.IGNORECASE | re.ASCII)


async def walk_plugin_markdown(
    root_dir: str,
    on_file: Callable[[str, list[str]], Awaitable[None]],
    *,
    stop_at_skill_dir: bool = False,
    log_label: str | None = None,
) -> None:
    """`walkPluginMarkdown`."""
    label = log_label or "plugin"

    async def scan(dir_path: str, namespace: list[str]) -> None:
        try:
            with os.scandir(dir_path) as iterator:
                entries = list(iterator)
            if stop_at_skill_dir and any(
                e.is_file(follow_symlinks=False) and SKILL_MD_RE.search(e.name) is not None for e in entries
            ):
                await asyncio.gather(*(
                    on_file(os.path.join(dir_path, e.name), namespace)
                    for e in entries
                    if e.is_file(follow_symlinks=False) and e.name.lower().endswith(".md")
                ))
                return
            tasks: list[Awaitable[None]] = []
            for entry in entries:
                full_path = os.path.join(dir_path, entry.name)
                if entry.is_dir(follow_symlinks=False):
                    tasks.append(scan(full_path, [*namespace, entry.name]))
                elif entry.is_file(follow_symlinks=False) and entry.name.lower().endswith(".md"):
                    tasks.append(on_file(full_path, namespace))
            await asyncio.gather(*tasks)
        except Exception as exc:  # noqa: BLE001 - one bad directory must not abort a plugin load
            logger.error("Failed to scan %s directory %s: %s", label, dir_path, exc)

    await scan(root_dir, [])


__all__ = ["SKILL_MD_RE", "walk_plugin_markdown"]
