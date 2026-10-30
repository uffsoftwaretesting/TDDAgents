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


def edit_text_in_editor(
    text: str, name: str, edit: Callable[[Path], EditorResult] = edit_file_in_editor
) -> tuple[str | None, str]:
    """
    `text` in a draft file (`<TDDAgents temp dir>/<name>-*.md`, deleted afterwards), opened in the
    user's editor: the edited text, or `None` and why there is none (no editor, a failed editor,
    no change).
    """
    import tempfile

    from app.utils.permissions.filesystem import get_tddagents_temp_dir

    directory = Path(get_tddagents_temp_dir())
    directory.mkdir(parents=True, exist_ok=True)
    fd, draft = tempfile.mkstemp(prefix=f"{name}-", suffix=".md", dir=directory)
    path = Path(draft)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text if text.endswith("\n") else f"{text}\n")
        result = edit(path)
    finally:
        path.unlink(missing_ok=True)
    if result.content is None:
        return None, result.error or "No editor found: set $VISUAL or $EDITOR."
    if result.content.strip() == text.strip():
        return None, f"The {name} is unchanged."
    return result.content, ""


__all__ = [
    "EDITOR_OVERRIDES",
    "FALLBACK_EDITORS",
    "EditorResult",
    "edit_file_in_editor",
    "edit_text_in_editor",
    "get_external_editor",
]
