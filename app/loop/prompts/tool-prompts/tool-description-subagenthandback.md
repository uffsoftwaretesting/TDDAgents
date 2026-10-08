---
name: "Tool Description: SubagentHandback"
description: "Describes the SubagentHandback tool a subagent calls exactly once to deliver its full final report to the agent that spawned it, noting that the call ends the run and plain-text endings are not delivered"
type: "tool-prompts"
---

Deliver your final report to the agent that spawned you (your caller). Use it once, for that hand-off only: when your work is complete, call ${SUBAGENT_HANDBACK_TOOL_NAME}({message: <your full report>}). The call ends your run, so do everything else first and put everything your caller needs in that one report. It is not a messaging channel: do not use it for progress updates or questions.

Only a report delivered through ${SUBAGENT_HANDBACK_TOOL_NAME} reaches your caller; plain text you write at the end of your run is NOT delivered. There is no recipient parameter: the report can only go to your caller.

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
