---
name: "Skill: Dynamic pacing loop execution"
description: "Step-by-step instructions for executing a dynamic pacing loop that runs tasks, arms and re-arms event monitors, schedules fallback heartbeat ticks, and handles task notifications"
type: "skill-prompts"
---

1. **Run ${TASK_RUN_LABEL} now**, following the instructions inlined below.
2. **If the next tick is gated on an event** (CI finishing, a PR comment, a log line) and no ${MONITOR_TOOL_NAME} is already running for it: ${MONITOR_ARMING_GUIDANCE_FN()}. Its events wake this loop immediately — you do not wait for the ${SCHEDULE_WAKEUP_TOOL_NAME} deadline. ${MONITOR_REARM_GUIDANCE_FN("ticks")}
${CONFIRM_AND_DECIDE_STEPS_BLOCK}
5. **If woken by a `<task-notification>`** rather than this prompt: handle the event, then make the same decision. If the loop should continue, ${REARM_WITH_STATUS_UPDATE_FN(`call ${SCHEDULE_WAKEUP_TOOL_NAME} again with `${DYNAMIC_MODE_SENTINEL}` and the same 1200–1800s `delaySeconds` (the ${MONITOR_TOOL_NAME} remains the wake signal; the new wakeup is only the fallback heartbeat)`)}. If the event means the work is finished, stop (step 6).
6. **To stop the loop** — the task is complete, further iterations can't make progress, or the user (ID: {user_id}) asked you to stop — call ${SCHEDULE_WAKEUP_TOOL_NAME} with `stop: true` (no other fields) and ${TASK_STOP_TOOL_NAME} any ${MONITOR_TOOL_NAME} you armed (use ${TASK_LIST_TOOL_NAME} to find the task ID if it is no longer in context).${STOPPED_LOOP_OUTCOME_NOTE_FN()} Stopping is the loop's normal ending — the user (ID: {user_id}) can restart it anytime with /loop.${ADDITIONAL_INFO_FN()}

### Autonomous Loop Pacing
Use the {sleep_tool_name} when waiting to avoid useless spin cycles.



> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
