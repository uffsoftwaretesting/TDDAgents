import inspect
from typing import Callable
import pytest

from src.rk2_heun import rk2_heun


def test_signature_has_strict_type_hints() -> None:
    """
    Ensure rk2_heun has strict type hints on all parameters and return type.
    """
    sig = inspect.signature(rk2_heun)
    params = sig.parameters

    # Check each parameter has the correct annotation
    assert 'f' in params, "Parameter 'f' should be present"
    assert params['f'].annotation == Callable[[float, float], float], (
        f"Annotation for f is {params['f'].annotation}, expected Callable[[float, float], float]"
    )

    for name in ['t0', 'y0', 't_final', 'h']:
        assert name in params, f"Parameter '{name}' should be present"
        assert params[name].annotation == float, (
            f"Annotation for {name} is {params[name].annotation}, expected float"
        )

    # Return type
    assert sig.return_annotation == float, (
        f"Return annotation is {sig.return_annotation}, expected float"
    )


def test_docstring_structure_contains_sections_and_params() -> None:
    """
    Validate that the docstring contains Args, Returns, Raises sections and lists all parameters.
    """
    doc = inspect.getdoc(rk2_heun)
    assert doc is not None, "Docstring should not be empty"

    # Check for section headers
    for section in ('Args:', 'Returns:', 'Raises:'):
        assert section in doc, f"Docstring must contain '{section}' section"

    # Check that each parameter is documented
    for param in ('f', 't0', 'y0', 't_final', 'h'):
        assert param in doc, f"Docstring should mention parameter '{param}'"

    # Basic sanity: docstring begins with a brief description
    first_line = doc.strip().splitlines()[0]
    assert 'RK2' in first_line or 'Heun' in first_line or 'RK2 Heun' in first_line, (
        "Docstring should start with a description of the RK2 Heun method"
    )
