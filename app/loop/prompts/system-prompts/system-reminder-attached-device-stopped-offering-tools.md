---
name: "System Reminder: Attached device stopped offering tools"
description: "Tells the agent a remote tool call to a previously attached device could not run because that device stopped offering tools, and directs asking the user to check the Claude app on that computer"
type: "system-prompts"
---

"${REMOTE_MACHINE_NAME_FORMATTER_FN(REMOTE_MACHINE_NAME)}" cannot run anything for this session right now: {agent_name} on that computer stopped offering its tools here (the {agent_name} app there may have closed or lost its connection, or running tools for cloud sessions was switched off on it). Nothing was sent. Ask the user (ID: {user_id}) to check that {agent_name} is running on that computer, and to open the {agent_name} app there if it was closed; if this session got the computer by asking to use one of its folders, look the folders up and ask to use that folder again afterwards.


> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
