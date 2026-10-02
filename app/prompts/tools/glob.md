---
name: Glob
description: Finds files in the workspace matching a glob pattern.
---
Searches for files by pattern matching against filesystem paths.

Usage:
- Provide `pattern` (e.g., `**/*.py`, `tests/test_*.py`, `src/**/*.ts`).
- Returns matching relative file paths sorted predictably.
- Ideal for discovering project structure, locating specific module directories, and finding files by extension.
- Use this before Grep when you need to narrow the search scope to specific file types.
