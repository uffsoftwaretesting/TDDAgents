---
name: "Agent Prompt: Session transcript chunk summary"
description: "Instructs an agent to summarize a chunk of a Claude Code session transcript concisely"
type: "agent-prompts"
---

Summarize this portion of a {agent_name} session transcript. Focus on:
1. What the user (ID: {user_id}) asked for
2. What {agent_name} did (tools used, files modified)
3. Any friction or issues
4. The outcome

Keep it concise - 3-5 sentences. Preserve specific details like file names, error messages, and user feedback.

TRANSCRIPT CHUNK:


> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
