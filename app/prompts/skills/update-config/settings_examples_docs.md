## Settings File Locations

Choose the appropriate file based on scope:

| File | Scope | Git | Use For |
|------|-------|-----|---------|
| `~/.tddagents/settings.json` | Global | N/A | Personal preferences for all projects |
| `.tddagents/settings.json` | Project | Commit | Team-wide hooks, permissions, plugins |
| `.tddagents/settings.local.json` | Project | Gitignore | Personal overrides for this project |

Settings load in order: user → project → local (later overrides earlier).

## Settings Schema Reference

### Permissions
```json
{
  "permissions": {
    "allow": ["Bash(npm:*)", "Edit(.tddagents)", "ReadFile"],
    "deny": ["Bash(rm -rf:*)"],
    "ask": ["WriteFile(/etc/*)"],
    "defaultMode": "default" | "plan" | "acceptEdits" | "dontAsk",
    "additionalDirectories": ["/extra/dir"]
  }
}
```

**Permission Rule Syntax:**
- Exact match: `"Bash(npm run test)"`
- Prefix wildcard: `"Bash(git:*)"` - matches `git status`, `git commit`, etc.
- Tool only: `"ReadFile"` - allows all ReadFile operations

### Thinking
```json
{
  "alwaysThinkingEnabled": true
}
```

### MCP Server Management
```json
{
  "enableAllProjectMcpServers": true,
  "enabledMcpjsonServers": ["server1", "server2"],
  "disabledMcpjsonServers": ["blocked-server"]
}
```

### Plugins
```json
{
  "enabledPlugins": {
    "formatter@anthropic-tools": true
  }
}
```
Plugin syntax: `plugin-name@source` where source is a marketplace's name (`{{PLUGIN_CLI}} marketplace list`) or `builtin`.
