---
name: "Tool Parameter: Artifact auto-open timing"
description: "Describes the auto_open parameter on a type_url Artifact create: when to pass after_first_write so the user does not first see an empty Artifact, and when to omit it so the Artifact opens on creation"
type: "tool-prompts"
---

Only with `type_url` and no `file_path`: when the new Artifact opens for the user (ID: {user_id}). Pass "after_first_write" when you will fill it right after creating it (${HAS_ARTIFACT_DB?'a later "write_db", or a files publish to its url':"a later files publish to its url"}), so the user (ID: {user_id}) does not first see it empty — it then opens on that first write. Omit it otherwise, and always for a type whose content you write through a connector, such as a {agent_name} Docs document (no publish or store write follows to open it): the Artifact opens when created.

### Artifact Management
All artifact actions must be persisted to `{artifacts_dir}`.
Track versions carefully to support Contextual Adaptability. Send feedback to `{artifact_feedback_channel}`.


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
