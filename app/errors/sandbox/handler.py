"""
Classifies workspace failures into the pipeline's retry taxonomy.

This module used to import six E2B exception types directly. It no longer imports the
SDK at all: `app/sandbox/adapter.py` translates provider exceptions into the
WorkspaceError family at the boundary, and each of those declares whether it is
retryable. That keeps the adapter as the single seam to the execution provider, which
is the whole point of having one.

The classification is unchanged from the E2B-typed version:

    TimeoutException        -> WorkspaceTimeout          -> TransientInfraError
    RateLimitException      -> WorkspaceRateLimited      -> TransientInfraError
    SandboxException (base) -> WorkspaceTransportError   -> TransientInfraError
    AuthenticationException -> WorkspaceAuthError        -> FatalInfraError
    NotFoundException       -> WorkspaceNotFound         -> FatalInfraError
    NotEnoughSpaceException -> WorkspaceCapacityError    -> FatalInfraError
    TemplateException       -> WorkspaceTemplateError    -> FatalInfraError
    anything else           -> WorkspaceProviderError    -> FatalInfraError
"""

from __future__ import annotations

from typing import NoReturn

from app.errors.exceptions import FatalInfraError, TransientInfraError
from app.workspace.base import WorkspaceError


def handle_workspace_exception(exc: Exception, context: str = "") -> NoReturn:
    """
    Always raises. Call it from inside an `except` block and never expect a return.

    Raises:
        TransientInfraError: The failure is worth retrying.
        FatalInfraError: The failure is terminal.
    """
    prefix = f"[{context}] " if context else ""

    if isinstance(exc, WorkspaceError) and exc.retryable:
        raise TransientInfraError(f"{prefix}{exc}", original_exc=exc) from exc

    if isinstance(exc, WorkspaceError):
        raise FatalInfraError(f"{prefix}{exc}", original_exc=exc) from exc

    # Fail closed: anything that never passed through the adapter's translation is
    # unclassified, and unclassified is terminal.
    raise FatalInfraError(
        f"{prefix}Unclassified infrastructure failure: {exc}", original_exc=exc
    ) from exc
