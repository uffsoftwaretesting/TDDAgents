---
name: "Data: Message Batches API reference — PHP"
description: "PHP Batches API reference including batch creation, status polling, and result retrieval"
type: "data-prompts"
---

# Message Batches - PHP

## Message Batches API

```php
$batch = $client->messages->batches->create(requests: [
    ['customId' => 'req-1', 'params' => ['model' => '{{OPUS_ID}}', 'maxTokens' => 1024, 'messages' => [...]]],
    ['customId' => 'req-2', 'params' => [...]],
]);
// Poll $client->messages->batches->retrieve($batch->id) until processingStatus === 'ended',
// then iterate $client->messages->batches->results($batch->id).
```

---


> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
