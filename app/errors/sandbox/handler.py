from __future__ import annotations

try:
    from e2b import (
        AuthenticationException,
        NotFoundException,
        NotEnoughSpaceException,
        SandboxException,
        TemplateException,
        TimeoutException,
    )
    from e2b.exceptions import RateLimitException
    _E2B_AVAILABLE = True
except ImportError:
    _E2B_AVAILABLE = False

from app.errors.exceptions import FatalInfraError, TransientInfraError

def handle_e2b_exception(exc: Exception, context: str = "") -> None:
    """
    Classifies exceptions from the E2B SDK.

    Raises:
        TransientInfraError
        FatalInfraError
    """
    prefix = f"[{context}] " if context else ""

    if _E2B_AVAILABLE:
        # Extra safeguard in case a terminal command error reaches the handler
        if type(exc).__name__ == "CommandExitException":
            raise TransientInfraError(
                f"{prefix}Internal command execution failed in the Sandbox: {exc}"
            ) from exc

        if isinstance(exc, TimeoutException):
            raise TransientInfraError(
                f"{prefix}E2B timeout reached."
            ) from exc

        if isinstance(exc, RateLimitException):
            raise TransientInfraError(
                f"{prefix}E2B rate limit reached."
            ) from exc

        if isinstance(exc, AuthenticationException):
            raise FatalInfraError(
                f"{prefix}E2B authentication failed."
            ) from exc

        if isinstance(exc, NotFoundException):
            raise FatalInfraError(
                f"{prefix}Sandbox or resource not found in E2B."
            ) from exc

        if isinstance(exc, NotEnoughSpaceException):
            raise FatalInfraError(
                f"{prefix}Insufficient disk space in the sandbox."
            ) from exc

        if isinstance(exc, TemplateException):
            raise FatalInfraError(
                f"{prefix}Invalid sandbox template."
            ) from exc

        # SandboxException is the base class for the exceptions above, so it
        # must come last to catch any other unknown E2B errors
        if isinstance(exc, SandboxException):
            raise TransientInfraError(
                f"{prefix}Sandbox error: {exc}"
            ) from exc

    raise FatalInfraError(
        f"{prefix}Unclassified infrastructure failure: {exc}"
    ) from exc