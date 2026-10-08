---
name: "Tool Description: NotebookEdit"
description: "Tool description for editing Jupyter notebook cells by replacing, inserting, or deleting a cell using cell IDs from the read tool"
type: "tool-prompts"
---

Replaces, inserts, or deletes a single cell in a Jupyter notebook (.ipynb file).

Usage:
- You must use the ${READ_TOOL_NAME} tool on the notebook in this conversation before editing — this tool will fail otherwise.
- `notebook_path` must be an absolute path.
- `cell_id` is the `id` attribute shown in the ${READ_TOOL_NAME} tool's `<cell id="...">` output. It is required for `replace` and `delete`.
- `edit_mode` defaults to `replace`. Use `insert` to add a new cell after the cell with the given `cell_id` (or at the beginning of the notebook if `cell_id` is omitted) — `cell_type` is required when inserting. Use `delete` to remove the cell.

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
