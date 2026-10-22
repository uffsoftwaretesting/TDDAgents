"""
Tools auto mode approves without asking the classifier.

Ported from `reference/claude-code/src/utils/permissions/classifierDecision.ts` ->
`SAFE_YOLO_ALLOWLISTED_TOOLS`, `isAutoModeAllowlistedTool`.

Upstream's names are kept for every tool, so a tool this build adds later under its upstream
name (`TaskOutput`, `TaskStop`, ...) is covered the day it lands; the two file tools renamed
here are listed under their names in this build (`Read` -> `ReadFile`). Feature-gated and
ant-only entries (`TerminalCapture`, `OverflowTest`, `VerifyPlanExecution`, `Workflow`) are
left out, as an external build leaves them out.
"""

from __future__ import annotations

from app.loop.permissions.yolo_classifier_names import YOLO_CLASSIFIER_TOOL_NAME

#: `SAFE_YOLO_ALLOWLISTED_TOOLS`.
SAFE_YOLO_ALLOWLISTED_TOOLS: frozenset[str] = frozenset(
    {
        "ReadFile",
        "Grep",
        "Glob",
        "LSP",
        "ToolSearch",
        "ListMcpResourcesTool",
        "ReadMcpResourceTool",
        "TodoWrite",
        "TaskCreate",
        "TaskGet",
        "TaskUpdate",
        "TaskList",
        "TaskStop",
        "TaskOutput",
        "AskUserQuestion",
        "EnterPlanMode",
        "ExitPlanMode",
        "TeamCreate",
        "TeamDelete",
        "SendMessage",
        "Sleep",
        YOLO_CLASSIFIER_TOOL_NAME,
    }
)


def is_auto_mode_allowlisted_tool(tool_name: str) -> bool:
    """`isAutoModeAllowlistedTool`."""
    return tool_name in SAFE_YOLO_ALLOWLISTED_TOOLS


__all__ = ["SAFE_YOLO_ALLOWLISTED_TOOLS", "is_auto_mode_allowlisted_tool"]
