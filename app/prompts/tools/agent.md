---
name: Agent
description: Launches a specialized subagent or fork to execute an autonomous task in an isolated context.
aliases: [Task]
---
Spawns an independent subagent or forked child to perform focused work on a specific task.

Each subagent operates within its own **isolated context window** and tool pool, configured according to its agent definition and TDD phase constraints. This isolation protects the caller's main context from pollution by large search results, verbose logs, or exploratory dead ends.

Usage:
- Provide `prompt` detailing the specific directive or task for the subagent to execute.
- Optionally provide `subagent_type` (e.g., `developer`, `tester`, `refactorer`, `explore`, `plan`, `verification`). If omitted, defaults to an implicit fork of the current conversation context.
- Set `run_in_background: true` to execute asynchronously and receive an immediate background task handle.
- Subagents return a concise summary and terminal status to the caller upon completion.

When to use subagents:
- **Explore**: When you need to search, navigate, or map unfamiliar parts of the codebase without polluting your main context.
- **Plan**: When a complex task requires architectural analysis and step-by-step planning before implementation.
- **Verification**: When you need an independent, impartial check that tests pass and changes are correct.
- Prefer subagents for any reconnaissance or exploration that may produce large volumes of output.
