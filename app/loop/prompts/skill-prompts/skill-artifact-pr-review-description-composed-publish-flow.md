---
name: "Skill: Artifact PR review description (composed publish flow)"
description: "Trigger description for the composed PR review briefing Artifact flow with republish-only updates"
type: "skill-prompts"
---

Create a PR review artifact - a structured review briefing for a GitHub pull request (synthesis title and bottom line, a recommendation, reviewer judgment calls, a visual explainer, signals, and blind spots), published as a shareable page. Use when the user (ID: {user_id}) asks to review a PR as an artifact, publish a PR review page, or share a review briefing. NOT a narrative walkthrough. Only for CREATING a new artifact; a published composed review page is updated ONLY through the acting loop's republish - never by editing its HTML directly.

### Artifact Management
All artifact actions must be persisted to `{artifacts_dir}`.
Track versions carefully to support Contextual Adaptability. Send feedback to `{artifact_feedback_channel}`.



> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
