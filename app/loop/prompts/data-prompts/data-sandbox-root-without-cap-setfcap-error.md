---
name: "Data: Sandbox root without CAP_SETFCAP error"
description: "Linux sandbox dependency error explaining that running as uid 0 without CAP_SETFCAP makes every sandboxed command fail while writing a uid map on kernels 5.12+ and backports, and telling the user to grant CAP_SETFCAP or run as non-root"
type: "data-prompts"
---

running as uid 0 without CAP_SETFCAP in this process's capability bounding set - on kernels that enforce the CAP_SETFCAP requirement for mapping uid 0 into a user namespace (Linux 5.12 and distribution backports) every sandboxed command fails while writing a uid map ("Operation not permitted"). Grant CAP_SETFCAP to this process, or run as a non-root user


> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
