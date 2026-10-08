---
name: "Tool Description: Skill-scoped git push refusal"
description: "Explains that an active skill rejects dangerous or bypassing git push forms, permits a plain push to the configured remote, and forbids evasion or silent substitution"
type: "tool-prompts"
---

${ACTIVE_SKILL_REFUSAL_SUBJECT} refuses `git push` forms that force, delete, mirror or prune refs, set push options, skip the pre-push hook or name a receive-pack (tokens starting `--force`, ` -f`, ` +`, `--de`, ` -d`, ` :`, `--m`, `--pru`, `--pu`, ` -o`, `--no-veri`, `--rece`, `--e`). A plain push of the branch to the configured remote is fine. The match is on the raw command text, so a ref name containing one of these fragments trips it too; tell the user (ID: {user_id}) rather than rewriting the command to slip past. ${EXPLICITLY_REQUESTED_REFUSED_FORM_GUIDANCE}

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
