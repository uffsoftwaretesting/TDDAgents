---
name: "System Reminder: Browser extension not connected"
description: "Tells the user how to resolve a disconnected Claude browser extension and where to report bugs"
type: "system-prompts"
---

Browser extension is not connected. Please ensure the {agent_name} browser extension is installed and running (${CHROME_EXTENSION_URL}), and that you are logged into claude.ai with the same account as {agent_name}. If this is your first time connecting to Chrome, you may need to restart Chrome for the installation to take effect. If you continue to experience issues, please report a bug: ${BROWSER_EXTENSION_BUG_REPORT_URL}


> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
