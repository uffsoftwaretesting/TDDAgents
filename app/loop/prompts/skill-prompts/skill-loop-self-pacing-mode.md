---
name: "Skill: /loop self-pacing mode"
description: "Instructs Claude how to self-pace a recurring loop by arming event monitors as primary wake signals and scheduling fallback heartbeat delays between iterations"
type: "skill-prompts"
---

The user (ID: {user_id}) wants you to self-pace. Decide what makes the next iteration worth running — a passage of time, or an observable event.

1. **Run the parsed prompt now.** If it's a slash command, invoke it via the Skill tool; otherwise act on it directly.
2. **If the next run is gated on an event** (CI finishing, a log line matching, a file changing, a PR comment) and no ${MONITOR_TOOL_NAME} is already running for it: ${MONITOR_ARMING_GUIDANCE_FN()}. Its events arrive as `<task-notification>` messages and wake this loop immediately — you do not wait for the ${SCHEDULE_WAKEUP_TOOL_NAME} deadline. ${MONITOR_REARM_GUIDANCE_FN("iterations")}
${CONFIRM_AND_DECIDE_STEPS_BLOCK}
5. **If you were woken by a `<task-notification>`** rather than this prompt: handle the event in the context of the loop task, then make the same decision. If the loop should continue, ${REARM_WITH_STATUS_UPDATE_FN(`call ${SCHEDULE_WAKEUP_TOOL_NAME} again with the same `prompt` and the same 1200–1800s `delaySeconds` from the schedule step above (the ${MONITOR_TOOL_NAME} remains the wake signal; the new wakeup is only the fallback heartbeat)`)}. If the event means the work is finished, stop (step 6).
6. **To stop the loop** — the task is complete, further iterations can't make progress, or the user (ID: {user_id}) asked you to stop — call ${SCHEDULE_WAKEUP_TOOL_NAME} with `stop: true` (no other fields) and ${TASK_STOP_TOOL_NAME} any ${MONITOR_TOOL_NAME} you armed (use ${TASK_LIST_TOOL_NAME} to find the task ID if it is no longer in context).${STOPPED_LOOP_OUTCOME_NOTE_FN()} Stopping is the loop's normal ending — the user (ID: {user_id}) can restart it anytime with /loop.${ADDITIONAL_INFO_FN()}


> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
