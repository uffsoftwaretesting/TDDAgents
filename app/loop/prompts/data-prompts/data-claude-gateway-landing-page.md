---
name: "Data: Claude gateway landing page"
description: "HTML status page served at a Claude Code gateway root, showing the gateway logo, the running gateway URL, the identity-provider host, an OAuth discovery link, and the gateway version"
type: "data-prompts"
---

<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{agent_name} gateway for Amazon Bedrock, Google Cloud, and Microsoft Foundry</title>
</head>
<body style="font-family: monospace; margin: 1em;">
<pre style="line-height: 1; margin: 0 0 1em 0;">${GATEWAY_ASCII_LOGO}</pre>
<pre style="margin: 0;">
<b>{agent_name} gateway for Amazon Bedrock, Google Cloud, and Microsoft Foundry</b>

Running at ${GATEWAY_URL}

To connect from {agent_name}:
  Your admin provisions this gateway URL via managed settings
  (forceLoginGatewayUrl) — then /login connects here directly.

Identity provider   ${IDENTITY_PROVIDER_HOST}
Discovery           <a href="/.well-known/oauth-authorization-server">/.well-known/oauth-authorization-server</a>
Version             ${HTML_ESCAPE_FN(GATEWAY_VERSION)}
</pre>
</body>
</html>


> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
