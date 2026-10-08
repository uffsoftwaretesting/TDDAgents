---
name: "Tool Description: Edit single replacement"
description: "Lean tool description for performing exact string replacement in a file, requiring a prior Read in the conversation and explaining which Read line-number prefix to strip before matching old_string"
type: "tool-prompts"
---

Performs exact string replacement in a file.

- You must ${READ_TOOL_NAME} the file in this conversation before editing, or the call will fail.
- `old_string` must match the file exactly, including indentation, and be unique — the edit fails otherwise. Strip the Read line prefix (${IS_TAB_AWARE_READ_SEPARATOR_ENABLED?"line number + a single tab or `:`":"line number + tab"}) before matching.
- `replace_all: true` replaces every occurrence instead.

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
