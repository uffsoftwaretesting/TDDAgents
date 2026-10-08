---
name: "Tool Description: memory_write prompt"
description: "Describes creating or replacing documents in memory stores available in this session with version checks, conflict handling, and secret-safety requirements"
type: "tool-prompts"
---

Create or update a memory document with full content, in the memory store named by store (call ${MEMORY_LIST_TOOL_NAME} with no arguments to see the stores available in this session). Overwrites if the path already exists: content replaces the ENTIRE document — this is not an append or a patch. Include every existing line you intend to keep; any line you omit is deleted. Use this to save durable knowledge about the project and how to work in it — not transient task state. Always pass if_version: the version token from your most recent ${MEMORY_READ_TOOL_NAME} or ${MEMORY_WRITE_TOOL_NAME} of this path, or the literal word new (without quotes) for a file that does not yet exist. The listing shows paths but not version tokens, so for any file already there you must ${MEMORY_READ_TOOL_NAME} it first. Writes with if_version=new to an existing path are rejected so you can't overwrite content you haven't seen. Both the rejection and a version conflict return the current content (when it is within the read cap) so you can merge and retry; an oversized document's content is withheld and must be replaced wholesale. The result includes the new version token for follow-up writes. Never write secrets or credentials into a memory — project stores are shared with every collaborator, and such writes are refused in every store.

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
