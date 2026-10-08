---
name: "Tool Description: Artifact nested runtime cleanup error"
description: "Formats the Artifact publish error for irreducibly nested runtime markers, base tags, or repeated page skeletons and directs publishing the innermost clean document"
type: "tool-prompts"
---

This page carries runtime-marker comment blocks (`<!-- frame-runtime -->…<!-- /frame-runtime -->`, `<!-- chart-runtime -->…<!-- /chart-runtime -->`, `<!--claude-mermaid-runtime-begin…`, `<!--claude-hljs-runtime-begin…`), `data-frame-runtime` attributes, `<base href="/_f/…">` tags, or repeated artifact skeletons (`<!doctype html><html><head>…` wrapped around the page again and again) nested so that removing one keeps exposing another — nothing a genuine page or a fetched artifact contains. Delete every such comment block, attribute, tag, and repeated outer skeleton from the source, keep the innermost document, and publish again.

### Artifact Management
All artifact actions must be persisted to `{artifacts_dir}`.
Track versions carefully to support Contextual Adaptability. Send feedback to `{artifact_feedback_channel}`.


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
