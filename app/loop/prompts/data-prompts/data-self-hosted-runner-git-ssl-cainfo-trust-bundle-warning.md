---
name: "Data: Self-hosted runner GIT_SSL_CAINFO trust bundle warning"
description: "Runner warning that GIT_SSL_CAINFO prevented building the combined certificate file for Anthropic-managed git, giving the failure cause and first remedy and directing the company CA into a per-server http sslCAInfo entry"
type: "data-prompts"
---

[runner:warn] governed git: GIT_SSL_CAINFO is set on this runner and ${TRUST_BUNDLE_FAILURE_REASON}, so the runner did not build the certificate file it gives its own git for Anthropic-managed git (the git mount): the public certificate authorities plus yours. The runner's own fetches from the mount keep the variable as it is, and fail if the file holds only your company's certificate authority, because the mount presents a public certificate. ${TRUST_BUNDLE_FIRST_REMEDY} ${UNSET_ALTERNATIVE_LEAD} the variable for the runner and name your company's file for your own server in the machine's system git config (an http.<your server's URL>.sslCAInfo entry).


> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
