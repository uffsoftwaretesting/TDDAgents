---
name: "System Reminder: File truncated"
description: "Notification that file was truncated due to size"
type: "system-prompts"
---

Note: The file ${ESCAPE_UNTRUSTED_TEXT_FN(ATTACHMENT_OBJECT.filename)} was too large and has been truncated to the first ${MAX_LINES_CONSTANT} lines. No need to mention the truncation. Use ${READ_TOOL_NAME} to read more of the file if you need.


> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
