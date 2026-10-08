---
name: "Data: Gateway ignored X-Forwarded-For warning"
description: "Gateway startup warning that an X-Forwarded-For header was ignored because the requesting address is not covered by listen.trusted_proxies, and what to configure when a load balancer or ingress fronts the gateway"
type: "data-prompts"
---

request from ${CLIENT_ADDRESS} carried an X-Forwarded-For header, but ${TRUSTED_PROXIES_LIST.length===0?"listen.trusted_proxies is not set":`${CLIENT_ADDRESS} is not in listen.trusted_proxies`}, so the gateway ignored the header and used ${CLIENT_ADDRESS} as the client address. If ${CLIENT_ADDRESS} is your load balancer or ingress, every developer behind it shares one sign-in rate limit (rate_limits) and one address in audit events: add its source range to listen.trusted_proxies. If developers connect directly and something on their side adds the header, nothing needs to change. Logged once per start, however many requests are affected.


> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
