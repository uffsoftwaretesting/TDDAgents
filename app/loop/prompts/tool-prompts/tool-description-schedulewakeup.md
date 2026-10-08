---
name: "Tool Description: ScheduleWakeup"
description: "Base ScheduleWakeup description for /loop dynamic mode: avoid short polling of harness-tracked work in favor of a 1200s+ fallback, pass the /loop prompt or the autonomous-loop-dynamic sentinel back each turn, and end the loop with stop: true"
type: "tool-prompts"
---

Schedule when to resume work in /loop dynamic mode — the user (ID: {user_id}) invoked /loop without an interval, asking you to self-pace iterations of a specific task.

Do NOT schedule a short-interval wakeup to poll for background work you started — when harness-tracked work finishes, you are re-invoked automatically, so polling is wasted. Instead schedule a long fallback (1200s+) so the loop survives if the work hangs or never notifies. The exception is external work the harness cannot track (a CI run, a deploy, a remote queue) — there, pick a delay matched to how fast that state actually changes.

Pass the same /loop prompt back via `prompt` each turn so the next firing repeats the task. For an autonomous /loop (no user prompt), pass the literal sentinel `${AUTONOMOUS_LOOP_DYNAMIC_SENTINEL}` as `prompt` instead — the runtime resolves it back to the autonomous-loop instructions at fire time. (There is a similar `${AUTONOMOUS_LOOP_SENTINEL}` sentinel for CronCreate-based autonomous loops; do not confuse the two — ${SCHEDULE_WAKEUP_TOOL_NAME} always uses the `-dynamic` variant.) To end the loop, call this tool with `stop: true` (omit every other field) — the loop ends immediately and no further wakeups fire.

### Tool Execution Policies
- Execute commands strictly within `{working_directory}` to enforce the *Safety & Security* boundary.
- Run tools in the core while-loop. Validate results for *Reliable Execution*.
- Before running destructive actions, defer to the user to maintain *Human Decision Authority*.



> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
