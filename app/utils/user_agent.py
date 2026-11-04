"""
User-Agent string helpers.

Ported from `reference/claude-code/src/utils/userAgent.ts` -> `getClaudeCodeUserAgent`: the product and
its version (`app/constants/product.py` -> `VERSION`), `tddagents/<version>` for upstream's
`claude-code/<version>`. The MCP client's (`app/services/mcp/client.py` -> `get_mcp_user_agent`) and
WebFetch's (`app/tools/web_fetch_tool/utils.py` -> `USER_AGENT`) are built on it, as upstream's
`utils/http.ts` builds `getMCPUserAgent` and `getWebFetchUserAgent`.
"""

from __future__ import annotations

from app.constants.product import VERSION

#: The product token of the User-Agent strings (upstream's `claude-code`).
USER_AGENT_PRODUCT = "tddagents"


def get_tddagents_user_agent() -> str:
    """`getClaudeCodeUserAgent`: `tddagents/<version>`."""
    return f"{USER_AGENT_PRODUCT}/{VERSION}"


__all__ = ["USER_AGENT_PRODUCT", "get_tddagents_user_agent"]
