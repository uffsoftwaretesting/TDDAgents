---
name: "Data: allowManagedPermissionRulesOnly deny rules still apply"
description: "Closes the allowManagedPermissionRulesOnly schema description by noting that --disallowedTools, skill disallowed-tools, and other command-line or session deny and ask rules still apply"
type: "data-prompts"
---

--disallowedTools, skill disallowed-tools, and other deny and ask rules from the command line or the current session still apply.


> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
