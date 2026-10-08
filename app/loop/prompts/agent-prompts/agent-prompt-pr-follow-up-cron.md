---
name: "Agent Prompt: PR follow-up cron"
description: "Cron prompt for checking a pull request created in the session and fixing failures, comments, or conflicts"
type: "agent-prompts"
---

${PR_INSTRUCTIONS_PREFIX}${PR_REFERENCE} (created in this session). Check state with `gh pr view ${PR_NUMBER} -R ${GITHUB_REPOSITORY} --json state,mergeable,mergeStateStatus,statusCheckRollup${IS_PR_STEWARD_ENABLED?",labels":""}` and new review comments with `gh api --paginate repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}/comments`. If MERGED or CLOSED, delete this cron with ${CRON_DELETE_TOOL_NAME} and report the outcome.${PR_STEWARD_LABEL_CHECK_BLOCK} If CI is failing, comments are unaddressed, or there are merge conflicts, fix and push.${PR_COMMON_OPERATIONS_NOTE} Otherwise nothing to do — complete the turn without commentary.


> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
