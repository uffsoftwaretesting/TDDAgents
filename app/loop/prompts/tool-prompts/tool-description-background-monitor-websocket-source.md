---
name: "Tool Description: Background monitor WebSocket source"
description: "Addendum to the background monitor tool description covering the WebSocket (ws) source, which opens a WebSocket and streams each incoming text frame as a notification event instead of running a shell command, with notes on binary frames, socket close, and rate limiting"
type: "tool-prompts"
---

**ws source** — open a WebSocket and stream each incoming text frame as an event. No shell, no polling: the server pushes, you get notified.

  Monitor({
    ws: {url: 'wss://events.example.com/stream', protocols: ['v1']},
    description: 'deploy events',
  })

Each text frame becomes one notification (multiline frames stay as one event). Binary frames are reported as `[binary frame, N bytes]` rather than passed through. Socket close ends the watch with the close code surfaced; errors are surfaced before close. Same rate limiting as bash — a firehose will be suppressed and eventually stopped, so subscribe to a filtered feed where one exists.

Prefer this over `command: 'websocat wss://…'` — it avoids the extra process and line-buffering pitfalls. Use bash when you need to transform or filter frames with shell tools before they become events.

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
