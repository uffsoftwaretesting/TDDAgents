---
name: SendMessage
description: Send a message to another agent
---

# SendMessage

Send a message to another agent.

```json
{"to": "a1b2c3d4e5f6a7b8c", "summary": "assign task 1", "message": "start on task #1"}
```

| `to` | |
|---|---|
| `"<agentId>"` | An agent by the agentId the Agent tool returned |

Your plain text output is NOT visible to other agents — to communicate, you MUST call this tool. A running agent receives your message at its next tool round; a stopped one is resumed in the background with it, and you are notified when it finishes.
