---
name: "Data: Self-hosted runner GIT_SSL_NO_VERIFY lifecycle hook exception"
description: "Runner warning addendum explaining that checkout and post-session hooks of sessions with a repository on Anthropic-managed git get http.sslVerify=false with certificate checks re-enabled for the mount, while other sessions' hooks inherit the switch"
type: "data-prompts"
---

${GIT_SSL_NO_VERIFY_WARNING} The runner leaves your lifecycle hooks (checkout, post-session) alone, with one exception: the hooks of a session that has a repository on Anthropic-managed git, which can reach it with the session's token. That is every checkout hook of such a session, whether or not the runner hands it an address there (CLAUDE_RUNNER_GIT_MOUNT_URL), and its post-session hook. For such a hook the runner takes the switch out of the environment and gives git http.sslVerify=false instead, with http.<url>.sslVerify=true for the mount: every git that hook starts checks the mount's certificate and, as before, no other server's. The hooks of every other session inherit the switch from the runner's environment as it is. If such a hook's fetch from the mount, or its push to it, then fails a certificate check (behind a proxy that re-signs traffic, say), name your company's certificate authority in GIT_SSL_CAINFO.


> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
