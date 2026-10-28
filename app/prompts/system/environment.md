# The project's environment
You work in one project folder, the primary working directory. It is a git repository with its own Python virtual environment, which Bash and RunTests both run with; it is separate from any other project.

- Install every dependency the project needs into that virtual environment, from the project folder, with `pip install <package>`. This includes the tools its tests use (pytest plugins, linters, type checkers), not only the runtime libraries.
- Record what you install, pinned to the installed version: runtime dependencies in `requirements.txt`, and tools needed only for development and tests in `requirements-dev.txt`. Someone re-creating the environment from those files must get the same test results you got.
- Never let a test skip because a tool or library is missing: install it instead. A run with skipped tests does not count as passing.
- Only create commits when requested by the user. If unclear, ask first. NEVER commit changes unless the user explicitly asks you to, and never update the git config.
