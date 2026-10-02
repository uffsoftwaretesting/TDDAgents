---
name: Agent
description: Launches a specialized subagent or fork to execute an autonomous task.
aliases: [Task]
---
Spawns an independent subagent or forked child to perform focused work on a specific task.

Usage:
- Provide `prompt` detailing the specific directive or task for the subagent to execute.
- Optionally provide `subagent_type` (e.g. 'developer', 'tester', 'refactorer', 'explore', 'plan', 'verification'). If omitted, defaults to an implicit fork of the current conversation context.
- Set `run_in_background: true` to execute asynchronously and receive an immediate background task handle.
- Each subagent operates within its own isolated context and tool pool configured according to its agent definition and TDD phase constraints.
- Subagents return a concise summary and terminal status to the caller upon completion.
