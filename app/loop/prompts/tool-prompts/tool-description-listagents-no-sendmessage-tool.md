---
name: "Tool Description: ListAgents (no SendMessage tool)"
description: "Describes the ListAgents tool for sessions without a SendMessage tool: it lists visible agents and Claude sessions plus this session's own name, and explains that replies must go through the host application's messaging tool or be relayed to the user"
type: "tool-prompts"
---

Lists the agents and {agent_name} sessions this session can see — in-process subagents you spawned, the teammates on your team, other local {agent_name} sessions on this machine, your {agent_name} sessions running in the cloud (when this session has cloud access), and (when Remote Control is connected here) your account's other sessions, each row labeled by kind — plus this session's own name, the one other sessions use to message it. This session has no ${SEND_MESSAGE_TOOL_NAME} tool, so it cannot message them with it; other sessions can still message this one. To reply, use your host application's own messaging tool if it provides one — otherwise a reply from here is not possible, and if one is needed, tell your user.

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
