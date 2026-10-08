---
name: "System Prompt: WSL managed settings double opt-in"
description: "Explains that WSL can read the Windows managed settings policy chain only when the admin-enabled flag is set, with HKCU requiring an additional user opt-in"
type: "system-prompts"
---

When set to true in either admin-only Windows source — the HKLM SOFTWARE/Policies/{agent_name}Code registry key or C:/Program Files/{agent_name}Code/managed-settings.json — WSL reads managed settings from the full Windows policy chain (HKLM, C:/Program Files/{agent_name}Code via DrvFs, HKCU) in addition to /etc/claude-code. Windows sources take priority. The flag is also required in HKCU itself for HKCU policy to apply on WSL (double opt-in: admin enables the chain, user confirms HKCU). On native Windows the flag has no effect.


> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
