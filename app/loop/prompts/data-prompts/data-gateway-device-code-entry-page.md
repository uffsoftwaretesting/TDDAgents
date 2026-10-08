---
name: "Data: Gateway device code entry page"
description: "HTML verification page served at a gateway device endpoint, prompting the user to enter the short device code shown by Claude Code so they can sign in through their company identity provider"
type: "data-prompts"
---

<span class="status warn">Connect device</span>
<h1>Enter the code from your device.</h1>
<p class="sub">{agent_name} shows a short code when you sign in. Enter it here to connect — then you'll sign in with your company identity provider.</p>
<form method="post" action="/device">
  <input class="code-input" name="user_code" inputmode="latin" autocomplete="off" autocapitalize="characters" autocorrect="off" spellcheck="false" placeholder="XXXX-XXXX" maxlength="9" autofocus required>
  <button class="go" type="submit">Continue</button>
</form>
${ERROR_CARD_BLOCK}


> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
