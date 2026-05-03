import inspect
from typing import Callable

import pytest

from src.solve import solve


def test_solve_signature_annotations():
    """
    The solve function should have proper type hints on parameters and return value.
    """
    sig = inspect.signature(solve)
    params = sig.parameters

    # Check parameter annotations
    assert 'f' in params, "solve should have a parameter 'f'"
    assert params['f'].annotation == Callable[[float, float], float], (
        f"Expected annotation for 'f' to be Callable[[float, float], float], got {params['f'].annotation}"
    )
    assert params['t0'].annotation == float, (
        f"Expected annotation for 't0' to be float, got {params['t0'].annotation}"
    )
    assert params['tf'].annotation == float, (
        f"Expected annotation for 'tf' to be float, got {params['tf'].annotation}"
    )
    assert params['y0'].annotation == float, (
        f"Expected annotation for 'y0' to be float, got {params['y0'].annotation}"
    )
    assert params['n'].annotation == int, (
        f"Expected annotation for 'n' to be int, got {params['n'].annotation}"
    )

    # Check return annotation
    assert sig.return_annotation == float, (
        f"Expected return annotation to be float, got {sig.return_annotation}"
    )


def test_solve_has_docstring():
    """
    The solve function should include a docstring that mentions step size and iteration.
    """
    doc = solve.__doc__
    assert doc is not None and isinstance(doc, str), "solve should have a non-empty docstring"
    doc_lower = doc.lower()
    assert 'step' in doc_lower, "Docstring should mention 'step'"
    assert 'iteration' in doc_lower, "Docstring should mention 'iteration'"
