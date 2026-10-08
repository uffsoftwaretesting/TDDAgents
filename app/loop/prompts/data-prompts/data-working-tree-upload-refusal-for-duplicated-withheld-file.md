---
name: "Data: Working tree upload refusal for duplicated withheld file"
description: "Error refusing a working-tree upload because an index entry appeared holding byte-for-byte the content of a file that must stay on this machine, indicating something outside the upload wrote into its index copy"
type: "data-prompts"
---

Not uploading this working tree: while it was read, an index entry appeared (${FORMAT_PATH_LIST_FN(SAME_BYTES_PATHS)}) holding, byte for byte, the content of a file that stays on this machine (named like credentials or keys${HAS_READ_RULES?", covered by a Read rule or a sandbox read-deny setting of yours, linked from your {agent_name} configuration":""}, or kept by a git filter) — at a name no step of this upload put it under, so something else wrote into the index copy this upload works in while it ran. Retry; if it recurs, check what else is running in this checkout.


> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
