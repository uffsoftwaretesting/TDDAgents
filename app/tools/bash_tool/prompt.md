---
name: Bash
description: Executes a shell command and returns stdout and stderr.
---
Runs a command in the bash shell within the workspace environment.

Usage:
- Provide `command` string to execute.
- Do not use for file reading or simple search when `ReadFile`, `Grep`, or `Glob` can be used instead.
- Optionally provide `timeout` in milliseconds and a short `description` of what the command does.
- Every command passes a deterministic command-injection check before it runs. Command substitution (`$()`, backticks), obfuscated flags, brace expansion, unquoted redirections, and similar constructs are refused with the reason; rewrite the command without them.
