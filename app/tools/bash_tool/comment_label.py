"""
A Bash command's `# comment` label.

Ported from `reference/claude-code/src/tools/BashTool/commentLabel.ts` -> `extractBashCommentLabel`:
under fullscreen mode the collapse group's hint for a bash command is what the model wrote for
the human to read.
"""

from __future__ import annotations

import re

_HASHES = re.compile(r"^#+\s*")


def extract_bash_comment_label(command: str) -> str | None:
    """The first line's `# comment` text (never a `#!` shebang), else `None`."""
    first_line = command.split("\n", 1)[0].strip()
    if not first_line.startswith("#") or first_line.startswith("#!"):
        return None
    return _HASHES.sub("", first_line) or None


__all__ = ["extract_bash_comment_label"]
