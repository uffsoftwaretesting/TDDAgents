---
name: "Agent Prompt: /code-review GitLab comment posting"
description: "Directs code review findings to a general GitLab merge request note when the comment flag is enabled"
type: "agent-prompts"
---

## Posting to GitLab (--comment)

The `--comment` flag was passed. After producing the findings list, if the
review target is a GitLab merge request, post the findings as one general MR
note via `${`glab mr note${GITLAB_MR_IID?` ${GITLAB_MR_IID}`:""}${GLAB_MR_NOTE_OPTIONS} -m "<body>"`}`${GITLAB_REPOSITORY_REFERENCE?"":" from inside that project's checkout"}
(every finding with its file:line, the issue, and the suggested fix). glab has no single verb for line-anchored
comments; those require `glab api projects/:id/merge_requests/:iid/discussions`,
so post the general note unless the user (ID: {user_id}) asks for inline threads. If glab is
not available in this session, print the findings instead. If the target is
not an MR, print the findings to the terminal and note that `--comment` was
ignored.


> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
