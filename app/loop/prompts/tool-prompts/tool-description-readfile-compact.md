---
name: "Tool Description: ReadFile compact"
description: "Compact file-read tool description served to newer models — absolute path, default line cap, and image/PDF/notebook handling"
type: "tool-prompts"
---

Reads a file from the local filesystem.

- `file_path` must be an absolute path.
- Reads up to ${MAX_LINES_CONSTANT} lines by default.
${CONDITIONAL_LENGTH_NOTE}
${READ_FULL_FILE_NOTE}
- Reads images (PNG, JPG, …) and presents them visually.${CAN_READ_PDF_FILES_FN()?' Reads PDFs via the `pages` parameter (e.g. "1-5", max 20 pages/request; required for PDFs over 10 pages).':""} Reads Jupyter notebooks (.ipynb) as cells with outputs.
- Reading a directory, a missing file, or an empty file returns an error or system reminder rather than content.${ADDITIONAL_READ_NOTE}

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
