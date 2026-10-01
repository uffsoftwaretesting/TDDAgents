<!--
name: "Skill: /plugin-types tsconfig setup"
description: "Closing guidance from the /plugin-types command explaining how to point a plugin's tsconfig.json or jsconfig.json at the generated declarations, which compiler options to set, what the plugin API import types, and how plugin validation reads the plugin the way the engine will"
ccVersion: "2.1.277"
variables:
  - "RELATIVE_PATH_FN"
  - "CWD"
  - "TYPES_OUTPUT_DIR"
  - "PLUGIN_API_TYPES_FILENAME"
-->
Point the plugin's tsconfig.json (or jsconfig.json) at them: "include": ["${RELATIVE_PATH_FN(CWD,TYPES_OUTPUT_DIR)||"."}", "hooks"] with "lib": ["es2023"] and "jsx": "react", "jsxFactory": "h"; the header of ${PLUGIN_API_TYPES_FILENAME} has the whole file. Then `import type { Register } from "claude-code"` types register(on, options), e narrows per tool, and what a plugin you depend on adds to $ is typed with nothing copied. `claude plugin validate <dir>` then reads the plugin the way the engine will and reports what it hooks, calls and would be refused.
