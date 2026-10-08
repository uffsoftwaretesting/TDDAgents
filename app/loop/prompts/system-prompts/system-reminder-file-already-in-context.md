---
name: "System Reminder: File already in context"
description: "Tells Claude that a file is already loaded in context and unchanged on disk, so it should use the existing content instead of re-reading"
type: "system-prompts"
---

${FILE_ALREADY_IN_CONTEXT_REMINDER_PREFIX} (see "Contents of ${FILE_PATH}" above) and has not changed on disk. Use that content instead of re-reading.</system-reminder>


> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
