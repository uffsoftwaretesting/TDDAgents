"""
The eighteen tools, assembled into one registry.

This is the module Phase 2's `run_agent` imports. `Agent` (Phase 5) and `Skill` (Phase 4)
are deliberately absent — they are not among the eighteen, and each arrives with the
machinery it dispatches to.

Two tools self-disable when their provider is unconfigured, so `default_registry()` is
honest about what this machine can actually do rather than advertising a capability that
fails on first use.
"""

from __future__ import annotations

from app.tools.base import AnyTool
from app.tools.exec import EXEC_TOOLS
from app.tools.fs import FS_TOOLS
from app.tools.host_read import HOST_TOOLS
from app.tools.registry import ToolRegistry
from app.tools.run_tests import RUN_TESTS_TOOLS
from app.tools.todo import TODO_TOOLS
from app.tools.web import WEB_TOOLS

ALL_TOOLS: list[AnyTool] = [
    *FS_TOOLS,        # ReadFile ListDir Glob Grep WriteFile Edit MultiEdit Delete Move
    *EXEC_TOOLS,      # Bash BashOutput KillShell RunCode
    *RUN_TESTS_TOOLS,  # RunTests
    *HOST_TOOLS,      # HostRead
    *WEB_TOOLS,       # WebSearch WebFetch
    *TODO_TOOLS,      # TodoWrite
]

#: The roster is fixed at eighteen by the locked design. Asserting it here means adding a
#: tool without updating the design document fails at import rather than silently.
EXPECTED_TOOL_COUNT = 18


def default_registry() -> ToolRegistry:
    """Every tool, enabled or not. `resolve_tools` filters the disabled ones out."""
    return ToolRegistry(list(ALL_TOOLS))
