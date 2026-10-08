---
name: "Data: Windows transcript read EBADF notice"
description: "Explains a Windows-only EBADF error reading the session transcript file likely caused by security or endpoint-management software intercepting file reads, and suggests excluding the .claude folder or allow-listing Claude Code before resuming"
type: "data-prompts"
---

Windows reported an error (EBADF) when {agent_name} read this session's transcript file, although the file had opened normally. This can happen when other software intercepts file reads — security, encryption or endpoint-management tools, for example. If it keeps happening for this conversation, try excluding the folder that holds {agent_name}'s session transcripts from such software (the .claude folder in your user profile, unless the app or CLAUDE_CONFIG_DIR points {agent_name} elsewhere), or adding {agent_name} to its allowed applications, then resume again.


> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
