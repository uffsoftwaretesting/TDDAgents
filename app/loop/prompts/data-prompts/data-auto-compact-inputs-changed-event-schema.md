---
name: "Data: Auto-compact inputs changed event schema"
description: "Schema description for worker-resolved auto-compaction state events used by thin clients to display the effective compaction countdown"
type: "data-prompts"
---

@internal Worker-resolved auto-compact state, emitted by CCR workers at boot, whenever the resolved state changes (/autocompact, model switch, settings change), re-checked at each turn start, and re-emitted after a conversation reset. Thin clients adopt it so the "% until auto-compact" indicator counts down to the worker's real compaction trigger instead of re-resolving against client-local state. Turn-scoped divergence is accepted: a turn running under a skill/command frontmatter model override compacts against that model's window while the frame keeps the resting model's (the local indicator shares this limitation). From sessionState.onAutocompactInputsChanged.


> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
