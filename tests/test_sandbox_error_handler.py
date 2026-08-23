"""
`handle_workspace_exception` — the sandbox side of the retry taxonomy.

This function decides, for every infrastructure failure the sandbox produces, whether
the pipeline retries it or aborts. Getting it wrong is expensive in both directions: a
fatal error classified as transient burns three LLM retries before failing anyway, and a
transient blip classified as fatal throws away a run that would have recovered.

It was rewritten during Phase 1A — the E2B-typed `isinstance` ladder replaced by
dispatch on `WorkspaceError.retryable` — and shipped with no tests at all. These pin the
mapping so the rewrite cannot drift from the behavior it replaced.
"""

from __future__ import annotations

import pytest

from app.errors.exceptions import FatalInfraError, TDDWorkflowError, TransientInfraError
from app.errors.sandbox.handler import handle_workspace_exception
from app.workspace.base import (
    WorkspaceAuthError,
    WorkspaceCapacityError,
    WorkspaceError,
    WorkspaceNotFound,
    WorkspacePathError,
    WorkspaceProviderError,
    WorkspaceRateLimited,
    WorkspaceTemplateError,
    WorkspaceTimeout,
    WorkspaceTransportError,
)

# The mapping this module exists to enforce. Each row is one row of the table in the
# handler's own docstring; if the two ever disagree, one of them is a lie.
RETRYABLE = [WorkspaceTimeout, WorkspaceRateLimited, WorkspaceTransportError]
TERMINAL = [
    WorkspaceAuthError,
    WorkspaceNotFound,
    WorkspaceCapacityError,
    WorkspaceTemplateError,
    WorkspaceProviderError,
    WorkspacePathError,
]


# ── The classification ───────────────────────────────────────────────────────

@pytest.mark.parametrize("error_type", RETRYABLE, ids=lambda t: t.__name__)
def test_retryable_errors_become_transient(error_type):
    with pytest.raises(TransientInfraError):
        handle_workspace_exception(error_type("boom"))


@pytest.mark.parametrize("error_type", TERMINAL, ids=lambda t: t.__name__)
def test_terminal_errors_become_fatal(error_type):
    with pytest.raises(FatalInfraError):
        handle_workspace_exception(error_type("boom"))


@pytest.mark.parametrize("error_type", RETRYABLE + TERMINAL, ids=lambda t: t.__name__)
def test_the_handler_never_returns(error_type):
    """
    Callers use it as a statement, not an expression — several `except` blocks end with
    a bare call to it and no `raise` of their own. A silent return would let execution
    fall through into the success path with unset variables.
    """
    with pytest.raises(TDDWorkflowError):
        handle_workspace_exception(error_type("boom"))


def test_every_workspace_error_subclass_is_covered():
    """
    Guards against a new WorkspaceError subclass being added without deciding whether
    it retries — it would otherwise silently inherit the base class default.
    """
    known = set(RETRYABLE) | set(TERMINAL)
    discovered = set(WorkspaceError.__subclasses__())
    assert discovered == known, (
        f"unclassified WorkspaceError subclasses: {discovered - known}"
    )


def test_retryable_flag_is_what_decides(monkeypatch):
    """
    Dispatch is on the `retryable` flag, not on a hardcoded type list — that is the
    whole point of the rewrite, and it is what lets the handler drop its E2B imports.
    """

    class CustomError(WorkspaceError):
        retryable = True

    with pytest.raises(TransientInfraError):
        handle_workspace_exception(CustomError("boom"))

    CustomError.retryable = False
    with pytest.raises(FatalInfraError):
        handle_workspace_exception(CustomError("boom"))


# ── Fail-closed on unknown types ─────────────────────────────────────────────

@pytest.mark.parametrize(
    "exc",
    [ValueError("nope"), RuntimeError("nope"), KeyError("nope"), Exception("nope")],
    ids=type,
)
def test_an_untranslated_exception_is_fatal(exc):
    """
    Anything that never passed through the adapter's translation is unclassified, and
    unclassified fails closed. Retrying an unknown failure three times is strictly worse
    than surfacing it once.
    """
    with pytest.raises(FatalInfraError, match="Unclassified infrastructure failure"):
        handle_workspace_exception(exc)


def test_the_unclassified_message_carries_the_original_text():
    with pytest.raises(FatalInfraError) as raised:
        handle_workspace_exception(ValueError("disk gremlins"))
    assert str(raised.value) == "Unclassified infrastructure failure: disk gremlins"


# ── Message and cause plumbing ───────────────────────────────────────────────

def test_the_context_label_is_prefixed(caplog):
    with pytest.raises(TransientInfraError) as raised:
        handle_workspace_exception(WorkspaceTimeout("timed out"), context="Runner")
    assert str(raised.value) == "[Runner] timed out"


def test_an_absent_context_adds_no_prefix():
    with pytest.raises(TransientInfraError) as raised:
        handle_workspace_exception(WorkspaceTimeout("timed out"))
    assert str(raised.value) == "timed out"


def test_an_empty_context_adds_no_prefix():
    with pytest.raises(TransientInfraError) as raised:
        handle_workspace_exception(WorkspaceTimeout("timed out"), context="")
    assert str(raised.value) == "timed out"


def test_the_context_is_prefixed_on_the_fatal_path_too():
    with pytest.raises(FatalInfraError) as raised:
        handle_workspace_exception(WorkspaceAuthError("bad key"), context="Adapter")
    assert str(raised.value) == "[Adapter] bad key"


def test_the_context_is_prefixed_on_the_unclassified_path_too():
    with pytest.raises(FatalInfraError) as raised:
        handle_workspace_exception(ValueError("huh"), context="SandboxUtils")
    assert str(raised.value) == "[SandboxUtils] Unclassified infrastructure failure: huh"


@pytest.mark.parametrize(
    "exc",
    [WorkspaceTimeout("t"), WorkspaceAuthError("a"), ValueError("v")],
    ids=["transient", "fatal", "unclassified"],
)
def test_the_original_exception_is_preserved_on_every_path(exc):
    """
    Nodes log `exc.original_exc` when they report a failure. Losing it turns a
    diagnosable error into "something went wrong".
    """
    with pytest.raises(TDDWorkflowError) as raised:
        handle_workspace_exception(exc)

    assert raised.value.original_exc is exc
    assert raised.value.__cause__ is exc
