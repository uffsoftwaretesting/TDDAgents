"""
Workspace boundary enforcement, dangerous path protection, and traversal guards (Part C5).

Ported from:
- `reference/claude-code/src/utils/permissions/filesystem.ts`
- `reference/claude-code/src/utils/permissions/pathValidation.ts`
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from app.loop.permissions.types import (
    PermissionBehavior,
    PermissionMode,
    PermissionResult,
    ToolPermissionContext,
)

DANGEROUS_FILES: tuple[str, ...] = (
    ".gitconfig",
    ".gitmodules",
    ".bashrc",
    ".bash_profile",
    ".zshrc",
    ".zprofile",
    ".profile",
    ".ripgreprc",
    ".mcp.json",
    ".claude.json",
)

DANGEROUS_DIRECTORIES: tuple[str, ...] = (
    ".git",
    ".vscode",
    ".idea",
    ".claude",
)


def normalize_case(p: str) -> str:
    """Normalize path case for case-insensitive comparison."""
    return p.lower()


def is_dangerous_path(path: str | Path) -> tuple[bool, str]:
    """
    Check if a path targets sensitive configuration files or directories (e.g. .git/, .bashrc).
    """
    p = Path(path)
    # Check filename
    name_lower = p.name.lower()
    if name_lower in DANGEROUS_FILES:
        return True, f"Modifying sensitive configuration file '{p.name}' is restricted."

    # Check directory components
    for part in p.parts:
        if part.lower() in DANGEROUS_DIRECTORIES:
            return True, f"Modifying files inside sensitive directory '{part}' is restricted."

    return False, ""


def is_path_in_working_dir(path: str | Path, working_dir: str | Path) -> bool:
    """
    Check if a path is located inside the working directory, resolving symlinks and traversal.
    """
    try:
        resolved_work = Path(working_dir).resolve()
        resolved_path = Path(path).resolve()
        return resolved_path.is_relative_to(resolved_work)
    except Exception:
        return False


def is_path_in_allowed_working_dirs(
    path: str | Path,
    working_dir: str | Path,
    additional_dirs: tuple[str, ...] = (),
) -> bool:
    """
    Check if a path is within the primary working directory or any additional working directories.
    """
    if is_path_in_working_dir(path, working_dir):
        return True

    for add_dir in additional_dirs:
        if is_path_in_working_dir(path, add_dir):
            return True

    return False


def is_path_allowed(
    path: str | Path,
    context: ToolPermissionContext,
    working_dir: str | Path,
    operation: Literal["read", "write"] = "write",
) -> PermissionResult:
    """
    Evaluate whether a path operation is allowed under the workspace boundary and permission context (Part C5).

    Precedence:
    1. Deny rules targeting path -> deny
    2. Containment in workspace (or additional dirs) -> if outside, deny
    3. Dangerous path safety check on write -> ask (bypass-immune!)
    4. Mode rules:
       - read -> allow
       - write in acceptEdits / bypassPermissions -> allow
       - write in default / plan -> ask
    """
    path_str = str(path)

    # 1. Deny rules
    for rule in context.always_deny_rules:
        if rule.rule_content and (rule.rule_content == path_str or path_str.startswith(rule.rule_content.rstrip("*"))):
            return PermissionResult(
                behavior=PermissionBehavior.DENY,
                message=f"Access to path '{path_str}' denied by rule.",
                decision_reason={"type": "rule", "rule": rule},
            )

    # 2. Workspace boundary check
    in_boundary = is_path_in_allowed_working_dirs(
        path, working_dir, context.additional_working_directories
    )
    if not in_boundary:
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            message=f"Path '{path_str}' is outside the allowed workspace boundary.",
            decision_reason={"type": "other", "reason": "outside_workspace"},
        )

    # 3. Dangerous files check (bypass-immune on write)
    if operation == "write":
        is_dang, dang_msg = is_dangerous_path(path)
        if is_dang:
            return PermissionResult(
                behavior=PermissionBehavior.ASK,
                message=dang_msg,
                decision_reason={"type": "safetyCheck", "reason": dang_msg},
            )

    # 4. Mode evaluation
    if operation == "read":
        return PermissionResult(
            behavior=PermissionBehavior.ALLOW,
            decision_reason={"type": "mode", "mode": context.mode},
        )

    # Write operation
    if context.mode in (PermissionMode.ACCEPT_EDITS, PermissionMode.BYPASS_PERMISSIONS):
        return PermissionResult(
            behavior=PermissionBehavior.ALLOW,
            decision_reason={"type": "mode", "mode": context.mode},
        )

    # Default / Plan / dontAsk modes
    if context.mode == PermissionMode.DONT_ASK:
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            message=f"Writing to '{path_str}' suppressed in dontAsk mode.",
            decision_reason={"type": "mode", "mode": context.mode},
        )

    return PermissionResult(
        behavior=PermissionBehavior.ASK,
        message=f"Writing to '{path_str}' requires permission in {context.mode} mode.",
        decision_reason={"type": "mode", "mode": context.mode},
    )
