"""
Editing a file in the user's external editor (2026-10-29).

Ported from `reference/claude-code/src/utils/promptEditor.ts` -> `editFileInEditor`,
`EDITOR_OVERRIDES`, and `utils/editor.ts` -> `getExternalEditor`. Upstream opens the plan file
this way when the user presses ctrl-g at the plan approval; here it opens the specification or
the plan under review (`/edit`, `app/session/driver.py`).

*Divergences:* there is no Ink renderer to pause (the REPL reads a line, then the editor owns the
terminal until it exits), `getExternalEditor` is not memoized, and the Windows `notepad`
fallback is not ported.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

#: `EDITOR_OVERRIDES`: GUI editors told to wait until the file is closed.
EDITOR_OVERRIDES: dict[str, str] = {
    "code": "code -w",
    "subl": "subl --wait",
}

#: `getExternalEditor`'s search order when neither `$VISUAL` nor `$EDITOR` is set.
FALLBACK_EDITORS = ("code", "vi", "nano")


@dataclass(frozen=True, slots=True)
class EditorResult:
    """`EditorResult`: the edited content, or `None` with why it could not be read."""

    content: str | None
    error: str | None = None


def get_external_editor(which: Callable[[str], str | None] = shutil.which) -> str | None:
    """`getExternalEditor`: `$VISUAL`, then `$EDITOR`, then the first fallback on `PATH`."""
    for name in ("VISUAL", "EDITOR"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return next((editor for editor in FALLBACK_EDITORS if which(editor)), None)


def edit_file_in_editor(path: Path, editor: str | None = None) -> EditorResult:
    """`editFileInEditor`: run the editor on `path` and read the file back once it exits."""
    editor = editor or get_external_editor()
    if not editor or not path.is_file():
        return EditorResult(None)
    command = EDITOR_OVERRIDES.get(editor, editor)
    # Upstream: `execSync(\`${editorCommand} "${filePath}"\`, { stdio: 'inherit' })`.
    status = subprocess.call(f'{command} "{path}"', shell=True)  # the user's own editor command, as upstream
    if status != 0:
        return EditorResult(None, f"{editor} exited with code {status}")
    return EditorResult(path.read_text(encoding="utf-8"))


__all__ = ["EDITOR_OVERRIDES", "FALLBACK_EDITORS", "EditorResult", "edit_file_in_editor", "get_external_editor"]
