---
name: Grep
description: Searches file contents across the workspace using regular expressions.
---
Searches files in the workspace for content matching a regex pattern.

Usage:
- Provide `pattern` (regular expression) and optional `path` or `include_glob` to narrow scope.
- Returns matching lines with file paths and line numbers.
- Ideal for finding symbol definitions, references, error strings, imports, and usage patterns.
- Prefer this over `Bash` with `grep` — the dedicated tool provides structured output and respects workspace boundaries.
