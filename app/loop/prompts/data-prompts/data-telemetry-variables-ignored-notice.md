---
name: "Data: Telemetry variables ignored notice"
description: "Settings-status warning listing OTEL/telemetry environment variables a settings file sets that Claude Code ignores because such files can only turn telemetry off, with guidance on where to set them intentionally"
type: "data-prompts"
---

{agent_name} ignores these telemetry variables in ${SETTINGS_FILE_DISPLAY_PATH}: ${IGNORED_TELEMETRY_VARIABLE_NAMES.join(", ")}. A project's settings files can only turn telemetry off: set OTEL_LOGS_EXPORTER, OTEL_METRICS_EXPORTER, or OTEL_TRACES_EXPORTER to none, or a content variable such as OTEL_LOG_USER_PROMPTS to 0, with the name in upper case. That doesn't work for a variable that managed settings, a --settings file, or the environment you start {agent_name} from already sets. If you set them on purpose, set them in your shell, your user settings (~/.claude/settings.json), or managed settings instead.


> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
