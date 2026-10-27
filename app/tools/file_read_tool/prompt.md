---
name: ReadFile
description: Reads a file from the workspace.
---
Reads a file from the workspace filesystem. It is okay to read a file that does not exist; an error will be returned.

Usage:
- Paths are workspace-relative.
- By default it reads from the beginning of the file. `offset` is the line number to start from and `limit` the number of lines; only provide them when the file is too large to read at once.
- Results are returned with line numbers: line number + tab, then the line content.
- This tool can only read files, not directories. Use `Glob` to discover files.
- Read a file before editing or overwriting it with `Edit` or `WriteFile`; both refuse to touch an existing file you have not read.
- If you read a file that exists but has empty contents you will receive a system reminder warning in place of file contents.
