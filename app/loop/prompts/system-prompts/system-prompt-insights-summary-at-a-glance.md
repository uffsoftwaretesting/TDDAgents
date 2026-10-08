---
name: "System Prompt: Insights summary (At a Glance)"
description: "The 'At a Glance' summary block of the Insights report (what's working / what's hindering)"
type: "system-prompts"
---

## At a Glance

${AT_A_GLANCE.whats_working?`**What's working:** ${AT_A_GLANCE.whats_working} See _Impressive Things You Did_.`:""}

${AT_A_GLANCE.whats_hindering?`**What's hindering you:** ${AT_A_GLANCE.whats_hindering} See _Where Things Go Wrong_.`:""}

${AT_A_GLANCE.quick_wins?`**Quick wins to try:** ${AT_A_GLANCE.quick_wins} See _Features to Try_.`:""}

${AT_A_GLANCE.ambitious_workflows?`**Ambitious workflows:** ${AT_A_GLANCE.ambitious_workflows} See _On the Horizon_.`:""}


> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
