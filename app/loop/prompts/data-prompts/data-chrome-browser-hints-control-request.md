---
name: "Data: Chrome browser hints control request"
description: "Documents the set_chrome_browser_hints control request that tells a session which connected Claude in Chrome browser to prefer, and that the hints are session-only and never override the live relay roster"
type: "data-prompts"
---

@internal The host's view of which {agent_name} in Chrome browser this session should use when several are connected to the account: `preferredDeviceId` is the browser its user picked last on that computer, `localDeviceIds` the relay device ids it knows to be running on that computer right now, `hostPlatform` that computer's OS (for the weaker same-OS label). Session memory only, never persisted; the built-in claude-in-chrome server reads them as its persisted pick and its on-this-computer set, so it defaults to that browser instead of asking. Hints only: nothing is selected unless it is in the live relay roster, and an empty list clears them.


> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
