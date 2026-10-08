---
name: "Data: Review upload excluded changes error"
description: "Error shown when a review upload has no new commits or uploadable changes because every uncommitted file was excluded from the git bundle"
type: "data-prompts"
---

It doesn't look like you have any new commits or changes to review: the only uncommitted changes here are to ${GIT_BUNDLE_OPTIONS.leftOutCount} ${PLURALIZE_FN(GIT_BUNDLE_OPTIONS.leftOutCount,"file")} that ${PLURALIZE_FN(GIT_BUNDLE_OPTIONS.leftOutCount,"stays","stay")} on this machine (named like credentials or keys, ${GIT_BUNDLE_OPTIONS.withheldByRules?"covered by a Read rule or a sandbox read-deny setting of yours, linked from your {agent_name} configuration, ":""}hard-linked to another file, kept by a git filter such as LFS, or still being written while read) and ${PLURALIZE_FN(GIT_BUNDLE_OPTIONS.leftOutCount,"is","are")} not uploaded. Stage or commit any other work first.


> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
