---
name: "Data: MCP server status error_code field"
description: "Schema description for the MCP server status error_code field, naming the host-fixable failure causes — a rejected claude.ai connector login, a rejected first-party Anthropic credential, and a project-scoped server awaiting approval — and noting that it is absent for every other failure and that new values are additive"
type: "data-prompts"
---

@internal Why a 'failed' row failed, for the causes a host can fix: CLAUDEAI_BEARER_REJECTED (a claude.ai connector rejected the claude.ai login — sign in again), FIRST_PARTY_AUTH_REJECTED (a first-party Anthropic server rejected its credential — the claude.ai login, or for the {agent_name} Design server its /design-login authorization; the row's error names which sign-in fixes it), APPROVAL_REQUIRED (a project-scoped server awaiting the user (ID: {user_id})'s approval). Absent for every other failure; new values are additive.


> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
