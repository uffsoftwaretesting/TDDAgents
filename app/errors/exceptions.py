"""
Custom exceptions for TDD Agents flow control.

The split between these two is what the whole retry taxonomy rests on: the classifiers
in `app/errors/` map every provider failure onto exactly one of them, and the graph nodes
branch on which one they caught.
"""

from __future__ import annotations


class TDDWorkflowError(Exception):
    """
    Base class for TDD pipeline errors.

    `original_exc` is defined here rather than on each subclass because every classifier
    sets it and every node reads it — nodes log `exc.original_exc` when reporting a
    failure. Making it part of the base contract means a caller can reach for it after
    catching either subclass without narrowing first.
    """

    def __init__(self, message: str, original_exc: Exception | None = None) -> None:
        super().__init__(message)
        self.original_exc = original_exc


class TransientInfraError(TDDWorkflowError):
    """
    Temporary errors (Network, Rate Limit, Timeout).
    Should trigger retries up to the configured limit.
    """


class FatalInfraError(TDDWorkflowError):
    """
    Destructive errors (Missing permission, Expired sandbox, Full disk, Context Window).
    Should abort the graph immediately (END).
    """
