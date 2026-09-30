"""
Authorization, safety, and permission subsystem (Part C).

Ported from:
- `reference/claude-code/src/utils/permissions/`
"""

from __future__ import annotations

from app.loop.permissions.capability import (
    READ_ONLY_BASE_COMMANDS,
    bash_is_read_only,
    has_output_redirection,
    is_bash_command_read_only,
    split_shell_commands,
)
from app.loop.permissions.filesystem import (
    DANGEROUS_DIRECTORIES,
    DANGEROUS_FILES,
    is_dangerous_path,
    is_path_allowed,
    is_path_in_allowed_working_dirs,
    is_path_in_working_dir,
)
from app.loop.permissions.gate import has_permissions_to_use_tool
from app.loop.permissions.rules import (
    check_rule_based_permissions,
    extract_permission_context,
    get_allow_rule_for_tool,
    get_ask_rule_for_tool,
    get_deny_rule_for_tool,
    rule_matches,
)
from app.loop.permissions.types import (
    EXTERNAL_PERMISSION_MODES,
    INTERNAL_PERMISSION_MODES,
    PermissionBehavior,
    PermissionMode,
    PermissionRule,
    PermissionRuleSource,
    ToolPermissionContext,
    cycle_permission_mode,
    get_next_permission_mode,
)

__all__ = [
    # Types & modes (C1)
    "PermissionMode",
    "EXTERNAL_PERMISSION_MODES",
    "INTERNAL_PERMISSION_MODES",
    "PermissionBehavior",
    "PermissionRuleSource",
    "PermissionRule",
    "ToolPermissionContext",
    "get_next_permission_mode",
    "cycle_permission_mode",
    # Rule evaluation (C2)
    "rule_matches",
    "get_deny_rule_for_tool",
    "get_ask_rule_for_tool",
    "get_allow_rule_for_tool",
    "check_rule_based_permissions",
    "extract_permission_context",
    # Runtime gate (C3)
    "has_permissions_to_use_tool",
    # Capability (C4)
    "READ_ONLY_BASE_COMMANDS",
    "is_bash_command_read_only",
    "bash_is_read_only",
    "has_output_redirection",
    "split_shell_commands",
    # Filesystem & boundary (C5)
    "DANGEROUS_FILES",
    "DANGEROUS_DIRECTORIES",
    "is_dangerous_path",
    "is_path_in_working_dir",
    "is_path_in_allowed_working_dirs",
    "is_path_allowed",
]
