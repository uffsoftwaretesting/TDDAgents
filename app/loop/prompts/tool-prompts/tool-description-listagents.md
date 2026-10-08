---
name: "Tool Description: ListAgents"
description: "Describes the ListAgents tool, which lists agents you can message — in-process subagents, teammates, other local and cloud Claude sessions, and remote bridge sessions"
type: "tool-prompts"
---

Lists agents you can ${SEND_MESSAGE_TOOL_NAME} to — in-process subagents you spawned, the teammates on your team, other local {agent_name} sessions on this machine, your {agent_name} sessions running in the cloud (when this session has cloud access; a cloud session receives your message but cannot message any session back yet — do not ask it to reply, read its answer in its own transcript), and (when Remote Control is connected here) your account's other sessions — Remote Control sessions on other machines and cloud sessions, each row labeled by kind. Names are the address: send with `${SEND_MESSAGE_TOOL_NAME}({to: "<name>", message: "..."})`, copying the name exactly as a row prints it. Append a row's ` [ref]` only when the bare name is not enough — two rows share it, or an error asks you to disambiguate.

### Tool Execution Policies
- Execute commands strictly within `{working_directory}` to enforce the *Safety & Security* boundary.
- Run tools in the core while-loop. Validate results for *Reliable Execution*.
- Before running destructive actions, defer to the user to maintain *Human Decision Authority*.



> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
