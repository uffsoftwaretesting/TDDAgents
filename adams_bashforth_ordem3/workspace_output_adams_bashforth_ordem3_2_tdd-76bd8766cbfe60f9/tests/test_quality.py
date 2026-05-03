import inspect
import pytest
from inspect import signature, _empty

from src.adams import _rk4_step, adams_bashforth_3


def test_rk4_step_has_docstring():
    """
    Ensure that the internal RK4 step function has a non-empty PEP257 docstring.
    """
    doc = inspect.getdoc(_rk4_step)
    assert doc is not None and len(doc.strip()) > 0, "_rk4_step missing or empty docstring"


def test_adams_bashforth_3_has_docstring():
    """
    Ensure that the main Adams–Bashforth function has a non-empty PEP257 docstring.
    """
    doc = inspect.getdoc(adams_bashforth_3)
    assert doc is not None and len(doc.strip()) > 0, "adams_bashforth_3 missing or empty docstring"


def test_rk4_step_type_annotations():
    """
    Ensure that all parameters and return of _rk4_step have type annotations.
    """
    sig = signature(_rk4_step)
    # Check all parameters
    for name, param in sig.parameters.items():
        assert param.annotation is not _empty, f"Parameter '{name}' of _rk4_step missing annotation"
    # Check return annotation
    assert sig.return_annotation is not _empty, "Return of _rk4_step missing annotation"


def test_adams_bashforth_3_type_annotations():
    """
    Ensure that all parameters and return of adams_bashforth_3 have type annotations.
    """
    sig = signature(adams_bashforth_3)
    # Check all parameters
    for name, param in sig.parameters.items():
        assert param.annotation is not _empty, f"Parameter '{name}' of adams_bashforth_3 missing annotation"
    # Check return annotation
    assert sig.return_annotation is not _empty, "Return of adams_bashforth_3 missing annotation"