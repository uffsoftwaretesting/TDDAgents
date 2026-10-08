---
name: "Data: Structured usage rate-limit rows field"
description: "Schema description for structured usage rate-limit rows, including server-defined meter ordering, null and empty semantics, and the synthesized header-fallback row"
type: "data-prompts"
---

The server's usage rows (the usage endpoint's limits[]), as sent: which meters apply, their scope, labels, severity and order are the server's, so a client renders them verbatim and a new meter needs no client release. Empty when the server reported no meters; null when the body carried no rows at all (a server that predates them). Null too while the usage fetch is failing: the rows here are only ever the server's current reply, so neither the row the CLI builds from rate-limit response headers for its own screen nor its snapshot of an earlier reply appears here.


> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
