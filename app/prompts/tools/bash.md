---
name: Bash
description: Executes a shell command and returns stdout and stderr.
---
Runs a command in the bash shell within the workspace environment.

Usage:
- Provide `command` string to execute.
- Do not use for file reading or simple search when `ReadFile`, `Grep`, or `Glob` can be used instead.
- Commands run with fail-closed safety checks. Unsafe system commands or destructive operations outside workspace boundaries are blocked.
