---
name: Grep
description: Searches file contents across the workspace using regular expressions.
---
A content search tool.

Usage:
- ALWAYS use `Grep` for search tasks. NEVER invoke `grep` or `rg` as a `Bash` command.
- Supports regular expression syntax (e.g. `log.*Error`, `def\s+\w+`).
- Filter files with `glob` (e.g. `*.py`, `*.{ts,tsx}`) and narrow the search with `path`.
- Output modes: `files_with_matches` (default) shows only file paths, `content` shows matching lines, `count` shows match counts per file.
- `-i` makes the search case insensitive; `-n` toggles line numbers in content mode.
- Results are paginated: `head_limit` caps entries (0 for unlimited) and `offset` skips entries. A pagination note appears when results were truncated.
