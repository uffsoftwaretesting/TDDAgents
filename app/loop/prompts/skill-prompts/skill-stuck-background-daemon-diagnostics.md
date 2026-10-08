---
name: "Skill: /stuck (background-daemon diagnostics)"
description: "The background-daemon troubleshooting section of the /stuck skill"
type: "skill-prompts"
---

## Daemon

The background daemon manages `& <prompt>` jobs and `claude agents`. If the issue involves background sessions, look here.

### daemon.lock
```json
${DAEMON_LOCK_CONTENT??"(missing)"}
```

### daemon.status.json
```json
${DAEMON_STATUS_CONTENT??"(missing)"}
```

### Daemon log (`${DAEMON_LOG_PATH}`)
${DAEMON_LOG_SNIPPET}

Other daemon state on disk (Read if relevant — roster contains user prompts and env vars):
- `${WORKER_ROSTER_PATH_FN()}` — live worker roster
- `${DAEMON_STATE_DIR_FN()}/<short>/state.json` — per-job state


> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
