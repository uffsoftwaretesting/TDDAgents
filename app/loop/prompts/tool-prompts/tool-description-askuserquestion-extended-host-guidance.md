---
name: "Tool Description: AskUserQuestion extended host guidance"
description: "Guides hosts with extended AskUserQuestion support on question ordering, text and number inputs, multi-select defaults, optional labels, and follow-up question requests"
type: "tool-prompts"
---

Extended questions (this host renders them):
- Put the most important question first.
- Omit "kind" for an ordinary choice question. Use "kind": "text" for an open-ended question (a text box, no options) and "kind": "number" with "min"/"max" (optionally "step", "defaultValue", "unit") for a quantity. Prefer choices whenever the likely answers can be listed.
- Set multiSelect: true on choice questions unless the options are mutually exclusive (people answering are often still exploring).
- Optional "title" is one short line above the questions; optional per-question "description" is one helper line. Option descriptions are optional here too: add one only when the label alone would be ambiguous.
- Do not add "Other" or "Skip" options: the user (ID: {user_id}) can always type their own answer or leave a question unanswered. The user (ID: {user_id}) can also ask you for more questions; when the result says so, call this tool again with follow-up questions before doing the task.

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
