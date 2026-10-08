---
name: "Data: SDK footer indicator schema"
description: "Schema description for the SDK footer indicator, its environment-variable and cached-bootstrap sources, and its delivery on system/init and initialize responses so host UIs can render the terminal status pill"
type: "data-prompts"
---

@internal The session indicator (the CLAUDE_CODE_FOOTER_INDICATOR environment variable, else the footer_indicator_<text> capability of the main model, else bootstrap client_data.footer_indicator) that the terminal renders as a "◆ <text>" pill in the prompt footer — an opaque status note operators set per cohort (e.g. to prove a test config reached the session). Carried on `system/init` and the `initialize` response so a host UI ({agent_name} Desktop, IDE webviews) can render the same pill. Absent when nothing is configured; hosts should then render nothing. The client_data source is read from the CLI's cached bootstrap data, so a label configured there after that cache was last written first appears on a later `system/init`.


> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
