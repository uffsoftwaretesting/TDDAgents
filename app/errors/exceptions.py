"""
Custom exceptions for TDD Agents flow control.
"""

class TDDWorkflowError(Exception):
    """Base class for TDD pipeline errors."""
    pass

class TransientInfraError(TDDWorkflowError):
    """
    Temporary errors (Network, Rate Limit, Timeout).
    Should trigger retries up to the configured limit.
    """
    def __init__(self, message: str, original_exc: Exception = None):
        super().__init__(message)
        self.original_exc = original_exc

class FatalInfraError(TDDWorkflowError):
    """
    Destructive errors (Missing permission, Expired sandbox, Full disk, Context Window).
    Should abort the graph immediately (END).
    """
    def __init__(self, message: str, original_exc: Exception = None):
        super().__init__(message)
        self.original_exc = original_exc
    