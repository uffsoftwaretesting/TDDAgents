---
name: "System Reminder: Hook additional context"
description: "Additional context from a hook"
type: "system-prompts"
---

${ATTACHMENT_OBJECT.hookName} hook additional context: ${ATTACHMENT_OBJECT.content.join(`
`)}


> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
