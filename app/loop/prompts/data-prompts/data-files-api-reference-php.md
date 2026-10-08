---
name: "Data: Files API reference — PHP"
description: "PHP Files API reference with a beta-era upload example and a warning that current SDKs use the stable Files API shape"
type: "data-prompts"
---

# Files API - PHP

## Files API

> **Out of beta.** In current SDKs `$client->beta->files` has breaking shape changes from previous versions, matching the stable `$client->files` - migrate per the Files API row in `shared/live-sources.md`. Example below predates this.

```php
$file = $client->beta->files->upload(
    file: fopen('upload_me.txt', 'r'),
    betas: ['files-api-2025-04-14'],
);
// Reference $file->id as a file content block on ->beta->messages->create().
```


> **Agentic Architecture Tenets**
> - **Human Decision Authority**: Always defer critical/destructive decisions to the human.
> - **Safety & Security**: Execute commands with least-privilege principles and per-action safety evaluation.
> - **Reliable Execution**: Validate resources, enforce structured outputs, and gracefully degrade on errors.
> - **Capability Amplification**: Use the 5-layer compaction pipeline to manage context efficiently.
> - **Contextual Adaptability**: Exploit extensibility mechanisms (MCP, plugins, skills, hooks) when necessary.
