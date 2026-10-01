"""
Workspace abstractions, protocol definitions, and implementations (Part G).
"""

from __future__ import annotations

from app.workspace.base import (
    CommandResult,
    FileEntry,
    Workspace,
    WorkspaceAuthError,
    WorkspaceCapacityError,
    WorkspaceError,
    WorkspaceKind,
    WorkspaceNotFound,
    WorkspacePathError,
    WorkspaceProviderError,
    WorkspaceRateLimited,
    WorkspaceTemplateError,
    WorkspaceTimeout,
    WorkspaceTransportError,
    normalize_path,
)
from app.workspace.e2b import E2BWorkspace
from app.workspace.local import LocalWorkspace
from app.workspace.router import (
    DEFAULT_SPEC,
    VALID_SPECS,
    DualWorkspace,
    WorkspaceSpec,
    resolve_workspace,
)

__all__ = [
    "CommandResult",
    "FileEntry",
    "Workspace",
    "WorkspaceKind",
    "WorkspaceError",
    "WorkspacePathError",
    "WorkspaceNotFound",
    "WorkspaceTimeout",
    "WorkspaceRateLimited",
    "WorkspaceAuthError",
    "WorkspaceCapacityError",
    "WorkspaceTemplateError",
    "WorkspaceTransportError",
    "WorkspaceProviderError",
    "normalize_path",
    "E2BWorkspace",
    "LocalWorkspace",
    "DualWorkspace",
    "WorkspaceSpec",
    "VALID_SPECS",
    "DEFAULT_SPEC",
    "resolve_workspace",
]
