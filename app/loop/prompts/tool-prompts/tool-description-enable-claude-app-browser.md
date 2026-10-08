---
name: "Tool Description: Enable Claude app browser"
description: "Describes the tool that enables the Claude desktop app's built-in browser for the conversation, to call once before other Claude app browser tools when the user needs that browser or their own sign-in, and not when those tools are already present"
type: "tool-prompts"
---

Enable the browser built into the {agent_name} desktop app on the user (ID: {user_id})'s computer for this conversation. If you already have tools whose names contain {agent_name}_Browser__, use those directly instead of calling this. Otherwise call it once, before any other {agent_name} app browser tool, when the user (ID: {user_id}) asks you to do something in the {agent_name} app's own browser or on a website that needs their own sign-in, or explicitly asks for that browser. Do not call it for questions you can answer from the conversation or with web search, or merely because a request mentions a website.

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
