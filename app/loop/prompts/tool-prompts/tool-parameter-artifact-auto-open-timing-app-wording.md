---
name: "Tool Parameter: Artifact auto-open timing (app wording)"
description: "Describes the auto_open parameter on a type_url Artifact create in third-person app wording: when Claude passes after_first_write so the person does not first see an empty Artifact, and when it omits the parameter so the Artifact opens on creation"
type: "tool-prompts"
---

Only with `type_url` and no `file_path`: when the new Artifact opens for the person. {agent_name} passes "after_first_write" when it will fill the Artifact right after creating it with a files publish to its url, so the person does not first see it empty. The Artifact then opens on that first write. Otherwise {agent_name} omits it, and the Artifact opens when created; {agent_name} always omits it for a type whose content it writes through a connector, such as a {agent_name} Docs document, since no publish or store write follows to open it.

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
