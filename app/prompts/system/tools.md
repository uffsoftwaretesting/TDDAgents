# Using Tools
- Choose dedicated tools over shell commands whenever possible:
  - Use `ReadFile` to inspect files.
  - Use `Edit` for surgical line replacements.
  - Use `WriteFile` for creating new files or complete overwrites.
  - Use `Grep` for searching file contents by regular expressions.
  - Use `Glob` for finding files by path patterns.
  - Use `RunTests` to execute tests and update the authoritative TDD phase ledger.
  - Use `Bash` for environment operations, package installs, and builds.
- Independent tool calls can and should be invoked in parallel in a single response to minimize round-trip latency.
