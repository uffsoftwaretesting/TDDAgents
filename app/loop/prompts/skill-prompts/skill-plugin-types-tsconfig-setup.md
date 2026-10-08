---
name: "Skill: /plugin-types tsconfig setup"
description: "Closing guidance from the /plugin-types command explaining how to point a plugin's tsconfig.json or jsconfig.json at the generated declarations, which compiler options to set, what the plugin API import types, and how plugin validation reads the plugin the way the engine will"
type: "skill-prompts"
---

Point the plugin's tsconfig.json (or jsconfig.json) at them: "include": ["${RELATIVE_PATH_FN(CWD,TYPES_OUTPUT_DIR)||"."}", "hooks"] with "lib": ["es2023"] and "jsx": "react", "jsxFactory": "h"; the header of ${PLUGIN_API_TYPES_FILENAME} has the whole file. Then `import type { Register } from "claude-code"` types register(on, options), e narrows per tool, and what a plugin you depend on adds to $ is typed with nothing copied. `claude plugin validate <dir>` then reads the plugin the way the engine will and reports what it hooks, calls and would be refused.


> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
