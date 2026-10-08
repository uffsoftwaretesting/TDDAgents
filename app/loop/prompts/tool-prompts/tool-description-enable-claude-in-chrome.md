---
name: "Tool Description: Enable Claude in Chrome"
description: "Describes the tool that enables the Claude in Chrome extension for the conversation, to call once before other Claude in Chrome tools when the user needs their own browser or sign-in, and not when those tools are already present"
type: "tool-prompts"
---

Enable {agent_name} in Chrome, the {agent_name} extension in the Chrome browser on the user (ID: {user_id})'s own computer, for this conversation. If you already have tools whose names contain claude-in-chrome__ or {agent_name}_in_Chrome__, use those directly instead of calling this. Otherwise call it once, before any other {agent_name} in Chrome tool, when the user (ID: {user_id}) asks you to do something in their browser or on a website that needs their own sign-in, or explicitly asks for {agent_name} in Chrome. Do not call it for questions you can answer from the conversation or with web search, or merely because a request mentions a website.

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
