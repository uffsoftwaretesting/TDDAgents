"""
MCP client (Phase 8): Claude Code's `services/mcp/` and the MCP tools, ported.

* `config.py` — where a run's servers come from (`getClaudeCodeMcpConfigs`);
* `transports.py` — stdio / sse / http / ws, headers and `headersHelper`;
* `client.py` — connect, list, call, result transformation, `getMcpInstructions`;
* `tools.py` — `mcp__<server>__<tool>` wrappers and the two resource tools;
* `manager.py` — `McpConnectionManager`: connect all, collect tools, close all;
* `types.py`, `normalization.py`, `env_expansion.py`, `output.py` — the supporting ports.
"""
