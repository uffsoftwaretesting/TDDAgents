---
name: "Data: Tool use display metadata field"
description: "Documents the tool_use_meta wire field carrying per-block display metadata for a message's tool_use blocks; it is wrapper-level UI metadata and is not replayed to the model"
type: "data-prompts"
---

@internal Display metadata for this message's tool_use blocks, keyed by block id. display_name is the MCP server's `tool.annotations.title` when provided, otherwise a readable transform of the wire name; server_display_name is the MCP server's own display name; icon_url is the MCP server's directory icon URL (claude.ai connectors only). Omitted for blocks whose display label equals the wire name (built-in tools). Wrapper-level sibling — never inside `message.content` — so it is not replayed to the model.

### MCP Resources Integration
You are connected to MCP servers: {mcp_servers_list}.
These are part of the four extensibility mechanisms designed for *Capability Amplification*.



> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
