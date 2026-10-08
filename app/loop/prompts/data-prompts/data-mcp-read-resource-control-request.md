---
name: "Data: MCP read resource control request"
description: "Documents the mcp_read_resource control request that reads one MCP Apps (SEP-1865) ui:// resource from a connected MCP server, its sandboxed-render requirement, SDK-server rejection, refused lanes and error cases"
type: "data-prompts"
---

Reads one MCP Apps (SEP-1865) UI resource — a `ui://` URI, typically the `_meta.ui.resourceUri` a tool declares — from a connected MCP server the CLI itself dialed, with `resources/read`, for a host that renders it. Read-only and no model turn. The reply is untrusted third-party content (HTML): render it sandboxed. SDK-type MCP servers (config.type === "sdk") are rejected — they are caller-provided, so the caller can read them directly. Errors name the cause: a non-ui:// URI, an unknown server, a server that managed policy blocks, that is disabled or that the project has not approved (the refusals mcp_reconnect gives), a server that is not connected (failed, pending or needs-auth: send mcp_reconnect; a connected server, or one still listed from the discovery cache, is read through the same connect path a tool call takes), a response over the size limit, or the server's own resources/read error. Refused on a lane that redacts what it persists (a Remote Control bridge worker, a tenant worker) and by the client of a cloud-hosted session. Advertised as `mcp_read_resource_v1` in system/init.capabilities.

### MCP Resources Integration
You are connected to MCP servers: {mcp_servers_list}.
These are part of the four extensibility mechanisms designed for *Capability Amplification*.



> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
