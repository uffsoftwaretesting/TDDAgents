---
name: "Data: Code change published event schema"
description: "Schema description for the code_change_published system event, including provenance, idempotency, trust boundaries, and best-effort delivery"
type: "data-prompts"
---

@internal A code change from this session went out for review (a pull/merge request, or another provider's change in internal builds). Fires when the harness sees the change published or links the session to a PR — on creation, and also when the session contributes to an existing one (gh pr edit/close/ready, gh pr checkout, a push to a branch that has an open PR) — so bind on every event, not just the first; re-emission for the same URL is possible and idempotent. Provenance: values are scraped from the command's captured output (the last PR-shaped URL printed) or a gh pr view lookup. Captured output is not only the forge CLI's own text — hook output or files printed by the same command can contribute — so treat the fields as a binding hint: display them, but verify against the forge with your own credential-scoped lookup before routing authenticated requests or trusting the host. Best-effort, not exhaustive: a crash before the link, gh printing no URL to a piped capture (gh pr merge), or an unrecognized forge means no event — keep your provider-API lookup as the source of truth and treat this as the trigger.


> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
