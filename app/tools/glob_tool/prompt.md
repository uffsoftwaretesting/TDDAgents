---
name: Glob
description: Fast file pattern matching across the workspace.
---
Fast file pattern matching that works with any workspace size.

Usage:
- Supports glob patterns like `**/*.py` or `tests/test_*.py`. A pattern without `/` matches file names at any depth.
- Optionally provide `path`, the directory to search in. Omit it to search the workspace root.
- Returns matching workspace-relative file paths. Results are capped; a truncation note tells you to narrow the pattern or path.
- Use this tool when you need to find files by name patterns; use `Grep` to search contents.
