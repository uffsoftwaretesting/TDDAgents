---
name: compact-no-tools-preamble
description: NO_TOOLS_PREAMBLE of the compaction request.
provenance: >-
  Ported verbatim from claude-code v2.1.88 src/services/compact/prompt.ts. Only the tool
  names are placeholders, rendered from this build's tool names.
---
CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

- Do NOT use {{TOOL_LIST}}, or ANY other tool.
- You already have all the context you need in the conversation above.
- Tool calls will be REJECTED and will waste your only turn — you will fail the task.
- Your entire response must be plain text: an <analysis> block followed by a <summary> block.

